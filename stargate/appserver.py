"""The guided app's local web server (M6 Part 1, spec 1 and 1b).

`stargate app` serves plain HTML, CSS and JavaScript from the
``stargate/app/`` package directory on 127.0.0.1 only (never another
address), standard library only. The owner opens it through the
codespace's private forwarded port.

Browser rules (spec 1b): one-time start link in the terminal; GET shows
only a Sign in button and changes nothing; the sign-in POST (the one
request allowed without a session, still needing an allowed Origin)
sets an HttpOnly, SameSite=Strict session cookie (Secure over the
forwarded https host) plus a CSRF token, and consumes the start token.
Every other request without a session is 401. Every change is a POST
with the CSRF header; GET never changes anything. Host and Origin are
checked as full exact values; no CORS headers are ever sent; every
response carries a strict Content-Security-Policy; bodies are at most
64 KB; one run at a time per step.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import appsession as session_module
from .build.image import build_id_for_plan
from .fit import SERVICES as KNOWN_SERVICES
from .plan import EXIT_REFUSED, run_plan
from .profile import validate_profile
from .scan import KNOWN_FIXTURES, build_profile

DEFAULT_PORT = 8770
BIND_ADDRESS = "127.0.0.1"
MAX_BODY = 64 * 1024

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self'; connect-src 'self'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")

SESSION_COOKIE = "session"
CSRF_HEADER = "X-CSRF-Token"

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")

STATIC_FILES = {
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "application/javascript"),
    "/logo.png": ("logo-light.png", "image/png"),
    # Browsers fetch this on their own; same mark, same session gate.
    "/favicon.ico": ("logo-light.png", "image/png"),
}

BOOT_TARGET = "Android Emulator (this demo)"
NO_RESEARCH_MESSAGE = "No recorded research for this phone yet"
M5_MESSAGE = "Install plan: added in Part 2"
LIVE_MESSAGE = "Live runs: not in this part"
PHONE_LINK_MESSAGE = "arrives when Link is joined in (platform Part 2)"
# Saved M5 run (P5): shown only for the budget profile, only when the
# copied file passes integrity and its recorded inputs match the app.
M5_SAVED_REPLAY = "budget-replay-v3.json"
M5_SAVED_PLAN = "budget-plan-v3.json"
M5_SAVED_TITLE = "Saved run (4 Nov), replayed offline"
M5_SAVED_LIMIT = "Install-routing proposal: not a validated install procedure"
M5_SAVED_UNAVAILABLE = "Saved run unavailable"
M5_SAVED_OUTDATED = "Saved run is out of date for this phone or selection"

QUESTIONS = (
    {"id": "q1",
     "text": "What do you want the phone to do?",
     "options": (("files", "Keep my files"),
                 ("assistant", "Answer questions for me"),
                 ("both", "Both"))},
    {"id": "q2",
     "text": "Show technical details on each screen?",
     "options": (("yes", "Yes"), ("no", "No"))},
)


def real_scan(profile_name: str):
    """Run M1 scan in-process. Returns (exit, profile_or_None, error)."""
    if profile_name not in KNOWN_FIXTURES:
        return 2, None, "unknown simulated device %r" % (profile_name,)
    profile = build_profile(profile_name)
    errors = validate_profile(profile)
    if errors:
        return 6, None, "built profile is invalid: %s" % errors[0]
    return 0, profile, ""


def real_plan(profile_name: str, services: list):
    """Run M3 plan in-process, no research, no live calls.

    Returns (exit, plan_or_None, error). A refused plan (exit 7) still
    returns its plan: refusals are shown with their reasons.
    """
    exit_code, stdout_text, stderr_text = run_plan(
        sim=profile_name, services_text=",".join(services),
        json_only=True)
    if exit_code not in (0, EXIT_REFUSED):
        return exit_code, None, (stderr_text or stdout_text).strip() \
            or ("plan failed (exit %d)" % exit_code)
    try:
        plan = json.loads(stdout_text)
    except ValueError:
        return 6, None, "plan output is not JSON"
    return exit_code, plan, ""


def preselected_services(answers: dict) -> list:
    """Services the answers pre-select (shown checked, user confirms)."""
    want = answers.get("q1")
    if want == "files":
        return ["files"]
    if want == "assistant":
        return ["assistant"]
    return ["assistant", "files"]


def default_out_root() -> str:
    """Fresh-build outputs live here (never the frozen M4 outputs)."""
    return os.environ.get("STARGATE_APP_OUT",
                          "/workspaces/m6-tools/out")


def default_tools_dir() -> str:
    """APK intermediates and key live here (never /workspaces/m4a-tools)."""
    return os.environ.get("STARGATE_APP_TOOLS",
                          "/workspaces/m6-tools/apptools")


def default_helper() -> str:
    """The Ahead heavy-step helper every build runs through."""
    return os.environ.get("STARGATE_APP_HELPER",
                          "/workspaces/m6-tools/ahead-heavy.sh")


def default_m5_saved_dir() -> str:
    """Copied M5 saved-run files plus SOURCES.txt (read-only)."""
    return os.environ.get("STARGATE_APP_M5_SAVED",
                          "/workspaces/m6-tools/m5-saved")


class App:
    """Server state: sessions, records, per-step runs, build settings."""

    def __init__(self, port: int, forwarded_host=None, out_root=None,
                 tools_dir=None, helper_path=None, scan_fn=None,
                 plan_fn=None, build_cmd_factory=None,
                 m5_saved_dir=None):
        self.port = port
        self.forwarded_host = forwarded_host
        self.out_root = out_root if out_root is not None \
            else default_out_root()
        self.tools_dir = tools_dir if tools_dir is not None \
            else default_tools_dir()
        self.helper_path = helper_path if helper_path is not None \
            else default_helper()
        self.m5_saved_dir = m5_saved_dir if m5_saved_dir is not None \
            else default_m5_saved_dir()
        self.scan_fn = scan_fn if scan_fn is not None else real_scan
        self.plan_fn = plan_fn if plan_fn is not None else real_plan
        self.build_cmd_factory = build_cmd_factory
        self.start_token = secrets.token_urlsafe(32)
        self.start_token_used = False
        self.sessions = {}
        self.running = {}
        self.lock = threading.RLock()

    def expected_hosts(self) -> set:
        """Exact Host values accepted (spec 1b)."""
        hosts = {"127.0.0.1:%d" % self.port,
                 "localhost:%d" % self.port}
        if self.forwarded_host:
            hosts.add(self.forwarded_host)
        return hosts

    def expected_origins(self) -> set:
        """Exact full origins accepted on POST (spec 1b)."""
        origins = {"http://127.0.0.1:%d" % self.port,
                   "http://localhost:%d" % self.port}
        if self.forwarded_host:
            origins.add("https://%s" % self.forwarded_host)
            # The Codespaces forwarder rewrites the page Origin to the
            # loopback target with the outside scheme (owner live test,
            # 4 Nov: 'https://localhost:8771'). Accept exactly that one:
            # https only, localhost only, this server's own port only.
            origins.add("https://localhost:%d" % self.port)
        return origins

    def _load_m5_file(self, name: str):
        """Integrity-checked copied JSON file, or None.

        Reads only our own copies plus SOURCES.txt (no M5 code, no
        network). Returns (sha256 hex, parsed dict). None when anything
        is missing, unparsable, or the hash differs. Never raises.
        """
        try:
            with open(os.path.join(self.m5_saved_dir, "SOURCES.txt"),
                      encoding="utf-8") as handle:
                sources = handle.read()
            expected = None
            for line in sources.splitlines():
                line = line.strip()
                if not line.startswith("sha256:"):
                    continue
                parts = line.split()
                if len(parts) >= 4 and parts[2] == "file:" and \
                        parts[3] == name:
                    expected = parts[1]
            if not expected:
                return None
            with open(os.path.join(self.m5_saved_dir, name),
                      "rb") as handle:
                raw = handle.read()
            if session_module.sha256_hex(raw) != expected.lower():
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                return None
            return expected.lower(), parsed
        except (OSError, ValueError):
            return None

    @staticmethod
    def _m5_draft_verdict(draft: dict) -> str:
        """ACCEPTED when every recorded rule passed, else REJECTED."""
        results = draft.get("rule_results") or []
        if not results:
            return "REJECTED"
        for entry in results:
            if not isinstance(entry, dict) or \
                    entry.get("status") != "pass":
                return "REJECTED"
        return "ACCEPTED"

    def _m5_view(self, record: dict) -> dict:
        """Install plan panel state (P5/P5b/P7).

        Only the budget profile can show the saved run, and only when
        (1) the copies pass integrity and (2) the current profile and
        plan hash (M5's own byte hashing) equal the recorded ones and
        the current selection equals the recorded plan's services.
        Anything else shows unavailable/out-of-date WITHOUT the run.
        """
        profile = record.get("profile")
        if profile is None or profile.get("name") != "budget":
            return {"status": "part2", "data": None,
                    "message": M5_MESSAGE}
        loaded = self._load_m5_file(M5_SAVED_REPLAY)
        plan_loaded = self._load_m5_file(M5_SAVED_PLAN)
        if loaded is None or plan_loaded is None:
            return {"status": "unavailable", "data": None,
                    "message": M5_SAVED_UNAVAILABLE, "sha256": None,
                    "inputs": None}
        file_hex, replay = loaded
        _plan_hex, plan_file = plan_loaded
        recorded = replay.get("inputs") or {}
        if not isinstance(recorded, dict):
            recorded = {}

        def _recorded(key: str):
            entry = recorded.get(key)
            if not isinstance(entry, dict):
                return None
            value = entry.get("sha256")
            return value if isinstance(value, str) and value else None

        recorded_services = (plan_file.get("handoff") or {}).get(
            "services")
        if not isinstance(recorded_services, list) or \
                not recorded_services or \
                any(not isinstance(item, str) or not item
                    for item in recorded_services):
            recorded_services = None
        else:
            recorded_services = sorted(recorded_services)
        selection = record.get("selection") or {}
        current_services = selection.get("services")
        if not isinstance(current_services, list) or \
                not current_services:
            current_services = None
        else:
            current_services = sorted(current_services)
        plan_data = (record["outputs"].get("plan") or {}).get("data")
        pairs = {
            "profile": {
                "recorded": _recorded("profile"),
                "current": session_module.m5_style_hash(
                    profile.get("data"))},
            "plan": {
                "recorded": _recorded("plan"),
                "current": session_module.m5_style_hash(plan_data)
                if isinstance(plan_data, dict) else None},
            "selection": {"recorded": recorded_services,
                          "current": current_services},
        }

        def _pair_ok(pair) -> bool:
            return pair["recorded"] is not None and \
                pair["current"] is not None and \
                pair["recorded"] == pair["current"]

        if not all(_pair_ok(pairs[key])
                   for key in ("profile", "plan", "selection")):
            return {"status": "outdated", "data": None,
                    "message": M5_SAVED_OUTDATED, "sha256": file_hex,
                    "inputs": pairs}
        pages = []
        for entry in replay.get("fetch_records") or []:
            if not isinstance(entry, dict):
                continue
            excerpt = entry.get("excerpt") or {}
            pages.append({"id": entry.get("id"),
                          "url": entry.get("url_final"),
                          "byte_size": entry.get("byte_size"),
                          "excerpt_ranges": excerpt.get("ranges"),
                          "excerpt_sha256": excerpt.get("sha256")})
        drafts = []
        for draft in replay.get("drafts") or []:
            if not isinstance(draft, dict):
                continue
            drafts.append({
                "index": draft.get("index"),
                "verdict": self._m5_draft_verdict(draft),
                "finish_reason": draft.get("finish_reason"),
                "usage": draft.get("usage"),
                "rules": [{"rule": item.get("rule"),
                           "status": item.get("status"),
                           "reasons": item.get("reasons") or []}
                          for item in draft.get("rule_results") or []
                          if isinstance(item, dict)]})
        refusal = None
        for draft in replay.get("drafts") or []:
            parsed = (draft.get("parsed") or {}) \
                if isinstance(draft, dict) else {}
            blocked = parsed.get("do_not_install")
            citation = (blocked.get("citation") or {}) \
                if isinstance(blocked, dict) else {}
            if citation.get("quote"):
                url = None
                for page in pages:
                    if page["id"] == citation.get("fetch_id"):
                        url = page["url"]
                refusal = {"reason": blocked.get("reason"),
                           "quote": citation.get("quote"),
                           "rule": citation.get("rule"),
                           "fetch_id": citation.get("fetch_id"),
                           "url": url}
                break
        return {"status": "saved", "message": M5_SAVED_TITLE,
                "sha256": file_hex, "inputs": pairs,
                "data": {"result": replay.get("result"),
                         "model": replay.get("model"),
                         "pages": pages, "drafts": drafts,
                         "refusal": refusal}}

    def state_for(self, session_id: str):
        """Public (csrf, state) for a session. Read-only: changes nothing."""
        with self.lock:
            session = self.sessions[session_id]
            record = session["record"]
            answers = record["answers"]
            state = {
                "profile": record["profile"],
                "answers": answers,
                "preselected": preselected_services(answers["data"])
                if answers is not None else None,
                "selection": record["selection"],
                "outputs": {
                    "plan": session_module.describe_output(
                        record, "plan"),
                    "research": {"status": "none", "data": None,
                                 "message": NO_RESEARCH_MESSAGE},
                    "m5": self._m5_view(record),
                    "build": session_module.describe_output(
                        record, "build"),
                },
                "jobs": {job_id: {
                    "step": job["step"], "status": job["status"],
                    "reason": job.get("reason")}
                    for job_id, job in record["jobs"].items()},
                "log": list(record["log"][-10:]),
                "questions": [dict(question) for question in QUESTIONS],
                "live": {"enabled": False, "message": LIVE_MESSAGE},
                "phone_link": {"status": "part2",
                               "message": PHONE_LINK_MESSAGE},
                "boot_target": BOOT_TARGET,
            }
            return session["csrf"], state


def start_links(port: int, token: str, forwarded_host=None) -> list:
    """Start links printed in the terminal (direct, then forwarded)."""
    links = ["http://127.0.0.1:%d/start?token=%s" % (port, token)]
    if forwarded_host:
        links.append("https://%s/start?token=%s"
                     % (forwarded_host, token))
    return links


class Handler(BaseHTTPRequestHandler):
    """One request. GET never changes anything; POST changes by CSRF."""

    server_version = "AppServer/1"

    @property
    def app(self) -> App:
        return self.server.app

    def log_message(self, *args):
        # The start link carries the token: never log query strings,
        # and never log cookies or tokens.
        sys.stderr.write(
            "%s - - [%s] %s\n" % (
                self.client_address[0],
                self.log_date_time_string(),
                (args[0] % args[1:]).split("?")[0]
                if args else "-"))

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler's errors must still carry the CSP,
        # and must never offer CORS.
        body = ("error %d\n" % code).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _send(self, code: int, body: bytes, content_type: str,
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj, extra: dict | None = None):
        self._send(code, (json.dumps(obj, sort_keys=True) + "\n").encode(
            "utf-8"), "application/json", extra)

    def _refuse(self, code: int, error: str, reason: str):
        # One-line refusal log: which check failed. The path is logged
        # without its query, and never with token or cookie values.
        path = urllib.parse.urlparse(self.path).path
        sys.stderr.write("app: refused %s %s: %s\n"
                         % (self.command, path, reason))
        self._json(code, {"error": error})

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        return host in self.app.expected_hosts()

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        return origin in self.app.expected_origins()

    def _session_id(self):
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name.strip() == SESSION_COOKIE:
                found = value.strip()
                if found:
                    return found
        return None

    def _read_body(self):
        """Body bytes, or None after a 413 was sent (over 64 KB)."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length < 0:
            length = 0
        if length > MAX_BODY:
            self._json(413, {"error": "body over 64 KB"})
            return None
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _static_path(self, name: str):
        path = os.path.join(APP_DIR, name)
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    # -- GET (never changes anything) -------------------------------

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler name)
        if not self._host_ok():
            self._refuse(403, "wrong Host", "host mismatch")
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/start":
            self._serve_signin(parsed)
            return
        if parsed.path == "/signin.js":
            # The sign-in page's POST script: no secrets in it, so it is
            # served before the session check like /start itself.
            data = self._static_path("signin.js")
            if data is None:
                self._json(500, {"error": "sign-in script missing"})
                return
            self._send(200, data, "application/javascript")
            return
        session_id = self._session_id()
        with self.app.lock:
            session = self.app.sessions.get(session_id) \
                if session_id else None
        if session is None:
            self._refuse(401, "sign in required", "no session")
            return
        if parsed.path == "/":
            data = self._static_path("index.html")
            if data is None:
                self._json(500, {"error": "app page missing"})
                return
            self._send(200, data, "text/html")
            return
        if parsed.path in STATIC_FILES:
            name, content_type = STATIC_FILES[parsed.path]
            data = self._static_path(name)
            if data is None:
                self._json(500, {"error": "app file missing"})
                return
            self._send(200, data, content_type)
            return
        if parsed.path == "/api/state":
            csrf, state = self.app.state_for(session_id)
            self._json(200, {"csrf": csrf, "state": state})
            return
        if parsed.path == "/api/boot":
            self._serve_boot(session_id)
            return
        if parsed.path.startswith("/api/jobs/"):
            self._serve_job(session_id, parsed.path[len("/api/jobs/"):])
            return
        self._json(404, {"error": "not found"})

    def _serve_signin(self, parsed):
        # Opening the start link only shows the Sign in button. The
        # token is embedded so the plain form can post it back; nothing
        # is consumed, created or changed here.
        query = urllib.parse.parse_qs(parsed.query)
        token = query.get("token", [""])[0]
        data = self._static_path("signin.html")
        if data is None:
            self._json(500, {"error": "sign-in page missing"})
            return
        page = data.decode("utf-8").replace("{{TOKEN}}",
                                            html.escape(token))
        self._send(200, page.encode("utf-8"), "text/html")

    def _serve_boot(self, session_id: str):
        with self.app.lock:
            record = self.app.sessions[session_id]["record"]
            view = session_module.describe_output(record, "build")
            build = session_module.session_build_view(record)
        if view["status"] != "current":
            self._json(409, {"error": "no current build",
                             "status": view["status"],
                             "message": view.get("message")})
            return
        from . import macsteps as macsteps_module
        try:
            steps = macsteps_module.generate(
                build["manifest"], build["summary"])
        except (KeyError, ValueError) as exc:
            self._json(500, {"error": "cannot render boot steps: %s"
                             % exc})
            return
        self._json(200, {"target": BOOT_TARGET, "steps": steps})

    def _serve_job(self, session_id: str, job_id: str):
        with self.app.lock:
            record = self.app.sessions[session_id]["record"]
            job = record["jobs"].get(job_id)
            if job is None:
                self._json(404, {"error": "unknown job"})
                return
            payload = {"job": {"id": job_id, "step": job["step"],
                               "status": job["status"],
                               "reason": job.get("reason")},
                       "progress": []}
            job_dir = job.get("dir")
        if job_dir is not None:
            log_path = os.path.join(job_dir, "progress.log")
            try:
                with open(log_path, "rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - 32768))
                    tail = handle.read().decode("utf-8", "replace")
            except OSError:
                tail = ""
            lines = tail.splitlines()
            payload["progress"] = lines[-30:]
        self._json(200, payload)

    # -- POST (every change; CSRF except the sign-in) ----------------

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler name)
        if not self._host_ok():
            self._refuse(403, "wrong Host", "host mismatch")
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/signin":
            self._signin()
            return
        session_id = self._session_id()
        with self.app.lock:
            known = session_id in self.app.sessions \
                if session_id else False
        if not known:
            self._refuse(401, "sign in required", "no session")
            return
        if not self._origin_ok():
            self._refuse(403, "wrong or missing Origin",
                         "origin mismatch")
            return
        with self.app.lock:
            csrf = self.app.sessions[session_id]["csrf"]
        if not hmac.compare_digest(
                self.headers.get(CSRF_HEADER) or "", csrf):
            self._refuse(403, "wrong or missing CSRF token",
                         "wrong or missing CSRF token")
            return
        if parsed.path == "/api/signout":
            body = self._read_body()
            if body is None:
                return
            with self.app.lock:
                self.app.sessions.pop(session_id, None)
            self._json(200, {"ok": True}, {
                "Set-Cookie": "%s=; HttpOnly; SameSite=Strict; Path=/; "
                              "Max-Age=0" % SESSION_COOKIE})
            return
        if parsed.path == "/api/connect":
            self._connect(session_id)
            return
        if parsed.path == "/api/questions":
            self._questions(session_id)
            return
        if parsed.path == "/api/selection":
            self._selection(session_id)
            return
        if parsed.path == "/api/build":
            self._build(session_id)
            return
        self._json(404, {"error": "not found"})

    def _signin(self):
        # The one request allowed without a session or CSRF token. It
        # still needs an allowed full Origin, and consumes the token.
        if not self._origin_ok():
            self._refuse(403, "wrong or missing Origin",
                         "origin mismatch")
            return
        body = self._read_body()
        if body is None:
            return
        content_type = self.headers.get("Content-Type") or ""
        token = ""
        if "application/json" in content_type:
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                self._json(400, {"error": "body is not JSON"})
                return
            if isinstance(payload, dict):
                token = payload.get("token") or ""
        else:
            fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            token = fields.get("token", [""])[0]
        with self.app.lock:
            valid = self.app.start_token_used is False and \
                hmac.compare_digest(token, self.app.start_token)
            if valid:
                self.app.start_token_used = True
                session_id = secrets.token_urlsafe(24)
                csrf = secrets.token_urlsafe(24)
                self.app.sessions[session_id] = {
                    "csrf": csrf,
                    "record": session_module.new_record(),
                }
        if not valid:
            self._refuse(401, "invalid or used start token",
                         "invalid or used start token")
            return
        host = (self.headers.get("Host") or "").strip()
        secure = self.app.forwarded_host is not None and \
            host == self.app.forwarded_host
        cookie = "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" % (
            SESSION_COOKIE, session_id, "; Secure" if secure else "")
        if "application/json" in content_type:
            self._json(200, {"ok": True, "csrf": csrf},
                       {"Set-Cookie": cookie})
        else:
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Set-Cookie", cookie)
            self.end_headers()

    def _json_body(self):
        """Parsed JSON dict body, or None after an error was sent."""
        body = self._read_body()
        if body is None:
            return None
        try:
            payload = json.loads(body.decode("utf-8")) \
                if body else {}
        except ValueError:
            self._json(400, {"error": "body is not JSON"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": "body must be an object"})
            return None
        return payload

    def _state_response(self, session_id: str, job_id, code: int = 200):
        csrf, state = self.app.state_for(session_id)
        self._json(code, {"job_id": job_id, "csrf": csrf,
                          "state": state})

    def _connect(self, session_id: str):
        payload = self._json_body()
        if payload is None:
            return
        name = payload.get("profile")
        if name not in KNOWN_FIXTURES:
            self._json(422, {"error": "unknown profile %r (known: %s)"
                              % (name, ", ".join(KNOWN_FIXTURES))})
            return
        with self.app.lock:
            if (session_id, "connect") in self.app.running:
                self._json(429, {"error": "a scan is already running"})
                return
            self.app.running[(session_id, "connect")] = True
        try:
            exit_code, profile, error = self.app.scan_fn(name)
        finally:
            with self.app.lock:
                self.app.running.pop((session_id, "connect"), None)
        if exit_code != 0 or profile is None:
            self._json(500, {"error": error or "scan failed"})
            return
        with self.app.lock:
            record = self.app.sessions[session_id]["record"]
            session_module.set_profile(record, name, profile)
            job = session_module.new_job(record, "connect")
            session_module.finish_job(record, job["id"], "done")
            job_id = job["id"]
        self._state_response(session_id, job_id)

    def _questions(self, session_id: str):
        payload = self._json_body()
        if payload is None:
            return
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            self._json(422, {"error": "answers must be an object"})
            return
        for question in QUESTIONS:
            options = [value for value, _label in question["options"]]
            if answers.get(question["id"]) not in options:
                self._json(422, {"error": "bad answer for %s"
                                 % question["id"]})
                return
        with self.app.lock:
            record = self.app.sessions[session_id]["record"]
            session_module.set_answers(
                record, {question["id"]: answers[question["id"]]
                         for question in QUESTIONS})
            job = session_module.new_job(record, "questions")
            session_module.finish_job(record, job["id"], "done")
            job_id = job["id"]
        self._state_response(session_id, job_id)

    def _selection(self, session_id: str):
        payload = self._json_body()
        if payload is None:
            return
        services = payload.get("services")
        if not isinstance(services, list) or not services or \
                any(item not in KNOWN_SERVICES for item in services):
            self._json(422, {"error": "services must be a non-empty "
                                      "list of %s"
                             % ", ".join(sorted(KNOWN_SERVICES))})
            return
        with self.app.lock:
            if (session_id, "selection") in self.app.running:
                self._json(429, {"error": "a plan is already running"})
                return
            record = self.app.sessions[session_id]["record"]
            if record["profile"] is None:
                self._json(409, {"error": "connect a phone first"})
                return
            session_module.set_selection(record, list(services))
            job = session_module.new_job(record, "selection")
            self.app.running[(session_id, "selection")] = job["id"]
            profile_name = record["profile"]["name"]
        try:
            exit_code, plan, error = self.app.plan_fn(
                profile_name, sorted(set(services)))
        finally:
            with self.app.lock:
                self.app.running.pop((session_id, "selection"), None)
        with self.app.lock:
            record = self.app.sessions[session_id]["record"]
            if exit_code not in (0, EXIT_REFUSED) or plan is None:
                session_module.finish_job(
                    record, job["id"], "failed",
                    error or "plan failed")
                job_id = job["id"]
            else:
                session_module.store_output(record, "plan", plan, job)
                session_module.finish_job(record, job["id"], "done")
                job_id = job["id"]
        if exit_code not in (0, EXIT_REFUSED) or plan is None:
            self._json(500, {"error": error or "plan failed"})
            return
        self._state_response(session_id, job_id)

    def _build(self, session_id: str):
        payload = self._json_body()
        if payload is None:
            return
        with self.app.lock:
            if (session_id, "build") in self.app.running:
                self._json(429, {"error": "a build is already running"})
                return
            record = self.app.sessions[session_id]["record"]
            status = session_module.output_status(record, "plan")
            if status == "missing":
                self._json(409, {"error": "no plan yet: confirm "
                                          "services first"})
                return
            if status == "stale":
                self._json(409, {"error": "plan is out of date: "
                                          "re-run Services"})
                return
            plan = record["outputs"]["plan"]["data"]
            if plan.get("selection_result") == "refused":
                reasons = plan.get("fit", {}).get("reasons", [])
                self._json(409, {"error": "plan refused: %s"
                                 % ("; ".join(reasons)
                                    if reasons else "see Services")})
                return
            if plan.get("build_allowed") != "emulator_only":
                self._json(409, {"error": "plan does not allow a build "
                                          "(build_allowed=%s)"
                                 % plan.get("build_allowed")})
                return
            plan_bytes = (json.dumps(plan, sort_keys=True, indent=2)
                          + "\n").encode("utf-8")
            plan_sha = hashlib.sha256(plan_bytes).hexdigest()
            build_id = build_id_for_plan(plan, plan_sha)
            job_dir = os.path.join(self.app.out_root, build_id)
            if os.path.exists(job_dir):
                # Fresh builds only: an old build is refused, not reused.
                self._json(409, {"error": "build %s already exists: "
                                          "refusing to reuse; fresh "
                                          "builds only" % build_id})
                return
            try:
                os.makedirs(job_dir)
            except OSError as exc:
                self._json(500, {"error": "cannot create job dir: %s"
                                 % exc})
                return
            try:
                with open(os.path.join(job_dir, "plan.json"),
                          "wb") as handle:
                    handle.write(plan_bytes)
                with open(os.path.join(job_dir, "progress.log"), "w",
                          encoding="utf-8") as handle:
                    handle.write("job created for build %s\n" % build_id)
            except OSError as exc:
                self._json(500, {"error": "cannot write job dir: %s"
                                 % exc})
                return
            job = session_module.new_job(record, "build")
            job["dir"] = job_dir
            job["build_id"] = build_id
            self.app.running[(session_id, "build")] = job["id"]
            job_id = job["id"]
        worker = threading.Thread(
            target=_run_build_job,
            args=(self.app, session_id, job_id),
            daemon=True)
        worker.start()
        csrf, state = self.app.state_for(session_id)
        self._json(202, {"job_id": job_id, "build_id": build_id,
                         "status": "running", "csrf": csrf,
                         "state": state})


def default_build_command(app: App, job_dir: str,
                          build_id: str) -> list:
    """The fresh-build command, always through the heavy-step helper."""
    from .build import image as image_module
    repo_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return [
        app.helper_path,
        sys.executable,
        "-m", "stargate.appbuild",
        "--plan", os.path.join(job_dir, "plan.json"),
        "--job-dir", job_dir,
        "--tools-dir", app.tools_dir,
        "--expect-build-id", build_id,
        "--assistant-apk", image_module.ASSISTANT_APK_PATH,
        "--model-dir", image_module.MODEL_DIR,
        "--repo", repo_root,
    ]


def _run_build_job(app: App, session_id: str, job_id: str):
    """Background build: helper + driver, then store or discard."""
    with app.lock:
        record = app.sessions[session_id]["record"]
        job = record["jobs"][job_id]
        job_dir = job["dir"]
        build_id = job["build_id"]
    if app.build_cmd_factory is not None:
        command = app.build_cmd_factory(app, job_dir, build_id)
    else:
        command = default_build_command(app, job_dir, build_id)
    log_path = os.path.join(job_dir, "progress.log")
    exit_code = 1
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))))
            proc.wait()
            exit_code = proc.returncode
    except OSError as exc:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("cannot start build command: %s\n" % exc)
    result = None
    result_path = os.path.join(job_dir, "result.json")
    try:
        with open(result_path, "rb") as handle:
            result = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        result = None
    with app.lock:
        record = app.sessions[session_id]["record"]
        job = record["jobs"][job_id]
        app.running.pop((session_id, "build"), None)
        if exit_code == 75:
            session_module.finish_job(
                record, job_id, "paused",
                "PAUSED for the Now track: re-run after the flag clears")
            return
        if exit_code != 0 or not isinstance(result, dict) or \
                result.get("status") != "ok":
            reason = None
            if isinstance(result, dict) and result.get("reason"):
                reason = result["reason"]
            if reason is None:
                reason = "build failed (exit %d)" % exit_code
            session_module.finish_job(record, job_id, "failed", reason)
            return
        if not session_module.job_is_current(record, job):
            session_module.discard_stale_result(record, job, "build")
            sys.stderr.write("app: discarded stale build %s\n" % job_id)
            return
        data = {"manifest": result.get("manifest"),
                "summary": result.get("summary"),
                "checks": result.get("checks")}
        session_module.store_output(record, "build", data, job)
        session_module.finish_job(record, job_id, "done")


