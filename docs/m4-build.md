# M4a build record (M4 Parts 1, 2 and 3)

Part 1 (route-independent): the section 1 plan gate, section 5a
binding, `build-v1`, the section 5 contents checks, section 7
carry-overs, and the launcher + Files apps. Part 2 (Route A):
image packaging, the image contents checks, the demo builds,
and the Mac steps. Part 3 (Route A, spec 4d): the Assistant
app in the image with the model added after boot ("apps in
image; model added after boot", never a pure image build).

## Part 1A: build gate and checks (Python)

- `stargate build --plan <plan.json> --out <dir>
  [--assistant-manifest <path>]`: the section 1 gate runs before any
  build step (full plan-v1 validation; handoff vs top level with
  `handoff.reasons == fit.reasons` exactly; `emulator_only` only;
  resolved Assistant model), then the section 5a binding when the
  Assistant is selected. Exit codes follow section 8: 6/7/9 from the
  gate, 8 from the build or a contents check. Part 1 has no route yet,
  so a gated build refuses with 8 ("route is not decided yet"); the
  `not_yet` route is internal draft state only and no finished build
  may carry it.
- Section 5a binding: the manifest's model ID and source revision must
  equal the handoff's, the file set must match (same names, same
  sizes), and every file must carry a SHA-256 in the manifest. M3's
  plan schema is unchanged (the handoff still carries no per-file
  hashes). The Assistant manifest's own SHA-256 is recorded in
  `build-manifest.json`.
- `build-v1` (`schemas/build-v1.json`, writer and validator in
  `stargate/build/manifest.py`): plan SHA, selection, route, build
  ID, labels, artifacts, apps, model files, runtime version,
  Assistant-manifest SHA, unresolved checks.
- Section 5 contents checks (`stargate/build/checks.py`): real
  package names from inside each APK (aapt2), model files in a model
  folder, assets in every APK. Exit 8 on an unselected/missing app or
  a model mismatch (including right name and size, different content).
- Section 7 carry-overs: `--explain` cap 4096 with one retry and the
  "cut short" label; `handoff.reasons == fit.reasons` for every
  result; estimates cannot list a sourced field; any `finish_reason`
  other than `stop` is incomplete; the AOSP page date stays on file
  with the quoted sentence in `docs/inference-rules.md`.

Tests: `tests/test_build_gate.py` (gate + binding on a real M3-made
plan with a small test model entry), `tests/test_build_manifest.py`
(build-v1), `tests/test_build_checks.py` (contents checks on real
aapt2-linked APKs), plus the section 7 additions. Full suite:
361 tests pass with sockets blocked (see Part 1B count below).

## Part 1B: launcher and Files apps (Android)

Built with the spike's toolchain (JDK 17 + Android build-tools 34.0.0
from read-only `/workspaces/m4-tools`); no Gradle, no network. Own
throwaway debug key at `/workspaces/m4a-tools/debug.keystore`.
Sequential, one app at a time, d8 capped at 2 GB heap (`-JXmx2g`); a
`ps` check before the build (and inside the script) confirmed no
other Android/Gradle build was running.

Build command (plan: Files-only flagship, SHA-256
`a67dad5fb8e3554a5def5c85d747da175ed3b2167541a2a765fd03a163759934`):

```
sh android/build_apps.sh --plan /tmp/part1-launcher-plan.json \
    --build-id m4a-part1
```

The launcher's services list is generated from that plan's handoff
only (`stargate.build.checks.launcher_config`); Part 1 bakes
`{"services": ["files"], "build_id": "m4a-part1", "labels": []}`.
Part 2 bakes per-build values the same way.

APKs (kept out of git, under `/workspaces/m4a-tools/out/`):

| APK | Package | Version | Bytes | SHA-256 | Permissions (aapt2) |
|---|---|---|---|---|---|
| stargate-launcher.apk | com.stargate.launcher | 1.0 (code 1) | 12787 | `2d817fc9f9dce6598fecd374a8f18eb5c88b777ec24d09892924e54f72c1e7f4` | none |
| stargate-files.apk | com.stargate.files | 1.0 (code 1) | 16811 | `f14855c8e839cd6952e85ab366711ab1b37acb33c3a844f36caab0f33a8a7e4c` | `android.permission.INTERNET` only |

Notes:

- Launcher (home app): shows "StarGate", the baked services, the
  build ID, and any labels. Requests no permissions.
- Files: `FilesServer.java` (shared, no Android dependencies) listens
  on 127.0.0.1 only; the access code is made on first start, persisted
  in the app's private storage, and shown on screen with the address.
  Protocol: `GET /files?code=` (JSON list), `GET /files/<name>?code=`
  (download), `PUT /files/<name>?code=` with Content-Length (upload).
  Quotas (50 MB/upload, 200 MB total) are refused with 413 before any
  body byte is read or file written; uploads land via temp file +
  atomic move. Its only permission is INTERNET (needed to listen on
  loopback); the code opens no client sockets.
- Both APKs pass `apksigner verify`, and the pair passes the
  section 5 contents checks for a Files-only selection (exit 0).

Tests: `tests/test_files_server.py` compiles the exact
`FilesServer.java` baked into the APK and exercises every rule on the
host (auth, confinement, quotas with no partial file, loopback
LISTEN entry in `/proc/net/tcp*`, code stability, no-outgoing
source scan). Full suite: 375 tests pass with sockets blocked
(loopback excepted).

## Live checks (Part 1C)

Two live calls, run once each on 1 Nov 2026. These are M4a's only two
live calls in all of M4: do not repeat them. (The key is never
recorded; both commands ran under `/usr/local/bin/python3` with
`PYTHONPATH=/workspaces/m4a`, loading
`/workspaces/m4a/stargate/__init__.py`.)

