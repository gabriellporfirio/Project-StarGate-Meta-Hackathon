"""Fresh-build driver for the guided app (M6 Part 1, Build step).

Runs one accepted plan through, always under the heavy-step helper and the
shared build lock (the server spawns ``ahead-heavy.sh ... appbuild ...``):

1. rebuild the launcher and Files APKs for this plan and build ID with
   ``android/build_apps.sh --plan --build-id`` and ``--out/--tools``
   inside the app's own tools folder (never /workspaces/m4a-tools);
2. run the Route A image build into the job dir through the existing
   in-process entry point, with explicit inputs naming those fresh APKs
   plus the read-only approved Assistant APK and model snapshots (never
   the ``stargate build`` CLI: it takes the frozen M4 APKs, whose
   launcher has M4's build ID baked in);
3. check the launcher config inside the new launcher APK (build ID and
   services) equals the new build-manifest.json, or fail.

Part 1 builds fresh only and adds no new shared build-manifest fields.
Every stage prints progress lines (the server captures them); result.json
is always written, on success or failure. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile

from .build import gate as gate_module
from .build.image import AFTER_BOOT_LABEL, build_id_for_plan

FROZEN_PREFIXES = (
    "/workspaces/m4a-tools",
    "/workspaces/m4b-tools",
    "/workspaces/m5-tools",
    "/workspaces/m4b-downloads",
)

EXIT_OK = 0
EXIT_USAGE = 2


def _say(text: str):
    print(text, flush=True)


def _refuse_frozen(path: str, what: str):
    """Refuse any output path inside a frozen tools folder."""
    real = os.path.realpath(path)
    for prefix in FROZEN_PREFIXES:
        if real == prefix or real.startswith(prefix + os.sep):
            raise DriverError(
                "%s may not point at frozen outputs: %s" % (what, path))


class DriverError(Exception):
    """A driver failure (maps to exit 8 unless a step says otherwise)."""


def read_launcher_asset(launcher_apk: str) -> dict:
    """Baked stargate-build.json from inside a launcher APK (no aapt2)."""
    try:
        with zipfile.ZipFile(launcher_apk, "r") as archive:
            raw = archive.read("assets/stargate-build.json")
    except KeyError:
        raise DriverError("launcher APK %s has no baked "
                          "stargate-build.json asset" % launcher_apk)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DriverError("launcher APK %s is unreadable: %s"
                          % (launcher_apk, exc))
    try:
        config = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise DriverError("launcher APK %s baked config is not JSON"
                          % launcher_apk)
    if not isinstance(config, dict):
        raise DriverError("launcher APK %s baked config is not an object"
                          % launcher_apk)
    return config


def check_launcher_config(launcher_apk: str, manifest: dict) -> dict:
    """The baked config equals the new manifest (build ID, services).

    Returns the check record; raises DriverError on any mismatch.
    """
    config = read_launcher_asset(launcher_apk)
    problems = []
    if config.get("build_id") != manifest.get("build_id"):
        problems.append("build_id baked %r != manifest %r"
                        % (config.get("build_id"),
                           manifest.get("build_id")))
    baked_services = config.get("services")
    manifest_services = manifest.get("selection")
    if not isinstance(baked_services, list) or \
            sorted(baked_services) != sorted(manifest_services or []):
        problems.append("services baked %r != manifest selection %r"
                        % (baked_services, manifest_services))
    baked_labels = config.get("labels")
    manifest_labels = manifest.get("labels")
    if not isinstance(baked_labels, list) or \
            sorted(baked_labels) != sorted(manifest_labels or []):
        problems.append("labels baked %r != manifest %r"
                        % (baked_labels, manifest_labels))
    if problems:
        raise DriverError("launcher config mismatch: %s"
                          % "; ".join(problems))
    return {"check": "launcher-config-equals-manifest",
            "build_id": config.get("build_id"),
            "services": sorted(baked_services),
            "labels": sorted(baked_labels)}


def run_apps_script(repo: str, plan_path: str, build_id: str,
                    extra_labels: list, out_dir: str,
                    tools_dir: str) -> None:
    """Rebuild launcher + Files APKs for this plan and build ID."""
    script = os.path.join(repo, "android", "build_apps.sh")
    command = ["sh", script, "--plan", plan_path, "--build-id", build_id,
               "--labels", json.dumps(extra_labels),
               "--out", out_dir, "--tools", tools_dir]
    _say("STAGE apk-build start: %s" % " ".join(command))
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        _say(line.rstrip("\n"))
    status = proc.wait()
    if status != 0:
        raise DriverError("android/build_apps.sh failed (exit %d)"
                          % status)
    _say("STAGE apk-build done")


def summarize_manifest(manifest: dict) -> dict:
    """Small display summary of a build manifest (no new fields)."""
    artifacts = {item.get("path"): item
                 for item in manifest.get("artifacts", [])
                 if isinstance(item, dict)}
    image = artifacts.get("system-stargate-files-assistant.img") or \
        artifacts.get("system-stargate-files.img") or \
        artifacts.get("system-stargate-assistant.img")
    gz = None
    for path, item in artifacts.items():
        if path and path.endswith(".img.gz"):
            gz = dict(item, path=path)
    payload = []
    for item in manifest.get("model_files") or []:
        payload.append({"name": item.get("name"),
                        "bytes": item.get("bytes"),
                        "sha256": item.get("sha256")})
    summary = {
        "build_id": manifest.get("build_id"),
        "selection": list(manifest.get("selection") or []),
        "image": dict(image) if image else None,
        "image_bytes": manifest.get("image_bytes"),
        "partition_limit_bytes": manifest.get(
            "partition_limit_bytes"),
        "gz": gz,
        "model_delivery": manifest.get("model_delivery"),
        "model_payload_path": manifest.get("model_payload_path"),
        "payload_files": payload,
        "apps": [{"package": item.get("package"),
                  "bytes": item.get("bytes"),
                  "sha256": item.get("sha256")}
                 for item in manifest.get("apps", [])],
    }
    if summary["image"] is not None and "path" not in summary["image"]:
        for path, item in artifacts.items():
            if item is image:
                summary["image"] = dict(item, path=path)
    return summary


def write_result(job_dir: str, result: dict):
    """Write result.json (always, success or failure)."""
    path = os.path.join(job_dir, "result.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")


def run_fresh_build(plan_path: str, job_dir: str, tools_dir: str,
                    expect_build_id: str, assistant_apk: str,
                    model_dir: str, repo: str, apk_builder=None,
                    image_builder=None) -> dict:
    """Run the three fresh-build stages. Returns the result dict.

    apk_builder and image_builder override the real steps for tests;
    both receive a dict and return None (raising DriverError to fail).
    """
    _say("STAGE setup start")
    _refuse_frozen(job_dir, "--job-dir")
    _refuse_frozen(tools_dir, "--tools-dir")
    plan, error = gate_module.load_plan_file(plan_path)
    if error is not None:
        raise DriverError(error)
    with open(plan_path, "rb") as handle:
        plan_sha = hashlib.sha256(handle.read()).hexdigest()
    build_id = build_id_for_plan(plan, plan_sha)
    if build_id != expect_build_id:
        raise DriverError("build ID %s != expected %s"
                          % (build_id, expect_build_id))
    ok, code, message = gate_module.gate_plan(plan)
    if not ok:
        raise DriverError("M4 gate refuses this plan (exit %d): %s"
                          % (code, message.strip()))
    services = list(plan["handoff"]["services"])
    extra_labels = [AFTER_BOOT_LABEL] if "assistant" in services else []
    _say("STAGE setup done: build %s services=%s" % (build_id, services))
    apk_dir = os.path.join(job_dir, "apks")
    if apk_builder is not None:
        _say("STAGE apk-build start (stub)")
        apk_builder({"repo": repo, "plan_path": plan_path,
                     "build_id": build_id, "extra_labels": extra_labels,
                     "out_dir": apk_dir, "tools_dir": tools_dir})
        _say("STAGE apk-build done")
    else:
        run_apps_script(repo, plan_path, build_id, extra_labels,
                        apk_dir, tools_dir)
    launcher_apk = os.path.join(apk_dir, "stargate-launcher.apk")
    files_apk = os.path.join(apk_dir, "stargate-files.apk")
    _say("STAGE image-build start")
    inputs = {"launcher_apk": launcher_apk, "files_apk": files_apk,
              "assistant_apk": assistant_apk, "model_dir": model_dir}
    if image_builder is not None:
        image_builder({"plan_path": plan_path, "job_dir": job_dir,
                       "inputs": inputs})
        exit_code, stdout_text, stderr_text = 0, "", ""
    else:
        exit_code, stdout_text, stderr_text = gate_module.run_build(
            plan_path, job_dir, inputs=inputs)
    if stdout_text:
        _say(stdout_text.rstrip("\n"))
    if stderr_text:
        _say(stderr_text.rstrip("\n"))
    if exit_code != 0:
        raise DriverError("image build failed (exit %d)" % exit_code)
    _say("STAGE image-build done")
    _say("STAGE checks start")
    manifest_path = os.path.join(job_dir, "build-manifest.json")
    try:
        with open(manifest_path, "rb") as handle:
            manifest = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise DriverError("cannot read new build-manifest.json: %s"
                          % exc)
    check = check_launcher_config(launcher_apk, manifest)
    _say("STAGE checks done: %s" % check["check"])
    return {"status": "ok", "build_id": build_id,
            "manifest": manifest,
            "summary": summarize_manifest(manifest),
            "checks": [check]}


def main(argv=None) -> int:
    """Driver entry point. Always writes result.json. Returns exit code."""
    parser = argparse.ArgumentParser(prog="stargate appbuild")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--tools-dir", required=True)
    parser.add_argument("--expect-build-id", required=True)
    parser.add_argument("--assistant-apk", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_fresh_build(
            args.plan, args.job_dir, args.tools_dir,
            args.expect_build_id, args.assistant_apk,
            args.model_dir, args.repo)
    except DriverError as exc:
        _say("STAGE failed: %s" % exc)
        try:
            write_result(args.job_dir,
                         {"status": "failed", "reason": str(exc)})
        except OSError as write_exc:
            _say("cannot write result.json: %s" % write_exc)
        return 8
    try:
        write_result(args.job_dir, result)
    except OSError as exc:
        _say("cannot write result.json: %s" % exc)
        return 8
    _say("RESULT ok: build %s" % result["build_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
