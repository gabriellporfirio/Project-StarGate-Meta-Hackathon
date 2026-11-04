#!/usr/local/bin/python3
"""Build com.stargate.assistant in two variants, directly.

Pipeline per variant: javac -> d8 -> aapt2 link -> add dex + .so +
licenses -> zipalign -> apksigner. No Gradle (the AARs are used
directly: classes.jar on the classpath and into dex, jni/<abi>/*.so
into lib/). Single process; d8 heap capped at 2 GB. Reads
/workspaces/m4-tools (never writes). By default works under
/workspaces/m4b-tools/assistant-build/<variant>/ and publishes to
/workspaces/m4b-tools/out/assistant.apk (local, no permissions) and
out/assistant-cloud.apk (cloud, exactly INTERNET). --out-dir and
--build-dir override both, so a BYOK rebuild writes everything
(intermediates, cleanup and the final APK) under one folder and
nothing under out/. Any other permission set is refused. v2.5: no
money tracking baked in; only CLOUD_ENABLED plus the model files.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

M4_TOOLS = "/workspaces/m4-tools"
M4B_TOOLS = "/workspaces/m4b-tools"
BT = os.path.join(M4_TOOLS, "android-sdk", "build-tools", "34.0.0")
JAVAC = os.path.join(M4_TOOLS, "jdk-17", "bin", "javac")
KEYTOOL = os.path.join(M4_TOOLS, "jdk-17", "bin", "keytool")
ANDROID_JAR = os.path.join(
    M4_TOOLS, "android-sdk", "platforms", "android-34", "android.jar")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "assistant-app")
OUT = os.path.join(M4B_TOOLS, "assistant-build")
PUBLISH = os.path.join(M4B_TOOLS, "out")
KEYSTORE = os.path.join(M4B_TOOLS, "assistant-debug.keystore")
ABIS = ("arm64-v8a", "x86_64")
CLOUD_APK_NAME = "assistant-cloud.apk"
LOCAL_APK_NAME = "assistant.apk"
PERMISSION_MARK = "<!--PERMISSIONS-->"
INTERNET_LINE = ('<uses-permission '
                 'android:name="android.permission.INTERNET" />')

sys.path.insert(0, REPO)


def run(cmd, **kwargs):
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def competing_build_running():
    try:
        out = subprocess.run(["ps", "aux"], capture_output=True, text=True,
                             check=True).stdout
    except Exception:
        return False
    me = str(os.getpid())
    for line in out.splitlines():
        low = line.lower()
        if ("gradle" in low or "java" in low) and "ps aux" not in low:
            parts = line.split()
            if parts and parts[1] != me:
                return True
    return False


def permissions_in_badging(badging):
    """Permission names from `aapt2 dump badging` output."""
    return re.findall(r"uses-permission:\s*name='([^']+)'", badging)


def check_permissions(variant, badging):
    """Refuses any permission set but the variant's own.

    Local allows none; cloud allows exactly INTERNET. Returns the
    found names, else raises ValueError.
    """
    found = permissions_in_badging(badging)
    if variant == "local":
        expected = []
    elif variant == "cloud":
        expected = ["android.permission.INTERNET"]
    else:
        raise ValueError("unknown variant: " + variant)
    if sorted(found) != sorted(expected):
        raise ValueError("variant %s allows %s, APK has %s"
                         % (variant, expected, found))
    return found


def gen_config(manifest_path, gen_dir, variant):
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    files = {item["name"]: item for item in manifest["files"]}
    pte = next(v for k, v in files.items() if k.endswith(".pte"))
    tok = files["tokenizer.model"]
    route_a = os.path.dirname(manifest["target_path_route_a"]) + "/"
    body = (
        "package com.stargate.assistant;\n\n"
        "/** Generated from assistant-manifest.json by build.py. */\n"
        "public final class ModelConfig {\n"
        "    public static final String MODEL_ID = \"%s\";\n"
        "    public static final String PTE_NAME = \"%s\";\n"
        "    public static final long PTE_BYTES = %dL;\n"
        "    public static final String PTE_SHA256 = \"%s\";\n"
        "    public static final String TOK_NAME = \"%s\";\n"
        "    public static final long TOK_BYTES = %dL;\n"
        "    public static final String TOK_SHA256 = \"%s\";\n"
        "    public static final String ROUTE_A_DIR = \"%s\";\n"
        "    public static final String ROUTE_B_LEAF = \"model\";\n"
        "    public static final boolean CLOUD_ENABLED = %s;\n"
        "    private ModelConfig() {\n"
        "    }\n"
        "}\n" % (manifest["model_id"], pte["name"], pte["bytes"],
                  pte["sha256"], tok["name"], tok["bytes"],
                  tok["sha256"], route_a,
                  "true" if variant == "cloud" else "false")
    )
    package_dir = os.path.join(gen_dir, "com", "stargate", "assistant")
    os.makedirs(package_dir, exist_ok=True)
    with open(os.path.join(package_dir, "ModelConfig.java"), "w",
              encoding="utf-8") as handle:
        handle.write(body)


def write_variant_manifest(out_dir, variant):
    with open(os.path.join(APP, "AndroidManifest.xml"),
              encoding="utf-8") as handle:
        template = handle.read()
    if PERMISSION_MARK not in template:
        print("manifest template lost its %s mark" % PERMISSION_MARK,
              file=sys.stderr)
        return None
    replacement = INTERNET_LINE if variant == "cloud" else ""
    path = os.path.join(out_dir, "AndroidManifest.xml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(template.replace(PERMISSION_MARK, replacement))
    return path


def build_variant(variant, out_dir=None, build_root=None):
    """Builds one variant; returns (apk path, sha256) or raises."""
    root = build_root or OUT
    dest = out_dir or PUBLISH
    out = os.path.join(root, variant)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    classes_dir = os.path.join(out, "classes")
    dex_dir = os.path.join(out, "dex")
    gen_dir = os.path.join(out, "gen")
    os.makedirs(classes_dir)
    os.makedirs(dex_dir)

    # 0. Generated config from the approved manifest.
    gen_config(os.path.join(REPO, "assistant-manifest.json"), gen_dir,
               variant)

    # 1. Unpack classes.jar from both AARs.
    exe_aar = os.path.join(M4B_TOOLS,
                           "executorch-android-1.5.1.aar")
    fbjni_aar = os.path.join(M4B_TOOLS, "fbjni-0.7.0.aar")
    exe_jar = os.path.join(out, "executorch-classes.jar")
    fbjni_jar = os.path.join(out, "fbjni-classes.jar")
    with zipfile.ZipFile(exe_aar) as zipped:
        with zipped.open("classes.jar") as src, \
                open(exe_jar, "wb") as dst:
            shutil.copyfileobj(src, dst)
    with zipfile.ZipFile(fbjni_aar) as zipped:
        with zipped.open("classes.jar") as src, \
                open(fbjni_jar, "wb") as dst:
            shutil.copyfileobj(src, dst)
    native_jar = os.path.join(M4B_TOOLS, "nativeloader-0.10.5.jar")
    kotlin_jar = os.path.join(M4B_TOOLS, "kotlin-stdlib-1.9.23.jar")
    for path in (exe_jar, fbjni_jar, native_jar, kotlin_jar):
        if not os.path.exists(path):
            print("missing input: " + path, file=sys.stderr)
            return None

    # 2. Compile app sources (Java 8).
    sources = []
    for root_dir, _dirs, names in os.walk(os.path.join(APP, "java")):
        sources.extend(os.path.join(root_dir, name) for name in names
                       if name.endswith(".java"))
    for root_dir, _dirs, names in os.walk(gen_dir):
        sources.extend(os.path.join(root_dir, name) for name in names
                       if name.endswith(".java"))
    classpath = ":".join(
        [ANDROID_JAR, exe_jar, fbjni_jar, native_jar, kotlin_jar])
    run([JAVAC, "--release", "8", "-cp", classpath, "-d", classes_dir]
        + sorted(sources))

    # 3. Dex everything (app + all library classes), min API 23.
    app_jar = os.path.join(out, "app-classes.jar")
    with zipfile.ZipFile(app_jar, "w",
                         compression=zipfile.ZIP_DEFLATED) as jar:
        for root_dir, _dirs, names in os.walk(classes_dir):
            for name in sorted(names):
                if not name.endswith(".class"):
                    continue
                full = os.path.join(root_dir, name)
                jar.write(full, os.path.relpath(full, classes_dir))
    d8_cmd = [BT + "/d8", "-J-Xmx2048m", "--min-api", "23",
              "--lib", ANDROID_JAR, "--output", dex_dir]
    probe = subprocess.run([BT + "/d8", "-J-Xmx2048m", "--version"],
                           capture_output=True)
    if probe.returncode != 0:
        d8_cmd = [BT + "/d8", "--min-api", "23",
                  "--lib", ANDROID_JAR, "--output", dex_dir]
    run(d8_cmd + [app_jar, exe_jar, fbjni_jar, native_jar,
                  kotlin_jar])

    # 4. Debug key (own keystore; standard throwaway credentials).
    if not os.path.exists(KEYSTORE):
        run([KEYTOOL, "-genkeypair", "-keystore", KEYSTORE,
             "-alias", "androiddebugkey", "-storepass", "android",
             "-keypass", "android", "-keyalg", "RSA", "-keysize", "2048",
             "-validity", "10950", "-dname", "CN=StarGate M4b Debug"])

    # 5. Variant manifest + compiled resources, then link.
    manifest = write_variant_manifest(out, variant)
    if manifest is None:
        return None
    res_zip = os.path.join(out, "res.zip")
    run([BT + "/aapt2", "compile", "--dir",
         os.path.join(APP, "res"), "-o", res_zip])
    unsigned = os.path.join(out, "unsigned.apk")
    run([BT + "/aapt2", "link", "-o", unsigned, "--manifest",
         manifest, "-I", ANDROID_JAR, "-R", res_zip,
         "--min-sdk-version", "23", "--target-sdk-version", "34",
         "--version-code", "1", "--version-name", "1.0"])

    # 6. Add dex + licenses asset + native libs (.so uncompressed).
    notices = os.path.join(REPO, "third_party", "NOTICES.md")
    if not os.path.exists(notices):
        print("missing third_party/NOTICES.md", file=sys.stderr)
        return None
    with open(notices, "rb") as handle:
        licenses_blob = handle.read()
    with zipfile.ZipFile(exe_aar) as zipped:
        exe_sos = {name: zipped.read(name)
                   for name in zipped.namelist()
                   if name.startswith("jni/") and name.endswith(".so")}
    with zipfile.ZipFile(fbjni_aar) as zipped:
        fbjni_sos = {name: zipped.read(name)
                     for name in zipped.namelist()
                     if name.startswith("jni/") and name.endswith(".so")}
    with zipfile.ZipFile(unsigned, "a") as apk:
        apk.writestr("assets/licenses.txt", licenses_blob,
                     compress_type=zipfile.ZIP_DEFLATED)
        for root_dir, _dirs, names in os.walk(dex_dir):
            for name in sorted(names):
                if not name.endswith(".dex"):
                    continue
                full = os.path.join(root_dir, name)
                with open(full, "rb") as handle:
                    apk.writestr(name, handle.read(),
                                 compress_type=zipfile.ZIP_DEFLATED)
        for abi in ABIS:
            for lib in ("libexecutorch.so",):
                data = exe_sos["jni/%s/%s" % (abi, lib)]
                apk.writestr("lib/%s/%s" % (abi, lib), data,
                             compress_type=zipfile.ZIP_STORED)
            for lib in ("libfbjni.so", "libc++_shared.so"):
                data = fbjni_sos["jni/%s/%s" % (abi, lib)]
                apk.writestr("lib/%s/%s" % (abi, lib), data,
                             compress_type=zipfile.ZIP_STORED)

    # 7. Align + sign.
    aligned = os.path.join(out, "aligned.apk")
    run([BT + "/zipalign", "-f", "-p", "4", unsigned, aligned])
    final = os.path.join(
        out, "com.stargate.assistant-%s.apk" % variant)
    run([BT + "/apksigner", "sign", "--ks", KEYSTORE,
         "--ks-pass", "pass:android", "--key-pass", "pass:android",
         "--ks-key-alias", "androiddebugkey", "--out", final, aligned])

    # 8. Verify + report.
    badging = subprocess.run([BT + "/aapt2", "dump", "badging", final],
                             capture_output=True, text=True, check=True)
    print(badging.stdout)
    try:
        check_permissions(variant, badging.stdout)
    except ValueError as bad:
        print("refusing: %s" % bad, file=sys.stderr)
        return None
    with zipfile.ZipFile(final) as apk:
        names = apk.namelist()
        if "assets/licenses.txt" not in names:
            print("refusing: assets/licenses.txt missing", file=sys.stderr)
            return None
        if apk.read("assets/licenses.txt") != licenses_blob:
            print("refusing: licenses asset differs from NOTICES.md",
                  file=sys.stderr)
            return None
    bad = [name for name in names
           if name.endswith((".pte", ".gguf"))
           or name.endswith("tokenizer.model")]
    if bad:
        print("refusing: model file in APK: %s" % bad, file=sys.stderr)
        return None
    published = os.path.join(
        dest,
        CLOUD_APK_NAME if variant == "cloud" else LOCAL_APK_NAME)
    os.makedirs(dest, exist_ok=True)
    shutil.copyfile(final, published)
    digest = hashlib.sha256()
    with open(published, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    print("APK: %s (%d bytes)"
          % (published, os.path.getsize(published)))
    print("SHA-256: " + digest.hexdigest())
    return published, digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="build the Assistant APK variants")
    parser.add_argument("--variant",
                        choices=("local", "cloud", "both"),
                        default="both")
    parser.add_argument("--out-dir", default=PUBLISH,
                        help="final APK directory")
    parser.add_argument("--build-dir", default=OUT,
                        help="intermediate build root")
    args = parser.parse_args(argv)
    if competing_build_running():
        print("another java/gradle build is running; refusing to build",
              file=sys.stderr)
        return 1
    jdk_bin = os.path.join(M4_TOOLS, "jdk-17", "bin")
    os.environ["PATH"] = jdk_bin + ":" + os.environ.get("PATH", "")
    os.environ["JAVA_HOME"] = os.path.join(M4_TOOLS, "jdk-17")
    for path in (JAVAC, ANDROID_JAR, BT + "/d8", BT + "/aapt2",
                 BT + "/zipalign", BT + "/apksigner"):
        if not os.path.exists(path):
            print("missing tool: " + path, file=sys.stderr)
            return 1
    variants = ("local", "cloud") if args.variant == "both" \
        else (args.variant,)
    for variant in variants:
        print("=== variant: %s ===" % variant, flush=True)
        if build_variant(variant, out_dir=args.out_dir,
                         build_root=args.build_dir) is None:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
