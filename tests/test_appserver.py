"""Platform browser rules (M6 Part 1, spec 1b) over live HTTP.

Light only: loopback server, ephemeral port, stub or real M1/M3 calls
(no Android tools, no network beyond loopback).
"""
from __future__ import annotations

import io
import json
import os
import threading
import unittest
from contextlib import redirect_stderr
from unittest import mock

from stargate import appserver

from .apphelper import AppCase

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "stargate", "app")


class BindTests(AppCase):
    def test_listens_on_loopback_only(self):
        self.start_app()
        host, _port = self.server.socket.getsockname()
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(appserver.BIND_ADDRESS, "127.0.0.1")

    def test_cli_offers_no_other_address(self):
        from stargate.cli import build_parser
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("app", help_text)
        # The app command takes --port/--forwarded-host only: no --host
        # or --address flag that could move it off loopback.
        namespace, _rest = parser.parse_known_args(["app"])
        self.assertEqual(namespace.port, 8770)
        self.assertIsNone(namespace.forwarded_host)
        self.assertFalse(hasattr(namespace, "host"))
        self.assertFalse(hasattr(namespace, "address"))


class SigninTests(AppCase):
    def test_no_session_is_401(self):
        self.start_app()
        for method, path in (
                ("GET", "/api/state"), ("GET", "/"),
                ("GET", "/app.js"), ("GET", "/app.css"),
                ("GET", "/logo.png"), ("GET", "/api/boot"),
                ("POST", "/api/connect")):
            status, headers, _body = self.request(
                method, path, {} if method == "POST" else None,
                {"Origin": self.base} if method == "POST" else None)
            self.assertEqual(status, 401, (method, path))
            self.assert_csp(headers, "%s %s" % (method, path))
            self.assert_no_cors(headers)

    def test_start_get_changes_nothing(self):
        self.start_app()
        token = self.app.start_token
        for _ in range(2):
            status, headers, body = self.request(
                "GET", "/start?token=" + token)
            self.assertEqual(status, 200)
            self.assertIn(b"Sign in", body)
            self.assert_csp(headers, "GET /start")
            self.assert_no_cors(headers)
        self.assertFalse(self.app.start_token_used)
        self.assertEqual(self.app.sessions, {})
        # The token still works afterwards.
        status, _h, _b = self.request(
            "POST", "/api/signin", {"token": token},
            {"Origin": self.base})
        self.assertEqual(status, 200)

    def test_start_token_works_once(self):
        self.start_app()
        headers = self.signin()
        self.assertIn("csrf", headers)
        status, _h, _b = self.request(
            "POST", "/api/signin", {"token": self.app.start_token},
            {"Origin": self.base})
        self.assertEqual(status, 401)

    def test_signin_rejects_bad_token(self):
        self.start_app()
        status, _h, _b = self.request(
            "POST", "/api/signin", {"token": "wrong"},
            {"Origin": self.base})
        self.assertEqual(status, 401)
        self.assertFalse(self.app.start_token_used)

    def test_signin_needs_allowed_origin(self):
        self.start_app()
        token = self.app.start_token
        status, _h, _b = self.request("POST", "/api/signin",
                                      {"token": token})
        self.assertEqual(status, 403)
        for origin in ("http://evil.example",
                       "https://127.0.0.1:%d" % self.port,
                       "http://127.0.0.1:9999",
                       "http://127.0.0.1:%d.evil.example" % self.port,
                       "http://127.0.0.1:%d/" % self.port):
            status, _h, _b = self.request(
                "POST", "/api/signin", {"token": token},
                {"Origin": origin})
            self.assertEqual(status, 403, origin)
        self.assertFalse(self.app.start_token_used)

    def test_signin_sets_session_cookie_and_csrf(self):
        self.start_app()
        status, headers, body = self.request(
            "POST", "/api/signin", {"token": self.app.start_token},
            {"Origin": self.base})
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload.get("csrf"))

    def test_form_signin_redirects_with_cookie(self):
        self.start_app()
        token = self.app.start_token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: 127.0.0.1:%d\r\n"
            "Origin: %s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n"
            "token=%s" % (self.port, self.base, len("token=" + token),
                          token))
        self.assertIn("303", raw.splitlines()[0])
        self.assertIn("Location: /", raw)
        self.assertIn("HttpOnly", raw)
        self.assertIn("Content-Security-Policy:", raw)
        self.assertTrue(self.app.start_token_used)

    def test_secure_cookie_over_forwarded_host(self):
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: fwd.example\r\n"
            "Origin: https://fwd.example\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (len(body), body))
        self.assertIn("303", raw.splitlines()[0])
        self.assertIn("; Secure", raw)


