"""M1 read-only probe: allowlist, refusal, limits, redaction, transports.

Standard library only. No phone, adb, USB or fastboot is used in M1:
M1 ships only a simulated transport that replays canned outputs. The real
adb transport is a stub that refuses without starting any process.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_PER_COMMAND_SECONDS = 5.0
MAX_OUTPUT_BYTES_PER_COMMAND = 64 * 1024
TOTAL_SCAN_SECONDS = 60.0
MAX_CONFIG_GZ_DECOMPRESSED_BYTES = 1024 * 1024
MAX_EXCERPT_CHARS = 200

SIMULATED_HEADER_PREFIX = "SIMULATED"

# Fixed allowlist of exact commands. Each reads one named property or file.
# No full `getprop` dump (it would include the serial number).
ALLOWLIST: tuple[str, ...] = (
    "getprop ro.product.manufacturer",
    "getprop ro.product.model",
    "getprop ro.product.device",
    "getprop ro.build.version.release",
    "getprop ro.build.version.sdk",
    "getprop ro.product.board",
    "getprop ro.boot.vbmeta.device_state",
    "getprop ro.oem_unlock_supported",
    "getprop sys.oem_unlock_allowed",
    "getprop ro.boot.verifiedbootstate",
    "getprop ro.build.ab_update",
    "getprop ro.boot.dynamic_partitions",
    "getprop ro.treble.enabled",
    "getprop ro.vndk.version",
    "getprop ro.product.cpu.abilist",
    "cat /proc/meminfo",
    "cat /proc/cpuinfo",
    "df /data",
    "cat /proc/version",
    "cat /proc/config.gz",
)
ALLOWLIST_SET = frozenset(ALLOWLIST)

PROBE_ID_FOR_COMMAND: dict[str, str] = {
    "getprop ro.product.manufacturer": "getprop-ro.product.manufacturer",
    "getprop ro.product.model": "getprop-ro.product.model",
    "getprop ro.product.device": "getprop-ro.product.device",
    "getprop ro.build.version.release": "getprop-ro.build.version.release",
    "getprop ro.build.version.sdk": "getprop-ro.build.version.sdk",
    "getprop ro.product.board": "getprop-ro.product.board",
    "getprop ro.boot.vbmeta.device_state": "getprop-ro.boot.vbmeta.device_state",
    "getprop ro.oem_unlock_supported": "getprop-ro.oem_unlock_supported",
    "getprop sys.oem_unlock_allowed": "getprop-sys.oem_unlock_allowed",
    "getprop ro.boot.verifiedbootstate": "getprop-ro.boot.verifiedbootstate",
    "getprop ro.build.ab_update": "getprop-ro.build.ab_update",
    "getprop ro.boot.dynamic_partitions": "getprop-ro.boot.dynamic_partitions",
    "getprop ro.treble.enabled": "getprop-ro.treble.enabled",
    "getprop ro.vndk.version": "getprop-ro.vndk.version",
    "getprop ro.product.cpu.abilist": "getprop-ro.product.cpu.abilist",
    "cat /proc/meminfo": "cat-proc-meminfo",
    "cat /proc/cpuinfo": "cat-proc-cpuinfo",
    "df /data": "df-data",
    "cat /proc/version": "cat-proc-version",
    "cat /proc/config.gz": "cat-proc-config-gz",
}

# Shell metacharacters refused before the transport is called.
_METACHARACTERS = (";", "|", "&", ">", "<", "`", "$(", "\n", "\r", "\0")

_IMEI_LIKE = re.compile("[0-9]{14,19}")
# Phone-like numbers: plus-led with separators (e.g. +44 7700 900123), or
# 0-led runs of 10+ digits (e.g. 07700900123). Bare counts never start with
# 0 or +, so meminfo/df lines and byte counts do not match.
_PHONE_PLUS = re.compile(r"\+\d(?:[ \-.]?\d){6,}")
_PHONE_ZERO_LEAD = re.compile(r"\b0\d(?:[ \-.]?\d){8,}\b")
_SENSITIVE_WORDS = (
    "serial",
    "serialno",
    "imei",
    "meid",
    "imsi",
    "account",
    "ssid",
    "bssid",
)

REDACTED = "***"
REDACTED_LINE = "[redacted]"


class RefusedCommand(Exception):
    """Raised when a command is not on the allowlist or has metacharacters."""


class RealDevicesNotEnabled(Exception):
    """Raised by the stub real transport (M1: simulated only)."""


class ProbeTimeout(Exception):
    """Raised when a probe hits its time limit."""


class ProbeOutputTooLarge(Exception):
    """Raised when a probe returns more than the byte limit."""


@dataclass
class TransportResult:
    stdout: bytes
    stderr: str
    exit_code: int
    timed_out: bool = False


def check_command(command: object) -> tuple[bool, str]:
    """Check a command against the refusal rules (before any transport call).

    Returns (allowed, reason). Anything not on the allowlist exactly is
    refused, as is anything containing shell metacharacters.
    """
    if not isinstance(command, str) or command == "":
        return False, "empty command"
    for token in _METACHARACTERS:
        if token in command:
            return False, "shell metacharacters are not allowed"
    if command not in ALLOWLIST_SET:
        return False, "command is not on the allowlist"
    return True, "allowed"


def _has_sensitive_word(text: str) -> bool:
    lowered = text.lower()
    for word in _SENSITIVE_WORDS:
        start = 0
        while True:
            index = lowered.find(word, start)
            if index < 0:
                break
            before = lowered[index - 1] if index > 0 else " "
            after_index = index + len(word)
            after = lowered[after_index] if after_index < len(lowered) else " "
            if not before.isalnum() and before != "_" and not after.isalnum() \
                    and after != "_":
                return True
            start = index + 1
    return False


def redact_text(text: str | None) -> str | None:
    """Redact secrets and serial-like values from text.

    Runs on values, evidence excerpts and error messages. Never prints the
    Meta key: it is read from the environment only to replace it.
    """
    if text is None or text == "":
        return text
    out = text
    try:
        secret = os.environ.get("MODEL_API_KEY")
    except Exception:
        secret = None
    if secret:
        out = out.replace(secret, REDACTED)
    out = _IMEI_LIKE.sub(REDACTED, out)
    out = _PHONE_PLUS.sub(REDACTED, out)
    out = _PHONE_ZERO_LEAD.sub(REDACTED, out)
    if _has_sensitive_word(out):
        return REDACTED_LINE
    return out


def make_excerpt(raw: str) -> str:
    """Build a single-line evidence excerpt (redacted, at most 200 chars)."""
    first = raw.splitlines()[0] if raw.splitlines() else ""
    cleaned = first.strip()
    redacted = redact_text(cleaned) or ""
    if len(redacted) > MAX_EXCERPT_CHARS:
        return redacted[:MAX_EXCERPT_CHARS]
    return redacted


class Transport:
    """Transport interface: runs one allowlisted command."""

    def run(self, command: str, timeout_seconds: float) -> TransportResult:
        raise NotImplementedError


class SimulatedTransport(Transport):
    """Replays canned outputs from a fixture folder (offline, no processes)."""

    def __init__(self, fixture_dir: str | Path) -> None:
        self.fixture_dir = Path(fixture_dir)
        self.calls: list[str] = []

    def _load(self) -> dict:
        target = self.fixture_dir / "outputs.txt"
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError("fixture missing: %s (%s)" % (target, exc))
        lines = raw.splitlines()
        if not lines or not lines[0].startswith(SIMULATED_HEADER_PREFIX):
            raise ValueError("fixture %s lacks a SIMULATED header" % target)
        body = "\n".join(lines[1:]).strip()
        if not body:
            return {}
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ValueError("fixture %s is not valid JSON: %s" % (target, exc))
        if not isinstance(payload, dict):
            raise ValueError("fixture %s must hold an object" % target)
        return payload

    def run(self, command: str, timeout_seconds: float) -> TransportResult:
        self.calls.append(command)
        payload = self._load()
        entry = payload.get(command)
        if entry is None:
            return TransportResult(
                stdout=b"",
                stderr="fixture has no canned output for %r" % command,
                exit_code=1,
                timed_out=False,
            )
        if not isinstance(entry, dict):
            return TransportResult(
                stdout=b"",
                stderr="fixture entry for %r is malformed" % command,
                exit_code=1,
                timed_out=False,
            )
        if entry.get("timeout") is True:
            return TransportResult(
                stdout=b"", stderr="", exit_code=124, timed_out=True
            )
        stdout_text = entry.get("stdout", "")
        stderr_text = entry.get("stderr", "")
        exit_code = entry.get("exit", 0)
        if not isinstance(stdout_text, str):
            stdout_text = str(stdout_text)
        if not isinstance(stderr_text, str):
            stderr_text = str(stderr_text)
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = 1
        return TransportResult(
            stdout=stdout_text.encode("utf-8", "replace"),
            stderr=stderr_text,
            exit_code=exit_code,
            timed_out=False,
        )


ADB_LIST_TIMEOUT_SECONDS = 10.0
_SERIAL_RE = re.compile(r"[A-Za-z0-9._:\-]{1,64}\Z")


class RealAdbTransport(Transport):
    """Read-only transport to one real phone through `adb shell`.

    Without a serial it refuses, as it always has. With a serial it
    runs exactly one allowlisted command per call: run_probe() has
    already refused anything that is not on the allowlist, and the
    command is passed as a single argument (no local shell).
    """

    def __init__(self, serial: str | None = None, adb_path: str = "adb",
                 runner=None) -> None:
        self.serial = serial
        self.adb_path = adb_path
        self.runner = runner

    def run(self, command: str, timeout_seconds: float) -> TransportResult:
        if not self.serial:
            raise RealDevicesNotEnabled(
                "no phone chosen: real scans need an explicit device")
        if not _SERIAL_RE.match(self.serial):
            raise RealDevicesNotEnabled("device id is not valid")
        if command not in ALLOWLIST_SET:
            raise RefusedCommand("%s: not on the allowlist" % command)
        import subprocess
        runner = self.runner or subprocess.run
        try:
            done = runner(
                [self.adb_path, "-s", self.serial, "shell", command],
                capture_output=True, timeout=timeout_seconds,
                check=False)
        except subprocess.TimeoutExpired:
            return TransportResult(stdout=b"", stderr="", exit_code=124,
                                   timed_out=True)
        except OSError as exc:
            return TransportResult(stdout=b"", stderr="adb failed: %s"
                                   % exc, exit_code=127)
        stderr = done.stderr.decode("utf-8", "replace") \
            if isinstance(done.stderr, bytes) else str(done.stderr or "")
        return TransportResult(stdout=done.stdout or b"", stderr=stderr,
                               exit_code=done.returncode)


def list_adb_devices(adb_path: str = "adb", runner=None) -> tuple:
    """Phones adb can see. Returns (devices, error_text).

    Each device is {"serial", "state", "model"}. Reads only; starts
    nothing on any phone.
    """
    import subprocess
    runner = runner or subprocess.run
    try:
        done = runner([adb_path, "devices", "-l"], capture_output=True,
                      timeout=ADB_LIST_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError:
        return [], "adb is not installed on this computer"
    except subprocess.TimeoutExpired:
        return [], "adb did not answer in time"
    except OSError as exc:
        return [], "adb failed: %s" % exc
    if done.returncode != 0:
        return [], "adb failed (exit %d)" % done.returncode
    text = done.stdout.decode("utf-8", "replace") \
        if isinstance(done.stdout, bytes) else str(done.stdout or "")
    devices = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or line.startswith("List of devices") \
                or line.startswith("*"):
            continue
        serial, state = parts[0], parts[1]
        if not _SERIAL_RE.match(serial):
            continue
        model = ""
        for part in parts[2:]:
            if part.startswith("model:"):
                model = part[len("model:"):].replace("_", " ")
        devices.append({"serial": serial, "state": state,
                        "model": model})
    return devices, ""


def run_probe(
    transport: Transport,
    command: str,
    timeout_seconds: float = TIMEOUT_PER_COMMAND_SECONDS,
    max_bytes: int = MAX_OUTPUT_BYTES_PER_COMMAND,
) -> TransportResult:
    """Run one command after allowlist refusal; enforces numeric limits.

    Refusal happens before the transport is called. A timeout becomes
    ProbeTimeout and oversize output becomes ProbeOutputTooLarge.
    """
    allowed, reason = check_command(command)
    if not allowed:
        raise RefusedCommand("%s: %s" % (command, reason))
    start = time.monotonic()
    result = transport.run(command, timeout_seconds)
    elapsed = time.monotonic() - start
    if result.timed_out or elapsed > timeout_seconds:
        raise ProbeTimeout(command)
    if len(result.stdout) > max_bytes:
        raise ProbeOutputTooLarge(command)
    return result
