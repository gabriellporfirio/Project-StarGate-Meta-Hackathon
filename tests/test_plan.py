"""Offline tests for stargate plan (spec sections 5, 6, 7)."""
from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stargate import catalog, cli, meta, plan, scan

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "schemas" / "plan-examples"
FAKE_KEY = "plan-fake-key-do-not-use"


def _research(name):
    return str(REPO / "fixtures" / "research" / ("%s.json" % name))


def _rules():
    return plan.load_rules()


def _catalog():
    models, _sha, _prov = plan.load_catalog_for_plan()
    return models


def _profile(name="flagship"):
    return scan.build_profile(name)


def _ok_send(text, model="muse-spark-1.3", finish_reason="stop"):
    def send(url, body, headers, timeout):
        send.calls.append(json.loads(body.decode("utf-8")))
        payload = {"choices": [{"message": {"content": text},
                                "finish_reason": finish_reason}],
                   "model": model,
                   "usage": {"completion_tokens": 10}}
        return 200, json.dumps(payload).encode("utf-8")

    send.calls = []
    return send


def _fail_send(status=500):
    def send(url, body, headers, timeout):
        send.calls.append(url)
        return status, b"{}"

    send.calls = []
    return send


def _raising_send(url, body, headers, timeout):
    raise AssertionError("no network call expected")


def _no_sleep(seconds):
    pass


def _write_temp(payload):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    with tmp:
        json.dump(payload, tmp)
    return tmp.name


class PlanExamples(unittest.TestCase):
    def test_every_example_matches_its_label(self):
        files = sorted(EXAMPLES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(example=path.name):
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                errors = plan.validate_plan(payload)
                if path.name.startswith("valid_"):
                    self.assertEqual(errors, [])
                elif path.name.startswith("invalid_"):
                    self.assertTrue(errors)
                else:
                    self.fail("example name must start valid_/invalid_")


class InputChecks(unittest.TestCase):
    def _base(self, **kwargs):
        params = {"sim": "flagship", "services_text": "files",
                  "research_path": _research("flagship")}
        params.update(kwargs)
        return plan.run_plan(**params)

    def test_wrong_phone_research_is_exit_6(self):
        code, _out, err = self._base(sim="old32")
        self.assertEqual(code, 6)
        self.assertIn("not this phone", err)

    def test_invalid_research_schema_is_exit_6(self):
        path = _write_temp({"schema_version": "research-v1"})
        try:
            code, _out, _err = self._base(research_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 6)

    def test_invalid_profile_schema_is_exit_6(self):
        path = _write_temp({"schema_version": "profile-v1"})
        try:
            code, _out, _err = plan.run_plan(
                profile_path=path, services_text="files")
        finally:
            os.unlink(path)
        self.assertEqual(code, 6)

    def test_unknown_service_is_exit_6(self):
        code, _out, _err = self._base(services_text="files,teleport")
        self.assertEqual(code, 6)

    def test_profile_source_errors_are_exit_6(self):
        code, _out, _err = plan.run_plan(
            sim="flagship", profile_path="/tmp/x.json",
            services_text="files")
        self.assertEqual(code, 6)
        code, _out, _err = plan.run_plan(services_text="files")
        self.assertEqual(code, 6)
        code, _out, _err = plan.run_plan(sim="nophone",
                                         services_text="files")
        self.assertEqual(code, 6)

    def test_missing_rules_are_exit_6(self):
        code, _out, _err = self._base(
            repo_root_path=Path("/nonexistent-m3-rules"))
        self.assertEqual(code, 6)

    def test_input_checks_run_before_any_model_call(self):
        def send(url, body, headers, timeout):
            send.calls.append(url)
            raise AssertionError("model must not be reached")

        send.calls = []
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, _out, _err = plan.run_plan(
                sim="old32", services_text="files",
                research_path=_research("flagship"), explain=True,
                send=send, sleep=_no_sleep)
        self.assertEqual(code, 6)
        self.assertEqual(send.calls, [])

    def test_no_network_without_explain(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_API_KEY", None)
            code, _out, _err = plan.run_plan(
                sim="flagship", services_text="files,assistant",
                research_path=_research("flagship"), send=_raising_send,
                sleep=_no_sleep)
            self.assertEqual(code, 0)
            code, _out, _err = plan.run_plan(
                sim="old32", services_text="files,assistant",
                research_path=_research("old32"), send=_raising_send,
                sleep=_no_sleep)
            self.assertEqual(code, 7)


class NoResearchFile(unittest.TestCase):
    def test_profile_without_research_is_not_exit_6(self):
        path = _write_temp(_profile("flagship"))
        try:
            code, stdout, _err = plan.run_plan(
                profile_path=path, services_text="files,assistant",
                json_only=True)
        finally:
            os.unlink(path)
        self.assertNotEqual(code, 6)
        report = json.loads(stdout)
        self.assertIsNone(report["inputs"]["research"])
        self.assertIsNone(report["handoff"]["provenance"]["research"])
        community = next(entry for entry in report["strategies"]
                         if entry["id"] == "community_build")
        self.assertEqual(community["status"], "unknown")
        self.assertIn("no research file",
                      [item["reason"] for item in community["missing"]])
        self.assertEqual(plan.validate_plan(report), [])

    def test_sim_without_research_is_not_exit_6(self):
        code, _out, _err = plan.run_plan(
            sim="flagship", services_text="files")
        self.assertEqual(code, 0)


class ProvenanceKept(unittest.TestCase):
    def test_synthetic_stays_synthetic(self):
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["inputs"]["profile"]["provenance"],
                         "synthetic")
        self.assertEqual(report["inputs"]["research"]["provenance"],
                         "synthetic")
        self.assertEqual(report["handoff"]["provenance"]["profile"],
                         "synthetic")
        self.assertEqual(report["handoff"]["provenance"]["research"],
                         "synthetic")
        self.assertEqual(report["provenance"], "synthetic")

    def test_replay_stays_replay(self):
        with open(_research("flagship"), encoding="utf-8") as handle:
            report = json.load(handle)
        report["provenance"] = "replay"
        research_path = _write_temp(report)
        profile = _profile("flagship")
        profile["simulated"] = False
        profile_path = _write_temp(profile)
        try:
            code, stdout, _err = plan.run_plan(
                profile_path=profile_path, services_text="files",
                research_path=research_path, json_only=True)
        finally:
            os.unlink(research_path)
            os.unlink(profile_path)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["inputs"]["profile"]["provenance"],
                         "live")
        self.assertEqual(report["inputs"]["research"]["provenance"],
                         "replay")
        self.assertEqual(report["handoff"]["provenance"]["research"],
                         "replay")
        self.assertEqual(report["provenance"], "replay")


