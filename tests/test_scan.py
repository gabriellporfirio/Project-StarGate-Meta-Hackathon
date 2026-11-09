"""Tests for stargate scan (offline, simulated only)."""
from __future__ import annotations

import gzip
import io
import json
import re
import socket
import unittest
import urllib.request
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stargate import cli, probe, scan
from stargate.profile import validate_profile
from stargate.probe import SimulatedTransport, TransportResult
from tests.procguard import process_guards

REPO = Path(__file__).resolve().parent.parent
SERIAL_LIKE = re.compile(r"\b\d{14,19}\b")
SENSITIVE_WORD = re.compile(
    r"(?i)\bserial\b|\bimei\b|\bmeid\b|\bimsi\b")


def _no_network(*args, **kwargs):
    raise AssertionError("network call during offline scan")


class FourFixtures(unittest.TestCase):
    def test_reports_differ(self):
        reports = {}
        for name in scan.KNOWN_FIXTURES:
            exit_code, output = scan.run_scan(name, False)
            self.assertEqual(exit_code, 0)
            reports[name] = output
        self.assertEqual(len(set(reports.values())),
                         len(scan.KNOWN_FIXTURES))

    def test_budget_has_unknown_with_reason(self):
        _, output = scan.run_scan("budget", False)
        self.assertIn("unknown (", output)
        profile = scan.build_profile("budget")
        unknowns = [fact for fact in profile["facts"].values()
                    if fact["status"] == "unknown"]
        self.assertGreaterEqual(len(unknowns), 1)
        self.assertTrue(all(fact.get("unknown_reason") for fact in unknowns))

    def test_locked_unlock_trio_separate(self):
        profile = scan.build_profile("locked")
        facts = profile["facts"]
        locked_state = facts["boot.bootloader_locked"]
        self.assertEqual(locked_state["status"], "inferred")
        self.assertIs(locked_state["value"], True)
        self.assertEqual(locked_state["inference"]["rule_id"],
                         "R-LOCK-FROM-VBS")
        self.assertEqual(facts["boot.oem_unlock_supported"]["value"], True)
        allowed = facts["boot.oem_unlock_allowed"]
        self.assertEqual(allowed["status"], "unknown")
        self.assertEqual(allowed["unknown_reason"], "permission_denied")
        _, output = scan.run_scan("locked", False)
        self.assertIn("Bootloader locked", output)
        self.assertIn("OEM unlocking supported", output)
        self.assertIn("OEM unlocking allowed", output)
        self.assertIn("Whether it can be unlocked", output)

    def test_json_validates_and_simulated(self):
        for name in scan.KNOWN_FIXTURES:
            with self.subTest(fixture=name):
                exit_code, output = scan.run_scan(name, True)
                self.assertEqual(exit_code, 0)
                payload = json.loads(output)
                self.assertTrue(payload["simulated"])
                self.assertEqual(payload["fixture_name"], name)
                self.assertEqual(validate_profile(payload), [])

    def test_no_fit_conclusions(self):
        for name in scan.KNOWN_FIXTURES:
            _, report = scan.run_scan(name, False)
            lowered = report.lower()
            self.assertNotIn("fits", lowered)
            self.assertNotIn("doesn't fit", lowered)
            self.assertNotIn("does not fit", lowered)
            _, dumped = scan.run_scan(name, True)
            self.assertNotIn("fits", dumped.lower())

    def test_no_serial_like_in_values_evidence_errors(self):
        for name in scan.KNOWN_FIXTURES:
            profile = scan.build_profile(name)
            blob = json.dumps(profile)
            self.assertIsNone(SERIAL_LIKE.search(blob))
            self.assertIsNone(SENSITIVE_WORD.search(blob))
            _, report = scan.run_scan(name, False)
            self.assertIsNone(SERIAL_LIKE.search(report))
            self.assertIsNone(SENSITIVE_WORD.search(report))

    def test_byte_counts_stay_typed_integers(self):
        for name in scan.KNOWN_FIXTURES:
            facts = scan.build_profile(name)["facts"]
            for fact_id in ("resources.ram_total", "resources.data_free"):
                with self.subTest(fixture=name, fact=fact_id):
                    fact = facts[fact_id]
                    self.assertEqual(fact["status"], "measured")
                    self.assertIs(type(fact["value"]), int)

    def test_offline(self):
        with mock.patch.object(socket, "socket", side_effect=_no_network), \
                mock.patch.object(urllib.request, "urlopen",
                                  side_effect=_no_network):
            for name in scan.KNOWN_FIXTURES:
                exit_code, _ = scan.run_scan(name, False)
                self.assertEqual(exit_code, 0)
                exit_code, _ = scan.run_scan(name, True)
                self.assertEqual(exit_code, 0)

    def test_facts_come_from_probe_not_name(self):
        budget_facts = scan.build_profile("budget")["facts"]
        relabelled = scan.build_profile(
            "locked", transport=SimulatedTransport(REPO / "fixtures" / "budget"))
        self.assertEqual(relabelled["facts"], budget_facts)
        self.assertEqual(relabelled["fixture_name"], "locked")

    def test_reserved_facts(self):
        for name in scan.KNOWN_FIXTURES:
            facts = scan.build_profile(name)["facts"]
            capacity = facts["capacity.system_image_max"]
            self.assertEqual(capacity["status"], "unknown")
            self.assertEqual(capacity["unknown_reason"], "deferred")
            for fact_id in scan.RUNTIME_FACTS:
                item = facts[fact_id]
                self.assertEqual(item["status"], "unknown")
                self.assertEqual(item["unknown_reason"], "not_probed_in_m1")

    def test_kernel_config_flagship_readable_others_not(self):
        flagship = scan.build_profile("flagship")["facts"]
        self.assertEqual(flagship["kernel.config.tun"]["value"], "built_in")
        self.assertEqual(flagship["kernel.config.exfat"]["value"], "module")
        for name in ("budget", "old32", "locked"):
            facts = scan.build_profile(name)["facts"]
            for fact_id in scan.CONFIG_SYMBOLS:
                self.assertEqual(facts[fact_id]["status"], "unknown")
                self.assertEqual(facts[fact_id]["unknown_reason"],
                                 "not_exposed")

    def test_old32_is_32bit_only(self):
        facts = scan.build_profile("old32")["facts"]
        abis = facts["resources.cpu_abis"]["value"]
        self.assertTrue(all("64" not in part for part in abis))
        self.assertIn("armeabi-v7a", abis)

    def test_unlock_not_supported(self):
        facts = scan.build_profile("budget")["facts"]
        self.assertEqual(facts["boot.oem_unlock_supported"]["value"], False)


