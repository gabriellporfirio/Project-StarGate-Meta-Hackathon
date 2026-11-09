"""Nine-step click-through over HTTP (M6 Part 1, spec 1 table).

Flagship (files, assistant) walks all nine steps; old32 shows the
refused plan and its Build block with the reason. Builds run a stub
driver (no Android tools, no helper, no network): the real fresh build
runs separately under the helper and is recorded in the Part 1 report.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unittest

from stargate import appserver

from .apphelper import AppCase

BRAND_RE = re.compile(r"\b(stargate|spare)\b", re.IGNORECASE)

STUB_SRC = (
    "import json,sys,time;"
    "job=sys.argv[1];"
    "print('STAGE stub-build start',flush=True);"
    "time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else 0.1);"
    "manifest={'build_id':sys.argv[2],'selection':['files','assistant'],"
    "'selection_result':'fits','labels':[],"
    "'artifacts':[{'path':'system-stargate-files-assistant.img',"
    "'bytes':1622147072,'sha256':'%s'},"
    "{'path':'system-stargate-files-assistant.img.gz','bytes':654703016,"
    "'sha256':'%s'}]," % ("a" * 64, "b" * 64) +
    "'apps':[{'package':'com.stargate.launcher','bytes':12787,"
    "'sha256':'%s'}," % ("c" * 64) +
    "{'package':'com.stargate.files','bytes':16811,'sha256':'%s'}," % ("d" * 64) +
    "{'package':'com.stargate.assistant','bytes':24777688,"
    "'sha256':'%s'}]," % ("e" * 64) +
    "'model_files':[{'name':'model.pte','bytes':1135951488,"
    "'sha256':'%s'}]," % ("f" * 64) +
    "'model_delivery':'after_boot','model_payload_path':'model-payload',"
    "'image_bytes':1622147072,'partition_limit_bytes':1622147072};"
    "summary={'build_id':sys.argv[2],'selection':['files','assistant'],"
    "'image':manifest['artifacts'][0],"
    "'image_bytes':1622147072,'partition_limit_bytes':1622147072,"
    "'gz':manifest['artifacts'][1],'model_delivery':'after_boot',"
    "'model_payload_path':'model-payload',"
    "'payload_files':manifest['model_files'],"
    "'apps':manifest['apps']};"
    "result={'status':'ok','manifest':manifest,'summary':summary,"
    "'checks':[{'check':'launcher-config-equals-manifest',"
    "'build_id':sys.argv[2],'services':['assistant','files'],"
    "'labels':[]}]};"
    "open(job+'/result.json','w').write(json.dumps(result));"
    "print('STAGE stub-build done',flush=True)"
)


def stub_factory(_app, job_dir, build_id, delay="0.1"):
    """Fake build command: writes result.json, no heavy tools."""
    return [sys.executable, "-c", STUB_SRC, job_dir, build_id, delay]


class ClickThroughTests(AppCase):
    def _flow_to_plan(self, headers, profile, services):
        status, _h, body = self.request(
            "POST", "/api/connect", {"profile": profile}, headers)
        self.assertEqual(status, 200, body[:300])
        status, _h, body = self.request(
            "POST", "/api/questions",
            {"answers": {"q1": "both", "q2": "no"}}, headers)
        self.assertEqual(status, 200, body[:300])
        status, _h, body = self.request(
            "POST", "/api/selection", {"services": services}, headers)
        self.assertEqual(status, 200, body[:300])
        return json.loads(body.decode("utf-8"))["state"]

    def _wait_job(self, headers, job_id, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            status, _h, body = self.request(
                "GET", "/api/jobs/" + job_id,
                headers={"Cookie": headers["cookie"]})
            self.assertEqual(status, 200, body[:300])
            payload = json.loads(body.decode("utf-8"))
            if payload["job"]["status"] != "running":
                return payload
            time.sleep(0.2)
        self.fail("job %s still running" % job_id)

    def test_flagship_all_nine_steps(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        state = self._flow_to_plan(headers, "flagship",
                                   ["files", "assistant"])
        # Services: the M3 plan fits with reasons and numbers.
        plan = state["outputs"]["plan"]
        self.assertEqual(plan["status"], "current")
        self.assertEqual(plan["data"]["selection_result"], "fits")
        self.assertTrue(plan["data"]["fit"]["checks"])
        # Research + M5 honesty placeholders; live disabled.
        self.assertIn("No recorded research",
                      state["outputs"]["research"]["message"])
        self.assertIn("No install plan has been researched",
                      state["outputs"]["m5"]["message"])
        self.assertFalse(state["live"]["enabled"])
        # Build: fresh job with real progress.
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        payload = json.loads(body.decode("utf-8"))
        job_id = payload["job_id"]
        build_id = payload["build_id"]
        self.assertTrue(build_id)
        done = self._wait_job(headers, job_id)
        self.assertEqual(done["job"]["status"], "done")
        self.assertTrue(any("STAGE" in line
                            for line in done["progress"]))
        # Build output current with ID, sizes and checks.
        status, _h, body = self.request(
            "GET", "/api/state", None, {"Cookie": headers["cookie"]})
        state = json.loads(body.decode("utf-8"))["state"]
        build = state["outputs"]["build"]
        self.assertEqual(build["status"], "current")
        summary = build["data"]["summary"]
        self.assertEqual(summary["build_id"], build_id)
        self.assertEqual(summary["image_bytes"],
                         summary["partition_limit_bytes"])
        self.assertTrue(build["data"]["checks"])
        # Boot: Mac steps generated from this build's manifest.
        status, _h, body = self.request(
            "GET", "/api/boot", None, {"Cookie": headers["cookie"]})
        self.assertEqual(status, 200, body[:300])
        boot = json.loads(body.decode("utf-8"))
        self.assertEqual(boot["target"], "Android Emulator (this demo)")
        self.assertIn(build_id, boot["steps"])
        self.assertIn("system-stargate-files-assistant.img", boot["steps"])
        # Done: what was built plus the phone-link placeholder.
        self.assertIn("once the phone is paired",
                      state["phone_link"]["message"])
        # No brand words in the served prose. The build output is
        # excluded: it carries package IDs and artifact file names
        # (technical identifiers, shown only in artifact lines and
        # shell commands, never as titles or labels).
        prose = dict(state)
        prose["outputs"] = dict(state["outputs"])
        prose["outputs"].pop("build", None)
        self.assertIsNone(BRAND_RE.search(json.dumps(prose)))

    def test_relic_refused_plan_blocks_build_with_reason(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        state = self._flow_to_plan(headers, "relic",
                                   ["files", "assistant"])
        plan = state["outputs"]["plan"]
        self.assertEqual(plan["status"], "current")
        self.assertEqual(plan["data"]["selection_result"], "refused")
        reasons = plan["data"]["fit"]["reasons"]
        self.assertTrue(reasons)
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 409)
        error = json.loads(body.decode("utf-8"))["error"]
        self.assertIn("refused", error)
        self.assertIn(reasons[0][:40], error)
        self.assertIsNone(BRAND_RE.search(json.dumps(state)))

    def test_build_blocked_for_stale_plan(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "budget"}, headers)
        self.assertEqual(status, 200)
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 409)
        self.assertIn("out of date",
                      json.loads(body.decode("utf-8"))["error"])

    def test_build_blocked_without_plan(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 409)

    def test_no_reuse_second_build_same_plan_refused(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        first = json.loads(body.decode("utf-8"))
        self._wait_job(headers, first["job_id"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 409)
        self.assertIn("refusing to reuse",
                      json.loads(body.decode("utf-8"))["error"])

    def test_second_build_while_running_is_429(self):
        def slow(app, job_dir, build_id):
            return stub_factory(app, job_dir, build_id, delay="3")

        self.start_app(build_cmd_factory=slow)
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        first = json.loads(body.decode("utf-8"))
        status, _h, _b = self.request("POST", "/api/build", {},
                                      headers)
        self.assertEqual(status, 429)
        done = self._wait_job(headers, first["job_id"])
        self.assertEqual(done["job"]["status"], "done")

    def test_older_build_discarded_and_logged(self):
        def slow(app, job_dir, build_id):
            return stub_factory(app, job_dir, build_id, delay="2")

        self.start_app(build_cmd_factory=slow)
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        job_id = json.loads(body.decode("utf-8"))["job_id"]
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "budget"}, headers)
        self.assertEqual(status, 200)
        done = self._wait_job(headers, job_id)
        self.assertEqual(done["job"]["status"], "discarded")
        status, _h, body = self.request(
            "GET", "/api/state", None, {"Cookie": headers["cookie"]})
        state = json.loads(body.decode("utf-8"))["state"]
        self.assertEqual(state["outputs"]["build"]["status"], "missing")
        self.assertTrue(any("discarded" in line
                            for line in state["log"]))

    def test_helper_pause_marks_job_paused(self):
        self.start_app(build_cmd_factory=lambda a, j, b: [
            sys.executable, "-c", "import sys; sys.exit(75)"])
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        job_id = json.loads(body.decode("utf-8"))["job_id"]
        done = self._wait_job(headers, job_id)
        self.assertEqual(done["job"]["status"], "paused")
        self.assertIn("PAUSED", done["job"]["reason"])

    def test_boot_without_build_is_409(self):
        # Regression: missing build once crashed with KeyError.
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        status, _h, body = self.request(
            "GET", "/api/boot", None, {"Cookie": headers["cookie"]})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body.decode("utf-8"))["status"],
                         "missing")

    def test_boot_with_stale_build_is_409(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        self._flow_to_plan(headers, "flagship", ["files", "assistant"])
        status, _h, body = self.request("POST", "/api/build", {},
                                        headers)
        self.assertEqual(status, 202, body[:300])
        self._wait_job(headers, json.loads(body.decode("utf-8"))["job_id"])
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "budget"}, headers)
        self.assertEqual(status, 200)
        status, _h, body = self.request(
            "GET", "/api/boot", None, {"Cookie": headers["cookie"]})
        self.assertEqual(status, 409)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["status"], "stale")
        self.assertIn("out of date", payload["message"])

    def test_input_validation(self):
        self.start_app(build_cmd_factory=stub_factory)
        headers = self.signin()
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "nope"}, headers)
        self.assertEqual(status, 422)
        status, _h, _b = self.request(
            "POST", "/api/questions", {"answers": {"q1": "x"}},
            headers)
        self.assertEqual(status, 422)
        status, _h, _b = self.request(
            "POST", "/api/selection", {"services": []}, headers)
        self.assertEqual(status, 422)
        status, _h, _b = self.request(
            "POST", "/api/selection", {"services": ["nope"]}, headers)
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
