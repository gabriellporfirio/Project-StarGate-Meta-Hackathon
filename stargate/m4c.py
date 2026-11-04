"""M4c cloud providers for the codespace (offline; no live calls).

Pins section 3 (endpoints, auth, request fields, the error table
with retry rules) and the section 6 per-request caps in one place,
for the codespace checks and the offline tests. A key is only ever
paired with its own provider's host. Nothing here sends anything:
tests pass a fake network layer. v2.5: users bring their own key,
no money tracking; the development attempt log (docs/m4c-calls.md)
counts sends only.
"""
from __future__ import annotations

import json
import re
import threading
import time

META = "meta"
BASETEN = "baseten"

PROVIDERS = {
    META: {
        "host": "api.meta.ai",
        "path": "/v1/chat/completions",
        "token_field": "max_completion_tokens",
        "display": "Meta",
    },
    BASETEN: {
        "host": "inference.baseten.co",
        "path": "/v1/chat/completions",
        "token_field": "max_tokens",
        "display": "Baseten",
    },
}

DEFAULT_META_MODEL = "muse-spark-1.3"
OPEN_WEIGHT_MODEL = "meta-models/muse-spark-open-1026"

SYSTEM_MESSAGE = (
    "You are an offline assistant on this phone. You cannot see the "
    "phone's sensors, battery, files, time, or the internet. Say so "
    "plainly when asked about them. Keep answers short.")

QUESTION_MAX_CHARS = 2000
QUESTION_MAX_BYTES = 8000
BODY_MAX_BYTES = 16384
OUTPUT_MAX_TOKENS = 4096
REPLY_MAX_BYTES = 262144
TIMEOUT_S = 60
RETRY_AFTER_MAX_S = 10
RETRY_AFTER_DEFAULT_S = 1
ATTEMPT_LIMIT = 5
MODEL_ID_MAX_LEN = 100

TRUNCATED_LABEL = "\n[truncated: reply exceeded 256 KB]"
INCOMPLETE_LABEL = " [incomplete]"

_MODEL_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")

_ASK_LOCK = threading.Lock()


