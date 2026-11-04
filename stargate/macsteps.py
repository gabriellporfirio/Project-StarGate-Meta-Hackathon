"""Mac boot steps for one build (M6 Part 1, Boot step).

Generates the owner-run Mac steps from the build's own build-manifest.json:
image name, hashes, AVD settings, and model payload hashes when present.
Ready to copy; the target is fixed to "Android Emulator (this demo)".

The device guard is byte-identical to the M4 record (docs/m4-build.md
Parts 2D and 3C); a test compares them. Shell prose avoids brand words;
``com.stargate.*`` package IDs appear only inside adb commands, where the
device requires them.
"""
from __future__ import annotations

GUARD = (
    'sg_guard() { local inv rows avd rc; '
    'inv="$(adb devices 2>/dev/null)"; rc=$?; '
    '[ "$rc" = "0" ] || { echo "STOP: adb devices failed"; return 1; }; '
    'inv="$(printf \'%s\' "$inv" | tr -d \'\\r\')"; '
    'rows="$(printf \'%s\\n\' "$inv" | sed \'1d\' | sed \'/^\\*/d\' | '
    'sed \'/^List of devices attached/d\' | sed \'/^[[:space:]]*$/d\')"; '
    '[ "$(printf \'%s\\n\' "$rows" | sed \'/^$/d\' | wc -l | tr -d \' \')" = "1" ] '
    '|| { echo "STOP: expected exactly one device row"; return 1; }; '
    '[ "$rows" = "$(printf \'%s\\tdevice\' "$SERIAL")" ] '
    '|| { echo "STOP: the only row must be $SERIAL in state device"; return 1; }; '
    'avd="$(adb -s "$SERIAL" emu avd name 2>/dev/null | head -1 | tr -d \'\\r\')"; '
    '[ "$avd" = "$1" ] || { echo "STOP: AVD is \'$avd\', expected \'$1\'"; '
    'return 1; }; }'
)

SERIAL = "emulator-5554"
MAC_DIR = "~/stargate-m6"
IMAGE_KIND = '"system-images;android-34;default;arm64-v8a"'
DEVICE = "pixel_7"


def avd_name_for_selection(selection: list) -> str:
    """Deterministic AVD name: files first, then assistant."""
    ordered = [name for name in ("files", "assistant")
               if name in selection]
    return "stargate-m6-%s" % "-".join(ordered)


def artifacts_by_path(manifest: dict) -> dict:
    """Manifest artifacts keyed by path."""
    found = {}
    for item in manifest.get("artifacts", []):
        if isinstance(item, dict) and item.get("path"):
            found[item["path"]] = item
    return found


def app_by_package(manifest: dict, package: str):
    """Manifest app entry for a package, or None."""
    for item in manifest.get("apps", []):
        if isinstance(item, dict) and item.get("package") == package:
            return item
    return None


