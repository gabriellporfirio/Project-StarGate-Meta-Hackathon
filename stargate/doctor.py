"""M0 health check: interpreter, plan pin, credential, Meta inference.

Standard library only. The Meta API key is read from the MODEL_API_KEY
environment variable and is never printed, logged, or written anywhere:
the whole report passes through :func:`redact` before it is returned.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The approved plan commit this workspace was built from (M0 Part B).
PLAN_COMMIT = "b498e7037e1e064fd6db8ff73687ecc5a81e449b"
PLAN_REPO_ENV_VAR = "STARGATE_PLAN_REPO"
PLAN_REPO_DEFAULT = "/workspaces/Project-Stargate"

# Meta Model API (checked against dev.meta.ai docs; see docs/decisions.md).
META_API_BASE = "https://api.meta.ai/v1"
META_INFERENCE_PATH = "/chat/completions"
META_MODEL = "muse-spark-1.3"
META_TIMEOUT_SECONDS = 20.0
META_MAX_ATTEMPTS = 2  # initial attempt + 1 retry
META_RETRY_SLEEP_SECONDS = 1.0
META_MAX_COMPLETION_TOKENS = 1024
META_INFERENCE_PROMPT = "Reply with the single word ok."

CREDENTIAL_ENV_VAR = "MODEL_API_KEY"
BASENTEN_KEY_VAR = "BASETEN_API_KEY"
BASENTEN_MODEL_VAR = "BASETEN_MODEL"

MIN_PYTHON = (3, 12)

# Result codes for the Meta inference check (spec B3).
OK = "ok"
MISSING_CREDENTIAL = "missing-credential"
AUTH_FAILED = "auth-failed"
QUOTA_OR_RATE_LIMITED = "quota-or-rate-limited"
NETWORK_ERROR = "network-error"
BAD_RESPONSE = "bad-response"
MODEL_NOT_AVAILABLE = "model-not-available"

EXIT_CODES = {
    OK: 0,
    MISSING_CREDENTIAL: 2,
    AUTH_FAILED: 3,
    QUOTA_OR_RATE_LIMITED: 4,
    NETWORK_ERROR: 5,
    BAD_RESPONSE: 6,
    MODEL_NOT_AVAILABLE: 7,
}
EXIT_INTERPRETER = 20
EXIT_PLAN_PIN = 21

REDACTED = "***"
MAX_DETAIL_CHARS = 200


def redact(text: str, secret: str | None) -> str:
    """Replace every occurrence of the secret with *** (no-op if unset)."""
    if not text or not secret:
        return text
    return text.replace(secret, REDACTED)


def _shorten(text: object, limit: int = MAX_DETAIL_CHARS) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def check_interpreter(
    version: tuple[int, ...] | None = None,
) -> tuple[bool, str]:
    """Check the interpreter is Python 3.12+."""
    if version is None:
        version = (
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        )
    ok = (version[0], version[1]) >= MIN_PYTHON
    detail = "Python %s at %s (requires %d.%d+)" % (
        ".".join(str(v) for v in version),
        sys.executable,
        MIN_PYTHON[0],
        MIN_PYTHON[1],
    )
    return ok, detail


def verify_plan_pin(
    recorded: str, observed_head: str | None, clean: bool
) -> tuple[bool, str]:
    """Pure check: the observed checkout matches the pin and is clean."""
    if observed_head is None:
        return False, "plan checkout not found"
    if observed_head != recorded:
        return False, "HEAD %s does not match pin %s" % (observed_head, recorded)
    if not clean:
        return False, "checkout is dirty"
    return True, "HEAD %s, clean" % observed_head


def default_repo_path() -> str:
    return os.environ.get(PLAN_REPO_ENV_VAR) or PLAN_REPO_DEFAULT


def gather_plan_pin(repo_path: str | None = None) -> dict:
    """Read HEAD + cleanliness of the plan checkout (local git, no network)."""
    path = repo_path or default_repo_path()
    result: dict = {"path": path, "head": None, "clean": False, "error": None}
    if not os.path.isdir(path):
        result["error"] = "directory not found"
        return result
    try:
        head = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        result["error"] = "git executable not found"
        return result
    except (subprocess.SubprocessError, OSError) as exc:
        result["error"] = "git rev-parse failed: %s" % _shorten(exc)
        return result
    if head.returncode != 0:
        result["error"] = "git rev-parse failed: %s" % _shorten(head.stderr)
        return result
    result["head"] = head.stdout.strip()
    try:
        status = subprocess.run(
            ["git", "-C", path, "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        result["error"] = "git status failed: %s" % _shorten(exc)
        return result
    if status.returncode != 0:
        result["error"] = "git status failed: %s" % _shorten(status.stderr)
        return result
    result["clean"] = status.stdout.strip() == ""
    return result


def get_credential() -> str | None:
    """Return the Meta API key, or None when unset/empty. Never prints it."""
    value = os.environ.get(CREDENTIAL_ENV_VAR)
    if value is None or value.strip() == "":
        return None
    return value


class NetworkFailure(Exception):
    """Raised when no HTTP response was received at all."""


def default_send(
    url: str, body: bytes, headers: dict, timeout: float
) -> tuple[int, bytes]:
    """POST with urllib. Returns (status, raw body); raises NetworkFailure."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except Exception:
            payload = b""
        return int(exc.code), payload
    except Exception as exc:  # timeout, DNS, refused, reset, TLS, ...
        raise NetworkFailure(str(exc) or type(exc).__name__)


