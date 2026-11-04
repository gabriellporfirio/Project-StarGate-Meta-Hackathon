"""Offline tests for the M3 strategy engine (spec section 3)."""
from __future__ import annotations

import itertools
import unittest

from stargate import strategy


def _measured(value):
    return {"status": "measured", "value": value, "unit": None,
            "evidence": []}


def _lock(value):
    return {"status": "inferred", "value": value, "unit": None,
            "inference": {"rule_id": "R-LOCK-FROM-VBS",
                          "inputs": ["boot.verified_boot_state"]}}


def _unknown_fact(reason="property_missing"):
    return {"status": "unknown", "value": None, "unit": None,
            "unknown_reason": reason}


def _profile(locked=False, supported=True, allowed=True, treble=True,
             abis=("arm64-v8a",), sdk=34):
    facts = {
        "boot.bootloader_locked": _lock(locked),
        "boot.oem_unlock_supported": _measured(supported),
        "boot.oem_unlock_allowed": _measured(allowed),
        "boot.treble_enabled": _measured(treble),
        "resources.cpu_abis": _measured(list(abis)),
        "identity.sdk_level": _measured(sdk),
    }
    return facts


def _research(support="current", tree=True, pmos="listed"):
    repos = [{"full_name": "LineageOS/android_device_x"}] if tree else []
    return {
        "lineage.support": {"name": "lineage.support", "status": "found",
                            "value": {"support": support, "versions": [],
                                      "maintainers": []}},
        "repos.device_tree": {"name": "repos.device_tree",
                              "status": "found",
                              "value": {"repos": repos}},
        "pmos.status": {"name": "pmos.status", "status": "found",
                        "value": {"status": pmos, "title": None,
                                  "url": None}},
    }


def _by_id(results):
    return {entry["id"]: entry for entry in results}


class UnlockState(unittest.TestCase):
    def test_conflicting_lock_is_unknown(self):
        facts = _profile(locked=True)
        facts["boot.bootloader_locked"] = {"status": "conflicting",
                                           "value": None, "unit": None,
                                           "candidates": []}
        self.assertEqual(strategy.unlock_state(facts), "unknown")

    def test_unlocked_despite_no_support(self):
        facts = _profile(locked=False, supported=False)
        self.assertEqual(strategy.unlock_state(facts), "already_unlocked")

    def test_allowed_now(self):
        facts = _profile(locked=True, supported=True, allowed=True)
        self.assertEqual(strategy.unlock_state(facts), "allowed_now")

    def test_not_allowed_now(self):
        facts = _profile(locked=True, supported=True, allowed=False)
        self.assertEqual(strategy.unlock_state(facts), "not_allowed_now")

    def test_no_official_path(self):
        facts = _profile(locked=True, supported=False, allowed=False)
        self.assertEqual(strategy.unlock_state(facts), "no_official_path")
        facts = _profile(locked=True, supported=False, allowed=True)
        self.assertEqual(strategy.unlock_state(facts), "no_official_path")
        facts = _profile(supported=False)
        facts["boot.bootloader_locked"] = _unknown_fact("deferred")
        self.assertEqual(strategy.unlock_state(facts), "no_official_path")

    def test_missing_facts_give_unknown(self):
        facts = _profile(locked=True)
        facts["boot.oem_unlock_allowed"] = _unknown_fact("permission_denied")
        self.assertEqual(strategy.unlock_state(facts), "unknown")
        self.assertEqual(strategy.unlock_state({}), "unknown")

    def test_exactly_one_row_applies(self):
        states = set()
        for locked, supported, allowed in itertools.product(
                (True, False, None), repeat=3):
            facts = {}
            if locked is None:
                facts["boot.bootloader_locked"] = _unknown_fact("deferred")
            else:
                facts["boot.bootloader_locked"] = _lock(locked)
            for fact_id, value in (
                    ("boot.oem_unlock_supported", supported),
                    ("boot.oem_unlock_allowed", allowed)):
                if value is None:
                    facts[fact_id] = _unknown_fact()
                else:
                    facts[fact_id] = _measured(value)
            states.add(strategy.unlock_state(facts))
        self.assertTrue(states <= {"already_unlocked", "allowed_now",
                                   "not_allowed_now", "no_official_path",
                                   "unknown"})
        self.assertEqual(len(states), 5)


class CommunityBuild(unittest.TestCase):
    def test_candidate(self):
        results = _by_id(strategy.evaluate(_profile(), _research()))
        entry = results["community_build"]
        self.assertEqual(entry["status"], "candidate")
        for fact_id in ("lineage.support", "repos.device_tree",
                        "boot.bootloader_locked"):
            self.assertIn(fact_id, entry["met"])
        self.assertTrue(entry["open_checks"])
        self.assertTrue(entry["risks"])
        self.assertTrue(entry["signing"])

    def test_blocked_current(self):
        facts = _profile(locked=True, supported=True, allowed=False)
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "community_build"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["blocker_kind"], "current")
        self.assertEqual(entry["blocker"]["fact"],
                         "boot.oem_unlock_allowed")
        self.assertIs(entry["blocker"]["value"], False)

    def test_blocked_no_official_path(self):
        facts = _profile(locked=True, supported=False)
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "community_build"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["blocker_kind"],
                         "no_official_path")

    def test_historical_and_not_listed_are_unknown(self):
        for support in ("historical_only", "not_listed"):
            with self.subTest(support=support):
                entry = _by_id(strategy.evaluate(
                    _profile(), _research(support=support)))[
                        "community_build"]
                self.assertEqual(entry["status"], "unknown")
                self.assertIn("lineage.support",
                              [item["fact"] for item in entry["missing"]])

    def test_no_research_file_is_unknown(self):
        entry = _by_id(strategy.evaluate(_profile(), None))[
            "community_build"]
        self.assertEqual(entry["status"], "unknown")
        reasons = [item["reason"] for item in entry["missing"]]
        self.assertTrue(reasons)
        self.assertTrue(all(reason == "no research file"
                            for reason in reasons))

    def test_no_established_tree_is_unknown(self):
        entry = _by_id(strategy.evaluate(
            _profile(), _research(tree=False)))["community_build"]
        self.assertEqual(entry["status"], "unknown")
        self.assertIn("repos.device_tree",
                      [item["fact"] for item in entry["missing"]])


