"""M2 fetch boundary: allowlisted HTTPS GET only, run by code.

Every request is built from validated arguments, re-checked at each
redirect hop, and recorded with its outcome. Outcomes: ok, denied,
rate_limited, timeout, too_large, refused, error. A 404 is ok (a
definitive absence; the fact layer decides what it means). No
credentials are ever attached to these requests.

M5 adds the android_page tool (source.android.com, exactly the two
canonical pages in the M5 spec); the same boundary checks apply.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TIMEOUT_SECONDS = 10.0
MAX_CONTENT_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
CACHE_FRESH_SECONDS = 24 * 3600
USER_AGENT = "StarGate-M2/0.1"

OUTCOMES = ("ok", "denied", "rate_limited", "timeout", "too_large",
            "refused", "error")

CODENAME_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,63}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,100}\Z")
REV_RE = re.compile(r"[A-Za-z0-9_.\-]{1,128}\Z")
TITLE_RE = re.compile(r"[A-Za-z0-9_().,\-]{1,128}\Z", re.ASCII)
QUERY_RE = re.compile(r"[A-Za-z0-9 _+.:@\-]{1,128}\Z")

CHALLENGE_MARKERS = ("captcha", "cf-challenge", "just a moment")
LOGIN_TITLE_RE = re.compile(r"<title>\s*(log\s?in|sign\s?in)\b", re.IGNORECASE)

# Step B detector. The broad signals ("captcha", "just a moment")
# are checked against the page title and the extracted visible text
# only, never against raw script or attribute content: ordinary
# pages carry them in markup (e.g. a devsite JS flag or a reCAPTCHA
# script tag). Narrow interstitial markup stays on the raw HTML.
VISIBLE_CHALLENGE_MARKERS = ("captcha", "just a moment")
_CHALLENGE_ATTR_RE = re.compile(
    r"""\b(?:id|class)\s*=\s*["'][^"']*cf-challenge[^"']*["']""",
    re.IGNORECASE)
_CHALLENGE_FORM_RE = re.compile(
    r"""<form\b[^>]*?(?:\baction\s*=\s*["'][^"']*/cdn-cgi/challenge"""
    r"""[^"']*["']|\b(?:id|name|class)\s*=\s*["'][^"']*"""
    r"""challenge-form[^"']*["'])""",
    re.IGNORECASE)
_TITLE_TEXT_RE = re.compile(r"<title[^>]*>(.*?)</title>",
                            re.IGNORECASE | re.DOTALL)
_LOGIN_TITLE_RE = re.compile(r"\A\s*(log\s?in|sign\s?in)\b",
                             re.IGNORECASE)

SEARCH_PER_PAGE = 5


class RefusedFetch(Exception):
    """Raised when a fetch is refused before any request."""


@dataclass
class FetchRecord:
    record_id: str
    tool: str
    url_requested: str
    url_final: str
    status: int | None
    fetched_at: str
    byte_size: int
    sha256: str
    outcome: str
    cached: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return {"id": self.record_id, "tool": self.tool,
                "url_requested": self.url_requested,
                "url_final": self.url_final, "status": self.status,
                "fetched_at": self.fetched_at, "byte_size": self.byte_size,
                "sha256": self.sha256, "outcome": self.outcome,
                "cached": self.cached}


@dataclass
class FetchResponse:
    record: FetchRecord
    body: bytes
    headers: dict = field(default_factory=dict)


def utc_now_z() -> str:
    moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_name(value: object) -> bool:
    return (isinstance(value, str) and NAME_RE.match(value) is not None
            and value not in (".", ".."))


def _valid_rev(value: object) -> bool:
    return (isinstance(value, str) and REV_RE.match(value) is not None
            and value not in (".", ".."))