def _code_for_status(status: int) -> str | None:
    """Map a definitive HTTP status to a result code; None means parse body."""
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
    return BAD_RESPONSE  # 400 and any other unexpected status


def _retryable(code: str) -> bool:
    return code in (NETWORK_ERROR, QUOTA_OR_RATE_LIMITED)


def _error_detail(status: int, raw: bytes) -> str:
    """Short, safe detail for a non-2xx response (truncated, redacted later)."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        text = raw[:120].decode("utf-8", "replace")
        return "HTTP %d, non-JSON body: %s" % (status, _shorten(text, 120))
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return "HTTP %d: %s" % (status, _shorten(error["message"]))
        if isinstance(error, str) and error:
            return "HTTP %d: %s" % (status, _shorten(error))
    return "HTTP %d" % status


def _no_usage() -> dict:
    return {"completion_tokens": None, "reasoning_tokens": None}


def _usage_of(payload: dict) -> dict:
    """Pull token counts from a response envelope (None when absent)."""
    usage = _no_usage()
    block = payload.get("usage")
    if not isinstance(block, dict):
        return usage
    completion = block.get("completion_tokens")
    if isinstance(completion, int) and not isinstance(completion, bool):
        usage["completion_tokens"] = completion
    details = block.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and not isinstance(reasoning, bool):
            usage["reasoning_tokens"] = reasoning
    return usage


def _parse_success(raw: bytes) -> tuple[str, str | None, str, dict]:
    """Validate a 2xx inference body.

    Returns (code, model_returned, detail, usage); usage carries the
    completion/reasoning token counts when the response includes them.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return BAD_RESPONSE, None, "HTTP 200 with invalid JSON", _no_usage()
    if not isinstance(payload, dict):
        return (
            BAD_RESPONSE,
            None,
            "HTTP 200 with unexpected JSON shape",
            _no_usage(),
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return BAD_RESPONSE, None, "HTTP 200 with no choices", _no_usage()
    first = choices[0]
    message = first.get("message", {}) if isinstance(first, dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, list):  # content-block form: join text parts
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str) or content.strip() == "":
        detail = "HTTP 200 with empty answer"
        reason = first.get("finish_reason") if isinstance(first, dict) else None
        if reason:
            detail += " (finish_reason=%s)" % reason
        usage = _usage_of(payload) if isinstance(payload, dict) else _no_usage()
        return BAD_RESPONSE, None, detail, usage
    model_returned = payload.get("model")
    if not isinstance(model_returned, str) or not model_returned:
        model_returned = None
    return OK, model_returned, "valid non-empty answer", _usage_of(payload)


def inference_url() -> str:
    return META_API_BASE + META_INFERENCE_PATH


def run_inference(
    key: str,
    model: str = META_MODEL,
    send=default_send,
    sleep=time.sleep,
) -> dict:
    """One inference request (+1 retry on transient failures)."""
    url = inference_url()
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": META_INFERENCE_PROMPT}],
            "max_completion_tokens": META_MAX_COMPLETION_TOKENS,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    attempts = 0
    last_code = NETWORK_ERROR
    detail = "no attempts made"
    model_returned = None
    usage = _no_usage()
    start = time.monotonic()
    while attempts < META_MAX_ATTEMPTS:
        attempts += 1
        try:
            status, raw = send(url, body, headers, META_TIMEOUT_SECONDS)
        except NetworkFailure as exc:
            last_code = NETWORK_ERROR
            detail = "request failed: %s" % _shorten(exc)
        except Exception as exc:  # a transport problem still maps cleanly
            last_code = NETWORK_ERROR
            detail = "request failed: %s" % _shorten(
                "%s: %s" % (type(exc).__name__, exc)
            )
        else:
            mapped = _code_for_status(status)
            if mapped is None:
                last_code, model_returned, detail, usage = _parse_success(raw)
            else:
                last_code = mapped
                detail = _error_detail(status, raw)
        if last_code == OK or not _retryable(last_code):
            break
        if attempts < META_MAX_ATTEMPTS:
            sleep(META_RETRY_SLEEP_SECONDS)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {
        "code": last_code,
        "endpoint": url,
        "model_requested": model,
        "model_returned": model_returned,
        "attempts": attempts,
        "detail": detail,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
    }


