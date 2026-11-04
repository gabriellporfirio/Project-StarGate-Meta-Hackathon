"""M3 strategy engine: rank ways of building StarGate OS for one phone.

Pure functions over profile facts and research typed facts. The rules
are written as tables in docs/strategy-rules.md; this module follows
them exactly.
"""
from __future__ import annotations

STRATEGIES = (
    "community_build",
    "generic_system_image",
    "linux",
    "stock_rebuild",
    "on_top_of_android",
)

UNLOCK_OK = ("already_unlocked", "allowed_now")

LOCK_FACT = "boot.bootloader_locked"
SUPPORTED_FACT = "boot.oem_unlock_supported"
ALLOWED_FACT = "boot.oem_unlock_allowed"

# App requirements are planning estimates until M4 sets them.
APP_MIN_SDK = 26
APP_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64")

# arm64-v8a is sourced (Android GSI page); x86_64 is a planning estimate.
GSI_ABIS = ("arm64-v8a", "x86_64")

_SIGNING_UNLOCK = (
    "Verified boot shows orange after unlocking; relocking needs "
    "your own signing key, if the phone supports it."
)
_WIPE_RISK = "Unlocking or relocking the bootloader wipes the phone's data."

RISKS = {
    "community_build": [
        _WIPE_RISK,
        "A community tree may not build, and some hardware may not work.",
    ],
    "generic_system_image": [
        _WIPE_RISK,
        "Some GSI hardware features may not work.",
    ],
    "linux": [
        _WIPE_RISK,
        "A listed port page shows a page exists, not that the port works.",
    ],
    "stock_rebuild": ["M3 does not judge stock rebuilds."],
    "on_top_of_android": [
        "The app modules do not exist yet; they are built in M4.",
    ],
}

SIGNING = {
    "community_build": _SIGNING_UNLOCK,
    "generic_system_image": _SIGNING_UNLOCK,
    "linux": _SIGNING_UNLOCK,
    "stock_rebuild": "Unknown in M3.",
    "on_top_of_android": "No bootloader change; stock verified boot is untouched.",
}

OPEN_CHECKS = {
    "community_build": [
        "The tree still builds.",
        "The vendor files are available.",
    ],
    "generic_system_image": [
        "Launch and vendor version compatibility (Android's GSI rules also "
        "check these, and M3 has no fact for them).",
    ],
    "linux": ["Which features of the listed port work is not checked."],
    "stock_rebuild": [],
    "on_top_of_android": [
        "The app modules do not exist yet (they are built in M4).",
    ],
}


def _value(facts: dict, fact_id: str) -> object:
    fact = facts.get(fact_id)
    if not isinstance(fact, dict):
        return None
    return fact.get("value")


def unlock_state(profile_facts: dict) -> str:
    """Work out the single unlock state (docs/strategy-rules.md, table 3a).

    Rows are checked in order; exactly one always applies.
    """
    lock = profile_facts.get(LOCK_FACT)
    if isinstance(lock, dict) and lock.get("status") == "conflicting":
        return "unknown"
    locked = _value(profile_facts, LOCK_FACT)
    if locked is False and isinstance(lock, dict) and lock.get("status") in (
            "measured", "inferred"):
        return "already_unlocked"
    supported = _value(profile_facts, SUPPORTED_FACT)
    allowed = _value(profile_facts, ALLOWED_FACT)
    if locked is True and supported is True and allowed is True:
        return "allowed_now"
    if locked is True and supported is True and allowed is False:
        return "not_allowed_now"
    if locked is not False and supported is False:
        return "no_official_path"
    return "unknown"


def unlock_blocker(state: str, profile_facts: dict) -> dict | None:
    """Blocker dict for unlock states that block, else None."""
    if state == "not_allowed_now":
        return {"fact": ALLOWED_FACT,
                "value": _value(profile_facts, ALLOWED_FACT),
                "blocker_kind": "current"}
    if state == "no_official_path":
        return {"fact": SUPPORTED_FACT,
                "value": _value(profile_facts, SUPPORTED_FACT),
                "blocker_kind": "no_official_path"}
    return None