class ForwardedTests(AppCase):
    """Sign-in through the forwarded https host (Safari case, 4 Nov).

    The owner opens the https start link through the Codespaces
    forwarder. The forwarder may pass Host through or rewrite it to
    localhost:<port>; the browser must send Origin https://<forwarded>.
    Safari's plain form POST carries no Origin, so the sign-in page
    POSTs via fetch (/signin.js, served pre-session); the server rules
    are unchanged -- a missing Origin is still refused.
    """

    def test_forwarded_json_signin_ok(self):
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: fwd.example\r\n"
            "Origin: https://fwd.example\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n"
            "{\"token\": \"%s\"}" % (
                len("{\"token\": \"%s\"}" % token), token))
        self.assertIn("200", raw.splitlines()[0])
        self.assertIn("; Secure", raw)
        self.assertIn("HttpOnly", raw)
        self.assertIn("SameSite=Strict", raw)
        self.assertIn("Content-Security-Policy:", raw)
        self.assertNotIn("access-control", raw.lower())

    def test_forwarded_rewritten_host_ok(self):
        # The forwarder may rewrite Host to the loopback target while
        # the page Origin stays the forwarded https origin.
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: localhost:%d\r\n"
            "Origin: https://fwd.example\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (
                self.port, len(body), body))
        self.assertIn("303", raw.splitlines()[0])
        self.assertIn("HttpOnly", raw)

    def test_forwarded_missing_origin_still_refused(self):
        # The Safari fix is client-side (fetch always sends Origin);
        # the server still refuses a missing Origin. No weakening.
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: fwd.example\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (len(body), body))
        self.assertIn("403", raw.splitlines()[0])
        self.assertFalse(self.app.start_token_used)

    def test_signin_script_served_without_session(self):
        self.start_app()
        status, headers, body = self.request("GET", "/signin.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"),
                         "application/javascript")
        self.assert_csp(headers, "GET /signin.js")
        self.assert_no_cors(headers)
        text = body.decode("utf-8")
        self.assertIn("fetch", text)
        self.assertNotIn("innerHTML", text)
        page = self.request("GET", "/start?token=x")[2].decode("utf-8")
        self.assertIn('<script src="/signin.js"></script>', page)

    def test_refusal_logs_reason_without_secrets(self):
        # Fixed reason strings only (P5b): never the received values,
        # never token or cookie values.
        self.start_app()
        token = self.app.start_token
        stream = io.StringIO()
        with redirect_stderr(stream):
            self.raw_request(
                "POST /api/signin HTTP/1.1\r\n"
                "Host: 127.0.0.1:%d\r\n"
                "Origin: http://evil.example\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: %d\r\n"
                "Connection: close\r\n\r\n"
                "{\"token\": \"%s\"}" % (
                    self.port,
                    len("{\"token\": \"%s\"}" % token), token))
            self.raw_request(
                "GET /api/state HTTP/1.1\r\n"
                "Host: evil.example\r\n"
                "Connection: close\r\n\r\n")
        log = stream.getvalue()
        self.assertIn("app: refused POST /api/signin: origin mismatch",
                      log)
        self.assertIn("app: refused GET /api/state: host mismatch", log)
        self.assertNotIn("evil.example", log)
        self.assertNotIn(token, log)
        self.assertNotIn("session=", log)

    def test_forwarded_only_configured_origin_accepted(self):
        # Only the configured forwarded origin counts: any other https
        # origin is refused, and proxy headers are never trusted.
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: fwd.example\r\n"
            "Origin: https://other.example\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (len(body), body))
        self.assertIn("403", raw.splitlines()[0])
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: evil.example\r\n"
            "X-Forwarded-Host: fwd.example\r\n"
            "Origin: https://fwd.example\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (len(body), body))
        self.assertIn("403", raw.splitlines()[0])
        self.assertFalse(self.app.start_token_used)

    def test_forwarded_rewritten_origin_accepted(self):
        # Owner live test: the forwarder rewrites the page Origin to
        # exactly https://localhost:<own port>. Accept only that.
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: localhost:%d\r\n"
            "Origin: https://localhost:%d\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (
                self.port, self.port, len(body), body))
        self.assertIn("303", raw.splitlines()[0])
        self.assertTrue(self.app.start_token_used)

    def test_rewritten_origin_narrow(self):
        # Another localhost port, plain http, and no forwarded host:
        # all refused.
        self.start_app(forwarded_host="fwd.example")
        token = self.app.start_token
        body = "token=" + token
        # (http://localhost:<own port> stays allowed: it is the
        # pre-existing direct-loopback origin, not the rewritten one.)
        for origin in ("https://localhost:%d" % (self.port + 1),
                       "http://localhost:%d" % (self.port + 1),
                       "https://127.0.0.1:%d" % self.port):
            raw = self.raw_request(
                "POST /api/signin HTTP/1.1\r\n"
                "Host: localhost:%d\r\n"
                "Origin: %s\r\n"
                "Content-Type: application/x-www-form-urlencoded\r\n"
                "Content-Length: %d\r\n"
                "Connection: close\r\n\r\n%s" % (
                    self.port, origin, len(body), body))
            self.assertIn("403", raw.splitlines()[0], origin)
        self.assertFalse(self.app.start_token_used)
        # Without --forwarded-host the rewritten origin is refused too.
        self.start_app()
        token = self.app.start_token
        body = "token=" + token
        raw = self.raw_request(
            "POST /api/signin HTTP/1.1\r\n"
            "Host: localhost:%d\r\n"
            "Origin: https://localhost:%d\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (
                self.port, self.port, len(body), body))
        self.assertIn("403", raw.splitlines()[0])


