"""Offline tests for the M3 fit engine (spec section 4)."""
from __future__ import annotations

import unittest
from pathlib import Path

from stargate import catalog, fit, scan

REPO = Path(__file__).resolve().parent.parent
GIB = 1024 ** 3


def _measured(value):
    return {"status": "measured", "value": value, "unit": None,
            "evidence": []}


def _facts(ram=12 * GIB, free=100 * GIB, abis=("arm64-v8a",)):
    return {"resources.ram_total": _measured(ram),
            "resources.data_free": _measured(free),
            "resources.cpu_abis": _measured(list(abis))}


def _model(model_id, total_bytes, abi="arm64-v8a"):
    return {"id": model_id, "files": [{"name": "m", "bytes": total_bytes}],
            "abi": abi}


def _real_catalog():
    loaded, errors = catalog.load_catalog(REPO / "catalog" / "models.json")
    assert errors == []
    return loaded["models"]


class ModelChoice(unittest.TestCase):
    def test_one_fits_so_chosen(self):
        models = [_model("b-small", 100_000_000),
                  _model("a-big", 200_000_000, abi="riscv64")]
        out = fit.evaluate_selection(_facts(), ["files", "assistant"],
                                     models)
        self.assertEqual(out["model_choice"]["status"], "chosen")
        self.assertEqual(out["model_choice"]["model_id"], "b-small")
        self.assertEqual(out["result"], "fits")

    def test_two_fit_so_larger_chosen(self):
        models = [_model("b-small", 100_000_000),
                  _model("a-big", 200_000_000)]
        out = fit.evaluate_selection(_facts(), ["files", "assistant"],
                                     models)
        self.assertEqual(out["model_choice"]["model_id"], "a-big")

    def test_tie_goes_to_first_id(self):
        models = [_model("b-second", 100_000_000),
                  _model("a-first", 100_000_000)]
        out = fit.evaluate_selection(_facts(), ["files", "assistant"],
                                     models)
        self.assertEqual(out["model_choice"]["model_id"], "a-first")

    def test_none_fit_but_one_unknown(self):
        unknown = {"id": "mystery", "files": [{"name": "m", "bytes": None}],
                   "abi": "arm64-v8a"}
        too_big = _model("huge", 100 * GIB)
        out = fit.evaluate_selection(_facts(), ["files", "assistant"],
                                     [too_big, unknown])
        self.assertEqual(out["model_choice"]["status"], "unconfirmed")
        self.assertIsNone(out["model_choice"]["model_id"])
        self.assertEqual(out["result"], "unconfirmed")

    def test_all_fail_gives_cloud_only_when_the_app_fits(self):
        # No model fits, but the assistant app itself does: it runs
        # through the user's own cloud key instead of being refused.
        models = [_model("huge", 100 * GIB),
                  _model("wrong-abi", 100_000_000, abi="riscv64")]
        out = fit.evaluate_selection(_facts(), ["files", "assistant"],
                                     models)
        self.assertEqual(out["model_choice"]["status"], "cloud_only")
        self.assertIsNone(out["model_choice"]["model_id"])
        self.assertEqual(out["result"], "fits")
        self.assertEqual(out["reasons"], [fit.CLOUD_ONLY_REASON])
        self.assertIn("huge", " ".join(out["model_choice"]["reasons"]))

    def test_all_fail_gives_refused_when_the_app_does_not_fit(self):
        facts = scan.build_profile("relic")["facts"]
        models = [_model("huge", 100 * GIB)]
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     models)
        self.assertEqual(out["model_choice"]["status"], "refused")
        self.assertEqual(out["result"], "refused")
        self.assertTrue(out["reasons"])

    def test_empty_catalog_gives_unknown(self):
        out = fit.evaluate_selection(_facts(), ["files", "assistant"], [])
        self.assertEqual(out["model_choice"]["status"], "unknown")
        self.assertEqual(out["result"], "unconfirmed")


