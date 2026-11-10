"""Build driver for the guided app: one phone package per plan.

For an accepted plan this makes the phone's one app with exactly the
chosen services baked in, checks the result against the plan, and
writes the package next to it:

    stargate-launcher.apk   the phone app
    build-manifest.json     what was built, with hashes
    INSTALL.txt             the exact install and undo steps

It prints STAGE lines as it goes (the app shows them) and always
writes result.json, on success or failure. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile

from . import offers as offers_module
from . import services as services_module
from . import takeover as takeover_module
from .build import hubapk
from .build.image import build_id_for_plan

SCHEMA_VERSION = "phone-package-v1"
MANIFEST_NAME = "build-manifest.json"
MODEL_BUNDLE_FILE = "model.json"


class DriverError(Exception):
    """The package could not be built; the message says why."""


def _say(text: str) -> None:
    print(text, flush=True)


def default_model_bundle() -> str:
    return os.environ.get("STARGATE_MODEL_BUNDLE") or os.path.join(
        hubapk.LOCAL_TOOLS, "model")


def load_model_bundle(folder: str):
    """A phone-ready model: (spec for the app, file paths) or None."""
    path = os.path.join(folder, MODEL_BUNDLE_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    files = []
    for key, sha_key in (("pte", "pte_sha256"),
                         ("tokenizer", "tokenizer_sha256")):
        file_path = os.path.join(folder, str(spec.get(key)))
        if not os.path.isfile(file_path):
            raise DriverError("model bundle is missing %s" % spec.get(key))
        if hubapk.sha256_file(file_path) != spec.get(sha_key):
            raise DriverError("model bundle file %s does not match its "
                              "recorded hash" % spec.get(key))
        files.append(file_path)
    return {key: spec[key] for key in (
        "name", "pte", "tokenizer", "pte_sha256",
        "tokenizer_sha256")}, files


def check_app_config(apk_path: str, services: list, build_id: str) -> dict:
    """The config inside the built app must equal the plan."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            config = json.loads(apk.read(
                "assets/" + hubapk.CONFIG_NAME).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as bad:
        raise DriverError("cannot read the app's config: %s" % bad)
    if config.get("build_id") != build_id:
        raise DriverError("app build ID %r != plan %r" % (
            config.get("build_id"), build_id))
    if sorted(config.get("services") or []) != sorted(services):
        raise DriverError("app services %r != plan %r" % (
            config.get("services"), services))
    return {"check": "app-config-equals-plan", "build_id": build_id,
            "services": sorted(services)}


