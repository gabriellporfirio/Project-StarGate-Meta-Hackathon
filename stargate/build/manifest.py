"""build-v1: the build manifest schema, writer and validator (M4).

Every build writes build-manifest.json: the plan's SHA-256, the
selection, the route, the build ID, each artifact (path, bytes,
SHA-256), each app (service, package name, version, SHA-256), the
model files (name, bytes, SHA-256, target path on the device) if any,
how the model is delivered (model_delivery: in_image, after_boot or
none, with the payload snapshot path when a snapshot was made), the
runtime version, the Assistant manifest's SHA-256, the unresolved
checks from the handoff, and (Route A) the image size against its
partition limit. Labels carry the section 1 experiment label.

The route accepts A or B, plus not_yet while Part 2 has not decided
it. not_yet is internal draft state only: no exit 0 and no finished
build may carry it; a real build refuses it. The authoritative
validator is here (standard library only); schemas/build-v1.json
describes it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

SCHEMA_VERSION = "build-v1"
MANIFEST_NAME = "build-manifest.json"

ROUTES = ("A", "B", "not_yet")
SELECTION_RESULTS = ("fits", "unconfirmed")
SERVICES = ("files", "assistant")

TOP_LEVEL_KEYS = frozenset({
    "schema_version", "plan_sha256", "selection", "selection_result",
    "route", "build_id", "labels", "artifacts", "apps", "model_files",
    "model_delivery", "model_payload_path",
    "runtime_version", "assistant_manifest_sha256", "unresolved",
    "image_bytes", "partition_limit_bytes",
})
MODEL_DELIVERIES = ("in_image", "after_boot", "none")
OPTIONAL_KEYS = frozenset({"image_bytes", "partition_limit_bytes"})
ARTIFACT_KEYS = frozenset({"path", "bytes", "sha256"})
APP_KEYS = frozenset({"service", "package", "version", "path", "sha256"})
MODEL_FILE_KEYS = frozenset({"name", "bytes", "sha256", "target"})

SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def sha256_file(path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.match(value) is not None


def _is_whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) \
        and value >= 0


def make_manifest(plan_sha256: str, selection: list,
                 selection_result: str, route: str, build_id: str,
                 labels: list, artifacts: list, apps: list,
                 model_files, runtime_version,
                 assistant_manifest_sha256, unresolved: list,
                 image_bytes=None,
                 partition_limit_bytes=None,
                 model_delivery: str = "none",
                 model_payload_path=None) -> dict:
    """Build a build-v1 dict. model_files/runtime_version/manifest hash
    are None when the Assistant is not selected. image_bytes and
    partition_limit_bytes record the Route A size check; None
    elsewhere. model_delivery is in_image, after_boot or none;
    model_payload_path is the payload snapshot dir (relative to the
    build outputs) when the build made one, else None."""
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": plan_sha256,
        "selection": list(selection),
        "selection_result": selection_result,
        "route": route,
        "build_id": build_id,
        "labels": list(labels),
        "artifacts": [dict(item) for item in artifacts],
        "apps": [dict(item) for item in apps],
        "model_files": [dict(item) for item in model_files]
        if model_files is not None else None,
        "model_delivery": model_delivery,
        "model_payload_path": model_payload_path,
        "runtime_version": runtime_version,
        "assistant_manifest_sha256": assistant_manifest_sha256,
        "unresolved": [dict(item) for item in unresolved],
        "image_bytes": image_bytes,
        "partition_limit_bytes": partition_limit_bytes,
    }


def validate_manifest(manifest: object) -> list:
    """Validate a build-v1 dict; returns a list of error strings."""
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    errors = []
    for key in manifest:
        if key not in TOP_LEVEL_KEYS:
            errors.append("manifest has unknown key %r" % (key,))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append('schema_version must be "%s"' % SCHEMA_VERSION)
    if not _is_sha(manifest.get("plan_sha256")):
        errors.append("plan_sha256 must be a SHA-256 hex string")
    selection = manifest.get("selection")
    if not isinstance(selection, list) or not selection or any(
            item not in SERVICES for item in selection):
        errors.append("selection must be a non-empty known selection")
    if manifest.get("selection_result") not in SELECTION_RESULTS:
        errors.append("selection_result must be one of %s"
                      % ("/".join(SELECTION_RESULTS),))
    if manifest.get("route") not in ROUTES:
        errors.append("route must be one of %s" % ("/".join(ROUTES),))
    build_id = manifest.get("build_id")
    if not isinstance(build_id, str) or not build_id.strip():
        errors.append("build_id is required")
    labels = manifest.get("labels")
    if not isinstance(labels, list) or any(
            not isinstance(item, str) for item in labels):
        errors.append("labels must be a list of strings")
    errors.extend(_validate_artifacts(manifest.get("artifacts")))
    errors.extend(_validate_apps(manifest.get("apps"), selection))
    errors.extend(_validate_model_files(
        manifest.get("model_files"), selection,
        manifest.get("runtime_version"),
        manifest.get("assistant_manifest_sha256")))
    errors.extend(_validate_delivery(
        manifest.get("model_delivery"),
        manifest.get("model_payload_path"),
        manifest.get("model_files"), selection))
    unresolved = manifest.get("unresolved")
    if not isinstance(unresolved, list) or not unresolved:
        errors.append("unresolved must be a non-empty list")
    else:
        for index, item in enumerate(unresolved):
            if not isinstance(item, dict) \
                    or not isinstance(item.get("area"), str) \
                    or not isinstance(item.get("detail"), str):
                errors.append("unresolved[%d]: needs area and detail"
                              % index)
    for key in ("image_bytes", "partition_limit_bytes"):
        value = manifest.get(key)
        if value is not None and not _is_whole(value):
            errors.append("%s must be whole or null" % key)
    for required in TOP_LEVEL_KEYS - OPTIONAL_KEYS:
        if required not in manifest:
            errors.append("manifest is missing %r" % required)
    return errors


def _validate_artifacts(artifacts: object) -> list:
    if not isinstance(artifacts, list):
        return ["artifacts must be a list"]
    errors = []
    for index, item in enumerate(artifacts):
        label = "artifacts[%d]" % index
        if not isinstance(item, dict):
            errors.append("%s: must be an object" % label)
            continue
        for key in item:
            if key not in ARTIFACT_KEYS:
                errors.append("%s: unknown key %r" % (label, key))
        if not isinstance(item.get("path"), str) or not item["path"]:
            errors.append("%s: path is required" % label)
        if not _is_whole(item.get("bytes")):
            errors.append("%s: bytes must be whole" % label)
        if not _is_sha(item.get("sha256")):
            errors.append("%s: sha256 must be SHA-256 hex" % label)
    return errors


def _validate_apps(apps: object, selection: object) -> list:
    if not isinstance(apps, list) or not apps:
        return ["apps must be a non-empty list"]
    errors = []
    seen_packages = set()
    seen_services = set()
    for index, item in enumerate(apps):
        label = "apps[%d]" % index
        if not isinstance(item, dict):
            errors.append("%s: must be an object" % label)
            continue
        for key in item:
            if key not in APP_KEYS:
                errors.append("%s: unknown key %r" % (label, key))
        service = item.get("service")
        if service not in ("launcher",) + SERVICES:
            errors.append("%s: bad service" % label)
        else:
            seen_services.add(service)
        package = item.get("package")
        if not isinstance(package, str) or not package:
            errors.append("%s: package is required" % label)
        else:
            if package in seen_packages:
                errors.append("%s: duplicate package %r" % (label,
                                                           package))
            seen_packages.add(package)
        if not isinstance(item.get("version"), str) \
                or not item["version"]:
            errors.append("%s: version is required" % label)
        if not isinstance(item.get("path"), str) or not item["path"]:
            errors.append("%s: path is required" % label)
        if not _is_sha(item.get("sha256")):
            errors.append("%s: sha256 must be SHA-256 hex" % label)
    if "launcher" not in seen_services:
        errors.append("apps must include the launcher")
    if isinstance(selection, list):
        for service in selection:
            if service in SERVICES and service not in seen_services:
                errors.append("apps is missing the %s app" % service)
    return errors


def _validate_model_files(model_files: object, selection: object,
                         runtime_version: object,
                         manifest_sha: object) -> list:
    wants_model = isinstance(selection, list) \
        and "assistant" in selection
    if not wants_model:
        errors = []
        if model_files is not None:
            errors.append("model_files must be null without "
                          "the Assistant")
        if runtime_version is not None:
            errors.append("runtime_version must be null without "
                          "the Assistant")
        if manifest_sha is not None:
            errors.append("assistant_manifest_sha256 must be null "
                          "without the Assistant")
        return errors
    errors = []
    if not isinstance(model_files, list) or not model_files:
        errors.append("model_files must be a non-empty list with "
                      "the Assistant")
    else:
        for index, item in enumerate(model_files):
            label = "model_files[%d]" % index
            if not isinstance(item, dict):
                errors.append("%s: must be an object" % label)
                continue
            for key in item:
                if key not in MODEL_FILE_KEYS:
                    errors.append("%s: unknown key %r" % (label, key))
            if not isinstance(item.get("name"), str) \
                    or not item["name"]:
                errors.append("%s: name is required" % label)
            if not _is_whole(item.get("bytes")):
                errors.append("%s: bytes must be whole" % label)
            if not _is_sha(item.get("sha256")):
                errors.append("%s: sha256 must be SHA-256 hex" % label)
            if not isinstance(item.get("target"), str) \
                    or not item["target"]:
                errors.append("%s: target is required" % label)
    if not isinstance(runtime_version, str) or not runtime_version:
        errors.append("runtime_version is required with the Assistant")
    if not _is_sha(manifest_sha):
        errors.append("assistant_manifest_sha256 must be a SHA-256 "
                      "hex string with the Assistant")
    return errors


def _validate_delivery(delivery: object, payload_path: object,
                       model_files: object,
                       selection: object) -> list:
    wants_model = isinstance(selection, list) \
        and "assistant" in selection
    if delivery not in MODEL_DELIVERIES:
        return ["model_delivery must be one of %s"
                % ("/".join(MODEL_DELIVERIES),)]
    if delivery == "none":
        errors = []
        if wants_model:
            errors.append("model_delivery is none with the Assistant "
                          "selected")
        if payload_path is not None:
            errors.append("model_payload_path must be null unless a "
                          "payload snapshot was made")
        return errors
    errors = []
    if not wants_model:
        errors.append("model_delivery is %s without the Assistant"
                      % (delivery,))
    if payload_path is not None:
        if delivery != "after_boot":
            errors.append("model_payload_path must be null unless "
                          "model_delivery is after_boot")
        elif not isinstance(payload_path, str) or not payload_path \
                or payload_path.startswith("/") \
                or ".." in payload_path.split("/"):
            errors.append("model_payload_path must be a relative path "
                          "without ..")
    if isinstance(model_files, list):
        for index, item in enumerate(model_files):
            if not isinstance(item, dict):
                continue
            target = item.get("target")
            if not isinstance(target, str) or not target:
                continue
            if delivery == "after_boot" \
                    and not target.startswith("/sdcard/"):
                errors.append(
                    "model_files[%d]: target must be the /sdcard/ "
                    "app folder when model_delivery is after_boot"
                    % index)
            if delivery == "in_image" \
                    and not target.startswith("/product/"):
                errors.append(
                    "model_files[%d]: target must be under /product/ "
                    "when model_delivery is in_image" % index)
    return errors


def write_manifest(manifest: dict, out_dir: str) -> str:
    """Validate and write build-manifest.json. Returns its path.

    Raises ValueError when the manifest is invalid; nothing is
    written then. A finished manifest whose route is still not_yet
    is refused too: that route is internal draft state only.
    """
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("invalid manifest: %s" % errors[0])
    if manifest.get("route") == "not_yet":
        raise ValueError("invalid manifest: route is still not_yet; "
                         "no finished build may carry it")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, MANIFEST_NAME)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return out_path


def verify_artifact_hashes(manifest: dict, base_dir: str) -> list:
    """Re-hash every artifact (and app APK) against the manifest.

    With an after_boot payload snapshot, each recorded model file is
    re-hashed from the snapshot dir too. Returns a list of mismatch
    strings; empty means every recorded hash matches the file on disk.
    """
    errors = []
    for item in manifest.get("artifacts", []):
        errors.extend(_verify_one(base_dir, item.get("path"),
                                  item.get("bytes"), item.get("sha256"),
                                  "artifact"))
    for item in manifest.get("apps", []):
        errors.extend(_verify_one(base_dir, item.get("path"), None,
                                  item.get("sha256"), "app"))
    if manifest.get("model_delivery") == "after_boot" \
            and isinstance(manifest.get("model_payload_path"), str):
        payload = manifest["model_payload_path"]
        for item in manifest.get("model_files", []):
            if not isinstance(item, dict):
                continue
            errors.extend(_verify_one(
                base_dir, payload + "/" + str(item.get("name")),
                item.get("bytes"), item.get("sha256"),
                "model payload"))
    return errors


def _verify_one(base_dir, path, want_bytes, want_sha, what) -> list:
    if not isinstance(path, str) or not path:
        return ["%s has no path" % what]
    full = os.path.join(base_dir, path)
    try:
        with open(full, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return ["%s %s is unreadable: %s" % (what, path, exc)]
    errors = []
    if want_bytes is not None and len(data) != want_bytes:
        errors.append("%s %s has %d bytes, manifest says %r"
                      % (what, path, len(data), want_bytes))
    if hashlib.sha256(data).hexdigest() != want_sha:
        errors.append("%s %s content does not match its manifest hash"
                      % (what, path))
    return errors


def validate_file(path: str):
    """Validate a build-v1 file. Returns (ok, errors)."""
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        return False, ["cannot read manifest: %s" % exc]
    errors = validate_manifest(manifest)
    return (not errors), errors