class TwoPhones(unittest.TestCase):
    def test_flagship_and_old32_rank_differently(self):
        code_flag, out_flag, _err = plan.run_plan(
            sim="flagship", services_text="files,assistant",
            research_path=_research("flagship"), json_only=True)
        code_old, out_old, _err = plan.run_plan(
            sim="old32", services_text="files,assistant",
            research_path=_research("old32"), json_only=True)
        self.assertEqual(code_flag, 0)
        self.assertEqual(code_old, 7)
        flagship = json.loads(out_flag)
        old32 = json.loads(out_old)
        flagship_order = [entry["id"] for entry in flagship["strategies"]]
        old32_order = [entry["id"] for entry in old32["strategies"]]
        self.assertNotEqual(flagship_order, old32_order)
        self.assertEqual(old32["selection_result"], "refused")
        self.assertTrue(old32["fit"]["reasons"])


class ExitPrecedence(unittest.TestCase):
    def test_refused_plus_failed_explain_gives_7(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_API_KEY", None)
            code, stdout, _err = plan.run_plan(
                sim="old32", services_text="files,assistant",
                research_path=_research("old32"), explain=True,
                json_only=True, sleep=_no_sleep)
        self.assertEqual(code, 7)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"]["status"], "failed")
        self.assertEqual(report["selection_result"], "refused")
        self.assertEqual(plan.validate_plan(report), [])

    def test_failed_explain_without_refusal_gives_2(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_API_KEY", None)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".json", delete=False)
            tmp.close()
            try:
                code, stdout, err = plan.run_plan(
                    sim="flagship", services_text="files",
                    research_path=_research("flagship"), explain=True,
                    json_out=tmp.name, sleep=_no_sleep)
            finally:
                pass
        self.assertEqual(code, 2)
        self.assertIn("Selection result", stdout)
        self.assertIn("warning", err)
        ok, errors = plan.validate_file(tmp.name)
        os.unlink(tmp.name)
        self.assertTrue(ok, errors)

    def test_failed_explain_can_give_5(self):
        send = _fail_send(500)
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files",
                research_path=_research("flagship"), explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 5)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"],
                         {"status": "failed", "code": 5,
                          "model": plan.EXPLAIN_MODEL, "reason": None})
        self.assertEqual(len(send.calls), 2)  # one retry


