"""The service catalogue: everything a phone can be asked to run.

One entry per service. The numbers are planning estimates (see
docs/fit-budgets.md) until a build measures them. `built` says whether
the phone-side app exists yet: only built services can be selected for
a build; the others are shown as "coming next" and never promised.
"""
from __future__ import annotations

MIB = 1024 * 1024

# Order here is the order shown to the user.
CATALOGUE = (
    {
        "id": "files",
        "title": "Files",
        "summary": "Keep photos and documents on the phone and reach "
                   "them from your computer.",
        "kind": "core",
        "built": True,
        "ram_overhead": 64 * MIB,
        "storage": 200 * MIB,
        "needs_model": False,
        "needs_route": None,
    },
    {
        "id": "assistant",
        "title": "AI assistant",
        "summary": "Ask questions in plain words. Runs on the phone "
                   "when it is strong enough, or through your own "
                   "cloud key when it is not.",
        "kind": "advanced",
        "built": True,
        "ram_overhead": 128 * MIB,
        "storage": 150 * MIB,
        "needs_model": True,
        "needs_route": None,
    },
    {
        "id": "vpn",
        "title": "Private VPN",
        "summary": "Reach your home network safely from anywhere, "
                   "through the phone.",
        "kind": "core",
        "built": False,
        "ram_overhead": 48 * MIB,
        "storage": 50 * MIB,
        "needs_model": False,
        "needs_route": None,
    },
    {
        "id": "adblock",
        "title": "Ad blocker for your home",
        "summary": "Filters ads and trackers for every device on your "
                   "Wi-Fi.",
        "kind": "extra",
        "built": False,
        "ram_overhead": 48 * MIB,
        "storage": 60 * MIB,
        "needs_model": False,
        # Answering DNS on port 53 needs system rights, which only the
        # full image has.
        "needs_route": "image",
    },
    {
        "id": "media",
        "title": "Media library",
        "summary": "Play the music and videos stored on the phone on "
                   "your TV or computer.",
        "kind": "extra",
        "built": False,
        "ram_overhead": 96 * MIB,
        "storage": 80 * MIB,
        "needs_model": False,
        "needs_route": None,
    },
)

SERVICE_IDS = tuple(entry["id"] for entry in CATALOGUE)
BUILT_IDS = tuple(entry["id"] for entry in CATALOGUE if entry["built"])
_BY_ID = {entry["id"]: entry for entry in CATALOGUE}


def get(service_id: str) -> dict:
    """The catalogue entry for one service (KeyError when unknown)."""
    return _BY_ID[service_id]


def is_built(service_id: str) -> bool:
    return service_id in _BY_ID and _BY_ID[service_id]["built"]


def public_catalogue() -> list:
    """Copies safe to hand to the UI."""
    return [dict(entry) for entry in CATALOGUE]
