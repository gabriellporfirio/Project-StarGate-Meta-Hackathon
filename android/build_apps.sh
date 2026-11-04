#!/bin/sh
# Build the StarGate launcher and Files APKs with JDK + build-tools only.
# No Gradle, no Maven, no network. Derived from the spike's proven
# build_launcher.sh; tools are read-only in /workspaces/m4-tools, while
# the key, intermediates and APKs live in /workspaces/m4a-tools.
#
#   sh android/build_apps.sh --plan <plan.json> --build-id <id> \
#       [--out <dir>] [--tools <dir>] [--labels '["label"]']
#
# The launcher's services list is generated from the plan's handoff
# only (stargate.build.checks.launcher_config); the plan must pass
# the section 1 gate or the script refuses. --labels adds extra baked
# labels (a JSON list, e.g. the section 4d after-boot label); the
# build verifies the baked config and refuses a mismatch. Runs one
# app at a time, sequentially, with d8 capped at 2 GB heap.
set -e

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PLAN=""
BUILD_ID=""
LABELS="[]"
OUT=/workspaces/m4a-tools/out
TOOLS=/workspaces/m4a-tools

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN="$2"; shift 2;;
    --build-id) BUILD_ID="$2"; shift 2;;
    --labels) LABELS="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --tools) TOOLS="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
if [ -z "$PLAN" ] || [ -z "$BUILD_ID" ]; then
  echo "usage: build_apps.sh --plan <plan.json> --build-id <id>" >&2
  exit 2
fi

echo "== 0. no other Android/Gradle build may run =="
OTHER=$(ps -eo args | grep -iE "[g]radle|[ /]d8( |$)|[a]apt2" || true)
if [ -n "$OTHER" ]; then
  echo "another Android/Gradle build seems to run; refusing:" >&2
  echo "$OTHER" >&2
  exit 1
fi
echo "ps check clean: no gradle/d8/aapt2 process"

# Read-only spike toolchain (never written to).
JAVA_HOME=/workspaces/m4-tools/jdk-17
ANDROID_HOME=/workspaces/m4-tools/android-sdk
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
BT="$ANDROID_HOME/build-tools/34.0.0"
JAR="$ANDROID_HOME/platforms/android-34/android.jar"
PYTHON=/usr/local/bin/python3
for tool in "$JAVA_HOME/bin/javac" "$JAVA_HOME/bin/keytool" \
    "$BT/d8" "$BT/aapt2" "$BT/zipalign" "$BT/apksigner" \
    "$JAR" "$PYTHON"; do
  if [ ! -e "$tool" ]; then
    echo "missing tool: $tool" >&2
    exit 1
  fi
done

WORK="$TOOLS/build"
KS="$TOOLS/debug.keystore"
rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT"

echo "== 1. launcher config from the plan handoff =="
CONFIG_DIR="$WORK/launcher-assets"
mkdir -p "$CONFIG_DIR"
PYTHONPATH="$REPO" "$PYTHON" - "$PLAN" "$BUILD_ID" "$LABELS" \
    > "$CONFIG_DIR/stargate-build.json" <<'EOF'
import json
import sys
from stargate.build import checks
from stargate.build import gate

plan, error = gate.load_plan_file(sys.argv[1])
if error is not None:
    sys.stderr.write("error: %s\n" % error)
    sys.exit(6)
ok, code, message = gate.gate_plan(plan)
if not ok:
    sys.stderr.write(message)
    sys.exit(code)
try:
    extra = json.loads(sys.argv[3])
except ValueError:
    sys.stderr.write("error: --labels is not a JSON list\n")
    sys.exit(2)
if not isinstance(extra, list) or any(
        not isinstance(item, str) for item in extra):
    sys.stderr.write("error: --labels must be a JSON list of strings\n")
    sys.exit(2)
config = checks.launcher_config(
    plan, sys.argv[2], gate.labels_for_plan(plan) + extra)
json.dump(config, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write("\n")
EOF
cat "$CONFIG_DIR/stargate-build.json"

echo "== 2. debug key (throwaway, M4a Part 1 only) =="
if [ ! -f "$KS" ]; then
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KS" \
    -storepass android -keypass android -keyalg RSA -keysize 2048 \
    -validity 10950 -alias stargate-debug \
    -dname "CN=StarGate M4a, OU=M4, O=StarGate, L=Part1, ST=Part1, C=US"
fi

build_app() {
  NAME="$1"      # launcher | files
  PACKAGE="$2"   # com.stargate.<name>
  MANIFEST="$3"  # AndroidManifest.xml path
  ASSETS="$4"    # assets dir or empty
  shift 4
  echo "== 3. javac ($NAME) =="
  APP_WORK="$WORK/$NAME"
  mkdir -p "$APP_WORK/classes" "$APP_WORK/dex"
  "$JAVA_HOME/bin/javac" -source 8 -target 8 -bootclasspath "$JAR" \
    -d "$APP_WORK/classes" "$@"
  echo "== 4. d8 ($NAME, 2 GB heap cap) =="
  # shellcheck disable=SC2046
  "$BT/d8" -JXmx2g --release --min-api 28 --lib "$JAR" \
    --output "$APP_WORK/dex" \
    $(find "$APP_WORK/classes" -name '*.class')
  echo "== 5. aapt2 link ($NAME) =="
  if [ -n "$ASSETS" ]; then
    "$BT/aapt2" link -o "$APP_WORK/unsigned.apk" -I "$JAR" \
      --manifest "$MANIFEST" -A "$ASSETS" \
      --min-sdk-version 28 --target-sdk-version 34
  else
    "$BT/aapt2" link -o "$APP_WORK/unsigned.apk" -I "$JAR" \
      --manifest "$MANIFEST" \
      --min-sdk-version 28 --target-sdk-version 34
  fi
  echo "== 6. add classes.dex ($NAME) =="
  cp "$APP_WORK/dex/classes.dex" "$APP_WORK/"
  (cd "$APP_WORK" && zip -q -j unsigned.apk classes.dex)
  echo "== 7. zipalign + apksigner ($NAME) =="
  "$BT/zipalign" -f 4 "$APP_WORK/unsigned.apk" "$APP_WORK/aligned.apk"
  "$BT/apksigner" sign --ks "$KS" --ks-pass pass:android \
    --key-pass pass:android --out "$OUT/stargate-$NAME.apk" \
    "$APP_WORK/aligned.apk"
  echo "== 8. verify ($NAME) =="
  "$BT/apksigner" verify "$OUT/stargate-$NAME.apk"
}

build_app launcher com.stargate.launcher \
  "$REPO/android/launcher/AndroidManifest.xml" \
  "$CONFIG_DIR" \
  "$REPO/android/launcher/java/com/stargate/launcher/HomeActivity.java"

build_app files com.stargate.files \
  "$REPO/android/files/AndroidManifest.xml" \
  "" \
  "$REPO/android/common/java/com/stargate/files/FilesServer.java" \
  "$REPO/android/files/java/com/stargate/files/FilesActivity.java"

echo "== 9. summary =="
for APK in "$OUT"/stargate-*.apk; do
  echo "--- $APK"
  "$BT/aapt2" dump packagename "$APK" | sed 's/^/package: /'
  "$BT/aapt2" dump badging "$APK" \
    | grep -E "^(package:|uses-permission:)" || true
  BYTES=$(wc -c < "$APK")
  echo "bytes: $BYTES"
  sha256sum "$APK"
done
