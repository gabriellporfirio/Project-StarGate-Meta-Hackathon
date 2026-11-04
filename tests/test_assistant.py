"""Offline tests for what M4b owns: the Assistant APK, its hash check,
and the assistant manifest's binding to the plan handoff (spec 5a)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "assistant-manifest.json"
NOTICES = REPO / "third_party" / "NOTICES.md"
DEP_FILES = {"kotlin-stdlib": "kotlin-stdlib-1.9.23.jar",
             "fbjni": "fbjni-0.7.0.aar",
             "nativeloader": "nativeloader-0.10.5.jar"}


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# Machine roots come from one setting each, defaulting to today's
# values with no absolute path hardcoded: the m4b root is read from
# the manifest's tools_dir (relative to the repo root) unless
# $M4B_TOOLS overrides it, and the spike root defaults to the
# sibling checkout next to this repo unless $M4_TOOLS overrides it.
def _m4b_tools() -> Path:
    override = os.environ.get("M4B_TOOLS")
    if override:
        return Path(override)
    configured = Path(_load_json(MANIFEST)["tools_dir"])
    if configured.is_absolute():
        return configured
    return REPO / configured


M4B_TOOLS = _m4b_tools()
M4_TOOLS = Path(os.environ.get("M4_TOOLS", REPO.parent / "m4-tools"))
# Verified Part 3 snapshot (read-only, builder-owned storage). The
# local Assistant APK is never read from the sibling m4b-tools/out/
# folder anymore; $ASSISTANT_APK overrides for future handoffs.
SNAPSHOT_APK = Path(
    "/workspaces/m4a-tools/inputs/assistant-1936549b.apk")


def _assistant_apk() -> Path:
    override = os.environ.get("ASSISTANT_APK")
    if override:
        return Path(override)
    return SNAPSHOT_APK


APK = _assistant_apk()


def model_files_in_apk(names) -> list:
    """APK entries that would be model files (licenses.txt is allowed)."""
    bad = []
    for name in names:
        lowered = name.lower()
        if lowered.endswith((".pte", ".gguf")):
            bad.append(name)
        elif lowered.endswith("tokenizer.model"):
            bad.append(name)
        elif lowered.startswith("assets/model"):
            bad.append(name)
    return bad
PLAN = REPO / "demo" / "plans" / "flagship-files-assistant.plan.json"
JAVAC = M4_TOOLS / "jdk-17" / "bin" / "javac"
JAVA = M4_TOOLS / "jdk-17" / "bin" / "java"
VERIFIER_SRC = (REPO / "assistant-app" / "java" / "com" / "stargate"
                / "assistant" / "ManifestVerifier.java")


def check_binding(manifest: dict, handoff_model: dict) -> list:
    """Spec 5a binding failures; [] means bound."""
    problems = []
    if manifest.get("model_id") != handoff_model.get("id"):
        problems.append("model id differs")
    if manifest.get("revision", manifest.get("source_revision")) != \
            handoff_model.get("revision"):
        problems.append("revision differs")
    manifest_files = {item.get("name"): item.get("bytes")
                      for item in manifest.get("files", [])}
    handoff_files = {item.get("name"): item.get("bytes")
                     for item in handoff_model.get("files", [])}
    if manifest_files != handoff_files:
        problems.append("file set differs")
    for item in manifest.get("files", []):
        if not item.get("sha256"):
            problems.append("missing hash for %s" % item.get("name"))
    return problems


class ApkContents(unittest.TestCase):
    def test_no_network_permission_in_apk(self):
        with zipfile.ZipFile(APK) as apk:
            blob = apk.read("AndroidManifest.xml")
        # Binary XML stores strings UTF-16; strip NULs to search text.
        text = blob.replace(b"\x00", b"")
        self.assertIn(b"com.stargate.assistant", text)
        self.assertNotIn(b"uses-permission", text)
        self.assertNotIn(b"INTERNET", text)

    def test_no_model_file_in_apk(self):
        with zipfile.ZipFile(APK) as apk:
            names = apk.namelist()
        self.assertEqual(model_files_in_apk(names), [])

    def test_model_file_check_allows_licenses(self):
        self.assertEqual(
            model_files_in_apk(["assets/licenses.txt",
                                "classes.dex", "lib/arm64-v8a/x.so"]),
            [])

    def test_model_file_check_refuses_models(self):
        self.assertEqual(
            model_files_in_apk(["assets/model/evil.pte"]), ["assets/model/evil.pte"])
        self.assertEqual(
            model_files_in_apk(["m.gguf"]), ["m.gguf"])
        self.assertEqual(
            model_files_in_apk(["assets/tokenizer.model"]),
            ["assets/tokenizer.model"])

    def test_licenses_asset_matches_notices(self):
        with zipfile.ZipFile(APK) as apk:
            blob = apk.read("assets/licenses.txt")
        self.assertEqual(blob, NOTICES.read_bytes())

    def test_native_libs_for_both_abis(self):
        with zipfile.ZipFile(APK) as apk:
            names = set(apk.namelist())
        for abi in ("arm64-v8a", "x86_64"):
            for lib in ("libexecutorch.so", "libfbjni.so",
                        "libc++_shared.so"):
                self.assertIn("lib/%s/%s" % (abi, lib), names)


class HashCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not JAVAC.exists() or not JAVA.exists():
            raise AssertionError("spike JDK missing under %s" % M4_TOOLS)
        cls.workdir = tempfile.mkdtemp(prefix="m4b-verify-")
        build = subprocess.run(
            [str(JAVAC), "--release", "8", "-d", cls.workdir,
             str(VERIFIER_SRC)], capture_output=True, text=True)
        self_assert = build.returncode == 0
        if not self_assert:
            raise AssertionError("javac failed: %s" % build.stderr)

    def _run_verifier(self, path, expected):
        return subprocess.run(
            [str(JAVA), "-cp", self.workdir,
             "com.stargate.assistant.ManifestVerifier", path, expected],
            capture_output=True, text=True)

    def test_match_passes(self):
        target = os.path.join(self.workdir, "model.pte")
        with open(target, "wb") as handle:
            handle.write(b"A" * 1000)
        expected = hashlib.sha256(b"A" * 1000).hexdigest()
        done = self._run_verifier(target, expected)
        self.assertEqual(done.returncode, 0)
        self.assertIn("MATCH", done.stdout)

    def test_same_name_same_size_different_content_refused(self):
        target = os.path.join(self.workdir, "model.pte")
        with open(target, "wb") as handle:
            handle.write(b"B" * 1000)  # same size, different content
        expected = hashlib.sha256(b"A" * 1000).hexdigest()
        done = self._run_verifier(target, expected)
        self.assertEqual(done.returncode, 1)
        self.assertIn("MISMATCH", done.stdout)

    def test_missing_file_refused(self):
        done = self._run_verifier(
            os.path.join(self.workdir, "absent.pte"), "0" * 64)
        self.assertNotEqual(done.returncode, 0)


class ManifestBinding(unittest.TestCase):
    def test_manifest_binds_to_handoff(self):
        manifest = _load_json(MANIFEST)
        plan = _load_json(PLAN)
        self.assertEqual(
            check_binding(manifest, plan["handoff"]["model"]), [])

    def test_binding_catches_differences(self):
        manifest = _load_json(MANIFEST)
        plan = _load_json(PLAN)
        handoff_model = plan["handoff"]["model"]
        mutated = copy.deepcopy(manifest)
        mutated["model_id"] = "something-else"
        self.assertTrue(check_binding(mutated, handoff_model))
        mutated = copy.deepcopy(manifest)
        mutated["source_revision"] = "0" * 40
        self.assertTrue(check_binding(mutated, handoff_model))
        mutated = copy.deepcopy(manifest)
        mutated["files"][0]["name"] = "renamed.pte"
        self.assertTrue(check_binding(mutated, handoff_model))
        mutated = copy.deepcopy(manifest)
        mutated["files"][0]["bytes"] += 1
        self.assertTrue(check_binding(mutated, handoff_model))
        mutated = copy.deepcopy(manifest)
        del mutated["files"][1]["sha256"]
        self.assertTrue(check_binding(mutated, handoff_model))

    def test_apk_fields_match_built_apk(self):
        manifest = _load_json(MANIFEST)
        apk = manifest["assistant_apk"]
        self.assertEqual(apk["package"], "com.stargate.assistant")
        self.assertFalse(os.path.isabs(apk["path"]))
        self.assertFalse(os.path.isabs(manifest["tools_dir"]))
        self.assertEqual(APK, _assistant_apk())
        self.assertTrue(APK.exists(), "missing assistant APK: %s" % APK)
        digest = hashlib.sha256()
        with open(APK, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        self.assertEqual(apk["sha256"], digest.hexdigest())

    def test_dependencies_match_downloads(self):
        manifest = _load_json(MANIFEST)
        deps = manifest["dependencies"]
        self.assertEqual(
            sorted(entry["name"] for entry in deps),
            ["fbjni", "kotlin-stdlib", "nativeloader"])
        for entry in deps:
            for key in ("name", "version", "bytes", "sha256"):
                self.assertIn(key, entry)
            path = M4B_TOOLS / DEP_FILES[entry["name"]]
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            self.assertEqual(path.stat().st_size, entry["bytes"])
            self.assertEqual(digest.hexdigest(), entry["sha256"])


APP_JAVA = (REPO / "assistant-app" / "java" / "com" / "stargate"
            / "assistant")
FIXUP3_SRCS = ["ChatFormat.java", "ModelFiles.java", "StatsLine.java",
               "AnswerAccumulator.java"]
FIXUP4_SRCS = ["ChatFormat.java", "DeviceFacts.java", "AskFlow.java"]
MAIN_ACTIVITY = APP_JAVA / "MainActivity.java"
ASKFLOW_SRC = APP_JAVA / "AskFlow.java"
APP_MANIFEST = REPO / "assistant-app" / "AndroidManifest.xml"

SYSTEM_MESSAGE = (
    "You are an offline assistant on this phone. You cannot see the "
    "phone's sensors, battery, files, time, or the internet. Say so "
    "plainly when asked about them. Keep answers short.")
SAMPLE_QUESTION = "What is the capital of France?"
EXPECTED_PROMPT = (
    "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
    + SYSTEM_MESSAGE +
    "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
    "What is the capital of France?"
    "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n")


class Fixup3Host(unittest.TestCase):
    """Fixup 3 pure-Java logic, run on a host JVM like ManifestVerifier."""

    @classmethod
    def setUpClass(cls):
        if not JAVAC.exists() or not JAVA.exists():
            raise AssertionError("spike JDK missing under %s" % M4_TOOLS)
        cls.workdir = tempfile.mkdtemp(prefix="m4b-fixup3-")
        build = subprocess.run(
            [str(JAVAC), "--release", "8", "-d", cls.workdir]
            + [str(APP_JAVA / name) for name in FIXUP3_SRCS],
            capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError("javac failed: %s" % build.stderr)

    def _java(self, *args):
        return subprocess.run(
            [str(JAVA), "-cp", self.workdir] + list(args),
            capture_output=True, text=True)

    def test_prompt_golden(self):
        done = self._java("com.stargate.assistant.ChatFormat",
                          "build", SAMPLE_QUESTION)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, EXPECTED_PROMPT)

    def test_token_cap_value(self):
        done = self._java("com.stargate.assistant.ChatFormat", "cap")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "256")

    def test_token_cap_single_setting_wired(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertEqual(src.count("maxNewTokens("), 1)
        self.assertIn("maxNewTokens(ChatFormat.MAX_NEW_TOKENS)", src)
        self.assertNotIn("maxNewTokens(128", src)

    def test_no_product_branding(self):
        self.assertNotIn("StarGate",
                         MAIN_ACTIVITY.read_text(encoding="utf-8"))
        self.assertNotIn("StarGate",
                         APP_MANIFEST.read_text(encoding="utf-8"))
        self.assertIn('android:label="Assistant"',
                      APP_MANIFEST.read_text(encoding="utf-8"))

    def _feed(self, *chunks):
        done = self._java("com.stargate.assistant.AnswerAccumulator",
                          "feed", *chunks)
        self.assertEqual(done.returncode, 0, done.stderr)
        stops = []
        display = None
        for line in done.stdout.split("\n"):
            if line.startswith("chunk "):
                stops.append(line.endswith("stop=true"))
            elif line.startswith("display:"):
                display = line[len("display:"):]
                if display.startswith(" "):
                    display = display[1:]
        self.assertIsNotNone(display, done.stdout)
        self.assertEqual(len(stops), len(chunks), done.stdout)
        return stops, display

    def test_stop_split_end_of_turn(self):
        stops, _ = self._feed("The answer is 4", ".<|eot", "_id|>5. more")
        self.assertEqual(stops, [False, False, True])

    def test_stop_end_of_turn_own_chunk(self):
        stops, _ = self._feed("Hello", "<|eot_id|>", "ramble")
        self.assertEqual(stops, [False, True, True])

    def test_stop_end_of_text(self):
        stops, _ = self._feed("Hi", " there<|end_of_text|>tail")
        self.assertEqual(stops, [False, True])

    def test_no_stop_plain_text(self):
        stops, display = self._feed("Just text")
        self.assertEqual(stops, [False])
        self.assertEqual(display, "Just text")

    def test_display_cuts_at_end_of_turn(self):
        _, display = self._feed("The answer is 4", ".<|eot", "_id|>5. more")
        self.assertEqual(display, "The answer is 4.")

    def test_display_cuts_terminating_chunk(self):
        _, display = self._feed("Hello", "<|eot_id|>", "ramble")
        self.assertEqual(display, "Hello")

    def test_display_cuts_at_end_of_text(self):
        _, display = self._feed("Hi", " there<|end_of_text|>tail")
        self.assertEqual(display, "Hi there")

    def test_display_strips_other_control_tokens(self):
        stops, display = self._feed(
            "A<|start_header_id|>B<|begin_of_text|>C")
        self.assertEqual(stops, [False])
        self.assertEqual(display, "ABC")

    def _model_files(self, *args):
        return self._java("com.stargate.assistant.ModelFiles", *args)

    def test_ensure_creates_missing_folder(self):
        target = os.path.join(self.workdir, "fresh", "model")
        done = self._model_files("ensure", target)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(done.stdout.strip(), "OK")
        self.assertTrue(os.path.isdir(target))
        again = self._model_files("ensure", target)
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(again.stdout.strip(), "OK")

    def test_ensure_reports_creation_failure(self):
        blocker = os.path.join(self.workdir, "blocker")
        with open(blocker, "wb") as handle:
            handle.write(b"x")
        done = self._model_files("ensure",
                                 os.path.join(blocker, "model"))
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith(
            "folder missing (could not create): "), done.stdout)

    def test_checkdir_reports_missing_folder(self):
        missing = os.path.join(self.workdir, "absent-dir")
        done = self._model_files("checkdir", missing, "a.pte", "10",
                                 "tokenizer.model", "5")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(),
                         "folder missing: " + missing)

    def test_checkdir_ready(self):
        folder = os.path.join(self.workdir, "ready")
        os.makedirs(folder)
        with open(os.path.join(folder, "a.pte"), "wb") as handle:
            handle.write(b"A" * 10)
        with open(os.path.join(folder, "tokenizer.model"),
                  "wb") as handle:
            handle.write(b"B" * 5)
        done = self._model_files("checkdir", folder, "a.pte", "10",
                                 "tokenizer.model", "5")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(done.stdout.split(),
                         ["OK:", "a.pte", "OK:", "tokenizer.model"])

    def test_check_reports_each_failure(self):
        folder = os.path.join(self.workdir, "checks")
        os.makedirs(folder)
        missing = self._model_files(
            "check", os.path.join(folder, "absent.pte"), "10")
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(missing.stdout.strip(),
                         "file missing: absent.pte")
        short = os.path.join(folder, "short.pte")
        with open(short, "wb") as handle:
            handle.write(b"A" * 4)
        wrong = self._model_files("check", short, "10")
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(wrong.stdout.strip(),
                         "wrong size: short.pte "
                         "(expected 10 bytes, got 4)")

    def test_check_reports_unreadable(self):
        if os.geteuid() == 0:
            self.skipTest("unreadable check needs a non-root user")
        folder = os.path.join(self.workdir, "unreadable")
        os.makedirs(folder)
        target = os.path.join(folder, "dark.pte")
        with open(target, "wb") as handle:
            handle.write(b"A" * 10)
        os.chmod(target, 0)
        try:
            if os.access(target, os.R_OK):
                self.skipTest("file still readable; cannot test")
            done = self._model_files("check", target, "10")
        finally:
            os.chmod(target, 0o644)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "not readable: dark.pte")

    def test_checkdir_reports_unreadable_folder(self):
        if os.geteuid() == 0:
            self.skipTest("unreadable check needs a non-root user")
        folder = os.path.join(self.workdir, "darkdir")
        os.makedirs(folder)
        os.chmod(folder, 0)
        try:
            if os.access(folder, os.R_OK | os.X_OK):
                self.skipTest("folder still readable; cannot test")
            done = self._model_files("checkdir", folder, "a.pte", "10",
                                     "tokenizer.model", "5")
        finally:
            os.chmod(folder, 0o755)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(),
                         "not readable: " + folder)

    def _stats(self, elapsed_ms, stats_json):
        done = self._java("com.stargate.assistant.StatsLine",
                          "format", str(elapsed_ms), stats_json)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_stats_line_with_tokens(self):
        self.assertEqual(
            self._stats(700, '{"prompt_tokens":42,"generated_tokens":26}'),
            "Offline \u00b7 26 tokens \u00b7 0.7 s")

    def test_stats_line_without_count(self):
        self.assertEqual(self._stats(1500, "{}"),
                         "Offline \u00b7 1.5 s")

    def test_stats_line_rounding(self):
        self.assertEqual(
            self._stats(65, '{"generated_tokens":3}'),
            "Offline \u00b7 3 tokens \u00b7 0.1 s")


APP_ANSWER = ("I'm an offline assistant and can't see the phone's "
              "battery, sensors, files, time or network.")
APP_STATS = "Answered by the app"

FAKE_ASK = """\
import com.stargate.assistant.AskFlow;