class Handoff(unittest.TestCase):
    def test_fits_handoff_is_complete(self):
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files,assistant",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        handoff = report["handoff"]
        self.assertEqual(handoff["services"], ["files", "assistant"])
        self.assertEqual(handoff["selection_result"], "fits")
        self.assertEqual(handoff["build_allowed"], "emulator_only")
        self.assertEqual(handoff["candidate_strategies"],
                         ["community_build", "generic_system_image",
                          "linux", "on_top_of_android"])
        areas = [item["area"] for item in handoff["unresolved"]]
        self.assertIn("image_size", areas)
        self.assertIn("strategy", areas)
        loaded, errors = catalog.load_catalog(
            REPO / "catalog" / "models.json")
        self.assertEqual(errors, [])
        entry = next(item for item in loaded["models"]
                     if item["id"] == handoff["model"]["id"])
        self.assertEqual(handoff["model"]["files"], entry["files"])
        self.assertEqual(handoff["model"]["total_bytes"],
                         sum(item["bytes"] for item in entry["files"]))
        rules = plan.load_rules()
        self.assertEqual(report["rules"], rules)

    def test_unconfirmed_handoff_has_no_model(self):
        unknown_catalog = {
            "schema_version": "catalog-v1",
            "models": [{
                "id": "mystery", "source": None, "revision": None,
                "files": [{"name": "w", "bytes": None}],
                "format": None, "runtime": None, "abi": None,
                "licence": None, "estimates": [],
                "provenance": {"kind": "synthetic", "note": "SIMULATED"},
            }],
        }
        path = _write_temp(unknown_catalog)
        try:
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files,assistant",
                research_path=_research("flagship"), json_only=True,
                catalog_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["selection_result"], "unconfirmed")
        self.assertEqual(report["build_allowed"], "emulator_only")
        self.assertIsNone(report["handoff"]["model"])
        self.assertTrue(report["handoff"]["model_reason"])
        areas = [item["area"] for item in report["handoff"]["unresolved"]]
        self.assertIn("image_size", areas)
        self.assertIn("fit", areas)

    def test_files_only_pair_storage(self):
        full = _profile("flagship")
        tiny = copy.deepcopy(full)
        tiny["facts"]["resources.data_free"]["value"] = 100
        full_path = _write_temp(full)
        tiny_path = _write_temp(tiny)
        try:
            code_full, out_full, _err = plan.run_plan(
                profile_path=full_path, services_text="files",
                research_path=_research("flagship"), json_only=True)
            code_tiny, out_tiny, _err = plan.run_plan(
                profile_path=tiny_path, services_text="files",
                research_path=_research("flagship"), json_only=True)
        finally:
            os.unlink(full_path)
            os.unlink(tiny_path)
        self.assertEqual(code_full, 0)
        self.assertEqual(code_tiny, 7)
        enough = json.loads(out_full)
        little = json.loads(out_tiny)
        self.assertEqual(enough["handoff"]["build_allowed"],
                         "emulator_only")
        self.assertEqual(little["selection_result"], "refused")
        self.assertEqual(little["handoff"]["build_allowed"], "none")
        self.assertIn("resources.data_free",
                      " ".join(little["fit"]["reasons"]))
        self.assertEqual(little["handoff"]["reasons"],
                         little["fit"]["reasons"])
        self.assertIn("resources.data_free",
                      " ".join(little["handoff"]["reasons"]))
        self.assertNotEqual(enough["handoff"], little["handoff"])

    def test_handoff_disagreement_rejected(self):
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        bad = copy.deepcopy(report)
        bad["handoff"]["selection_result"] = "refused"
        self.assertTrue(plan.validate_plan(bad))
        bad = copy.deepcopy(report)
        bad["handoff"]["build_allowed"] = "none"
        self.assertTrue(plan.validate_plan(bad))
        bad = copy.deepcopy(report)
        bad["selection_result"] = "refused"
        bad["fit"]["result"] = "refused"
        bad["handoff"]["selection_result"] = "refused"
        bad["handoff"]["build_allowed"] = "emulator_only"
        self.assertTrue(plan.validate_plan(bad))

    def test_refused_handoff_reasons_must_match(self):
        code, stdout, _err = plan.run_plan(
            sim="old32", services_text="files",
            research_path=_research("old32"), json_only=True)
        self.assertEqual(code, 7)
        report = json.loads(stdout)
        self.assertEqual(report["selection_result"], "refused")
        self.assertEqual(report["handoff"]["reasons"],
                         report["fit"]["reasons"])
        bad = copy.deepcopy(report)
        bad["handoff"]["reasons"] = []
        self.assertTrue(plan.validate_plan(bad))

    def test_handoff_reasons_must_match_for_every_result(self):
        # M4 section 7: fits and unconfirmed plans are held to the
        # same handoff/fit reasons equality as refused ones.
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["selection_result"], "fits")
        self.assertEqual(report["handoff"]["reasons"],
                         report["fit"]["reasons"])
        bad = copy.deepcopy(report)
        bad["handoff"]["reasons"] = ["forged reason"]
        errors = plan.validate_plan(bad)
        self.assertTrue(any("handoff reasons" in item for item in errors))
        bad = copy.deepcopy(report)
        bad["handoff"]["reasons"] = list(reversed(
            report["handoff"]["reasons"])) + ["extra"]
        self.assertTrue(plan.validate_plan(bad))
        unknown_catalog = {
            "schema_version": "catalog-v1",
            "models": [{
                "id": "mystery", "source": None, "revision": None,
                "files": [{"name": "w", "bytes": None}],
                "format": None, "runtime": None, "abi": None,
                "licence": None, "estimates": [],
                "provenance": {"kind": "synthetic", "note": "SIMULATED"},
            }],
        }
        path = _write_temp(unknown_catalog)
        try:
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files,assistant",
                research_path=_research("flagship"), json_only=True,
                catalog_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["selection_result"], "unconfirmed")
        self.assertEqual(report["handoff"]["reasons"],
                         report["fit"]["reasons"])
        bad = copy.deepcopy(report)
        bad["handoff"]["reasons"] = []
        errors = plan.validate_plan(bad)
        self.assertTrue(any("handoff reasons" in item for item in errors))