class RealFixtures(unittest.TestCase):
    def test_flagship_fits_with_largest_model(self):
        facts = scan.build_profile("flagship")["facts"]
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     _real_catalog())
        self.assertEqual(out["result"], "fits")
        self.assertTrue(out["estimated"])
        # Largest fitting model wins; the M4b .pte entry is the largest.
        self.assertEqual(out["model_choice"]["model_id"],
                         "llama-3.2-1b-instruct-spinquant-int4-eo8-pte")

    def test_old32_is_cloud_only_every_model_needs_64bit(self):
        facts = scan.build_profile("old32")["facts"]
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     _real_catalog())
        self.assertEqual(out["result"], "fits")
        self.assertEqual(out["model_choice"]["status"], "cloud_only")
        blob = " ".join(out["model_choice"]["reasons"])
        self.assertIn("resources.cpu_abis", blob)
        self.assertIn("arm64-v8a", blob)

    def test_budget_gets_the_smallest_model(self):
        facts = scan.build_profile("budget")["facts"]
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     _real_catalog())
        self.assertEqual(out["result"], "fits")
        self.assertEqual(out["model_choice"]["model_id"],
                         "smollm2-135m-instruct")

    def test_relic_refused_ram_below_every_need(self):
        facts = scan.build_profile("relic")["facts"]
        for selection in (["files"], ["files", "assistant"]):
            out = fit.evaluate_selection(facts, selection,
                                         _real_catalog())
            self.assertEqual(out["result"], "refused")
            blob = " ".join(out["reasons"])
            self.assertIn("resources.ram_total", blob)
            self.assertIn(str(facts["resources.ram_total"]["value"]),
                          blob)

    def test_every_catalogue_service_is_counted(self):
        facts = scan.build_profile("flagship")["facts"]
        alone = fit.memory_needed(["files"], None)
        both = fit.memory_needed(["files", "vpn"], None)
        self.assertGreater(both, alone)
        out = fit.evaluate_selection(facts, ["files", "vpn", "media"],
                                     _real_catalog())
        self.assertEqual(out["result"], "fits")

    def test_refusal_names_both_numbers(self):
        facts = scan.build_profile("budget")["facts"]
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     _real_catalog())
        for reason in out["reasons"]:
            if "resources.ram_total" in reason:
                self.assertIn("2147483648", reason)
                self.assertIn("bytes", reason)
                self.assertIn("GiB", reason)


class SelectionRules(unittest.TestCase):
    def test_files_plus_assistant_needs_more_than_assistant(self):
        model_bytes = 269060552
        self.assertGreater(
            fit.memory_needed(["files", "assistant"], model_bytes),
            fit.memory_needed(["assistant"], model_bytes))
        self.assertGreater(
            fit.storage_needed(["files", "assistant"], model_bytes),
            fit.storage_needed(["assistant"], model_bytes))

    def test_missing_fact_gives_unconfirmed_never_fits(self):
        facts = _facts()
        facts["resources.ram_total"] = {
            "status": "unknown", "value": None, "unit": "bytes",
            "unknown_reason": "property_missing"}
        out = fit.evaluate_selection(facts, ["files"], _real_catalog())
        self.assertEqual(out["result"], "unconfirmed")
        facts["resources.data_free"] = {
            "status": "unknown", "value": None, "unit": "bytes",
            "unknown_reason": "property_missing"}
        out = fit.evaluate_selection(facts, ["files", "assistant"],
                                     _real_catalog())
        self.assertEqual(out["result"], "unconfirmed")

    def test_kernel_not_needed_yet(self):
        out = fit.evaluate_selection(_facts(), ["files"], [])
        kernel = next(check for check in out["checks"]
                      if check["name"] == "kernel")
        self.assertEqual(kernel["status"], "not_needed")

    def test_exact_boundary_fits(self):
        needed = fit.memory_needed(["files"], None)
        facts = _facts(ram=needed)
        check = fit.check_memory(facts, ["files"], None)
        self.assertEqual(check["status"], "fits")

    def test_unknown_model_abi_is_unknown(self):
        model = {"id": "no-abi", "files": [{"name": "m", "bytes": 1000}],
                 "abi": None}
        out = fit.evaluate_selection(_facts(), ["assistant"], [model])
        self.assertEqual(out["result"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
