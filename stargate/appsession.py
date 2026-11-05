"""One session record for the guided app (M6 Part 1, spec 1a).

The record holds the current profile, answers and selection, each with its
SHA-256, plus every output the session made. Each output records the hashes
of the inputs it was made from; a screen shows an output only when those
equal the current hashes, otherwise "out of date" with a re-run button.

Staleness is derived, never flagged: changing the profile, answers or
selection automatically makes every later output stale, because its recorded
input hashes no longer match. Details views show the output hash and its
input hashes.

Jobs have IDs. A result that arrives for an older job (its recorded input
hashes differ from the current ones, or a newer job exists for that step)
is discarded and logged, never shown.
"""
from __future__ import annotations

import hashlib
import json

OUT_OF_DATE = "out of date"

# Volatile M1 fields that must not affect input hashes: two scans of the
# same simulated phone differ only in these timestamps.
VOLATILE_KEYS = frozenset({"created_at", "observed_at"})


def canonical_bytes(obj) -> bytes:
    """Canonical JSON bytes for hashing (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def strip_volatile(obj):
    """Copy of obj without volatile timestamp keys (recursive)."""
    if isinstance(obj, dict):
        return {key: strip_volatile(value)
                for key, value in obj.items()
                if key not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(item) for item in obj]
    return obj


def hash_value(obj) -> str:
    """Stable hash of a session input (volatile keys excluded)."""
    return sha256_hex(canonical_bytes(strip_volatile(obj)))


def m5_style_hash(obj) -> str:
    """M5's input byte hashing, reproduced (P7, no M5 import).

    stargate.install records input hashes as SHA-256 over the input
    FILE bytes, and those files are json.dumps(obj, indent=2) plus one
    trailing newline (verified byte-identical against the budget v3
    profile and plan files). Volatile timestamps are included, exactly
    as M5 hashed them, so fresh scans legitimately differ from old
    records: that mismatch honestly shows "out of date".
    """
    raw = (json.dumps(obj, indent=2, ensure_ascii=True) + "\n").encode(
        "utf-8")
    return sha256_hex(raw)


def current_input_hashes(record: dict) -> dict:
    """Hashes of the current inputs: profile, answers, selection, research.

    Research is always None in Part 1: no M2 replay has been recorded yet,
    and a replay is never bound or shown without its matching rule.
    """
    hashes = {}
    for key in ("profile", "answers", "selection"):
        entry = record.get(key)
        hashes[key] = entry["sha256"] if entry is not None else None
    hashes["research"] = None
    return hashes


def new_record() -> dict:
    """A fresh session record: inputs empty, no outputs, no jobs."""
    return {
        "profile": None,
        "answers": None,
        "selection": None,
        "outputs": {},
        "jobs": {},
        "job_seq": 0,
        "log": [],
    }


def set_profile(record: dict, name: str, profile: dict) -> dict:
    """Store a scanned profile. Returns the stored entry."""
    entry = {"name": name, "sha256": hash_value(profile), "data": profile}
    record["profile"] = entry
    return entry


def set_answers(record: dict, answers: dict) -> dict:
    """Store the question answers. Returns the stored entry."""
    entry = {"sha256": hash_value(answers), "data": dict(answers)}
    record["answers"] = entry
    return entry


def set_selection(record: dict, services: list) -> dict:
    """Store the confirmed service selection. Returns the stored entry."""
    entry = {"sha256": hash_value({"services": sorted(services)}),
             "services": sorted(services)}
    record["selection"] = entry
    return entry


def new_job(record: dict, step: str) -> dict:
    """Start a job for a step. Returns {id, step, input_hashes, status}."""
    record["job_seq"] += 1
    job = {"id": "job-%d" % record["job_seq"], "step": step,
           "input_hashes": current_input_hashes(record),
           "status": "running"}
    record["jobs"][job["id"]] = job
    return job


def latest_job_for_step(record: dict, step: str):
    """Newest job ID for a step, or None."""
    best = None
    for job_id, job in record["jobs"].items():
        if job["step"] != step:
            continue
        if best is None or job_id > best:
            best = job_id
    return best


def job_is_current(record: dict, job: dict) -> bool:
    """True when a job's result may still be shown.

    A result is discarded when its input hashes differ from the current
    ones, or when a newer job exists for the same step.
    """
    if job["input_hashes"] != current_input_hashes(record):
        return False
    return latest_job_for_step(record, job["step"]) == job["id"]


def finish_job(record: dict, job_id: str, status: str,
               reason=None) -> dict:
    """Mark a job finished. Returns the job."""
    job = record["jobs"][job_id]
    job["status"] = status
    if reason is not None:
        job["reason"] = reason
    return job


def discard_stale_result(record: dict, job: dict, what: str) -> dict:
    """Discard an older job's result and log it. Returns the job."""
    reason = ("%s for %s discarded: inputs changed "
              "(job input hashes no longer current)" % (what, job["id"]))
    record["log"].append(reason)
    return finish_job(record, job["id"], "discarded", reason)


def store_output(record: dict, name: str, data, job: dict) -> dict:
    """Store an output with its input hashes and output hash."""
    output = {"job_id": job["id"],
              "input_hashes": dict(job["input_hashes"]),
              "output_sha256": sha256_hex(canonical_bytes(data)),
              "data": data}
    record["outputs"][name] = output
    return output


def output_status(record: dict, name: str) -> str:
    """current, stale, or missing for a stored output."""
    output = record["outputs"].get(name)
    if output is None:
        return "missing"
    if output["input_hashes"] != current_input_hashes(record):
        return "stale"
    return "current"


def describe_output(record: dict, name: str):
    """Public view of an output: status, hashes, and data when current.

    Stale outputs show OUT_OF_DATE without their data; the Details view
    (output hash plus input hashes) is always present once stored.
    """
    output = record["outputs"].get(name)
    if output is None:
        return {"status": "missing", "data": None,
                "output_sha256": None, "input_hashes": None,
                "job_id": None}
    status = output_status(record, name)
    return {"status": status,
            "data": output["data"] if status == "current" else None,
            "message": None if status == "current"
            else "%s: re-run to refresh" % OUT_OF_DATE,
            "output_sha256": output["output_sha256"],
            "input_hashes": dict(output["input_hashes"]),
            "job_id": output["job_id"]}


def session_build_view(record: dict):
    """Current build output data (manifest plus summary), or None."""
    output = record["outputs"].get("build")
    if output is None:
        return None
    if output["input_hashes"] != current_input_hashes(record):
        return None
    return output["data"]


def research_changed(record: dict) -> bool:
    """Note new research facts: the M3 plan must be re-run (spec 1a).

    Returns True when a stored plan went stale because of the change.
    Part 1 has no recorded research, so nothing calls this yet; the rule
    is implemented here so Part 2's research completion only calls it.
    """
    plan = record["outputs"].get("plan")
    if plan is None:
        return False
    plan["input_hashes"] = dict(plan["input_hashes"],
                                research="changed-unrecorded")
    record["log"].append("research changed: M3 plan marked for re-run")
    return True
