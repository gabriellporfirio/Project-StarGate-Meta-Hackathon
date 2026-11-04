#!/bin/sh
# Build the minimal StarGate launcher APK with JDK + build-tools only.
# No Gradle, no Maven, no network. Run from anywhere; source env.sh first.
#   . /workspaces/m4-tools/env.sh
#   sh spike/m4/build_launcher.sh
set -e

: "${JAVA_HOME:?source /workspaces/m4-tools/env.sh first}"
: "${ANDROID_HOME:?source /workspaces/m4-tools/env.sh first}"

BT="$ANDROID_HOME/build-tools/34.0.0"
JAR="$ANDROID_HOME/platforms/android-34/android.jar"
HERE=$(dirname "$0")
SRC="$HERE/launcher"
WORK=/workspaces/m4-tools/launcher-build
OUT=/workspaces/m4-tools/stargate-launcher.apk
KS=/workspaces/m4-tools/debug.keystore

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK/classes" "$WORK/dex"

echo "== 1. debug key (throwaway, spike only) =="
if [ ! -f "$KS" ]; then
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KS" \
    -storepass android -keypass android -keyalg RSA -keysize 2048 \
    -validity 10950 -alias stargate-debug \
    -dname "CN=StarGate Spike, OU=M4, O=StarGate, L=Spike, ST=Spike, C=US"
fi

echo "== 2. javac =="
"$JAVA_HOME/bin/javac" -source 8 -target 8 -bootclasspath "$JAR" \
  -d "$WORK/classes" \
  "$SRC/java/com/stargate/launcher/HomeActivity.java"

echo "== 3. d8 =="
"$BT/d8" --release --min-api 28 --lib "$JAR" \
  --output "$WORK/dex" \
  "$WORK/classes/com/stargate/launcher/HomeActivity.class"

echo "== 4. aapt2 link (manifest only, no resources) =="
"$BT/aapt2" link -o "$WORK/unsigned.apk" -I "$JAR" \
  --manifest "$SRC/AndroidManifest.xml" \
  --min-sdk-version 28 --target-sdk-version 34

echo "== 5. add classes.dex =="
cp "$WORK/dex/classes.dex" "$WORK/"
(cd "$WORK" && zip -q -j unsigned.apk classes.dex)

echo "== 6. zipalign + apksigner =="
"$BT/zipalign" -f 4 "$WORK/unsigned.apk" "$WORK/aligned.apk"
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android \
  --key-pass pass:android --out "$OUT" "$WORK/aligned.apk"

echo "== 7. verify =="
"$BT/apksigner" verify --print-certs "$OUT"
ls -l "$OUT"
sha256sum "$OUT"