class LimitsAndConflicting(unittest.TestCase):
    def test_timeout_becomes_unknown(self):
        outputs = {}
        for command in probe.ALLOWLIST:
            outputs[command] = {"exit": 0, "stdout": "x\n", "stderr": "",
                                "timeout": True}
        transport = _DictTransport(outputs)
        profile = scan.build_profile("budget", transport=transport)
        self.assertEqual(
            profile["facts"]["identity.model"]["unknown_reason"], "timeout")

    def test_oversize_becomes_unknown(self):
        class BigTransport(SimulatedTransport):
            def run(self, command, timeout_seconds):
                self.calls.append(command)
                return TransportResult(
                    stdout=b"z" * (probe.MAX_OUTPUT_BYTES_PER_COMMAND + 1),
                    stderr="", exit_code=0, timed_out=False)

        transport = BigTransport(REPO / "fixtures" / "budget")
        profile = scan.build_profile("budget", transport=transport)
        self.assertEqual(
            profile["facts"]["identity.model"]["unknown_reason"],
            "output_too_large")

    def test_total_scan_cap_with_fake_clock(self):
        transport = SimulatedTransport(REPO / "fixtures" / "budget")

        def clock():
            # Trip the 60 s budget once two commands have fully returned.
            if len(transport.calls) < 3:
                return 0.0
            return 1000.0

        with mock.patch.object(scan.time, "monotonic", side_effect=clock):
            profile = scan.build_profile("budget", transport=transport)
        self.assertEqual(len(transport.calls), 3)
        facts = profile["facts"]
        for fact_id in scan.ALL_FACT_IDS:
            with self.subTest(fact=fact_id):
                fact = facts[fact_id]
                if fact_id in ("identity.manufacturer", "identity.model"):
                    self.assertEqual(fact["status"], "measured")
                elif fact_id == "capacity.system_image_max":
                    self.assertEqual(fact["status"], "unknown")
                    self.assertEqual(fact["unknown_reason"], "deferred")
                elif fact_id in scan.RUNTIME_FACTS:
                    self.assertEqual(fact["status"], "unknown")
                    self.assertEqual(
                        fact["unknown_reason"], "not_probed_in_m1")
                elif fact_id == "boot.bootloader_locked":
                    self.assertEqual(fact["status"], "unknown")
                    self.assertEqual(fact["unknown_reason"], "deferred")
                else:
                    self.assertEqual(fact["status"], "unknown")
                    self.assertEqual(fact["unknown_reason"], "timeout")

    def test_config_gz_decompressed_cap(self):
        over = probe.MAX_CONFIG_GZ_DECOMPRESSED_BYTES + 100000
        gzipped = gzip.compress(b"0" * over)
        self.assertLess(len(gzipped), probe.MAX_OUTPUT_BYTES_PER_COMMAND)
        transport = _ConfigGzTransport(gzipped)
        profile = scan.build_profile("budget", transport=transport)
        for fact_id in scan.CONFIG_SYMBOLS:
            with self.subTest(fact=fact_id):
                fact = profile["facts"][fact_id]
                self.assertEqual(fact["status"], "unknown")
                self.assertEqual(fact["unknown_reason"], "output_too_large")
        self.assertEqual(
            profile["facts"]["identity.model"]["status"], "measured")

    def test_gunzip_capped_never_expands_fully(self):
        limit = probe.MAX_CONFIG_GZ_DECOMPRESSED_BYTES
        bomb = gzip.compress(b"0" * (3 * limit))

        real_decompressobj = zlib.decompressobj
        seen = {}

        class SpyObj:
            def __init__(self, wbits):
                seen.setdefault("made", 0)
                seen["made"] += 1
                self._inner = real_decompressobj(wbits)

            def decompress(self, data, max_length=0):
                seen["calls"] = seen.get("calls", 0) + 1
                seen["max_length"] = max_length
                out = self._inner.decompress(data, max_length)
                seen["out_total"] = seen.get("out_total", 0) + len(out)
                return out

            def __getattr__(self, name):
                return getattr(self._inner, name)

        with mock.patch.object(scan.zlib, "decompressobj", SpyObj):
            self.assertIsNone(scan._gunzip_capped(bomb))
        self.assertTrue(seen.get("calls", 0) >= 1)
        self.assertEqual(seen.get("max_length"), limit + 1)
        self.assertLessEqual(seen.get("out_total", 0), limit + 1)
        small = gzip.compress(b"CONFIG_TUN=y\n")
        self.assertEqual(scan._gunzip_capped(small), "CONFIG_TUN=y\n")

    def test_conflicting_report_shows_candidates(self):
        profile = scan.build_profile("budget")
        profile["facts"]["identity.model"] = {
            "status": "conflicting",
            "value": None,
            "unit": None,
            "candidates": [
                {"value": "A", "evidence": [
                    {"probe_id": "p", "command": "getprop ro.product.model",
                     "excerpt": "A",
                     "observed_at": "2026-10-30T12:00:01Z"}]},
                {"value": "B", "evidence": [
                    {"probe_id": "p", "command": "getprop ro.product.model",
                     "excerpt": "B",
                     "observed_at": "2026-10-30T12:00:02Z"}]},
            ],
        }
        report = scan.render_report(profile)
        self.assertIn("conflicting", report)
        self.assertIn('"A"', report)
        self.assertIn('"B"', report)

    def test_conflicting_survives_scan_json(self):
        profile = scan.build_profile("budget")
        profile["facts"]["identity.model"] = {
            "status": "conflicting",
            "value": None,
            "unit": None,
            "candidates": [
                {"value": "Acme A", "evidence": [
                    {"probe_id": "getprop-ro.product.model",
                     "command": "getprop ro.product.model",
                     "excerpt": "Acme A",
                     "observed_at": "2026-10-30T12:00:01Z"}]},
                {"value": "Acme B", "evidence": [
                    {"probe_id": "getprop-ro.product.model",
                     "command": "getprop ro.product.model",
                     "excerpt": "Acme B",
                     "observed_at": "2026-10-30T12:00:02Z"}]},
            ],
        }
        with mock.patch.object(scan, "build_profile", return_value=profile):
            exit_code, dumped = scan.run_scan("budget", True)
        self.assertEqual(exit_code, 0)
        loaded = json.loads(dumped)
        self.assertEqual(validate_profile(loaded), [])
        fact = loaded["facts"]["identity.model"]
        self.assertEqual(fact["status"], "conflicting")
        self.assertIsNone(fact["value"])
        self.assertEqual(len(fact["candidates"]), 2)
        self.assertEqual(fact["candidates"][0]["value"], "Acme A")
        self.assertEqual(fact["candidates"][1]["value"], "Acme B")
        for candidate in fact["candidates"]:
            self.assertTrue(candidate["evidence"])
            for item in candidate["evidence"]:
                self.assertIn("excerpt", item)


