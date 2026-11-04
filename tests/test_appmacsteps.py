"""Boot steps generation (M6 Part 1, Boot step). Light only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from stargate import macsteps as mod

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _manifest(tmp, selection=("files", "assistant")):
    key = "+".join(sorted(selection))
    image = {"assistant+files": "system-stargate-files-assistant.img",
             "files": "system-stargate-files.img",
             "assistant": "system-stargate-assistant.img"}[key]
    manifest = {
        "build_id": "m4a-test-1234abcd",
        "selection": list(selection),
        "artifacts": [
            {"path": image, "bytes": 1622147072, "sha256": "a" * 64},
            {"path": image + ".gz", "bytes": 654703016,
             "sha256": "b" * 64}],
        "apps": [{"package": "com.stargate.assistant", "bytes": 1,
                  "sha256": "e" * 64}],
        "model_files": [{"name": "model.pte", "bytes": 1135951488,
                         "sha256": "f" * 64}],
        "model_delivery": "after_boot",
        "model_payload_path": "model-payload",
        "image_bytes": 1622147072,
        "partition_limit_bytes": 1622147072,
    }
    summary = {
        "build_id": manifest["build_id"],
        "selection": list(selection),
        "image": dict(manifest["artifacts"][0]),
        "image_bytes": 1622147072,
        "partition_limit_bytes": 1622147072,
        "gz": dict(manifest["artifacts"][1]),
        "model_delivery": "after_boot",
        "model_payload_path": "model-payload",
        "payload_files": list(manifest["model_files"]),
        "apps": list(manifest["apps"]),
    }
    return manifest, summary


class GuardTests(unittest.TestCase):
    def test_guard_byte_identical_to_m4_record(self):
        with open(os.path.join(REPO, "docs", "m4-build.md"),
                  encoding="utf-8") as handle:
            docs = handle.read()
        copies = [line for line in docs.splitlines()
                  if line.startswith("sg_guard() {")]
        self.assertEqual(len(copies), 2)
        for copy in copies:
            self.assertEqual(copy, mod.GUARD)

    def test_avd_name_deterministic(self):
        self.assertEqual(
            mod.avd_name_for_selection(["assistant", "files"]),
            "stargate-m6-files-assistant")
        self.assertEqual(mod.avd_name_for_selection(["files"]),
                         "stargate-m6-files")


class GenerateTests(unittest.TestCase):
    def test_files_assistant_steps(self):
        manifest, summary = _manifest(None)
        text = mod.generate(manifest, summary)
        self.assertIn("Target: Android Emulator (this demo)", text)
        self.assertIn("m4a-test-1234abcd", text)
        self.assertIn("stargate-m6-files-assistant", text)
        self.assertIn("system-stargate-files-assistant.img", text)
        self.assertIn("a" * 64, text)
        self.assertIn("b" * 64, text)
        self.assertIn("model.pte", text)
        self.assertIn("f" * 64, text)
        self.assertIn("hw.ramSize=6144", text)
        self.assertIn("com.stargate.assistant", text)
        self.assertIn("com.stargate.files", text)

    def test_files_only_skips_model_steps(self):
        manifest, summary = _manifest(None, selection=("files",))
        text = mod.generate(manifest, summary)
        self.assertIn("stargate-m6-files", text)
        self.assertIn("system-stargate-files.img", text)
        self.assertNotIn("model.pte", text)
        self.assertNotIn("hw.ramSize", text)
        self.assertNotIn("com.stargate.assistant", text)
        self.assertIn("com.stargate.files", text)

    def test_bad_manifest_refused(self):
        manifest, summary = _manifest(None)
        with self.assertRaises(ValueError):
            mod.generate({}, summary)
        with self.assertRaises(ValueError):
            mod.generate(dict(manifest, artifacts=[]), summary)

    def test_shell_blocks_parse(self):
        manifest, summary = _manifest(None)
        text = mod.generate(manifest, summary)
        blocks = re.findall(r"```sh\n(.*?)```", text, re.DOTALL)
        self.assertTrue(blocks)
        tmp = tempfile.mkdtemp(prefix="macsteps-sh-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for index, block in enumerate(blocks):
            path = os.path.join(tmp, "block-%d.sh" % index)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(block)
            proc = subprocess.run(
                ["sh", "-n", path], capture_output=True, text=True,
                timeout=30)
            self.assertEqual(proc.returncode, 0,
                             "block %d: %s" % (index, proc.stderr))

    def test_prose_has_no_brand_words(self):
        manifest, summary = _manifest(None)
        text = mod.generate(manifest, summary)
        scrubbed = re.sub(r"\S*stargate\S*", "", text,
                           flags=re.IGNORECASE)
        self.assertIsNone(
            re.search(r"\b(stargate|spare)\b", scrubbed,
                      re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