- `PYTHONPATH=/workspaces/m4a /usr/local/bin/python3 -m stargate
  plan --sim flagship --services files,assistant --research
  fixtures/research/flagship.json --explain --json-out
  /tmp/m4a-part1-explain-plan.json` (plan SHA-256
  `6f010c500e552c2b934962799d2e5f5a51b11d76f8723442171911b9d90048be`):
  exit 0, 1 attempt (no retry needed), `finish_reason=stop`,
  model `muse-spark-1.3`, 2637 completion tokens (1900 reasoning),
  123 prompt tokens. Explanation status `ok`, label "model
  explanation, not checked". 2637 completion tokens would have blown
  past M3's 2048 cap, so this run confirms the 4096 cap holds.
- `stargate doctor`: exit 0, `ok`, 1 attempt, 1399 ms, model returned
  `muse-spark-1.3`, 265 completion tokens (255 reasoning). Plan pin
  ok (HEAD `b498e70…`, clean).

## Part 2B: Route A packaging (image work)

Method: the spike's proven same-size repack
(`spike/m4/path_a_repack.py`): verify the stock SHA-256, extract
the system/system_ext/product ext4 regions, e2fsck, build.prop
cross-check, debugfs writes (0644/0755, 0:0, `system_file` with
the 1-byte NUL fix), splice back same-size, closed-loop verify
(outside-untouched, re-extract, hashes). Code:
`stargate/build/image.py`, wired as the default `stargate build`
behind the section 1 gate and the 5a binding. The section 5
checks read the real image bytes (every APK dumped and read
with aapt2, the model folder dumped and hashed, the launcher
baked config compared, image size vs the partition limit).

Two extensions past the spike, both verified below:
- The stock launcher APK (`com.android.launcher3`, the only
  stock home screen, HOME confirmed via xmltree) is removed
  from system_ext so ours is the home screen. `debugfs rm`
  leaves block bitmaps stale, so `e2fsck -y` repairs that
  filesystem then `e2fsck -n` confirms clean. The empty
  `Launcher3QuickStep` dir keeps its orphaned `oat/` subdir
  (no APK, nothing installs from it).
- New files and dirs reuse the exact stock mode/owner/label
  (0644/0755, 0:0, `u:object_r:system_file:s0` as a 26-byte
  NUL-terminated xattr, verified on a stock priv-app APK and
  dir). Launcher to `/system/priv-app/StargateLauncher/`,
  Files to `/system/app/StargateFiles/`, build ID to
  `/system/stargate-build-id.txt` as `build_id=<id>`.

Disk (before the first extraction): 20 GB free on
`/workspaces`. Peak new use estimated 7 GB (one build
transient ~2.9 GB: image copy 1.6 + system 0.84 +
system_ext 0.15 + product 0.31; finished Files build
~2.3 GB: img 1.6 + gz ~0.64; APKs < 0.1). The 1.14 GB
model is read in place, never copied. 20 >= 7 + 5:
proceed. All image and Android work ran under the shared
`flock /workspaces/.android-build.lock`, sequential, d8
capped at 2 GB heap.

Fit verdict (measured, not assumed) — PARTIAL from here on:
- Model need (`assistant-manifest.json`): 1,138,135,470 bytes
  (pte 1,135,951,488 + tokenizer 2,183,982).
- Product partition: 305,967,104 bytes total, 901,120 free
  (220 blocks), 48 free inodes. System: 67,137,536 free,
  64 free inodes. system_ext total 149,532,672.
- The model exceeds the whole product partition, so no
  growth within the spike's same-size method can place it;
  LP-metadata surgery to enlarge the image is not the
  proven method and was not attempted. Recorded Route A
  failure for the Assistant (spec 4b): exit 8, never a
  push after boot. Only the Files-only build proceeds; the
  reduced-demo decision is the owner's (spec section 10).

Stock census (read-only): 89 APKs, none `com.stargate.*`;
one pre-existing stock duplicate (`com.android.networkstack`
in NetworkStack v14 and NetworkStackNext vT-next) that the
checks tolerate. After the repack (90 APKs), HOME holders
are ours (`com.stargate.launcher`) plus 3 stock
provisioning/setup/fallback holders also present at stock
boot (managedprovisioning, sdksetup, settings); the Mac
boot screenshot is the proof ours wins.

Validation build (scratch, deleted after; the real code
path, exit 0):
- Plan `/tmp/flagship-files.plan.json` (flagship + files via
  M3, fits/emulator_only, SHA `cc8f48d6…`), build ID
  `m4a-files-cc8f48d6`.
- APKs via `android/build_apps.sh` (locked, sequential, d8
  `-JXmx2g`): launcher 12787 B `51b5c8f6…`, files 16811 B
  `1bb6731e…`.
- Image 1,622,147,072 B (== stock == limit), SHA
  `840dc758…`; .gz 637,235,629 B, SHA `7ace6c12…`.
  Manifest route A with both artifacts, both apps, and the
  image size vs the limit. Build ID reads back as
  `build_id=m4a-files-cc8f48d6`.
- Assistant attempt (M4b plan + committed manifest; gate
  and 5a binding pass): exit 8 with the recorded Route A
  failure, no outputs created.

Findings:
- `assistant-manifest.json`'s `assistant_apk.sha256`
  (`d9a32869…`) does not match
  `/workspaces/m4b-tools/out/assistant.apk` (`f98a59a5…`,
  24,765,321 B, M4b fixup 3 rebuild). The model hashes
  match (.pte `8715cdba…`, tokenizer `82e9d319…`). No
  Assistant artifacts were built, so nothing consumed the
  stale hash; re-approval is needed before any future
  Assistant build.