def build_url(tool: str, args: dict) -> tuple[str, str]:
    """Build the request URL for one tool call. Returns (url, kind)
    where kind is github-core, github-search or other. Raises
    RefusedFetch on anything invalid."""
    if not isinstance(args, dict):
        raise RefusedFetch("arguments must be an object")
    if tool == "lineage_device":
        codename = args.get("codename")
        if not isinstance(codename, str) or not CODENAME_RE.match(codename):
            raise RefusedFetch("bad codename")
        return ("https://raw.githubusercontent.com/LineageOS/lineage_wiki/"
                "main/_data/devices/%s.yml" % codename), "other"
    if tool == "github_repo":
        owner, repo = args.get("owner"), args.get("repo")
        if not _valid_name(owner) or not _valid_name(repo):
            raise RefusedFetch("bad owner/repo")
        return "https://api.github.com/repos/%s/%s" % (owner, repo), \
            "github-core"
    if tool == "github_search":
        query = args.get("q")
        if not isinstance(query, str) or not QUERY_RE.match(query):
            raise RefusedFetch("bad search query")
        if not query.strip():
            raise RefusedFetch("empty search query")
        return ("https://api.github.com/search/repositories?%s"
                % urllib.parse.urlencode(
                    {"q": query, "per_page": SEARCH_PER_PAGE})), \
            "github-search"
    if tool == "hf_model_search":
        search = args.get("search", "")
        author = args.get("author", "")
        if search != "" and (
                not isinstance(search, str) or not QUERY_RE.match(search)):
            raise RefusedFetch("bad search term")
        if author != "" and (
                not isinstance(author, str) or not QUERY_RE.match(author)):
            raise RefusedFetch("bad author")
        if not str(search).strip() and not str(author).strip():
            raise RefusedFetch("empty model search")
        return ("https://huggingface.co/api/models?%s"
                % urllib.parse.urlencode(
                    {"search": search, "author": author})), "other"
    if tool in ("hf_model", "hf_model_files"):
        repo_id = args.get("repo_id")
        if not isinstance(repo_id, str) or repo_id.count("/") != 1:
            raise RefusedFetch("bad repo_id")
        owner, name = repo_id.split("/")
        if not _valid_name(owner) or not _valid_name(name):
            raise RefusedFetch("bad repo_id")
        if tool == "hf_model":
            return "https://huggingface.co/api/models/%s/%s" % (
                owner, name), "other"
        rev = args.get("rev")
        if not _valid_rev(rev):
            raise RefusedFetch("bad revision")
        return "https://huggingface.co/api/models/%s/%s/tree/%s" % (
            owner, name, rev), "other"
    if tool == "pmos_page":
        title = args.get("title")
        if not isinstance(title, str) or not TITLE_RE.match(title):
            raise RefusedFetch("bad page title")
        return "https://wiki.nura.eco/wiki/%s" % title, "other"
    if tool == "android_page":
        # M5: exactly the two canonical source.android.com pages.
        page = args.get("page")
        if page == "fastbootd":
            return ("https://source.android.com/docs/core/architecture/"
                    "bootloader/fastbootd"), "other"
        if page == "running":
            return "https://source.android.com/docs/setup/test/running", \
                "other"
        raise RefusedFetch("bad android page")
    raise RefusedFetch("unknown tool %r" % (tool,))


def _route_allowed(host: str, path: str, query: str) -> bool:
    """Check one URL against the allowlist table (host + route)."""
    if host == "raw.githubusercontent.com":
        if query:
            return False
        prefix = "/LineageOS/lineage_wiki/main/_data/devices/"
        if not path.startswith(prefix) or not path.endswith(".yml"):
            return False
        return CODENAME_RE.match(path[len(prefix):-len(".yml")]) is not None
    if host == "api.github.com":
        if path == "/search/repositories":
            return True
        parts = path.split("/")
        return (len(parts) == 4 and parts[1] == "repos"
                and _valid_name(parts[2]) and _valid_name(parts[3])
                and not query)
    if host == "huggingface.co":
        if path == "/api/models":
            return True
        parts = path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "models"]:
            return _valid_name(parts[3]) and _valid_name(parts[4]) \
                and not query
        if len(parts) == 7 and parts[1:3] == ["api", "models"] \
                and parts[5] == "tree":
            return _valid_name(parts[3]) and _valid_name(parts[4]) \
                and _valid_rev(parts[6]) and not query
        return False
    if host == "wiki.nura.eco":
        if query or not path.startswith("/wiki/"):
            return False
        return TITLE_RE.match(path[len("/wiki/"):]) is not None
    if host == "source.android.com":
        # M5: exactly the two canonical pages, no query string.
        if query:
            return False
        return path in (
            "/docs/core/architecture/bootloader/fastbootd",
            "/docs/setup/test/running",
        )
    return False


