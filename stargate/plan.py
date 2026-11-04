"""`stargate plan`: strategy + fit + explanation in one plan-v1 report."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import catalog as catalog_module
from . import fit as fit_module
from . import meta as meta_module
from . import strategy as strategy_module
from .profile import ALL_FACT_IDS, validate_profile
from .research import FACT_NAMES, validate_report
from .scan import KNOWN_FIXTURES, build_profile

EXIT_OK = 0
EXIT_MISSING_KEY = 2
EXIT_META_FAILURE = 5
EXIT_INVALID_INPUT = 6
EXIT_REFUSED = 7

SCHEMA_VERSION = "plan-v1"
EXPLAIN_MAX_TOKENS = 4096
NOTES_LABEL = "model explanation, not checked"
NOTES_LABEL_INCOMPLETE = "model explanation, cut short, not checked"
EXPLAIN_MODEL = meta_module.META_MODEL

TOP_LEVEL_KEYS = frozenset({
    "schema_version", "subject", "inputs", "rules", "unlock_state",
    "strategies", "fit", "selection_result", "build_allowed",
    "explanation_status", "handoff", "notes", "provenance",
})
SUBJECT_KEYS = frozenset({"codename", "manufacturer", "model"})
INPUT_KEYS = frozenset({"profile", "research", "catalog"})
INPUT_ENTRY_KEYS = frozenset({"sha256", "provenance"})
RULE_FILES = (("strategy_rules", "docs/strategy-rules.md"),
              ("fit_budgets", "docs/fit-budgets.md"),
              ("inference_rules", "docs/inference-rules.md"))
RESULTS = ("fits", "unconfirmed", "refused")
BUILD_ALLOWED = ("emulator_only", "none")
EXPLANATION_STATUSES = ("not_requested", "ok", "incomplete", "failed")
STRATEGY_STATUSES = ("candidate", "blocked", "unknown", "not_covered")
BLOCKER_KINDS = ("current", "no_official_path", "unsupported")
CHECK_STATUSES = ("fits", "does_not_fit", "unknown", "not_needed")
CHOICE_STATUSES = ("chosen", "unconfirmed", "refused", "unknown",
                   "not_needed")
OVERALL_PROVENANCE = ("live", "replay", "synthetic")

CITED_FACT_RE = re.compile(r"\bF[1-9][0-9]*\b")
CITED_DOTTED_RE = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class InputError(Exception):
    """Invalid plan input (exit 6)."""


def repo_root() -> Path:
    """Repository root, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def parse_services(text: object) -> list:
    """Parse --services; raises InputError for anything unknown."""
    if not isinstance(text, str) or text.strip() == "":
        raise InputError("no services selected")
    services = []
    for part in text.split(","):
        name = part.strip()
        if name not in fit_module.SERVICES:
            raise InputError("unknown service %r" % (part,))
        if name not in services:
            services.append(name)
    return services


def _read_json_file(path: str, what: str):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise InputError("cannot read %s: %s" % (what, exc))
    try:
        parsed = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise InputError("%s is not valid JSON: %s" % (what, exc))
    return parsed, data


def load_profile(sim: str | None, profile_path: str | None):
    """Load the profile; returns (profile, sha256, provenance)."""
    if sim is not None and profile_path is not None:
        raise InputError("use exactly one of --sim and --profile")
    if sim is not None:
        if sim not in KNOWN_FIXTURES:
            raise InputError("unknown fixture %r" % (sim,))
        profile = build_profile(sim)
        errors = validate_profile(profile)
        if errors:
            raise InputError("built profile is invalid: %s" % errors[0])
        blob = json.dumps(profile, sort_keys=True, indent=2).encode("utf-8")
        return profile, sha256_bytes(blob), _profile_provenance(profile)
    if profile_path is None:
        raise InputError("use exactly one of --sim and --profile")
    profile, data = _read_json_file(profile_path, "profile")
    errors = validate_profile(profile)
    if errors:
        raise InputError("profile is invalid: %s" % errors[0])
    return profile, sha256_bytes(data), _profile_provenance(profile)


def _profile_provenance(profile: dict) -> str:
    if profile.get("simulated") is True:
        return "synthetic"
    return "live"