- M4b plan SHA `1f8f2553…` matches the manifest's
  `plan_sha256`: the committed plan is the approved one.

Tests: `tests/test_build_image.py` (29 tests: pure
build-ID/naming/fit/targets/gzip cases plus synthetic-ext4
fs roundtrips, clobber guards, splice confinement, stock
verify, full builds with a small model that fits, every
build failure incl. the recorded Route A failure, and
every image-check failure through `run_image_checks`
with exit 8).

## Part 2C: demo builds (PARTIAL)

Profile: `flagship` (sim) + Files only. Plan
`demo/plans/flagship-files.plan.json` made fresh with M3's
code (`stargate plan --sim flagship --services files
--research fixtures/research/flagship.json --json-out …`,
offline, no `--explain`): fits/emulator_only,
SHA `cc8f48d644f59aad951b1ce2dc3305811425f0d1639f933fa209da7429dd2be7`,
build ID `m4a-files-cc8f48d6`. The M4b plan
`demo/plans/flagship-files-assistant.plan.json` re-validates
(`plan is valid`, exit 0).

Files-only build (`/workspaces/m4a-tools/out/build-files`),
exit 0 with the section 5 checks passing:
- APKs rebuilt for this plan+ID (locked, sequential, d8
  `-JXmx2g`): launcher 12787 B
  `ca3b940a66dfd098e2d08a7672a9235e7dff453adf0c276c5de96b02bdcf5e72`,
  files 16811 B
  `91ea1098ae77b68fc72a5cbb41d6a6f334634b8b13322469c12d8c8c73bd32c3`.
- Image `system-stargate-files.img`: 1,622,147,072 B (==
  stock == limit),
  `25f3b379df16333e60c40773249c95db36e2c8b924897d3ddc3779eb0cbce0ea`.
- Transfer `system-stargate-files.img.gz`: 637,235,632 B,
  `30d84ffe5161971624d5651b65ca03946db01da53b2ac4c6d5bcccdd31fb2be5`.
- Manifest `demo/manifests/build-files.build-manifest.json`
  (copy of the build's `build-manifest.json`; artifact
  paths are relative to
  `/workspaces/m4a-tools/out/build-files/`): route A,
  both artifacts with size and SHA-256, both apps with
  package/version/SHA-256, image size vs the limit.
  Images and APKs stay out of git.

Files + Assistant attempt
(`--out …/build-files-assistant`): exit 8, the recorded
Route A failure (`needs 1138135470 bytes but the partition
holds only 305967104 in total; … never pushed after boot`),
no image, no manifest, no out dir. Per the prompt and spec
4b/10 this is PARTIAL, not retried: only the Files-only
build and its Mac steps ship. The reduced-demo decision is
the owner's.

Full suite at Part 2 (commit `95e2547`, re-run from a
pristine archive for the record): 456 tests, 454 pass, 1
skip, 1 failure. The skip is
`test_no_manifest_flag_before_handover_is_9` (handover
manifest already committed); the failure is
`test_apk_fields_match_built_apk` (manifest hash
`d9a32869…` vs the fixup-3 rebuilt APK). The Part 2
report's "455 pass" was an arithmetic slip; 454 is the
real number.

## Part 2D: Mac boot steps (PARTIAL: Files only; written, not run)

Run every block below in ONE Terminal session on the Mac, in
order (the guard, SERIAL, FH and CODE live in that session
only). Each line pastes cleanly into zsh: one command per
line, no placeholders in brackets. Three lines need your
own value, each marked in capitals: FH (step 3, your
forwarded address), SERIAL (step 6, only if the emulator is
not emulator-5554), CODE (step 7, read from the emulator
screen). No installs: everything reuses `~/stargate-spike`
from the spike. No Assistant AVD or steps (PARTIAL).

The guard differs from the base text in two tested ways
(12-case fake-adb harness, 12/12 under bash and zsh: allow
once, deny on extra/offline/unauthorized rows, empty,
wrong serial, wrong AVD, CRLF, failing adb even with
valid-looking output, daemon-startup chatter): adb's exit
status is captured directly so a failing `adb devices` can
never pass, and one added filter drops a surviving header
line when daemon chatter precedes it.

### Step 0 — environment (first, in every new Terminal)

```sh
export SG=~/stargate-spike
export ANDROID_HOME=$SG/sdk
export ANDROID_USER_HOME=~/.android
export JAVA_HOME=$SG/jdk-17/Contents/Home
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$JAVA_HOME/bin:$PATH
sg_guard() { local inv rows avd rc; inv="$(adb devices 2>/dev/null)"; rc=$?; [ "$rc" = "0" ] || { echo "STOP: adb devices failed"; return 1; }; inv="$(printf '%s' "$inv" | tr -d '\r')"; rows="$(printf '%s\n' "$inv" | sed '1d' | sed '/^\*/d' | sed '/^List of devices attached/d' | sed '/^[[:space:]]*$/d')"; [ "$(printf '%s\n' "$rows" | sed '/^$/d' | wc -l | tr -d ' ')" = "1" ] || { echo "STOP: expected exactly one device row"; return 1; }; [ "$rows" = "$(printf '%s\tdevice' "$SERIAL")" ] || { echo "STOP: the only row must be $SERIAL in state device"; return 1; }; avd="$(adb -s "$SERIAL" emu avd name 2>/dev/null | head -1 | tr -d '\r')"; [ "$avd" = "$1" ] || { echo "STOP: AVD is '$avd', expected '$1'"; return 1; }; }
emulator -version
adb --version
```

