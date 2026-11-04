"""Offline tests for the M3 model catalog and its validator."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from stargate import catalog

REPO = Path(__file__).resolve().parent.parent


def _entry():
    return {"id": "test-tiny", "source": "Example/test-tiny",
            "revision": "main",
            "files": [{"name": "model.safetensors", "bytes": 1000}],
            "format": "safetensors", "runtime": "test runtime",
            "abi": "arm64-v8a", "licence": "apache-2.0",
            "estimates": ["runtime", "abi"],
            "provenance": {"kind": "synthetic",
                           "note": "SIMULATED test entry"}}


def _catalog(entry):
    return {"schema_version": "catalog-v1", "models": [entry]}


class CatalogValidation(unittest.TestCase):
    def test_shipped_catalog_validates(self):
        loaded, errors = catalog.load_catalog(
            REPO / "catalog" / "models.json")
        self.assertEqual(errors, [])
        # 2 M3 SmolLM2 entries + the M4b ExecuTorch Llama entry.
        self.assertEqual(len(loaded["models"]), 3)

    def test_shipped_entries_are_sourced_not_synthetic(self):
        loaded, errors = catalog.load_catalog(
            REPO / "catalog" / "models.json")
        self.assertEqual(errors, [])
        for entry in loaded["models"]:
            self.assertEqual(entry["provenance"]["kind"], "sourced")
            self.assertTrue(entry["provenance"]["url"])
            self.assertTrue(entry["provenance"]["date"])

    def test_synthetic_entry_validates(self):
        self.assertEqual(catalog.validate_catalog(_catalog(_entry())),
                         [])

    def test_size_as_string_rejected(self):
        entry = _entry()
        entry["files"] = [{"name": "m", "bytes": "2 GB"}]
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_unknown_values_allowed(self):
        entry = _entry()
        for key in ("source", "revision", "format", "runtime", "abi",
                    "licence"):
            entry[key] = None
        entry["files"] = [{"name": "m", "bytes": None}]
        self.assertEqual(catalog.validate_catalog(_catalog(entry)), [])

    def test_every_value_sourced_estimated_or_unknown(self):
        loaded, errors = catalog.load_catalog(
            REPO / "catalog" / "models.json")
        self.assertEqual(errors, [])
        for entry in loaded["models"]:
            with self.subTest(model=entry["id"]):
                estimates = set(entry["estimates"])
                for key in ("source", "revision", "format", "runtime",
                            "abi", "licence"):
                    if entry[key] is None:
                        continue  # unknown
                    if key in estimates:
                        continue  # labelled planning estimate
                    # Otherwise asserted sourced: must be one of the
                    # docs/catalog.md field-table sourced fields.
                    self.assertIn(key, ("source", "revision", "format",
                                        "licence"))
                for item in entry["files"]:
                    self.assertIsInstance(item["bytes"], int)

    def test_bad_estimates_rejected(self):
        entry = _entry()
        entry["estimates"] = ["runtime", "teleport"]
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))
        entry = _entry()
        entry["estimates"] = "runtime"
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))
        entry = _entry()
        del entry["estimates"]
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_estimates_cannot_list_a_sourced_field(self):
        # M4 section 7: source, revision, format and licence come
        # from the model page, so estimates cannot claim them.
        for field in ("source", "revision", "format", "licence"):
            with self.subTest(field=field):
                entry = _entry()
                entry["estimates"] = ["runtime", field]
                errors = catalog.validate_catalog(_catalog(entry))
                self.assertTrue(
                    any("sourced field" in item for item in errors),
                    errors)
        entry = _entry()
        entry["estimates"] = ["runtime", "abi"]
        self.assertEqual(catalog.validate_catalog(_catalog(entry)), [])

    def test_missing_provenance_rejected(self):
        entry = _entry()
        del entry["provenance"]
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_sourced_needs_url_and_date(self):
        entry = _entry()
        entry["provenance"] = {"kind": "sourced"}
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_duplicate_ids_rejected(self):
        both = {"schema_version": "catalog-v1",
                "models": [_entry(), _entry()]}
        errors = catalog.validate_catalog(both)
        self.assertTrue(any("duplicate" in error for error in errors))

    def test_empty_files_rejected(self):
        entry = _entry()
        entry["files"] = []
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_unknown_keys_rejected(self):
        entry = _entry()
        entry["extra"] = 1
        self.assertTrue(catalog.validate_catalog(_catalog(entry)))

    def test_missing_file(self):
        loaded, errors = catalog.load_catalog(REPO / "catalog" / "nope.json")
        self.assertIsNone(loaded)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
