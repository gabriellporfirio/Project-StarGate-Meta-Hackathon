"""Offline tests for R-LOCK-FROM-VBS (M3 section 2)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from stargate import inference, profile, scan

REPO = Path(__file__).resolve().parent.parent

VB_COMMAND = "getprop ro.boot.verifiedbootstate"
VB_PROBE_ID = "getprop-ro.boot.verifiedbootstate"


def _vb_evidence(excerpt):
    return [{
        "probe_id": VB_PROBE_ID,
        "command": VB_COMMAND,
        "excerpt": excerpt,
        "observed_at": "2026-11-01T00:00:01Z",
    }]


def _vb_fact(value):
    return {"status": "measured", "value": value, "unit": None,
            "evidence": _vb_evidence(value)}


def _unknown_lock(reason="deferred"):
    return {"status": "unknown", "value": None, "unit": None,
            "unknown_reason": reason}


class LockFromVbs(unittest.TestCase):
    def test_green_and_yellow_give_true(self):
        for state in ("green", "yellow"):
            with self.subTest(state=state):
                out = inference.derive_bootloader_locked(
                    _vb_fact(state), _unknown_lock())
                self.assertEqual(out["status"], "inferred")
                self.assertIs(out["value"], True)
                self.assertEqual(
                    out["inference"],
                    {"rule_id": "R-LOCK-FROM-VBS",
                     "inputs": ["boot.verified_boot_state"]})
                self.assertEqual(
                    profile.validate_fact("boot.bootloader_locked", out),
                    [])

    def test_orange_gives_false(self):
        out = inference.derive_bootloader_locked(
            _vb_fact("orange"), _unknown_lock())
        self.assertEqual(out["status"], "inferred")
        self.assertIs(out["value"], False)
        self.assertEqual(out["inference"]["rule_id"], "R-LOCK-FROM-VBS")
        self.assertEqual(
            profile.validate_fact("boot.bootloader_locked", out), [])

    def test_red_unknown_and_conflicting_leave_unknown(self):
        vb_cases = [
            _vb_fact("red"),
            {"status": "unknown", "value": None, "unit": None,
             "unknown_reason": "property_missing"},
            {"status": "conflicting", "value": None, "unit": None,
             "candidates": [
                 {"value": "green", "evidence": _vb_evidence("green")},
                 {"value": "orange", "evidence": _vb_evidence("orange")},
             ]},
        ]
        for vb_fact in vb_cases:
            with self.subTest(vb=vb_fact["status"]):
                before = _unknown_lock()
                out = inference.derive_bootloader_locked(vb_fact, before)
                self.assertEqual(out, before)
                self.assertEqual(out["status"], "unknown")

    def test_never_relabelled_as_measured(self):
        out = inference.derive_bootloader_locked(
            _vb_fact("green"), _unknown_lock())
        self.assertNotEqual(out["status"], "measured")
        self.assertIn("inference", out)

    def test_agreeing_value_is_untouched(self):
        existing = {"status": "measured", "value": True, "unit": None,
                    "evidence": _vb_evidence("green")}
        out = inference.derive_bootloader_locked(_vb_fact("green"),
                                                  existing)
        self.assertEqual(out, existing)

    def test_disagreeing_value_gives_conflicting(self):
        # The other source is hypothetical (no probe measures the lock);
        # its evidence uses an allowlisted command so the shape validates.
        existing = {
            "status": "measured",
            "value": False,
            "unit": None,
            "evidence": [{
                "probe_id": "getprop-ro.oem_unlock_supported",
                "command": "getprop ro.oem_unlock_supported",
                "excerpt": "0",
                "observed_at": "2026-11-01T00:00:01Z",
            }],
        }
        out = inference.derive_bootloader_locked(_vb_fact("green"),
                                                  existing)
        self.assertEqual(out["status"], "conflicting")
        self.assertIsNone(out["value"])
        self.assertEqual(len(out["candidates"]), 2)
        self.assertEqual(
            sorted(candidate["value"] for candidate in out["candidates"]),
            [False, True])
        for candidate in out["candidates"]:
            self.assertTrue(candidate["evidence"])
        self.assertEqual(
            profile.validate_fact("boot.bootloader_locked", out), [])

    def test_inputs_are_not_mutated(self):
        vb_fact = _vb_fact("green")
        lock_fact = _unknown_lock()
        inference.derive_bootloader_locked(vb_fact, lock_fact)
        self.assertEqual(vb_fact, _vb_fact("green"))
        self.assertEqual(lock_fact, _unknown_lock())


class LockInSimFixtures(unittest.TestCase):
    def test_fixture_lock_values(self):
        expected = {"budget": True, "flagship": False, "old32": True,
                    "locked": True}
        for name, locked in expected.items():
            with self.subTest(fixture=name):
                facts = scan.build_profile(name)["facts"]
                lock = facts["boot.bootloader_locked"]
                self.assertEqual(lock["status"], "inferred")
                self.assertIs(lock["value"], locked)
                self.assertEqual(lock["inference"]["rule_id"],
                                 "R-LOCK-FROM-VBS")


class AospCitation(unittest.TestCase):
    def test_page_date_recorded_with_quoted_sentence(self):
        # M4 section 7: the AOSP boot-flow page date stays on file
        # next to the quoted sentence in docs/inference-rules.md.
        text = (REPO / "docs" / "inference-rules.md").read_text(
            encoding="utf-8")
        self.assertIn(
            "https://source.android.com/docs/security/features/"
            "verifiedboot/boot-flow", text)
        self.assertIn(
            "The bootloader sets the `androidboot.verifiedbootstate`"
            " option to one", text)
        self.assertRegex(
            text, r"fetched \d{1,2} \w+ \d{4}")


if __name__ == "__main__":
    unittest.main()
