"""What this phone can be offered: one plain answer per service.

Built on the fit engine (does it fit?) and the strategy engine (how
can it be installed?). The aim is to say yes wherever the phone can
honestly do the job, and to refuse the whole phone only when nothing
at all can run on it.

Install route, in the owner's order: a full clean image first, and
only when the phone cannot be reflashed, taking over the Android that
is already on it.
"""
from __future__ import annotations

from . import fit as fit_module
from . import services as services_module
from . import strategy as strategy_module

IMAGE_STRATEGIES = ("community_build", "generic_system_image", "linux")
TAKEOVER_STRATEGY = "on_top_of_android"

ROUTES = ("image", "image_or_takeover", "takeover", "none")
ROUTE_TEXT = {
    "image": "The phone's own Android is replaced by a clean system "
             "that runs only what you pick.",
    "image_or_takeover": "If the phone allows unlocking (checked in "
                         "the research step), its Android is replaced "
                         "by a clean system. If not, its own Android "
                         "is kept and taken over.",
    "takeover": "This phone cannot be reflashed, so its own Android is "
                "kept and taken over: only your services run on it.",
    "none": "This phone cannot run any install route.",
}

STATUSES = ("ready", "limited", "not_possible", "unknown")


def install_route(strategies: list) -> str:
    """Pick the install route from ranked strategy results."""
    by_id = {entry["id"]: entry["status"] for entry in strategies}
    image = [by_id.get(name) for name in IMAGE_STRATEGIES]
    takeover = by_id.get(TAKEOVER_STRATEGY)
    if "candidate" in image:
        return "image"
    if "unknown" in image:
        return "image_or_takeover" if takeover in (
            "candidate", "unknown") else "image"
    if takeover in ("candidate", "unknown"):
        return "takeover"
    return "none"


def _score(checks: list) -> int:
    """0-100: how comfortably the service fits (100 = twice the need)."""
    ratios = []
    for check in checks:
        have = check.get("have_bytes")
        need = check.get("needed_bytes")
        if isinstance(have, int) and isinstance(need, int) and need > 0:
            ratios.append(have / need)
    if not ratios:
        return 0
    return max(0, min(100, int(round(50 * min(ratios)))))


def _plain_reasons(result: dict) -> list:
    reasons = []
    for check in result["checks"]:
        if check["status"] != "does_not_fit":
            continue
        have = check.get("have_bytes")
        need = check.get("needed_bytes")
        if isinstance(have, int) and isinstance(need, int):
            what = "memory" if check["name"] == "memory" else "free space"
            reasons.append("Needs about %s of %s; this phone has %s." % (
                fit_module.human_bytes(need), what,
                fit_module.human_bytes(have)))
        elif check.get("reason"):
            reasons.append(check["reason"])
    return reasons


def service_offer(facts: dict, service_id: str, models: list,
                  route: str) -> dict:
    """The offer for one service on its own on this phone."""
    entry = services_module.get(service_id)
    result = fit_module.evaluate_selection(facts, [service_id], models)
    choice = result["model_choice"]
    offer = {
        "id": service_id,
        "title": entry["title"],
        "summary": entry["summary"],
        "kind": entry["kind"],
        "built": entry["built"],
        "future": bool(entry.get("future")),
        "status": "ready",
        "mode": None,
        "model_id": choice["model_id"],
        "score": _score(result["checks"]),
        "headline": "",
        "reasons": [],
    }
    if result["result"] == "refused":
        offer["status"] = "not_possible"
        plain = _plain_reasons(result)
        if not plain:
            # A refused model list: explain with the app-only numbers.
            app_only = {"checks": [
                fit_module._size_check(
                    "memory", "resources.ram_total", facts,
                    fit_module.memory_needed([service_id], 0)),
                fit_module._size_check(
                    "storage", "resources.data_free", facts,
                    fit_module.storage_needed([service_id], 0))]}
            plain = _plain_reasons(app_only)
            offer["score"] = _score(app_only["checks"])
        offer["reasons"] = plain or list(result["reasons"])
        offer["headline"] = "Too much for this phone."
    elif result["result"] == "unconfirmed":
        offer["status"] = "unknown"
        offer["reasons"] = list(result["reasons"])
        offer["headline"] = "We could not measure enough to be sure."
    elif entry["needs_route"] == "image" and route != "image":
        offer["status"] = "limited" if route == "image_or_takeover" \
            else "not_possible"
        offer["headline"] = (
            "Needs the full clean system, which this phone may not "
            "allow." if route == "image_or_takeover" else
            "Needs the full clean system, which this phone does not "
            "allow.")
        offer["reasons"] = [offer["headline"]]
    elif service_id == "assistant":
        if choice["status"] == "cloud_only":
            offer["status"] = "limited"
            offer["mode"] = "cloud_only"
            offer["headline"] = ("Works through your own cloud key. "
                                 "The phone is too small to run a "
                                 "model itself.")
            offer["reasons"] = [fit_module.CLOUD_ONLY_REASON]
        else:
            offer["mode"] = "on_phone"
            offer["headline"] = "Runs on the phone, even offline."
    else:
        offer["headline"] = "Runs well on this phone."
    return offer


def build_offers(facts: dict, models: list,
                 research_facts: dict | None = None) -> dict:
    """Offers for every catalogue service, plus the phone's verdict."""
    strategies = strategy_module.evaluate(facts, research_facts)
    route = install_route(strategies)
    offers = [service_offer(facts, service_id, models, route)
              for service_id in services_module.SERVICE_IDS]
    possible = [offer for offer in offers
                if offer["status"] in ("ready", "limited", "unknown")]
    reasons: list = []
    if route == "none":
        verdict = "not_usable"
        reasons.append(
            "It is too old to install on: none of the install routes "
            "work on this phone.")
    elif not possible:
        verdict = "not_usable"
        reasons.append("Nothing in the catalogue fits on this phone.")
    else:
        verdict = "usable"
    if verdict == "not_usable":
        # Explain with the lightest service only: if even that does
        # not fit, nothing does.
        lightest = min(offers, key=lambda offer: (
            services_module.get(offer["id"])["ram_overhead"],
            offer["id"]))
        for reason in lightest["reasons"]:
            if reason not in reasons:
                reasons.append(reason)
    return {
        "verdict": verdict,
        "route": route,
        "route_text": ROUTE_TEXT[route],
        "reasons": reasons[:4],
        "services": offers,
        "selectable": [offer["id"] for offer in possible
                       if offer["built"]
                       and offer["status"] in ("ready", "limited")],
        "unlock_state": strategy_module.unlock_state(facts),
    }
