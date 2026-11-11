#!/bin/sh
# Install what the phone app needs to build, on Debian or Ubuntu.
# No Android Studio and no Gradle: just the command-line tools.
#
#   sh scripts/setup-android-tools.sh
#   python3 -m stargate.build.hubapk --doctor
#
# apt gives aapt2, zipalign and apksigner. Two jars are downloaded
# into ~/.stargate/tools: the dex compiler (R8/D8) and the Android 14
# framework classes to compile against.
set -e
TOOLS="$HOME/.stargate/tools"
mkdir -p "$TOOLS"
SUDO=""
[ "$(id -u)" = "0" ] || SUDO="sudo"
# A broken extra package source (Codespaces ships one for yarn with an
# expired key) must not stop the install: the main sources still refresh.
$SUDO apt-get update -qq || echo "Some package sources failed to refresh; carrying on."
$SUDO apt-get install -y -qq default-jdk-headless aapt apksigner zipalign unzip curl
get() {
  [ -s "$2" ] || curl -fsSL -o "$2" "$1"
}
get "https://storage.googleapis.com/r8-releases/raw/8.5.35/r8.jar" "$TOOLS/r8.jar"
get "https://repo1.maven.org/maven2/org/robolectric/android-all/14-robolectric-10818077/android-all-14-robolectric-10818077.jar" "$TOOLS/android-all-14.jar"
echo "Tools are in $TOOLS"