Expect emulator 37.2.13.0 (the spike's install). From here
on, every `adb -s` line starts with
`sg_guard stargate-m4-files &&`: the guard reads one fresh
`adb devices` inventory and allows the command only with
exactly one row, exactly `$SERIAL` in state `device`, and
the AVD name `stargate-m4-files`. Anything else prints
STOP: stop and report, do not continue.

### Step 1 — free space

```sh
df -h /
```

Need at least 12 GB free (transfer 0.7 + image 1.6 + AVD
plus the spec's 5 GB margin); record the number. Below
that: stop and report.

### Step 2 — cleanup log first (before creating anything)

```sh
echo '2026-11-01 M4 files: AVD stargate-m4-files -- undo: rm -rf ~/.android/avd/stargate-m4-files ~/.android/avd/stargate-m4-files.ini' >> ~/stargate-cleanup-log.md
echo '2026-11-01 M4 files: dir ~/stargate-m4 (image, sysdir, logs, screenshots) -- undo: rm -rf ~/stargate-m4' >> ~/stargate-cleanup-log.md
cat ~/stargate-cleanup-log.md
```

### Step 3 — transfer (one image)

In the codespace terminal (leave it running until the
download below finishes, then Ctrl-C):

```sh
cd /workspaces/m4a-tools/out
/usr/local/bin/python3 -m http.server 8765 --bind 127.0.0.1
```

Forward port 8765 as PRIVATE in the Codespaces Ports
panel and copy its forwarded address (no trailing slash).
On the Mac:

```sh
mkdir -p ~/stargate-m4
FH=PASTE-FORWARDED-ADDRESS-HERE
curl -fSL -o ~/stargate-m4/system-stargate-files.img.gz "$FH/build-files/system-stargate-files.img.gz"
shasum -a 256 ~/stargate-m4/system-stargate-files.img.gz
```

Must equal
`30d84ffe5161971624d5651b65ca03946db01da53b2ac4c6d5bcccdd31fb2be5`.
Mismatch = stop.

```sh
gunzip -k ~/stargate-m4/system-stargate-files.img.gz
shasum -a 256 ~/stargate-m4/system-stargate-files.img
```

Must equal
`25f3b379df16333e60c40773249c95db36e2c8b924897d3ddc3779eb0cbce0ea`.
Mismatch = stop. If Safari unpacked the download on its
own, verify this `.img` hash only.

### Step 4 — create the AVD

```sh
echo no | avdmanager create avd -n stargate-m4-files -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
avdmanager list avd
```

`Could not load devices.xml` warnings are harmless (the
spike saw them too); `list avd` must show
`stargate-m4-files`.

### Step 5 — sysdir of symlinks

```sh
mkdir -p ~/stargate-m4/sysdir-files
ln -s ~/stargate-spike/sdk/system-images/android-34/default/arm64-v8a/* ~/stargate-m4/sysdir-files/
rm ~/stargate-m4/sysdir-files/system.img
ln -s ~/stargate-m4/system-stargate-files.img ~/stargate-m4/sysdir-files/system.img
ls -l ~/stargate-m4/sysdir-files/system.img
```

The last line must show `system.img` pointing at your
`system-stargate-files.img`. Never `-system` (it silently
boots stock) and never `-writable-system`.

### Step 6 — boot and boot record

```sh
adb devices
SERIAL=emulator-5554
emulator -avd stargate-m4-files -sysdir ~/stargate-m4/sysdir-files -no-snapshot -wipe-data -no-metrics > ~/stargate-m4/files-boot.log 2>&1 &
for i in $(seq 1 60); do line=$(adb devices 2>/dev/null | tr -d '\r' | sed '1d' | sed '/^\*/d' | sed '/^List of devices attached/d' | sed '/^[[:space:]]*$/d'); [ "$line" = "$(printf '%s\tdevice' "$SERIAL")" ] && break; sleep 10; done
for i in $(seq 1 60); do sg_guard stargate-m4-files || break; v=$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
sg_guard stargate-m4-files && adb -s "$SERIAL" shell getprop sys.boot_completed
sg_guard stargate-m4-files && adb -s "$SERIAL" emu avd name
sg_guard stargate-m4-files && adb -s "$SERIAL" shell cat /system/stargate-build-id.txt
sg_guard stargate-m4-files && adb -s "$SERIAL" exec-out screencap -p > ~/stargate-m4/files-home.png
open ~/stargate-m4/files-home.png
```

The first loop only runs `adb devices` (host inventory,
touches no device) until exactly the one row appears; every
`-s` line after is guarded. Expect: `sys.boot_completed`
is `1`; AVD name is `stargate-m4-files`; the build ID
reads back exactly `build_id=m4a-files-cc8f48d6`; the
screenshot shows the StarGate home screen with Files only
(no Assistant, no chooser). Record every line's output;
the adb list for the record holds no `install` or `push`.

### Step 7 — Files tests

```sh
sg_guard stargate-m4-files && adb -s "$SERIAL" shell am start -n com.stargate.files/.FilesActivity
sleep 3
sg_guard stargate-m4-files && adb -s "$SERIAL" forward tcp:8080 tcp:8080
echo "stargate-files-test" > ~/stargate-m4/upload.txt
shasum -a 256 ~/stargate-m4/upload.txt
CODE=READ-FROM-EMULATOR-SCREEN
curl -fSL -X PUT --data-binary @$HOME/stargate-m4/upload.txt "http://127.0.0.1:8080/files/upload.txt?code=$CODE" && echo UPLOAD-OK
curl -fSL -o ~/stargate-m4/download.txt "http://127.0.0.1:8080/files/upload.txt?code=$CODE" && echo DOWNLOAD-OK
shasum -a 256 ~/stargate-m4/download.txt
curl -fSL "http://127.0.0.1:8080/files?code=$CODE"
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8080/files/upload.txt"
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8080/files/upload.txt?code=WRONGCODE12"
sg_guard stargate-m4-files && adb -s "$SERIAL" shell ss -ltn
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iTCP:5037 -sTCP:LISTEN
```

The Files app on the emulator screen shows `Access code:`
plus 12 characters: put them on the CODE line. Expect:
UPLOAD-OK and DOWNLOAD-OK print; both shasums match each
other and the upload's; the list shows `upload.txt`; both
refusal lines print `403`; `ss` shows `127.0.0.1:8080`
(listening on loopback only); the first `lsof` shows
`127.0.0.1:8080` (the forward); the second shows
`127.0.0.1:5037` (the ADB server was not started with
`-a`). If `ss` is missing on the emulator, run this line
instead of the `ss` line:

```sh
sg_guard stargate-m4-files && adb -s "$SERIAL" shell netstat -ltn
```

### Step 8 — shut down

```sh
sg_guard stargate-m4-files && adb -s "$SERIAL" emu kill
sleep 10
adb devices
```

The last line must show no device rows. Paste back: step
1 free space; both step 3 shasums; step 6
(`sys.boot_completed`, AVD name, build ID, screenshot
verdict, and any boot-log tail on failure); step 7 (both
file shasums, list output, both refusal codes, all three
listener outputs); and anything you had to deviate, with
exact commands.

## Part 3A: Assistant app in the image, model after boot (code)

Spec 4d (owner decision 1 Nov 22:57): the Assistant APK goes
into the Route A image like the Files app; only the model
files are copied after first boot into the app's own folder.
No APK is ever installed after boot. Every output, the
manifest, the launcher and this record carry "apps in image;
model added after boot".

Placement (measured from the approved snapshot
`/workspaces/m4a-tools/inputs/assistant-1936549b.apk`,
24,777,688 B,
`1936549be6da213eb543be672a20fbf5f74c4f2a03de61939d4bbb1a1a95c187`):
the APK lands at
`/system/app/StargateAssistant/StargateAssistant.apk` with
the same debugfs method as the Files app (0644/0755, 0:0,
`system_file` with the NUL fix). Its 6 native libraries are
stored uncompressed but none is page-aligned, and the APK
must not be changed or re-signed, so they ship in a `lib/`
folder beside it as stock prebuilts do: `lib/arm64/` (3
`.so`, 10,526,976 B) and `lib/x86_64/` (3 `.so`,
13,479,504 B), 24,006,480 B in total. The APK requests no
permissions (aapt2 badging shows none).

Fit against Part 2's measured system free space
(67,137,536 bytes / 64 inodes): new content 48,813,804
bytes (48,865,280 with block rounding: APK 24,777,688 +
libs 24,006,480 + launcher 12,787 + files 16,811 + build
ID 38; launcher/files sizes are the Part 2 APKs, the Step
3 launcher bakes a longer config) and 16 inodes (launcher
2 + files 2 + assistant 2 + `lib/` 1 + ABI dirs 2 + libs
6 + build ID 1). Both fit with margin; the build
re-checks bytes and inodes from dumpe2fs before writing.

The model never goes in the image. A model that fits the
product partition still goes in (`in_image`); the real
1,138,135,470-byte model takes the `after_boot` path. The
build copies the manifest's files from `/workspaces/m4b-downloads`
into an immutable payload snapshot
`<out>/model-payload/` (read-only files), binds them first
(exit 9 when the model ID, revision, file names or sizes
differ from the M3 handoff, or a hash is missing), then
hashes the copied bytes (exit 8 when a file is missing or
its hash differs). `build-manifest.json` gains
`model_delivery` (`in_image`, `after_boot` or `none`), the
payload snapshot path, and each model file's name, bytes,
SHA-256 and target under
`/sdcard/Android/data/com.stargate.assistant/files/model/`.
`android/build_apps.sh` takes `--labels '["..."]'` so the
launcher bakes the after-boot label; the build refuses a
launcher baked for another plan or label set.

Section 5 checks read the real image: the Assistant APK
present with the manifest's SHA-256 and no permissions;
its `lib/` files byte-identical to the APK's embedded
copies; no model file anywhere in the image or any APK's
assets (exit 8); the launcher lists the Assistant. Every
APK in the image is policed against a stock package
baseline recorded from the pristine regions (89 APKs on
the real image), not just ours. The Assistant screen keeps
its existing per-file check lines (`ModelFiles.checkDir`:
"file missing: …" until each file arrives); no downloader,
no network code.

Reviewer notes landed: `dumpe2fs` in `require_tools`,
every APK compared against the stock baseline, and the
fake-adb guard harness committed
(`tests/test_mac_steps.py`: `sg_guard` extracted from this
doc as-is, 12 cases under sh, bash and zsh, plus every
`adb -s` line carrying the guard).

Files-only revalidation (read-only, no rebuild, original
provenance kept): `system-stargate-files.img` passes the
new section 5 checks (exit 0 against the 89-APK baseline)
and its artifact hashes still match (image
`25f3b379df16333e60c40773249c95db36e2c8b924897d3ddc3779eb0cbce0ea`);
its manifest predates the `model_delivery` /
`model_payload_path` fields (3 schema notes), which is
recorded, not repaired.

Tests: 6 new section 4d cases on small synthetic images
(correct payload passes; same-size wrong-hash fails 8;
missing file fails 8; names/sizes off the handoff stop 9;
model file in the image fails 8; Files-only with a payload
fails 8), lib placement plus tamper, baseline foreign and
removed cases, delivery schema cases, and the guard
harness. Full suite: 590 tests, 589 pass, 1 skip (the
known handover-committed skip), 0 failures.

## Part 3B: Files + Assistant build (BUILD READY, MAC PROOF PENDING)

Plan `demo/plans/flagship-files-assistant.plan.json`
re-validated first (`plan is valid`, exit 0), SHA
`1f8f2553fee50c71985d4988e466a943094230cccdf1d64e7f2e584ebb457418`
matching the manifest's `plan_sha256`. Build ID
`m4a-assistant-files-1f8f2553`.

APKs rebuilt for this plan+ID (locked, sequential, d8
`-JXmx2g`), baked with
`--labels '["apps in image; model added after boot"]'`
into a scratch `--out` dir (the Part 2 `build-files/`
outputs were never in the wipe path), then installed as
the build inputs: launcher 12787 B
`0e57e4ac862f426310d7b103e59bfe3a987bb0ad17b5f10dc68780eb9266e79f`,
files 16811 B
`6faad5f25eb99fcee1b48778ae5d1f412f54863398cadde74a160e37c9dcff0d`.

Disk before the build: 18 GB free on `/workspaces`. Peak
new use estimated 9 GB (build transient ~4.5: image copy
1.6 + regions 1.3 + baseline dumps ~0.5 + payload 1.14;
finished build ~3.4: img 1.6 + gz ~0.66 + payload 1.14).
18 >= 9 + 5: proceed. The build ran under
`flock /workspaces/.android-build.lock`, single attempt,
no retry loop.

Build (`/workspaces/m4a-tools/out/build-files-assistant`),
exit 0 with the section 5 checks passing
(`model_delivery=after_boot`):
- Image `system-stargate-files-assistant.img`: 1,622,147,072 B
  (== stock == limit),
  `6f2ed5d670267e23ff1103808e5262be797293c68f20f275f37284bf06cc0f31`.
- Transfer `system-stargate-files-assistant.img.gz`: 654,703,016 B,
  `d8b05d2d76ebd7c444ad87aed772abf6d562f9f91cf2f5bdf5f8f8336822ef82`.
- Payload `model-payload/` (read-only files, hashed copies):
  `Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte`
  1,135,951,488 B
  `8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b`,
  `tokenizer.model` 2,183,982 B
  `82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55`.
- Manifest `demo/manifests/build-files-assistant.build-manifest.json`
  (copy of the build's `build-manifest.json`; artifact
  paths are relative to
  `/workspaces/m4a-tools/out/build-files-assistant/`):
  route A, both artifacts, all three apps
  (assistant `1936549b…`), both model files with
  `/sdcard/` targets, `model_delivery: after_boot`,
  the after-boot label, `assistant_manifest_sha256`
  `b6add2c5…` (the committed handover, re-hashed).
  Images, APKs and payload stay out of git.

Nothing else rebuilt: `build-files/` re-hashed after the
build, image `25f3b379…` and `.gz` `30d84ffe…` unchanged.
Per spec section 10 this is build ready with the Mac
proof pending, never complete before the owner's run.

## Part 3C: Mac steps for Files + Assistant (written, not run)

Run every block below in ONE Terminal session on the Mac, in
order (the guard, SERIAL, FH and CODE live in that session
only). Each line pastes cleanly into zsh: one command per
line, no placeholders in brackets. Three lines need your
own value, each marked in capitals: FH (step 3, your
forwarded address), SERIAL (step 6, only if the emulator is
not emulator-5554), CODE (step 11, read from the emulator
screen). No installs: everything reuses `~/stargate-spike`
from the spike. All shell blocks below pass `zsh -n`.

The guard is byte-identical to Part 2D's (the committed
harness in `tests/test_mac_steps.py` extracts both copies
and runs the 12 fake-adb cases under sh, bash and zsh).

### Step 0 — environment (first, in every new Terminal)

```sh
export SG=~/stargate-spike
export ANDROID_HOME=$SG/sdk
export ANDROID_USER_HOME=~/.android
export JAVA_HOME=$SG/jdk-17/Contents/Home
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$JAVA_HOME/bin:$PATH
sg_guard() { local inv rows avd rc; inv="$(adb devices 2>/dev/null)"; rc=$?; [ "$rc" = "0" ] || { echo "STOP: adb devices failed"; return 1; }; inv="$(printf '%s' "$inv" | tr -d '\r')"; rows="$(printf '%s\n' "$inv" | sed '1d' | sed '/^\*/d' | sed '/^List of devices attached/d' | sed '/^[[:space:]]*$/d')"; [ "$(printf '%s\n' "$rows" | sed '/^$/d' | wc -l | tr -d ' ')" = "1" ] || { echo "STOP: expected exactly one device row"; return 1; }; [ "$rows" = "$(printf '%s\tdevice' "$SERIAL")" ] || { echo "STOP: the only row must be $SERIAL in state device"; return 1; }; avd="$(adb -s "$SERIAL" emu avd name 2>/dev/null | head -1 | tr -d '\r')"; [ "$avd" = "$1" ] || { echo "STOP: AVD is '$avd', expected '$1'"; return 1; }; }
emulator -version
adb --version
```

Expect emulator 37.2.13.0 (the spike's install). From here
on, every `adb -s` line starts with
`sg_guard stargate-m4-files-assistant &&`: the guard reads
one fresh `adb devices` inventory and allows the command
only with exactly one row, exactly `$SERIAL` in state
`device`, and the AVD name `stargate-m4-files-assistant`.
Anything else prints STOP: stop and report, do not
continue.

### Step 1 — free space

```sh
df -h /
```

Need at least 14 GB free (transfer ~1.9 + image 1.6 + AVD
plus the spec's 5 GB margin); record the number. Below
that: stop and report.

### Step 2 — cleanup log first (before creating anything)

```sh
echo '2026-11-02 M4 files+assistant: AVD stargate-m4-files-assistant -- undo: rm -rf ~/.android/avd/stargate-m4-files-assistant ~/.android/avd/stargate-m4-files-assistant.ini' >> ~/stargate-cleanup-log.md
cat ~/stargate-cleanup-log.md
```

The `~/stargate-m4` dir row from Part 2D already covers
the transfer folder; only the AVD row is new.

### Step 3 — transfer (one image, two model files)

In the codespace terminal (leave it running until every
download below finishes, then Ctrl-C):

```sh
cd /workspaces/m4a-tools/out
/usr/local/bin/python3 -m http.server 8765 --bind 127.0.0.1
```

Forward port 8765 as PRIVATE in the Codespaces Ports
panel and copy its forwarded address (no trailing slash).
On the Mac:

```sh
mkdir -p ~/stargate-m4
FH=PASTE-FORWARDED-ADDRESS-HERE
curl -fSL -o ~/stargate-m4/system-stargate-files-assistant.img.gz "$FH/build-files-assistant/system-stargate-files-assistant.img.gz"
shasum -a 256 ~/stargate-m4/system-stargate-files-assistant.img.gz
```

Must equal
`d8b05d2d76ebd7c444ad87aed772abf6d562f9f91cf2f5bdf5f8f8336822ef82`.
Mismatch = stop.

```sh
gunzip -k ~/stargate-m4/system-stargate-files-assistant.img.gz
shasum -a 256 ~/stargate-m4/system-stargate-files-assistant.img
```

Must equal
`6f2ed5d670267e23ff1103808e5262be797293c68f20f275f37284bf06cc0f31`.
Mismatch = stop. If Safari unpacked the download on its
own, verify this `.img` hash only.

```sh
curl -fSL -o ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte "$FH/build-files-assistant/model-payload/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte"
curl -fSL -o ~/stargate-m4/tokenizer.model "$FH/build-files-assistant/model-payload/tokenizer.model"
shasum -a 256 ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte ~/stargate-m4/tokenizer.model
```

Must equal `8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b`
for the `.pte` and
`82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55`
for the tokenizer. Mismatch = stop.

### Step 4 — create the AVD

```sh
echo no | avdmanager create avd -n stargate-m4-files-assistant -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
avdmanager list avd
```

`Could not load devices.xml` warnings are harmless (the
spike saw them too); `list avd` must show
`stargate-m4-files-assistant`. Then size it (model
1,138,135,470 bytes + 1 GB headroom, spec 4b):

```sh
CFG=~/.android/avd/stargate-m4-files-assistant.avd/config.ini
test -f "$CFG"
sed -i '' '/^hw.ramSize=/d; /^disk.dataPartition.size=/d' "$CFG"
printf '%s\n' 'hw.ramSize=6144' 'disk.dataPartition.size=4G' | tee -a "$CFG"
grep -E '^(hw.ramSize|disk.dataPartition.size)=' "$CFG"
```

The last line must print exactly `hw.ramSize=6144` and
`disk.dataPartition.size=4G`.

### Step 5 — sysdir of symlinks

```sh
mkdir -p ~/stargate-m4/sysdir-assistant
ln -s ~/stargate-spike/sdk/system-images/android-34/default/arm64-v8a/* ~/stargate-m4/sysdir-assistant/
rm ~/stargate-m4/sysdir-assistant/system.img
ln -s ~/stargate-m4/system-stargate-files-assistant.img ~/stargate-m4/sysdir-assistant/system.img
ls -l ~/stargate-m4/sysdir-assistant/system.img
```

The last line must show `system.img` pointing at your
`system-stargate-files-assistant.img`. Never `-system`
(it silently boots stock) and never `-writable-system`.

### Step 6 — boot and boot record

```sh
adb devices
SERIAL=emulator-5554
emulator -avd stargate-m4-files-assistant -sysdir ~/stargate-m4/sysdir-assistant -no-snapshot -wipe-data -no-metrics > ~/stargate-m4/assistant-boot.log 2>&1 &
for i in $(seq 1 60); do line=$(adb devices 2>/dev/null | tr -d '\r' | sed '1d' | sed '/^\*/d' | sed '/^List of devices attached/d' | sed '/^[[:space:]]*$/d'); [ "$line" = "$(printf '%s\tdevice' "$SERIAL")" ] && break; sleep 10; done
for i in $(seq 1 60); do sg_guard stargate-m4-files-assistant || break; v=$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell getprop sys.boot_completed
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" emu avd name
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell cat /system/stargate-build-id.txt
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" exec-out screencap -p > ~/stargate-m4/assistant-home.png
open ~/stargate-m4/assistant-home.png
```

The first loop only runs `adb devices` (host inventory,
touches no device) until exactly the one row appears; every
`-s` line after is guarded. Expect: `sys.boot_completed`
is `1`; AVD name is `stargate-m4-files-assistant`; the
build ID reads back exactly
`build_id=m4a-assistant-files-1f8f2553`; the screenshot
shows the StarGate home screen with Files and Assistant
and the label "apps in image; model added after boot".
Record every line's output.

The stock image can restart once on first boot, so wait
for boot again before touching the app:

```sh
for i in $(seq 1 60); do sg_guard stargate-m4-files-assistant || break; v=$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell getprop sys.boot_completed
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell pm path com.stargate.assistant
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell sha256sum /system/app/StargateAssistant/StargateAssistant.apk
```

Expect `1` again; `pm path` prints
`package:/system/app/StargateAssistant/StargateAssistant.apk`;
the on-device APK hash equals
`1936549be6da213eb543be672a20fbf5f74c4f2a03de61939d4bbb1a1a95c187`.
Mismatch = stop.

### Step 7 — first launch, then push the model files

The app creates its own folder on first launch; never
`mkdir` it from the shell:

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am start -W -n com.stargate.assistant/.MainActivity
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell ls -ld /sdcard/Android/data/com.stargate.assistant/files/model/
```

The `ls` line must show the model folder. If it is
missing, stop: the app did not create it (its error
screen says which check failed). Then the two pushes
(recorded exception, spec 4d — the only pushes in this
record, each already checked with `shasum` in step 3):

```sh
# recorded exception, spec 4d
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" push ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/
# recorded exception, spec 4d
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" push ~/stargate-m4/tokenizer.model /sdcard/Android/data/com.stargate.assistant/files/model/
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell sha256sum /sdcard/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/tokenizer.model
```

The two on-device hashes must equal the step 3 hashes
(`8715cdba…`, `82e9d319…`). Mismatch = stop.

### Step 8 — networking off

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell svc wifi disable
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell svc data disable
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell settings get global wifi_on
```

The last line must print `0`; the status bar must show
no connectivity.

### Step 9 — ask one ordinary question

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
```

On the emulator screen: read the two on-screen model
hashes (must match step 3), type exactly `Name three
planets`, submit, and note the answer with its time.
Then back in the Terminal:

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" logcat -d | grep 'given pos_'
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" exec-out screencap -p > ~/stargate-m4/assistant-answer.png
open ~/stargate-m4/assistant-answer.png
```

The logcat line must show `num_prompt_tokens`; the
screenshot keeps the answer with the hashes on screen.
Paste back the logcat line, the answer, and its time.

### Step 10 — if the app still says not found (EMULATOR ONLY)

Only when the app's error screen says it cannot read the
pushed files (foreign ownership left by an earlier
shell-made folder). Never on a real phone. Re-push as
root, hand the files to the app's uid and gid, fix the
SELinux labels, then drop root and relaunch (from M4b
docs section 10):

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" root
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" wait-for-device
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" push ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /data/media/0/Android/data/com.stargate.assistant/files/model/
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" push ~/stargate-m4/tokenizer.model /data/media/0/Android/data/com.stargate.assistant/files/model/
sg_guard stargate-m4-files-assistant && FUID=$(adb -s "$SERIAL" shell stat -c %u /data/media/0/Android/data/com.stargate.assistant/files | tr -d '\r')
sg_guard stargate-m4-files-assistant && FGID=$(adb -s "$SERIAL" shell stat -c %g /data/media/0/Android/data/com.stargate.assistant/files | tr -d '\r')
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell chown "$FUID:$FGID" /data/media/0/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /data/media/0/Android/data/com.stargate.assistant/files/model/tokenizer.model
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell restorecon -R /data/media/0/Android/data/com.stargate.assistant/files/model/
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" unroot
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" wait-for-device
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
```

`root`/`unroot` restart adbd: if the guard denies while
the emulator is still re-attaching, wait a moment and
re-paste the line. Skip this whole step when step 9
already works.

### Step 11 — Files tests

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell am start -n com.stargate.files/.FilesActivity
sleep 3
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" forward tcp:8080 tcp:8080
echo "stargate-files-test" > ~/stargate-m4/upload.txt
shasum -a 256 ~/stargate-m4/upload.txt
CODE=READ-FROM-EMULATOR-SCREEN
curl -fSL -X PUT --data-binary @$HOME/stargate-m4/upload.txt "http://127.0.0.1:8080/files/upload.txt?code=$CODE" && echo UPLOAD-OK
curl -fSL -o ~/stargate-m4/download.txt "http://127.0.0.1:8080/files/upload.txt?code=$CODE" && echo DOWNLOAD-OK
shasum -a 256 ~/stargate-m4/download.txt
curl -fSL "http://127.0.0.1:8080/files?code=$CODE"
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8080/files/upload.txt"
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8080/files/upload.txt?code=WRONGCODE12"
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell ss -ltn
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iTCP:5037 -sTCP:LISTEN
```

As Part 2D: UPLOAD-OK and DOWNLOAD-OK print; both
shasums match each other and the upload's; the list shows
`upload.txt`; both refusal lines print `403`; `ss` shows
`127.0.0.1:8080`; the first `lsof` shows `127.0.0.1:8080`;
the second shows `127.0.0.1:5037`. If `ss` is missing on
the emulator, run this line instead of the `ss` line:

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" shell netstat -ltn
```

### Step 12 — shut down

```sh
sg_guard stargate-m4-files-assistant && adb -s "$SERIAL" emu kill
sleep 10
adb devices
```

The last line must show no device rows. Paste back: step
1 free space; all four step 3 shasums; step 4 sizing
lines; step 6 (`sys.boot_completed` twice, AVD name,
build ID, screenshot verdict, `pm path`, on-device APK
hash); step 7 (folder listing, both on-device model
hashes); step 8 (`wifi_on`); step 9 (on-screen hashes,
question, answer, time, logcat line, screenshot verdict);
step 10 (skipped, or every line's output); step 11 (both
file shasums, list output, both refusal codes, all three
listener outputs); and anything you had to deviate, with
exact commands.
