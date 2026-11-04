"""M3 fit engine: do the chosen services fit this phone?

Pure functions over profile facts, a service selection and catalog
models. Budgets live in docs/fit-budgets.md. M3 is the one place fit
conclusions are made, and only by this code.
"""
from __future__ import annotations

SERVICES = ("files", "assistant")

# Planning estimates (docs/fit-budgets.md); all whole bytes.
OS_BASELINE_RAM = 1610612736
FILES_RAM_OVERHEAD = 67108864
ASSISTANT_RAM_OVERHEAD = 134217728
RUNTIME_FACTOR = 2
FILES_STORAGE = 209715200
ASSISTANT_APP_STORAGE = 157286400


def human_bytes(count: int) -> str:
    """Format whole bytes readably, e.g. 2147483648 -> '2.0 GiB'."""
    units = (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024))
    for name, size in units:
        if count >= size and count % size == 0:
            return "%d %s" % (count // size, name)
        if count >= size:
            return "%.1f %s" % (count / size, name)
    return "%d B" % count


def total_model_bytes(model: dict) -> int | None:
    """Sum a model's file bytes; None when any size is unknown."""
    total = 0
    for item in model.get("files", []):
        size = item.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool):
            return None
        total += size
    return total


def memory_needed(services: list, model_bytes: int | None) -> int:
    """RAM needed in bytes for the whole selection (model may be None)."""
    needed = OS_BASELINE_RAM
    if "files" in services:
        needed += FILES_RAM_OVERHEAD
    if "assistant" in services:
        needed += ASSISTANT_RAM_OVERHEAD
        if model_bytes is not None:
            needed += model_bytes * RUNTIME_FACTOR
    return needed


def storage_needed(services: list, model_bytes: int | None) -> int:
    """Storage needed in bytes for the whole selection."""
    needed = 0
    if "files" in services:
        needed += FILES_STORAGE
    if "assistant" in services:
        needed += ASSISTANT_APP_STORAGE
        if model_bytes is not None:
            needed += model_bytes
    return needed


def _fact_value(facts: dict, fact_id: str):
    fact = facts.get(fact_id)
    if not isinstance(fact, dict):
        return None, "not in profile"
    if fact.get("status") == "unknown":
        return None, "unknown: %s" % fact.get("unknown_reason")
    value = fact.get("value")
    if fact.get("status") not in ("measured", "inferred"):
        return None, "status: %s" % fact.get("status")
    return value, ""


def _size_check(name, fact_id, facts, needed):
    have, problem = _fact_value(facts, fact_id)
    if have is None or not isinstance(have, int) \
            or isinstance(have, bool):
        return {"name": name, "status": "unknown", "fact": fact_id,
                "needed_bytes": needed, "have_bytes": None,
                "estimated": True,
                "reason": "missing fact %s (%s)" % (
                    fact_id, problem if have is None
                    else "not a whole number")}
    if have >= needed:
        return {"name": name, "status": "fits", "fact": fact_id,
                "needed_bytes": needed, "have_bytes": have,
                "estimated": True, "reason": ""}
    return {"name": name, "status": "does_not_fit", "fact": fact_id,
            "needed_bytes": needed, "have_bytes": have,
            "estimated": True,
            "reason": "%s: need %s (%d bytes), have %s (%d bytes)" % (
                fact_id, human_bytes(needed), needed,
                human_bytes(have), have)}


def check_memory(facts: dict, services: list,
                 model_bytes: int | None) -> dict:
    """Memory check; unknown when the model size is unknown."""
    if "assistant" in services and model_bytes is None:
        return {"name": "memory", "status": "unknown",
                "fact": "resources.ram_total", "needed_bytes": None,
                "have_bytes": None, "estimated": True,
                "reason": "model size unknown"}
    return _size_check("memory", "resources.ram_total", facts,
                       memory_needed(services, model_bytes))


def check_storage(facts: dict, services: list,
                  model_bytes: int | None) -> dict:
    """Storage check; unknown when the model size is unknown."""
    if "assistant" in services and model_bytes is None:
        return {"name": "storage", "status": "unknown",
                "fact": "resources.data_free", "needed_bytes": None,
                "have_bytes": None, "estimated": True,
                "reason": "model size unknown"}
    return _size_check("storage", "resources.data_free", facts,
                       storage_needed(services, model_bytes))