def load_research(research_path: str | None, codename: str):
    """Load the research file, or None; checks it is about this phone."""
    if research_path is None:
        return None
    report, data = _read_json_file(research_path, "research")
    errors = validate_report(report)
    if errors:
        raise InputError("research is invalid: %s" % errors[0])
    if report["subject"]["codename"] != codename:
        raise InputError(
            "research is about %r, not this phone (%r)"
            % (report["subject"]["codename"], codename))
    return report, sha256_bytes(data), report["provenance"]


def load_rules(root: Path | None = None) -> dict:
    """Hash the three rule files; raises InputError when unreadable."""
    base = root if root is not None else repo_root()
    rules = {}
    for key, relative in RULE_FILES:
        try:
            data = (base / relative).read_bytes()
        except OSError as exc:
            raise InputError("cannot read %s: %s" % (relative, exc))
        rules[key] = sha256_bytes(data)
    return rules


def load_catalog_for_plan(catalog_path=None):
    """Load the pinned catalog; returns (models, sha256, provenance)."""
    path = catalog_path if catalog_path is not None \
        else repo_root() / "catalog" / "models.json"
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise InputError("cannot read catalog: %s" % exc)
    loaded, errors = catalog_module.load_catalog(path)
    if errors:
        raise InputError("catalog is invalid: %s" % errors[0])
    models = loaded["models"]
    kinds = {entry["provenance"]["kind"] for entry in models}
    if kinds <= {"synthetic"}:
        provenance = "synthetic"
    elif kinds <= {"sourced"}:
        provenance = "sourced"
    else:
        provenance = "mixed"
    return models, sha256_bytes(data), provenance


def overall_provenance(profile_prov: str, research_prov: str | None,
                       catalog_prov: str) -> str:
    """One label for the whole plan: synthetic wins, then replay."""
    synthetic_side = catalog_prov in ("synthetic", "mixed")
    if profile_prov == "synthetic" or research_prov == "synthetic" \
            or synthetic_side:
        return "synthetic"
    if research_prov == "replay":
        return "replay"
    return "live"


def build_explain_prompt(plan: dict) -> str:
    """Prompt for --explain: decisions and fact IDs only, never raw keys."""
    lines = ["Explain these StarGate plan decisions in plain English for",
             "a non-specialist. Explain the risks; do not change any",
             "decision. Cite fact IDs like resources.ram_total when you",
             "mention a phone fact.", ""]
    subject = plan["subject"]
    lines.append("Phone: %s %s (%s)" % (
        subject.get("manufacturer") or "unknown maker",
        subject.get("model") or "unknown model",
        subject["codename"]))
    lines.append("Services: %s" % ", ".join(plan["fit"]["services"]))
    lines.append("Unlock state: %s" % plan["unlock_state"])
    lines.append("Strategies (ranked):")
    for entry in plan["strategies"]:
        lines.append("- %s: %s" % (entry["id"], entry["status"]))
    lines.append("Selection result: %s" % plan["selection_result"])
    for reason in plan["fit"]["reasons"]:
        lines.append("Reason: %s" % reason)
    return "\n".join(lines)


def extract_cited_ids(text: str) -> list:
    """Fact IDs cited in explanation text (F-numbers and dotted IDs)."""
    cited = CITED_FACT_RE.findall(text or "")
    cited.extend(CITED_DOTTED_RE.findall(text or ""))
    return sorted(set(cited))


def missing_cited_ids(cited: list, research_fact_ids: set) -> list:
    """Cited IDs that do not exist as a research fact or known name."""
    missing = []
    for entry in cited:
        if entry.startswith("F"):
            if entry not in research_fact_ids:
                missing.append(entry)
        elif entry not in ALL_FACT_IDS and entry not in FACT_NAMES:
            missing.append(entry)
    return sorted(missing)


def cut_short_reason(text: str, finish_reason: object) -> str:
    """Why an explanation looks cut short, or "" when it looks whole.

    Any finish_reason other than stop counts as incomplete, even when
    the text ends cleanly.
    """
    if finish_reason != "stop":
        return "finish_reason %s" % (finish_reason,)
    core = (text or "").rstrip().rstrip("\"')]}`*_")
    if not core or core[-1] not in ".!?":
        return "no closing punctuation"
    return ""