def git_info() -> str:
    try:
        proc = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return "not available"
    if proc.returncode != 0:
        return "not available"
    return proc.stdout.strip() or "not available"


def disk_info(path: str = "/") -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return "not available (%s)" % _shorten(exc)
    free_gb = usage.free / (1024**3)
    return "%.1f GB free at %s" % (free_gb, path)


def baseten_info() -> str:
    key = os.environ.get(BASENTEN_KEY_VAR)
    model = os.environ.get(BASENTEN_MODEL_VAR)
    if key and key.strip() and model and model.strip():
        return "configured (connectivity not checked in M0)"
    return "not configured"


def _format_usage(usage: dict) -> str:
    """Render ', N completion tokens (M reasoning)' or '' when unknown."""
    completion = usage.get("completion_tokens")
    if not isinstance(completion, int) or isinstance(completion, bool):
        return ""
    text = ", %d completion tokens" % completion
    reasoning = usage.get("reasoning_tokens")
    if isinstance(reasoning, int) and not isinstance(reasoning, bool):
        text += " (%d reasoning)" % reasoning
    return text


def run_doctor(send=None, sleep=None) -> tuple[int, str]:
    """Run every check and render the report. Returns (exit_code, report)."""
    if send is None:
        send = default_send
    if sleep is None:
        sleep = time.sleep
    lines = ["StarGate doctor (M0)"]
    key = get_credential()

    interp_ok, interp_detail = check_interpreter()
    lines.append(
        "Python: %s -- %s" % (interp_detail, "ok" if interp_ok else "FAIL")
    )

    plan = gather_plan_pin()
    if plan["error"] is None:
        plan_ok, plan_detail = verify_plan_pin(
            PLAN_COMMIT, plan["head"], plan["clean"]
        )
    else:
        plan_ok, plan_detail = False, plan["error"]
    lines.append(
        "Plan pin: %s @ %s -- %s"
        % (
            PLAN_COMMIT,
            plan["path"],
            ("ok (%s)" % plan_detail) if plan_ok else ("FAIL (%s)" % plan_detail),
        )
    )

    if key is None:
        lines.append(
            "Meta credential: missing (env %s is unset or empty) -- FAIL"
            % CREDENTIAL_ENV_VAR
        )
        inference = {
            "code": MISSING_CREDENTIAL,
            "endpoint": inference_url(),
            "model_requested": META_MODEL,
            "model_returned": None,
            "attempts": 0,
            "detail": "env %s is unset or empty" % CREDENTIAL_ENV_VAR,
            "elapsed_ms": 0,
            "usage": _no_usage(),
        }
    else:
        lines.append("Meta credential: present (env %s) -- ok" % CREDENTIAL_ENV_VAR)
        inference = run_inference(key, send=send, sleep=sleep)

    code = inference["code"]
    if code == OK:
        returned = inference["model_returned"] or "not reported by API"
        usage_text = _format_usage(inference.get("usage") or {})
        lines.append(
            "Meta inference: ok -- endpoint %s, model requested %s, "
            "model returned %s (%d attempt(s), %d ms%s)"
            % (
                inference["endpoint"],
                inference["model_requested"],
                returned,
                inference["attempts"],
                inference["elapsed_ms"],
                usage_text,
            )
        )
    else:
        lines.append(
            "Meta inference: %s -- endpoint %s, model requested %s "
            "(%d attempt(s)): %s"
            % (
                code,
                inference["endpoint"],
                inference["model_requested"],
                inference["attempts"],
                inference["detail"],
            )
        )

    lines.append("git: %s (report-only)" % git_info())
    lines.append("disk: %s (report-only)" % disk_info())
    lines.append("Baseten: %s (report-only)" % baseten_info())

    if not interp_ok:
        exit_code, overall = EXIT_INTERPRETER, "interpreter-unsupported"
    elif not plan_ok:
        exit_code, overall = EXIT_PLAN_PIN, "plan-pin-failed"
    else:
        exit_code, overall = EXIT_CODES[code], code
    lines.append("Result: %s" % overall)
    report = "\n".join(lines) + "\n"
    return exit_code, redact(report, key)
