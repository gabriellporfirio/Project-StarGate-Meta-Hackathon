"""Shared harness for the guided-app server tests (light only).

Starts the real server on 127.0.0.1 with an ephemeral port (port 0: any
free port, never 8765 or 8080). Fresh builds point at a temporary
out-root. Nothing here starts an Android build tool, compresses, builds
images, or touches the network beyond loopback.
"""
from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from stargate import appserver


class AppCase(unittest.TestCase):
    """Start/stop the app server with temporary build outputs."""

    def start_app(self, **kwargs):
        kwargs.setdefault("port", 0)
        if "out_root" not in kwargs:
            tmp = tempfile.mkdtemp(prefix="app-out-")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            kwargs["out_root"] = tmp
        if "tools_dir" not in kwargs:
            tmp = tempfile.mkdtemp(prefix="app-tools-")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            kwargs["tools_dir"] = tmp
        self.app = appserver.App(**kwargs)
        self.server = appserver.AppHTTPServer(self.app)
        self.port = self.server.socket.getsockname()[1]
        self.base = "http://127.0.0.1:%d" % self.port
        thread = threading.Thread(target=self.server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        return self.app

    def request(self, method, path, body=None, headers=None):
        data = None
        all_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            all_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def raw_request(self, text: str) -> str:
        sock = socket.create_connection(("127.0.0.1", self.port),
                                        timeout=10)
        try:
            sock.sendall(text.encode("latin1"))
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        finally:
            sock.close()
        return b"".join(chunks).decode("latin1")

    def signin(self):
        """GET the start link, POST it, return authed header dict."""
        token = self.app.start_token
        status, _h, body = self.request(
            "GET", "/start?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn(b"Sign in", body)
        status, headers, body = self.request(
            "POST", "/api/signin", {"token": token},
            {"Origin": self.base})
        self.assertEqual(status, 200, body[:200])
        payload = json.loads(body.decode("utf-8"))
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("session="))
        return {"Origin": self.base, "Cookie": cookie,
                "X-CSRF-Token": payload["csrf"],
                "csrf": payload["csrf"], "cookie": cookie}

    def assert_no_cors(self, headers):
        for key in headers:
            self.assertFalse(key.lower().startswith("access-control"),
                             "CORS header sent: %s" % key)

    def assert_csp(self, headers, what="response"):
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp, what)
        self.assertIn("style-src 'self'", csp, what)
        self.assertNotIn("unsafe-inline", csp, what)
        self.assertNotIn("http://", csp, what)
        self.assertNotIn("https://", csp, what)
