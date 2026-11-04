"""Tests for the fetch boundary (offline, fake opener and resolver)."""
from __future__ import annotations

import hashlib
import io
import unittest
import urllib.error

from stargate import fetch
from stargate.budgets import RunBudgets


def _public_resolve(host):
    return [(None, None, None, None, ("93.184.216.34", 443))]


def _private_resolve(host):
    return [(None, None, None, None, ("10.0.0.5", 443))]


class FakeResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = dict(headers)
        self._body = body

    def read(self, size=-1):
        if size is None or size < 0:
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:size], self._body[size:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        status, headers, body = action
        return FakeResponse(status, headers, body)


def _http_error(url, status, headers, body):
    return urllib.error.HTTPError(url, status, "x", dict(headers),
                                  io.BytesIO(body))


def _fetcher(opener, budgets=None, resolve=_public_resolve, **kwargs):
    return fetch.Fetcher(budgets=budgets, opener=opener, resolve=resolve,
                         **kwargs)


class BoundaryRefusals(unittest.TestCase):
    def test_invalid_args_refused_before_request(self):
        opener = FakeOpener([])
        getter = _fetcher(opener)
        bad = [
            ("lineage_device", {"codename": "ENCHILADA!"}),
            ("lineage_device", {"codename": "../x"}),
            ("lineage_device", {"codename": ""}),
            ("github_repo", {"owner": "a/b", "repo": "c"}),
            ("github_repo", {"owner": "..", "repo": "c"}),
            ("github_search", {"q": "x; rm -rf /"}),
            ("github_search", {"q": "   "}),
            ("hf_model", {"repo_id": "no-slash"}),
            ("hf_model", {"repo_id": "a/b/c"}),
            ("hf_model_files", {"repo_id": "a/b", "rev": ".."}),
            ("pmos_page", {"title": "a b"}),
            ("pmos_page", {"title": "a/b"}),
            ("evil_tool", {}),
            ("github_repo", "not-a-dict"),
        ]
        for tool, args in bad:
            with self.subTest(tool=tool, args=args):
                response = getter.fetch(tool, args)
                self.assertEqual(response.record.outcome, "refused")
        self.assertEqual(opener.calls, [])

    def test_redirect_to_disallowed_host_refused(self):
        opener = FakeOpener([(301, {"Location": "https://evil.example/x"},
                              b"")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "refused")
        self.assertEqual(len(opener.calls), 1)

    def test_redirect_to_private_host_refused(self):
        opener = FakeOpener([(301, {"Location": "https://internal.local/x"},
                              b"")])
        response = _fetcher(opener, resolve=_private_resolve).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "refused")

    def test_redirect_to_ip_literal_refused(self):
        opener = FakeOpener([(301, {"Location": "https://93.184.216.34/x"},
                              b"")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "refused")

    def test_redirect_with_credentials_refused(self):
        opener = FakeOpener(
            [(301, {"Location": "https://user:pw@api.github.com/"}, b"")])
        response = _fetcher(opener).fetch(
            "github_search", {"q": "enchilada"})
        self.assertEqual(response.record.outcome, "refused")

    def test_redirect_to_http_refused(self):
        opener = FakeOpener(
            [(301, {"Location": "http://api.github.com/"}, b"")])
        response = _fetcher(opener).fetch(
            "github_search", {"q": "enchilada"})
        self.assertEqual(response.record.outcome, "refused")

    def test_too_many_redirects_refused(self):
        opener = FakeOpener(
            [(301, {"Location": "https://wiki.nura.eco/wiki/A"}, b"")] * 5)
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "refused")
        self.assertEqual(len(opener.calls), 4)

    def test_check_url_direct(self):
        for url in ("https://93.184.216.34/x",
                    "https://user@api.github.com/",
                    "http://api.github.com/",
                    "https://evil.example/x",
                    "https://api.github.com/other/path"):
            with self.subTest(url=url):
                with self.assertRaises(fetch.RefusedFetch):
                    fetch.check_url(url, _public_resolve)

    def test_redirect_to_allowlisted_route_ok(self):
        opener = FakeOpener([
            (301, {"Location": "https://wiki.nura.eco/wiki/Target"}, b""),
            (200, {}, b"<title>Target</title>"),
        ])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "ok")
        self.assertEqual(response.record.url_final,
                         "https://wiki.nura.eco/wiki/Target")
        self.assertEqual(len(opener.calls), 2)


