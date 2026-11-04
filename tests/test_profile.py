"""Tests for profile-v1 validator and schema examples (offline)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from stargate import profile

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "schemas" / "examples"


def _base_fact(**overrides):
    fact = {
        "status": "measured",
        "value": "Acme",
        "unit": None,
        "evidence": [
            {
                "probe_id": "getprop-ro.product.manufacturer",
                "command": "getprop ro.product.manufacturer",
                "excerpt": "Acme",
                "observed_at": "2026-10-30T12:00:01Z",
            }
        ],
    }
    fact.update(overrides)
    return fact


class ExampleLabels(unittest.TestCase):
    def test_every_example_matches_its_label(self):
        files = sorted(EXAMPLES.glob("*.json"))
        self.assertGreaterEqual(len(files), 9)
        for target in files:
            with self.subTest(example=target.name):
                payload = json.loads(target.read_text(encoding="utf-8"))
                errors = profile.validate_profile(payload)
                if target.name.startswith("valid_"):
                    self.assertEqual(errors, [], msg=str(errors))
                elif target.name.startswith("invalid_"):
                    self.assertNotEqual(errors, [])
                else:
                    self.fail("example name must start valid_/invalid_: %s"
                              % target.name)


class FactValidation(unittest.TestCase):
    def test_unknown_requires_null_and_reason(self):
        fact = _base_fact(status="unknown", value=None, unit=None)
        del fact["evidence"]
        fact["unknown_reason"] = "property_missing"
        self.assertEqual(
            profile.validate_fact("boot.vndk_version", fact), [])

    def test_bytes_must_be_int(self):
        fact = _base_fact(status="measured", value="2 GB", unit="bytes")
        errors = profile.validate_fact("resources.ram_total", fact)
        self.assertNotEqual(errors, [])

    def test_unit_fixed_per_fact(self):
        fact = _base_fact(unit="bytes")
        errors = profile.validate_fact("identity.model", fact)
        self.assertTrue(any("unit must be None" in message for message in errors))

    def test_excerpt_length_and_newline(self):
        long_fact = _base_fact()
        long_fact["evidence"][0]["excerpt"] = "x" * 201
        self.assertNotEqual(
            profile.validate_fact("identity.model", long_fact), [])
        newline_fact = _base_fact()
        newline_fact["evidence"][0]["excerpt"] = "a\nb"
        self.assertNotEqual(
            profile.validate_fact("identity.model", newline_fact), [])

    def test_created_at_must_be_utc(self):
        bad = {
            "schema_version": "profile-v1",
            "simulated": True,
            "fixture_name": "example",
            "created_at": "2026-10-30 12:00:00",
            "facts": {
                "identity.model": _base_fact(value="Phone",
                                             evidence=[
                                                 {"probe_id": "p",
                                                  "command": "c",
                                                  "excerpt": "e",
                                                  "observed_at": "2026-10-30T12:00:01Z"}])
            },
        }
        self.assertTrue(
            any("created_at" in message
                for message in profile.validate_profile(bad)))

    def test_conflicting_distinctness(self):
        fact = {
            "status": "conflicting",
            "value": None,
            "unit": "count",
            "candidates": [
                {"value": 4, "evidence": [
                    {"probe_id": "p", "command": "cat /proc/cpuinfo",
                     "excerpt": "e",
                     "observed_at": "2026-10-30T12:00:01Z"}]},
                {"value": 4, "evidence": [
                    {"probe_id": "p", "command": "cat /proc/cpuinfo",
                     "excerpt": "e",
                     "observed_at": "2026-10-30T12:00:02Z"}]},
            ],
        }
        errors = profile.validate_fact("resources.cpu_cores", fact)
        self.assertTrue(any("distinct" in message for message in errors))

    def test_unknown_fact_id_rejected(self):
        fact = _base_fact()
        errors = profile.validate_fact("identity.unknown_field", fact)
        self.assertTrue(any("unknown fact ID" in message for message in errors))

    def test_evidence_command_must_be_allowlisted(self):
        fact = _base_fact()
        fact["evidence"][0]["command"] = "getprop ro.serialno"
        errors = profile.validate_fact("identity.model", fact)
        self.assertTrue(any("allowlist" in message for message in errors))
        allowed = _base_fact()
        self.assertEqual(
            profile.validate_fact("identity.model", allowed), [])

    def test_inference_rule_must_be_known(self):
        fact = {
            "status": "inferred",
            "value": "sun",
            "unit": None,
            "inference": {
                "rule_id": "made-up-rule-v9",
                "inputs": ["identity.device_codename"],
            },
        }
        errors = profile.validate_fact("identity.platform", fact)
        self.assertTrue(
            any("not a known rule" in message for message in errors))
        fact["inference"]["rule_id"] = "example-platform-from-codename-v1"
        self.assertEqual(
            profile.validate_fact("identity.platform", fact), [])

    def test_rule_ids_listed_in_docs(self):
        docs = (REPO / "docs" / "inference-rules.md").read_text(
            encoding="utf-8")
        self.assertTrue(profile.KNOWN_INFERENCE_RULES)
        for rule_id in profile.KNOWN_INFERENCE_RULES:
            self.assertIn(rule_id, docs)
        documented = []
        for line in docs.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip().strip("`") for cell in
                     line.strip().strip("|").split("|")]
            if not cells or cells[0] in ("Rule ID", ""):
                continue
            if set(cells[0]) <= set("-:"):
                continue
            documented.append(cells[0])
        self.assertTrue(documented)
        for rule_id in documented:
            self.assertIn(rule_id, profile.KNOWN_INFERENCE_RULES)

    def test_all_fact_ids_known(self):
        self.assertEqual(len(profile.ALL_FACT_IDS), 28)


if __name__ == "__main__":
    unittest.main()
