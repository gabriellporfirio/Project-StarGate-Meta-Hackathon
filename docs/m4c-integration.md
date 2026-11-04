# M4c integration steps for the builder (m4a)

Scope: everything in this file is the builder's (`stargate build`
in `/workspaces/m4a`). The M4c app side (variants, providers,
keys, the `stargate/m4c.py` library, the development log) is done
on branch `m4c-byok` in `/workspaces/m4b`. Merge that branch
first: it carries `stargate/m4c.py` (provider pins, per-request
limits, development-log calls), `docs/m4c-calls.md` (the attempt
log, no money) and `assistant-app/build.py` (both variants, no
money baked in).

v2.5 removal (BYOK): users bring their own key and the app never
tracks money. The old v2.4 names are gone: no pinned prices, no
dollar limits, no cost reservations, no input-token ceiling, no
"unpriced model refused" rule, no per-build phone share and no
priced-model allowlist in the APK. Concretely, the builder must
not implement, call or test: `Ledger`, `GateRefused`, `priced`,
`allowed_models_for_provider`, `estimate_usd`, `committed_usd`,
`dollar_cap`, `reserve`, `record_done`, `record_failed`,
`most_expensive_priced`, `reserve_build_share`,
`reserve_cloud_share`, `BUILD_ID`, `SHARE_ATTEMPTS`,
`SHARE_ATTEMPTS_PER_PROVIDER`, `ALLOWED_META_MODELS`,
`ALLOWED_BASETEN_MODELS`, `input_usd_per_1k`,
`output_usd_per_1k`, `dollar_limits`, `owner_credit`,
`actual_spend`, or `docs/m4c-ledger.json` (deleted). The removal
check in `tests/test_m4c.py` scans for these names; this section
is its documented exception for describing the removal.

## 1. The flag

In `stargate/cli.py`, on the `build` subcommand next to
`--assistant-manifest`, add:

- `--assistant-cloud` (`store_true`): pick the cloud Assistant
  variant for this build. A build contains exactly one variant.

In `stargate/build/gate.py::run_build`, before any build step
(no directories, no artifacts, no image work): when
`--assistant-cloud` was given without the Assistant selected,
stop with exit 6. Suggested helper, tested directly:

- `gate.assistant_cloud_allowed(selection, assistant_cloud) ->
  (ok, message)`; `run_build` returns `(6, "", message)` when
  not ok.

There is no ledger or share reservation in
`stargate build --assistant-cloud`. A cloud build never stops
for money: no price, dollar or attempt gate before building.

## 2. Build the variant

the builder at `5a9c7e6` reads the verified snapshot
`/workspaces/m4a-tools/inputs/assistant-1936549b.apk`
(`stargate/build/image.py:106-107`). For a cloud build:

1. Run the app build under the shared lock, with the variant
   and an isolated output folder (never `out/`, which stays
   frozen):
   `flock /workspaces/.android-build.lock
   /usr/local/bin/python3 assistant-app/build.py --variant cloud
   --out-dir the folder assistant-manifest.json's cloud entry names
   --build-dir the folder assistant-manifest.json's cloud entry names/build`.
   The script bakes only `CLOUD_ENABLED` plus the model files
   into the APK, and refuses any permission set but exactly
   INTERNET.
2. Use the APK named by `assistant-manifest.json`
   `assistant_cloud_apk` (in the folder
   assistant-manifest.json's cloud entry names) for the rest
   of the build (image, transfer, hashes).

Local builds keep `--variant local`
(`/workspaces/m4b-tools/out/assistant.apk`, no permissions,
approved hash `1936549b…`, untouched).

## 3. Record the variant

`build-manifest.json` records `assistant_variant` (`local` or
`cloud`) plus the Assistant APK's package, version and SHA-256.
Concretely:

- `schemas/build-v1.json` gains the variant field (enum
  `local`/`cloud`) and the APK record. Suggested shape, next to
  the existing assistant entries:
  `"assistant_variant": "cloud"`,
  `"assistant_apk": {"package": "com.stargate.assistant",
  "version": "1.0", "sha256": "<built APK>"}`.
- `stargate/build/manifest.py::make_manifest` takes the new
  values and writes them; `validate_manifest` requires them
  (variant present and one of the two; APK package, version and
  SHA-256 present and well-formed).

## 4. Contents check reads the real permissions

M4's section 5 contents check already reads permissions from
inside the APK (`stargate/build/checks.py::apk_permissions`,
via `run_aapt2_badging`). Extend it with the variant
comparison. Suggested helper, tested directly:

- `checks.check_assistant_variant_permissions(apk_path,
  variant) -> list` returning error strings (`[]` means pass):
  read `apk_permissions(apk_path)`; local passes only on `[]`,
  cloud passes only on exactly
  `["android.permission.INTERNET"]`; anything else is an error.
- Call it from `run_contents_checks` with the manifest's
  `assistant_variant`, and map any error to exit 8 like the
  other contents failures.

## 5. Key scan covers APKs

M4's key scan must also scan the Assistant APK (both variants):
every zip entry, including `classes.dex` and
`AndroidManifest.xml`, for key-shaped strings. No key is ever
baked into an image, an APK, a build manifest, a log or a
screenshot. A hit fails the build.

## 6. Tests the builder should add (offline, fakes)

In `/workspaces/m4a/tests/`, mirroring the existing gate and
checks tests:

- `test_assistant_cloud_without_assistant_stops_6`: plan
  without the Assistant + `--assistant-cloud` returns exit 6
  from `run_build`, and the out dir stays empty (no build step
  ran).
- `test_assistant_cloud_local_default`: without the flag the
  variant records `local`.
- `test_assistant_cloud_never_gates_on_money`: a cloud
  `run_build` writes no ledger rows and stops for no price,
  dollar or share reason; the only pre-build stop is the
  missing-Assistant exit 6.
- `test_manifest_variant_fields`: `make_manifest` writes
  `assistant_variant` plus package/version/SHA-256, and
  `validate_manifest` rejects a missing or wrong variant.
- `test_contents_variant_permissions`: with fake APKs (or the
  real badging parser over canned output), local + INTERNET
  fails, cloud + none fails, cloud + INTERNET + CAMERA fails,
  and the two correct sets pass; failures surface as exit 8
  from `run_contents_checks`.
- `test_key_scan_covers_apk`: a synthetic APK containing a
  key-shaped string fails the key scan; the real built APKs
  pass.

## 7. After the Mac proof run

The owner reconciles `docs/m4c-calls.md` directly (it is the
log, no separate JSON): each `planned` phone row becomes
`sent` (with HTTP status and token counts from the app's
screen and metadata log) or `uncertain` (timeout, dropped
connection, anything that may have reached the provider). A
`planned` row is deleted only when the app's log shows that
send never happened. No billing page is re-read and no spend
is recorded. No more live work starts until every planned row
is reconciled.