def _unlock_unknown_missing(profile_facts: dict) -> list:
    missing = []
    for fact_id in (LOCK_FACT, SUPPORTED_FACT, ALLOWED_FACT):
        fact = profile_facts.get(fact_id)
        if not isinstance(fact, dict) or fact.get("value") is not True \
                and fact.get("value") is not False:
            status = fact.get("status") if isinstance(fact, dict) \
                else "missing"
            missing.append({"fact": fact_id,
                            "reason": "unlock state unknown: %s" % status})
    return missing


def _unlock_met(state: str) -> list:
    if state == "already_unlocked":
        return [LOCK_FACT]
    if state == "allowed_now":
        return [LOCK_FACT, SUPPORTED_FACT, ALLOWED_FACT]
    return []


def _research_fact(research_facts: dict | None, name: str):
    if research_facts is None:
        return None, "no research file"
    fact = research_facts.get(name)
    if not isinstance(fact, dict):
        return None, "not in report"
    if fact.get("status") == "unknown":
        return None, "research unknown: %s" % fact.get("reason")
    if fact.get("status") == "none_found":
        return None, "research none_found"
    if fact.get("status") != "found":
        return None, "research status: %s" % fact.get("status")
    return fact, ""


def _lineage_verdict(research_facts):
    fact, problem = _research_fact(research_facts, "lineage.support")
    if fact is None:
        return False, [{"fact": "lineage.support", "reason": problem}]
    support = fact.get("value", {}).get("support")
    if support == "current":
        return True, []
    return False, [{"fact": "lineage.support",
                    "reason": "support is %s" % support}]


def _tree_verdict(research_facts):
    fact, problem = _research_fact(research_facts, "repos.device_tree")
    if fact is None:
        return False, [{"fact": "repos.device_tree", "reason": problem}]
    repos = fact.get("value", {}).get("repos")
    if isinstance(repos, list) and repos:
        return True, []
    return False, [{"fact": "repos.device_tree",
                    "reason": "no established repo"}]


def _pmos_verdict(research_facts):
    fact, problem = _research_fact(research_facts, "pmos.status")
    if fact is None:
        return False, [{"fact": "pmos.status", "reason": problem}]
    status = fact.get("value", {}).get("status")
    if status == "listed":
        return True, []
    return False, [{"fact": "pmos.status",
                    "reason": "status is %s" % status}]


def _abi_verdict(profile_facts, wanted, label):
    fact = profile_facts.get("resources.cpu_abis")
    if not isinstance(fact, dict):
        return False, [{"fact": "resources.cpu_abis",
                        "reason": "not in profile"}]
    if fact.get("status") == "unknown":
        return False, [{"fact": "resources.cpu_abis",
                        "reason": "unknown: %s" % fact.get("unknown_reason")}]
    abis = fact.get("value")
    if not isinstance(abis, list):
        return False, [{"fact": "resources.cpu_abis",
                        "reason": "status: %s" % fact.get("status")}]
    if any(abi in wanted for abi in abis):
        return True, []
    return False, [{"fact": "resources.cpu_abis",
                    "reason": "no %s ABI in %s" % (label, abis)}]


def _result(strategy_id, status, met=None, blocker=None, missing=None):
    return {
        "id": strategy_id,
        "status": status,
        "met": met or [],
        "blocker": blocker,
        "missing": missing or [],
        "open_checks": list(OPEN_CHECKS[strategy_id]),
        "risks": list(RISKS[strategy_id]),
        "signing": SIGNING[strategy_id],
    }