class ModelCannotChangeDecisions(unittest.TestCase):
    def test_recorded_claim_leaves_status_unchanged(self):
        text = ("This phone can run GSI. See resources.ram_total "
                "and F1. All good.")
        send = _ok_send(text)
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files,assistant",
                research_path=_research("flagship"), explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        explained = json.loads(stdout)
        code, plain_out, _err = plan.run_plan(
            sim="flagship", services_text="files,assistant",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(code, 0)
        plain = json.loads(plain_out)
        self.assertEqual(explained["strategies"], plain["strategies"])
        self.assertEqual(explained["fit"], plain["fit"])
        self.assertEqual(explained["selection_result"],
                         plain["selection_result"])
        self.assertEqual(explained["explanation_status"]["status"], "ok")
        self.assertEqual(explained["notes"]["label"], plan.NOTES_LABEL)
        self.assertIn("resources.ram_total",
                      explained["notes"]["cited_ids"])
        self.assertIn("F1", explained["notes"]["cited_ids"])
        self.assertEqual(explained["notes"]["missing_ids"], [])
        self.assertEqual(plan.validate_plan(explained), [])

    def test_missing_fact_id_is_flagged(self):
        send = _ok_send("Check F99 and resources.bogus today.")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files",
                research_path=_research("flagship"), explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertIn("F99", report["notes"]["missing_ids"])
        self.assertIn("resources.bogus", report["notes"]["missing_ids"])

    def test_explain_uses_one_capped_call(self):
        send = _ok_send("Fine.")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, _out, _err = plan.run_plan(
                sim="flagship", services_text="files", explain=True,
                send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        self.assertEqual(len(send.calls), 1)
        self.assertEqual(send.calls[0]["model"], "muse-spark-1.3")
        self.assertEqual(send.calls[0]["max_completion_tokens"], 4096)

    def test_m4_cap_keeps_one_retry_and_cut_short_label(self):
        # M4 section 7: the cap is 4096, with one retry and the
        # "cut short" label when the cap cuts the text off.
        self.assertEqual(plan.EXPLAIN_MAX_TOKENS, 4096)
        send = _ok_send("A complete sentence.", finish_reason="length")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files", explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        self.assertEqual(len(send.calls), 1)
        self.assertEqual(
            send.calls[0]["max_completion_tokens"], 4096)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"]["status"],
                         "incomplete")
        self.assertEqual(report["notes"]["label"],
                         plan.NOTES_LABEL_INCOMPLETE)
        failing = _fail_send(500)
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, _out, _err = plan.run_plan(
                sim="flagship", services_text="files", explain=True,
                json_only=True, send=failing, sleep=_no_sleep)
        self.assertEqual(code, 5)
        self.assertEqual(len(failing.calls), 2)  # one retry


class IncompleteExplanations(unittest.TestCase):
    def test_length_finish_is_incomplete(self):
        send = _ok_send("A complete sentence.", finish_reason="length")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files",
                research_path=_research("flagship"), explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"]["status"],
                         "incomplete")
        self.assertEqual(report["explanation_status"]["reason"],
                         "finish_reason length")
        self.assertIsNone(report["explanation_status"]["code"])
        self.assertEqual(report["notes"]["label"],
                         plan.NOTES_LABEL_INCOMPLETE)
        self.assertEqual(plan.validate_plan(report), [])

    def test_mid_sentence_end_is_incomplete(self):
        send = _ok_send("Same kind of phone as you have now,")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files",
                research_path=_research("flagship"), explain=True,
                send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        self.assertIn("model explanation, cut short, not checked", stdout)
        self.assertIn("Cut short: no closing punctuation.", stdout)

    def test_closed_text_stays_ok(self):
        send = _ok_send('All good (see "notes").')
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files", explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"]["status"], "ok")
        self.assertIsNone(report["explanation_status"]["reason"])
        self.assertEqual(report["notes"]["label"], plan.NOTES_LABEL)

    def test_any_non_stop_finish_is_incomplete(self):
        # M4 section 7: any finish_reason other than stop counts as
        # incomplete, even with clean closing punctuation.
        send = _ok_send("A complete sentence.", finish_reason="tool_calls")
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files",
                research_path=_research("flagship"), explain=True,
                json_only=True, send=send, sleep=_no_sleep)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["explanation_status"]["status"],
                         "incomplete")
        self.assertEqual(report["explanation_status"]["reason"],
                         "finish_reason tool_calls")
        self.assertEqual(report["notes"]["label"],
                         plan.NOTES_LABEL_INCOMPLETE)
        self.assertEqual(plan.validate_plan(report), [])
        self.assertEqual(plan.cut_short_reason("Fine.", None),
                         "finish_reason None")
        self.assertEqual(plan.cut_short_reason("Fine.", "stop"), "")


