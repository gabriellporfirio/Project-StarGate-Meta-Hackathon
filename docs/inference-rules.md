# Inference rules (M1 scan, M3 strategy)

M1 emitted no `inferred` facts. M3 adds one production rule below.
Every other fact is `measured`, `unknown` or (test-only) `conflicting`;
reserved facts are `unknown` (`capacity.*` → `deferred`,
`runtime.*` → `not_probed_in_m1`).

## Production rules (emitted by scan)

| Rule ID | Inputs | Output | Notes |
|---|---|---|---|
| R-LOCK-FROM-VBS | boot.verified_boot_state | boot.bootloader_locked | green/yellow → true, orange → false, red/unknown/conflicting → stays unknown. Never overwrites another source; a disagreeing value gives conflicting with both kept. Source quoted below. |

`R-LOCK-FROM-VBS` reads the measured `boot.verified_boot_state` value:

- `green` or `yellow` gives `boot.bootloader_locked: true`, status `inferred`
- `orange` gives `boot.bootloader_locked: false`, status `inferred`
- `red`, `unknown` or `conflicting` leaves the lock fact `unknown`

The result keeps status `inferred` and names the rule; it is never
relabelled as measured. `ro.boot.verifiedbootstate` is the Android
property mirror of the bootloader's `androidboot.verifiedbootstate`
option, which AOSP's boot-flow documentation defines as follows
(https://source.android.com/docs/security/features/verifiedboot/boot-flow,
section "Communicate Verified Boot state to Android", fetched 1 Nov 2026):

> The bootloader sets the `androidboot.verifiedbootstate` option to one
> of the following values: `green` if the device is `LOCKED` and
> user-settable root of trust isn't used; `yellow` if the device is
> `LOCKED` and user-settable root of trust is used; `orange` if the
> device is `UNLOCKED`.

## Example-only rules (schema examples, not emitted by scan)

| Rule ID | Inputs | Output | Notes |
|---|---|---|---|
| example-platform-from-codename-v1 | identity.device_codename | identity.platform | Used only in `schemas/examples/valid_inferred.json` to exercise the `inferred` shape. Not used by `stargate scan`. |

Future rules that derive `inferred` facts must be listed here with
their rule ID, inputs and meaning before use.