class AppHTTPServer(ThreadingHTTPServer):
    """HTTP server on 127.0.0.1 only. Never another address."""

    daemon_threads = True

    def __init__(self, app: App):
        self.app = app
        super().__init__((BIND_ADDRESS, app.port), Handler)
        app.port = self.socket.getsockname()[1]


def run_app(argv=None) -> int:
    """Run `stargate app`. Returns an exit code."""
    import argparse
    parser = argparse.ArgumentParser(prog="stargate app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--forwarded-host", default=None)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("error: --port must be 1..65535", file=sys.stderr)
        return 2
    if args.forwarded_host is not None and (
            not args.forwarded_host.strip() or "/"
            in args.forwarded_host or " " in args.forwarded_host
            or "://" in args.forwarded_host):
        print("error: --forwarded-host must be a bare host",
              file=sys.stderr)
        return 2
    app = App(port=args.port, forwarded_host=args.forwarded_host)
    try:
        server = AppHTTPServer(app)
    except OSError as exc:
        print("error: cannot listen on 127.0.0.1:%d: %s"
              % (args.port, exc), file=sys.stderr)
        return 1
    for link in start_links(server.socket.getsockname()[1],
                            app.start_token, args.forwarded_host):
        print("start: %s" % link)
    print("forward port %d as Private, then open the start link"
          % server.socket.getsockname()[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