def run_explanation(prompt: str, research_fact_ids: set,
                    send=None, sleep=None):
    """One Meta call; returns (notes_or_None, status, code, model, reason).

    A reply cut short by the token cap is labelled "incomplete", never
    shown as complete. Reason is "" unless incomplete.
    """
    try:
        result = meta_module.chat(
            [{"role": "user", "content": prompt}],
            max_completion_tokens=EXPLAIN_MAX_TOKENS,
            send=send or meta_module.default_send,
            sleep=sleep or time.sleep)
    except meta_module.MetaError as exc:
        code = EXIT_MISSING_KEY \
            if exc.code == meta_module.MISSING_CREDENTIAL \
            else EXIT_META_FAILURE
        return None, "failed", code, EXPLAIN_MODEL, ""
    cut_short = cut_short_reason(result.content, result.finish_reason)
    label = NOTES_LABEL_INCOMPLETE if cut_short else NOTES_LABEL
    cited = extract_cited_ids(result.content)
    notes = {"text": result.content, "cited_ids": cited,
             "missing_ids": missing_cited_ids(cited, research_fact_ids),
             "label": label}
    status = "incomplete" if cut_short else "ok"
    return notes, status, None, result.model_returned or EXPLAIN_MODEL, \
        cut_short


def build_handoff(plan: dict, catalog_models: list) -> dict:
    """Handoff block for M4; always carries the decision itself."""
    choice = plan["fit"]["model_choice"]
    model = None
    if choice["model_id"] is not None:
        entry = next(item for item in catalog_models
                     if item["id"] == choice["model_id"])
        model = {"id": entry["id"], "source": entry["source"],
                 "revision": entry["revision"],
                 "files": [dict(item) for item in entry["files"]],
                 "format": entry["format"], "runtime": entry["runtime"],
                 "total_bytes": fit_module.total_model_bytes(entry)}
    unresolved = [{
        "area": "image_size",
        "detail": "capacity.system_image_max is always unknown in M3 "
                  "(M1 does not collect it); not checked yet.",
    }]
    for entry in plan["strategies"]:
        if entry["status"] in ("candidate", "unknown"):
            for check in entry["open_checks"]:
                unresolved.append({"area": "strategy",
                                   "strategy": entry["id"],
                                   "detail": check})
    for check in plan["fit"]["checks"]:
        if check["status"] == "unknown":
            unresolved.append({"area": "fit", "model": None,
                               "detail": "%s: %s" % (check["name"],
                                                    check["reason"])})
    for evaluated in plan["fit"]["models"]:
        for check in evaluated["checks"]:
            if check["status"] == "unknown":
                unresolved.append(
                    {"area": "fit", "model": evaluated["id"],
                     "detail": "%s: %s" % (check["name"], check["reason"])})
    return {
        "services": list(plan["fit"]["services"]),
        "model": model,
        "model_reason": "; ".join(choice["reasons"]) or "no model needed",
        "reasons": list(plan["fit"]["reasons"]),
        "candidate_strategies": [
            entry["id"] for entry in plan["strategies"]
            if entry["status"] == "candidate"],
        "unresolved": unresolved,
        "selection_result": plan["selection_result"],
        "build_allowed": plan["build_allowed"],
        "provenance": {
            "profile": plan["inputs"]["profile"]["provenance"],
            "research": plan["inputs"]["research"]["provenance"]
            if plan["inputs"]["research"] is not None else None,
            "catalog": plan["inputs"]["catalog"]["provenance"],
        },
    }


def build_plan(profile: dict, profile_sha: str, profile_prov: str,
               research_loaded, services: list, catalog_models: list,
               catalog_sha: str, catalog_prov: str, rules: dict,
               explain: bool = False, send=None, sleep=None) -> dict:
    """Build the plan-v1 dict. All input checks happen before any call."""
    facts = profile["facts"]
    codename = facts["identity.device_codename"]["value"]
    research_facts = None
    research_fact_ids: set = set()
    if research_loaded is not None:
        report = research_loaded[0]
        research_facts = {fact["name"]: fact for fact in report["facts"]}
        research_fact_ids = {fact["id"] for fact in report["facts"]}
    unlock = strategy_module.unlock_state(facts)
    strategies = strategy_module.evaluate(facts, research_facts)
    fit_result = fit_module.evaluate_selection(facts, services,
                                               catalog_models)
    result = fit_result["result"]
    build_allowed = "none" if result == "refused" else "emulator_only"
    maker = facts.get("identity.manufacturer", {}).get("value")
    model_name = facts.get("identity.model", {}).get("value")
    plan = {
        "schema_version": SCHEMA_VERSION,
        "subject": {"codename": codename,
                    "manufacturer": maker
                    if isinstance(maker, str) else None,
                    "model": model_name
                    if isinstance(model_name, str) else None},
        "inputs": {
            "profile": {"sha256": profile_sha,
                        "provenance": profile_prov},
            "research": {"sha256": research_loaded[1],
                         "provenance": research_loaded[2]}
            if research_loaded is not None else None,
            "catalog": {"sha256": catalog_sha,
                        "provenance": catalog_prov},
        },
        "rules": dict(rules),
        "unlock_state": unlock,
        "strategies": strategies,
        "fit": fit_result,
        "selection_result": result,
        "build_allowed": build_allowed,
        "explanation_status": {"status": "not_requested", "code": None,
                               "model": None, "reason": None},
        "handoff": {},
        "notes": None,
        "provenance": overall_provenance(
            profile_prov,
            research_loaded[2] if research_loaded is not None else None,
            catalog_prov),
    }
    if explain:
        notes, status, code, model, reason = run_explanation(
            build_explain_prompt(plan), research_fact_ids,
            send=send, sleep=sleep)
        plan["notes"] = notes
        plan["explanation_status"] = {"status": status, "code": code,
                                      "model": model,
                                      "reason": reason or None}
    plan["handoff"] = build_handoff(plan, catalog_models)
    return plan


