# M1 probes

Read-only probe (M1). Each command reads one named property or file. There
is no full `getprop` dump (it would include the serial number).

Authoritative validator: `stargate/profile.py` is the real validator for
profile-v1. `schemas/profile-v1.json` is descriptive and kept consistent
with it; when they differ, `stargate/profile.py` wins. Validate JSON with:

```sh
.venv/bin/python -m stargate.profile validate /tmp/budget.json
```

## Allowlist (20 commands)

| Probe ID | Command | Fact(s) | Source |
|---|---|---|---|
| getprop-ro.product.manufacturer | `getprop ro.product.manufacturer` | identity.manufacturer | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.product.model | `getprop ro.product.model` | identity.model | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.product.device | `getprop ro.product.device` | identity.device_codename | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.build.version.release | `getprop ro.build.version.release` | identity.android_release | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.build.version.sdk | `getprop ro.build.version.sdk` | identity.sdk_level | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.product.board | `getprop ro.product.board` | identity.platform (board name as platform) | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) |
| getprop-ro.boot.vbmeta.device_state | `getprop ro.boot.vbmeta.device_state` | boot.bootloader_locked (always `unknown`/`deferred`: no official source, see below) | No official source found: absent from the [AVB README](https://android.googlesource.com/platform/external/avb/+/master/README.md), libavb, [property_contexts](https://android.googlesource.com/platform/system/sepolicy/+/main/private/property_contexts) and the official bootloader/verified-boot docs. Probe kept read-only but not relied on. |
| getprop-ro.oem_unlock_supported | `getprop ro.oem_unlock_supported` | boot.oem_unlock_supported | [Lock/unlock bootloader](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking) (set `1`/`0` at build time), [property_contexts](https://android.googlesource.com/platform/system/sepolicy/+/main/private/property_contexts) (`exact int`) |
| getprop-sys.oem_unlock_allowed | `getprop sys.oem_unlock_allowed` | boot.oem_unlock_allowed | [PersistentDataBlockService.java](https://android.googlesource.com/platform/frameworks/base/+/android-15.0.0_r1/services/core/java/com/android/server/pdb/PersistentDataBlockService.java) at the Android 15 tag (`OEM_UNLOCK_PROP`, set `1`/`0`); no reference at main |
| getprop-ro.boot.verifiedbootstate | `getprop ro.boot.verifiedbootstate` | boot.verified_boot_state (`green`/`yellow`/`orange`/`red`) | [Verified Boot](https://source.android.com/docs/security/features/verifiedboot/verified-boot), [Boot flow](https://source.android.com/docs/security/features/verifiedboot/boot-flow) (`androidboot.verifiedbootstate`; `ro.boot.*` is the init-exposed form), [AVB](https://android.googlesource.com/platform/external/avb/+/master/README.md) |
| getprop-ro.build.ab_update | `getprop ro.build.ab_update` | boot.ab_slots | [install.cpp](https://android.googlesource.com/platform/bootable/recovery/+/b62ff1abe3f3cdaaefdc17a51cf6479428606c31/install/install.cpp) (`GetBoolProperty`), [dm.cpp](https://android.googlesource.com/platform/system/core/+/master/fs_mgr/libdm/dm.cpp) (`GetProperty`), [property_contexts](https://android.googlesource.com/platform/system/sepolicy/+/main/private/property_contexts) (`exact string`) |
| getprop-ro.boot.dynamic_partitions | `getprop ro.boot.dynamic_partitions` | boot.dynamic_partitions | [OTA dynamic partitions](https://source.android.com/docs/core/ota/dynamic_partitions/ab_launch), [recovery_main.cpp](https://android.googlesource.com/platform/bootable/recovery/+/39c684a7/recovery_main.cpp) (`GetBoolProperty`), [property_contexts](https://android.googlesource.com/platform/system/sepolicy/+/main/private/property_contexts) (`exact string`) |
| getprop-ro.treble.enabled | `getprop ro.treble.enabled` | boot.treble_enabled | [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) (`SystemProperties.getBoolean("ro.treble.enabled", false)`) |
| getprop-ro.vndk.version | `getprop ro.vndk.version` | boot.vndk_version | [VNDK](https://source.android.com/docs/core/architecture/vndk) (`ro.vndk.version`; removed for some Android 15 builds) |
| getprop-ro.product.cpu.abilist | `getprop ro.product.cpu.abilist` | resources.cpu_abis | [Build](https://developer.android.com/reference/android/os/Build), [Build.java](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/master/core/java/android/os/Build.java) (`SUPPORTED_ABIS` from `ro.product.cpu.abilist`) |
| cat-proc-meminfo | `cat /proc/meminfo` | resources.ram_total (`MemTotal` kB → bytes) | [proc docs](https://docs.kernel.org/filesystems/proc.html) |
| cat-proc-cpuinfo | `cat /proc/cpuinfo` | resources.cpu_cores (count of `processor` lines) | [proc docs](https://docs.kernel.org/filesystems/proc.html) |
| df-data | `df /data` | resources.data_free (Available 1K-blocks → bytes, user data only) | [POSIX df](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/df.html) (`df file`: free space in the file system holding `file`) |
| cat-proc-version | `cat /proc/version` | kernel.release (`Linux version <release> ...`) | [proc docs](https://docs.kernel.org/filesystems/proc.html) |
| cat-proc-config-gz | `cat /proc/config.gz` | kernel.config.tun/overlay_fs/ext4/exfat (`CONFIG_TUN`, `CONFIG_OVERLAY_FS`, `CONFIG_EXT4_FS`, `CONFIG_EXFAT_FS`: `=y` built_in, `=m` module, `is not set` disabled) | [proc docs](https://docs.kernel.org/filesystems/proc.html) (`/proc/config.gz` when `IKCONFIG_PROC`). 1 MiB decompressed cap enforced. |

Sourcing notes: `ro.build.ab_update`, `ro.boot.dynamic_partitions` and
`ro.oem_unlock_supported` now each have official sources (rows above).
`sys.oem_unlock_allowed` is sourced at the Android 15 tag (absent at
main). `ro.boot.vbmeta.device_state` has no official source after an
explicit search, so `boot.bootloader_locked` is `unknown` (`deferred`)
in every fixture and in `stargate/scan.py` instead of measured.

## Refusal, limits, privacy

- Refused before the transport is called: anything not on the allowlist
  exactly, and any shell metacharacters (`;`, `|`, `&`, `>`, `<`,
  backticks, `$(`, newlines). See `stargate/probe.py:check_command`.
- Limits: 5 s per command, 64 KiB output per command, 60 s total per scan;
  `/proc/config.gz` 1 MiB decompressed. Limit hits become `unknown` with
  `timeout` or `output_too_large`. The 64 KiB cap bounds the raw bytes
  each probe may return (for config.gz, the still-compressed bytes);
  the 1 MiB cap separately bounds the decompressed config text, and is
  enforced while streaming so an expanding payload is refused as
  `output_too_large` without ever being fully expanded in memory.
- Privacy: never reads serials, IMEI, phone numbers, accounts, Wi-Fi
  details or user files. Redaction (`stargate/probe.py:redact_text`) runs
  on values, evidence excerpts and error messages.
- Transport: `SimulatedTransport` replays `fixtures/<name>/outputs.txt`
  (first line `SIMULATED`, then JSON). `RealAdbTransport` is a stub that
  raises without starting any process.

## Fixture format

`fixtures/<name>/outputs.txt`: line 1 starts with `SIMULATED`, lines 2+
are a JSON object mapping each exact allowlisted command to
`{"exit": int, "stdout": str, "stderr": str, "timeout": bool}`.
Every file under `fixtures/` must start with `SIMULATED`
(`tests/test_fixtures.py` fails otherwise).
