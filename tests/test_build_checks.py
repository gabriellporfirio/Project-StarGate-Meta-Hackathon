"""Offline tests for the no-image section 5 checks (M4 Part 1).

Fixture APKs are real APKs: aapt2 (read-only, from /workspaces/m4-tools)
links a minimal manifest, so the package names the checks read come
from inside the APK, and assets are appended for the hidden-model tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from stargate import plan
from stargate.build import checks

ANDROID_JAR = ("/workspaces/m4-tools/android-sdk/platforms/android-34/"
               "android.jar")

MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="%(package)s"
    android:versionCode="1"
    android:versionName="1.0">
  %(permission)s
  <uses-sdk android:minSdkVersion="28" android:targetSdkVersion="34" />
  <application android:label="Test">
  </application>
</manifest>
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_apk(package: str, out_path: str, permission=None,
             assets=None, libs=None):
    """Link a minimal unsigned APK with aapt2.

    assets maps a name to bytes, appended under assets/. libs maps
    (abi, leaf) to bytes, appended uncompressed under lib/<abi>/
    like real native libraries.
    """
    assert os.path.isfile(checks.AAPT2), \
        "missing read-only aapt2 at %s" % checks.AAPT2
    assert os.path.isfile(ANDROID_JAR), \
        "missing read-only android.jar at %s" % ANDROID_JAR
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = os.path.join(tmp, "AndroidManifest.xml")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            handle.write(MANIFEST_XML % {
                "package": package,
                "permission": ('<uses-permission android:name="%s" />'
                               % permission) if permission else "",
            })
        proc = subprocess.run(
            [checks.AAPT2, "link", "-o", out_path, "-I", ANDROID_JAR,
             "--manifest", manifest_path,
             "--min-sdk-version", "28", "--target-sdk-version", "34"],
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
    if assets or libs:
        with zipfile.ZipFile(out_path, "a") as archive:
            for name, data in (assets or {}).items():
                archive.writestr("assets/" + name, data)
            for (abi, leaf), data in (libs or {}).items():
                archive.writestr("lib/%s/%s" % (abi, leaf), data,
                                 compress_type=zipfile.ZIP_STORED)
    return out_path


def _apks(packages, assets_by_package=None):
    tmp = tempfile.TemporaryDirectory()
    paths = []
    for package in packages:
        out = os.path.join(tmp.name, package + ".apk")
        assets = (assets_by_package or {}).get(package)
        make_apk(package, out, assets=assets)
        paths.append(out)
    return tmp, paths


class ApkReading(unittest.TestCase):
    def test_package_read_from_inside_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "misleading-name.apk")
            make_apk("com.stargate.files", out)
            package, _version = checks.apk_package(out)
        self.assertEqual(package, "com.stargate.files")

    def test_permissions_read_from_inside_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = os.path.join(tmp, "plain.apk")
            make_apk("com.stargate.launcher", plain)
            self.assertEqual(checks.apk_permissions(plain), [])
            net = os.path.join(tmp, "net.apk")
            make_apk("com.stargate.files", net,
                     permission="android.permission.INTERNET")
            self.assertEqual(checks.apk_permissions(net),
                             ["android.permission.INTERNET"])

    def test_assets_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.apk")
            make_apk("com.stargate.files", out,
                     assets={"model.pte": b"12", "note.txt": b"hi"})
            self.assertEqual(checks.apk_assets(out),
                             ["model.pte", "note.txt"])


TEST_ASSISTANT_PACKAGE = "com.stargate.assistant"


class AssistantPackageFromManifest(unittest.TestCase):
    def test_reads_committed_handover(self):
        path = Path(__file__).resolve().parent.parent \
            / "assistant-manifest.json"
        with open(path, encoding="utf-8") as handle:
            manifest_doc = json.load(handle)
        self.assertEqual(
            checks.assistant_package_from_manifest(manifest_doc),
            "com.stargate.assistant")

    def test_reads_package_not_constant(self):
        doc = {"assistant_apk": {
            "package": "com.example.test.assistant",
            "version": "9.9", "path": "a.apk",
            "sha256": "0" * 64}}
        self.assertEqual(
            checks.assistant_package_from_manifest(doc),
            "com.example.test.assistant")

    def test_missing_package_rejected(self):
        bad_docs = [{}, {"assistant_apk": {}},
                    {"assistant_apk": {"package": ""}},
                    {"assistant_apk": {"package": None}},
                    {"assistant_apk": []}]
        for bad in bad_docs:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    checks.assistant_package_from_manifest(bad)

    def test_distinct_package_passes_end_to_end(self):
        package = checks.assistant_package_from_manifest(
            {"assistant_apk": {
                "package": "com.example.test.assistant"}})
        tmp, paths = _apks(["com.stargate.launcher",
                            "com.example.test.assistant"])
        try:
            self.assertEqual(
                checks.check_apps_match_selection(
                    paths, ["assistant"],
                    assistant_package=package), [])
            wrong = checks.check_apps_match_selection(
                paths, ["assistant"],
                assistant_package="com.stargate.assistant")
            self.assertTrue(any("missing" in item for item in wrong))
            self.assertTrue(any("unselected" in item for item in wrong))
        finally:
            tmp.cleanup()


class SelectionChecks(unittest.TestCase):
    def test_expected_packages_per_selection(self):
        self.assertEqual(checks.expected_packages_for_selection(
            ["files"]),
            ["com.stargate.launcher", "com.stargate.files"])
        assistant_only = checks.expected_packages_for_selection(
            ["assistant"], "com.example.test.assistant")
        self.assertEqual(assistant_only,
                         ["com.stargate.launcher",
                          "com.example.test.assistant"])
        self.assertNotIn("com.stargate.files", assistant_only)
        self.assertEqual(checks.expected_packages_for_selection(
            ["files", "assistant"], "com.example.test.assistant"),
            ["com.stargate.launcher", "com.stargate.files",
             "com.example.test.assistant"])

    def test_assistant_selection_needs_its_package(self):
        with self.assertRaises(ValueError):
            checks.expected_packages_for_selection(["assistant"])
        with self.assertRaises(ValueError):
            checks.expected_packages_for_selection(
                ["files", "assistant"])

    def test_files_only_passes(self):
        tmp, paths = _apks(["com.stargate.launcher",
                            "com.stargate.files"])
        try:
            self.assertEqual(
                checks.check_apps_match_selection(paths, ["files"]), [])
        finally:
            tmp.cleanup()

    def test_assistant_only_has_no_files(self):
        tmp, paths = _apks(["com.stargate.launcher",
                            "com.stargate.assistant"])
        try:
            self.assertEqual(
                checks.check_apps_match_selection(
                    paths, ["assistant"],
                    assistant_package=TEST_ASSISTANT_PACKAGE), [])
            errors = checks.check_apps_match_selection(
                paths, ["files", "assistant"],
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertTrue(any("com.stargate.files" in item
                                for item in errors))
        finally:
            tmp.cleanup()

    def test_unselected_app_fails(self):
        tmp, paths = _apks(["com.stargate.launcher",
                            "com.stargate.files",
                            "com.stargate.assistant"])
        try:
            errors = checks.check_apps_match_selection(paths, ["files"])
            self.assertTrue(any("unselected" in item and
                                "com.stargate.assistant" in item
                                for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["files"],
                os.path.join(tmp.name, "absent"), None)
            self.assertEqual(code, 8)
        finally:
            tmp.cleanup()

    def test_missing_app_fails(self):
        tmp, paths = _apks(["com.stargate.launcher",
                            "com.stargate.files"])
        try:
            errors = checks.check_apps_match_selection(
                paths, ["files", "assistant"],
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertTrue(any("missing" in item and
                                "com.stargate.assistant" in item
                                for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["files", "assistant"],
                os.path.join(tmp.name, "absent"), None,
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertEqual(code, 8)
        finally:
            tmp.cleanup()


class ModelFileChecks(unittest.TestCase):
    def _model_dir(self, files):
        tmp = tempfile.TemporaryDirectory()
        model_dir = os.path.join(tmp.name, "model")
        os.makedirs(model_dir)
        for name, data in files.items():
            with open(os.path.join(model_dir, name), "wb") as handle:
                handle.write(data)
        return tmp, model_dir

    def test_clean_files_only_passes(self):
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.files"])
        model_tmp, model_dir = self._model_dir({})
        try:
            self.assertEqual(
                checks.check_model_files(model_dir, paths, None), [])
            self.assertEqual(
                checks.check_model_files(
                    os.path.join(model_tmp.name, "absent"), paths,
                    None), [])
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_model_file_in_folder_fails_without_assistant(self):
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.files"])
        model_tmp, model_dir = self._model_dir({"tiny.pte": b"1234"})
        try:
            errors = checks.check_model_files(model_dir, paths, None)
            self.assertTrue(any("tiny.pte" in item for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["files"], model_dir, None)
            self.assertEqual(code, 8)
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_model_file_hidden_in_assets_fails(self):
        apk_tmp, paths = _apks(
            ["com.stargate.launcher", "com.stargate.files"],
            {"com.stargate.files": {"sneaky.gguf": b"1234"}})
        model_tmp, model_dir = self._model_dir({})
        try:
            errors = checks.check_model_files(model_dir, paths, None)
            self.assertTrue(any("sneaky.gguf" in item for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["files"], model_dir, None)
            self.assertEqual(code, 8)
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_matching_model_files_pass(self):
        data = b"model-bytes-12"
        expected = [{"name": "model.pte", "bytes": len(data),
                     "sha256": _sha(data)}]
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.files",
                                "com.stargate.assistant"])
        model_tmp, model_dir = self._model_dir({"model.pte": data})
        try:
            self.assertEqual(
                checks.check_model_files(model_dir, paths, expected),
                [])
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_same_size_different_content_fails(self):
        expected = [{"name": "model.pte", "bytes": 4,
                     "sha256": _sha(b"good")}]
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.assistant"])
        model_tmp, model_dir = self._model_dir({"model.pte": b"evil"})
        try:
            errors = checks.check_model_files(model_dir, paths,
                                              expected)
            self.assertTrue(any("model.pte" in item for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["assistant"], model_dir, expected,
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertEqual(code, 8)
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_missing_and_extra_model_files_fail(self):
        expected = [{"name": "model.pte", "bytes": 4,
                     "sha256": _sha(b"good")}]
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.assistant"])
        model_tmp, model_dir = self._model_dir(
            {"model.pte": b"good", "extra.pte": b"xx"})
        try:
            errors = checks.check_model_files(model_dir, paths,
                                              expected)
            self.assertTrue(any("extra.pte" in item for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["assistant"], model_dir, expected,
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertEqual(code, 8)
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()
        model_tmp, model_dir = self._model_dir({})
        try:
            errors = checks.check_model_files(model_dir, paths,
                                              expected)
            self.assertTrue(any("missing" in item and "model.pte" in item
                                for item in errors))
            code, _ = checks.run_contents_checks(
                paths, ["assistant"], model_dir, expected,
                assistant_package=TEST_ASSISTANT_PACKAGE)
            self.assertEqual(code, 8)
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()

    def test_contents_checks_exit_8(self):
        apk_tmp, paths = _apks(["com.stargate.launcher",
                                "com.stargate.files",
                                "com.stargate.assistant"])
        model_tmp, model_dir = self._model_dir({})
        try:
            code, errors = checks.run_contents_checks(
                paths, ["files"], model_dir, None)
            self.assertEqual(code, 8)
            self.assertTrue(errors)
            code, errors = checks.run_contents_checks(
                paths[:2], ["files"], model_dir, None)
            self.assertEqual(code, 0)
            self.assertEqual(errors, [])
        finally:
            apk_tmp.cleanup()
            model_tmp.cleanup()


class LauncherConfig(unittest.TestCase):
    def test_services_come_from_handoff_only(self):
        code, stdout, _err = plan.run_plan(
            sim="flagship", services_text="files,assistant",
            research_path=str(
                Path(__file__).resolve().parent.parent / "fixtures"
                / "research" / "flagship.json"),
            json_only=True)
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        report["fit"]["services"] = ["files"]
        self.assertEqual(checks.launcher_services(report),
                         ["files", "assistant"])
        config = checks.launcher_config(report, "build-7", ["label"])
        self.assertEqual(config, {"services": ["files", "assistant"],
                                 "build_id": "build-7",
                                 "labels": ["label"]})


if __name__ == "__main__":
    unittest.main()