def render_human(plan: dict) -> str:
    """Readable plan: strategies, fit, model, result, checks, notes."""
    subject = plan["subject"]
    inputs = plan["inputs"]
    research_prov = inputs["research"]["provenance"] \
        if inputs["research"] is not None else "no research file"
    lines = ["StarGate plan for %s (%s)" % (
        subject.get("model") or "unknown model", subject["codename"]),
        "Provenance: profile=%s research=%s catalog=%s plan=%s" % (
            inputs["profile"]["provenance"], research_prov,
            inputs["catalog"]["provenance"], plan["provenance"]),
        "", "Unlock state: %s" % plan["unlock_state"], "",
        "Strategies (ranked):"]
    for rank, entry in enumerate(plan["strategies"], start=1):
        lines.append("%d. %s: %s" % (rank, entry["id"],
                                     entry["status"]))
        if entry["status"] == "candidate":
            lines.append("   met: %s" % ", ".join(entry["met"]))
        if entry["status"] == "blocked":
            blocker = entry["blocker"]
            lines.append("   blocked by %s = %r (%s)" % (
                blocker["fact"], blocker["value"],
                blocker["blocker_kind"]))
        if entry["status"] == "unknown":
            lines.append("   missing: %s" % "; ".join(
                "%s (%s)" % (item["fact"], item["reason"])
                for item in entry["missing"]))
        for check in entry["open_checks"]:
            lines.append("   still open: %s" % check)
        for risk in entry["risks"]:
            lines.append("   risk: %s" % risk)
        lines.append("   signing: %s" % entry["signing"])
    lines.extend(["", "Fit (%s):" % ", ".join(plan["fit"]["services"])])
    for check in plan["fit"]["checks"]:
        if check["name"] == "kernel":
            lines.append("  kernel: not needed yet")
        elif check["status"] == "fits":
            lines.append("  %s: fits%s" % (
                check["name"],
                " (estimated)" if check.get("estimated") else ""))
        elif check["status"] == "does_not_fit":
            lines.append("  %s: DOES NOT FIT (%s)" % (check["name"],
                                                     check["reason"]))
        else:
            lines.append("  %s: unknown (%s)" % (check["name"],
                                                check["reason"]))
    choice = plan["fit"]["model_choice"]
    if choice["status"] == "chosen":
        lines.append("Model choice: %s (%s)" % (
            choice["model_id"], "; ".join(choice["reasons"])))
    elif choice["status"] == "not_needed":
        lines.append("Model choice: no model needed (Files only)")
    else:
        lines.append("Model choice: %s (%s)" % (
            choice["status"], "; ".join(choice["reasons"])))
    result = plan["selection_result"]
    if result == "fits" and plan["fit"]["estimated"]:
        result = "fits (estimated)"
    lines.extend(["", "Selection result: %s" % result,
                  "Build allowed: %s" % plan["build_allowed"], "",
                  "Build checks for M4:"])
    for item in plan["handoff"]["unresolved"]:
        lines.append("  - %s" % item["detail"])
    lines.append("")
    status = plan["explanation_status"]["status"]
    if status in ("ok", "incomplete"):
        lines.append("Explanation (%s):" % plan["notes"]["label"])
        if status == "incomplete":
            lines.append("(Cut short: %s.)"
                         % plan["explanation_status"]["reason"])
        lines.append(plan["notes"]["text"])
        if plan["notes"]["missing_ids"]:
            lines.append("Cited IDs with no fact: %s"
                         % ", ".join(plan["notes"]["missing_ids"]))
    elif status == "failed":
        lines.append("Explanation failed (code %s); plan above stands."
                     % plan["explanation_status"]["code"])
    else:
        lines.append("Explanation: not requested.")
    return "\n".join(lines) + "\n"


