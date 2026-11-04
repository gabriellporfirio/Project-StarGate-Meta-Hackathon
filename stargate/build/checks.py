"""Section 5 contents checks that need no image (M4 Part 1).

All checks read the actual artifacts, not just the manifest: the real
package names inside each APK (via aapt2), the model files in a model
folder, and the assets inside every APK. Any failure maps to exit 8.
Route A image checks and the Part 2 wiring come later.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import zipfile

EXIT_OK = 0
EXIT_CHECK_FAILED = 8

# Read-only spike toolchain (M4a never writes to /workspaces/m4-tools).
AAPT2 = "/workspaces/m4-tools/android-sdk/build-tools/34.0.0/aapt2"

# Package per M4a-owned module. The Assistant package is NOT a constant:
# the caller reads it from the approved assistant-manifest.json
# (assistant_package_from_manifest) and passes it in.
EXPECTED_PACKAGES = {
    "launcher": "com.stargate.launcher",
    "files": "com.stargate.files",
}

# File suffixes that mark a model payload (runtime weight, tokenizer
# or converted model file). Anything with one of these is treated as
# a model file by the contents checks.
MODEL_SUFFIXES = (
    ".pte", ".ptl", ".gguf", ".ggml", ".bin", ".safetensors",
    ".onnx", ".tflite", ".model", ".ckpt", ".pth", ".pt", ".pb",
)

_PACKAGE_RE = re.compile(r"^package:\s+name='([^']+)'\s.*?"
                         r"versionName='([^']*)'", re.MULTILINE)
_PERMISSION_RE = re.compile(r"^uses-permission:\s+name='([^']+)'",
                            re.MULTILINE)


def assistant_package_from_manifest(manifest: dict) -> str:
    """Assistant package name from the approved assistant-manifest.json.

    Raises ValueError when the handover has no assistant_apk.package.
    """
    package = None
    if isinstance(manifest, dict):
        apk = manifest.get("assistant_apk")
        if isinstance(apk, dict):
            package = apk.get("package")
    if not isinstance(package, str) or not package:
        raise ValueError(
            "assistant manifest has no assistant_apk.package")
    return package


def expected_packages_for_selection(services: list,
                                    assistant_package=None) -> list:
    """Packages a build must contain: the launcher plus one per service.

    Each module is included only when its service is selected, so an
    Assistant-only build has no Files app and vice versa. The Assistant
    package comes from the approved manifest; a ValueError is raised
    when the Assistant is selected without one.
    """
    packages = [EXPECTED_PACKAGES["launcher"]]
    for service in services:
        if service == "files":
            packages.append(EXPECTED_PACKAGES["files"])
        elif service == "assistant":
            if assistant_package is None:
                raise ValueError(
                    "assistant selected but no assistant package "
                    "given (read it from assistant-manifest.json)")
            packages.append(assistant_package)
    return packages


def launcher_services(plan: dict) -> list:
    """Services the launcher shows, from the handoff only."""
    return list(plan["handoff"]["services"])


def launcher_config(plan: dict, build_id: str, labels: list) -> dict:
    """Display strings baked into the launcher (services: handoff only)."""
    return {"services": launcher_services(plan),
            "build_id": build_id, "labels": list(labels)}


def is_model_file(name: str) -> bool:
    """True when a file name looks like a model payload file."""
    lowered = name.lower()
    return lowered.endswith(MODEL_SUFFIXES)


def run_aapt2_badging(apk_path: str, aapt2: str = AAPT2) -> str:
    """Dump an APK's badging lines with aapt2. Raises on failure."""
    proc = subprocess.run(
        [aapt2, "dump", "badging", apk_path],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise OSError("aapt2 dump badging failed for %s: %s"
                      % (apk_path, proc.stderr.strip()))
    return proc.stdout


def apk_package(apk_path: str, aapt2: str = AAPT2):
    """Real (package, version) read from inside the APK itself."""
    match = _PACKAGE_RE.search(run_aapt2_badging(apk_path, aapt2))
    if match is None:
        raise OSError("aapt2 found no package line in %s" % apk_path)
    return match.group(1), match.group(2)


def apk_permissions(apk_path: str, aapt2: str = AAPT2) -> list:
    """uses-permission names read from inside the APK itself."""
    return _PERMISSION_RE.findall(
        run_aapt2_badging(apk_path, aapt2))


def apk_assets(apk_path: str) -> list:
    """Names under assets/ inside the APK (relative, sorted)."""
    with zipfile.ZipFile(apk_path, "r") as archive:
        names = [item.filename for item in archive.infolist()
                 if item.filename.startswith("assets/")
                 and not item.is_dir()]
    return sorted(names[len("assets/"):] for names in names)


def model_folder_files(model_dir: str) -> list:
    """Every file under the model folder (relative paths, sorted).

    An empty list when the folder is missing: a Files-only build has
    no model folder at all.
    """
    found = []
    if not os.path.isdir(model_dir):
        return found
    for root, _dirs, files in os.walk(model_dir):
        for name in files:
            full = os.path.join(root, name)
            found.append(os.path.relpath(full, model_dir))
    return sorted(found)


def check_apps_match_selection(apk_paths: list, selection: list,
                              aapt2: str = AAPT2,
                              assistant_package=None) -> list:
    """Apps match the selection exactly (exit 8).

    assistant_package is the Assistant package from the approved
    manifest; required when the Assistant is selected.
    """
    errors = []
    try:
        packages = [apk_package(path, aapt2)[0] for path in apk_paths]
    except OSError as exc:
        return ["cannot list APK packages: %s" % exc]
    wanted = expected_packages_for_selection(selection, assistant_package)
    for package in wanted:
        if package not in packages:
            errors.append("missing app %s for this selection" % package)
    for package in packages:
        if package not in wanted:
            errors.append("unselected app %s in this build" % package)
    if len(set(packages)) != len(packages):
        errors.append("duplicate package in this build")
    return errors


def check_model_files(model_dir: str, apk_paths: list,
                      expected) -> list:
    """Model files match exactly (exit 8).

    expected is None without the Assistant (then any model payload
    fails), or the approved manifest's file list of {name, bytes,
    sha256} with it. APK assets must never hide a model file.
    """
    errors = []
    for apk_path in apk_paths:
        try:
            assets = apk_assets(apk_path)
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append("cannot list assets of %s: %s"
                          % (apk_path, exc))
            continue
        for name in assets:
            if is_model_file(name):
                errors.append("model file %s hidden in %s assets"
                              % (name, apk_path))
    try:
        present = model_folder_files(model_dir)
    except OSError as exc:
        return errors + ["cannot list model folder: %s" % exc]
    if expected is None:
        for name in present:
            errors.append("model file %s in a build without "
                          "the Assistant" % name)
        return errors
    wanted = {item["name"]: item for item in expected}
    for name in present:
        if name not in wanted:
            errors.append("unexpected model file %s" % name)
            continue
        full = os.path.join(model_dir, name)
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            errors.append("model file %s is unreadable: %s"
                          % (name, exc))
            continue
        if len(data) != wanted[name]["bytes"]:
            errors.append("model file %s has %d bytes, expected %r"
                          % (name, len(data), wanted[name]["bytes"]))
            continue
        if hashlib.sha256(data).hexdigest() != wanted[name]["sha256"]:
            errors.append("model file %s content does not match "
                          "its approved hash" % name)
    for name in wanted:
        if name not in present:
            errors.append("missing model file %s" % name)
    return errors


def run_contents_checks(apk_paths: list, selection: list,
                       model_dir: str, expected_model_files,
                       aapt2: str = AAPT2, assistant_package=None):
    """Run the no-image section 5 checks. Returns (exit_code, errors).

    assistant_package is the Assistant package from the approved
    manifest; required when the Assistant is selected.
    """
    errors = check_apps_match_selection(apk_paths, selection, aapt2,
                                        assistant_package)
    errors.extend(check_model_files(model_dir, apk_paths,
                                    expected_model_files))
    if errors:
        return EXIT_CHECK_FAILED, errors
    return EXIT_OK, []
