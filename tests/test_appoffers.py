"""The app's offers: what each connected phone is shown, and what
may be selected."""
from __future__ import annotations

import json

from tests.apphelper import AppCase


class OffersInTheApp(AppCase):
    def setUp(self):
        self.start_app()
        self.headers = self.signin()

    def _connect(self, name):
        status, _h, body = self.request(
            "POST", "/api/connect", {"profile": name}, self.headers)
        self.assertEqual(status, 200, body[:300])
        return json.loads(body.decode("utf-8"))["state"]

    def test_no_offers_before_a_phone_is_connected(self):
        status, _h, body = self.request("GET", "/api/state", None,
                                        self.headers)
        self.assertEqual(status, 200)
        state = json.loads(body.decode("utf-8"))["state"]
        self.assertIsNone(state["offers"])
        self.assertIn("vpn", [e["id"] for e in state["catalogue"]])
        self.assertIn("relic", state["fixtures"])

    def test_small_phone_is_offered_services_not_refused(self):
        state = self._connect("old32")
        offers = state["offers"]
        self.assertEqual(offers["verdict"], "usable")
        self.assertEqual(offers["route"], "takeover")
        by_id = {entry["id"]: entry for entry in offers["services"]}
        self.assertEqual(by_id["assistant"]["mode"], "cloud_only")
        status, _h, body = self.request(
            "POST", "/api/selection",
            {"services": ["files", "assistant"]}, self.headers)
        self.assertEqual(status, 200, body[:300])
        plan = json.loads(body.decode("utf-8"))["state"]["outputs"][
            "plan"]["data"]
        self.assertEqual(plan["selection_result"], "fits")
        self.assertEqual(plan["fit"]["model_choice"]["status"],
                         "cloud_only")

    def test_hopeless_phone_is_refused_with_plain_reasons(self):
        offers = self._connect("relic")["offers"]
        self.assertEqual(offers["verdict"], "not_usable")
        self.assertEqual(offers["selectable"], [])
        self.assertTrue(offers["reasons"])

    def test_preselection_only_holds_selectable_services(self):
        self._connect("flagship")
        status, _h, body = self.request(
            "POST", "/api/questions",
            {"answers": {"q1": "both", "q2": "no"}}, self.headers)
        self.assertEqual(status, 200)
        state = json.loads(body.decode("utf-8"))["state"]
        self.assertEqual(sorted(state["preselected"]),
                         ["assistant", "files", "vault"])

    def test_services_that_are_not_built_cannot_be_selected(self):
        self._connect("flagship")
        status, _h, body = self.request(
            "POST", "/api/selection", {"services": ["files", "vpn"]},
            self.headers)
        self.assertEqual(status, 422)
        self.assertIn(b"not ready to build yet: vpn", body)

    def test_fonts_are_served_from_the_app_itself(self):
        cookie = {"Cookie": self.headers["Cookie"]} \
            if "Cookie" in self.headers else \
            {"Cookie": self.headers["cookie"]}
        for name in ("space-grotesk", "jetbrains-mono"):
            status, headers, body = self.request(
                "GET", "/fonts/%s.woff2" % name, None, cookie)
            self.assertEqual(status, 200)
            self.assertEqual(body[:4], b"wOF2")
            self.assertIn("font-src 'self'",
                          headers.get("Content-Security-Policy", ""))


if __name__ == "__main__":
    import unittest
    unittest.main()
