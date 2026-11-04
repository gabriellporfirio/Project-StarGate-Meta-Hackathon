"""Offline tests for the Mac-steps device guard (M4 Parts 2 and 3).

The sg_guard function is extracted from docs/m4-build.md as-is and
run against a fake adb through 12 cases (allow once, deny on extra,
offline and unauthorized rows, empty, wrong serial, wrong AVD, CRLF,
failing adb even with valid-looking output, daemon-startup chatter),
under every available shell. Every adb -s line in the doc's shell
blocks must also carry the guard.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "m4-build.md"

GUARD_PREFIX = "sg_guard() {"
EXPECTED_AVD = "stargate-m4-files"
SERIAL = "emulator-5554"

CHATTER = (b"* daemon not running; starting now at "
           b"tcp:5037\n* daemon started successfully\n")


def _shell_blocks():
    text = DOC.read_text(encoding="utf-8")
    blocks = []
    pos = 0
    while True:
        start = text.find("```sh\n", pos)
        if start == -1:
            return blocks
        end = text.find("```", start + 6)
        assert end != -1, "unterminated sh fence in docs/m4-build.md"
        blocks.append(text[start + 6:end])
        pos = end + 3


def _guard_definitions():
    found = []
    for block in _shell_blocks():
        for line in block.splitlines():
            if line.startswith(GUARD_PREFIX):
                found.append(line)
    return found


def _shells():
    shells = []
    for name in ("sh", "bash", "zsh"):
        if shutil.which(name) is not None:
            shells.append(name)
    return shells


class GuardDefinitions(unittest.TestCase):
    def test_guard_present_and_identical(self):
        definitions = _guard_definitions()
        self.assertGreaterEqual(len(definitions), 1)
        for definition in definitions:
            self.assertEqual(definition, definitions[0])

    def test_every_adb_command_guarded(self):
        seen_adb = False
        for block in _shell_blocks():
            for line in block.splitlines():
                if "adb -s" not in line:
                    continue
                if line.startswith(GUARD_PREFIX):
                    continue
                seen_adb = True
                self.assertIn("sg_guard", line, line)
        self.assertTrue(seen_adb)


class GuardAgainstFakeAdb(unittest.TestCase):
    def _run_guard(self, shell, devices_out, avd_out,
                   devices_rc=0, avd_rc=0):
        tmp = tempfile.mkdtemp(prefix="m4a-guard-")
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
                         "  exit \"${FAKE_DEVICES_RC:-0}\"\n"
                         "elif [ \"$1\" = \"-s\" ] && [ \"$3\" = \"emu\" ]; "
                         "then\n"
                         "  printf '%s' \"$FAKE_AVD\"\n"
                         "  exit \"${FAKE_AVD_RC:-0}\"\n"
                         "else\n"
                         "  echo \"unexpected adb call: $*\" >&2\n"
                         "  exit 1\n"
                         "fi\n")
        os.chmod(fake, 0o755)
        definitions = _guard_definitions()
        self.assertTrue(definitions)
        runner = os.path.join(tmp, "run.sh")
        with open(runner, "w", encoding="utf-8") as handle:
            handle.write(definitions[0] + "\n"
                         "sg_guard %s\n" % EXPECTED_AVD)
        env = dict(os.environ)
        env["PATH"] = tmp + ":" + env.get("PATH", "")
        env["FAKE_DEVICES"] = devices_file
        env["FAKE_AVD"] = avd_out
        env["FAKE_ADB_LOG"] = log_file
        env["FAKE_DEVICES_RC"] = str(devices_rc)
        env["FAKE_AVD_RC"] = str(avd_rc)
        env["SERIAL"] = SERIAL
        done = subprocess.run([shell, runner], capture_output=True,
                              text=True, env=env, timeout=60)
        calls = []
        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8") as handle:
                calls = [line.strip() for line in handle
                         if line.strip()]
        return done.returncode, calls

    def _devices(self, *rows):
        out = b"List of devices attached\n"
        for row in rows:
            out += row + b"\n"
        return out

    def _each_shell(self, devices_out, avd_out, **kwargs):
        shells = _shells()
        self.assertIn("sh", shells)
        results = []
        for shell in shells:
            with self.subTest(shell=shell):
                results.append(self._run_guard(
                    shell, devices_out, avd_out, **kwargs))
        return results

    def test_allow_single_valid_emulator_once(self):
        for code, calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice"),
                "stargate-m4-files\n"):
            self.assertEqual(code, 0)
            self.assertEqual(calls, ["devices",
                                     "-s emulator-5554 emu avd name"])

    def test_deny_second_device_row(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice",
                              b"emulator-5556\tdevice"),
                "stargate-m4-files\n"):
            self.assertNotEqual(code, 0)

    def test_deny_offline_row(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice",
                              b"emulator-5556\toffline"),
                "stargate-m4-files\n"):
            self.assertNotEqual(code, 0)

    def test_deny_unauthorized_row(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice",
                              b"emulator-5556\tunauthorized"),
                "stargate-m4-files\n"):
            self.assertNotEqual(code, 0)

    def test_deny_empty(self):
        for code, _calls in self._each_shell(
                self._devices(), "stargate-m4-files\n"):
            self.assertNotEqual(code, 0)

    def test_deny_wrong_serial(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5556\tdevice"),
                "stargate-m4-files\n"):
            self.assertNotEqual(code, 0)

    def test_deny_wrong_avd(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice"),
                "some-other-avd\n"):
            self.assertNotEqual(code, 0)

    def test_allow_crlf(self):
        out = (b"List of devices attached\r\n"
               b"emulator-5554\tdevice\r\n")
        for code, _calls in self._each_shell(
                out, "stargate-m4-files\r\n"):
            self.assertEqual(code, 0)

    def test_deny_failing_adb(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice"),
                "stargate-m4-files\n", devices_rc=1):
            self.assertNotEqual(code, 0)

    def test_allow_daemon_chatter_after_header(self):
        out = (b"List of devices attached\n"
               b"* daemon started successfully\n"
               b"emulator-5554\tdevice\n")
        for code, _calls in self._each_shell(
                out, "stargate-m4-files\n"):
            self.assertEqual(code, 0)

    def test_allow_chatter_before_header(self):
        out = (CHATTER + b"List of devices attached\n"
               b"emulator-5554\tdevice\n")
        for code, _calls in self._each_shell(
                out, "stargate-m4-files\n"):
            self.assertEqual(code, 0)

    def test_deny_avd_failure(self):
        for code, _calls in self._each_shell(
                self._devices(b"emulator-5554\tdevice"), "",
                avd_rc=1):
            self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
