# Strategy rules (M3)

Fixed rules for judging the five strategies. Code in
`stargate/strategy.py` follows these tables exactly.

## 3a. Unlock state (one answer only)

Rows are checked in order; the first match wins. Exactly one row
always applies.

| Order | When | Unlock state | Meaning |
|---|---|---|---|
| 1 | The lock fact is `conflicting` | `unknown` | The evidence disagrees. Both pieces are shown |
| 2 | `boot.bootloader_locked` is `false` | `already_unlocked` | No unlock step needed |
| 3 | Locked is `true`, `boot.oem_unlock_supported` is `true`, `boot.oem_unlock_allowed` is `true` | `allowed_now` | Unlocking is possible today |
| 4 | Locked is `true`, supported is `true`, allowed is `false` | `not_allowed_now` | Blocked for now (for example, the setting is off or a carrier lock applies). This may change |
| 5 | Locked isn't `false`, and supported is `false` | `no_official_path` | The phone reports no official unlock. Blocked, but labelled "no official path found", not "impossible forever" |
| 6 | Anything else | `unknown` | A needed fact is missing |

Strategies that need an unlocked bootloader treat `already_unlocked`
and `allowed_now` as "unlock OK". `not_allowed_now` gives `blocked`
with `blocker_kind: current`, naming `boot.oem_unlock_allowed`.
`no_official_path` gives `blocked` with
`blocker_kind: no_official_path`, naming
`boot.oem_unlock_supported`. `unknown` gives `unknown`.

## 3b. Strategy rules

| Strategy | `candidate` when (all true) | `blocked` when (any true) | Otherwise `unknown`, including | Always listed as still open |
|---|---|---|---|---|
| `community_build` (e.g. a LineageOS device tree) | `lineage.support` is `current`; `repos.device_tree` has an established repo; unlock OK | Unlock blocked | LineageOS support is `historical_only` or `not_listed` (other community builds weren't checked, so this isn't proof it's impossible); no research file; a needed research fact missing or unknown | The tree still builds; the vendor files are available |
| `generic_system_image` (GSI) | `boot.treble_enabled` is `true`; unlock OK; `resources.cpu_abis` names an ABI that a GSI is published for | `treble_enabled` is `false` (`blocker_kind: unsupported`); unlock blocked | ABI unknown; a known ABI with no GSI-published overlap (M3 has no stronger verdict for this) | Launch and vendor version compatibility (Android's GSI rules also check these, and M3 has no fact for them) |
| `linux` (postmarketOS-style) | `pmos.status` is `listed`; unlock OK | Unlock blocked | `pmos.status` is `not_found` (not being listed isn't proof a port can't exist); no research file | A listed wiki page shows a page exists, not that the port works: which features work isn't checked |
| `stock_rebuild` | Never in M3 | Never | Never | Always `not_covered` (M2 doesn't research stock firmware) |
| `on_top_of_android` | `identity.sdk_level` is at least the app minimum (26, planning estimate); `resources.cpu_abis` includes an ABI the apps support (planning estimate, see below) | `identity.sdk_level` is below the app minimum (`blocker_kind: unsupported`) | SDK or ABI unknown; a known ABI with no app-supported overlap | The app modules don't exist yet (they're built in M4) |

When a strategy is both blocked by its own rule (Treble, SDK) and by
unlock, the strategy's own blocker is named. `blocker_kind:
unsupported` is StarGate's own label for "this phone can't meet a
fixed requirement".

## App requirements (planning estimates until M4 sets them)

- Minimum SDK level: 26.
- Supported ABIs: `arm64-v8a`, `armeabi-v7a`, `x86_64` (the apps are
  mainly Java/Kotlin, and the emulator matters).

## GSI ABIs

- `arm64-v8a`: a GSI is published for it. Source: Android's GSI page
  ("The GSI CPU architecture type must match the device's CPU
  architecture. ... on a Pixel 5, the output would indicate that the
  CPU architecture is `arm64-v8a`, so you would use the `arm64` type
  of GSI."),
  https://developer.android.com/topic/generic-system-image (fetched
  1 Nov 2026).
- `x86_64`: planning estimate (a GSI-published ABI is assumed for the
  emulator; no source checked in M3).

## Ranking

Candidates first, in fixed order: community build, GSI, Linux, on
top of Android. Then `unknown` (same fixed order), then `blocked`
(`current` blockers before `no_official_path` before `unsupported`,
fixed order inside each kind), then `not_covered`.

## Risks and signing (fixed, code-owned text)

- "Unlocking or relocking the bootloader wipes the phone's data."
  Source: AOSP device-state documentation ("To protect user data,
  *all* state transitions wipe the data partitions"),
  https://source.android.com/docs/security/features/verifiedboot/device-state
  (fetched 1 Nov 2026).
- "Verified boot shows orange after unlocking" — source: AOSP
  boot-flow documentation ("`orange` if the device is `UNLOCKED`"),
  https://source.android.com/docs/security/features/verifiedboot/boot-flow
  (fetched 1 Nov 2026). "Relocking needs your own signing key, if
  the phone supports it" — source: AOSP device-state documentation
  ("Devices can optionally allow the user to configure the root of
  trust"), same device-state URL above.
- "A community tree may not build, and some hardware may not work."
  StarGate's own guidance.
- "Some GSI hardware features may not work." StarGate's own
  guidance.
- "A listed port page shows a page exists, not that the port
  works." StarGate's own guidance.
- "The app modules do not exist yet; they are built in M4." /
  "No bootloader change; stock verified boot is untouched." /
  "M3 does not judge stock rebuilds." / "Unknown in M3."
  StarGate's own guidance.
