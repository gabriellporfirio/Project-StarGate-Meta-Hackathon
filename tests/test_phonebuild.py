"""The phone package: hub app config, takeover steps, build driver.

The driver tests use a stub app builder (no Android tools needed); one
extra test builds the real app when the tools are on this computer,
and one runs the phone server's own host test when a JDK is here.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from stargate import appserver
from stargate import phonebuild
from stargate import takeover
from stargate.build import hubapk
from stargate.build.image import build_id_for_plan

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stub_apk(config, out_dir):
    path = os.path.join(out_dir, hubapk.APK_NAME)
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr("assets/" + hubapk.CONFIG_NAME, json.dumps(config))
    return {"path": path, "sha256": hubapk.sha256_file(path),
            "bytes": os.path.getsize(path), "package": hubapk.PACKAGE,
            "services": config["services"]}


def write_plan(folder, fixture, services):
    code, plan, error = appserver.real_plan(fixture, services)
    assert plan is not None, error
    raw = (json.dumps(plan, sort_keys=True, indent=2) + "\n").encode()
    path = os.path.join(folder, "plan.json")
    with open(path, "wb") as handle:
        handle.write(raw)
    return path, build_id_for_plan(plan, hashlib.sha256(raw).hexdigest())


class HubConfig(unittest.TestCase):
    def test_only_the_chosen_services_in_fixed_order(self):
        config = hubapk.hub_config(["vault", "files"], "B-1")
        self.assertEqual(config["services"], ["files", "vault"])
        self.assertNotIn("local_model", config)

    def test_unknown_service_refused(self):
        with self.assertRaises(hubapk.BuildError):
            hubapk.hub_config(["files", "vpn"], "B-1")

    def test_model_needs_the_assistant_and_its_hashes(self):
        model = {"name": "m", "pte": "m.pte", "tokenizer": "t.json",
                 "pte_sha256": "a" * 64, "tokenizer_sha256": "b" * 64}
        with self.assertRaises(hubapk.BuildError):
            hubapk.hub_config(["files"], "B-1", local_model=model)
        with self.assertRaises(hubapk.BuildError):
            hubapk.hub_config(["assistant"], "B-1",
                              local_model={"name": "m"})
        config = hubapk.hub_config(["assistant"], "B-1", local_model=model)
        self.assertEqual(config["local_model"]["pte"], "m.pte")


class Takeover(unittest.TestCase):
    def test_steps_install_then_home_then_link(self):
        steps = takeover.install_steps("app.apk")
        verbs = [step["argv"][0] for step in steps]
        self.assertEqual(verbs, ["install", "shell", "shell", "forward"])
        text = takeover.steps_text(steps)
        self.assertIn("adb -s SERIAL install -r app.apk", text)
        self.assertIn("set-home-activity", text)

    def test_nothing_is_erased_or_unlocked(self):
        words = json.dumps(takeover.install_steps("a.apk", ["m.pte"])
                           + takeover.undo_steps())
        for banned in ("fastboot", "wipe", "format", "flash", "oem",
                       "rm -", "pm clear", "factory"):
            self.assertNotIn(banned, words)

    def test_run_needs_a_serial_and_stops_at_first_failure(self):
        with self.assertRaises(takeover.TakeoverError):
            takeover.run("", takeover.install_steps("a.apk"))
        with self.assertRaises(takeover.TakeoverError):
            takeover.run("bad serial;rm", takeover.install_steps("a.apk"))
        seen = []

        def runner(argv):
            seen.append(argv)
            return (1, "no") if "set-home-activity" in argv else (0, "")
        with self.assertRaises(takeover.TakeoverError):
            takeover.run("ABC123", takeover.install_steps("a.apk"),
                         runner=runner)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][:3], ["adb", "-s", "ABC123"])

    def test_undo_removes_only_our_app(self):
        steps = takeover.undo_steps()
        self.assertEqual(steps[-1]["argv"], ["uninstall", takeover.PACKAGE])


class Driver(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="phonebuild-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_package_for_three_services(self):
        plan, build_id = write_plan(self.dir, "flagship",
                                    ["files", "assistant", "vault"])
        result = phonebuild.run_build(
            plan, self.dir, build_id, apk_builder=stub_apk,
            model_bundle=os.path.join(self.dir, "none"))
        self.assertEqual(result["status"], "ok")
        summary = result["summary"]
        self.assertEqual(summary["kind"], "phone_package")
        self.assertEqual(summary["selection"],
                         ["files", "assistant", "vault"])
        self.assertEqual(summary["route"], "image")
        self.assertIn("CLEAN SYSTEM", summary["steps"])
        self.assertIn("UNDO", summary["steps"])
        # No phone-ready model here: said plainly, not hidden.
        self.assertEqual(summary["assistant_mode"], "cloud_only")
        self.assertTrue(summary["notes"])
        for name in ("INSTALL.txt", "build-manifest.json",
                     hubapk.APK_NAME):
            self.assertTrue(os.path.isfile(os.path.join(self.dir, name)))

    def test_small_phone_is_cloud_only_and_takeover(self):
        plan, build_id = write_plan(self.dir, "old32",
                                    ["files", "assistant"])
        result = phonebuild.run_build(plan, self.dir, build_id,
                                      apk_builder=stub_apk)
        self.assertEqual(result["summary"]["route"], "takeover")
        self.assertEqual(result["summary"]["assistant_mode"],
                         "cloud_only")
        self.assertNotIn("CLEAN SYSTEM", result["summary"]["steps"])

    def test_wrong_build_id_and_bad_app_are_refused(self):
        plan, build_id = write_plan(self.dir, "budget", ["files"])
        with self.assertRaises(phonebuild.DriverError):
            phonebuild.run_build(plan, self.dir, "OTHER",
                                 apk_builder=stub_apk)

        def lying(config, out_dir):
            return stub_apk(dict(config, services=["files", "vault"]),
                            out_dir)
        with self.assertRaises(phonebuild.DriverError):
            phonebuild.run_build(plan, self.dir, build_id,
                                 apk_builder=lying)

    def test_main_always_writes_result(self):
        code = phonebuild.main(["--plan", os.path.join(self.dir, "no"),
                                "--job-dir", self.dir,
                                "--expect-build-id", "X"])
        self.assertEqual(code, 8)
        with open(os.path.join(self.dir, "result.json")) as handle:
            self.assertEqual(json.load(handle)["status"], "failed")

    @unittest.skipIf(hubapk.missing_tools(hubapk.find_tools()),
                     "Android build tools are not on this computer")
    def test_real_app_builds_and_matches_the_plan(self):
        plan, build_id = write_plan(self.dir, "budget",
                                    ["files", "vault"])
        result = phonebuild.run_build(plan, self.dir, build_id)
        apk = os.path.join(self.dir, hubapk.APK_NAME)
        with zipfile.ZipFile(apk) as zipped:
            names = zipped.namelist()
        self.assertIn("classes.dex", names)
        self.assertIn("assets/ui/phone.html", names)
        self.assertIn("assets/ui/portal.js", names)
        # No model runtime in a build that has no on-phone model.
        self.assertFalse([name for name in names
                          if name.startswith("lib/")])
        self.assertLess(result["summary"]["app"]["bytes"], 1024 * 1024)


@unittest.skipIf(shutil.which("javac") is None, "no JDK on this computer")
class PhoneServerHost(unittest.TestCase):
    def test_host_checks_pass(self):
        out = tempfile.mkdtemp(prefix="hub-host-")
        self.addCleanup(shutil.rmtree, out, True)
        hub = os.path.join(REPO, "android", "hub")
        sources = []
        for part in ("java", "test"):
            folder = os.path.join(hub, part, "com", "stargate", "hub")
            sources += [os.path.join(folder, name)
                        for name in sorted(os.listdir(folder))]
        env = dict(os.environ)
        env.pop("JAVA_TOOL_OPTIONS", None)
        subprocess.run(["javac", "--release", "8", "-Xlint:-options",
                        "-nowarn", "-d", out] + sources, check=True,
                       env=env, capture_output=True)
        done = subprocess.run(
            ["java", "-cp", out, "com.stargate.hub.HubTestMain",
             os.path.join(hub, "assets", "ui")],
            capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        self.assertIn("ALL OK", done.stdout)


class PhonePages(unittest.TestCase):
    """The pages on the phone carry no product name and no markup built
    from data."""

    def test_pages_are_clean(self):
        folder = os.path.join(REPO, "android", "hub", "assets", "ui")
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                text = handle.read()
            for banned in ("StarGate", "Stargate", "innerHTML",
                           "outerHTML", "insertAdjacentHTML",
                           "document.write", "eval(", "https://fonts"):
                self.assertNotIn(banned, text, "%s in %s" % (banned, name))


if __name__ == "__main__":
    unittest.main()