class ResearchLayers(unittest.TestCase):
    def test_candidates_and_notes_are_ignored(self):
        with open(_research("flagship"), encoding="utf-8") as handle:
            report = json.load(handle)
        for fact in report["facts"]:
            if fact["name"] == "repos.candidates":
                fact["value"]["repos"].append(
                    {"full_name": "junk/forged-tree", "via": "forged"})
            if fact["name"] == "ai.candidates":
                fact["value"]["models"].append(
                    {"repo_id": "junk/forged-model", "revision": None,
                     "file": None, "bytes": 1, "base_model": None,
                     "license": None})
        report["notes"]["text"] = "SIMULATED forged notes claiming GSI."
        path = _write_temp(report)
        try:
            code, stdout, _err = plan.run_plan(
                sim="flagship", services_text="files,assistant",
                research_path=path, json_only=True)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        forged = json.loads(stdout)
        code, plain_out, _err = plan.run_plan(
            sim="flagship", services_text="files,assistant",
            research_path=_research("flagship"), json_only=True)
        self.assertEqual(plan.validate_plan(forged), [])
        self.assertEqual(forged["strategies"],
                         json.loads(plain_out)["strategies"])


class CliAndSchema(unittest.TestCase):
    def test_json_parsed_from_real_stdout(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["plan", "--sim", "flagship",
                             "--services", "files", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(buf.getvalue())
        self.assertEqual(plan.validate_plan(report), [])

    def test_cli_refused_exit(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["plan", "--sim", "old32",
                             "--services", "files,assistant",
                             "--research", _research("old32")])
        self.assertEqual(code, 7)
        self.assertIn("Selection result: refused", buf.getvalue())

    def test_validate_entry_point(self):
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files", json_only=True)
        self.assertEqual(code, 0)
        path = _write_temp(json.loads(stdout))
        try:
            self.assertEqual(plan.main(["validate", path]), 0)
            ok, errors = plan.validate_file(path)
            self.assertTrue(ok, errors)
        finally:
            os.unlink(path)
        self.assertEqual(plan.main([]), 2)
        self.assertEqual(
            plan.main(["validate", "/nonexistent-m3-plan.json"]), 6)


if __name__ == "__main__":
    unittest.main()