/** Fake runner: records call order and simulated start positions. */
public class FakeAsk {
    static final class Fake implements AskFlow.Runner {
        int pos = 0;
        final StringBuilder log = new StringBuilder();

        @Override
        public void resetContext() {
            log.append("reset\\n");
            pos = 0;
        }

        @Override
        public void generate(String prompt,
                AskFlow.GenerateCallback callback) {
            log.append("generate start=" + pos + "\\n");
            pos += 67;
        }
    }

    static final class Nop implements AskFlow.GenerateCallback {
        @Override
        public void onResult(String token) {
        }

        @Override
        public void onStats(String stats) {
        }

        @Override
        public void onError(int code, String message) {
            System.out.println("error: " + code + " " + message);
        }

        @Override
        public void onAppAnswer(String answer) {
            System.out.println("app: " + answer);
        }
    }

    public static void main(String[] args) {
        Fake fake = new Fake();
        Nop nop = new Nop();
        boolean first = AskFlow.ask(
                fake, "What is the capital of France?", nop);
        boolean second = AskFlow.ask(fake, "And its population?", nop);
        boolean third = AskFlow.ask(
                fake, "What's the current battery level?", nop);
        System.out.print(fake.log.toString());
        System.out.println("app answered: " + first + " " + second
                + " " + third);
    }
}
"""


class Fixup4Host(unittest.TestCase):
    """Fixup 4 pure-Java logic, run on a host JVM like Fixup3Host."""

    @classmethod
    def setUpClass(cls):
        if not JAVAC.exists() or not JAVA.exists():
            raise AssertionError("spike JDK missing under %s" % M4_TOOLS)
        cls.workdir = tempfile.mkdtemp(prefix="m4b-fixup4-")
        build = subprocess.run(
            [str(JAVAC), "--release", "8", "-d", cls.workdir]
            + [str(APP_JAVA / name) for name in FIXUP4_SRCS],
            capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError("javac failed: %s" % build.stderr)
        harness = os.path.join(cls.workdir, "FakeAsk.java")
        with open(harness, "w", encoding="utf-8") as handle:
            handle.write(FAKE_ASK)
        build_fake = subprocess.run(
            [str(JAVAC), "--release", "8", "-cp", cls.workdir,
             "-d", cls.workdir, harness],
            capture_output=True, text=True)
        if build_fake.returncode != 0:
            raise AssertionError(
                "javac fake failed: %s" % build_fake.stderr)

    def _java(self, *args):
        return subprocess.run(
            [str(JAVA), "-cp", self.workdir] + list(args),
            capture_output=True, text=True)

    def _check(self, question):
        done = self._java("com.stargate.assistant.DeviceFacts",
                          "check", question)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_catches_battery_level(self):
        self.assertEqual(
            self._check("What's the current battery level?"), "YES")

    def test_catches_time(self):
        self.assertEqual(self._check("what time is it"), "YES")

    def test_catches_storage(self):
        self.assertEqual(
            self._check("How much storage is left"), "YES")

    def test_ignores_packing_list(self):
        self.assertEqual(
            self._check("Give me a 3-item packing list for a rainy day "
                       "hike"),
            "NO")

    def test_ignores_battery_explainer(self):
        self.assertEqual(
            self._check("Explain what a battery does"), "NO")

    def test_ignores_substrings(self):
        # Whole words only: "updates" holds "date", "sometimes"
        # holds "time", but neither asks about this phone.
        self.assertEqual(
            self._check("Explain how updates work"), "NO")
        self.assertEqual(self._check("I sometimes sing"), "NO")

    def test_catches_day_and_location_phrases(self):
        self.assertEqual(self._check("What day is it?"), "YES")
        self.assertEqual(self._check("Where am I?"), "YES")

    def test_answer_text(self):
        done = self._java("com.stargate.assistant.DeviceFacts",
                          "answer")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), APP_ANSWER)

    def test_stats_line(self):
        done = self._java("com.stargate.assistant.DeviceFacts",
                          "stats")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), APP_STATS)

    def _ask_flow(self):
        done = self._java("FakeAsk")
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _runner_lines(self, output):
        return [line for line in output.splitlines()
                if line == "reset" or line.startswith("generate")]

    def test_reset_before_every_generate(self):
        lines = self._runner_lines(self._ask_flow())
        self.assertEqual(lines, ["reset", "generate start=0",
                                 "reset", "generate start=0"])

    def test_device_fact_never_touches_runner(self):
        output = self._ask_flow()
        # Two questions reached the model (4 runner calls); the
        # battery question was answered by the app instead.
        self.assertEqual(len(self._runner_lines(output)), 4)
        self.assertIn("app answered: false false true", output)
        self.assertEqual(output.count("app: "), 1)
        self.assertIn("app: " + APP_ANSWER, output)

    def test_askflow_resets_before_generating(self):
        src = ASKFLOW_SRC.read_text(encoding="utf-8")
        reset = src.find("runner.resetContext()")
        generate = src.find("runner.generate(")
        self.assertNotEqual(reset, -1)
        self.assertNotEqual(generate, -1)
        self.assertLess(reset, generate)

    def test_main_activity_asks_through_flow(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertEqual(src.count("AskFlow.ask("), 1)
        self.assertIn("new LlmRunner()", src)
        self.assertNotIn("ChatFormat.buildPrompt(", src)

    def test_main_activity_reset_wiring(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("module.resetContext()", src)
        self.assertEqual(src.count("resetContext()"), 2)

    def test_main_activity_app_answer_wiring(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("onAppAnswer(String answer)", src)
        self.assertIn("setAnswer(answer)", src)
        self.assertIn("DeviceFacts.APP_STATS_LINE", src)


class GuardAgainstFakeAdb(unittest.TestCase):
    """Section 10 device guard, extracted from the docs and run as-is."""

    GUARD_FENCE = "```sh\n# check-device guard"

    def _guard_src(self):
        text = (REPO / "docs" / "m4-assistant.md").read_text(
            encoding="utf-8")
        self.assertEqual(text.count(self.GUARD_FENCE), 1,
                         "guard block must appear exactly once in docs")
        start = text.find(self.GUARD_FENCE) + len("```sh\n")
        end = text.find("```", start)
        self.assertNotEqual(end, -1)
        return text[start:end]

    def _run_guard(self, devices_out, avd_out, serial="emulator-5554",
                   with_serial=True):
        tmp = tempfile.mkdtemp(prefix="m4b-guard-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        devices_file = os.path.join(tmp, "devices.txt")
        with open(devices_file, "wb") as handle:
            handle.write(devices_out)
        log_file = os.path.join(tmp, "adb.log")
        fake = os.path.join(tmp, "adb")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n"
                         "echo \"$*\" >> \"$FAKE_ADB_LOG\"\n"
                         "if [ \"$1\" = \"devices\" ]; then\n"
                         "  cat \"$FAKE_DEVICES\"\n"
                         "elif [ \"$1\" = \"-s\" ] && [ \"$3\" = \"emu\" ]; "
                         "then\n"
                         "  printf '%s' \"$FAKE_AVD\"\n"
                         "else\n"
                         "  echo \"unexpected adb call: $*\" >&2\n"
                         "  exit 1\n"
                         "fi\n")
        os.chmod(fake, 0o755)
        runner = os.path.join(tmp, "run.sh")
        with open(runner, "w", encoding="utf-8") as handle:
            handle.write(self._guard_src() + "\nguard\n")
        env = dict(os.environ)
        env["PATH"] = tmp + ":" + env.get("PATH", "")
        env["FAKE_DEVICES"] = devices_file
        env["FAKE_AVD"] = avd_out
        env["FAKE_ADB_LOG"] = log_file
        if with_serial:
            env["SERIAL"] = serial
        else:
            env.pop("SERIAL", None)
        done = subprocess.run(["sh", runner], capture_output=True,
                              text=True, env=env)
        calls = []
        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8") as handle:
                calls = [line.strip() for line in handle if line.strip()]
        return done.returncode, calls

    def _devices(self, *rows):
        out = b"List of devices attached\r\n"
        for row in rows:
            out += row + b"\r\n"
        return out

    def test_single_valid_emulator_allowed(self):
        code, calls = self._run_guard(
            self._devices(b"emulator-5554\tdevice"),
            "stargate-m4b-assistant\r\n")
        self.assertEqual(code, 0)
        self.assertEqual(calls.count("devices"), 1)
        self.assertIn("-s emulator-5554 emu avd name", calls)

    def test_extra_unauthorized_denied(self):
        code, _ = self._run_guard(
            self._devices(b"emulator-5554\tdevice",
                          b"emulator-5556\tunauthorized"),
            "stargate-m4b-assistant\r\n")
        self.assertNotEqual(code, 0)

    def test_extra_offline_denied(self):
        code, _ = self._run_guard(
            self._devices(b"emulator-5554\tdevice",
                          b"emulator-5556\toffline"),
            "stargate-m4b-assistant\r\n")
        self.assertNotEqual(code, 0)

    def test_two_emulators_denied_without_avd_check(self):
        code, calls = self._run_guard(
            self._devices(b"emulator-5554\tdevice",
                          b"emulator-5556\tdevice"),
            "stargate-m4b-assistant\r\n")
        self.assertNotEqual(code, 0)
        self.assertEqual([call for call in calls if "emu" in call], [])

    def test_empty_list_denied(self):
        code, _ = self._run_guard(
            self._devices(), "stargate-m4b-assistant\r\n")
        self.assertNotEqual(code, 0)

    def test_wrong_serial_denied(self):
        code, _ = self._run_guard(
            self._devices(b"emulator-5556\tdevice"),
            "stargate-m4b-assistant\r\n", serial="emulator-5554")
        self.assertNotEqual(code, 0)

    def test_wrong_avd_denied(self):
        code, _ = self._run_guard(
            self._devices(b"emulator-5554\tdevice"), "other-avd\r\n")
        self.assertNotEqual(code, 0)

    def test_missing_serial_denied(self):
        code, _ = self._run_guard(
            self._devices(b"emulator-5554\tdevice"),
            "stargate-m4b-assistant\r\n", with_serial=False)
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
