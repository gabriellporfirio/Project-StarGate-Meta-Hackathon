"""Tests for run budgets (offline, fake clock)."""
from __future__ import annotations

import unittest

from stargate import budgets
from stargate.budgets import BudgetExhausted, RunBudgets


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class MetaBudgets(unittest.TestCase):
    def test_twelve_calls_then_stop(self):
        clock = FakeClock()
        wallet = RunBudgets(monotonic=clock)
        for _ in range(12):
            self.assertEqual(wallet.begin_meta_attempt(), 2048)
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.begin_meta_attempt()
        self.assertEqual(raised.exception.reason, "meta-calls")

    def test_peek_does_not_count(self):
        wallet = RunBudgets(monotonic=FakeClock())
        self.assertEqual(wallet.peek_meta_allowance(), 2048)
        self.assertEqual(wallet.meta_calls_used, 0)
        wallet.meta_calls_used = 12
        with self.assertRaises(BudgetExhausted):
            wallet.peek_meta_allowance()

    def test_completion_allowance_lowers(self):
        wallet = RunBudgets(monotonic=FakeClock())
        wallet.note_meta_usage({"completion_tokens": 19000})
        self.assertEqual(wallet.begin_meta_attempt(), 1000)

    def test_too_little_completion_stops(self):
        wallet = RunBudgets(monotonic=FakeClock())
        wallet.note_meta_usage({"completion_tokens": 19900})
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.begin_meta_attempt()
        self.assertEqual(raised.exception.reason, "meta-tokens")
        self.assertEqual(wallet.meta_calls_used, 0)

    def test_deadline_stops_meta(self):
        clock = FakeClock()
        wallet = RunBudgets(monotonic=clock)
        clock.now += 181.0
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.begin_meta_attempt()
        self.assertEqual(raised.exception.reason, "deadline")

    def test_input_trim_newest_first(self):
        wallet = RunBudgets(monotonic=FakeClock())
        kept = wallet.fit_sources(["b" * 20000, "a" * 20000])
        self.assertIn("b" * 20000, kept)
        self.assertLessEqual(len(kept), 24000 + 2)
        self.assertEqual(wallet.input_chars_used, len(kept.replace("\n", "")))

    def test_input_total_cap(self):
        wallet = RunBudgets(monotonic=FakeClock())
        wallet.fit_sources(["x" * 24000])
        wallet.fit_sources(["y" * 24000])
        kept = wallet.fit_sources(["z" * 24000])
        self.assertEqual(len(kept), 12000)
        self.assertEqual(wallet.input_chars_used, 60000)
        self.assertEqual(wallet.fit_sources(["w" * 10]), "")


class HttpBudgets(unittest.TestCase):
    def test_http_cap(self):
        wallet = RunBudgets(monotonic=FakeClock())
        for _ in range(30):
            wallet.check_http("other")
            wallet.note_http("other")
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.check_http("other")
        self.assertEqual(raised.exception.reason, "http")

    def test_github_caps(self):
        wallet = RunBudgets(monotonic=FakeClock())
        for _ in range(10):
            wallet.check_http("github-core")
            wallet.note_http("github-core")
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.check_http("github-core")
        self.assertEqual(raised.exception.reason, "github-core")
        for _ in range(5):
            wallet.check_http("github-search")
            wallet.note_http("github-search")
        with self.assertRaises(BudgetExhausted) as raised:
            wallet.check_http("github-search")
        self.assertEqual(raised.exception.reason, "github-search")

    def test_github_low_remaining_latches(self):
        wallet = RunBudgets(monotonic=FakeClock())
        self.assertTrue(wallet.github_usable())
        wallet.note_http("github-core", {"x-ratelimit-remaining": "2"})
        self.assertFalse(wallet.github_usable())
        self.assertTrue(wallet.github_stopped)
        wallet.note_http("github-core", {"x-ratelimit-remaining": "50"})
        self.assertFalse(wallet.github_usable())

    def test_github_remaining_tracks_minimum(self):
        wallet = RunBudgets(monotonic=FakeClock())
        wallet.note_http("github-core", {"x-ratelimit-remaining": "50"})
        wallet.note_http("github-core", {"x-ratelimit-remaining": "48"})
        self.assertEqual(wallet.github_remaining, 48)

    def test_deadline_stops_http(self):
        clock = FakeClock()
        wallet = RunBudgets(monotonic=clock)
        clock.now += 200.0
        with self.assertRaises(BudgetExhausted):
            wallet.check_http("other")


if __name__ == "__main__":
    unittest.main()
