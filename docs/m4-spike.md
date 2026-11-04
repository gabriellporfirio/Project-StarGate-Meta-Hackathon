# M4 spike: can we build and boot a StarGate image?

Status: codespace work done 1 Nov 2026 ~00:05 UTC; Mac results owner-run
1 Nov 2026 — Path A PASSED, Path B PASSED (see "Mac results (owner-run,
1 Nov 2026)" below). Original Mac steps kept verbatim; corrections added
as notes.
Spec: `gabriellporfirio/Project-Stargate`, `plan/milestones/M4-spike.md` at
`4db11e438b78005afda39c64ecc0834d2e745496` (v2). Spec wins on differences.
Branch `m4-spike` from `1673b97fb97d2612f42fa28c6fe07caa2a937bb3` (verified
clean at start). Clock: start 2026-10-31T23:38:41Z, 4 h stop 03:38:41Z.

## Q1 — Image (Q7)

Choice: `system-images;android-34;default;arm64-v8a`, revision 4.
Android 14 (API 34, extension level 7), AOSP "Default" tag, arm64-v8a.

- URL: `https://dl.google.com/android/repository/sys-img/android/arm64-v8a-34_r04.zip`
  (from Google's catalog `sys-img/android/sys-img2-1.xml`)
- Size: 705,115,396 bytes (~672.5 MiB)
- Catalog SHA-1: `d9f2011131919abe952814e041f16f317242c3fa` (verified OK)
- SHA-256: `1447958a4c6747c44390ac5f5f4c894be6d1dfce93868a0385a95c5f0ae4c339`
- Min emulator per catalog: 29.1.11 (any recent emulator qualifies)

Why this one:

1. arm64 is mandatory (owner's Mac is Apple Silicon).
2. It is the only variant with explicit official documentation for `adb root`:
   Google's AVD docs say Play Store images "are signed with a release key,
   which means that you can't get elevated privileges (root)" and name AOSP
   images as the ones where `adb root`/`unroot` work. The `google_apis`
   variant's root status is not explicitly documented, and the spec says
   check, don't assume. Build fingerprint confirms
   `.../emu64a:14/...:userdebug/test-keys`.
3. Smallest: google_apis API 34 r14 is 1,610,393,229 bytes, API 35 r09 is
   1,778,933,980 bytes. Matters for the 10 GB budget, the 2.5 h Path A
   window, and the browser-download transfer to the Mac.
4. API 34 over 35: mature, no 16 KB-page-size complications.
5. No Google apps: fewer moving parts for the repack; the launcher needs
   framework APIs only.

`adb root` empirical check happens on the Mac after boot (see Mac steps).

## Q2 — Image layout (inspected, not assumed)

Zip: 22 files, 1,757,882,519 bytes unpacked. Partition images:

| File | Bytes | SHA-256 | Contents |
|---|---|---|---|
| system.img | 1,622,147,072 | `250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d` | GPT: vbmeta + super (below) |
| vendor.img | 96,468,992 | `d773f0f86590257bc45c979085d03a25b69be2f0b2bcf87fd0741d74b4ed1339` | GPT: one ext4 `vendor` partition |
| ramdisk.img | 1,791,657 | `91d2259c141607b5fda903afa4d0a6912389753f40e24015aed74db19fd6bd6c` | LZ4 compressed |
| userdata.img | 1,048,576 | `30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58` | 1 MB seed (emulator expands) |
| kernel-ranchu | 12,784,248 | `4076bd230e0832e23837e62c1fb03daf5eb4fd5aac731818320c26fa1bf8b2e5` | gzip kernel |
| encryptionkey.img | 18,874,368 | (not hashed; untouched) | GPT metadata/key disk |

Plus `build.prop` (4,692 bytes), `VerifiedBootParams.textproto`, `source.properties`,
`advancedFeatures.ini` (`DynamicPartition = on`), `NOTICE.txt`, `data/` seed files.

system.img GPT: `vbmeta` @1,048,576 (1 MiB, AVB0, flags 0x0 — neither
hashtree- nor verification-disabled at vbmeta level) and `super` @2,097,152
(1,619,001,344 bytes). super holds LP metadata (geometry @+4K, tables @+12K)
and five logical partitions, each a single contiguous linear extent:

| LP partition | Abs offset | Bytes | Filesystem |
|---|---|---|---|
| system | 3,145,728 | 853,868,544 | ext4, 840,314,880 B (205,155 x 4K) + ~13.5 MB verity trailer |
| system_dlkm | 857,735,168 | 4,370,432 | (not inspected) |
| system_ext | 862,978,048 | 149,532,672 | (not inspected) |
| product | 1,012,924,416 | 305,967,104 | (not inspected) |
| vendor | 1,319,108,608 | 93,540,352 | (not inspected; separate vendor.img also ships) |

System ext4 verified: `e2fsck -n` clean, 2,736/2,800 files, 16,391 free
blocks, 64 free inodes, no journal, no metadata_csum. System-as-root layout
(fs root = device `/`, real system under `/system`). `/system/build.prop`
in the fs is byte-identical to the loose `build.prop` (extent map proven).

Boot verification: vbmeta carries one hashtree descriptor (system,
dm-verity) plus whole-image hash descriptors (vendor_boot sha256;
product, system_dlkm, system_ext, vendor sha1) and build prop descriptors.
`VerifiedBootParams.textproto` passes the vbmeta digest
(`253e1a8d...`, size 6,656) to the kernel; our change does not touch vbmeta
so that digest stays valid. Whether dm-verity is *enforced* on the emulator
boot path is not stated in the docs — the Mac boot test decides (Path A).

## Q3 — Path A (image): repack done in codespace, boot pending

Method (`spike/m4/path_a_repack.py`, stdlib only + pre-existing debugfs):
extract the system ext4, `e2fsck -n`, cross-check build.prop, `debugfs write`
the marker, fix mode with `sif` (this debugfs has no chmod/chown), set the
SELinux label with `ea_set` plus a 1-byte NUL-terminator patch (debugfs omits
it; kernel requires it — verified byte-identical to build.prop's xattr),
re-verify, splice back at the same offset. Same size, in place.

- Marker path (device): `/system/stargate-marker.txt` (67 bytes, 0644 0:0,
  `u:object_r:system_file:s0`, 26-byte NUL-terminated xattr)
- Selection: `stargate-spike-a`
- Random (128-bit, from /dev/urandom for this run): `5c056da9fc167da1caf8d9c05d1040d1`
- Stock system.img SHA-256: `250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d`
- Stock fs region SHA-256: `5ba0ffb06a3e56e0c54afbbe8b49915231be8531f348a12647a19004f6827880`
- Output image: `/workspaces/m4-tools/system-stargate-a.img`, 1,622,147,072 B
- Output SHA-256: `58961c2024ab3506ed790bf156690cf2d4542889eb05b4afa688133c27cd20e5`
- Patched fs region SHA-256: `f1a6b5a2d3ba11fc7ea5634a0c787bd424da21c46baf35411e121702dd0fb785`
- Diff: 179 bytes, all inside the system fs extent — nothing else touched.
- Closed loop: re-extracted the fs from the *output* image, fsck clean,
  marker + xattr read back correctly.

Codespace time on Path A: ~15 min (cap was 2.5 h). Result: **not tested**
until the Mac boots it (see Mac steps). If dm-verity is enforced the boot
fails there with verity errors — that outcome is itself the Path A answer.

## Q4 — Launcher toolchain (lightest route, proven by building)

JDK 17 + build-tools 34.0.0 used directly. No Gradle, no Kotlin compiler,
no commandlinetools/sdkmanager in the codespace (direct pinned-URL downloads
via preinstalled curl; SDK license acceptance recorded below), no Maven
Central (framework-only Java sources, `android.jar` only).

| Tool | Version | Source | Size (bytes) | SHA-256 | Installed to |
|---|---|---|---|---|---|
| Eclipse Temurin JDK | 17.0.21+9, hotspot linux x64 | github.com/adoptium (API-pinned) | 193,594,705 | `8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711` | /workspaces/m4-tools/jdk-17/ |
| Build-tools | 34.0.0 linux | dl.google.com repository2-1.xml | 61,224,257 | `e858c4b60069d0431051b225d384413b1643e1289b00a4825aed347f25bd510f` | .../android-sdk/build-tools/34.0.0/ |
| Platform android-34 | ext7_r03 (highest-rev channel-0 entry = what sdkmanager resolves) | dl.google.com repository2-1.xml | 63,180,081 | `16fdb74c55e59ae3ef52def135aec713508467bd56d7dabcd8c9be31fa8b20f3` | .../android-sdk/platforms/android-34/ |

Catalog SHA-1s all verified OK (JDK via Adoptium SHA-256). JDK 17 is the
documented toolchain generation for API 34 (AGP 8.x requires JDK 17).
Bonus: `aapt2 2.19`, `D8 8.2.2-dev`, `apksigner 0.9`, `zipalign` all work;
`platform-34-ext12` fallback was not needed.

Build (`spike/m4/build_launcher.sh`, `spike/m4/launcher/` sources):
javac (bootclasspath android.jar) -> d8 -> aapt2 link (manifest only, no
resources) -> add classes.dex -> zipalign -> apksigner (throwaway debug key).
One fix during the spike: this apksigner takes `--ks-pass pass:x` (space).

- APK: `/workspaces/m4-tools/stargate-launcher.apk`, 8,619 bytes
- SHA-256: `81048cafae18d901b4aa34514c533522d1779f6ff22ce1476bde73de80a6150d`
- Package `com.stargate.launcher`, minSdk 28, targetSdk 34, label StarGate,
  HOME intent confirmed (`provides-component: launcher`, MAIN+HOME in xmltree)
- `apksigner verify` passes; signer cert SHA-256
  `606080644678d9b64aa730cb9e7f6e43de5772b64b3df5195a4622a5ee21b30dc`
- (`stargate-launcher.apk.idsig` beside it is apksigner's sidecar, not shipped.)

Path B status: APK built (**configuration bundle, not an image**). Install +
home-screen proof pending Mac steps. Result: **not tested** until then.

## Q5 — Mac setup and owner-run steps

The owner runs everything below on the Mac. Nothing here was run by Muse.

```
INSTALL APPROVAL REQUIRED (Mac — owner runs; ~2.0 GB downloads, ~11 GB with AVDs)
All installs are user-local: SDK under ~/stargate-spike/sdk, JDK (if needed)
under ~/stargate-spike/jdk-17, AVDs under ~/.android. No sudo, no system
changes: Apple Silicon uses the built-in Hypervisor.framework (no HAXM) and
needs no Rosetta (native arm64 emulator + arm64 image). Env exports apply to
that Terminal session only — do NOT add them to .zshrc.
Undo: rm -rf ~/stargate-spike ~/.android/avd/stargate-spike-* \
  ~/.android/avd/stargate-spike-*.ini
(then delete the two transferred spike files wherever you put them).

Item 1 — Android command-line tools (sdkmanager, avdmanager)
  What: commandlinetools Mac ARM 15859902
  URL:  https://dl.google.com/android/repository/commandlinetools-mac_arm64-15859902_latest.zip
  Size: 156.1 MB. SHA-256: 835b62a26162b229b441d1f6d4680383815a270809eb33522c0d480fa5002c4e
  To:   ~/stargate-spike/sdk/cmdline-tools/latest/

Item 2 — JDK 17, ONLY if `java -version` fails on your Mac (sdkmanager needs Java)
  What: Eclipse Temurin 17.0.21+9, aarch64 mac, tarball (not the .pkg: no system install)
  URL:  https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.21_9.tar.gz
  Size: 186,192,554 bytes. SHA-256: 78b08c7f5a0598242d5719c022e19479cb4a3aa45c37c19726a757a469ed1c15
  To:   ~/stargate-spike/jdk-17/ (set JAVA_HOME for that Terminal only)

Item 3 — platform-tools 37.0.1, emulator 37.2.13 (stable), system image
  Via:  sdkmanager (verifies catalog SHA-1 itself)
  Sizes: platform-tools darwin 16,110,554 B; emulator darwin 488,733,162 B;
         system image 705,115,396 B (same bytes as the codespace stock zip)
  To:   ~/stargate-spike/sdk/
  Check after install: shasum of the extracted system.img MUST equal the
  manifest stock hash 250ee9bb... (step 2 below). Mismatch = stop.

Item 4 — spike artifacts (browser download from this codespace, step 3 below)
  system-stargate-a.img.gz 639,251,897 B, SHA-256
    854a467577301c22e1b1e7cf1a5c311b57bbe214aa062db4d34043bb91e11f86
  stargate-launcher.apk 8,619 B, SHA-256
    81048cafae18d901b4aa34514c533522d1779f6ff22ce1476bde73de80a6150d
  After gunzip, system-stargate-a.img MUST hash to
    58961c2024ab3506ed790bf156690cf2d4542889eb05b4afa688133c27cd20e5
  Mismatch = stop.
```

### Mac step 0 — free space

`df -h /` — need at least 15 GB free. Record the number for the report.

### Mac step 1 — install tools

```sh
export SG=~/stargate-spike ANDROID_HOME=$SG/sdk ANDROID_USER_HOME=~/.android
export JAVA_HOME=$SG/jdk-17   # only if you installed item 2
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$JAVA_HOME/bin:$PATH
mkdir -p $SG/sdk/cmdline-tools
cd $SG
curl -fSL -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-mac_arm64-15859902_latest.zip
shasum -a 256 cmdtools.zip   # must equal 835b62a2... (item 1)
unzip -q cmdtools.zip -d cmdline-tools-tmp
mkdir -p sdk/cmdline-tools/latest
mv cmdline-tools-tmp/cmdline-tools/* sdk/cmdline-tools/latest/
rm -rf cmdline-tools-tmp cmdtools.zip
sdkmanager --version
yes | sdkmanager --licenses > /dev/null
sdkmanager "platform-tools" "emulator" "system-images;android-34;default;arm64-v8a"
emulator -version
adb --version
```

### Mac step 2 — verify stock image bytes, create AVDs

```sh
shasum -a 256 ~/stargate-spike/sdk/system-images/android-34/default/arm64-v8a/system.img
# MUST equal 250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d. If not: stop.
echo no | avdmanager create avd -n stargate-spike-stock -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
echo no | avdmanager create avd -n stargate-spike-a -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
echo no | avdmanager create avd -n stargate-spike-b -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
```

### Mac step 3 — transfer the two spike artifacts

In the codespace web UI file explorer, download to `~/stargate-spike/`:

- `/workspaces/m4-tools/system-stargate-a.img.gz`
- `/workspaces/m4-tools/stargate-launcher.apk`

```sh
cd ~/stargate-spike
shasum -a 256 system-stargate-a.img.gz stargate-launcher.apk
# MUST equal 854a4675... and 81048caf... (item 4). Mismatch = stop.
gunzip -k system-stargate-a.img.gz
shasum -a 256 system-stargate-a.img
# MUST equal 58961c2024ab3506ed790bf156690cf2d4542889eb05b4afa688133c27cd20e5. If not: stop.
```

NOTE (Mac result, 1 Nov 2026 — transfer correction): the browser download
from the codespace file explorer produced 9-byte files for the .img.gz and
must not be relied on. The working transfer was a private forwarded port
(`python3 -m http.server` bound to 127.0.0.1). Safari unpacked the .gz on
the Mac; hashes verified after transfer (see "Mac results" below).

### Rules for every boot below

- Boot ONE emulator at a time. Note its serial from `adb devices`.
- Before every adb command: `adb devices` must list ONLY the emulator. If a
  physical device is ever listed: stop immediately.
- Every adb command names the serial: `adb -s emulator-5554 ...`
  (replace `emulator-5554` with the actual serial; confirm the AVD with
  `adb -s SERIAL emu avd name`).
- Between tests: `adb -s SERIAL emu kill`, wait for exit.

### Mac step 4 — boot the stock image (required baseline)

```sh
emulator -avd stargate-spike-stock -no-snapshot -wipe-data > stock-boot.log 2>&1 &
adb -s emulator-5554 wait-for-device
for i in $(seq 1 60); do v=$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
adb -s emulator-5554 shell getprop sys.boot_completed   # expect: 1
adb -s emulator-5554 emu avd name                        # expect: stargate-spike-stock
adb -s emulator-5554 root                                # AOSP root check: expect restart as root
adb -s emulator-5554 shell 'id; getprop ro.build.type'  # expect uid=0, userdebug
adb -s emulator-5554 emu kill
```

Record: `sys.boot_completed`, root/id output, and the exact launch command.
Paste back `stock-boot.log` tail (last 30 lines) only if boot fails.

### Mac step 5 — Path A: boot the repacked image (fresh dedicated AVD)

```sh
emulator -avd stargate-spike-a -no-snapshot -wipe-data -system ~/stargate-spike/system-stargate-a.img > patha-boot.log 2>&1 &
# No -writable-system. Nothing installed or changed after boot.
adb -s emulator-5554 wait-for-device
for i in $(seq 1 60); do v=$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
adb -s emulator-5554 shell getprop sys.boot_completed   # expect: 1
adb -s emulator-5554 emu avd name                        # expect: stargate-spike-a
adb -s emulator-5554 shell cat /system/stargate-marker.txt
# expect exactly:
#   selection=stargate-spike-a
#   random=5c056da9fc167da1caf8d9c05d1040d1
adb -s emulator-5554 exec-out screencap -p > ~/stargate-spike/patha-screen.png
adb -s emulator-5554 emu kill
```

NOTE (Mac result, 1 Nov 2026 — Path A launch correction; original command
above kept verbatim for the record): `-system` must not be used.
(1) `emulator -avd stargate-spike-a -no-snapshot -wipe-data -system <img>`
exited at once with "no init encryptionkey.img / Encryption is requested
but failed to create encrypt partition".
(2) With encryptionkey.img copied next to the image, the same `-system`
command booted, but /system/stargate-marker.txt was missing: the emulator
booted the STOCK system and silently ignored `-system`.
(3) Working method (PASSED): build a sysdir of symlinks to every file in
the stock system-image folder, with system.img replaced by a symlink to
system-stargate-a.img, then boot with `-sysdir`:

```sh
emulator -avd stargate-spike-a -sysdir ~/stargate-spike/patha-sysdir -no-snapshot -wipe-data -no-metrics
```

Path A passes only if boot completes AND the marker matches. On failure,
paste the last 50 lines of `patha-boot.log` and any adb errors.

### Mac step 6 — Path B: stock + launcher (configuration bundle, not an image)

```sh
emulator -avd stargate-spike-b -no-snapshot -wipe-data > pathb-boot.log 2>&1 &
adb -s emulator-5554 wait-for-device
for i in $(seq 1 60); do v=$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'); [ "$v" = 1 ] && break; sleep 10; done
adb -s emulator-5554 shell getprop sys.boot_completed   # expect: 1
adb -s emulator-5554 install -r ~/stargate-spike/stargate-launcher.apk  # expect: Success
adb -s emulator-5554 shell cmd package set-home-activity com.stargate.launcher/.HomeActivity
adb -s emulator-5554 shell input keyevent KEYCODE_HOME
sleep 3
adb -s emulator-5554 exec-out screencap -p > ~/stargate-spike/home-b.png
adb -s emulator-5554 emu kill
```

Open `home-b.png`: it must show the StarGate home screen ("StarGate" +
"Services: Files, Assistant"). Paste back every command's output and whether
the screenshot matches (keep the .png files on the Mac).

### What to paste back to Muse

1. Mac free space (step 0) and `emulator -version` output.
2. The four `shasum` results (steps 2–3).
3. Step 4: boot_completed, avd name, root/id output.
4. Step 5: boot_completed, avd name, full marker text (or failure logs).
5. Step 6: install output, set-home output, screenshot verdict (or failure logs).
6. Anything you had to deviate (exact commands used instead).

## Owner results (Mac)

Recorded 1 Nov 2026 in "Mac results (owner-run, 1 Nov 2026)" below, then
committed (one commit on m4-spike, no push).

## Mac results (owner-run, 1 Nov 2026)

Owner-observed results for Q5 steps 0–6. Original steps above are kept
verbatim; corrections are the NOTE boxes on steps 3 and 5.

- Mac: MacBook Pro (Apple silicon), macOS, about 186 GB free (step 0).
- Tools, all installed user-local under `~/stargate-spike` (step 1):
  - Eclipse Temurin JDK 17.0.21+9 (aarch64 mac tarball), SHA-256
    `78b08c7f5a0598242d5719c022e19479cb4a3aa45c37c19726a757a469ed1c15`
  - Android command-line tools 22.0 (Mac ARM zip), SHA-256
    `835b62a26162b229b441d1f6d4680383815a270809eb33522c0d480fa5002c4e`
  - platform-tools / adb 37.0.1, emulator 37.2.13.0 (via sdkmanager)
  - system-images;android-34;default;arm64-v8a (via sdkmanager);
    extracted system.img SHA-256
    `250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d`
    (matches the manifest stock hash, step 2)
- AVDs (step 2): `stargate-spike-stock`, `stargate-spike-a`,
  `stargate-spike-b`, all `pixel_7` with 2 GB RAM. `avdmanager` printed
  "Could not load devices.xml" warnings, but all three AVDs were created.
- Transfer (step 3): the browser download from the codespace file explorer
  produced 9-byte files for the .img.gz and did not work. The working
  transfer was a private forwarded port (`python3 -m http.server` bound to
  127.0.0.1). Safari unpacked the .gz on the Mac. After transfer:
  - `system-stargate-a.img` SHA-256
    `58961c2024ab3506ed790bf156690cf2d4542889eb05b4afa688133c27cd20e5`
    (match)
  - `stargate-launcher.apk` SHA-256
    `81048cafae18d901b4aa34514c533522d1779f6ff22ce1476bde73de80a6150d`
    (match)
- Stock boot (step 4): `sys.boot_completed=1`, avd name
  `stargate-spike-stock`, `adb root` gave uid=0 (root),
  `ro.build.type=userdebug`, clean shutdown, and only `emulator-5554`
  was ever listed (no physical device).
- Path A (step 5): PASSED, with a corrected launch method.
  (1) `emulator -avd stargate-spike-a -no-snapshot -wipe-data -system <img>`
  exited at once with "no init encryptionkey.img / Encryption is requested
  but failed to create encrypt partition".
  (2) With encryptionkey.img copied next to the image, the same `-system`
  command booted, but /system/stargate-marker.txt was missing: the emulator
  booted the STOCK system and silently ignored `-system`. `-system` must
  not be used.
  (3) Working method: a sysdir made of symlinks to every file in the stock
  system-image folder, with system.img replaced by a symlink to
  system-stargate-a.img, launched as
  `emulator -avd stargate-spike-a -sysdir ~/stargate-spike/patha-sysdir -no-snapshot -wipe-data -no-metrics`.
  Result: `sys.boot_completed=1`, avd name `stargate-spike-a`, and
  /system/stargate-marker.txt read back exactly `selection=stargate-spike-a`
  and `random=5c056da9fc167da1caf8d9c05d1040d1`. No install or push after
  boot (`-writable-system` not used).
- Path B (step 6): PASSED. `adb install -r stargate-launcher.apk` gave
  Success, `set-home-activity` gave Success, and the home-screen screenshot
  shows "StarGate" and "Services: Files, Assistant".
- Spike conclusion: Path A passed, so per the M4 spec section 2, M4 takes
  Route A (image). Path B also works and stays the fallback.

## Codespace installs

Approved: owner replied OK on 31 Oct 2026 (~23:38 UTC) to the single
INSTALL APPROVAL REQUIRED block (4 downloads, ~1.02 GB). All downloads
verified against catalog SHA-1 / Adoptium SHA-256 before use. Contingent
item 4b (platform ext12) was not needed. SDK license accepted by the
approval; hash recorded at `android-sdk/licenses/android-sdk-license`.
No system-wide installs, no `~/.bashrc`/git/pip changes, no Gradle.

## Artifact manifest (path, size, SHA-256)

Under /workspaces/m4-downloads (pristine downloads):

- arm64-v8a-34_r04.zip — 705,115,396 —
  `1447958a4c6747c44390ac5f5f4c894be6d1dfce93868a0385a95c5f0ae4c339`
- OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz — 193,594,705 —
  `8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711`
- build-tools_r34-linux.zip — 61,224,257 —
  `e858c4b60069d0431051b225d384413b1643e1289b00a4825aed347f25bd510f`
- platform-34-ext7_r03.zip — 63,180,081 —
  `16fdb74c55e59ae3ef52def135aec713508467bd56d7dabcd8c9be31fa8b20f3`

Under /workspaces/m4-tools (built/unpacked; deleted scratch fs copies excluded):

- img-stock/arm64-v8a/system.img — 1,622,147,072 —
  `250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d`
- img-stock/arm64-v8a/vendor.img — 96,468,992 —
  `d773f0f86590257bc45c979085d03a25b69be2f0b2bcf87fd0741d74b4ed1339`
- img-stock/arm64-v8a/ramdisk.img — 1,791,657 —
  `91d2259c141607b5fda903afa4d0a6912389753f40e24015aed74db19fd6bd6c`
- img-stock/arm64-v8a/kernel-ranchu — 12,784,248 —
  `4076bd230e0832e23837e62c1fb03daf5eb4fd5aac731818320c26fa1bf8b2e5`
- img-stock/arm64-v8a/userdata.img — 1,048,576 —
  `30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58`
- system-stargate-a.img (Path A output) — 1,622,147,072 —
  `58961c2024ab3506ed790bf156690cf2d4542889eb05b4afa688133c27cd20e5`
- system-stargate-a.img.gz (transfer) — 639,251,897 —
  `854a467577301c22e1b1e7cf1a5c311b57bbe214aa062db4d34043bb91e11f86`
- stargate-launcher.apk (Path B) — 8,619 —
  `81048cafae18d901b4aa34514c533522d1779f6ff22ce1476bde73de80a6150d`

Derived values: stock fs region (840,314,880 B @3,145,728)
`5ba0ffb06a3e56e0c54afbbe8b49915231be8531f348a12647a19004f6827880`;
patched fs region
`f1a6b5a2d3ba11fc7ea5634a0c787bd424da21c46baf35411e121702dd0fb785`;
signer cert
`606080644678d9b64aa730cb9e7f6e43de5772b64b3df5195a4622a5ee21b30dc`.

Repo files (this branch): `docs/m4-spike.md`, `spike/m4/path_a_repack.py`,
`spike/m4/build_launcher.sh`, `spike/m4/launcher/AndroidManifest.xml`,
`spike/m4/launcher/java/com/stargate/launcher/HomeActivity.java`.

## Commands run outside the sandbox

The codespace sandbox is broken (every sandboxed call fails in `bwrap` with
a user-namespace error), so all shell work ran escalated with approval.
Pre-OK: (1) pre-flight branch/commit/disk check; (2) plan-repo fetch +
spec-commit verify; (3) `git show` of the spec; (4) tree/tool probe (re-sent
after an accidental reject — the rejected call never ran); (5) fs/download
tool inventory. Post-OK: (6) dirs + env.sh + 4 downloads; (7) checksum verify
+ toolchain extract (hit `source`-vs-`.` Dash issue, extraction OK);
(8) toolchain verify; (9) image unzip + magic scan; (10) boot-params + GPT
parse v1 (struct bug, failed); (11) vbmeta + ext4 scan v1 (layout bug,
failed); (12) vbmeta + super scan v2 (int-format bug, failed); (13) correct
descriptor + super-header dump; (14) descriptor + full super scan;
(15) partition-name strings + liblp header fetch attempt (404, abandoned);
(16) fs-type verdict (ext4) + second header attempt (404); (17) LP extent map
v1 (field order wrong); (18) extent map v2 (correct); (19) fs extract +
e2fsck + listings; (20) build.prop cross-check + xattr inspect; (21) marker
write test v1 (`chgrp` missing, batch aborted); (22) v2 (`chown` missing,
batch aborted); (23) debugfs command inventory; (24) write+sif+ea_set test
(works; xattr lacks NUL); (25) `ea_set -f` test (unsupported);
(26) inode locate; (27) xattr patch v1 (wrong header size, failed);
(28) inode hexdump; (29) xattr patch v2 (wrong value base, corrupted test
copy only); (30) xattr patch v3 (correct, verified); (31) real repack run +
diff confinement; (32) closed-loop re-extract verify; (33) launcher build v1
(apksigner flag syntax, failed); (34) launcher build v2 (APK verified);
(35) HOME-intent check + gzip transfer artifact.
No command touched /workspaces/Project-StarGate-Meta-Hackathon except one
accidental `python3 --version` resolving to its venv (read-only, disclosed);
all later shells pin /usr/local/bin/python3 via env.sh.

## Disk and time used

- Codespace free at start: 28 GB. Peak used by the spike: ~8.6 GB total on
  /workspaces (vs ~12 GB estimated). 23 GB free after transfer prep.
  Downloads 976 MiB of the 10 GB budget. Mac free space: about 186 GB
  (owner step 0).
- Clock: downloads 23:38:41–23:38:46Z; toolchain verified ~23:39Z; Q2
  inspection 23:40–23:50Z; Path A repack done 23:58:52Z (~15 min active);
  APK built 00:02:35Z; transfer prepped 00:04:10Z; docs written ~00:20Z.

## Results

- Stock boot on Mac: PASSED — `sys.boot_completed=1`,
  `stargate-spike-stock`, `adb root` uid=0, `ro.build.type=userdebug`
  (owner step 4; details in "Mac results" above).
- Path A: **PASSED** — repacked image booted via the corrected `-sysdir`
  method and the marker read back exactly (`selection=stargate-spike-a`,
  `random=5c056da9fc167da1caf8d9c05d1040d1`); no dm-verity block, no
  `-system` (owner step 5; `-system` must not be used).
- Path B: **PASSED** — install Success, set-home-activity Success,
  home-screen screenshot shows "StarGate" + "Services: Files, Assistant"
  (owner step 6).
- Conclusion: Path A passed, so per the M4 spec section 2, M4 takes
  Route A (image). Path B also works and stays the fallback.

## Unknowns and preliminary recommendation

Resolved 1 Nov 2026: Path A booted and the marker verified, so dm-verity
did not block the repack; M4 takes Route A (image) per spec section 2,
Path B stays the fallback. Original unknowns kept below for the record.

1. The one real unknown is whether the emulator enforces dm-verity on this
   userdebug system image (vbmeta flags are 0x0, hashtree present). Paper
   research could not settle it; the Path A boot is the experiment.
2. If Path A boots: prefer Path A (true image, marker proven in-system).
   The repack pipeline is scripted and re-runnable for M4's two builds.
3. If Path A fails on verity: Path B is ready as the configuration bundle;
   M4 then needs the "not an image" labelling the spec requires, or a
   verity-disabling repack line (vbmeta re-sign — needs keys we don't have).
4. Minor: `pixel_7` AVD device definition assumed present in current
   commandlinetools; owner substitutes from `avdmanager list device` if not.
5. Minor: codespace `python3` on PATH resolves into the other Muse's venv;
   env.sh neutralizes it. Worth telling them (their .venv/bin leaked onto
   the shared PATH).