def _with_unlock(strategy_id, state, profile_facts, ok, met, missing,
                 own_blocker=None):
    if ok and state in UNLOCK_OK:
        return _result(strategy_id, "candidate",
                        met=met + _unlock_met(state))
    if own_blocker is not None:
        return _result(strategy_id, "blocked", blocker=own_blocker)
    blocker = unlock_blocker(state, profile_facts)
    if blocker is not None:
        return _result(strategy_id, "blocked", blocker=blocker)
    if state not in UNLOCK_OK:
        missing = list(missing) + _unlock_unknown_missing(profile_facts)
    return _result(strategy_id, "unknown", missing=missing)


def evaluate(profile_facts: dict, research_facts: dict | None) -> list:
    """Judge every strategy; returns ranked strategy dicts (best first)."""
    state = unlock_state(profile_facts)
    results = []

    lineage_ok, lineage_missing = _lineage_verdict(research_facts)
    tree_ok, tree_missing = _tree_verdict(research_facts)
    results.append(_with_unlock(
        "community_build", state, profile_facts,
        lineage_ok and tree_ok,
        ["lineage.support", "repos.device_tree"],
        lineage_missing + tree_missing))

    treble = _value(profile_facts, "boot.treble_enabled")
    treble_blocker = None
    treble_missing: list = []
    treble_ok = treble is True
    if treble is False:
        treble_blocker = {"fact": "boot.treble_enabled", "value": False,
                          "blocker_kind": "unsupported"}
    elif not treble_ok:
        fact = profile_facts.get("boot.treble_enabled")
        status = fact.get("status") if isinstance(fact, dict) else "missing"
        treble_missing = [{"fact": "boot.treble_enabled",
                           "reason": "treble state: %s" % status}]
    abi_ok, abi_missing = _abi_verdict(profile_facts, GSI_ABIS,
                                       "GSI-published")
    results.append(_with_unlock(
        "generic_system_image", state, profile_facts,
        treble_ok and abi_ok, ["boot.treble_enabled", "resources.cpu_abis"],
        treble_missing + abi_missing, own_blocker=treble_blocker))

    pmos_ok, pmos_missing = _pmos_verdict(research_facts)
    results.append(_with_unlock(
        "linux", state, profile_facts, pmos_ok, ["pmos.status"],
        pmos_missing))

    results.append(_result("stock_rebuild", "not_covered"))

    sdk = _value(profile_facts, "identity.sdk_level")
    sdk_blocker = None
    sdk_missing: list = []
    sdk_ok = isinstance(sdk, int) and not isinstance(sdk, bool) \
        and sdk >= APP_MIN_SDK
    if isinstance(sdk, int) and not isinstance(sdk, bool) \
            and sdk < APP_MIN_SDK:
        sdk_blocker = {"fact": "identity.sdk_level", "value": sdk,
                       "blocker_kind": "unsupported"}
    elif not sdk_ok:
        fact = profile_facts.get("identity.sdk_level")
        status = fact.get("status") if isinstance(fact, dict) else "missing"
        sdk_missing = [{"fact": "identity.sdk_level",
                        "reason": "sdk level: %s" % status}]
    app_abi_ok, app_abi_missing = _abi_verdict(profile_facts, APP_ABIS,
                                               "app-supported")
    if sdk_ok and app_abi_ok:
        results.append(_result(
            "on_top_of_android", "candidate",
            met=["identity.sdk_level", "resources.cpu_abis"]))
    elif sdk_blocker is not None:
        results.append(_result("on_top_of_android", "blocked",
                               blocker=sdk_blocker))
    else:
        results.append(_result("on_top_of_android", "unknown",
                               missing=sdk_missing + app_abi_missing))

    return sorted(results, key=_rank_key)


def _rank_key(entry: dict) -> tuple:
    order = {"candidate": 0, "unknown": 1, "blocked": 2, "not_covered": 3}
    kinds = {"current": 0, "no_official_path": 1, "unsupported": 2}
    kind = 0
    if entry["status"] == "blocked" and isinstance(entry["blocker"], dict):
        kind = kinds.get(entry["blocker"].get("blocker_kind"), 3)
    return (order[entry["status"]], kind, STRATEGIES.index(entry["id"]))
