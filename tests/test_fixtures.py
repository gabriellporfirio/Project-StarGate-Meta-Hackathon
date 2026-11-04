"""Fixture hygiene: SIMULATED labels and invented data (offline)."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
SERIAL_LIKE = re.compile(r"\b\d{14,19}\b")


class FixtureHeaders(unittest.TestCase):
    def test_every_fixture_file_labelled_simulated(self):
        files = sorted(path for path in FIXTURES.rglob("*") if path.is_file())
        self.assertGreaterEqual(len(files), 4)
        for target in files:
            with self.subTest(fixture=str(target.relative_to(REPO))):
                body = target.read_text(encoding="utf-8")
                if target.suffix == ".json":
                    # Valid JSON: the SIMULATED label lives in a field.
                    json.loads(body)
                    self.assertIn(
                        "SIMULATED", body,
                        "missing SIMULATED label: %s" % target.name)
                    continue
                first = body.splitlines()
                self.assertTrue(first, "empty fixture file")
                self.assertTrue(
                    first[0].startswith("SIMULATED"),
                    "missing SIMULATED header: %s" % target.name)

    def test_no_serial_like_values_in_fixtures(self):
        for target in sorted(path for path in FIXTURES.rglob("*")
                             if path.is_file()):
            with self.subTest(fixture=str(target.relative_to(REPO))):
                body = target.read_text(encoding="utf-8")
                self.assertIsNone(
                    SERIAL_LIKE.search(body),
                    "serial-like digits in %s" % target.name)


if __name__ == "__main__":
    unittest.main()