class Refused(Exception):
    """Refusal before sending; the network layer never sees it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utf8_bytes(text: str) -> int:
    return len((text or "").encode("utf-8"))


def codepoints(text: str) -> int:
    return len(list(text or ""))


def validate_question(question: str) -> str:
    """Refuses a question over 2,000 code points or 8,000 bytes."""
    asked = question or ""
    if codepoints(asked) > QUESTION_MAX_CHARS:
        raise Refused("question", "question too long to send")
    if utf8_bytes(asked) > QUESTION_MAX_BYTES:
        raise Refused("question", "question too long to send")
    return asked


def validate_model(provider: str, model: str) -> str:
    """Typed Meta ID format check; open-weight ID fixed."""
    if provider == META:
        if not model or len(model) > MODEL_ID_MAX_LEN \
                or not _MODEL_RE.match(model):
            raise Refused("model", "model not available")
        return model
    if provider == BASETEN:
        if model != OPEN_WEIGHT_MODEL:
            raise Refused("model", "model not available")
        return model
    raise Refused("provider", "unknown provider")


def build_body(model: str, system: str, question: str,
               token_field: str, max_tokens: int) -> str:
    """Chat body: model, system + one user message, token field."""
    return json.dumps({"model": model,
                       "messages": [{"role": "system",
                                     "content": system},
                                    {"role": "user",
                                     "content": question}],
                       token_field: max_tokens},
                      ensure_ascii=False, separators=(",", ":"))


def build_request(provider: str, key: str, model: str, system: str,
                  question: str,
                  max_tokens: int = OUTPUT_MAX_TOKENS) -> dict:
    """Builds one request; the key only ever goes to its own host."""
    if provider not in PROVIDERS:
        raise Refused("provider", "unknown provider")
    if not key:
        raise Refused("no-key", "no key saved")
    validate_model(provider, model)
    asked = validate_question(question)
    if not isinstance(max_tokens, int) or max_tokens < 1 \
            or max_tokens > OUTPUT_MAX_TOKENS:
        raise Refused("output", "output cap exceeded")
    body = build_body(model, system, asked,
                      PROVIDERS[provider]["token_field"],
                      max_tokens)
    if utf8_bytes(body) > BODY_MAX_BYTES:
        raise Refused("body", "request body over 16 KB")
    return {"provider": provider,
            "host": PROVIDERS[provider]["host"],
            "path": PROVIDERS[provider]["path"],
            "auth": "Bearer " + key,
            "body": body,
            "model": model}


def parse_reply(body: str) -> dict:
    """Parses the shared reply shape; ok False when malformed."""
    blank = {"ok": False, "text": "", "finish": None,
             "has_usage": False, "prompt": -1, "completion": -1}
    try:
        root = json.loads(body or "")
    except (ValueError, TypeError):
        return blank
    if not isinstance(root, dict):
        return blank
    choices = root.get("choices")
    if not isinstance(choices, list) or not choices:
        return blank
    first = choices[0]
    if not isinstance(first, dict):
        return blank
    message = first.get("message")
    if not isinstance(message, dict):
        return blank
    content = message.get("content")
    if not isinstance(content, str):
        return blank
    finish = first.get("finish_reason")
    usage = root.get("usage")
    if not isinstance(usage, dict):
        return {"ok": True, "text": content,
                "finish": finish if isinstance(finish, str)
                else None,
                "has_usage": False, "prompt": -1,
                "completion": -1}
    return {"ok": True, "text": content,
            "finish": finish if isinstance(finish, str) else None,
            "has_usage": True,
            "prompt": _count(usage.get("prompt_tokens")),
            "completion": _count(usage.get("completion_tokens"))}


def _count(value) -> int:
    if isinstance(value, bool):
        return -1
    if isinstance(value, (int, float)):
        return int(value)
    return -1


def _billing(body: str) -> bool:
    low = (body or "").lower()
    return ("billing" in low or "payment" in low
            or "insufficient" in low or "invoice" in low)


def _quota(body: str) -> bool:
    return "quota" in (body or "").lower()


def decide(provider: str, model: str, status: int, body: str,
           reply: dict, retry_after: int) -> tuple:
    """Section 3 error table: (kind, message, wait seconds).

    Kind is answer, retry or fail. Messages never carry the body.
    Hard quota-exhausted errors never retry, even on 429.
    """
    shown = PROVIDERS[provider]["display"]
    if 200 <= status < 300:
        if not reply["ok"] or not reply["text"]:
            return ("fail", "No usable answer", 0)
        if reply["finish"] is not None \
                and reply["finish"] != "stop":
            return ("answer", reply["text"] + INCOMPLETE_LABEL, 0)
        return ("answer", reply["text"], 0)
    if status in (401, 403):
        return ("fail", "Key rejected by " + shown, 0)
    if status == 402 or _billing(body) or _quota(body):
        return ("fail", shown + " account needs payment", 0)
    if status in (400, 404) and model is not None \
            and body is not None and model in body:
        return ("fail", "Model not available", 0)
    if status == 429:
        if retry_after > RETRY_AFTER_MAX_S:
            return ("fail", "Too many requests, try later", 0)
        wait = retry_after if retry_after >= 0 \
            else RETRY_AFTER_DEFAULT_S
        return ("retry", "Too many requests, try later", wait)
    if status >= 500:
        return ("retry", shown + " not reachable", 0)
    return ("fail", shown + " not reachable", 0)


def utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CALLS_HEADER = (
    "# M4c calls (development log)\n"
    "\n"
    "Attempt limit per provider: 5. One row per send. States: "
    "planned (written before a send is allowed), sent (a reply or "
    "HTTP status came back), uncertain (a timeout, a dropped "
    "connection, or anything where the request may have reached "
    "the provider). Planned and uncertain both count as used "
    "until the owner reconciles them, and an uncertain row is "
    "never given back.\n"
    "\n"
    "| id | time | provider | model | where | status | prompt | "
    "completion | state |\n"
    "|---|---|---|---|---|---|---|---|---|\n")


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if "|" in text or "\n" in text:
        raise Refused("log", "log cell must not hold pipes")
    return text


class CallsLog:
    """Section 6 development log: counts sends, never money.

    Backed by docs/m4c-calls.md, one row per send: time,
    provider, model, where (codespace or phone), HTTP status,
    token counts from usage, state. Never holds a key,
    question, answer or money amount. Committed, so totals
    survive restarts. Codespace code writes planned before
    sending and refuses a sixth send per provider.
    """

    def __init__(self, path):
        self.path = str(path)
        self.rows = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                text = handle.read()
        except FileNotFoundError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != 9:
                continue
            if cells[0] == "id" or cells[0].startswith("---"):
                continue
            self.rows.append({"id": cells[0], "time": cells[1],
                              "provider": cells[2],
                              "model": cells[3],
                              "where": cells[4],
                              "status": cells[5],
                              "prompt": cells[6],
                              "completion": cells[7],
                              "state": cells[8]})

    def save(self):
        lines = [CALLS_HEADER.rstrip("\n")]
        for row in sorted(self.rows, key=lambda item: item["id"]):
            lines.append("| %s | %s | %s | %s | %s | %s | %s | "
                         "%s | %s |" % (
                             _cell(row["id"]), _cell(row["time"]),
                             _cell(row["provider"]),
                             _cell(row["model"]),
                             _cell(row["where"]),
                             _cell(row["status"]),
                             _cell(row["prompt"]),
                             _cell(row["completion"]),
                             _cell(row["state"])))
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def attempts_used(self, provider: str) -> int:
        return sum(1 for row in self.rows
                   if row["provider"] == provider)

    def _next_id(self) -> str:
        best = 0
        for row in self.rows:
            rid = row.get("id", "")
            if rid.startswith("c") and rid[1:].isdigit():
                best = max(best, int(rid[1:]))
        return "c%04d" % (best + 1)

    def _row(self, row_id: str) -> dict:
        for row in self.rows:
            if row["id"] == row_id:
                return row
        raise KeyError("no such send: " + row_id)

    def plan(self, provider: str, model: str, where: str,
             at: str = None) -> dict:
        """Writes one planned row; refuses a sixth send."""
        if provider not in PROVIDERS:
            raise Refused("provider", "unknown provider")
        if where not in ("codespace", "phone"):
            raise Refused("where", "where must be codespace/phone")
        validate_model(provider, model)
        if self.attempts_used(provider) >= ATTEMPT_LIMIT:
            raise Refused("limit", "attempt limit reached")
        row = {"id": self._next_id(), "time": at or utcnow(),
               "provider": provider, "model": model,
               "where": where, "status": "", "prompt": "",
               "completion": "", "state": "planned"}
        self.rows.append(row)
        self.save()
        return dict(row)

    def mark_sent(self, row_id: str, status=None, prompt: int = -1,
                  completion: int = -1) -> dict:
        row = self._row(row_id)
        row["state"] = "sent"
        row["status"] = "" if status is None else str(status)
        if prompt is None or prompt < 0:
            row["prompt"] = ""
        else:
            row["prompt"] = str(int(prompt))
        if completion is None or completion < 0:
            row["completion"] = ""
        else:
            row["completion"] = str(int(completion))
        self.save()
        return dict(row)

    def mark_uncertain(self, row_id: str, status=None,
                       prompt: int = -1,
                       completion: int = -1) -> dict:
        row = self._row(row_id)
        row["state"] = "uncertain"
        row["status"] = "" if status is None else str(status)
        if prompt is None or prompt < 0:
            row["prompt"] = ""
        else:
            row["prompt"] = str(int(prompt))
        if completion is None or completion < 0:
            row["completion"] = ""
        else:
            row["completion"] = str(int(completion))
        self.save()
        return dict(row)

    def delete_planned(self, row_id: str):
        """Deletes a planned row only when the send never happened."""
        row = self._row(row_id)
        if row["state"] != "planned":
            raise Refused("log", "only a planned row is deleted")
        self.rows = [item for item in self.rows
                     if item["id"] != row_id]
        self.save()

    def preflight_phone(self, provider: str, model: str,
                        at: str = None) -> list:
        """Owner preflight: two planned phone rows or refusal.

        One question plus its one possible retry need two sends;
        with four already used, the proof for that provider
        cannot start.
        """
        if self.attempts_used(provider) + 2 > ATTEMPT_LIMIT:
            raise Refused("preflight",
                          "phone proof would pass the limit")
        return [self.plan(provider, model, "phone", at=at),
                self.plan(provider, model, "phone", at=at)]

    def attempt(self, provider: str, model: str, system: str,
                question: str, key: str, network,
                where: str = "codespace",
                max_tokens: int = OUTPUT_MAX_TOKENS,
                timeout_s: float = TIMEOUT_S) -> dict:
        """One logged send through a network layer (tests: fake).

        Refusals happen before any row is written and before the
        network sees anything. Timeouts and dropped connections
        leave an uncertain row that still counts.
        """
        request = build_request(provider, key, model, system,
                                question, max_tokens)
        row = self.plan(provider, model, where)
        send = getattr(network, "send")
        try:
            reply = send(request, timeout_s)
        except Exception:
            self.mark_uncertain(row["id"])
            raise
        body = reply.get("body", "") if isinstance(reply, dict) \
            else ""
        status = reply.get("status") if isinstance(reply, dict) \
            else None
        after = reply.get("retry_after", -1) \
            if isinstance(reply, dict) else -1
        try:
            retry_after = int(after)
        except (TypeError, ValueError):
            retry_after = -1
        parsed = parse_reply(body)
        if parsed["ok"] and parsed["has_usage"]:
            updated = self.mark_sent(row["id"], status,
                                     parsed["prompt"],
                                     parsed["completion"])
        else:
            updated = self.mark_sent(row["id"], status)
        truncated = utf8_bytes(body) > REPLY_MAX_BYTES
        return {"row": updated, "reply": parsed, "status": status,
                "retry_after": retry_after, "body": body,
                "truncated": truncated, "request": request}


def _cancel(network):
    """Cancels and closes the in-flight request, if the layer
    offers it. Called on every "timed out" return, before the
    busy slot is released, so a stuck transport never keeps
    running into the next question."""
    cancel = getattr(network, "cancel", None)
    if callable(cancel):
        cancel()


def ask(provider: str, model: str, system: str, question: str,
        key: str, network, log: CallsLog,
        clock=None, sleeper=None,
        where: str = "codespace",
        max_tokens: int = OUTPUT_MAX_TOKENS) -> dict:
    """One question: at most two sends in 60 s, one at a time.

    One retry only for section 3 retryable errors (429 after
    its wait, 5xx, timeouts), never for hard refusals, never a
    third send. One 60-second deadline on a monotonic clock
    covers connecting, sending, reading the reply, the 429
    wait and the one retry: each send and each wait gets only
    the time left, a reply that arrives after the deadline is
    dropped, and when time runs out the transport is
    cancelled and closed and the answer is "timed out". No
    late answer is ever shown. A second question while one
    runs is refused before the network sees it.
    """
    if not _ASK_LOCK.acquire(blocking=False):
        raise Refused("busy", "another question is running")
    try:
        now = clock if callable(clock) else time.monotonic
        rest = sleeper if callable(sleeper) else time.sleep
        deadline = now() + TIMEOUT_S
        last_message = PROVIDERS.get(provider, {}).get(
            "display", provider) + " not reachable"
        for index in range(2):
            remaining = deadline - now()
            if remaining <= 0 and index > 0:
                _cancel(network)
                return {"kind": "fail",
                        "message": "timed out"}
            timeout_s = max(0.001, min(remaining, TIMEOUT_S))
            try:
                done = log.attempt(
                    provider, model, system, question, key,
                    network, where=where, max_tokens=max_tokens,
                    timeout_s=timeout_s)
            except Refused as refused:
                if refused.code in ("busy", "limit", "question",
                                    "body", "model", "output",
                                    "no-key", "provider"):
                    return {"kind": "refused",
                            "message": str(refused),
                            "code": refused.code}
                raise
            except Exception:
                if now() >= deadline:
                    _cancel(network)
                    return {"kind": "fail",
                            "message": "timed out"}
                if index == 0:
                    last_message = PROVIDERS[provider]["display"] \
                        + " not reachable"
                    continue
                return {"kind": "fail", "message": last_message}
            if now() >= deadline:
                _cancel(network)
                return {"kind": "fail",
                        "message": "timed out"}
            kind, message, wait = decide(
                provider, model, done["status"], done["body"],
                done["reply"], done["retry_after"])
            last_message = message
            if kind == "answer":
                if done["truncated"]:
                    message += TRUNCATED_LABEL
                return {"kind": "answer", "message": message}
            if kind != "retry" or index > 0:
                return {"kind": "fail", "message": message}
            rest(min(wait, max(0.0, deadline - now())))
        return {"kind": "fail", "message": last_message}
    finally:
        _ASK_LOCK.release()
