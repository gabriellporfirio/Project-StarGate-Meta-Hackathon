"""Tests for the read-only probe (offline, no processes for refusals)."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from stargate import probe
from stargate.probe import (
    ALLOWLIST,
    RealAdbTransport,
    RefusedCommand,
    SimulatedTransport,
    TransportResult,
)
from tests.procguard import process_guards

REPO = Path(__file__).resolve().parent.parent


class _RecordingTransport(probe.Transport):
    def __init__(self):
        self.calls = []

    def run(self, command, timeout_seconds):
        self.calls.append(command)
        return TransportResult(
            stdout=b"", stderr="", exit_code=0, timed_out=False)


class AllowlistRefusal(unittest.TestCase):
    def test_every_non_allowlisted_command_refused_before_transport(self):
        bad_commands = [
            "reboot",
            "fastboot devices",
            "setprop foo bar",
            "pm list packages",
            "am start foo",
            "rm -rf /",
            "su",
            "dd if=/dev/zero of=/dev/null",
            "getprop",  # full dump refused (serial risk)
            "getprop ro.serialno",
            "getprop ro.boot.serialno",
            "cat /proc/meminfo ",
            " cat /proc/meminfo",
            "GETPROP ro.product.model",
        ]
        for command in bad_commands:
            with self.subTest(command=command):
                allowed, _ = probe.check_command(command)
                self.assertFalse(allowed)
                transport = _RecordingTransport()
                with self.assertRaises(RefusedCommand):
                    probe.run_probe(transport, command)
                self.assertEqual(transport.calls, [])

    def test_metacharacters_refused_before_transport(self):
        bad = [
            "getprop ro.product.model; reboot",
            "getprop ro.product.model | cat",
            "getprop ro.product.model &",
            "getprop ro.product.model > /tmp/x",
            "getprop ro.product.model < /tmp/x",
            "getprop `ro.product.model`",
            "getprop $(ro.product.model)",
            "getprop ro.product.model\nreboot",
            "getprop ro.product.model\r\n",
        ]
        for command in bad:
            with self.subTest(command=repr(command)):
                allowed, reason = probe.check_command(command)
                self.assertFalse(allowed)
                self.assertIn("metacharacters", reason)
                transport = _RecordingTransport()
                with self.assertRaises(RefusedCommand):
                    probe.run_probe(transport, command)
                self.assertEqual(transport.calls, [])

    def test_allowlist_exactly(self):
        self.assertEqual(len(ALLOWLIST), 20)
        for command in ALLOWLIST:
            with self.subTest(command=command):
                allowed, _ = probe.check_command(command)
                self.assertTrue(allowed)


class Limits(unittest.TestCase):
    def test_timeout_maps(self):
        transport = mock.MagicMock()
        transport.run.return_value = TransportResult(
            stdout=b"", stderr="", exit_code=124, timed_out=True)
        with self.assertRaises(probe.ProbeTimeout):
            probe.run_probe(transport, ALLOWLIST[0])

    def test_oversize_maps(self):
        transport = mock.MagicMock()
        transport.run.return_value = TransportResult(
            stdout=b"x" * (probe.MAX_OUTPUT_BYTES_PER_COMMAND + 1),
            stderr="", exit_code=0, timed_out=False)
        with self.assertRaises(probe.ProbeOutputTooLarge):
            probe.run_probe(transport, ALLOWLIST[0])

    def test_numeric_limits(self):
        self.assertEqual(probe.TIMEOUT_PER_COMMAND_SECONDS, 5.0)
        self.assertEqual(probe.MAX_OUTPUT_BYTES_PER_COMMAND, 64 * 1024)
        self.assertEqual(probe.TOTAL_SCAN_SECONDS, 60.0)
        self.assertEqual(probe.MAX_CONFIG_GZ_DECOMPRESSED_BYTES, 1024 * 1024)


class Redaction(unittest.TestCase):
    def test_imei_like_redacted(self):
        self.assertNotIn("490154203237518",
                         probe.redact_text("imei 490154203237518") or "")
        self.assertIn("***", probe.redact_text("490154203237518") or "")

    def test_serial_word_redacted(self):
        self.assertEqual(probe.redact_text("serial: ABC123"), "[redacted]")

    def test_excerpt_capped(self):
        excerpt = probe.make_excerpt("y" * 500)
        self.assertLessEqual(len(excerpt), 200)

    def test_values_evidence_errors_redacted(self):
        serial = "490154203237518"
        phone = "+44 7700 900123"
        value = probe.redact_text("unit %s" % serial) or ""
        self.assertNotIn(serial, value)
        self.assertIn("***", value)
        excerpt = probe.make_excerpt("callback %s failed" % phone)
        self.assertNotIn("7700", excerpt)
        self.assertIn("***", excerpt)
        error = probe.redact_text(
            "probe error: device %s, callback %s" % (serial, phone)) or ""
        self.assertNotIn(serial, error)
        self.assertNotIn("7700 900123", error)
        self.assertNotIn("+44", error)
        self.assertIn("***", error)

    def test_phone_numbers_redacted_on_all_surfaces(self):
        for phone in ("+44 7700 900123", "07700900123"):
            with self.subTest(phone=phone):
                digits = phone.replace(" ", "").replace("+", "")
                value = probe.redact_text("contact %s" % phone) or ""
                self.assertNotIn(digits, value.replace(" ", ""))
                self.assertIn("***", value)
                excerpt = probe.make_excerpt("contact %s ok" % phone)
                self.assertNotIn(digits, excerpt.replace(" ", ""))
                self.assertIn("***", excerpt)
                error = probe.redact_text(
                    "dial failed for %s" % phone) or ""
                self.assertNotIn(digits, error.replace(" ", ""))
                self.assertIn("***", error)

    def test_serialno_with_value_redacted(self):
        for text in ("serialno ABC123XYZ", "ro.serialno=ABC123XYZ"):
            with self.subTest(text=text):
                self.assertEqual(probe.redact_text(text), "[redacted]")
                self.assertEqual(probe.make_excerpt(text), "[redacted]")
                self.assertEqual(
                    probe.redact_text("error: %s" % text), "[redacted]")

    def test_legitimate_fixture_numbers_untouched(self):
        for name in ("budget", "flagship", "old32", "locked"):
            target = REPO / "fixtures" / name / "outputs.txt"
            lines = target.read_text(encoding="utf-8").splitlines()
            payload = json.loads("\n".join(lines[1:]))
            for command, entry in payload.items():
                for key in ("stdout", "stderr"):
                    for line in str(entry.get(key, "")).splitlines():
                        if not line.strip():
                            continue
                        with self.subTest(fixture=name, command=command):
                            self.assertEqual(
                                probe.redact_text(line), line)


class Transports(unittest.TestCase):
    def test_simulated_replays(self):
        transport = SimulatedTransport(REPO / "fixtures" / "budget")
        result = probe.run_probe(transport, "getprop ro.product.model")
        self.assertEqual(result.stdout.decode("utf-8").strip(),
                         "Acme Budget 1")

    def test_real_stub_refuses_without_process(self):
        transport = RealAdbTransport()
        with process_guards() as guards:
            with self.assertRaises(probe.RealDevicesNotEnabled):
                transport.run("getprop ro.product.model", 5.0)
        for guard in guards:
            guard.assert_not_called()

    def test_guards_catch_process_spawn(self):
        with process_guards():
            with self.assertRaises(AssertionError):
                subprocess.run(["echo", "hi"])
            with self.assertRaises(AssertionError):
                probe.os.system("echo hi")


if __name__ == "__main__":
    unittest.main()