def default_resolve(host: str):
    return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)


def _host_forbidden(host: str, resolve) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        infos = resolve(host)
    except OSError:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError, TypeError):
            continue
        if (address.is_private or address.is_loopback
                or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            return True
    return False


def check_url(url: str, resolve=default_resolve) -> tuple[str, str, str]:
    """Validate one URL. Returns (host, path, query) or raises
    RefusedFetch."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise RefusedFetch("bad URL: %s" % exc)
    if parts.scheme != "https":
        raise RefusedFetch("only https is allowed")
    if parts.username or parts.password:
        raise RefusedFetch("credentials in URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise RefusedFetch("URL has no host")
    if _host_forbidden(host, resolve):
        raise RefusedFetch("host is not a public name: %s" % host)
    path = parts.path or "/"
    if not _route_allowed(host, path, parts.query):
        raise RefusedFetch("URL is not on the allowlist")
    return host, path, parts.query


def html_to_text(body: bytes) -> str:
    """Visible text of an HTML body.

    The single HTML-to-text boundary: comments, scripts, styles and
    tags are stripped first (so escaped angle brackets stay text),
    then character references are decoded exactly once (html.unescape
    semantics), then whitespace is collapsed. The output is plain
    text; no later stage may decode entities again. Double-encoded
    input (e.g. "&amp;#39;") therefore stays "&#39;" here and
    everywhere downstream.
    """
    try:
        raw = body.decode("utf-8", "replace")
    except Exception:
        return ""
    text = re.sub(r"(?is)<!--.*?-->", " ", raw)
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ",
                  text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _is_challenge(body: bytes, headers: dict | None = None) -> bool:
    """Whether a response body is a challenge or login wall.

    Checked in order: a cf-mitigated: challenge header (its absence
    proves nothing), narrow interstitial markup on the raw HTML
    (cf-challenge id/class, known challenge forms), a login-wall
    page title (never a navigation link), and the broad "captcha" /
    "just a moment" signals against the page title and the visible
    text only. A /cdn-cgi/challenge-platform/ background script path
    alone matches none of these. Anchor text is irrelevant here:
    challenge detection stays separate from anchor validation.
    """
    if headers is not None:
        mitigated = str(headers.get("cf-mitigated") or "")
        if mitigated.strip().lower() == "challenge":
            return True
    try:
        raw = body.decode("utf-8", "replace")
    except Exception:
        return False
    if _CHALLENGE_ATTR_RE.search(raw) \
            or _CHALLENGE_FORM_RE.search(raw):
        return True
    match = _TITLE_TEXT_RE.search(raw)
    title = html.unescape(match.group(1)).strip() if match else ""
    if _LOGIN_TITLE_RE.match(title):
        return True
    visible = html_to_text(body).lower()
    lowered_title = title.lower()
    for marker in VISIBLE_CHALLENGE_MARKERS:
        if marker in visible or marker in lowered_title:
            return True
    return False


def _is_rate_limit_body(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", "replace").lower()
    except Exception:
        return False
    return "rate limit" in text or "rate_limit" in text


CONTEXT_CHARS = 80


def _locate_match(text: str, index: int) -> str:
    """Where a marker match sits: comment, title, script, style,
    attribute, or visible text. Best effort over raw markup."""
    for match in re.finditer(r"<!--.*?-->", text, re.DOTALL):
        if match.start() <= index < match.end():
            return "comment"
    for match in re.finditer(r"<title\b[^>]*>.*?</title\s*>", text,
                             re.IGNORECASE | re.DOTALL):
        if match.start() <= index < match.end():
            return "title"
    for tag in ("script", "style"):
        for match in re.finditer(r"<%s\b[^>]*>" % tag, text,
                                 re.IGNORECASE):
            open_start, open_end = match.span()
            if open_start <= index < open_end:
                return "attribute"
            close = re.search(r"</%s\s*>" % tag, text[open_end:],
                              re.IGNORECASE)
            if close is not None and open_end <= index < \
                    open_end + close.start():
                return tag
    for match in re.finditer(r"<[^>]*>", text):
        if match.start() <= index < match.end():
            return "attribute"
    return "visible"


def _match_record(text: str, start: int, end: int,
                  marker: str) -> dict:
    return {"marker": marker, "matched": text[start:end],
            "start": start,
            "before": text[max(0, start - CONTEXT_CHARS):start],
            "after": text[end:end + CONTEXT_CHARS],
            "location": _locate_match(text, start)}


def _timeout_for(budgets, default: float) -> float:
    """One socket read timeout: the default, or only the time left.

    Budgets without a run deadline (M2's) keep the default; M5's
    deadline budgets cap every read at the time left.
    """
    cap = getattr(budgets, "timeout_for", None)
    if not callable(cap):
        return default
    try:
        return min(default, float(cap(default)))
    except Exception:
        return default


def find_marker_matches(body: bytes) -> list:
    """Every raw-markup challenge-marker match, for diagnosis.

    This is evidence, not the detector: it reports every
    CHALLENGE_MARKERS substring and login-title pattern wherever it
    sits. Each hit carries 80 characters of context on each side and
    its location (comment, title, script, style, attribute, or
    visible text), so a denial can be checked against what the
    detector actually reads. The login-title hit uses the
    detector's own title pattern (attributes tolerated).
    """
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return []
    lowered = text.lower()
    matches = []
    for marker in CHALLENGE_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index < 0:
                break
            end = index + len(marker)
            if text[index:end].lower() == marker:
                matches.append(_match_record(text, index, end,
                                             marker))
            start = index + 1
    for match in _TITLE_TEXT_RE.finditer(text):
        inner = html.unescape(match.group(1)).strip()
        if _LOGIN_TITLE_RE.match(inner):
            start, end = match.span()
            matches.append(_match_record(text, start, end,
                                         "login-title"))
    matches.sort(key=lambda item: item["start"])
    return matches


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_opener():
    return urllib.request.build_opener(_NoRedirect)


class Fetcher:
    """Runs allowlisted fetches with cache, budgets and records."""

    def __init__(self, budgets=None, opener=None, resolve=None,
                 cache=None, no_cache: bool = False) -> None:
        self.budgets = budgets
        self.opener = opener or default_opener()
        self.resolve = resolve or default_resolve
        self.cache: dict = cache if cache is not None else {}
        self.no_cache = no_cache
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return "R%d" % self._next_id

    def _record(self, tool, requested, final, status, size, digest,
                outcome, cached=False, detail=""):
        return FetchRecord(
            record_id=self._new_id(), tool=tool, url_requested=requested,
            url_final=final, status=status, fetched_at=utc_now_z(),
            byte_size=size, sha256=digest, outcome=outcome, cached=cached,
            detail=detail)

    def _store(self, key, record, body, headers, fetched_epoch):
        self.cache[key] = {"record": record, "body": body,
                           "headers": headers, "epoch": fetched_epoch}

    def fetch(self, tool: str, args: dict) -> FetchResponse:
        try:
            url, kind = build_url(tool, args)
        except RefusedFetch as exc:
            record = self._record(tool, "", "", None, 0, "", "refused",
                                  detail=str(exc))
            return FetchResponse(record=record, body=b"")
        try:
            check_url(url, self.resolve)
        except RefusedFetch as exc:
            record = self._record(tool, url, url, None, 0, "", "refused",
                                  detail=str(exc))
            return FetchResponse(record=record, body=b"")
        if tool.startswith("github_") and self.budgets is not None:
            if not self.budgets.github_usable():
                record = self._record(tool, url, url, None, 0, "",
                                      "rate_limited",
                                      detail="github allowance exhausted")
                return FetchResponse(record=record, body=b"")
        key = (tool, url)
        if not self.no_cache and key in self.cache:
            entry = self.cache[key]
            if time.time() - entry["epoch"] < CACHE_FRESH_SECONDS:
                old = entry["record"]
                record = self._record(tool, url, old.url_final, old.status,
                                      old.byte_size, old.sha256, old.outcome,
                                      cached=True)
                return FetchResponse(record=record, body=entry["body"],
                                     headers=dict(entry["headers"]))
        current = url
        hops = 0
        headers: dict = {}
        while True:
            if self.budgets is not None:
                self.budgets.check_http(kind)
            request = urllib.request.Request(
                current, headers={"User-Agent": USER_AGENT}, method="GET")
            try:
                response = self.opener.open(
                    request,
                    timeout=_timeout_for(self.budgets,
                                         TIMEOUT_SECONDS))
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                try:
                    raw = exc.read(MAX_CONTENT_BYTES + 1) \
                        if exc.fp else b""
                except Exception:
                    raw = b""
                headers = {k.lower(): v
                           for k, v in (exc.headers or {}).items()}
                outcome, final = self._classify(status, raw, headers,
                                                current)
                if self.budgets is not None:
                    self.budgets.note_http(kind, headers)
                    if tool.startswith("github_") \
                            and outcome == "rate_limited":
                        self.budgets.github_stopped = True
                return self._finish(tool, url, final or current, status,
                                    raw, headers, outcome)
            except (TimeoutError, socket.timeout) as exc:
                if self.budgets is not None:
                    self.budgets.note_http(kind)
                record = self._record(tool, url, current, None, 0, "",
                                      "timeout", detail=str(exc)[:80])
                return FetchResponse(record=record, body=b"")
            except (urllib.error.URLError, OSError) as exc:
                if self.budgets is not None:
                    self.budgets.note_http(kind)
                reason = getattr(exc, "reason", None)
                outcome = "timeout" if isinstance(
                    reason, TimeoutError) else "error"
                record = self._record(tool, url, current, None, 0, "",
                                      outcome, detail=str(exc)[:80])
                return FetchResponse(record=record, body=b"")
            with response:
                status = int(response.status)
                headers = {k.lower(): v
                           for k, v in response.headers.items()}
                if status in (301, 302, 303, 307, 308):
                    if self.budgets is not None:
                        self.budgets.note_http(kind, headers)
                    target = response.headers.get("Location")
                    hops += 1
                    if hops > MAX_REDIRECTS:
                        record = self._record(
                            tool, url, current, status, 0, "", "refused",
                            detail="too many redirects")
                        return FetchResponse(record=record, body=b"")
                    if not target:
                        record = self._record(
                            tool, url, current, status, 0, "", "error",
                            detail="redirect without location")
                        return FetchResponse(record=record, body=b"")
                    current = urllib.parse.urljoin(current, target)
                    try:
                        check_url(current, self.resolve)
                    except RefusedFetch as exc:
                        record = self._record(
                            tool, url, current, status, 0, "", "refused",
                            detail=str(exc))
                        return FetchResponse(record=record, body=b"")
                    continue
                raw = response.read(MAX_CONTENT_BYTES + 1)
            if len(raw) > MAX_CONTENT_BYTES:
                if self.budgets is not None:
                    self.budgets.note_http(kind, headers)
                record = self._record(tool, url, current, status, 0, "",
                                      "too_large")
                return FetchResponse(record=record, body=b"")
            outcome, _ = self._classify(status, raw, headers, current)
            if self.budgets is not None:
                self.budgets.note_http(kind, headers)
                if tool.startswith("github_") \
                        and outcome == "rate_limited":
                    self.budgets.github_stopped = True
            return self._finish(tool, url, current, status, raw, headers,
                                outcome)

    def diagnose(self, tool: str, args: dict) -> FetchResponse:
        """One-shot evidence capture for one allowlisted URL.

        Same request (GET, same headers), same allowlist, same size
        and time limits, same classification as fetch(). Differences:
        at most one request (a redirect is recorded, never followed;
        a failure is never retried) and the cache is neither read
        nor written. The body is always returned, even when denied,
        so a denial can be diagnosed instead of staying blind.
        """
        try:
            url, kind = build_url(tool, args)
        except RefusedFetch as exc:
            record = self._record(tool, "", "", None, 0, "", "refused",
                                  detail=str(exc))
            return FetchResponse(record=record, body=b"")
        try:
            check_url(url, self.resolve)
        except RefusedFetch as exc:
            record = self._record(tool, url, url, None, 0, "",
                                  "refused", detail=str(exc))
            return FetchResponse(record=record, body=b"")
        if tool.startswith("github_") and self.budgets is not None:
            if not self.budgets.github_usable():
                record = self._record(tool, url, url, None, 0, "",
                                      "rate_limited",
                                      detail="github allowance exhausted")
                return FetchResponse(record=record, body=b"")
        if self.budgets is not None:
            self.budgets.check_http(kind)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            response = self.opener.open(
                request, timeout=_timeout_for(self.budgets,
                                              TIMEOUT_SECONDS))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                raw = exc.read(MAX_CONTENT_BYTES + 1) \
                    if exc.fp else b""
            except Exception:
                raw = b""
            headers = {k.lower(): v
                       for k, v in (exc.headers or {}).items()}
            outcome, _ = self._classify(status, raw, headers, url)
            if self.budgets is not None:
                self.budgets.note_http(kind, headers)
                if tool.startswith("github_") \
                        and outcome == "rate_limited":
                    self.budgets.github_stopped = True
            digest = hashlib.sha256(raw).hexdigest()
            record = self._record(tool, url, url, status, len(raw),
                                  digest, outcome)
            return FetchResponse(record=record, body=raw,
                                 headers=dict(headers))
        except (TimeoutError, socket.timeout) as exc:
            if self.budgets is not None:
                self.budgets.note_http(kind)
            record = self._record(tool, url, url, None, 0, "",
                                  "timeout", detail=str(exc)[:80])
            return FetchResponse(record=record, body=b"")
        except (urllib.error.URLError, OSError) as exc:
            if self.budgets is not None:
                self.budgets.note_http(kind)
            reason = getattr(exc, "reason", None)
            outcome = "timeout" if isinstance(
                reason, TimeoutError) else "error"
            record = self._record(tool, url, url, None, 0, "",
                                  outcome, detail=str(exc)[:80])
            return FetchResponse(record=record, body=b"")
        with response:
            status = int(response.status)
            headers = {k.lower(): v
                       for k, v in response.headers.items()}
            if status in (301, 302, 303, 307, 308):
                if self.budgets is not None:
                    self.budgets.note_http(kind, headers)
                target = response.headers.get("Location")
                final = urllib.parse.urljoin(url, target) \
                    if target else url
                record = self._record(
                    tool, url, final, status, 0, "", "error",
                    detail="redirect to %s; not followed in "
                           "diagnostic mode" % (target or "(none)"))
                return FetchResponse(record=record, body=b"",
                                     headers=dict(headers))
            raw = response.read(MAX_CONTENT_BYTES + 1)
        if len(raw) > MAX_CONTENT_BYTES:
            if self.budgets is not None:
                self.budgets.note_http(kind, headers)
            digest = hashlib.sha256(raw).hexdigest()
            record = self._record(tool, url, url, status, len(raw),
                                  digest, "too_large")
            return FetchResponse(record=record, body=raw,
                                 headers=dict(headers))
        outcome, _ = self._classify(status, raw, headers, url)
        if self.budgets is not None:
            self.budgets.note_http(kind, headers)
            if tool.startswith("github_") \
                    and outcome == "rate_limited":
                self.budgets.github_stopped = True
        digest = hashlib.sha256(raw).hexdigest()
        record = self._record(tool, url, url, status, len(raw),
                              digest, outcome)
        return FetchResponse(record=record, body=raw,
                             headers=dict(headers))

    def _classify(self, status, raw, headers, current):
        if status == 429:
            return "rate_limited", current
        if status == 403:
            remaining = (headers or {}).get("x-ratelimit-remaining")
            if str(remaining) == "0" or _is_rate_limit_body(raw):
                return "rate_limited", current
            return "denied", current
        if status == 404:
            return "ok", current
        if 200 <= status < 300:
            if _is_challenge(raw, headers):
                return "denied", current
            return "ok", current
        return "error", current

    def _finish(self, tool, requested, final, status, raw, headers,
                outcome):
        digest = hashlib.sha256(raw).hexdigest()
        record = self._record(tool, requested, final, status, len(raw),
                              digest, outcome)
        if not self.no_cache and outcome == "ok":
            self._store((tool, requested), record, raw, dict(headers),
                        time.time())
        return FetchResponse(record=record, body=raw,
                             headers=dict(headers))
