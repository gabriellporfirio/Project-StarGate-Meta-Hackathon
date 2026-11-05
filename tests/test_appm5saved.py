"""Saved M5 budget run in the Install plan panel (P5/P5b/P7).

The panel shows the copied 4 Nov replay only for the budget profile, only
when the copies pass integrity (SHA-256 vs SOURCES.txt) and the current
profile and plan hash (M5's own byte hashing) equal the recorded ones and
the current selection equals the recorded plan's services. Anything else
shows "Saved run unavailable" or "Saved run is out of date ..." WITHOUT
the old result. Light only: loopback, temp dirs, no M5 code, no network.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import unittest
from unittest import mock

from stargate import appsession as session_module
from stargate import appserver

from .apphelper import AppCase

EVIL = "<script>alert(1)</script>"


def _replay(profile_sha, plan_sha, quote="A plain quote."):
    """Minimal synthetic replay with the real schema's key shape.

    Like the real budget replay, it records no selection hash: the
    selection comes from the recorded plan file (P7).
    """
    inputs = {
        "profile": {"provenance": "synthetic", "sha256": profile_sha},
        "plan": {"provenance": "synthetic", "sha256": plan_sha},
    }
    ok_rules = [{"rule": name, "status": "pass", "reasons": []}
                for name in ("I-FORMAT", "I-QUOTE")]
    bad_rules = [{"rule": "I-FORMAT", "status": "fail",
                  "reasons": ["bad shape"]},
                 {"rule": "I-QUOTE", "status": "not_evaluated",
                  "reasons": ["needs a well-formed draft"]}]
    return {
        "result": "do_not_install",
        "model": {"id": "muse-spark-1.3", "provider": "meta"},
        "provenance": "replay",
        "inputs": inputs,
        "fetch_records": [{
            "id": "R2", "byte_size": 162511,
            "url_final": "https://source.android.com/docs/setup/test/running",  # noqa: E501
            "excerpt": {"ranges": [[5616, 6526]],
                        "sha256": "e" * 64}}],
        "drafts": [
            {"index": 1, "finish_reason": "stop",
             "model": {"id": "muse-spark-1.3", "provider": "meta"},
             "parsed": {"do_not_install": True},
             "rule_results": bad_rules,
             "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                       "reasoning_tokens": 30}},
            {"index": 2, "finish_reason": "stop",
             "model": {"id": "muse-spark-1.3", "provider": "meta"},
             "parsed": {"do_not_install": {
                 "reason": "locked, install not allowed",
                 "citation": {"rule": "I-UNLOCK", "fetch_id": "R2",
                             "quote": quote}}},
             "rule_results": ok_rules,
             "usage": {"prompt_tokens": 11, "completion_tokens": 21,
                       "reasoning_tokens": 31}},
        ],
    }


def _write_file(tmp: str, name: str, obj: dict) -> str:
    raw = (json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")
    with open(os.path.join(tmp, name), "wb") as handle:
        handle.write(raw)
    return hashlib.sha256(raw).hexdigest()


def _write_saved(test: unittest.TestCase, replay: dict,
                 services=("files",), corrupt: str | None = None) -> str:
    """Temp m5-saved dir: replay + plan + SOURCES.txt. Returns the dir.

    corrupt is None, "replay" or "plan" (that SOURCES line gets zeros).
    """
    tmp = tempfile.mkdtemp(prefix="m5saved-")
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    replay_hex = _write_file(tmp, "budget-replay-v3.json", replay)
    plan_hex = _write_file(tmp, "budget-plan-v3.json",
                           {"handoff": {"services": list(services)}})
    if corrupt == "replay":
        replay_hex = "0" * 64
    if corrupt == "plan":
        plan_hex = "0" * 64
    with open(os.path.join(tmp, "SOURCES.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(
            "# test copy\n"
            "sha256: %s  file: budget-replay-v3.json  source: test\n"
            "sha256: %s  file: budget-plan-v3.json  source: test\n"
            % (replay_hex, plan_hex))
    return tmp


class M5SavedTests(AppCase):
    def _budget_state(self, services):
        self.request("POST", "/api/connect", {"profile": "budget"},
                     self.headers)
        status, _, body = self.request(
            "POST", "/api/selection", {"services": services},
            self.headers)
        self.assertEqual(status, 200, body[:200])
        status, _, body = self.request(
            "GET", "/api/state", None, {"Cookie": self.headers["cookie"]})
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))["state"]

    def _m5(self):
        status, _, body = self.request(
            "GET", "/api/state", None, {"Cookie": self.headers["cookie"]})
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))["state"]["outputs"]["m5"]

    def _record(self):
        """Live session record (HTTP re-sorts keys; the comparison runs
        on record objects, so crafting must too)."""
        session_id = self.headers["cookie"].split("=", 1)[1]
        return self.app.sessions[session_id]["record"]

    def _record_current(self, tmp, record, services):
        """Write a saved dir whose records match this record. No
        hard-coding: recorded hashes are computed from the live record
        with M5's own byte hashing."""
        replay = _replay(
            session_module.m5_style_hash(record["profile"]["data"]),
            session_module.m5_style_hash(
                record["outputs"]["plan"]["data"]))
        replay_hex = _write_file(tmp, "budget-replay-v3.json", replay)
        plan_hex = _write_file(
            tmp, "budget-plan-v3.json",
            {"handoff": {"services": list(services)}})
        with open(os.path.join(tmp, "SOURCES.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "sha256: %s  file: budget-replay-v3.json  source: test\n"
                "sha256: %s  file: budget-plan-v3.json  source: test\n"
                % (replay_hex, plan_hex))

    def test_matching_inputs_show_saved_run(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files", "assistant"])
        self._record_current(tmp, self._record(), ["files", "assistant"])
        view = self._m5()
        self.assertEqual(view["status"], "saved")
        self.assertEqual(view["message"], appserver.M5_SAVED_TITLE)
        data = view["data"]
        self.assertEqual(data["result"], "do_not_install")
        self.assertEqual([d["index"] for d in data["drafts"]], [1, 2])
        self.assertEqual([d["verdict"] for d in data["drafts"]],
                         ["REJECTED", "ACCEPTED"])
        self.assertEqual(data["refusal"]["quote"], "A plain quote.")
        self.assertIn("running", data["refusal"]["url"])
        self.assertEqual(data["model"]["id"], "muse-spark-1.3")
        self.assertEqual(view["inputs"]["selection"]["recorded"],
                         ["assistant", "files"])

    def test_profile_mismatch_shows_outdated_without_result(self):
        replay = _replay("0" * 64, "1" * 64)
        tmp = _write_saved(self, replay)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files", "assistant"])
        view = self._m5()
        self.assertEqual(view["status"], "outdated")
        self.assertEqual(view["message"], appserver.M5_SAVED_OUTDATED)
        self.assertIsNone(view["data"])
        pairs = view["inputs"]
        self.assertEqual(pairs["profile"]["recorded"], "0" * 64)
        self.assertTrue(pairs["profile"]["current"])
        self.assertNotEqual(pairs["profile"]["recorded"],
                            pairs["profile"]["current"])

    def test_changed_selection_shows_outdated(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        # Recorded for Files-only; the app now selects both.
        self._budget_state(["files"])
        self._record_current(tmp, self._record(), ["files"])
        self._budget_state(["files", "assistant"])
        view = self._m5()
        self.assertEqual(view["status"], "outdated")
        self.assertIsNone(view["data"])
        self.assertEqual(view["inputs"]["selection"]["recorded"],
                         ["files"])
        self.assertEqual(view["inputs"]["selection"]["current"],
                         ["assistant", "files"])

    def test_plan_without_services_shows_outdated(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files"])
        record = self._record()
        replay = _replay(
            session_module.m5_style_hash(record["profile"]["data"]),
            session_module.m5_style_hash(
                record["outputs"]["plan"]["data"]))
        replay_hex = _write_file(tmp, "budget-replay-v3.json", replay)
        plan_hex = _write_file(tmp, "budget-plan-v3.json",
                               {"handoff": {}})
        with open(os.path.join(tmp, "SOURCES.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "sha256: %s  file: budget-replay-v3.json  source: test\n"
                "sha256: %s  file: budget-plan-v3.json  source: test\n"
                % (replay_hex, plan_hex))
        view = self._m5()
        self.assertEqual(view["status"], "outdated")
        self.assertIsNone(view["data"])
        self.assertIsNone(view["inputs"]["selection"]["recorded"])

    def test_hash_mismatch_shows_unavailable(self):
        replay = _replay("0" * 64, "1" * 64)
        for corrupt in ("replay", "plan"):
            tmp = _write_saved(self, replay, corrupt=corrupt)
            self.start_app(m5_saved_dir=tmp)
            self.headers = self.signin()
            self._budget_state(["files"])
            view = self._m5()
            self.assertEqual(view["status"], "unavailable", corrupt)
            self.assertEqual(view["message"],
                             appserver.M5_SAVED_UNAVAILABLE, corrupt)
            self.assertIsNone(view["data"])
            self.server.shutdown()
            self.server.server_close()

    def test_missing_file_shows_unavailable(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files"])
        view = self._m5()
        self.assertEqual(view["status"], "unavailable")
        self.assertIsNone(view["data"])

    def test_quote_served_raw_for_text_rendering(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files"])
        record = self._record()
        replay = _replay(
            session_module.m5_style_hash(record["profile"]["data"]),
            session_module.m5_style_hash(
                record["outputs"]["plan"]["data"]),
            quote="x %s y" % EVIL)
        replay_hex = _write_file(tmp, "budget-replay-v3.json", replay)
        plan_hex = _write_file(tmp, "budget-plan-v3.json",
                               {"handoff": {"services": ["files"]}})
        with open(os.path.join(tmp, "SOURCES.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(
                "sha256: %s  file: budget-replay-v3.json  source: test\n"
                "sha256: %s  file: budget-plan-v3.json  source: test\n"
                % (replay_hex, plan_hex))
        view = self._m5()
        # Served verbatim; the client inserts it with textContent only
        # (test_client_uses_text_not_html scans the render code).
        self.assertEqual(view["data"]["refusal"]["quote"],
                         "x %s y" % EVIL)

    def test_other_profiles_keep_placeholder(self):
        replay = _replay("0" * 64, "1" * 64)
        tmp = _write_saved(self, replay)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        for profile in ("flagship", "old32", "locked"):
            status, _, body = self.request(
                "POST", "/api/connect", {"profile": profile},
                self.headers)
            self.assertEqual(status, 200, body[:200])
            view = self._m5()
            self.assertEqual(view["status"], "part2", profile)
            self.assertEqual(view["message"], appserver.M5_MESSAGE,
                             profile)
            self.assertIsNone(view["data"])

    def test_saved_view_needs_no_network(self):
        tmp = tempfile.mkdtemp(prefix="m5saved-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.start_app(m5_saved_dir=tmp)
        self.headers = self.signin()
        self._budget_state(["files", "assistant"])
        self._record_current(tmp, self._record(), ["files", "assistant"])
        session_id = self.headers["cookie"].split("=", 1)[1]

        def _no_network(*args, **kwargs):
            raise AssertionError("network used")

        with mock.patch.object(socket, "socket",
                               side_effect=_no_network), \
                mock.patch.object(socket, "create_connection",
                                  side_effect=_no_network):
            _csrf, state = self.app.state_for(session_id)
        self.assertEqual(state["outputs"]["m5"]["status"], "saved")


if __name__ == "__main__":
    unittest.main()
