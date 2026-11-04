"""Session record and inputs-by-hash rules (M6 Part 1, spec 1a)."""
from __future__ import annotations

import unittest

from stargate import appsession as mod


def _profile():
    return {"schema_version": "profile-v1", "simulated": True,
            "created_at": "2026-11-04T00:00:00Z",
            "facts": {"identity.model": {
                "status": "measured", "value": "Fx",
                "evidence": [{"observed_at": "2026-11-04T00:00:01Z"}]}}}


class HashTests(unittest.TestCase):
    def test_same_phone_same_hash_despite_timestamps(self):
        first = _profile()
        second = _profile()
        second["created_at"] = "2026-11-05T00:00:00Z"
        second["facts"]["identity.model"]["evidence"][0][
            "observed_at"] = "2026-11-05T00:00:01Z"
        self.assertEqual(mod.hash_value(first), mod.hash_value(second))

    def test_different_value_different_hash(self):
        first = _profile()
        second = _profile()
        second["facts"]["identity.model"]["value"] = "Other"
        self.assertNotEqual(mod.hash_value(first), mod.hash_value(second))


class StalenessTests(unittest.TestCase):
    def test_missing_then_current(self):
        record = mod.new_record()
        self.assertEqual(mod.output_status(record, "plan"), "missing")
        mod.set_profile(record, "flagship", _profile())
        mod.set_answers(record, {"q1": "both", "q2": "no"})
        mod.set_selection(record, ["files", "assistant"])
        job = mod.new_job(record, "selection")
        mod.store_output(record, "plan", {"ok": True}, job)
        self.assertEqual(mod.output_status(record, "plan"), "current")
        view = mod.describe_output(record, "plan")
        self.assertEqual(view["data"], {"ok": True})
        self.assertIsNotNone(view["output_sha256"])
        self.assertEqual(set(view["input_hashes"]),
                         {"profile", "answers", "selection", "research"})

    def test_profile_change_marks_plan_stale(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        mod.set_answers(record, {"q1": "both", "q2": "no"})
        mod.set_selection(record, ["files", "assistant"])
        job = mod.new_job(record, "selection")
        mod.store_output(record, "plan", {"ok": True}, job)
        other = _profile()
        other["facts"]["identity.model"]["value"] = "Old"
        mod.set_profile(record, "old32", other)
        self.assertEqual(mod.output_status(record, "plan"), "stale")
        view = mod.describe_output(record, "plan")
        self.assertIsNone(view["data"])
        self.assertIn("out of date", view["message"])
        self.assertIsNotNone(view["output_sha256"])
        self.assertIsNotNone(view["input_hashes"])

    def test_selection_change_marks_plan_and_build_stale(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        mod.set_answers(record, {"q1": "both", "q2": "no"})
        mod.set_selection(record, ["files", "assistant"])
        plan_job = mod.new_job(record, "selection")
        mod.store_output(record, "plan", {"ok": True}, plan_job)
        build_job = mod.new_job(record, "build")
        mod.store_output(record, "build", {"image": True}, build_job)
        mod.set_selection(record, ["files"])
        self.assertEqual(mod.output_status(record, "plan"), "stale")
        self.assertEqual(mod.output_status(record, "build"), "stale")

    def test_answers_change_marks_plan_stale(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        mod.set_answers(record, {"q1": "both", "q2": "no"})
        mod.set_selection(record, ["files"])
        job = mod.new_job(record, "selection")
        mod.store_output(record, "plan", {"ok": True}, job)
        mod.set_answers(record, {"q1": "files", "q2": "no"})
        self.assertEqual(mod.output_status(record, "plan"), "stale")


class JobTests(unittest.TestCase):
    def test_older_job_result_discarded_and_logged(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        first = mod.new_job(record, "build")
        other = _profile()
        other["facts"]["identity.model"]["value"] = "Old"
        mod.set_profile(record, "old32", other)
        second = mod.new_job(record, "build")
        self.assertFalse(mod.job_is_current(record, first))
        self.assertTrue(mod.job_is_current(record, second))
        mod.discard_stale_result(record, first, "build")
        self.assertEqual(record["jobs"][first["id"]]["status"],
                         "discarded")
        self.assertTrue(any("discarded" in line
                            for line in record["log"]))

    def test_newer_job_same_inputs_supersedes(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        first = mod.new_job(record, "build")
        second = mod.new_job(record, "build")
        self.assertFalse(mod.job_is_current(record, first))
        self.assertTrue(mod.job_is_current(record, second))


class ResearchHookTests(unittest.TestCase):
    def test_research_change_marks_plan_for_rerun(self):
        record = mod.new_record()
        mod.set_profile(record, "flagship", _profile())
        mod.set_answers(record, {"q1": "both", "q2": "no"})
        mod.set_selection(record, ["files"])
        job = mod.new_job(record, "selection")
        mod.store_output(record, "plan", {"ok": True}, job)
        self.assertEqual(mod.output_status(record, "plan"), "current")
        self.assertTrue(mod.research_changed(record))
        self.assertEqual(mod.output_status(record, "plan"), "stale")
        self.assertTrue(any("re-run" in line for line in record["log"]))

    def test_research_change_without_plan_is_quiet(self):
        record = mod.new_record()
        self.assertFalse(mod.research_changed(record))


if __name__ == "__main__":
    unittest.main()
