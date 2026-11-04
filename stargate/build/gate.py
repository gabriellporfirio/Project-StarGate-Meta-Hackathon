"""`stargate build` plan gate and Assistant manifest binding (M4).

Section 1 runs before any build step, in order: full plan-v1
validation (exit 6); handoff vs top level, including handoff.reasons
equal to fit.reasons exactly (exit 6); build only when build_allowed
is emulator_only, else exit 7 with the refusal reasons; an Assistant
with no resolved model gives exit 9. Section 5a then binds the
approved assistant manifest to the plan's handoff (exit 9 on any
mismatch), still before any build step.

The actual build defaults to the Route A image builder; tests inject
a stub (plan, out_dir, labels, ctx) that must never be called on a
gate failure. ctx carries the plan path and SHA-256 plus the bound
Assistant manifest (or None without the Assistant).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import plan as plan_module
from . import image as image_module

EXIT_OK = 0
EXIT_INVALID_PLAN = 6
EXIT_REFUSED = 7
EXIT_BUILD_FAILED = 8
EXIT_UNRESOLVED_MODEL = 9

SHA_RE = re.compile(r"[0-9a-f]{64}\Z")

ASSISTANT_MANIFEST_NAME = "assistant-manifest.json"

UNCONFIRMED_LABEL = "emulator experiment: fit not confirmed"


def repo_root() -> Path:
    """Repository root, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent.parent


def default_manifest_path() -> Path:
    """Where the approved Assistant manifest is committed (M4b)."""
    return repo_root() / ASSISTANT_MANIFEST_NAME


def load_plan_file(path: str):
    """Read and parse a plan file. Returns (plan_or_None, error_or_None)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return None, "cannot read plan: %s" % exc
    try:
        parsed = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        return None, "plan is not valid JSON: %s" % exc
    return parsed, None


def gate_plan(plan: object):
    """Section 1 gate. Returns (ok, exit_code, message).

    Checks, in order: whole-file plan-v1 validation; handoff vs top
    level (selection result, build_allowed, reasons exactly); the
    build_allowed value; the Assistant's resolved model. Every failure
    returns before any build step.
    """
    errors = plan_module.validate_plan(plan)
    if errors:
        return False, EXIT_INVALID_PLAN, \
            "error: invalid plan: %s\n" % errors[0]
    handoff = plan["handoff"]
    if handoff["selection_result"] != plan["selection_result"]:
        return False, EXIT_INVALID_PLAN, \
            "error: handoff.selection_result disagrees with the plan\n"
    if handoff["build_allowed"] != plan["build_allowed"]:
        return False, EXIT_INVALID_PLAN, \
            "error: handoff.build_allowed disagrees with the plan\n"
    if handoff["reasons"] != plan["fit"]["reasons"]:
        return False, EXIT_INVALID_PLAN, \
            "error: handoff.reasons differs from fit.reasons\n"
    if plan["build_allowed"] != "emulator_only":
        lines = ["error: plan does not allow a build "
                 "(build_allowed=%s)" % plan["build_allowed"]]
        lines.extend("reason: %s" % reason
                     for reason in handoff["reasons"])
        return False, EXIT_REFUSED, "\n".join(lines) + "\n"
    if "assistant" in handoff["services"] \
            and model_unresolved(handoff["model"]):
        return False, EXIT_UNRESOLVED_MODEL, \
            "error: assistant is selected but the handoff has no " \
            "resolved model: add a concrete catalog entry and make " \
            "a fresh M3 plan\n"
    return True, EXIT_OK, ""


def model_unresolved(model: object) -> bool:
    """True when the handoff model is missing, null or unresolved."""
    if not isinstance(model, dict):
        return True
    if not isinstance(model.get("id"), str) or not model["id"].strip():
        return True
    files = model.get("files")
    if not isinstance(files, list) or not files:
        return True
    for item in files:
        if not isinstance(item, dict):
            return True
        if not isinstance(item.get("name"), str) or not item["name"]:
            return True
        size = item.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) \
                or size < 0:
            return True
    return False


def load_manifest_file(path) -> tuple:
    """Read and parse an Assistant manifest. Returns (manifest, error)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return None, "cannot read assistant manifest: %s" % exc
    try:
        parsed = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        return None, "assistant manifest is not valid JSON: %s" % exc
    if not isinstance(parsed, dict):
        return None, "assistant manifest must be an object"
    return parsed, None


