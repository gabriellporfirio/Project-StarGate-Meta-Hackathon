"""Offline tests for build-v1 (M4 section 5): schema, writer, validator."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from stargate.build import manifest

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "schemas" / "build-examples"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files_only(**overrides):
    doc = manifest.make_manifest(
        plan_sha256=_sha(b"plan"),
        selection=["files"],
        selection_result="fits",
        route="not_yet",
        build_id="test-build-1",
        labels=[],
        artifacts=[{"path": "launcher.apk", "bytes": 4,
                    "sha256": _sha(b"apk1")},
                   {"path": "files.apk", "bytes": 4,
                    "sha256": _sha(b"apk2")}],
        apps=[{"service": "launcher",
               "package": "com.stargate.launcher", "version": "1.0",
               "path": "launcher.apk", "sha256": _sha(b"apk1")},
              {"service": "files", "package": "com.stargate.files",
               "version": "1.0", "path": "files.apk",
               "sha256": _sha(b"apk2")}],
        model_files=None,
        runtime_version=None,
        assistant_manifest_sha256=None,
        unresolved=[{"area": "image_size", "detail": "not checked"}])
    doc.update(overrides)
    return doc


def _with_assistant(**overrides):
    doc = _files_only(
        selection=["files", "assistant"],
        route="B",
        apps=[{"service": "launcher",
               "package": "com.stargate.launcher", "version": "1.0",
               "path": "launcher.apk", "sha256": _sha(b"apk1")},
              {"service": "files", "package": "com.stargate.files",
               "version": "1.0", "path": "files.apk",
               "sha256": _sha(b"apk2")},
              {"service": "assistant",
               "package": "com.stargate.assistant", "version": "1.0",
               "path": "assistant.apk", "sha256": _sha(b"apk3")}],
        model_files=[{"name": "model.pte", "bytes": 12,
                      "sha256": _sha(b"model-bytes"),
                      "target": "/sdcard/Android/data/"
                              "com.stargate.assistant/files/model/"
                              "model.pte"}],
        model_delivery="after_boot",
        runtime_version="executorch 1.5.1",
        assistant_manifest_sha256=_sha(b"assistant-manifest"))
    doc.update(overrides)
    return doc


class BuildExamples(unittest.TestCase):
    def test_every_example_matches_its_label(self):
        files = sorted(EXAMPLES.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(example=path.name):
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                errors = manifest.validate_manifest(payload)
                if path.name.startswith("valid_"):
                    self.assertEqual(errors, [])
                elif path.name.startswith("invalid_"):
                    self.assertTrue(errors)
                else:
                    self.fail("example name must start valid_/invalid_")


class BuildValidation(unittest.TestCase):
    def test_files_only_validates(self):
        self.assertEqual(manifest.validate_manifest(_files_only()), [])

    def test_files_assistant_validates(self):
        self.assertEqual(
            manifest.validate_manifest(_with_assistant()), [])

    def test_routes_a_b_and_not_yet(self):
        for route in ("A", "B", "not_yet"):
            with self.subTest(route=route):
                self.assertEqual(
                    manifest.validate_manifest(
                        _files_only(route=route)), [])

    def test_bad_route_rejected(self):
        errors = manifest.validate_manifest(_files_only(route="C"))
        self.assertTrue(any("route" in item for item in errors))

    def test_missing_launcher_rejected(self):
        doc = _files_only()
        doc["apps"] = [app for app in doc["apps"]
                       if app["service"] != "launcher"]
        errors = manifest.validate_manifest(doc)
        self.assertTrue(any("launcher" in item for item in errors))

    def test_missing_selected_app_rejected(self):
        doc = _with_assistant()
        doc["apps"] = [app for app in doc["apps"]
                       if app["service"] != "assistant"]
        errors = manifest.validate_manifest(doc)
        self.assertTrue(any("assistant" in item for item in errors))

    def test_model_files_required_with_assistant(self):
        errors = manifest.validate_manifest(
            _with_assistant(model_files=None))
        self.assertTrue(any("model_files" in item for item in errors))

    def test_model_files_forbidden_without_assistant(self):
        doc = _files_only(model_files=[{"name": "m.pte", "bytes": 1,
                                        "sha256": _sha(b"x"),
                                        "target": "/t"}])
        errors = manifest.validate_manifest(doc)
        self.assertTrue(any("model_files" in item for item in errors))

    def test_manifest_sha_required_with_assistant(self):
        errors = manifest.validate_manifest(
            _with_assistant(assistant_manifest_sha256=None))
        self.assertTrue(any("assistant_manifest_sha256" in item
                            for item in errors))

    def test_delivery_none_rejected_with_assistant(self):
        errors = manifest.validate_manifest(
            _with_assistant(model_delivery="none"))
        self.assertTrue(any("model_delivery" in item for item in errors))

    def test_delivery_after_boot_rejected_without_assistant(self):
        errors = manifest.validate_manifest(
            _files_only(model_delivery="after_boot"))
        self.assertTrue(any("model_delivery" in item for item in errors))

    def test_delivery_after_boot_rejects_product_target(self):
        doc = _with_assistant()
        doc["model_files"][0]["target"] = \
            "/product/stargate/model/model.pte"
        errors = manifest.validate_manifest(doc)
        self.assertTrue(any("/sdcard/" in item for item in errors))

    def test_delivery_in_image_rejects_sdcard_target(self):
        doc = _with_assistant(model_delivery="in_image")
        errors = manifest.validate_manifest(doc)
        self.assertTrue(any("/product/" in item for item in errors))

    def test_delivery_in_image_accepts_product_target(self):
        doc = _with_assistant(model_delivery="in_image")
        doc["model_files"][0]["target"] = \
            "/product/stargate/model/model.pte"
        self.assertEqual(manifest.validate_manifest(doc), [])

    def test_payload_path_only_with_after_boot(self):
        errors = manifest.validate_manifest(
            _files_only(model_payload_path="model-payload"))
        self.assertTrue(any("model_payload_path" in item
                            for item in errors))
        doc = _with_assistant(model_delivery="in_image",
                              model_payload_path="model-payload")
        doc["model_files"][0]["target"] = \
            "/product/stargate/model/model.pte"
        errors = manifest.validate_manifest(doc)
        self.assertEqual(len(errors), 1)
        self.assertIn("model_payload_path", errors[0])

    def test_payload_path_must_be_relative(self):
        for bad in ("/abs/model-payload", "a/../model-payload", ""):
            with self.subTest(bad=bad):
                errors = manifest.validate_manifest(
                    _with_assistant(model_payload_path=bad))
                self.assertTrue(any("model_payload_path" in item
                                    for item in errors))

    def test_payload_path_accepted_with_after_boot(self):
        doc = _with_assistant(model_payload_path="model-payload")
        self.assertEqual(manifest.validate_manifest(doc), [])

    def test_labels_must_be_strings(self):
        errors = manifest.validate_manifest(_files_only(labels=[None]))
        self.assertTrue(any("labels" in item for item in errors))

    def test_unconfirmed_label_accepted(self):
        from stargate.build import gate

        doc = _files_only(selection_result="unconfirmed",
                          labels=[gate.UNCONFIRMED_LABEL])
        self.assertEqual(manifest.validate_manifest(doc), [])

    def test_unknown_keys_rejected(self):
        doc = _files_only()
        doc["extra"] = 1
        self.assertTrue(manifest.validate_manifest(doc))

    def test_image_size_keys_optional(self):
        doc = _files_only()
        del doc["image_bytes"]
        del doc["partition_limit_bytes"]
        self.assertEqual(manifest.validate_manifest(doc), [])

    def test_image_size_keys_accepted(self):
        doc = _files_only(image_bytes=1622147072,
                          partition_limit_bytes=1622147072)
        self.assertEqual(manifest.validate_manifest(doc), [])

    def test_image_size_keys_reject_non_whole(self):
        for bad in (-1, True, "1622147072", 1.5):
            with self.subTest(bad=bad):
                doc = _files_only(image_bytes=bad)
                self.assertTrue(
                    any("image_bytes" in item
                        for item in manifest.validate_manifest(doc)))
                doc = _files_only(partition_limit_bytes=bad)
                self.assertTrue(
                    any("partition_limit_bytes" in item
                        for item in manifest.validate_manifest(doc)))


class BuildWriter(unittest.TestCase):
    def test_write_then_validate_file(self):
        doc = _with_assistant()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "nested", "out")
            path = manifest.write_manifest(doc, out)
            self.assertEqual(path, os.path.join(
                out, manifest.MANIFEST_NAME))
            ok, errors = manifest.validate_file(path)
            self.assertTrue(ok, errors)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), doc)

    def test_invalid_manifest_writes_nothing(self):
        doc = _files_only(route="C")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            with self.assertRaises(ValueError):
                manifest.write_manifest(doc, out)
            self.assertFalse(os.path.exists(
                os.path.join(out, manifest.MANIFEST_NAME)))

    def test_not_yet_route_refused_by_writer(self):
        doc = _files_only()
        self.assertEqual(doc["route"], "not_yet")
        self.assertEqual(manifest.validate_manifest(doc), [])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            with self.assertRaises(ValueError) as raised:
                manifest.write_manifest(doc, out)
            self.assertIn("not_yet", str(raised.exception))
            self.assertFalse(os.path.exists(
                os.path.join(out, manifest.MANIFEST_NAME)))

    def test_hashes_match_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = os.path.join(tmp, "launcher.apk")
            files_apk = os.path.join(tmp, "files.apk")
            Path(launcher).write_bytes(b"fake-apk-bytes")
            Path(files_apk).write_bytes(b"fake-files-apk")
            digest = manifest.sha256_file(launcher)
            self.assertEqual(digest, _sha(b"fake-apk-bytes"))
            files_digest = manifest.sha256_file(files_apk)
            doc = _files_only(
                artifacts=[{"path": "launcher.apk", "bytes": 14,
                            "sha256": digest},
                           {"path": "files.apk", "bytes": 14,
                            "sha256": files_digest}],
                apps=[{"service": "launcher",
                       "package": "com.stargate.launcher",
                       "version": "1.0", "path": "launcher.apk",
                       "sha256": digest},
                      {"service": "files",
                       "package": "com.stargate.files",
                       "version": "1.0", "path": "files.apk",
                       "sha256": files_digest}])
            self.assertEqual(manifest.validate_manifest(doc), [])
            self.assertEqual(
                manifest.verify_artifact_hashes(doc, tmp), [])
            Path(launcher).write_bytes(b"tampered-bytes!")
            errors = manifest.verify_artifact_hashes(doc, tmp)
            self.assertTrue(any("launcher.apk" in item for item in errors))

    def test_missing_artifact_fails_verification(self):
        doc = _files_only()
        with tempfile.TemporaryDirectory() as tmp:
            errors = manifest.verify_artifact_hashes(doc, tmp)
            self.assertTrue(errors)

    def test_payload_hashes_match_the_snapshot(self):
        payload = b"payload-bytes-1"
        doc = _with_assistant(model_payload_path="model-payload")
        doc["model_files"] = [{"name": "tiny.pte",
                               "bytes": len(payload),
                               "sha256": _sha(payload),
                               "target": "/sdcard/m/tiny.pte"}]
        doc["artifacts"] = []
        doc["apps"] = []
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "model-payload"))
            with open(os.path.join(tmp, "model-payload",
                                   "tiny.pte"), "wb") as handle:
                handle.write(payload)
            self.assertEqual(
                manifest.verify_artifact_hashes(doc, tmp), [])
            with open(os.path.join(tmp, "model-payload",
                                   "tiny.pte"), "wb") as handle:
                handle.write(b"X" + payload[1:])
            errors = manifest.verify_artifact_hashes(doc, tmp)
            self.assertTrue(any("tiny.pte" in item for item in errors))

    def test_missing_payload_file_fails_verification(self):
        doc = _with_assistant(model_payload_path="model-payload")
        doc["artifacts"] = []
        doc["apps"] = []
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "model-payload"))
            errors = manifest.verify_artifact_hashes(doc, tmp)
            self.assertTrue(any("model.pte" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