class RuleTests(AppCase):
    def test_csrf_missing_or_wrong_refused(self):
        self.start_app()
        headers = self.signin()
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "flagship"},
            {"Origin": self.base, "Cookie": headers["cookie"]})
        self.assertEqual(status, 403)
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "flagship"},
            {"Origin": self.base, "Cookie": headers["cookie"],
             "X-CSRF-Token": "wrong"})
        self.assertEqual(status, 403)

    def test_foreign_or_missing_origin_refused(self):
        self.start_app()
        headers = self.signin()
        good = {"Cookie": headers["cookie"],
                "X-CSRF-Token": headers["csrf"]}
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "flagship"}, dict(good))
        self.assertEqual(status, 403)
        for origin in ("http://evil.example",
                       "https://127.0.0.1:%d" % self.port,
                       "http://localhost:1"):
            attempt = dict(good, Origin=origin)
            status, _h, _b = self.request(
                "POST", "/api/connect", {"profile": "flagship"},
                attempt)
            self.assertEqual(status, 403, origin)

    def test_wrong_host_refused(self):
        self.start_app()
        headers = self.signin()
        cookie = headers["cookie"]
        for host in ("evil.example", "127.0.0.1",
                     "127.0.0.1:9999", "localhost",
                     "127.0.0.1:%d.evil.example" % self.port):
            raw = self.raw_request(
                "GET /api/state HTTP/1.1\r\n"
                "Host: %s\r\n"
                "Cookie: %s\r\n"
                "Connection: close\r\n\r\n" % (host, cookie))
            self.assertIn("403", raw.splitlines()[0], host)
            self.assertIn("Content-Security-Policy:", raw)
        raw = self.raw_request(
            "GET /api/state HTTP/1.1\r\n"
            "Host: localhost:%d\r\n"
            "Cookie: %s\r\n"
            "Connection: close\r\n\r\n" % (self.port, cookie))
        self.assertIn("200", raw.splitlines()[0])

    def test_host_checked_before_session(self):
        self.start_app()
        raw = self.raw_request(
            "GET /api/state HTTP/1.1\r\n"
            "Host: evil.example\r\n"
            "Connection: close\r\n\r\n")
        self.assertIn("403", raw.splitlines()[0])

    def test_no_cors_headers_on_any_response(self):
        self.start_app()
        headers = self.signin()
        cases = [("GET", "/api/state", None, {"Cookie": headers["cookie"]}),
                 ("GET", "/", None, {"Cookie": headers["cookie"]}),
                 ("GET", "/app.css", None, {"Cookie": headers["cookie"]}),
                 ("GET", "/no-such-path", None,
                  {"Cookie": headers["cookie"]}),
                 ("POST", "/api/questions",
                  {"answers": {"q1": "both", "q2": "no"}}, headers)]
        for method, path, body, hdrs in cases:
            _s, resp_headers, _b = self.request(method, path, body, hdrs)
            self.assert_no_cors(resp_headers)
        raw = self.raw_request("OPTIONS /api/state HTTP/1.1\r\n"
                               "Host: 127.0.0.1:%d\r\n"
                               "Connection: close\r\n\r\n" % self.port)
        self.assertNotIn("access-control", raw.lower())

    def test_csp_on_every_page(self):
        self.start_app()
        headers = self.signin()
        cookie = {"Cookie": headers["cookie"]}
        for method, path, body, hdrs in (
                ("GET", "/start?token=x", None, None),
                ("GET", "/", None, cookie),
                ("GET", "/app.js", None, cookie),
                ("GET", "/api/state", None, cookie),
                ("GET", "/missing", None, cookie),
                ("POST", "/api/questions",
                 {"answers": {"q1": "files", "q2": "yes"}}, headers)):
            _s, resp_headers, _b = self.request(method, path, body, hdrs)
            self.assert_csp(resp_headers, "%s %s" % (method, path))

    def test_no_get_changes_state(self):
        self.start_app()
        headers = self.signin()
        cookie = {"Cookie": headers["cookie"]}
        before = self.request("GET", "/api/state", None, cookie)[2]
        for path in ("/", "/app.js", "/app.css", "/logo.png",
                     "/api/state", "/api/boot", "/api/jobs/job-9",
                     "/start?token=x", "/missing"):
            self.request("GET", path, None, cookie)
        after = self.request("GET", "/api/state", None, cookie)[2]
        self.assertEqual(json.loads(before.decode("utf-8")),
                         json.loads(after.decode("utf-8")))
        self.assertEqual(len(self.app.sessions), 1)

    def test_body_limit_64kb(self):
        self.start_app()
        headers = self.signin()
        big = "x" * (64 * 1024 + 1)
        status, resp_headers, _b = self.request(
            "POST", "/api/connect", {"profile": "flagship",
                                     "pad": big}, headers)
        self.assertEqual(status, 413)
        self.assert_csp(resp_headers, "413")

    def test_signout_ends_session(self):
        self.start_app()
        headers = self.signin()
        status, _h, _b = self.request("POST", "/api/signout", {},
                                      headers)
        self.assertEqual(status, 200)
        status, _h, _b = self.request(
            "GET", "/api/state", None, {"Cookie": headers["cookie"]})
        self.assertEqual(status, 401)

    def test_one_run_per_step(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_scan(_name):
            entered.set()
            self.assertTrue(release.wait(timeout=10))
            return 0, {"simulated": True, "facts": {}}, ""

        self.start_app(scan_fn=slow_scan)
        headers = self.signin()
        results = []

        def attempt():
            results.append(self.request(
                "POST", "/api/connect", {"profile": "flagship"},
                headers)[0])

        first = threading.Thread(target=attempt)
        first.start()
        self.assertTrue(entered.wait(timeout=10))
        second = threading.Thread(target=attempt)
        second.start()
        second.join(timeout=10)
        release.set()
        first.join(timeout=10)
        self.assertEqual(sorted(results), [200, 429])

    def test_start_link_never_logged(self):
        self.start_app()
        token = self.app.start_token
        stream = io.StringIO()
        with redirect_stderr(stream):
            self.request("GET", "/start?token=" + token)
            self.request("POST", "/api/signin", {"token": token},
                         {"Origin": self.base})
        self.assertNotIn(token, stream.getvalue())


class SafeRenderingTests(AppCase):
    def _read(self, name):
        with open(os.path.join(APP_DIR, name), encoding="utf-8") as handle:
            return handle.read()

    def test_client_uses_text_not_html(self):
        for name in ("app.js", "signin.js"):
            script = self._read(name)
            for banned in ("innerHTML", "outerHTML",
                           "insertAdjacentHTML", "document.write"):
                self.assertNotIn(banned, script, name)

    def test_pages_have_no_inline_scripts_or_handlers(self):
        # Own external scripts are allowed by the CSP and spec 1b; only
        # inline scripts and handlers are banned. Each page keeps exactly
        # its one external script tag.
        allowed = {"index.html": '<script src="/app.js"></script>',
                   "signin.html": '<script src="/signin.js"></script>'}
        for name, tag in allowed.items():
            page = self._read(name)
            self.assertEqual(page.count("<script"), 1, name)
            self.assertIn(tag, page)
            rest = page.replace(tag, "")
            self.assertNotIn("<script", rest)
            self.assertNotIn("onclick", rest.lower())
            self.assertNotIn("onerror", rest.lower())
            self.assertNotIn("javascript:", rest.lower())

    def test_model_text_served_raw_for_text_rendering(self):
        evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"

        def evil_plan(_profile, _services):
            return 0, {"selection_result": "fits",
                       "build_allowed": "emulator_only",
                       "fit": {"reasons": [], "checks": [],
                               "model_choice": {}},
                       "strategies": [], "provenance": "synthetic",
                       "notes": evil}, ""

        self.start_app(plan_fn=evil_plan)
        headers = self.signin()
        status, _h, _b = self.request(
            "POST", "/api/connect", {"profile": "flagship"}, headers)
        self.assertEqual(status, 200)
        status, _h, body = self.request(
            "POST", "/api/selection", {"services": ["files"]}, headers)
        self.assertEqual(status, 200)
        state = json.loads(body.decode("utf-8"))["state"]
        notes = state["outputs"]["plan"]["data"]["notes"]
        self.assertEqual(notes, evil)


if __name__ == "__main__":
    unittest.main()
