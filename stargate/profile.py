"""M1 device profile (profile-v1): authoritative validator.

This module is the authoritative validator for profile-v1. The JSON Schema
file at schemas/profile-v1.json is descriptive and kept consistent with
this module, but this module is what tests and `stargate scan --json`
validate against (standard library only, no JSON-schema library).
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

from .probe import ALLOWLIST_SET

SCHEMA_VERSION = "profile-v1"

STATUSES = ("measured", "inferred", "unknown", "conflicting")
UNKNOWN_REASONS = (
    "not_exposed",
    "property_missing",
    "permission_denied",
    "timeout",
    "output_too_large",
    "malformed_output",
    "not_probed_in_m1",
    "deferred",
)
UNITS = ("bytes", "count")

VERIFIED_BOOT_STATES = ("green", "yellow", "orange", "red")
KERNEL_CONFIG_STATES = ("built_in", "module", "disabled")

TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "simulated", "fixture_name", "created_at", "facts"}
)
FACT_KEYS = frozenset(
    {
        "status",
        "value",
        "unit",
        "evidence",
        "unknown_reason",
        "inference",
        "candidates",
    }
)
EVIDENCE_KEYS = frozenset({"probe_id", "command", "excerpt", "observed_at"})
INFERENCE_KEYS = frozenset({"rule_id", "inputs"})
CANDIDATE_KEYS = frozenset({"value", "evidence"})

MAX_EXCERPT_CHARS = 200

# fact_id -> {"kind": ..., "unit": ...}
# kinds: string, integer, boolean, enum, list_of_strings
FACT_SPECS: dict[str, dict] = {
    "identity.manufacturer": {"kind": "string", "unit": None},
    "identity.model": {"kind": "string", "unit": None},
    "identity.device_codename": {"kind": "string", "unit": None},
    "identity.android_release": {"kind": "string", "unit": None},
    "identity.sdk_level": {"kind": "integer", "unit": None, "min": 1},
    "identity.platform": {"kind": "string", "unit": None},
    "boot.bootloader_locked": {"kind": "boolean", "unit": None},
    "boot.oem_unlock_supported": {"kind": "boolean", "unit": None},
    "boot.oem_unlock_allowed": {"kind": "boolean", "unit": None},
    "boot.verified_boot_state": {
        "kind": "enum",
        "values": VERIFIED_BOOT_STATES,
        "unit": None,
    },
    "boot.ab_slots": {"kind": "boolean", "unit": None},
    "boot.dynamic_partitions": {"kind": "boolean", "unit": None},
    "boot.treble_enabled": {"kind": "boolean", "unit": None},
    "boot.vndk_version": {"kind": "string", "unit": None},
    "resources.ram_total": {"kind": "integer", "unit": "bytes", "min": 0},
    "resources.data_free": {"kind": "integer", "unit": "bytes", "min": 0},
    "resources.cpu_abis": {"kind": "list_of_strings", "unit": None},
    "resources.cpu_cores": {"kind": "integer", "unit": "count", "min": 1},
    "kernel.release": {"kind": "string", "unit": None},
    "kernel.config.tun": {
        "kind": "enum",
        "values": KERNEL_CONFIG_STATES,
        "unit": None,
    },
    "kernel.config.overlay_fs": {
        "kind": "enum",
        "values": KERNEL_CONFIG_STATES,
        "unit": None,
    },
    "kernel.config.ext4": {
        "kind": "enum",
        "values": KERNEL_CONFIG_STATES,
        "unit": None,
    },
    "kernel.config.exfat": {
        "kind": "enum",
        "values": KERNEL_CONFIG_STATES,
        "unit": None,
    },
    "capacity.system_image_max": {"kind": "integer", "unit": "bytes", "min": 0},
    "runtime.tun_usable": {"kind": "boolean", "unit": None},
    "runtime.overlay_fs_usable": {"kind": "boolean", "unit": None},
    "runtime.ext4_usable": {"kind": "boolean", "unit": None},
    "runtime.exfat_usable": {"kind": "boolean", "unit": None},
}

ALL_FACT_IDS = tuple(sorted(FACT_SPECS))

# Single source of known inference rule IDs. docs/inference-rules.md lists
# the same IDs; a test keeps the two in sync.
KNOWN_INFERENCE_RULES = (
    "example-platform-from-codename-v1",
    "R-LOCK-FROM-VBS",
)


def _is_utc_iso8601(text: object) -> bool:
    """Check for a UTC ISO 8601 timestamp (requires T and Z/+00:00)."""
    if not isinstance(text, str) or "T" not in text:
        return False
    raw = text.strip()
    if raw.endswith("Z"):
        candidate = raw[:-1] + "+00:00"
    elif raw.endswith("+00:00"):
        candidate = raw
    else:
        return False
    try:
        moment = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        return False
    if moment.tzinfo is None:
        return False
    try:
        offset = moment.utcoffset()
    except Exception:
        return False
    return offset == datetime.timedelta(0)


def _value_errors(fact_id: str, value: object) -> list[str]:
    """Check a non-null value against the fact's declared type."""
    spec = FACT_SPECS[fact_id]
    kind = spec["kind"]
    if kind == "string":
        if not isinstance(value, str) or value.strip() == "":
            return ["value must be a non-empty string"]
        return []
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return ["value must be an integer (whole bytes/count, not a string)"]
        minimum = int(spec.get("min", 0))
        if value < minimum:
            return ["value must be >= %d" % minimum]
        return []
    if kind == "boolean":
        if not isinstance(value, bool):
            return ["value must be a boolean"]
        return []
    if kind == "enum":
        allowed = spec["values"]
        if not isinstance(value, str) or value not in allowed:
            return ["value must be one of %s" % ("/".join(allowed))]
        return []
    if kind == "list_of_strings":
        if not isinstance(value, list) or not value:
            return ["value must be a non-empty list of strings"]
        for entry in value:
            if not isinstance(entry, str) or entry.strip() == "":
                return ["value must be a non-empty list of strings"]
        return []
    return ["unknown kind for %s" % fact_id]


