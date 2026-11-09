"""M1 `stargate scan`: simulated profiles and plain-English reports.

Standard library only. Scans are offline: they read canned fixture files
and make no network calls. No conclusions about whether an OS image fits
are printed or stored here; that is M3's job.
"""
from __future__ import annotations

import datetime
import json
import time
import zlib
from pathlib import Path

from . import inference as inference_module
from . import probe as probe_module
from .probe import (
    PROBE_ID_FOR_COMMAND,
    TOTAL_SCAN_SECONDS,
    ProbeOutputTooLarge,
    ProbeTimeout,
    RealDevicesNotEnabled,
    RefusedCommand,
    SimulatedTransport,
    TransportResult,
    make_excerpt,
    redact_text,
)
from .profile import ALL_FACT_IDS, validate_profile

KNOWN_FIXTURES = ("budget", "flagship", "old32", "locked", "relic")

CONFIG_SYMBOLS = {
    "kernel.config.tun": "CONFIG_TUN",
    "kernel.config.overlay_fs": "CONFIG_OVERLAY_FS",
    "kernel.config.ext4": "CONFIG_EXT4_FS",
    "kernel.config.exfat": "CONFIG_EXFAT_FS",
}

RUNTIME_FACTS = (
    "runtime.tun_usable",
    "runtime.overlay_fs_usable",
    "runtime.ext4_usable",
    "runtime.exfat_usable",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def fixtures_dir() -> Path:
    return _repo_root() / "fixtures"


def utc_now_z() -> str:
    moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _decode_stdout(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _is_permission_error(stderr: str) -> bool:
    return "permission denied" in stderr.lower()


def _is_missing_file(stderr: str) -> bool:
    lowered = stderr.lower()
    return "no such file" in lowered or "not found" in lowered


def _measured(
    value: object,
    unit: object,
    command: str,
    excerpt_raw: str,
    observed_at: str,
) -> dict:
    return {
        "status": "measured",
        "value": value,
        "unit": unit,
        "evidence": [
            {
                "probe_id": PROBE_ID_FOR_COMMAND[command],
                "command": command,
                "excerpt": make_excerpt(excerpt_raw),
                "observed_at": observed_at,
            }
        ],
    }


def _unknown(unit: object, reason: str) -> dict:
    return {"status": "unknown", "value": None, "unit": unit,
            "unknown_reason": reason}


def _parse_bool_text(text: str) -> bool | None:
    lowered = text.strip().lower()
    if lowered in ("1", "true", "yes"):
        return True
    if lowered in ("0", "false", "no"):
        return False
    return None


def _getprop_string(
    command: str,
    result: TransportResult,
    observed_at: str,
    *,
    not_exposed: bool = False,
) -> dict:
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            reason = "not_exposed" if not_exposed else "permission_denied"
            return _unknown(None, reason)
        if _is_missing_file(result.stderr):
            return _unknown(None, "property_missing")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip()
    if text == "":
        return _unknown(None, "property_missing")
    line = text.splitlines()[0].strip()
    value = redact_text(line)
    if not isinstance(value, str) or value.strip() == "":
        return _unknown(None, "malformed_output")
    return _measured(value, None, command, line, observed_at)


def _getprop_int(command: str, result: TransportResult, observed_at: str,
                 *, minimum: int) -> dict:
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown(None, "permission_denied")
        if _is_missing_file(result.stderr):
            return _unknown(None, "property_missing")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip()
    if text == "":
        return _unknown(None, "property_missing")
    try:
        number = int(text.split()[0], 10)
    except (ValueError, IndexError):
        return _unknown(None, "malformed_output")
    if number < minimum:
        return _unknown(None, "malformed_output")
    return _measured(number, None, command, text.splitlines()[0], observed_at)


def _getprop_bool(command: str, result: TransportResult, observed_at: str,
                  *, not_exposed: bool = False) -> dict:
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            reason = "not_exposed" if not_exposed else "permission_denied"
            return _unknown(None, reason)
        if _is_missing_file(result.stderr):
            return _unknown(None, "property_missing")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip()
    if text == "":
        return _unknown(None, "property_missing")
    parsed = _parse_bool_text(text)
    if parsed is None:
        return _unknown(None, "malformed_output")
    return _measured(parsed, None, command, text.splitlines()[0], observed_at)


def _verified_boot(result: TransportResult, observed_at: str) -> dict:
    command = "getprop ro.boot.verifiedbootstate"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown(None, "permission_denied")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip().lower()
    if text == "":
        return _unknown(None, "property_missing")
    if text in ("green", "yellow", "orange", "red"):
        return _measured(text, None, command,
                         _decode_stdout(result.stdout).splitlines()[0],
                         observed_at)
    return _unknown(None, "malformed_output")


def _cpu_abis(result: TransportResult, observed_at: str) -> dict:
    command = "getprop ro.product.cpu.abilist"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown(None, "permission_denied")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip()
    if text == "":
        return _unknown(None, "property_missing")
    abis = [part.strip() for part in text.split(",")]
    abis = [part for part in abis if part]
    if not abis:
        return _unknown(None, "malformed_output")
    clean = [redact_text(part) for part in abis]
    if any(not isinstance(part, str) or part.strip() == "" for part in clean):
        return _unknown(None, "malformed_output")
    return _measured(clean, None, command, text.splitlines()[0], observed_at)


def _ram_total(result: TransportResult, observed_at: str) -> dict:
    command = "cat /proc/meminfo"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown("bytes", "permission_denied")
        return _unknown("bytes", "malformed_output")
    text = _decode_stdout(result.stdout)
    if text.strip() == "":
        return _unknown("bytes", "property_missing")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("MemTotal:"):
            parts = stripped.split()
            if len(parts) >= 3 and parts[2].lower().startswith("kb"):
                try:
                    kilobytes = int(parts[1], 10)
                except ValueError:
                    return _unknown("bytes", "malformed_output")
                if kilobytes < 0:
                    return _unknown("bytes", "malformed_output")
                return _measured(kilobytes * 1024, "bytes", command, line,
                                 observed_at)
            return _unknown("bytes", "malformed_output")
    return _unknown("bytes", "malformed_output")


def _cpu_cores(result: TransportResult, observed_at: str) -> dict:
    command = "cat /proc/cpuinfo"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown("count", "permission_denied")
        return _unknown("count", "malformed_output")
    text = _decode_stdout(result.stdout)
    if text.strip() == "":
        return _unknown("count", "property_missing")
    count = 0
    first_line = ""
    for line in text.splitlines():
        if line.strip().lower().startswith("processor"):
            count += 1
            if not first_line:
                first_line = line
    if count < 1:
        return _unknown("count", "malformed_output")
    return _measured(count, "count", command, first_line or "processor",
                     observed_at)


def _data_free(result: TransportResult, observed_at: str) -> dict:
    command = "df /data"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown("bytes", "permission_denied")
        return _unknown("bytes", "malformed_output")
    text = _decode_stdout(result.stdout)
    if text.strip() == "":
        return _unknown("bytes", "property_missing")
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[-1] != "/data":
            continue
        if len(parts) < 6:
            return _unknown("bytes", "malformed_output")
        try:
            available_kb = int(parts[3], 10)
        except ValueError:
            return _unknown("bytes", "malformed_output")
        if available_kb < 0:
            return _unknown("bytes", "malformed_output")
        return _measured(available_kb * 1024, "bytes", command, line,
                         observed_at)
    return _unknown("bytes", "malformed_output")


def _kernel_release(result: TransportResult, observed_at: str) -> dict:
    command = "cat /proc/version"
    if result.exit_code != 0:
        if _is_permission_error(result.stderr):
            return _unknown(None, "permission_denied")
        return _unknown(None, "malformed_output")
    text = _decode_stdout(result.stdout).strip()
    if text == "":
        return _unknown(None, "property_missing")
    line = text.splitlines()[0]
    tokens = line.split()
    if len(tokens) >= 3 and tokens[0] == "Linux" and tokens[1] == "version":
        release = redact_text(tokens[2])
        if isinstance(release, str) and release.strip():
            return _measured(release, None, command, line, observed_at)
    return _unknown(None, "malformed_output")


def _gunzip_capped(raw: bytes) -> str | None:
    """Decompress gzip config text without ever fully expanding a bomb.

    Returns the decoded text, or None when the decompressed size passes
    MAX_CONFIG_GZ_DECOMPRESSED_BYTES. At most the cap plus one byte of
    output is materialised. Raises ValueError on corrupt/truncated input.
    """
    limit = probe_module.MAX_CONFIG_GZ_DECOMPRESSED_BYTES
    decompressor = zlib.decompressobj(31)
    try:
        piece = decompressor.decompress(raw, limit + 1)
    except zlib.error as exc:
        raise ValueError("bad gzip: %s" % exc)
    if len(piece) > limit:
        return None
    if not decompressor.eof:
        raise ValueError("truncated gzip")
    try:
        tail = decompressor.flush()
    except zlib.error as exc:
        raise ValueError("bad gzip: %s" % exc)
    if len(piece) + len(tail) > limit:
        return None
    return (piece + tail).decode("utf-8", "replace")


def _kernel_config(
    results: dict[str, TransportResult | Exception],
    observed_at: str,
) -> dict[str, dict]:
    command = "cat /proc/config.gz"
    outcome = results.get(command)
    if isinstance(outcome, ProbeTimeout):
        return {fact: _unknown(None, "timeout") for fact in CONFIG_SYMBOLS}
    if isinstance(outcome, ProbeOutputTooLarge):
        return {
            fact: _unknown(None, "output_too_large") for fact in CONFIG_SYMBOLS
        }
    if not isinstance(outcome, TransportResult):
        return {fact: _unknown(None, "malformed_output")
                for fact in CONFIG_SYMBOLS}
    if outcome.exit_code != 0:
        return {fact: _unknown(None, "not_exposed")
                for fact in CONFIG_SYMBOLS}
    raw = outcome.stdout
    if len(raw) > probe_module.MAX_CONFIG_GZ_DECOMPRESSED_BYTES and not (
        len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B
    ):
        return {
            fact: _unknown(None, "output_too_large") for fact in CONFIG_SYMBOLS
        }
    try:
        if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
            text = _gunzip_capped(raw)
            if text is None:
                return {
                    fact: _unknown(None, "output_too_large")
                    for fact in CONFIG_SYMBOLS
                }
        else:
            text = raw.decode("utf-8", "replace")
    except (OSError, ValueError, EOFError):
        return {fact: _unknown(None, "malformed_output")
                for fact in CONFIG_SYMBOLS}
    if text.strip() == "":
        return {fact: _unknown(None, "not_exposed")
                for fact in CONFIG_SYMBOLS}
    lines = text.splitlines()
    facts: dict[str, dict] = {}
    for fact_id, symbol in CONFIG_SYMBOLS.items():
        state: str | None = None
        used = ""
        for line in lines:
            stripped = line.strip()
            if stripped == symbol + "=y":
                state, used = "built_in", line
                break
            if stripped == symbol + "=m":
                state, used = "module", line
                break
            if stripped == "# %s is not set" % symbol:
                state, used = "disabled", line
                break
        if state is None:
            facts[fact_id] = _unknown(None, "malformed_output")
        else:
            facts[fact_id] = _measured(state, None, command, used, observed_at)
    return facts


def build_profile(
    fixture_name: str,
    created_at: str | None = None,
    *,
    transport: SimulatedTransport | None = None,
) -> dict:
    """Build a validated profile for one simulated fixture (offline)."""
    if fixture_name not in KNOWN_FIXTURES:
        raise ValueError("unknown simulated device: %r" % (fixture_name,))
    stamp = created_at or utc_now_z()
    owned = transport is None
    if owned:
        transport = SimulatedTransport(fixtures_dir() / fixture_name)
    assert transport is not None
    started = time.monotonic()
    results: dict[str, TransportResult | Exception] = {}
    for command in probe_module.ALLOWLIST:
        if time.monotonic() - started > TOTAL_SCAN_SECONDS:
            results[command] = ProbeTimeout(command)
            continue
        try:
            results[command] = probe_module.run_probe(transport, command)
        except (ProbeTimeout, ProbeOutputTooLarge) as exc:
            results[command] = exc
        except (RefusedCommand, RealDevicesNotEnabled) as exc:
            raise RuntimeError("allowlisted probe refused: %s" % exc)

    def outcome(command: str) -> TransportResult | Exception:
        return results[command]

    def limited(command: str, unit: object) -> dict | None:
        item = outcome(command)
        if isinstance(item, ProbeTimeout):
            return _unknown(unit, "timeout")
        if isinstance(item, ProbeOutputTooLarge):
            return _unknown(unit, "output_too_large")
        return None

    facts: dict[str, dict] = {}
    simple_strings = {
        "identity.manufacturer": "getprop ro.product.manufacturer",
        "identity.model": "getprop ro.product.model",
        "identity.device_codename": "getprop ro.product.device",
        "identity.android_release": "getprop ro.build.version.release",
        "identity.platform": "getprop ro.product.board",
        "boot.vndk_version": "getprop ro.vndk.version",
    }
    for fact_id, command in simple_strings.items():
        hit = limited(command, None)
        if hit is not None:
            facts[fact_id] = hit
            continue
        result = outcome(command)
        assert isinstance(result, TransportResult)
        facts[fact_id] = _getprop_string(command, result, stamp)
    sdk_hit = limited("getprop ro.build.version.sdk", None)
    if sdk_hit is not None:
        facts["identity.sdk_level"] = sdk_hit
    else:
        sdk_result = outcome("getprop ro.build.version.sdk")
        assert isinstance(sdk_result, TransportResult)
        facts["identity.sdk_level"] = _getprop_int(
            "getprop ro.build.version.sdk", sdk_result, stamp, minimum=1
        )
    bool_facts = {
        "boot.oem_unlock_supported": "getprop ro.oem_unlock_supported",
        "boot.oem_unlock_allowed": "getprop sys.oem_unlock_allowed",
        "boot.ab_slots": "getprop ro.build.ab_update",
        "boot.dynamic_partitions": "getprop ro.boot.dynamic_partitions",
        "boot.treble_enabled": "getprop ro.treble.enabled",
    }
    for fact_id, command in bool_facts.items():
        hit = limited(command, None)
        if hit is not None:
            facts[fact_id] = hit
            continue
        result = outcome(command)
        assert isinstance(result, TransportResult)
        facts[fact_id] = _getprop_bool(command, result, stamp)
    # Fix 9: ro.boot.vbmeta.device_state has no official Android/AOSP
    # source, so this fact is never measured from the probe output.
    # M3 derives it from the verified-boot state instead (R-LOCK-FROM-VBS).
    vb_hit = limited("getprop ro.boot.verifiedbootstate", None)
    if vb_hit is not None:
        facts["boot.verified_boot_state"] = vb_hit
    else:
        vb_result = outcome("getprop ro.boot.verifiedbootstate")
        assert isinstance(vb_result, TransportResult)
        facts["boot.verified_boot_state"] = _verified_boot(vb_result, stamp)
    facts["boot.bootloader_locked"] = (
        inference_module.derive_bootloader_locked(
            facts["boot.verified_boot_state"], _unknown(None, "deferred")))
    abi_hit = limited("getprop ro.product.cpu.abilist", None)
    if abi_hit is not None:
        facts["resources.cpu_abis"] = abi_hit
    else:
        abi_result = outcome("getprop ro.product.cpu.abilist")
        assert isinstance(abi_result, TransportResult)
        facts["resources.cpu_abis"] = _cpu_abis(abi_result, stamp)
    ram_hit = limited("cat /proc/meminfo", "bytes")
    if ram_hit is not None:
        facts["resources.ram_total"] = ram_hit
    else:
        ram_result = outcome("cat /proc/meminfo")
        assert isinstance(ram_result, TransportResult)
        facts["resources.ram_total"] = _ram_total(ram_result, stamp)
    cpu_hit = limited("cat /proc/cpuinfo", "count")
    if cpu_hit is not None:
        facts["resources.cpu_cores"] = cpu_hit
    else:
        cpu_result = outcome("cat /proc/cpuinfo")
        assert isinstance(cpu_result, TransportResult)
        facts["resources.cpu_cores"] = _cpu_cores(cpu_result, stamp)
    df_hit = limited("df /data", "bytes")
    if df_hit is not None:
        facts["resources.data_free"] = df_hit
    else:
        df_result = outcome("df /data")
        assert isinstance(df_result, TransportResult)
        facts["resources.data_free"] = _data_free(df_result, stamp)
    ver_hit = limited("cat /proc/version", None)
    if ver_hit is not None:
        facts["kernel.release"] = ver_hit
    else:
        ver_result = outcome("cat /proc/version")
        assert isinstance(ver_result, TransportResult)
        facts["kernel.release"] = _kernel_release(ver_result, stamp)
    facts.update(_kernel_config(results, stamp))
    facts["capacity.system_image_max"] = _unknown("bytes", "deferred")
    for fact_id in RUNTIME_FACTS:
        facts[fact_id] = _unknown(None, "not_probed_in_m1")
    profile = {
        "schema_version": "profile-v1",
        "simulated": True,
        "fixture_name": fixture_name,
        "created_at": stamp,
        "facts": facts,
    }
    errors = validate_profile(profile)
    if errors:
        raise RuntimeError("built profile is invalid: %s" % errors[0])
    missing = [name for name in ALL_FACT_IDS if name not in facts]
    if missing:
        raise RuntimeError("built profile is missing %s" % ",".join(missing))
    return profile


def _describe_value(fact: dict) -> str:
    status = fact.get("status")
    if status == "unknown":
        return "unknown (%s)" % fact.get("unknown_reason")
    if status == "conflicting":
        candidates = fact.get("candidates", [])
        rendered = ", ".join(json.dumps(item.get("value")) for item in candidates)
        return "conflicting (candidates: %s)" % rendered
    if status == "inferred":
        inference = fact.get("inference", {})
        return "%s (inferred via %s)" % (
            json.dumps(fact.get("value")),
            inference.get("rule_id"),
        )
    value = fact.get("value")
    unit = fact.get("unit")
    if unit == "bytes" and isinstance(value, int):
        return "%d bytes" % value
    if unit == "count":
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(part) for part in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render_report(profile: dict) -> str:
    """Render a plain-English report grouped as in spec table 1c."""
    facts = profile.get("facts", {})
    fixture = profile.get("fixture_name")

    def line(fact_id: str, label: str) -> str:
        fact = facts.get(fact_id, {})
        return "  %s: %s" % (label, _describe_value(fact))

    parts: list[str] = []
    parts.append("Simulated phone report (SIMULATED): %s" % fixture)
    parts.append("")
    parts.append("Identity:")
    parts.append(line("identity.manufacturer", "Manufacturer"))
    parts.append(line("identity.model", "Model"))
    parts.append(line("identity.device_codename", "Device codename"))
    parts.append(line("identity.android_release", "Android release"))
    parts.append(line("identity.sdk_level", "SDK level"))
    parts.append(line("identity.platform", "Platform/board"))
    parts.append("")
    parts.append("Boot:")
    parts.append(line("boot.bootloader_locked", "Bootloader locked"))
    parts.append(line("boot.oem_unlock_supported", "OEM unlocking supported"))
    parts.append(line("boot.oem_unlock_allowed", "OEM unlocking allowed"))
    parts.append(line("boot.verified_boot_state", "Verified boot state"))
    parts.append(line("boot.ab_slots", "A/B slots"))
    parts.append(line("boot.dynamic_partitions", "Dynamic partitions"))
    parts.append(line("boot.treble_enabled", "Treble enabled"))
    parts.append(line("boot.vndk_version", "VNDK version"))
    parts.append("")
    parts.append("Resources:")
    parts.append(line("resources.ram_total", "RAM total"))
    parts.append(line("resources.data_free", "Data free (user data only)"))
    parts.append(line("resources.cpu_abis", "CPU ABIs"))
    parts.append(line("resources.cpu_cores", "CPU cores"))
    parts.append("")
    parts.append("Kernel:")
    parts.append(line("kernel.release", "Kernel release"))
    parts.append(line("kernel.config.tun", "Kernel TUN"))
    parts.append(line("kernel.config.overlay_fs", "Kernel overlayfs"))
    parts.append(line("kernel.config.ext4", "Kernel ext4"))
    parts.append(line("kernel.config.exfat", "Kernel exFAT"))
    parts.append("")
    parts.append("Capacity (reserved in M1):")
    parts.append(line("capacity.system_image_max", "System image maximum"))
    parts.append("")
    parts.append("Runtime (reserved in M1):")
    parts.append(line("runtime.tun_usable", "TUN usable"))
    parts.append(line("runtime.overlay_fs_usable", "overlayfs usable"))
    parts.append(line("runtime.ext4_usable", "ext4 usable"))
    parts.append(line("runtime.exfat_usable", "exFAT usable"))
    parts.append("")
    parts.append("What this means (deterministic, no AI):")
    parts.extend(_what_this_means(facts))
    return "\n".join(parts) + "\n"


def _state(facts: dict, fact_id: str) -> tuple[str, object, object]:
    fact = facts.get(fact_id, {})
    return (
        str(fact.get("status", "unknown")),
        fact.get("value"),
        fact.get("unknown_reason"),
    )


def _yes_no_unknown(facts: dict, fact_id: str) -> str:
    status, value, reason = _state(facts, fact_id)
    if status == "measured" and isinstance(value, bool):
        return "yes" if value else "no"
    if status == "unknown":
        return "unknown (%s)" % reason
    return str(status)


def _what_this_means(facts: dict) -> list[str]:
    lines: list[str] = []
    status, value, reason = _state(facts, "boot.bootloader_locked")
    if status == "measured" and value is True:
        lines.append("- Bootloader is locked.")
    elif status == "measured" and value is False:
        lines.append("- Bootloader is unlocked.")
    elif status == "unknown":
        lines.append("- Bootloader lock state is unknown (%s)." % reason)
    else:
        lines.append("- Bootloader lock state is %s." % status)
    supported = _yes_no_unknown(facts, "boot.oem_unlock_supported")
    allowed = _yes_no_unknown(facts, "boot.oem_unlock_allowed")
    if supported == "yes":
        build_part = "build supports it"
    elif supported == "no":
        build_part = "build does not support it"
    else:
        build_part = "build support %s" % supported
    if allowed == "yes":
        permission_part = "permission allowed"
    elif allowed == "no":
        permission_part = "permission not allowed"
    else:
        permission_part = "permission %s" % allowed
    lines.append(
        "- Whether it can be unlocked: %s, %s." % (build_part, permission_part)
    )
    vb_status, vb_value, vb_reason = _state(facts, "boot.verified_boot_state")
    if vb_status == "measured":
        lines.append("- Verified boot state is %s." % vb_value)
    elif vb_status == "unknown":
        lines.append("- Verified boot state is unknown (%s)." % vb_reason)
    else:
        lines.append("- Verified boot state is %s." % vb_status)
    lines.append(
        "- A/B slots: %s. Dynamic partitions: %s. Treble: %s."
        % (
            _yes_no_unknown(facts, "boot.ab_slots"),
            _yes_no_unknown(facts, "boot.dynamic_partitions"),
            _yes_no_unknown(facts, "boot.treble_enabled"),
        )
    )
    abi_status, abi_value, abi_reason = _state(facts, "resources.cpu_abis")
    if abi_status == "measured" and isinstance(abi_value, list):
        joined = ", ".join(str(part) for part in abi_value)
        if all("64" not in str(part) for part in abi_value):
            lines.append("- Reported ABIs: %s (32-bit-only list)." % joined)
        else:
            lines.append("- Reported ABIs: %s." % joined)
    elif abi_status == "unknown":
        lines.append("- Reported ABIs are unknown (%s)." % abi_reason)
    else:
        lines.append("- Reported ABIs are %s." % abi_status)
    kernel_bits: list[str] = []
    for fact_id, label in (
        ("kernel.config.tun", "TUN"),
        ("kernel.config.overlay_fs", "overlayfs"),
        ("kernel.config.ext4", "ext4"),
        ("kernel.config.exfat", "exFAT"),
    ):
        item_status, item_value, item_reason = _state(facts, fact_id)
        if item_status == "measured":
            kernel_bits.append("%s=%s" % (label, item_value))
        elif item_status == "unknown":
            kernel_bits.append("%s=unknown (%s)" % (label, item_reason))
        else:
            kernel_bits.append("%s=%s" % (label, item_status))
    lines.append(
        "- Kernel was built with: %s. Whether services can use them "
        "is not probed in M1." % (", ".join(kernel_bits),)
    )
    ram_status, ram_value, ram_reason = _state(facts, "resources.ram_total")
    df_status, df_value, df_reason = _state(facts, "resources.data_free")
    cores_status, cores_value, cores_reason = _state(
        facts, "resources.cpu_cores"
    )
    if ram_status == "measured":
        ram_part = "%d bytes" % ram_value
    elif ram_status == "unknown":
        ram_part = "unknown (%s)" % ram_reason
    else:
        ram_part = str(ram_status)
    if df_status == "measured":
        df_part = "%d bytes" % df_value
    elif df_status == "unknown":
        df_part = "unknown (%s)" % df_reason
    else:
        df_part = str(df_status)
    if cores_status == "measured":
        cores_part = str(cores_value)
    elif cores_status == "unknown":
        cores_part = "unknown (%s)" % cores_reason
    else:
        cores_part = str(cores_status)
    lines.append(
        "- RAM total: %s. Data free: %s. CPU cores: %s. "
        "Data free says nothing about system image room; that "
        "needs M3 capacity facts." % (ram_part, df_part, cores_part)
    )
    return lines


def run_scan(
    fixture_name: str | None, json_output: bool = False
) -> tuple[int, str]:
    """Run one scan. No --sim exits 3 without starting any process."""
    if fixture_name is None:
        return 3, "real devices are not enabled in M1\n"
    if fixture_name not in KNOWN_FIXTURES:
        known = ", ".join(KNOWN_FIXTURES)
        return 2, "unknown simulated device %r (known: %s)\n" % (
            fixture_name,
            known,
        )
    profile = build_profile(fixture_name)
    if json_output:
        return 0, json.dumps(profile, indent=2, sort_keys=False) + "\n"
    return 0, render_report(profile)
