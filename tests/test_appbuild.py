"""Fresh-build driver rules (M6 Part 1, Build step).

Light only: stub APK/image builders and hand-made APK zips. No Android
tools, no helper, no image builder, no writes outside temp dirs.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

from stargate import appbuild as mod
from stargate.build.image import AFTER_BOOT_LABEL, build_id_for_plan
from stargate.plan import run_plan


def _plan_file(services, sim="flagship"):
    exit_code, stdout_text, _stderr = run_plan(
        sim=sim, services_text=",".join(services), json_only=True)
    assert exit_code in (0, 7), stdout_text[:200]
    tmp = tempfile.mkdtemp(prefix="appbuild-plan-")
    path = os.path.join(tmp, "plan.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(stdout_text)
    return tmp, path, json.loads(stdout_text)


def _fake_apk(path, config):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("assets/stargate-build.json",
                         json.dumps(config, sort_keys=True))


def _manifest(build_id, services, labels):
    return {
        "schema_version": "build-v1",
        "build_id": build_id,
        "selection": list(services),
        "selection_result": "fits",
        "labels": list(labels),
        "artifacts": [
            {"path": "system-stargate-files-assistant.img",
             "bytes": 1622147072, "sha256": "a" * 64},
            {"path": "system-stargate-files-assistant.img.gz",
             "bytes": 654703016, "sha256": "b" * 64}],
        "apps": [{"package": "com.stargate.launcher", "bytes": 12787,
                  "sha256": "c" * 64}],
        "model_files": [{"name": "model.pte", "bytes": 1135951488,
                         "sha256": "f" * 64}],
        "model_delivery": "after_boot",
        "model_payload_path": "model-payload",
        "image_bytes": 1622147072,
        "partition_limit_bytes": 1622147072,
    }


class FrozenGuardTests(unittest.TestCase):
    def test_job_dir_in_frozen_outputs_refused(self):
        with self.assertRaises(mod.DriverError):
            mod.run_fresh_build(
                "/none/plan.json", "/workspaces/m4a-tools/out/x",
                tempfile.gettempdir(), "id", "a.apk", "m", "r")

    def test_tools_dir_in_frozen_outputs_refused(self):
        with self.assertRaises(mod.DriverError):
            mod.run_fresh_build(
                "/none/plan.json", tempfile.gettempdir(),
                "/workspaces/m4a-tools", "id", "a.apk", "m", "r")


class GateTests(unittest.TestCase):
    def test_build_id_mismatch_fails(self):
        tmp, plan_path, _plan = _plan_file(["files", "assistant"])
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        job = tempfile.mkdtemp(prefix="appbuild-job-")
        self.addCleanup(shutil.rmtree, job, ignore_errors=True)
        with self.assertRaises(mod.DriverError) as ctx:
            mod.run_fresh_build(plan_path, job, job, "wrong-id",
                                "a.apk", "m", "r")
        self.assertIn("!= expected", str(ctx.exception))

    def test_refused_plan_fails_at_m4_gate(self):
        tmp, plan_path, plan = _plan_file(["files", "assistant"],
                                          sim="relic")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertEqual(plan["selection_result"], "refused")
        job = tempfile.mkdtemp(prefix="appbuild-job-")
        self.addCleanup(shutil.rmtree, job, ignore_errors=True)
        with open(plan_path, "rb") as handle:
            import hashlib
            plan_sha = hashlib.sha256(handle.read()).hexdigest()
        build_id = build_id_for_plan(plan, plan_sha)
        with self.assertRaises(mod.DriverError) as ctx:
            mod.run_fresh_build(plan_path, job, job, build_id,
                                "a.apk", "m", "r")
        self.assertIn("gate refuses", str(ctx.exception))


class LauncherCheckTests(unittest.TestCase):
    def test_match_passes(self):
        tmp = tempfile.mkdtemp(prefix="appbuild-apk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        apk = os.path.join(tmp, "stargate-launcher.apk")
        _fake_apk(apk, {"build_id": "b1",
                        "services": ["files", "assistant"],
                        "labels": [AFTER_BOOT_LABEL]})
        manifest = _manifest("b1", ["assistant", "files"],
                             [AFTER_BOOT_LABEL])
        check = mod.check_launcher_config(apk, manifest)
        self.assertEqual(check["check"],
                         "launcher-config-equals-manifest")
        self.assertEqual(check["services"], ["assistant", "files"])

    def test_mismatch_fails(self):
        tmp = tempfile.mkdtemp(prefix="appbuild-apk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest = _manifest("b1", ["files"], [])
        for config in ({"build_id": "other", "services": ["files"],
                        "labels": []},
                       {"build_id": "b1", "services": ["assistant"],
                        "labels": []},
                       {"build_id": "b1", "services": ["files"],
                        "labels": ["extra"]},
                       {"build_id": "b1"}):
            apk = os.path.join(tmp, "stargate-launcher.apk")
            _fake_apk(apk, config)
            with self.assertRaises(mod.DriverError, msg=repr(config)):
                mod.check_launcher_config(apk, manifest)

    def test_unreadable_apk_fails(self):
        tmp = tempfile.mkdtemp(prefix="appbuild-apk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest = _manifest("b1", ["files"], [])
        with self.assertRaises(mod.DriverError):
            mod.check_launcher_config(os.path.join(tmp, "missing.apk"),
                                      manifest)
        plain = os.path.join(tmp, "plain.apk")
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("assets/other.txt", "x")
        with self.assertRaises(mod.DriverError):
            mod.check_launcher_config(plain, manifest)


class FreshFlowTests(unittest.TestCase):
    def test_stub_flow_writes_result_with_fresh_inputs(self):
        tmp, plan_path, plan = _plan_file(["files", "assistant"])
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        job = tempfile.mkdtemp(prefix="appbuild-job-")
        self.addCleanup(shutil.rmtree, job, ignore_errors=True)
        with open(plan_path, "rb") as handle:
            import hashlib
            plan_sha = hashlib.sha256(handle.read()).hexdigest()
        build_id = build_id_for_plan(plan, plan_sha)
        seen = {}

        def fake_apks(ctx):
            seen["apk"] = dict(ctx)
            os.makedirs(ctx["out_dir"], exist_ok=True)
            _fake_apk(os.path.join(ctx["out_dir"],
                                   "stargate-launcher.apk"),
                      {"build_id": build_id,
                       "services": ["files", "assistant"],
                       "labels": [AFTER_BOOT_LABEL]})
            open(os.path.join(ctx["out_dir"], "stargate-files.apk"),
                 "wb").close()

        def fake_image(ctx):
            seen["image"] = dict(ctx)
            manifest = _manifest(build_id, ["files", "assistant"],
                                 [AFTER_BOOT_LABEL])
            with open(os.path.join(ctx["job_dir"],
                                   "build-manifest.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(manifest, handle)

        result = mod.run_fresh_build(
            plan_path, job, tempfile.gettempdir(), build_id,
            "/ro/assistant.apk", "/ro/models", "/repo",
            apk_builder=fake_apks, image_builder=fake_image)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["build_id"], build_id)
        # Fresh APK paths under the job dir, never the frozen ones.
        for key in ("launcher_apk", "files_apk"):
            self.assertTrue(
                seen["image"]["inputs"][key].startswith(job + os.sep))
            self.assertNotIn("m4a-tools",
                             seen["image"]["inputs"][key])
        self.assertEqual(seen["image"]["inputs"]["assistant_apk"],
                         "/ro/assistant.apk")
        self.assertEqual(seen["image"]["inputs"]["model_dir"],
                         "/ro/models")
        self.assertEqual(seen["apk"]["extra_labels"], [AFTER_BOOT_LABEL])
        summary = result["summary"]
        self.assertEqual(summary["image_bytes"],
                         summary["partition_limit_bytes"])
        self.assertTrue(summary["gz"]["path"].endswith(".img.gz"))

    def test_main_writes_result_on_failure(self):
        job = tempfile.mkdtemp(prefix="appbuild-job-")
        self.addCleanup(shutil.rmtree, job, ignore_errors=True)
        code = mod.main(["--plan", os.path.join(job, "missing.json"),
                         "--job-dir", job, "--tools-dir", job,
                         "--expect-build-id", "x",
                         "--assistant-apk", "a", "--model-dir", "m",
                         "--repo", "r"])
        self.assertEqual(code, 8)
        with open(os.path.join(job, "result.json"),
                  encoding="utf-8") as handle:
            result = json.load(handle)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["reason"])

    def test_default_command_goes_through_helper(self):
        from stargate import appserver as server_module
        app = server_module.App(port=0, out_root="/tmp/o",
                                tools_dir="/tmp/t",
                                helper_path="/tmp/h/ahead-heavy.sh")
        command = server_module.default_build_command(app, "/tmp/o/b",
                                                      "b")
        self.assertEqual(command[0], "/tmp/h/ahead-heavy.sh")
        self.assertIn("stargate.appbuild", command)
        self.assertIn("/tmp/o/b/plan.json", command)
        self.assertNotIn("/workspaces/m4a-tools/out/stargate-launcher.apk",
                         command)
        self.assertNotIn("/workspaces/m4a-tools/out/stargate-files.apk",
                         command)


if __name__ == "__main__":
    unittest.main()