class GenericSystemImage(unittest.TestCase):
    def test_candidate(self):
        entry = _by_id(strategy.evaluate(_profile(), _research()))[
            "generic_system_image"]
        self.assertEqual(entry["status"], "candidate")
        self.assertTrue(entry["open_checks"])

    def test_treble_false_blocks(self):
        facts = _profile(locked=True, treble=False)
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "generic_system_image"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["fact"], "boot.treble_enabled")
        self.assertIs(entry["blocker"]["value"], False)

    def test_unlock_blocked_blocks(self):
        facts = _profile(locked=True, supported=True, allowed=False)
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "generic_system_image"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["blocker_kind"], "current")

    def test_unknown_abi_is_unknown(self):
        facts = _profile()
        facts["resources.cpu_abis"] = _unknown_fact("property_missing")
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "generic_system_image"]
        self.assertEqual(entry["status"], "unknown")
        self.assertIn("resources.cpu_abis",
                      [item["fact"] for item in entry["missing"]])

    def test_abi_without_gsi_overlap_is_unknown(self):
        facts = _profile(abis=("armeabi-v7a",))
        entry = _by_id(strategy.evaluate(facts, _research()))[
            "generic_system_image"]
        self.assertEqual(entry["status"], "unknown")


class LinuxStrategy(unittest.TestCase):
    def test_candidate(self):
        entry = _by_id(strategy.evaluate(_profile(), _research()))["linux"]
        self.assertEqual(entry["status"], "candidate")
        self.assertTrue(entry["open_checks"])

    def test_unlock_blocked_blocks(self):
        facts = _profile(locked=True, supported=False)
        entry = _by_id(strategy.evaluate(facts, _research()))["linux"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["blocker_kind"],
                         "no_official_path")

    def test_not_found_is_unknown(self):
        entry = _by_id(strategy.evaluate(
            _profile(), _research(pmos="not_found")))["linux"]
        self.assertEqual(entry["status"], "unknown")

    def test_no_research_file_is_unknown(self):
        entry = _by_id(strategy.evaluate(_profile(), None))["linux"]
        self.assertEqual(entry["status"], "unknown")


class StockRebuild(unittest.TestCase):
    def test_always_not_covered(self):
        for facts in (_profile(), _profile(locked=True, treble=False,
                                           abis=("armeabi-v7a",), sdk=26)):
            with self.subTest(facts=facts):
                for research in (_research(), None):
                    entry = _by_id(strategy.evaluate(facts, research))[
                        "stock_rebuild"]
                    self.assertEqual(entry["status"], "not_covered")


class OnTopOfAndroid(unittest.TestCase):
    def test_candidate(self):
        entry = _by_id(strategy.evaluate(_profile(), None))[
            "on_top_of_android"]
        self.assertEqual(entry["status"], "candidate")
        self.assertTrue(entry["open_checks"])

    def test_sdk_boundary(self):
        facts = _profile(sdk=26, abis=("armeabi-v7a",))
        entry = _by_id(strategy.evaluate(facts, None))["on_top_of_android"]
        self.assertEqual(entry["status"], "candidate")
        facts = _profile(sdk=25)
        entry = _by_id(strategy.evaluate(facts, None))["on_top_of_android"]
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["blocker"]["fact"], "identity.sdk_level")
        self.assertEqual(entry["blocker"]["value"], 25)

    def test_unknown_sdk_is_unknown(self):
        facts = _profile()
        facts["identity.sdk_level"] = _unknown_fact("property_missing")
        entry = _by_id(strategy.evaluate(facts, None))["on_top_of_android"]
        self.assertEqual(entry["status"], "unknown")

    def test_needs_no_unlock(self):
        facts = _profile(locked=True, supported=False)
        entry = _by_id(strategy.evaluate(facts, None))["on_top_of_android"]
        self.assertEqual(entry["status"], "candidate")


class Ranking(unittest.TestCase):
    def test_ranked_order(self):
        facts = _profile(treble=False)
        research = _research(pmos="not_found")
        ranked = strategy.evaluate(facts, research)
        self.assertEqual([entry["id"] for entry in ranked],
                         ["community_build", "on_top_of_android", "linux",
                          "generic_system_image", "stock_rebuild"])
        self.assertEqual([entry["status"] for entry in ranked],
                         ["candidate", "candidate", "unknown", "blocked",
                          "not_covered"])

    def test_blocked_kind_order(self):
        def blocked(kind, strategy_id):
            return {"id": strategy_id, "status": "blocked",
                    "blocker": {"fact": "f", "value": 0,
                                "blocker_kind": kind}}
        entries = [blocked("unsupported", "generic_system_image"),
                   blocked("no_official_path", "linux"),
                   blocked("current", "community_build")]
        ordered = sorted(entries, key=strategy._rank_key)
        self.assertEqual([entry["blocker"]["blocker_kind"]
                          for entry in ordered],
                         ["current", "no_official_path", "unsupported"])


if __name__ == "__main__":
    unittest.main()