def run_build(plan_path: str, job_dir: str, expect_build_id: str, *,
              model_bundle=None, runtime_dir=None,
              apk_builder=None) -> dict:
    """Build the package; returns the result dict for result.json."""
    _say("STAGE check-plan start")
    try:
        with open(plan_path, "rb") as handle:
            raw = handle.read()
        plan = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as bad:
        raise DriverError("cannot read the plan: %s" % bad)
    build_id = build_id_for_plan(plan, hashlib.sha256(raw).hexdigest())
    if build_id != expect_build_id:
        raise DriverError("build ID %s != expected %s"
                          % (build_id, expect_build_id))
    if plan.get("selection_result") == "refused":
        raise DriverError("the plan refuses this phone")
    services = list((plan.get("handoff") or {}).get("services") or [])
    if not services:
        raise DriverError("the plan has no services")
    unbuilt = [name for name in services
               if not services_module.is_built(name)]
    if unbuilt:
        raise DriverError("not ready to build yet: %s"
                          % ", ".join(unbuilt))
    route = offers_module.install_route(plan.get("strategies") or [])
    choice = (plan.get("fit") or {}).get("model_choice") or {}
    wants_local = "assistant" in services \
        and choice.get("status") == "chosen"
    notes = []
    local_model = None
    model_files = []
    if wants_local:
        bundle = load_model_bundle(model_bundle or default_model_bundle())
        runtime_dir = runtime_dir or os.path.join(
            hubapk.LOCAL_TOOLS, "model-runtime")
        if bundle is None or not os.path.isdir(runtime_dir):
            notes.append(
                "This build's assistant works in cloud mode only: no "
                "phone-ready model bundle was found on this computer.")
        else:
            local_model, model_files = bundle
    elif "assistant" in services:
        notes.append("The assistant works through your own cloud key: "
                     "this phone is too small to run a model itself.")
    _say("STAGE check-plan done: build %s services=%s route=%s"
         % (build_id, ",".join(services), route))

    _say("STAGE app-build start")
    try:
        config = hubapk.hub_config(services, build_id,
                                   local_model=local_model)
        if apk_builder is not None:
            app = apk_builder(config, job_dir)
        else:
            app = hubapk.build(config, job_dir, runtime_dir=runtime_dir
                               if local_model else None)
    except hubapk.BuildError as failed:
        raise DriverError(str(failed))
    _say("STAGE app-build done: %d bytes" % app["bytes"])

    _say("STAGE package start")
    apk_name = os.path.basename(app["path"])
    install = takeover_module.install_steps(apk_name, model_files)
    undo = takeover_module.undo_steps()
    steps = (
        "INSTALL (keeps the phone's own Android; nothing is erased)\n"
        "Run these in the build folder, with the phone on the cable and\n"
        "USB debugging allowed. Replace SERIAL with the phone's serial\n"
        "number from `adb devices`.\n\n"
        + takeover_module.steps_text(install)
        + "\nThen open http://127.0.0.1:%d on this computer and enter "
          "the code shown on the phone (open Files on its screen).\n"
          % takeover_module.PORT
        + "\nUNDO (puts the phone back as it was)\n\n"
        + takeover_module.steps_text(undo))
    if route in ("image", "image_or_takeover"):
        steps += (
            "\nCLEAN SYSTEM\nThis phone may also take a full clean "
            "system (its own Android replaced). That needs a base "
            "system image made for this exact phone model; with one, "
            "run:\n  python3 -m stargate.build.cleanimage --base "
            "BASE.img --app %s --out clean-system.img\nThe install "
            "above works today without it.\n" % apk_name)
    with open(os.path.join(job_dir, "INSTALL.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(steps)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "plan_sha256": hashlib.sha256(raw).hexdigest(),
        "selection": services,
        "route": route,
        "assistant_mode": None if "assistant" not in services
        else ("on_phone" if local_model else "cloud_only"),
        "apps": [{"package": app["package"], "path": apk_name,
                  "bytes": app["bytes"], "sha256": app["sha256"]}],
        "model_files": [
            {"name": os.path.basename(path),
             "bytes": os.path.getsize(path),
             "sha256": hubapk.sha256_file(path)}
            for path in model_files],
        "notes": notes,
    }
    with open(os.path.join(job_dir, MANIFEST_NAME), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _say("STAGE package done")

    _say("STAGE checks start")
    check = check_app_config(app["path"], services, build_id)
    _say("STAGE checks done: %s" % check["check"])
    summary = {
        "kind": "phone_package",
        "build_id": build_id,
        "selection": services,
        "route": route,
        "route_text": offers_module.ROUTE_TEXT[route],
        "app": manifest["apps"][0],
        "assistant_mode": manifest["assistant_mode"],
        "folder": job_dir,
        "steps": steps,
        "notes": notes,
        "image": None,
    }
    return {"status": "ok", "build_id": build_id, "manifest": manifest,
            "summary": summary, "checks": [check]}


def write_result(job_dir: str, result: dict) -> None:
    with open(os.path.join(job_dir, "result.json"), "w",
              encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m stargate.phonebuild")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--expect-build-id", required=True)
    parser.add_argument("--model-bundle")
    parser.add_argument("--runtime")
    args = parser.parse_args(argv)
    try:
        result = run_build(args.plan, args.job_dir, args.expect_build_id,
                           model_bundle=args.model_bundle,
                           runtime_dir=args.runtime)
    except (DriverError, OSError) as failed:
        _say("STAGE failed: %s" % failed)
        try:
            write_result(args.job_dir,
                         {"status": "failed", "reason": str(failed)})
        except OSError:
            pass
        return 8
    write_result(args.job_dir, result)
    _say("RESULT ok: build %s" % result["build_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