def run_plan(sim=None, profile_path=None, services_text=None,
             research_path=None, explain=False, json_only=False,
             json_out=None, repo_root_path=None, catalog_path=None,
             send=None, sleep=None):
    """Run stargate plan; returns (exit_code, stdout, stderr)."""
    try:
        services = parse_services(services_text)
        profile, profile_sha, profile_prov = load_profile(
            sim, profile_path)
        codename = profile["facts"]["identity.device_codename"]["value"]
        research_loaded = load_research(research_path, codename)
        rules = load_rules(repo_root_path)
        models, catalog_sha, catalog_prov = load_catalog_for_plan(
            catalog_path)
    except InputError as exc:
        return EXIT_INVALID_INPUT, "", "error: %s\n" % exc
    plan = build_plan(profile, profile_sha, profile_prov,
                      research_loaded, services, models, catalog_sha,
                      catalog_prov, rules, explain=explain,
                      send=send, sleep=sleep)
    errors = validate_plan(plan)
    if errors:
        return EXIT_INVALID_INPUT, "", \
            "error: built plan is invalid: %s\n" % errors[0]
    if json_out is not None:
        try:
            with open(json_out, "w", encoding="utf-8") as handle:
                json.dump(plan, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            return EXIT_INVALID_INPUT, "", \
                "error: cannot write %s: %s\n" % (json_out, exc)
    if json_only:
        stdout_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    else:
        stdout_text = render_human(plan)
    if plan["selection_result"] == "refused":
        return EXIT_REFUSED, stdout_text, ""
    if plan["explanation_status"]["status"] == "failed":
        return plan["explanation_status"]["code"], stdout_text, \
            "warning: explanation failed; plan above stands.\n"
    return EXIT_OK, stdout_text, ""


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.match(value) is not None


def validate_plan(plan: object) -> list:
    """Validate a plan-v1 report; returns a list of error strings."""
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    errors = []
    for key in plan:
        if key not in TOP_LEVEL_KEYS:
            errors.append("plan has unknown key %r" % (key,))
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append('schema_version must be "%s"' % SCHEMA_VERSION)
    subject = plan.get("subject")
    if not isinstance(subject, dict):
        errors.append("subject must be an object")
    else:
        for key in subject:
            if key not in SUBJECT_KEYS:
                errors.append("subject has unknown key %r" % (key,))
        if not isinstance(subject.get("codename"), str) \
                or not subject["codename"]:
            errors.append("subject.codename is required")
        for key in ("manufacturer", "model"):
            item = subject.get(key)
            if item is not None and not isinstance(item, str):
                errors.append("subject.%s must be a string or null" % key)
    errors.extend(_validate_inputs(plan.get("inputs")))
    rules = plan.get("rules")
    if not isinstance(rules, dict):
        errors.append("rules must be an object")
    else:
        for key, _relative in RULE_FILES:
            if not _is_sha(rules.get(key)):
                errors.append("rules.%s must be a SHA-256 hex string"
                              % key)
        for key in rules:
            if key not in {name for name, _rel in RULE_FILES}:
                errors.append("rules has unknown key %r" % (key,))
    if plan.get("unlock_state") not in (
            "already_unlocked", "allowed_now", "not_allowed_now",
            "no_official_path", "unknown"):
        errors.append("unlock_state %r is not known"
                      % (plan.get("unlock_state"),))
    errors.extend(_validate_strategies(plan.get("strategies")))
    errors.extend(_validate_fit(plan.get("fit")))
    result = plan.get("selection_result")
    if result not in RESULTS:
        errors.append("selection_result must be one of %s"
                      % ("/".join(RESULTS),))
    allowed = plan.get("build_allowed")
    if allowed not in BUILD_ALLOWED:
        errors.append("build_allowed must be one of %s"
                      % ("/".join(BUILD_ALLOWED),))
    if result == "refused" and allowed != "none":
        errors.append("a refused plan must pair with build_allowed none")
    if result in ("fits", "unconfirmed") and allowed != "emulator_only":
        errors.append("a %s plan must pair with build_allowed "
                      "emulator_only" % result)
    if isinstance(plan.get("fit"), dict) and \
            plan["fit"].get("result") != result:
        errors.append("fit.result disagrees with selection_result")
    status = plan.get("explanation_status")
    if not isinstance(status, dict):
        errors.append("explanation_status must be an object")
    else:
        if status.get("status") not in EXPLANATION_STATUSES:
            errors.append("explanation_status.status must be one of %s"
                          % ("/".join(EXPLANATION_STATUSES),))
        code = status.get("code")
        if status.get("status") == "failed" and code not in (2, 5):
            errors.append("a failed explanation needs code 2 or 5")
        if status.get("status") != "failed" and code is not None:
            errors.append("explanation code must be null unless failed")
        model = status.get("model")
        if model is not None and not isinstance(model, str):
            errors.append("explanation_status.model must be a string")
        reason = status.get("reason")
        if status.get("status") == "incomplete":
            if not isinstance(reason, str) or not reason:
                errors.append("an incomplete explanation needs a reason")
        elif reason is not None:
            errors.append("explanation reason must be null unless "
                          "incomplete")
    errors.extend(_validate_notes(plan.get("notes"),
                                  plan.get("explanation_status")))
    errors.extend(_validate_handoff(plan.get("handoff"), result, allowed))
    if isinstance(plan.get("fit"), dict) \
            and isinstance(plan.get("handoff"), dict):
        if plan["handoff"].get("reasons") != plan["fit"].get("reasons"):
            errors.append("handoff reasons must match the top-level "
                          "reasons exactly")
    if plan.get("provenance") not in OVERALL_PROVENANCE:
        errors.append("provenance must be one of %s"
                      % ("/".join(OVERALL_PROVENANCE),))
    for required in TOP_LEVEL_KEYS:
        if required not in plan:
            errors.append("plan is missing %r" % required)
    return errors


def _validate_inputs(inputs: object) -> list:
    if not isinstance(inputs, dict):
        return ["inputs must be an object"]
    errors = []
    for key in inputs:
        if key not in INPUT_KEYS:
            errors.append("inputs has unknown key %r" % (key,))
    for key in ("profile", "catalog"):
        entry = inputs.get(key)
        if not isinstance(entry, dict):
            errors.append("inputs.%s must be an object" % key)
            continue
        if not _is_sha(entry.get("sha256")):
            errors.append("inputs.%s.sha256 must be SHA-256 hex" % key)
        if not isinstance(entry.get("provenance"), str):
            errors.append("inputs.%s.provenance is required" % key)
    research = inputs.get("research")
    if research is not None:
        if not isinstance(research, dict):
            errors.append("inputs.research must be an object or null")
        else:
            if not _is_sha(research.get("sha256")):
                errors.append("inputs.research.sha256 must be SHA-256 hex")
            if not isinstance(research.get("provenance"), str):
                errors.append("inputs.research.provenance is required")
    return errors


def _validate_strategies(strategies: object) -> list:
    if not isinstance(strategies, list) or not strategies:
        return ["strategies must be a non-empty list"]
    errors = []
    seen = set()
    for index, entry in enumerate(strategies):
        label = "strategies[%d]" % index
        if not isinstance(entry, dict):
            errors.append("%s: must be an object" % label)
            continue
        strategy_id = entry.get("id")
        if strategy_id not in strategy_module.STRATEGIES:
            errors.append("%s: unknown strategy %r" % (label, strategy_id))
        if strategy_id in seen:
            errors.append("%s: duplicate strategy %r" % (label,
                                                         strategy_id))
        seen.add(strategy_id)
        if entry.get("status") not in STRATEGY_STATUSES:
            errors.append("%s: bad status" % label)
        if not isinstance(entry.get("met"), list):
            errors.append("%s: met must be a list" % label)
        blocker = entry.get("blocker")
        if entry.get("status") == "blocked":
            if not isinstance(blocker, dict):
                errors.append("%s: blocked needs a blocker" % label)
            else:
                if not isinstance(blocker.get("fact"), str):
                    errors.append("%s: blocker.fact required" % label)
                if blocker.get("blocker_kind") not in BLOCKER_KINDS:
                    errors.append("%s: bad blocker_kind" % label)
        elif blocker is not None:
            errors.append("%s: blocker must be null unless blocked"
                          % label)
        missing = entry.get("missing")
        if not isinstance(missing, list):
            errors.append("%s: missing must be a list" % label)
        else:
            for item in missing:
                if not isinstance(item, dict) \
                        or not isinstance(item.get("fact"), str) \
                        or not isinstance(item.get("reason"), str):
                    errors.append("%s: missing needs fact and reason"
                                  % label)
        for key in ("open_checks", "risks"):
            items = entry.get(key)
            if not isinstance(items, list) or any(
                    not isinstance(item, str) for item in items):
                errors.append("%s: %s must be a list of strings"
                              % (label, key))
        if not isinstance(entry.get("signing"), str):
            errors.append("%s: signing is required" % label)
    if not errors and [entry["id"] for entry in strategies] != [
            entry["id"] for entry in sorted(
                strategies, key=strategy_module._rank_key)]:
        errors.append("strategies are not in ranked order")
    return errors


def _validate_fit(fit_result: object) -> list:
    if not isinstance(fit_result, dict):
        return ["fit must be an object"]
    errors = []
    services = fit_result.get("services")
    if not isinstance(services, list) or not services or any(
            item not in fit_module.SERVICES for item in services):
        errors.append("fit.services must be a non-empty known selection")
    if fit_result.get("result") not in RESULTS:
        errors.append("fit.result must be one of %s"
                      % ("/".join(RESULTS),))
    if not isinstance(fit_result.get("estimated"), bool):
        errors.append("fit.estimated must be a boolean")
    for label, checks in (("fit.checks", fit_result.get("checks")),
                          ("fit.models", fit_result.get("models"))):
        if label == "fit.checks":
            if not isinstance(checks, list):
                errors.append("fit.checks must be a list")
            else:
                for index, check in enumerate(checks):
                    errors.extend(_check_errors(
                        "fit.checks[%d]" % index, check))
        else:
            if not isinstance(checks, list):
                errors.append("fit.models must be a list")
            else:
                for index, evaluated in enumerate(checks):
                    entry_label = "fit.models[%d]" % index
                    if not isinstance(evaluated, dict):
                        errors.append("%s: must be an object"
                                      % entry_label)
                        continue
                    if not isinstance(evaluated.get("id"), str):
                        errors.append("%s: id required" % entry_label)
                    if evaluated.get("verdict") not in (
                            "fits", "does_not_fit", "unknown"):
                        errors.append("%s: bad verdict" % entry_label)
                    model_checks = evaluated.get("checks")
                    if not isinstance(model_checks, list):
                        errors.append("%s: checks must be a list"
                                      % entry_label)
                    else:
                        for check_index, check in enumerate(model_checks):
                            errors.extend(_check_errors(
                                "%s.checks[%d]" % (entry_label,
                                                  check_index), check))
    choice = fit_result.get("model_choice")
    if not isinstance(choice, dict):
        errors.append("fit.model_choice must be an object")
    else:
        if choice.get("status") not in CHOICE_STATUSES:
            errors.append("fit.model_choice.status is not known")
        model_id = choice.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            errors.append("fit.model_choice.model_id must be a string")
        if choice.get("status") == "chosen" and not model_id:
            errors.append("a chosen model needs its id")
        if choice.get("status") != "chosen" and model_id is not None:
            errors.append("model_id must be null unless chosen")
        reasons = choice.get("reasons")
        if not isinstance(reasons, list) or any(
                not isinstance(item, str) for item in reasons):
            errors.append("fit.model_choice.reasons must be strings")
    reasons = fit_result.get("reasons")
    if not isinstance(reasons, list) or any(
            not isinstance(item, str) for item in reasons):
        errors.append("fit.reasons must be a list of strings")
    return errors


def _check_errors(label: str, check: object) -> list:
    if not isinstance(check, dict):
        return ["%s: must be an object" % label]
    errors = []
    if not isinstance(check.get("name"), str):
        errors.append("%s: name required" % label)
    if check.get("status") not in CHECK_STATUSES:
        errors.append("%s: bad status" % label)
    if not isinstance(check.get("fact"), str):
        errors.append("%s: fact required" % label)
    if not isinstance(check.get("reason"), str):
        errors.append("%s: reason required" % label)
    return errors


def _validate_notes(notes: object, status: object) -> list:
    state = status.get("status") if isinstance(status, dict) else None
    if state not in ("ok", "incomplete"):
        if notes is not None:
            return ["notes must be null unless the explanation is ok "
                    "or incomplete"]
        return []
    if not isinstance(notes, dict):
        return ["notes must be an object when the explanation is %s"
                % state]
    errors = []
    if not isinstance(notes.get("text"), str):
        errors.append("notes.text must be a string")
    for key in ("cited_ids", "missing_ids"):
        items = notes.get(key)
        if not isinstance(items, list) or any(
                not isinstance(item, str) for item in items):
            errors.append("notes.%s must be a list of strings" % key)
    expected = NOTES_LABEL if state == "ok" else NOTES_LABEL_INCOMPLETE
    if notes.get("label") != expected:
        errors.append("notes.label must be %r" % expected)
    return errors


def _validate_handoff(handoff: object, result: object,
                      allowed: object) -> list:
    if not isinstance(handoff, dict):
        return ["handoff must be an object"]
    errors = []
    services = handoff.get("services")
    if not isinstance(services, list) or not services or any(
            item not in fit_module.SERVICES for item in services):
        errors.append("handoff.services must be a known selection")
    model = handoff.get("model")
    if model is not None:
        if not isinstance(model, dict):
            errors.append("handoff.model must be an object or null")
        else:
            for key in ("id", "source", "revision", "format", "runtime"):
                item = model.get(key)
                if item is not None and not isinstance(item, str):
                    errors.append("handoff.model.%s must be a string"
                                  % key)
            if not _is_non_empty_str(model.get("id")):
                errors.append("handoff.model.id is required")
            files = model.get("files")
            if not isinstance(files, list) or not files:
                errors.append("handoff.model.files must be non-empty")
            else:
                for index, item in enumerate(files):
                    if not isinstance(item, dict) \
                            or not _is_non_empty_str(item.get("name")):
                        errors.append("handoff.model.files[%d]: bad file"
                                      % index)
                    size = item.get("bytes") if isinstance(item, dict) \
                        else None
                    if not isinstance(size, int) \
                            or isinstance(size, bool) or size < 0:
                        errors.append("handoff.model.files[%d]: bytes "
                                      "must be whole" % index)
            total = model.get("total_bytes")
            if not isinstance(total, int) or isinstance(total, bool) \
                    or total < 0:
                errors.append("handoff.model.total_bytes must be whole")
    if not isinstance(handoff.get("model_reason"), str):
        errors.append("handoff.model_reason is required")
    reasons = handoff.get("reasons")
    if not isinstance(reasons, list) or any(
            not isinstance(item, str) for item in reasons):
        errors.append("handoff.reasons must be a list of strings")
    candidates = handoff.get("candidate_strategies")
    if not isinstance(candidates, list) or any(
            item not in strategy_module.STRATEGIES for item in candidates):
        errors.append("handoff.candidate_strategies must list strategies")
    unresolved = handoff.get("unresolved")
    if not isinstance(unresolved, list) or not unresolved:
        errors.append("handoff.unresolved must be a non-empty list")
    else:
        for index, item in enumerate(unresolved):
            if not isinstance(item, dict) \
                    or not isinstance(item.get("area"), str) \
                    or not isinstance(item.get("detail"), str):
                errors.append("handoff.unresolved[%d]: needs area and "
                              "detail" % index)
    if handoff.get("selection_result") != result:
        errors.append("handoff.selection_result disagrees with the plan")
    if handoff.get("build_allowed") != allowed:
        errors.append("handoff.build_allowed disagrees with the plan")
    provenance = handoff.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("handoff.provenance must be an object")
    else:
        for key in ("profile", "catalog"):
            if not isinstance(provenance.get(key), str):
                errors.append("handoff.provenance.%s required" % key)
        research_prov = provenance.get("research")
        if research_prov is not None \
                and not isinstance(research_prov, str):
            errors.append("handoff.provenance.research must be a string")
    return errors


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_file(path: str):
    """Validate a plan-v1 file. Returns (ok, errors)."""
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        return False, ["cannot read plan: %s" % exc]
    errors = validate_plan(plan)
    return (not errors), errors


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m stargate.plan validate <file>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "validate":
        print("usage: python -m stargate.plan validate <file>",
              file=sys.stderr)
        return 2
    ok, errors = validate_file(args[0 + 1])
    if ok:
        print("plan is valid")
        return 0
    for error in errors:
        print("error: %s" % error, file=sys.stderr)
    return 6


if __name__ == "__main__":
    sys.exit(main())
