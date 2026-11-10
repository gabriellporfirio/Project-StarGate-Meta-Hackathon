"""Build the phone's one app (the hub) for one set of chosen services.

The hub is the phone's home screen and its one server. This builder
compiles it with plain tools (javac, d8, aapt2, zipalign, apksigner):
no Gradle and no network. Only what the person chose is baked in: the
list of services, the build ID, and (when the assistant runs on the
phone) the model runtime.

Tools are found in this order: the STARGATE_ANDROID_TOOLS folder (an
Android SDK), the old /workspaces/m4-tools SDK, then the system
(apt packages aapt, apksigner, zipalign) plus the two jars that
scripts/setup-android-tools.sh downloads into ~/.stargate/tools (or
STARGATE_ANDROID_JAR and STARGATE_R8_JAR). `python3 -m stargate.build.hubapk --doctor` prints
what was found.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HUB = os.path.join(REPO, "android", "hub")
APP_UI = os.path.join(REPO, "stargate", "app")
ASSISTANT_SRC = os.path.join(
    REPO, "assistant-app", "java", "com", "stargate", "assistant")

PACKAGE = "com.stargate.launcher"
APK_NAME = "stargate-launcher.apk"
CONFIG_NAME = "stargate-build.json"
SERVICES = ("files", "assistant", "vault")
MIN_SDK = "28"
TARGET_SDK = "34"

# The assistant classes the hub reuses (cloud path, pure Java plus the
# Keystore store). The old assistant screen is not part of the hub.
ASSISTANT_CLASSES = (
    "AnswerAccumulator", "ChatFormat", "CloudAsk", "CloudRequest",
    "CloudSpec", "ErrorPolicy", "HttpNetworkLayer", "Json",
    "KeyStorage", "KeystoreKeyStorage", "NetworkLayer",
)
# Extra page files the hub borrows from the computer-side app.
SHARED_UI = (
    os.path.join("fonts", "space-grotesk.woff2"),
    os.path.join("fonts", "jetbrains-mono.woff2"),
    "logo-light.png",
)
# Where scripts/setup-android-tools.sh puts what apt does not carry.
LOCAL_TOOLS = os.path.join(os.path.expanduser("~"), ".stargate", "tools")
ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64")


class BuildError(Exception):
    """The hub could not be built; the message says why."""


def _first(paths: list):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def find_tools(env=None) -> dict:
    """Locate every tool; missing ones map to None."""
    env = os.environ if env is None else env
    sdks = [env.get("STARGATE_ANDROID_TOOLS"),
            env.get("ANDROID_HOME"),
            "/workspaces/m4-tools/android-sdk"]
    build_tools = []
    platforms = []
    for sdk in sdks:
        if not sdk:
            continue
        build_tools += sorted(glob.glob(
            os.path.join(sdk, "build-tools", "*")), reverse=True)
        platforms += sorted(glob.glob(
            os.path.join(sdk, "platforms", "android-3[4-9]",
                         "android.jar")), reverse=True)

    def tool(name):
        return _first([os.path.join(folder, name)
                       for folder in build_tools]) or shutil.which(name)

    java_home = _first([env.get("STARGATE_JAVA_HOME"),
                        "/workspaces/m4-tools/jdk-17"])

    def java(name):
        if java_home and os.path.exists(
                os.path.join(java_home, "bin", name)):
            return os.path.join(java_home, "bin", name)
        return shutil.which(name)

    return {
        "javac": java("javac"),
        "java": java("java"),
        "keytool": java("keytool"),
        "aapt2": tool("aapt2"),
        "zipalign": tool("zipalign"),
        "apksigner": tool("apksigner"),
        "d8": tool("d8"),
        "r8_jar": _first([env.get("STARGATE_R8_JAR")]
                         + sorted(glob.glob(os.path.join(
                             LOCAL_TOOLS, "r8*.jar")))),
        "android_jar": _first([env.get("STARGATE_ANDROID_JAR")]
                              + platforms
                              + sorted(glob.glob(os.path.join(
                                  LOCAL_TOOLS, "android*.jar")))),
    }


def missing_tools(tools: dict) -> list:
    need = ["javac", "keytool", "aapt2", "zipalign", "apksigner",
            "android_jar"]
    missing = [name for name in need if not tools.get(name)]
    if not tools.get("d8") and not (tools.get("r8_jar")
                                    and tools.get("java")):
        missing.append("d8 (or STARGATE_R8_JAR)")
    return missing


def hub_config(services: list, build_id: str, labels=None,
               local_model=None) -> dict:
    """The config baked into the app: exactly what was chosen."""
    unknown = [name for name in services if name not in SERVICES]
    if unknown:
        raise BuildError("unknown service: %s" % ", ".join(unknown))
    if not isinstance(build_id, str) or not build_id:
        raise BuildError("a build ID is required")
    config = {
        "services": [name for name in SERVICES if name in services],
        "build_id": build_id,
        "labels": list(labels or []),
    }
    if local_model is not None:
        if "assistant" not in services:
            raise BuildError("a model needs the assistant service")
        for key in ("name", "pte", "tokenizer", "pte_sha256",
                    "tokenizer_sha256"):
            if not isinstance(local_model.get(key), str) \
                    or not local_model[key]:
                raise BuildError("local model is missing %r" % key)
        config["local_model"] = dict(local_model)
    return config


def _run(cmd: list, log) -> None:
    log("+ " + " ".join(cmd))
    env = dict(os.environ)
    env.pop("JAVA_TOOL_OPTIONS", None)
    done = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if done.returncode != 0:
        raise BuildError("%s failed (exit %d):\n%s" % (
            os.path.basename(cmd[0]), done.returncode,
            (done.stdout + done.stderr)[-4000:]))


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sources(with_model: bool) -> list:
    found = sorted(glob.glob(os.path.join(
        HUB, "java", "com", "stargate", "hub", "*.java")))
    found += sorted(glob.glob(os.path.join(
        HUB, "android", "com", "stargate", "hub", "*.java")))
    found += [os.path.join(ASSISTANT_SRC, name + ".java")
              for name in ASSISTANT_CLASSES]
    if with_model:
        found += sorted(glob.glob(os.path.join(
            HUB, "local", "com", "stargate", "hub", "*.java")))
    return found


def _runtime_jars(runtime_dir: str, work: str) -> tuple:
    """Unpack the model runtime: (class jars, {abi: [.so paths]})."""
    jars = []
    libs: dict = {}
    for aar in sorted(glob.glob(os.path.join(runtime_dir, "*.aar"))):
        stem = os.path.splitext(os.path.basename(aar))[0]
        with zipfile.ZipFile(aar) as zipped:
            jar = os.path.join(work, stem + "-classes.jar")
            with open(jar, "wb") as out:
                out.write(zipped.read("classes.jar"))
            jars.append(jar)
            for name in zipped.namelist():
                parts = name.split("/")
                if len(parts) == 3 and parts[0] == "jni" \
                        and parts[1] in ABIS and name.endswith(".so"):
                    target = os.path.join(work, "lib", parts[1], parts[2])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "wb") as out:
                        out.write(zipped.read(name))
                    libs.setdefault(parts[1], []).append(target)
    jars += sorted(glob.glob(os.path.join(runtime_dir, "*.jar")))
    if not jars:
        raise BuildError(
            "no model runtime found in %s (expected the ExecuTorch "
            "and fbjni .aar files plus nativeloader .jar)" % runtime_dir)
    return jars, libs


def build(config: dict, out_dir: str, *, tools=None, keystore=None,
          runtime_dir=None, log=None) -> dict:
    """Build and sign the hub; returns {path, sha256, bytes, package}."""
    log = log or (lambda line: None)
    tools = tools or find_tools()
    missing = missing_tools(tools)
    if missing:
        raise BuildError("missing build tools: %s" % ", ".join(missing))
    with_model = "local_model" in config
    if with_model and not runtime_dir:
        raise BuildError("this build carries a model, so it needs the "
                         "model runtime folder")
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="hub-build-")
    try:
        classes = os.path.join(work, "classes")
        dex = os.path.join(work, "dex")
        assets = os.path.join(work, "assets")
        os.makedirs(classes)
        os.makedirs(dex)
        shutil.copytree(os.path.join(HUB, "assets"), assets)
        for name in SHARED_UI:
            shutil.copy(os.path.join(APP_UI, name),
                        os.path.join(assets, "ui", os.path.basename(name)))
        with open(os.path.join(assets, CONFIG_NAME), "w",
                  encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")

        jars, libs = ([], {})
        if with_model:
            jars, libs = _runtime_jars(runtime_dir, work)
        android_jar = tools["android_jar"]
        with zipfile.ZipFile(android_jar) as platform:
            has_core = "java/lang/Object.class" in platform.namelist()
        if has_core:
            # A real SDK android.jar: it is the whole platform.
            flags = ["-source", "8", "-target", "8", "-Xlint:-options",
                     "-bootclasspath", android_jar,
                     "-classpath", os.pathsep.join(jars)]
        else:
            # A framework-only jar: core Java comes from the JDK.
            flags = ["--release", "8", "-Xlint:-options", "-classpath",
                     os.pathsep.join([android_jar] + jars)]
        _run([tools["javac"], "-nowarn"] + flags
             + ["-d", classes] + _sources(with_model), log)
        class_files = sorted(glob.glob(
            os.path.join(classes, "**", "*.class"), recursive=True))
        d8_args = ["--release", "--min-api", MIN_SDK, "--lib",
                   android_jar, "--output", dex] + class_files + jars
        if tools.get("d8"):
            _run([tools["d8"], "-JXmx2g"] + d8_args, log)
        else:
            _run([tools["java"], "-Xmx2g", "-cp", tools["r8_jar"],
                  "com.android.tools.r8.D8"] + d8_args, log)

        unsigned = os.path.join(work, "unsigned.apk")
        _run([tools["aapt2"], "link", "-o", unsigned, "-I", android_jar,
              "--manifest", os.path.join(HUB, "AndroidManifest.xml"),
              "-A", assets, "--min-sdk-version", MIN_SDK,
              "--target-sdk-version", TARGET_SDK], log)
        with zipfile.ZipFile(unsigned, "a") as apk:
            for name in sorted(os.listdir(dex)):
                apk.write(os.path.join(dex, name), name,
                          compress_type=zipfile.ZIP_DEFLATED)
            for abi in sorted(libs):
                for path in sorted(libs[abi]):
                    apk.write(path, "lib/%s/%s" % (
                        abi, os.path.basename(path)),
                        compress_type=zipfile.ZIP_STORED)
        aligned = os.path.join(work, "aligned.apk")
        _run([tools["zipalign"], "-f", "-p", "4", unsigned, aligned], log)

        keystore = keystore or os.path.join(out_dir, "debug.keystore")
        if not os.path.isfile(keystore):
            _run([tools["keytool"], "-genkeypair", "-keystore", keystore,
                  "-storepass", "android", "-keypass", "android",
                  "-keyalg", "RSA", "-keysize", "2048",
                  "-validity", "10950", "-alias", "hub",
                  "-dname", "CN=Local build"], log)
        final = os.path.join(out_dir, APK_NAME)
        _run([tools["apksigner"], "sign", "--ks", keystore,
              "--ks-pass", "pass:android", "--key-pass", "pass:android",
              "--out", final, aligned], log)
        _run([tools["apksigner"], "verify", final], log)
        side = final + ".idsig"
        if os.path.exists(side):
            os.remove(side)
        return {"path": final, "sha256": sha256_file(final),
                "bytes": os.path.getsize(final), "package": PACKAGE,
                "services": config["services"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m stargate.build.hubapk",
        description="Build the phone's one app for the chosen services.")
    parser.add_argument("--doctor", action="store_true",
                        help="only show which build tools were found")
    parser.add_argument("--services", default="files,assistant,vault")
    parser.add_argument("--build-id", default="LOCAL-0001")
    parser.add_argument("--out", default=os.path.join(REPO, "out", "hub"))
    parser.add_argument("--local-model",
                        help="JSON file describing the on-phone model")
    parser.add_argument("--runtime", help="folder with the model runtime")
    args = parser.parse_args(argv)
    tools = find_tools()
    if args.doctor:
        for name in sorted(tools):
            print("%-12s %s" % (name, tools[name] or "MISSING"))
        missing = missing_tools(tools)
        print("ready" if not missing
              else "missing: " + ", ".join(missing))
        return 0 if not missing else 1
    try:
        model = None
        if args.local_model:
            with open(args.local_model, encoding="utf-8") as handle:
                model = json.load(handle)
        config = hub_config(
            [name for name in args.services.split(",") if name],
            args.build_id, local_model=model)
        result = build(config, args.out, tools=tools,
                       runtime_dir=args.runtime, log=print)
    except (BuildError, OSError, ValueError) as failed:
        sys.stderr.write("error: %s\n" % failed)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
