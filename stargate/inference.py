"""M3 inference rules: derive facts from other facts, never from probes.

Each rule here is listed in docs/inference-rules.md before use.
"""
from __future__ import annotations

RULE_LOCK_FROM_VBS = "R-LOCK-FROM-VBS"

VB_FACT_ID = "boot.verified_boot_state"
LOCK_FACT_ID = "boot.bootloader_locked"

_LOCKED_STATES = frozenset({"green", "yellow"})
_UNLOCKED_STATES = frozenset({"orange"})


def locked_from_verified_boot_state(value: object) -> bool | None:
    """Map a verified-boot state to locked True/False, or None if unknown."""
    if value in _LOCKED_STATES:
        return True
    if value in _UNLOCKED_STATES:
        return False
    return None


def _copied_evidence(fact: dict) -> list | None:
    evidence = fact.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    return [dict(item) for item in evidence]


def derive_bootloader_locked(vb_fact: dict, lock_fact: dict) -> dict:
    """Apply R-LOCK-FROM-VBS. Returns the new lock fact; inputs untouched.

    A derived value never overwrites a value from another source: when the
    existing lock fact disagrees and both sides carry evidence, the fact
    becomes conflicting with both candidates kept.
    """
    derived = locked_from_verified_boot_state(vb_fact.get("value"))
    if derived is None:
        return lock_fact
    status = lock_fact.get("status")
    if status == "unknown":
        out = {
            "status": "inferred",
            "value": derived,
            "unit": None,
            "inference": {
                "rule_id": RULE_LOCK_FROM_VBS,
                "inputs": [VB_FACT_ID],
            },
        }
        evidence = _copied_evidence(vb_fact)
        if evidence is not None:
            out["evidence"] = evidence
        return out
    if status not in ("measured", "inferred"):
        return lock_fact
    current = lock_fact.get("value")
    if not isinstance(current, bool):
        return lock_fact
    if current == derived:
        return lock_fact
    existing_evidence = _copied_evidence(lock_fact)
    derived_evidence = _copied_evidence(vb_fact)
    if existing_evidence is None or derived_evidence is None:
        return lock_fact
    return {
        "status": "conflicting",
        "value": None,
        "unit": None,
        "candidates": [
            {"value": current, "evidence": existing_evidence},
            {"value": derived, "evidence": derived_evidence},
        ],
    }