class Outcomes(unittest.TestCase):
    def test_ok_record_fields(self):
        opener = FakeOpener([(200, {}, b"hello")])
        response = _fetcher(opener).fetch(
            "lineage_device", {"codename": "enchilada"})
        record = response.record
        self.assertEqual(record.outcome, "ok")
        self.assertEqual(record.status, 200)
        self.assertEqual(record.byte_size, 5)
        self.assertEqual(record.sha256, hashlib.sha256(b"hello").hexdigest())
        self.assertTrue(record.record_id.startswith("R"))
        self.assertIn("enchilada.yml", record.url_final)
        self.assertEqual(response.body, b"hello")

    def test_404_is_ok(self):
        opener = FakeOpener([_http_error("u", 404, {}, b"not found")])
        response = _fetcher(opener).fetch(
            "lineage_device", {"codename": "nope"})
        self.assertEqual(response.record.outcome, "ok")
        self.assertEqual(response.record.status, 404)

    def test_429_is_rate_limited(self):
        opener = FakeOpener([_http_error("u", 429, {}, b"slow")])
        response = _fetcher(opener).fetch(
            "github_search", {"q": "enchilada"})
        self.assertEqual(response.record.outcome, "rate_limited")

    def test_403_with_zero_remaining_is_rate_limited(self):
        opener = FakeOpener(
            [_http_error("u", 403, {"x-ratelimit-remaining": "0"}, b"x")])
        response = _fetcher(opener).fetch(
            "github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(response.record.outcome, "rate_limited")

    def test_403_is_denied(self):
        opener = FakeOpener([_http_error("u", 403, {}, b"no")])
        response = _fetcher(opener).fetch(
            "github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(response.record.outcome, "denied")

    def test_captcha_page_denied(self):
        opener = FakeOpener([(200, {}, b"<html>prove you are human: "
                                       b"captcha here</html>")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "denied")

    def test_login_page_denied(self):
        opener = FakeOpener([(200, {}, b"<html><head><title>Log in - "
                                       b"Wiki</title></head></html>")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "denied")

    def test_login_link_in_text_is_fine(self):
        opener = FakeOpener([(200, {}, b"<p>Log in to edit this page</p>")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "ok")

    def test_500_is_error(self):
        opener = FakeOpener([_http_error("u", 500, {}, b"boom")])
        response = _fetcher(opener).fetch(
            "github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(response.record.outcome, "error")

    def test_timeout(self):
        opener = FakeOpener([TimeoutError("timed out")])
        response = _fetcher(opener).fetch(
            "github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(response.record.outcome, "timeout")

    def test_too_large(self):
        opener = FakeOpener(
            [(200, {}, b"x" * (fetch.MAX_CONTENT_BYTES + 2))])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "too_large")
        self.assertEqual(response.body, b"")

    def test_url_error_is_error(self):
        opener = FakeOpener(
            [urllib.error.URLError(OSError("dns failed"))])
        response = _fetcher(opener).fetch(
            "github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(response.record.outcome, "error")


class CacheAndBudgets(unittest.TestCase):
    def test_cache_serves_second_fetch(self):
        opener = FakeOpener([(200, {}, b"data")])
        getter = _fetcher(opener)
        first = getter.fetch("lineage_device", {"codename": "enchilada"})
        second = getter.fetch("lineage_device", {"codename": "enchilada"})
        self.assertFalse(first.record.cached)
        self.assertTrue(second.record.cached)
        self.assertEqual(second.body, b"data")
        self.assertEqual(len(opener.calls), 1)

    def test_no_cache_bypasses(self):
        opener = FakeOpener([(200, {}, b"a"), (200, {}, b"a")])
        getter = _fetcher(opener, no_cache=True)
        getter.fetch("lineage_device", {"codename": "enchilada"})
        second = getter.fetch("lineage_device", {"codename": "enchilada"})
        self.assertFalse(second.record.cached)
        self.assertEqual(len(opener.calls), 2)

    def test_stale_entry_refetches(self):
        import time as time_module

        opener = FakeOpener([(200, {}, b"fresh")])
        cache = {}
        getter = _fetcher(opener, cache=cache)
        key = ("lineage_device",
               "https://raw.githubusercontent.com/LineageOS/lineage_wiki/"
               "main/_data/devices/enchilada.yml")
        cache[key] = {"record": fetch.FetchRecord(
            record_id="R0", tool="lineage_device", url_requested=key[1],
            url_final=key[1], status=200, fetched_at="x", byte_size=4,
            sha256="x", outcome="ok"), "body": b"old", "headers": {},
            "epoch": time_module.time() - fetch.CACHE_FRESH_SECONDS - 1}
        response = getter.fetch("lineage_device", {"codename": "enchilada"})
        self.assertFalse(response.record.cached)
        self.assertEqual(response.body, b"fresh")

    def test_budgets_counted_per_hop(self):
        opener = FakeOpener([
            (301, {"Location": "https://wiki.nura.eco/wiki/Target"}, b""),
            (200, {}, b"ok"),
        ])
        wallet = RunBudgets()
        _fetcher(opener, budgets=wallet).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(wallet.http_used, 2)

    def test_github_kinds_counted(self):
        opener = FakeOpener([(200, {}, b"{}"), (200, {}, b"{}")])
        wallet = RunBudgets()
        getter = _fetcher(opener, budgets=wallet)
        getter.fetch("github_repo", {"owner": "a", "repo": "b"})
        getter.fetch("github_search", {"q": "enchilada"})
        self.assertEqual(wallet.github_core_used, 1)
        self.assertEqual(wallet.github_search_used, 1)
        self.assertEqual(wallet.http_used, 2)

    def test_github_rate_limit_latches_stop(self):
        opener = FakeOpener([_http_error("u", 429, {}, b"slow"),
                             (200, {}, b"{}")])
        wallet = RunBudgets()
        getter = _fetcher(opener, budgets=wallet)
        first = getter.fetch("github_search", {"q": "enchilada"})
        self.assertEqual(first.record.outcome, "rate_limited")
        self.assertTrue(wallet.github_stopped)
        second = getter.fetch("github_repo", {"owner": "a", "repo": "b"})
        self.assertEqual(second.record.outcome, "rate_limited")
        self.assertEqual(len(opener.calls), 1)

    def test_no_credentials_on_requests(self):
        opener = FakeOpener([(200, {}, b"{}")])
        _fetcher(opener).fetch("github_repo", {"owner": "a", "repo": "b"})
        request = opener.calls[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertTrue(request.get_header("User-agent"))
        self.assertNotIn("@", request.full_url.split("://", 1)[1].split(
            "/", 1)[0])


class DiagnoseMode(unittest.TestCase):
    def test_denied_body_is_captured(self):
        # A challenge title is denied under the Step B detector, so
        # it exercises denied-body capture.
        body = (b"<html><head><title>Just a moment...</title></head>"
                b"<body>checking your browser</body></html>")
        opener = FakeOpener([(200, {}, body)])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(response.record.outcome, "denied")
        self.assertEqual(response.body, body)
        # Same classification as the production path.
        production = _fetcher(FakeOpener([(200, {}, body)])).fetch(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(production.record.outcome, "denied")

    def test_script_marker_page_captured_as_ok(self):
        # A marker inside script markup alone is not a challenge
        # under the Step B detector; diagnose captures it as ok,
        # exactly like the production path.
        body = (b"<html><head><title>Docs</title></head><body>"
                b"<script src=\"https://www.google.com/recaptcha/api.js\">"
                b"</script>plain text</body></html>")
        opener = FakeOpener([(200, {}, body)])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(response.record.outcome, "ok")
        self.assertEqual(response.body, body)
        production = _fetcher(FakeOpener([(200, {}, body)])).fetch(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(production.record.outcome, "ok")

    def test_redirect_recorded_not_followed(self):
        opener = FakeOpener([(301, {"Location": "https://evil.example/x"},
                              b"")])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(response.record.status, 301)
        self.assertEqual(response.record.outcome, "error")
        self.assertIn("https://evil.example/x",
                      response.record.detail)
        self.assertIn("not followed", response.record.detail)
        self.assertEqual(response.record.url_final,
                         "https://evil.example/x")

    def test_cache_bypassed_for_reads_and_writes(self):
        stale_body = b"stale"
        live_body = b"<html>live</html>"
        key = ("android_page",
               "https://source.android.com/docs/core/architecture/"
               "bootloader/fastbootd")
        cached_record = fetch.FetchRecord(
            record_id="R9", tool="android_page",
            url_requested=key[1], url_final=key[1], status=200,
            fetched_at="2026-11-02T00:00:00Z",
            byte_size=len(stale_body),
            sha256=hashlib.sha256(stale_body).hexdigest(),
            outcome="ok")
        cache = {key: {"record": cached_record, "body": stale_body,
                       "headers": {}, "epoch": 9999999999.0}}
        opener = FakeOpener([(200, {}, live_body)])
        getter = _fetcher(opener, cache=cache)
        response = getter.diagnose("android_page",
                                   {"page": "fastbootd"})
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(response.body, live_body)
        self.assertEqual(cache[key]["body"], stale_body)
        fresh = _fetcher(FakeOpener([(200, {}, live_body)]))
        fresh.diagnose("android_page", {"page": "fastbootd"})
        self.assertEqual(fresh.cache, {})

    def test_allowlist_still_enforced(self):
        opener = FakeOpener([])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "elsewhere"})
        self.assertEqual(response.record.outcome, "refused")
        self.assertEqual(opener.calls, [])

    def test_same_request_as_fetch(self):
        opener = FakeOpener([(200, {}, b"ok")])
        _fetcher(opener).diagnose("android_page",
                                  {"page": "running"})
        request = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("User-agent"),
                         fetch.USER_AGENT)
        self.assertTrue(request.full_url.startswith("https://"))

    def test_size_limit_kept(self):
        body = b"x" * (fetch.MAX_CONTENT_BYTES + 10)
        opener = FakeOpener([(200, {}, body)])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(response.record.outcome, "too_large")
        self.assertEqual(len(response.body),
                         fetch.MAX_CONTENT_BYTES + 1)

    def test_failures_never_retried(self):
        opener = FakeOpener([TimeoutError("slow")])
        response = _fetcher(opener).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(response.record.outcome, "timeout")
        self.assertEqual(len(opener.calls), 1)

    def test_budgets_counted(self):
        wallet = RunBudgets()
        opener = FakeOpener([(200, {}, b"ok")])
        _fetcher(opener, budgets=wallet).diagnose(
            "android_page", {"page": "fastbootd"})
        self.assertEqual(wallet.http_used, 1)


class MarkerLocations(unittest.TestCase):
    def _locations(self, body):
        return [(hit["marker"], hit["location"])
                for hit in fetch.find_marker_matches(body)]

    def test_recaptcha_script_src_is_attribute(self):
        body = (b"<html><head><script src=\"https://www.google.com/"
                b"recaptcha/api.js\"></script></head><body>text"
                b"</body></html>")
        hits = fetch.find_marker_matches(body)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["marker"], "captcha")
        self.assertEqual(hits[0]["location"], "attribute")
        self.assertIn("recaptcha", hits[0]["before"]
                      + hits[0]["matched"] + hits[0]["after"])
        self.assertLessEqual(len(hits[0]["before"]), 80)
        self.assertLessEqual(len(hits[0]["after"]), 80)

    def test_comment_style_visible_and_title(self):
        body = (b"<html><head><title>Docs</title><style>/* just a "
                b"moment */</style></head><body><!-- captcha --><p>"
                b"just a moment</p><script>var c = 'captcha';"
                b"</script></body></html>")
        self.assertEqual(
            self._locations(body),
            [("just a moment", "style"), ("captcha", "comment"),
             ("just a moment", "visible"), ("captcha", "script")])

    def test_login_title(self):
        body = b"<html><head><title>Sign in - x</title></head></html>"
        hits = fetch.find_marker_matches(body)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["marker"], "login-title")
        self.assertEqual(hits[0]["location"], "title")

    def test_no_matches(self):
        self.assertEqual(fetch.find_marker_matches(b"<p>plain</p>"),
                         [])


class ChallengeDetector(unittest.TestCase):
    """Step B detector, tested directly (not only through fetch)."""

    def test_challenge_title_denied(self):
        body = (b"<html><head><title>Just a moment...</title></head>"
                b"<body>checking your browser</body></html>")
        self.assertTrue(fetch._is_challenge(body))

    def test_challenge_header_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body>plain</body></html>",
            {"cf-mitigated": "challenge"}))

    def test_challenge_header_case_and_space(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body>plain</body></html>",
            {"cf-mitigated": "  Challenge "}))

    def test_other_mitigated_value_is_not_a_challenge(self):
        self.assertFalse(fetch._is_challenge(
            b"<html><body>plain</body></html>",
            {"cf-mitigated": "none"}))
        self.assertFalse(fetch._is_challenge(
            b"<html><body>plain</body></html>", {}))

    def test_challenge_markup_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body><div class=\"cf-challenge-form\">x</div>"
            b"</body></html>"))
        self.assertTrue(fetch._is_challenge(
            b"<html><body><div id=\"cf-challenge\">x</div>"
            b"</body></html>"))

    def test_challenge_forms_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body><form id=\"challenge-form\" action=\"/x\">"
            b"</form></body></html>"))
        self.assertTrue(fetch._is_challenge(
            b"<html><body><form action=\"https://example.com/"
            b"cdn-cgi/challenge/abcd\"></form></body></html>"))

    def test_challenge_denied_despite_anchor(self):
        # Detection stays separate from anchor validation.
        body = (b"<html><head><title>Just a moment...</title></head>"
                b"<body><p>The bootloader must not allow the flashing "
                b"or erasing of dynamic partitions and must return an "
                b"error if these operations are attempted.</p>"
                b"</body></html>")
        self.assertTrue(fetch._is_challenge(body))

    def test_visible_captcha_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body><p>prove you are human: captcha</p>"
            b"</body></html>"))

    def test_visible_moment_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><body><p>just a moment please</p></body></html>"))

    def test_login_wall_title_denied(self):
        self.assertTrue(fetch._is_challenge(
            b"<html><head><title>Sign in</title></head>"
            b"<body>wall</body></html>"))

    def test_sign_in_nav_link_is_fine(self):
        self.assertFalse(fetch._is_challenge(
            b"<html><head><title>Docs page</title></head><body>"
            b"<nav><a href=\"/login\">Sign in</a></nav><p>text</p>"
            b"</body></html>"))

    def test_script_flag_marker_is_fine(self):
        # The Step 5 shape: a devsite JS flag carrying "captcha".
        body = (b"<html><head><title>Docs</title><script>"
                b"var flags=[\"DevPro__enable_devsite_captcha\"];"
                b"</script></head><body><p>text</p></body></html>")
        self.assertFalse(fetch._is_challenge(body))

    def test_recaptcha_script_is_fine(self):
        body = (b"<html><head><title>Docs</title>"
                b"<script src=\"https://www.google.com/recaptcha/"
                b"api.js\"></script></head><body><p>text</p>"
                b"</body></html>")
        self.assertFalse(fetch._is_challenge(body))

    def test_cloudflare_background_script_is_fine(self):
        body = (b"<html><head><title>Docs</title>"
                b"<script src=\"/cdn-cgi/challenge-platform/scripts/"
                b"jsd/api.js\"></script></head><body><p>text</p>"
                b"</body></html>")
        self.assertFalse(fetch._is_challenge(body))

    def test_ordinary_form_is_fine(self):
        self.assertFalse(fetch._is_challenge(
            b"<html><body><form action=\"/search\"><input name=\"q\">"
            b"</form></body></html>"))

    def test_cf_challenge_in_prose_is_fine(self):
        self.assertFalse(fetch._is_challenge(
            b"<html><body><p>this page mentions cf-challenge in "
            b"prose only</p></body></html>"))

    def test_header_denied_through_fetch(self):
        opener = FakeOpener(
            [(200, {"cf-mitigated": "challenge"}, b"<p>plain</p>")])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "denied")

    def test_script_flag_ok_through_fetch(self):
        body = (b"<html><head><title>Docs</title><script>"
                b"var flags=[\"DevPro__enable_devsite_captcha\"];"
                b"</script></head><body><p>text</p></body></html>")
        opener = FakeOpener([(200, {}, body)])
        response = _fetcher(opener).fetch(
            "pmos_page", {"title": "OnePlus_6_(oneplus-enchilada)"})
        self.assertEqual(response.record.outcome, "ok")
        self.assertEqual(response.body, body)


if __name__ == "__main__":
    unittest.main()
