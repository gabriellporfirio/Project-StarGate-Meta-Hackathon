# M4c section 8 proof on the Mac (owner-run, written not run)

One Files + Assistant cloud build (`stargate build
--assistant-cloud`, builder's), proven on a fresh dedicated 6 GB
AVD. Reuses M4's transfer hash checks and ADB safety rules. All
work under `~/stargate-m4/`. Every block pastes cleanly into zsh
(one command per line; no angle brackets). No real phones, USB,
or `fastboot`. Steps marked LIVE need the owner's OK, and each
live answer counts against section 6 of the spec (at most 5
sends per provider, retries included).

Device guard: after `SERIAL` is set, EVERY adb command below is
prefixed `guard &&`, except the read-only `adb devices`
inventory listings. Paste the guard once per shell:

```sh
# check-device guard: one inventory, strict single-device match
guard() {
  if [ -z "$SERIAL" ]; then
    echo "guard: SERIAL is not set" >&2
    return 1
  fi
  _inv=$(adb devices | tr -d '\r')
  _rows=$(printf '%s\n' "$_inv" | tail -n +2 | grep -c . || true)
  if [ "$_rows" != "1" ]; then
    echo "guard: expected exactly one device row, saw $_rows" >&2
    return 1
  fi
  _row=$(printf '%s\n' "$_inv" | tail -n +2 | grep .)
  _want=$(printf '%s\tdevice' "$SERIAL")
  if [ "$_row" != "$_want" ]; then
    echo "guard: device row is not [$SERIAL device]" >&2
    return 1
  fi
  _avd=$(adb -s "$SERIAL" emu avd name | tr -d '\r')
  if [ "$_avd" != "stargate-m4c-cloud" ]; then
    echo "guard: AVD is not stargate-m4c-cloud" >&2
    return 1
  fi
}
```

It passes only for exactly one device row equal to `$SERIAL`,
a tab, then `device`, with AVD `stargate-m4c-cloud`. If it
fails, stop and fix the device list first.

0. **Cleanup list first.** Add a row to
   `~/stargate-cleanup-log.md`: AVD `stargate-m4c-cloud`, undo
   `avdmanager delete avd -n stargate-m4c-cloud` and delete
   `~/.android/avd/stargate-m4c-cloud.avd/`; files under
   `~/stargate-m4/`, undo `rm`.
1. **AVD.** Stock `system-images;android-34;default;arm64-v8a`
   (already installed by the spike), device `pixel_7`:

   ```sh
   echo no | avdmanager create avd -n stargate-m4c-cloud -k "system-images;android-34;default;arm64-v8a" -d pixel_7 --force
   ```

2. **AVD sizing.** Same model as M4b (1138135470 bytes) + 1 GB
   = 2211877294 bytes minimum; 6 GB RAM and a 4 GB data
   partition:

   ```sh
   CFG=~/.android/avd/stargate-m4c-cloud.avd/config.ini
   test -f "$CFG"
   sed -i '' '/^hw.ramSize=/d; /^disk.dataPartition.size=/d' "$CFG"
   printf '%s\n' 'hw.ramSize=6144' 'disk.dataPartition.size=4G' | tee -a "$CFG"
   grep -E '^(hw.ramSize|disk.dataPartition.size)=' "$CFG"
   ```

3. **Launch** (record the exact command), then wait for boot:

   ```sh
   emulator -avd stargate-m4c-cloud -no-snapshot -wipe-data
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" wait-for-device
   guard && until [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed)" = 1 ]; do sleep 5; done
   ```

4. **Transfer + check.** Download these four files from the
   codespace to `~/stargate-m4/` through the browser (cloud APK
   from `/workspaces/m4b-tools/out-byok5/assistant-cloud.apk`,
   notices from `third_party/NOTICES.md`), then check each:

   ```sh
   shasum -a 256 ~/stargate-m4/assistant-cloud.apk
   shasum -a 256 ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte
   shasum -a 256 ~/stargate-m4/tokenizer.model
   shasum -a 256 ~/stargate-m4/NOTICES.md
   ```

   Expected (a mismatch means stop): `assistant-cloud.apk`
   `0f80c9c12b574a3996baca4ad17e20b1a910effeb6f7f7bdeb1b70c034977281`
   (from `assistant-manifest.json`), `.pte`
   `8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b`,
   `tokenizer.model`
   `82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55`,
   `NOTICES.md`
   `7e95dcb163ee469d2ad839de3336ea736ea0b45dc0e09daebdb94b60d21f0aa1`.
   Confirm the builder's `build-manifest.json` records
   `assistant_variant` `cloud` with this APK's package,
   version and SHA-256.
5. **Install**, then re-wait for boot:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" install ~/stargate-m4/assistant-cloud.apk
   guard && adb -s "$SERIAL" wait-for-device
   guard && until [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed)" = 1 ]; do sleep 5; done
   ```

6. **First launch, then push the model files.** The app creates
   its own Route B folder on first launch; never `mkdir` it
   from the shell:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell am start -W -n com.stargate.assistant/.MainActivity
   guard && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
   guard && adb -s "$SERIAL" shell ls -ld /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" push ~/stargate-m4/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" push ~/stargate-m4/tokenizer.model /sdcard/Android/data/com.stargate.assistant/files/model/
   guard && adb -s "$SERIAL" shell sha256sum /sdcard/Android/data/com.stargate.assistant/files/model/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte /sdcard/Android/data/com.stargate.assistant/files/model/tokenizer.model
   ```

   The two on-device hashes must match step 4.
7. **Key handling with dummy keys** (manual UI, no live call).
   Recording rule for every key entry here and in steps
   11-12: stop recording, type or paste the key into the
   masked field, save, clear the field and the Mac clipboard,
   then resume recording. Check the masking once with a dummy
   key on camera, never with a real key: in the app's Cloud
   keys section, paste `DUMMY-KEY-0000` as the Meta key and
   save; the entry field must show dots, never the key text,
   and the row must show only the last four (`0000`). Then
   restart the app and check the key is still there; then
   Delete and check it is gone:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell am force-stop com.stargate.assistant
   guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
   ```

   Repeat the save/restart/delete round for the Baseten row.
   Screenshots must never show a key.
8. **No backups, checked two ways.**
   In the codespace (record the outputs):

   ```sh
   BT=/workspaces/m4-tools/android-sdk/build-tools/34.0.0
   O=/workspaces/m4b-tools/out-byok5
   $BT/aapt2 dump xmltree $O/assistant-cloud.apk --file AndroidManifest.xml
   $BT/aapt2 dump xmltree $O/assistant-cloud.apk --file res/xml/data_extraction_rules.xml
   ```

   Expected: `allowBackup=false`, a `dataExtractionRules`
   reference, and both a `cloud-backup` and a
   `device-transfer` section excluding every domain (root,
   file, database, sharedpref, external). On the emulator:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell dumpsys package com.stargate.assistant
   ```

   Save the `flags=[ ... ]` line. **Expected:** `ALLOW_BACKUP`
   is not in that line. If it is there, the check fails.
9. **Local mode** answers with networking off:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell svc wifi disable
   guard && adb -s "$SERIAL" shell svc data disable
   guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
   ```

   Pick Local in the Model drop-down, ask one question, note
   the answer and the "Answered by: Local" line. Best-effort
   zero-request check: read the app's netstats counters before
   and after the question (find its user ID first); no growth
   is expected, but background noise can move the counters:

   ```sh
   adb devices
   SERIAL=emulator-5554
   guard && adb -s "$SERIAL" shell dumpsys package com.stargate.assistant
   guard && adb -s "$SERIAL" shell dumpsys netstats detail
   ```

   Record both readings with the question.
10. **Preflight before any live question (owner, codespace).**
    Stop all other development calls, count the rows for each
    provider, then write two `planned` phone rows per cloud
    mode to be tested (the question plus its one possible
    retry). If that would pass 5, that mode is not tested and
    the record says so. Run from `/workspaces/m4b`, one
    command per line:

    ```sh
    cd /workspaces/m4b
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; log=CallsLog('docs/m4c-calls.md'); print('meta', log.attempts_used('meta')); print('baseten', log.attempts_used('baseten'))"
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; CallsLog('docs/m4c-calls.md').preflight_phone('meta', 'muse-spark-1.3')"
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; CallsLog('docs/m4c-calls.md').preflight_phone('baseten', 'meta-models/muse-spark-open-1026')"
    ```

    With four sends already used, the phone proof for that
    provider cannot start: one question and its retry need
    two. Record the counts and the new row IDs.
11. **LIVE: Meta mode answers one question.** Needs the
    owner's OK. Re-enable networking, then with recording
    stopped (step 7 rule) paste the real Meta key in settings
    (last four shown, never screenshotted; clear the field
    and the Mac clipboard, then resume recording), pick Meta,
    ask one question, note the answer with the
    "Answered by: Meta" line and model:

    ```sh
    adb devices
    SERIAL=emulator-5554
    guard && adb -s "$SERIAL" shell svc wifi enable
    guard && adb -s "$SERIAL" shell svc data enable
    guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
    ```

12. **LIVE: Meta open-weight mode answers one question.**
    Needs the owner's OK. With recording stopped (step 7
    rule) paste the real Baseten key, then clear the field
    and the Mac clipboard and resume recording; pick Meta
    open-weight, ask one question through
    `meta-models/muse-spark-open-1026`, note the answer with
    the model ID shown:

    ```sh
    adb devices
    SERIAL=emulator-5554
    guard && adb -s "$SERIAL" shell am start -n com.stargate.assistant/.MainActivity
    ```

13. **Reconcile after the run (owner, codespace).** Turn each
    `planned` phone row into `sent` or `uncertain` from the
    app's screen and its metadata log, and delete a `planned`
    row only if the app's log shows that send never happened.
    No more live work starts until this is reconciled. List
    the rows, then mark each (replace `c0001` with the listed
    ID, one command per line):

    ```sh
    cd /workspaces/m4b
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; [print(row['id'], row['provider'], row['model'], row['where'], row['state']) for row in CallsLog('docs/m4c-calls.md').rows]"
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; CallsLog('docs/m4c-calls.md').mark_sent('c0001', 200, 10, 5)"
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; CallsLog('docs/m4c-calls.md').mark_uncertain('c0002')"
    /usr/local/bin/python3 -c "from stargate.m4c import CallsLog; CallsLog('docs/m4c-calls.md').delete_planned('c0003')"
    ```

    Use `mark_sent` with the HTTP status and token counts from
    usage, `mark_uncertain` for a timeout, a dropped
    connection, or anything where the request may have reached
    the provider, and `delete_planned` only for a send the
    app's log proves never happened. Commit the log.
14. **Proof.** One screenshot per answered mode, with no key
    visible; then the emulator is killed:

    ```sh
    adb devices
    SERIAL=emulator-5554
    guard && adb -s "$SERIAL" shell screencap -p /sdcard/m4c-local.png
    guard && adb -s "$SERIAL" pull /sdcard/m4c-local.png ~/stargate-m4/m4c-local.png
    guard && adb -s "$SERIAL" shell screencap -p /sdcard/m4c-meta.png
    guard && adb -s "$SERIAL" pull /sdcard/m4c-meta.png ~/stargate-m4/m4c-meta.png
    guard && adb -s "$SERIAL" shell screencap -p /sdcard/m4c-open.png
    guard && adb -s "$SERIAL" pull /sdcard/m4c-open.png ~/stargate-m4/m4c-open.png
    guard && adb -s "$SERIAL" emu kill
    adb devices
    ```

    Record every command run and its result. Every live answer
    is in `docs/m4c-calls.md` within the 5-send limit. This
    AVD is proof-only and stays out of the M4 demo builds.