def validate_evidence(item: object) -> list[str]:
    """Validate one evidence item; returns a list of error strings."""
    if not isinstance(item, dict):
        return ["evidence item must be an object"]
    errors: list[str] = []
    for key in item:
        if key not in EVIDENCE_KEYS:
            errors.append("evidence has unknown key %r" % (key,))
    probe_id = item.get("probe_id")
    if not isinstance(probe_id, str) or probe_id.strip() == "":
        errors.append("evidence.probe_id must be a non-empty string")
    command = item.get("command")
    if not isinstance(command, str) or command.strip() == "":
        errors.append("evidence.command must be a non-empty string")
    elif command not in ALLOWLIST_SET:
        errors.append("evidence.command must be exactly on the probe allowlist")
    excerpt = item.get("excerpt")
    if not isinstance(excerpt, str):
        errors.append("evidence.excerpt must be a string")
    else:
        if len(excerpt) > MAX_EXCERPT_CHARS:
            errors.append(
                "evidence.excerpt must be at most %d characters"
                % MAX_EXCERPT_CHARS
            )
        if "\n" in excerpt or "\r" in excerpt:
            errors.append("evidence.excerpt must be a single line")
    observed_at = item.get("observed_at")
    if not _is_utc_iso8601(observed_at):
        errors.append("evidence.observed_at must be UTC ISO 8601")
    for required in ("probe_id", "command", "excerpt", "observed_at"):
        if required not in item:
            errors.append("evidence is missing %r" % required)
    return errors


def _normalise_for_distinct(value: object) -> object:
    """Normalise a candidate value for the distinctness check."""
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", int(value))
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, list):
        return ("list", tuple(value))
    return ("other", repr(value))