class ScanCli(unittest.TestCase):
    def test_no_sim_exits_3_without_process(self):
        with process_guards() as guards:
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = cli.main(["scan"])
        self.assertEqual(exit_code, 3)
        self.assertIn("real devices are not enabled in M1", buf.getvalue())
        for guard in guards:
            guard.assert_not_called()

    def test_sim_json_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.main(["scan", "--sim", "budget", "--json"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(validate_profile(payload), [])

    def test_unknown_sim_exits_2(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.main(["scan", "--sim", "nophone"])
        self.assertEqual(exit_code, 2)


class _ConfigGzTransport(SimulatedTransport):
    def __init__(self, gz_bytes):
        super().__init__(REPO / "fixtures" / "budget")
        self._gz_bytes = gz_bytes
        self._inner = SimulatedTransport(REPO / "fixtures" / "budget")

    def run(self, command, timeout_seconds):
        self.calls.append(command)
        if command == "cat /proc/config.gz":
            return TransportResult(stdout=self._gz_bytes, stderr="",
                                   exit_code=0, timed_out=False)
        return self._inner.run(command, timeout_seconds)


class _DictTransport(SimulatedTransport):
    def __init__(self, outputs):
        super().__init__(REPO / "fixtures" / "budget")
        self._outputs = outputs

    def run(self, command, timeout_seconds):
        self.calls.append(command)
        entry = self._outputs.get(command, {})
        if entry.get("timeout"):
            return TransportResult(stdout=b"", stderr="", exit_code=124,
                                   timed_out=True)
        return TransportResult(
            stdout=str(entry.get("stdout", "")).encode("utf-8"),
            stderr=str(entry.get("stderr", "")),
            exit_code=int(entry.get("exit", 0)),
            timed_out=False)


if __name__ == "__main__":
    unittest.main()
