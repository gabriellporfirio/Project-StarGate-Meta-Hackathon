"""Offline tests for the M3 synthetic research fixtures."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from stargate import research, scan

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    with open(REPO / "fixtures" / "research" / ("%s.json" % name),
              encoding="utf-8") as handle:
        return json.load(handle)


class SyntheticResearch(unittest.TestCase):
    def test_each_fixture_has_matching_synthetic_research(self):
        for name in scan.KNOWN_FIXTURES:
            with self.subTest(fixture=name):
                report = _load(name)
                self.assertEqual(research.validate_report(report), [])
                self.assertEqual(report["provenance"], "synthetic")
                profile = scan.build_profile(name)
                codename = profile["facts"][
                    "identity.device_codename"]["value"]
                self.assertEqual(report["subject"]["codename"], codename)

    def test_simulated_label_is_a_field_not_a_header(self):
        for name in scan.KNOWN_FIXTURES:
            with self.subTest(fixture=name):
                report = _load(name)
                self.assertNotIn("simulated", report)
                self.assertIn("SIMULATED", report["notes"]["text"])
                self.assertIn("SIMULATED", report["subject"]["model"])


if __name__ == "__main__":
    unittest.main()