def validate_fact(fact_id: str, fact: object) -> list[str]:
    """Validate one fact record against its declared type."""
    prefix = fact_id
    if fact_id not in FACT_SPECS:
        return ["%s: unknown fact ID" % prefix]
    if not isinstance(fact, dict):
        return ["%s: fact must be an object" % prefix]
    errors: list[str] = []
    for key in fact:
        if key not in FACT_KEYS:
            errors.append("%s: unknown key %r" % (prefix, key))
    status = fact.get("status")
    if status not in STATUSES:
        errors.append(
            "%s: status must be one of %s" % (prefix, "/".join(STATUSES))
        )
        return errors
    if "value" not in fact:
        errors.append("%s: value is required" % prefix)
        return errors
    value = fact.get("value")
    expected_unit = FACT_SPECS[fact_id]["unit"]
    if "unit" not in fact:
        errors.append("%s: unit is required" % prefix)
    elif fact.get("unit") != expected_unit:
        errors.append(
            "%s: unit must be %r" % (prefix, expected_unit)
        )
    if status in ("unknown", "conflicting"):
        if value is not None:
            errors.append(
                "%s: value must be null when %s" % (prefix, status)
            )
    else:
        if value is None:
            errors.append(
                "%s: value must not be null when %s" % (prefix, status)
            )
        else:
            for message in _value_errors(fact_id, value):
                errors.append("%s: %s" % (prefix, message))
    evidence = fact.get("evidence")
    if status == "measured":
        if not isinstance(evidence, list) or not evidence:
            errors.append(
                "%s: measured facts need at least one evidence item" % prefix
            )
        else:
            for index, item in enumerate(evidence):
                for message in validate_evidence(item):
                    errors.append("%s.evidence[%d]: %s" % (prefix, index, message))
    elif status == "conflicting":
        if "evidence" in fact:
            errors.append(
                "%s: conflicting facts keep evidence on candidates, "
                "not at the top level" % prefix
            )
    else:
        if "evidence" in fact:
            if not isinstance(evidence, list):
                errors.append("%s: evidence must be a list" % prefix)
            else:
                for index, item in enumerate(evidence):
                    for message in validate_evidence(item):
                        errors.append(
                            "%s.evidence[%d]: %s" % (prefix, index, message)
                        )
    if status == "unknown":
        reason = fact.get("unknown_reason")
        if reason not in UNKNOWN_REASONS:
            errors.append(
                "%s: unknown_reason must be one of %s"
                % (prefix, "/".join(UNKNOWN_REASONS))
            )
    elif "unknown_reason" in fact:
        errors.append(
            "%s: unknown_reason must be absent unless unknown" % prefix
        )
    if status == "inferred":
        inference = fact.get("inference")
        if not isinstance(inference, dict):
            errors.append("%s: inference is required when inferred" % prefix)
        else:
            for key in inference:
                if key not in INFERENCE_KEYS:
                    errors.append("%s: inference has unknown key %r" % (prefix, key))
            rule_id = inference.get("rule_id")
            if not isinstance(rule_id, str) or rule_id.strip() == "":
                errors.append("%s: inference.rule_id must be a string" % prefix)
            elif rule_id not in KNOWN_INFERENCE_RULES:
                errors.append(
                    "%s: inference.rule_id %r is not a known rule"
                    % (prefix, rule_id)
                )
            inputs = inference.get("inputs")
            if not isinstance(inputs, list):
                errors.append("%s: inference.inputs must be a list" % prefix)
            else:
                for entry in inputs:
                    if entry not in FACT_SPECS:
                        errors.append(
                            "%s: inference input %r is not a known fact ID"
                            % (prefix, entry)
                        )
            for required in ("rule_id", "inputs"):
                if required not in inference:
                    errors.append("%s: inference is missing %r" % (prefix, required))
    elif "inference" in fact:
        errors.append("%s: inference must be absent unless inferred" % prefix)
    if status == "conflicting":
        candidates = fact.get("candidates")
        if not isinstance(candidates, list) or len(candidates) < 2:
            errors.append(
                "%s: conflicting facts need at least two candidates" % prefix
            )
        else:
            seen: list[object] = []
            for index, candidate in enumerate(candidates):
                label = "%s.candidates[%d]" % (prefix, index)
                if not isinstance(candidate, dict):
                    errors.append("%s: candidate must be an object" % label)
                    continue
                for key in candidate:
                    if key not in CANDIDATE_KEYS:
                        errors.append("%s: unknown key %r" % (label, key))
                if "value" not in candidate or "evidence" not in candidate:
                    errors.append(
                        "%s: candidate needs value and evidence" % label
                    )
                    continue
                candidate_value = candidate.get("value")
                if candidate_value is None:
                    errors.append("%s: candidate value must not be null" % label)
                else:
                    for message in _value_errors(fact_id, candidate_value):
                        errors.append("%s: %s" % (label, message))
                    seen.append(_normalise_for_distinct(candidate_value))
                candidate_evidence = candidate.get("evidence")
                if (
                    not isinstance(candidate_evidence, list)
                    or not candidate_evidence
                ):
                    errors.append(
                        "%s: candidate needs at least one evidence item" % label
                    )
                else:
                    for evidence_index, item in enumerate(candidate_evidence):
                        for message in validate_evidence(item):
                            errors.append(
                                "%s.evidence[%d]: %s"
                                % (label, evidence_index, message)
                            )
            if len(seen) != len(set(seen)):
                errors.append(
                    "%s: candidate values must be distinct" % prefix
                )
    elif "candidates" in fact:
        errors.append("%s: candidates must be absent unless conflicting" % prefix)
    return errors