def check_cpu(facts: dict, model: dict) -> dict:
    """CPU check: the phone must offer the model's runtime ABI."""
    abi = model.get("abi")
    if not isinstance(abi, str) or not abi:
        return {"name": "cpu", "status": "unknown",
                "fact": "resources.cpu_abis", "needed_abi": None,
                "have_abis": None, "estimated": True,
                "reason": "model %s has no known ABI" % model.get("id")}
    have, problem = _fact_value(facts, "resources.cpu_abis")
    if have is None or not isinstance(have, list):
        return {"name": "cpu", "status": "unknown",
                "fact": "resources.cpu_abis", "needed_abi": abi,
                "have_abis": None, "estimated": True,
                "reason": "missing fact resources.cpu_abis (%s)" % (
                    problem if have is None else "not a list")}
    if abi in have:
        return {"name": "cpu", "status": "fits",
                "fact": "resources.cpu_abis", "needed_abi": abi,
                "have_abis": list(have), "estimated": True,
                "reason": ""}
    return {"name": "cpu", "status": "does_not_fit",
            "fact": "resources.cpu_abis", "needed_abi": abi,
            "have_abis": list(have), "estimated": True,
            "reason": "resources.cpu_abis: need %s, have %s"
            % (abi, have)}


def kernel_check() -> dict:
    """Kernel features: no M3 service needs any yet."""
    return {"name": "kernel", "status": "not_needed",
            "fact": "kernel.config.*", "estimated": False,
            "reason": "not needed yet"}


def evaluate_model(facts: dict, services: list, model: dict) -> dict:
    """Check one catalog model; verdict fits/does_not_fit/unknown."""
    model_bytes = total_model_bytes(model)
    checks = [check_memory(facts, services, model_bytes),
              check_storage(facts, services, model_bytes),
              check_cpu(facts, model)]
    verdict = "fits"
    if any(check["status"] == "does_not_fit" for check in checks):
        verdict = "does_not_fit"
    elif any(check["status"] == "unknown" for check in checks):
        verdict = "unknown"
    return {"id": model["id"], "total_bytes": model_bytes,
            "verdict": verdict, "checks": checks}


def choose_model(evaluated: list) -> dict:
    """Pick the Assistant's model (spec 4c)."""
    if not evaluated:
        return {"status": "unknown", "model_id": None,
                "reasons": ["no candidate models listed"]}
    fitting = [entry for entry in evaluated
               if entry["verdict"] == "fits"]
    if fitting:
        fitting.sort(key=lambda entry: (-entry["total_bytes"],
                                        entry["id"]))
        chosen = fitting[0]
        return {"status": "chosen", "model_id": chosen["id"],
                "reasons": ["%s fits and has the largest total file bytes"
                            " (%d)" % (chosen["id"],
                                       chosen["total_bytes"])]}
    unknown = [entry for entry in evaluated
               if entry["verdict"] == "unknown"]
    if unknown:
        return {"status": "unconfirmed", "model_id": None,
                "reasons": ["%s is unknown: %s" % (
                    entry["id"], "; ".join(
                        check["reason"] for check in entry["checks"]
                        if check["status"] == "unknown"))
                    for entry in unknown]}
    return {"status": "refused", "model_id": None,
            "reasons": ["%s does not fit: %s" % (
                entry["id"], "; ".join(
                    check["reason"] for check in entry["checks"]
                    if check["status"] == "does_not_fit"))
                for entry in evaluated]}


def evaluate_selection(facts: dict, services: list,
                       models: list) -> dict:
    """Judge a service selection; result fits/unconfirmed/refused."""
    kernel = kernel_check()
    if "assistant" not in services:
        checks = [check_memory(facts, services, None),
                  check_storage(facts, services, None), kernel]
        return _selection(services, checks, [], _not_needed_choice())
    evaluated = [evaluate_model(facts, services, model)
                 for model in models]
    choice = choose_model(evaluated)
    if choice["status"] == "chosen":
        chosen = next(entry for entry in evaluated
                      if entry["id"] == choice["model_id"])
        checks = list(chosen["checks"]) + [kernel]
    else:
        checks = [kernel]
    return _selection(services, checks, evaluated, choice)


def _not_needed_choice() -> dict:
    return {"status": "not_needed", "model_id": None, "reasons": []}


def _selection(services, checks, evaluated, choice) -> dict:
    reasons: list = []
    if choice["status"] == "refused":
        result = "refused"
        reasons = list(choice["reasons"])
    elif choice["status"] in ("unconfirmed", "unknown"):
        result = "unconfirmed"
        reasons = list(choice["reasons"])
    elif any(check["status"] == "does_not_fit" for check in checks):
        result = "refused"
        reasons = [check["reason"] for check in checks
                   if check["status"] == "does_not_fit"]
    elif any(check["status"] == "unknown" for check in checks):
        result = "unconfirmed"
        reasons = [check["reason"] for check in checks
                   if check["status"] == "unknown"]
    else:
        result = "fits"
    estimated = any(check.get("estimated") for check in checks)
    estimated = estimated or any(
        check.get("estimated") for entry in evaluated
        for check in entry["checks"])
    return {"services": list(services), "result": result,
            "estimated": estimated, "checks": checks,
            "models": evaluated, "model_choice": choice,
            "reasons": reasons}
