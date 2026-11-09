"""Offers: one plain answer per service, and a refusal only when
nothing at all can run."""
from __future__ import annotations

import unittest

from stargate import offers, plan, scan, services


def _offers(name):
    models, _sha, _prov = plan.load_catalog_for_plan()
    return offers.build_offers(scan.build_profile(name)["facts"], models)


def _by_id(result):
    return {entry["id"]: entry for entry in result["services"]}


class Catalogue(unittest.TestCase):
    def test_ids_are_unique_and_core_services_exist(self):
        self.assertEqual(len(set(services.SERVICE_IDS)),
                         len(services.SERVICE_IDS))
        for wanted in ("files", "assistant", "vpn"):
            self.assertIn(wanted, services.SERVICE_IDS)

    def test_only_built_services_are_selectable(self):
        result = _offers("flagship")
        for service_id in result["selectable"]:
            self.assertTrue(services.is_built(service_id))


class Routes(unittest.TestCase):
    def test_image_first_then_takeover(self):
        self.assertEqual(_offers("flagship")["route"], "image")
        self.assertEqual(_offers("budget")["route"], "takeover")
        self.assertEqual(_offers("old32")["route"], "takeover")
        self.assertEqual(_offers("locked")["route"], "image_or_takeover")
        self.assertEqual(_offers("relic")["route"], "none")

    def test_route_choice_table(self):
        def ranked(image, takeover):
            return [{"id": "generic_system_image", "status": image},
                    {"id": "community_build", "status": "blocked"},
                    {"id": "linux", "status": "blocked"},
                    {"id": "on_top_of_android", "status": takeover}]
        self.assertEqual(offers.install_route(
            ranked("candidate", "candidate")), "image")
        self.assertEqual(offers.install_route(
            ranked("unknown", "candidate")), "image_or_takeover")
        self.assertEqual(offers.install_route(
            ranked("blocked", "candidate")), "takeover")
        self.assertEqual(offers.install_route(
            ranked("blocked", "blocked")), "none")


class Universal(unittest.TestCase):
    def test_ordinary_phones_are_never_refused(self):
        for name in ("flagship", "budget", "old32", "locked"):
            with self.subTest(phone=name):
                result = _offers(name)
                self.assertEqual(result["verdict"], "usable")
                self.assertIn("files", result["selectable"])
                self.assertIn("assistant", result["selectable"])

    def test_small_phone_gets_cloud_only_assistant(self):
        entry = _by_id(_offers("old32"))["assistant"]
        self.assertEqual(entry["status"], "limited")
        self.assertEqual(entry["mode"], "cloud_only")
        self.assertIsNone(entry["model_id"])

    def test_strong_phone_runs_the_model_itself(self):
        entry = _by_id(_offers("flagship"))["assistant"]
        self.assertEqual(entry["status"], "ready")
        self.assertEqual(entry["mode"], "on_phone")
        self.assertTrue(entry["model_id"])

    def test_image_only_service_follows_the_route(self):
        self.assertEqual(
            _by_id(_offers("flagship"))["adblock"]["status"], "ready")
        self.assertEqual(
            _by_id(_offers("budget"))["adblock"]["status"],
            "not_possible")
        self.assertEqual(
            _by_id(_offers("locked"))["adblock"]["status"], "limited")

    def test_only_a_hopeless_phone_is_refused(self):
        result = _offers("relic")
        self.assertEqual(result["verdict"], "not_usable")
        self.assertEqual(result["selectable"], [])
        self.assertTrue(result["reasons"])
        for entry in result["services"]:
            self.assertEqual(entry["status"], "not_possible")
            self.assertTrue(entry["reasons"])
            # Plain words only: no fact ids in what the user reads.
            self.assertNotIn("resources.", " ".join(entry["reasons"]))

    def test_scores_are_bounded(self):
        for name in scan.KNOWN_FIXTURES:
            for entry in _offers(name)["services"]:
                self.assertGreaterEqual(entry["score"], 0)
                self.assertLessEqual(entry["score"], 100)


if __name__ == "__main__":
    unittest.main()