def bind_assistant_manifest(model: dict, manifest: dict) -> list:
    """Section 5a binding. Returns a list of mismatch strings.

    The manifest's model ID and source revision must equal the
    handoff's, the file set must match (same names, same sizes), and
    every file must carry a SHA-256. Unknown manifest keys are
    ignored: M4b's handover carries more than the binding needs.
    """
    errors = []
    if manifest.get("model_id") != model.get("id"):
        errors.append("assistant manifest model_id %r does not match "
                      "the handoff model %r"
                      % (manifest.get("model_id"), model.get("id")))
    if manifest.get("source_revision") != model.get("revision"):
        errors.append("assistant manifest source_revision %r does not "
                      "match the handoff revision %r"
                      % (manifest.get("source_revision"),
                         model.get("revision")))
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("assistant manifest files must be a non-empty list")
        return errors
    wanted = {}
    for item in model.get("files", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            wanted[item["name"]] = item.get("bytes")
    seen: dict = {}
    for index, item in enumerate(files):
        label = "assistant manifest files[%d]" % index
        if not isinstance(item, dict):
            errors.append("%s: must be an object" % label)
            continue
        name = item.get("name")
        size = item.get("bytes")
        digest = item.get("sha256")
        if not isinstance(name, str) or not name:
            errors.append("%s: name is required" % label)
            continue
        if name in seen:
            errors.append("%s: duplicate file %r" % (label, name))
        seen[name] = size
        if size != wanted.get(name):
            errors.append("%s: bytes %r does not match the handoff "
                          "(%r)" % (label, size, wanted.get(name)))
        if not isinstance(digest, str) or SHA_RE.match(digest) is None:
            errors.append("%s: sha256 is required" % label)
    for name in wanted:
        if name not in seen:
            errors.append("assistant manifest is missing handoff file "
                          "%r" % (name,))
    for name in seen:
        if name not in wanted:
            errors.append("assistant manifest has extra file %r not in "
                          "the handoff" % (name,))
    return errors


def labels_for_plan(plan: dict) -> list:
    """Every output label for this plan (section 1; route adds more)."""
    if plan["selection_result"] == "unconfirmed":
        return [UNCONFIRMED_LABEL]
    return []


def run_build(plan_path: str, out_dir: str,
              assistant_manifest_path=None, build_fn=None,
              inputs=None):
    """Run stargate build. Returns (exit_code, stdout, stderr).

    The section 1 gate and the section 5a binding run before any
    build step; build_fn (default: the Route A image builder) runs
    only when they pass. Nothing is written on a gate failure.
    inputs overrides ctx entries (stock and APK paths) for tests.
    """
    plan, error = load_plan_file(plan_path)
    if error is not None:
        return EXIT_INVALID_PLAN, "", "error: %s\n" % error
    with open(plan_path, "rb") as handle:
        plan_sha256 = hashlib.sha256(handle.read()).hexdigest()
    ok, exit_code, message = gate_plan(plan)
    if not ok:
        return exit_code, "", message
    handoff = plan["handoff"]
    assistant_manifest = None
    assistant_manifest_sha = None
    if "assistant" in handoff["services"]:
        manifest_path = assistant_manifest_path \
            if assistant_manifest_path is not None \
            else str(default_manifest_path())
        assistant_manifest, error = load_manifest_file(manifest_path)
        if error is not None:
            return EXIT_UNRESOLVED_MODEL, "", "error: %s\n" % error
        mismatches = bind_assistant_manifest(handoff["model"],
                                             assistant_manifest)
        if mismatches:
            return EXIT_UNRESOLVED_MODEL, "", \
                "error: %s\n" % mismatches[0]
        with open(manifest_path, "rb") as handle:
            assistant_manifest_sha = hashlib.sha256(
                handle.read()).hexdigest()
    labels = labels_for_plan(plan)
    ctx = {"plan_path": plan_path, "plan_sha256": plan_sha256,
           "assistant_manifest": assistant_manifest,
           "assistant_manifest_sha256": assistant_manifest_sha,
           "assistant_manifest_path": assistant_manifest_path}
    if inputs:
        ctx.update(inputs)
    build = build_fn if build_fn is not None \
        else image_module.build_route_a
    return build(plan, out_dir, labels, ctx)