def generate(manifest: dict, summary: dict) -> str:
    """Render the Mac steps text for this build. Raises ValueError."""
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ValueError("manifest and summary must be objects")
    build_id = manifest.get("build_id")
    selection = manifest.get("selection")
    if not build_id or not isinstance(selection, list) or not selection:
        raise ValueError("manifest has no build_id or selection")
    artifacts = artifacts_by_path(manifest)
    image_name = (summary.get("image") or {}).get("path")
    image = artifacts.get(image_name) if image_name else None
    gz = summary.get("gz") or {}
    gz_name = gz.get("path")
    if image is None or not gz_name:
        raise ValueError("manifest has no image or transfer artifact")
    has_files = "files" in selection
    has_assistant = "assistant" in selection
    avd = avd_name_for_selection(selection)
    sysdir = "sysdir-%s" % "-".join(
        name for name in ("files", "assistant") if name in selection)
    lines = []
    add = lines.append
    add("Target: Android Emulator (this demo)")
    add("Build: %s" % build_id)
    add("Services: %s" % ", ".join(sorted(selection)))
    add("")
    add("Run every block below in ONE Terminal session on the Mac, in")
    add("order (the guard, SERIAL, FH and CODE live in that session")
    add("only). Each line pastes cleanly into zsh: one command per")
    add("line, no placeholders in brackets. Three lines need your")
    add("own value, each marked in capitals: FH (step 3, your")
    add("forwarded address), SERIAL (step 6, only if the emulator is")
    add("not %s), CODE (Files tests, read from the emulator" % SERIAL)
    add("screen). No installs: everything reuses ~/stargate-spike")
    add("from the spike.")
    add("")
    add("Step 0 - environment (first, in every new Terminal)")
    add("```sh")
    add("export SG=~/stargate-spike")
    add("export ANDROID_HOME=$SG/sdk")
    add("export ANDROID_USER_HOME=~/.android")
    add("export JAVA_HOME=$SG/jdk-17/Contents/Home")
    add("export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:"
        "$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:"
        "$JAVA_HOME/bin:$PATH")
    add(GUARD)
    add("emulator -version")
    add("adb --version")
    add("```")
    add("")
    add("Expect emulator 37.2.13.0 (the spike's install). From here")
    add("on, every `adb -s` line starts with")
    add("`sg_guard %s &&`: the guard reads" % avd)
    add("one fresh `adb devices` inventory and allows the command")
    add("only with exactly one row, exactly `$SERIAL` in state")
    add("`device`, and the AVD name `%s`." % avd)
    add("Anything else prints STOP: stop and report, do not")
    add("continue.")
    add("")
    add("Step 1 - free space")
    add("```sh")
    add("df -h /")
    add("```")
    add("")
    if has_assistant:
        add("Need at least 14 GB free (transfer ~1.9 + image 1.6 + AVD")
        add("plus the 5 GB margin); record the number. Below")
        add("that: stop and report.")
    else:
        add("Need at least 12 GB free (transfer 0.7 + image 1.6 + AVD")
        add("plus the 5 GB margin); record the number. Below")
        add("that: stop and report.")
    add("")
    add("Step 2 - cleanup log first (before creating anything)")
    add("```sh")
    add("echo 'M6 %s: AVD %s -- undo: rm -rf "
        "~/.android/avd/%s ~/.android/avd/%s.ini' "
        ">> ~/stargate-cleanup-log.md" % (build_id, avd, avd, avd))
    add("cat ~/stargate-cleanup-log.md")
    add("```")
    add("")
    payload_files = list(summary.get("payload_files") or [])
    if has_assistant:
        add("Step 3 - transfer (one image, %d model file%s)"
            % (len(payload_files),
               "" if len(payload_files) == 1 else "s"))
    else:
        add("Step 3 - transfer (one image)")
    add("")
    add("In the codespace terminal (leave it running until every")
    add("download below finishes, then Ctrl-C):")
    add("```sh")
    add("cd /workspaces/m6-tools/out")
    add("/usr/local/bin/python3 -m http.server 8765 --bind 127.0.0.1")
    add("```")
    add("")
    add("Forward port 8765 as PRIVATE in the Codespaces Ports")
    add("panel and copy its forwarded address (no trailing slash).")
    add("On the Mac:")
    add("```sh")
    add("mkdir -p %s" % MAC_DIR)
    add("FH=PASTE-FORWARDED-ADDRESS-HERE")
    add('curl -fSL -o %s/%s "$FH/%s/%s"'
        % (MAC_DIR, gz_name, build_id, gz_name))
    add("shasum -a 256 %s/%s" % (MAC_DIR, gz_name))
    add("```")
    add("")
    add("Must equal")
    add("`%s`." % gz.get("sha256"))
    add("Mismatch = stop.")
    add("")
    add("```sh")
    add("gunzip -k %s/%s" % (MAC_DIR, gz_name))
    add("shasum -a 256 %s/%s" % (MAC_DIR, image_name))
    add("```")
    add("")
    add("Must equal")
    add("`%s`." % image.get("sha256"))
    add("Mismatch = stop. If Safari unpacked the download on its")
    add("own, verify this `.img` hash only.")
    add("")
    if has_assistant and payload_files:
        add("```sh")
        for item in payload_files:
            add('curl -fSL -o %s/%s "$FH/%s/%s/%s"'
                % (MAC_DIR, item["name"], build_id,
                   summary.get("model_payload_path"), item["name"]))
        add("shasum -a 256 %s" % " ".join(
            "%s/%s" % (MAC_DIR, item["name"])
            for item in payload_files))
        add("```")
        add("")
        for item in payload_files:
            add("`%s` must equal `%s`."
                % (item["name"], item["sha256"]))
        add("Mismatch = stop.")
        add("")
    add("Step 4 - create the AVD")
    add("```sh")
    add('echo no | avdmanager create avd -n %s -k %s -d %s --force'
        % (avd, IMAGE_KIND, DEVICE))
    add("avdmanager list avd")
    add("```")
    add("")
    add("`Could not load devices.xml` warnings are harmless (the")
    add("spike saw them too); `list avd` must show")
    add("`%s`." % avd)
    if has_assistant:
        add("Then size it (model %d bytes + 1 GB headroom):"
            % sum(item.get("bytes", 0) for item in payload_files))
        add("```sh")
        add('CFG=~/.android/avd/%s.avd/config.ini' % avd)
        add('test -f "$CFG"')
        add("sed -i '' '/^hw.ramSize=/d; /^disk.dataPartition.size=/d' "
            '"$CFG"')
        add("printf '%s\\n' 'hw.ramSize=6144' "
            "'disk.dataPartition.size=4G' | tee -a \"$CFG\"")
        add("grep -E '^(hw.ramSize|disk.dataPartition.size)=' \"$CFG\"")
        add("```")
        add("")
        add("The last line must print exactly `hw.ramSize=6144` and")
        add("`disk.dataPartition.size=4G`.")
        add("")
    add("Step 5 - sysdir of symlinks")
    add("```sh")
    add("mkdir -p %s/%s" % (MAC_DIR, sysdir))
    add("ln -s ~/stargate-spike/sdk/system-images/android-34/"
        "default/arm64-v8a/* %s/%s/" % (MAC_DIR, sysdir))
    add("rm %s/%s/system.img" % (MAC_DIR, sysdir))
    add("ln -s %s/%s %s/%s/system.img"
        % (MAC_DIR, image_name, MAC_DIR, sysdir))
    add("ls -l %s/%s/system.img" % (MAC_DIR, sysdir))
    add("```")
    add("")
    add("The last line must show `system.img` pointing at your")
    add("`%s`. Never `-system`" % image_name)
    add("(it silently boots stock) and never `-writable-system`.")
    add("")
    add("Step 6 - boot and boot record")
    add("```sh")
    add("adb devices")
    add("SERIAL=%s" % SERIAL)
    add("emulator -avd %s -sysdir %s/%s -no-snapshot -wipe-data "
        "-no-metrics > %s/boot.log 2>&1 &"
        % (avd, MAC_DIR, sysdir, MAC_DIR))
    add("for i in $(seq 1 60); do line=$(adb devices 2>/dev/null | "
        "tr -d '\\r' | sed '1d' | sed '/^\\*/d' | "
        "sed '/^List of devices attached/d' | "
        "sed '/^[[:space:]]*$/d'); [ \"$line\" = "
        "\"$(printf '%s\\tdevice' \"$SERIAL\")\" ] && break; "
        "sleep 10; done")
    add("for i in $(seq 1 60); do sg_guard %s || break; "
        "v=$(adb -s \"$SERIAL\" shell getprop sys.boot_completed "
        "2>/dev/null | tr -d '\\r'); [ \"$v\" = 1 ] && break; "
        "sleep 10; done" % avd)
    add("sg_guard %s && adb -s \"$SERIAL\" shell getprop "
        "sys.boot_completed" % avd)
    add("sg_guard %s && adb -s \"$SERIAL\" emu avd name" % avd)
    add("sg_guard %s && adb -s \"$SERIAL\" shell cat "
        "/system/stargate-build-id.txt" % avd)
    add("sg_guard %s && adb -s \"$SERIAL\" exec-out screencap -p > "
        "%s/home.png" % (avd, MAC_DIR))
    add("open %s/home.png" % MAC_DIR)
    add("```")
    add("")
    add("The first loop only runs `adb devices` (host inventory,")
    add("touches no device) until exactly the one row appears; every")
    add("`-s` line after is guarded. Expect: `sys.boot_completed`")
    add("is `1`; AVD name is `%s`; the" % avd)
    add("build ID reads back exactly")
    add("`build_id=%s`; the screenshot" % build_id)
    tiles = " and ".join(
        name for name in ("Files", "Assistant")
        if name.lower() in selection)
    add("shows the home screen with %s." % tiles)
    add("Record every line's output.")
    add("")
    if has_assistant:
        assistant_app = app_by_package(
            manifest, "com.stargate.assistant") or {}
        add("The stock image can restart once on first boot, so wait")
        add("for boot again before touching the app:")
        add("```sh")
        add("for i in $(seq 1 60); do sg_guard %s || break; "
            "v=$(adb -s \"$SERIAL\" shell getprop sys.boot_completed "
            "2>/dev/null | tr -d '\\r'); [ \"$v\" = 1 ] && break; "
            "sleep 10; done" % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" shell getprop "
            "sys.boot_completed" % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" shell pm path "
            "com.stargate.assistant" % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" shell sha256sum "
            "/system/app/StargateAssistant/StargateAssistant.apk" % avd)
        add("```")
        add("")
        add("Expect `1` again; `pm path` prints")
        add("`package:/system/app/StargateAssistant/"
            "StargateAssistant.apk`;")
        add("the on-device APK hash equals")
        add("`%s`." % assistant_app.get("sha256"))
        add("Mismatch = stop.")
        add("")
        add("Step 7 - first launch, then the model files")
        add("")
        add("The app creates its own folder on first launch; never")
        add("`mkdir` it from the shell:")
        add("```sh")
        for line in (
                "sg_guard %s && adb -s \"$SERIAL\" shell am start -W -n "
                "com.stargate.assistant/.MainActivity" % avd,
                "sg_guard %s && adb -s \"$SERIAL\" shell am force-stop "
                "com.stargate.assistant" % avd,
                "sg_guard %s && adb -s \"$SERIAL\" shell ls -ld "
                "/sdcard/Android/data/com.stargate.assistant/files/model/"
                % avd):
            add(line)
        add("```")
        add("")
        add("The `ls` line must show the model folder. If it is")
        add("missing, stop: the app did not create it. Then the pushes")
        add("(recorded exception - the only pushes in this")
        add("record, each already checked with `shasum` in step 3):")
        add("```sh")
        for item in payload_files:
            add("# recorded exception")
            add("sg_guard %s && adb -s \"$SERIAL\" push %s/%s "
                "/sdcard/Android/data/com.stargate.assistant/files/model/"
                % (avd, MAC_DIR, item["name"]))
        add("sg_guard %s && adb -s \"$SERIAL\" shell sha256sum %s"
            % (avd, " ".join(
                "/sdcard/Android/data/com.stargate.assistant/files/model/"
                "%s" % item["name"] for item in payload_files)))
        add("```")
        add("")
        add("The on-device hashes must equal the step 3 hashes.")
        add("Mismatch = stop.")
        add("")
        add("Step 8 - networking off")
        add("```sh")
        add("sg_guard %s && adb -s \"$SERIAL\" shell svc wifi disable"
            % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" shell svc data disable"
            % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" shell settings get "
            "global wifi_on" % avd)
        add("```")
        add("")
        add("The last line must print `0`; the status bar must show")
        add("no connectivity.")
        add("")
        add("Step 9 - ask one ordinary question")
        add("```sh")
        add("sg_guard %s && adb -s \"$SERIAL\" shell am start -n "
            "com.stargate.assistant/.MainActivity" % avd)
        add("```")
        add("")
        add("On the emulator screen: read the two on-screen model")
        add("hashes (must match step 3), type exactly `Name three")
        add("planets`, submit, and note the answer with its time.")
        add("Then back in the Terminal:")
        add("```sh")
        add("sg_guard %s && adb -s \"$SERIAL\" logcat -d | "
            "grep 'given pos_'" % avd)
        add("sg_guard %s && adb -s \"$SERIAL\" exec-out screencap -p > "
            "%s/answer.png" % (avd, MAC_DIR))
        add("open %s/answer.png" % MAC_DIR)
        add("```")
        add("")
        add("The logcat line must show `num_prompt_tokens`; the")
        add("screenshot keeps the answer with the hashes on screen.")
        add("Paste back the logcat line, the answer, and its time.")
        add("")
    if has_files:
        add("Step 10 - Files tests")
        add("```sh")
        add("sg_guard %s && adb -s \"$SERIAL\" shell am start -n "
            "com.stargate.files/.FilesActivity" % avd)
        add("sleep 3")
        add("sg_guard %s && adb -s \"$SERIAL\" forward tcp:8080 "
            "tcp:8080" % avd)
        add("echo \"files-test\" > %s/upload.txt" % MAC_DIR)
        add("shasum -a 256 %s/upload.txt" % MAC_DIR)
        add("CODE=READ-FROM-EMULATOR-SCREEN")
        add("curl -fSL -X PUT --data-binary @%s/upload.txt "
            "\"http://127.0.0.1:8080/files/upload.txt?code=$CODE\" "
            "&& echo UPLOAD-OK" % MAC_DIR)
        add("curl -fSL -o %s/download.txt "
            "\"http://127.0.0.1:8080/files/upload.txt?code=$CODE\" "
            "&& echo DOWNLOAD-OK" % MAC_DIR)
        add("shasum -a 256 %s/download.txt" % MAC_DIR)
        add("curl -fSL "
            "\"http://127.0.0.1:8080/files?code=$CODE\"")
        add("curl -s -o /dev/null -w \"%%{http_code}\\n\" "
            "\"http://127.0.0.1:8080/files/upload.txt\"")
        add("curl -s -o /dev/null -w \"%%{http_code}\\n\" "
            "\"http://127.0.0.1:8080/files/upload.txt?code=WRONGCODE12\"")
        add("sg_guard %s && adb -s \"$SERIAL\" shell ss -ltn" % avd)
        add("lsof -nP -iTCP:8080 -sTCP:LISTEN")
        add("lsof -nP -iTCP:5037 -sTCP:LISTEN")
        add("```")
        add("")
        add("UPLOAD-OK and DOWNLOAD-OK print; both")
        add("shasums match each other and the upload's; the list shows")
        add("`upload.txt`; both refusal lines print `403`; `ss` shows")
        add("`127.0.0.1:8080`; the first `lsof` shows `127.0.0.1:8080`;")
        add("the second shows `127.0.0.1:5037`.")
        add("")
    add("Last step - shut down")
    add("```sh")
    add("sg_guard %s && adb -s \"$SERIAL\" emu kill" % avd)
    add("sleep 10")
    add("adb devices")
    add("```")
    add("")
    add("The last line must show no device rows.")
    return "\n".join(lines) + "\n"