def validate_profile(profile: object) -> list[str]:
    """Validate a complete profile; returns a list of error strings."""
    if not isinstance(profile, dict):
        return ["profile must be an object"]
    errors: list[str] = []
    for key in profile:
        if key not in TOP_LEVEL_KEYS:
            errors.append("profile has unknown key %r" % (key,))
    if profile.get("schema_version") != SCHEMA_VERSION:
        errors.append('schema_version must be "%s"' % SCHEMA_VERSION)
    if not isinstance(profile.get("simulated"), bool):
        errors.append("simulated must be a boolean")
    fixture_name = profile.get("fixture_name")
    if fixture_name is not None and not isinstance(fixture_name, str):
        errors.append("fixture_name must be a string or null")
    if not _is_utc_iso8601(profile.get("created_at")):
        errors.append("created_at must be UTC ISO 8601")
    facts = profile.get("facts")
    if not isinstance(facts, dict) or not facts:
        errors.append("facts must be a non-empty object")
        return errors
    for fact_id, fact in facts.items():
        errors.extend(validate_fact(fact_id, fact))
    for required in (
        "schema_version",
        "simulated",
        "fixture_name",
        "created_at",
        "facts",
    ):
        if required not in profile:
            errors.append("profile is missing %r" % required)
    return errors


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def validate_file(path: str | Path) -> tuple[bool, list[str]]:
    """Validate one JSON file on disk as a profile."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        return False, ["cannot read %s: %s" % (target, exc)]
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return False, ["%s is not valid JSON: %s" % (target, exc)]
    errors = validate_profile(payload)
    return (not errors), errors


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m stargate.profile validate <file> [more files...]"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "validate":
        print(
            "usage: python -m stargate.profile validate <file> [more files...]",
            file=sys.stderr,
        )
        return 2
    failed = False
    for name in args[1:]:
        ok, errors = validate_file(name)
        if ok:
            print("%s: valid %s" % (name, SCHEMA_VERSION))
        else:
            failed = True
            print("%s: INVALID %s" % (name, SCHEMA_VERSION))
            for message in errors[:20]:
                print("  - %s" % message)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
