"""M2 Meta chat client: tool calling and structured output.

Standard library only. The key comes from MODEL_API_KEY and is never
logged or stored: records carry a redacted echo built here. Error
vocabulary mirrors M0 (stargate/doctor.py) so research can show the
same result codes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .doctor import META_API_BASE, META_INFERENCE_PATH, META_MODEL

META_TIMEOUT_SECONDS = 45.0
META_MAX_ATTEMPTS = 2  # initial attempt + 1 retry
META_RETRY_SLEEP_SECONDS = 1.0
CREDENTIAL_ENV_VAR = "MODEL_API_KEY"
REDACTED = "***"

# M0 result codes, mirrored.
AUTH_FAILED = "auth-failed"
QUOTA_OR_RATE_LIMITED = "quota-or-rate-limited"
NETWORK_ERROR = "network-error"
BAD_RESPONSE = "bad-response"
MODEL_NOT_AVAILABLE = "model-not-available"
MISSING_CREDENTIAL = "missing-credential"


class MetaError(Exception):
    """A Meta call failure carrying its M0 result code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def inference_url() -> str:
    return META_API_BASE + META_INFERENCE_PATH


def default_send(url: str, body: bytes, headers: dict,
                 timeout: float) -> tuple[int, bytes]:
    """POST with urllib. Returns (status, raw body)."""
    request = urllib.request.Request(
        url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except Exception:
            payload = b""
        return int(exc.code), payload


def _code_for_status(status: int) -> str | None:
    """Map a definitive HTTP status to a result code (None: parse body)."""
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        return AUTH_FAILED
    if status in (402, 429):
        return QUOTA_OR_RATE_LIMITED
    if status == 404:
        return MODEL_NOT_AVAILABLE
    if status == 408 or 500 <= status < 600:
        return NETWORK_ERROR
    return BAD_RESPONSE


def _error_detail(status: int, raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "HTTP %d, non-JSON body" % status
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return "HTTP %d: %s" % (status, str(error["message"])[:120])
        if isinstance(error, str) and error:
            return "HTTP %d: %s" % (status, error[:120])
    return "HTTP %d" % status


def _usage_of(payload: dict) -> dict:
    usage = {"completion_tokens": None, "reasoning_tokens": None,
             "prompt_tokens": None}
    block = payload.get("usage")
    if not isinstance(block, dict):
        return usage

    def _int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    usage["completion_tokens"] = _int(block.get("completion_tokens"))
    usage["prompt_tokens"] = _int(block.get("prompt_tokens"))
    details = block.get("completion_tokens_details")
    if isinstance(details, dict):
        usage["reasoning_tokens"] = _int(details.get("reasoning_tokens"))
    return usage


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments_raw: str
    arguments: dict | None


@dataclass
class ChatResult:
    content: str
    tool_calls: list
    finish_reason: str | None
    model_returned: str | None
    usage: dict
    attempts: int
    elapsed_ms: int
    request_echo: dict = field(default_factory=dict)


def _parse_tool_calls(message: dict) -> list:
    calls = []
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise MetaError(BAD_RESPONSE, "tool_calls is not a list")
    for entry in raw_calls:
        if not isinstance(entry, dict):
            raise MetaError(BAD_RESPONSE, "tool call is not an object")
        function = entry.get("function")
        if not isinstance(function, dict):
            raise MetaError(BAD_RESPONSE, "tool call lacks function")
        name = function.get("name")
        arguments_raw = function.get("arguments")
        call_id = entry.get("id")
        if not isinstance(name, str) or not name:
            raise MetaError(BAD_RESPONSE, "tool call lacks a name")
        if not isinstance(arguments_raw, str):
            raise MetaError(BAD_RESPONSE, "tool call lacks arguments")
        if not isinstance(call_id, str) or not call_id:
            raise MetaError(BAD_RESPONSE, "tool call lacks an id")
        try:
            arguments = json.loads(arguments_raw)
        except ValueError:
            arguments = None
        if arguments is not None and not isinstance(arguments, dict):
            arguments = None
        calls.append(ToolCall(call_id=call_id, name=name,
                              arguments_raw=arguments_raw,
                              arguments=arguments))
    return calls


def _parse_success(raw: bytes) -> tuple[str, list, str | None, str | None,
                                        dict]:
    """Validate a 2xx body. Returns (content, tool_calls, finish, model,
    usage). A tool call is valid even with empty content; otherwise the
    M0 non-empty rule applies."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise MetaError(BAD_RESPONSE, "HTTP 200 with invalid JSON")
    if not isinstance(payload, dict):
        raise MetaError(BAD_RESPONSE, "HTTP 200 with unexpected JSON shape")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MetaError(BAD_RESPONSE, "HTTP 200 with no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise MetaError(BAD_RESPONSE, "HTTP 200 with bad choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise MetaError(BAD_RESPONSE, "HTTP 200 with no message")
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict))
    if not isinstance(content, str):
        content = ""
    tool_calls = _parse_tool_calls(message)
    finish = first.get("finish_reason")
    if not tool_calls and content.strip() == "":
        detail = "HTTP 200 with empty answer"
        if finish:
            detail += " (finish_reason=%s)" % finish
        raise MetaError(BAD_RESPONSE, detail)
    model_returned = payload.get("model")
    if not isinstance(model_returned, str) or not model_returned:
        model_returned = None
    return content, tool_calls, finish, model_returned, _usage_of(payload)


def _read_key() -> str:
    import os

    value = os.environ.get(CREDENTIAL_ENV_VAR)
    if value is None or value.strip() == "":
        raise MetaError(MISSING_CREDENTIAL,
                        "env %s is unset or empty" % CREDENTIAL_ENV_VAR)
    return value


def chat(messages: list,
         *,
         tools: list | None = None,
         tool_choice: object = None,
         response_format: dict | None = None,
         max_completion_tokens: int,
         timeout: float = META_TIMEOUT_SECONDS,
         send=default_send,
         sleep=time.sleep,
         before_attempt=None) -> ChatResult:
    """One chat call with one retry. Raises MetaError on failure.

    before_attempt, when given, runs before each attempt (initial and
    retry); its exceptions propagate so budgets can abort the call."""
    key = _read_key()
    payload: dict = {"model": META_MODEL,
                     "messages": messages,
                     "max_completion_tokens": max_completion_tokens}
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if response_format is not None:
        payload["response_format"] = response_format
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + key}
    echo = {"url": inference_url(),
            "headers": {"Content-Type": "application/json",
                        "Authorization": "Bearer " + REDACTED},
            "body": payload}
    attempts = 0
    total_ms = 0
    while attempts < META_MAX_ATTEMPTS:
        if before_attempt is not None:
            before_attempt()
        attempts += 1
        start = time.monotonic()
        try:
            status, raw = send(inference_url(), body, headers, timeout)
        except Exception as exc:  # timeout, DNS, refused, TLS, ...
            total_ms += int((time.monotonic() - start) * 1000)
            if attempts < META_MAX_ATTEMPTS:
                sleep(META_RETRY_SLEEP_SECONDS)
                continue
            raise MetaError(NETWORK_ERROR, str(exc) or type(exc).__name__)
        total_ms += int((time.monotonic() - start) * 1000)
        code = _code_for_status(status)
        if code is None:
            try:
                parsed = _parse_success(raw)
            except MetaError as exc:
                if exc.code == BAD_RESPONSE and attempts < META_MAX_ATTEMPTS \
                        and exc.detail.startswith("HTTP 200 with empty"):
                    sleep(META_RETRY_SLEEP_SECONDS)
                    continue
                raise
            content, calls, finish, model, usage = parsed
            return ChatResult(content=content, tool_calls=calls,
                              finish_reason=finish, model_returned=model,
                              usage=usage, attempts=attempts,
                              elapsed_ms=total_ms, request_echo=echo)
        if code in (NETWORK_ERROR, QUOTA_OR_RATE_LIMITED):
            if attempts < META_MAX_ATTEMPTS:
                sleep(META_RETRY_SLEEP_SECONDS)
                continue
        raise MetaError(code, _error_detail(status, raw))
    raise MetaError(NETWORK_ERROR, "exhausted retries")
