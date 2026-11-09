"""Reading a real phone over the cable: read-only, explicit, and the
serial never reaches the page or the profile."""
from __future__ import annotations

import json
import subprocess
import unittest

from stargate import probe, scan
from stargate.probe import SimulatedTransport
from tests.apphelper import AppCase

SERIAL = "TESTSERIAL0001"


class FakeAdb:
    """Stands in for subprocess.run: replays a fixture as a phone."""

    def __init__(self, fixture="flagship"):
        self.sim = SimulatedTransport(scan.fixtures_dir() / fixture)
        self.calls = []

    def __call__(self, args, capture_output=True, timeout=None,
                 check=False):
        self.calls.append(list(args))
        if args[1:] == ["devices", "-l"]:
            out = ("List of devices attached\n"
                   "%s device usb:1-1 product:x model:Acme_Flagship_X "
                   "device:acme_fx transport_id:1\n"
                   "OTHER0002 unauthorized usb:1-2 transport_id:2\n"
                   % SERIAL)
            return subprocess.CompletedProcess(args, 0, out.encode(), b"")
        assert args[1:4] == ["-s", SERIAL, "shell"], args
        result = self.sim.run(args[4], timeout)
        return subprocess.CompletedProcess(
            args, result.exit_code, result.stdout,
            result.stderr.encode())


class Transport(unittest.TestCase):
    def test_refuses_without_a_chosen_phone(self):
        with self.assertRaises(probe.RealDevicesNotEnabled):
            probe.RealAdbTransport().run("getprop ro.product.model", 5)

    def test_runs_only_allowlisted_commands_as_one_argument(self):
        fake = FakeAdb()
        transport = probe.RealAdbTransport(SERIAL, runner=fake)
        result = probe.run_probe(transport, "getprop ro.product.model")
        self.assertEqual(result.stdout, b"Acme Flagship X\n")
        self.assertEqual(fake.calls, [["adb", "-s", SERIAL, "shell",
                                       "getprop ro.product.model"]])
        with self.assertRaises(probe.RefusedCommand):
            probe.run_probe(transport, "rm -rf /sdcard")
        with self.assertRaises(probe.RefusedCommand):
            transport.run("getprop", 5)
        self.assertEqual(len(fake.calls), 1)

    def test_bad_serial_is_refused_before_any_process(self):
        fake = FakeAdb()
        transport = probe.RealAdbTransport("x; reboot", runner=fake)
        with self.assertRaises(probe.RealDevicesNotEnabled):
            transport.run("getprop ro.product.model", 5)
        self.assertEqual(fake.calls, [])

    def test_missing_adb_is_a_plain_error(self):
        def missing(*_args, **_kwargs):
            raise FileNotFoundError("adb")
        devices, error = probe.list_adb_devices(runner=missing)
        self.assertEqual(devices, [])
        self.assertIn("not installed", error)

    def test_device_list_is_parsed(self):
        devices, error = probe.list_adb_devices(runner=FakeAdb())
        self.assertEqual(error, "")
        self.assertEqual([d["state"] for d in devices],
                         ["device", "unauthorized"])
        self.assertEqual(devices[0]["model"], "Acme Flagship X")

    def test_real_profile_is_live_and_holds_no_serial(self):
        transport = probe.RealAdbTransport(SERIAL, runner=FakeAdb())
        profile = scan.build_real_profile(SERIAL, transport=transport)
        self.assertIs(profile["simulated"], False)
        self.assertIsNone(profile["fixture_name"])
        self.assertNotIn(SERIAL, json.dumps(profile))
        self.assertEqual(
            profile["facts"]["identity.model"]["value"],
            "Acme Flagship X")


class InTheApp(AppCase):
    def setUp(self):
        self.fake = FakeAdb("old32")

        def devices():
            return probe.list_adb_devices(runner=self.fake)

        def device_scan(serial):
            transport = probe.RealAdbTransport(serial, runner=self.fake)
            return 0, scan.build_real_profile(
                serial, transport=transport), ""

        self.start_app(devices_fn=devices, device_scan_fn=device_scan)
        self.headers = self.signin()

    def _get(self, path):
        status, _h, body = self.request("GET", path, None, self.headers)
        return status, body

    def test_scan_needs_a_listed_phone(self):
        status, _h, body = self.request(
            "POST", "/api/connect", {"device": "d1"}, self.headers)
        self.assertEqual(status, 422, body[:200])
        self.assertEqual(self.fake.calls, [])

    def test_list_pick_scan_and_plan(self):
        status, body = self._get("/api/devices")
        self.assertEqual(status, 200)
        self.assertNotIn(SERIAL.encode(), body)
        listed = json.loads(body.decode("utf-8"))
        self.assertEqual(listed["devices"][0]["id"], "d1")
        self.assertTrue(listed["devices"][0]["ready"])
        self.assertFalse(listed["devices"][1]["ready"])
        status, _h, body = self.request(
            "POST", "/api/connect", {"device": "d1"}, self.headers)
        self.assertEqual(status, 200, body[:300])
        self.assertNotIn(SERIAL.encode(), body)
        state = json.loads(body.decode("utf-8"))["state"]
        self.assertEqual(state["profile"]["name"], "device")
        self.assertIs(state["profile"]["data"]["simulated"], False)
        self.assertEqual(state["offers"]["verdict"], "usable")
        # Every command sent to the phone was on the allowlist.
        for call in self.fake.calls:
            if call[1] == "-s":
                self.assertIn(call[4], probe.ALLOWLIST_SET)
        status, _h, body = self.request(
            "POST", "/api/selection", {"services": ["files"]},
            self.headers)
        self.assertEqual(status, 200, body[:300])
        plan = json.loads(body.decode("utf-8"))["state"]["outputs"][
            "plan"]["data"]
        self.assertEqual(plan["selection_result"], "fits")
        self.assertEqual(plan["inputs"]["profile"]["provenance"], "live")


if __name__ == "__main__":
    unittest.main()
