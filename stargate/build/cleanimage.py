"""Make a clean system image: a base Android stripped to the one app.

Takes a base system image for one exact phone model (a raw ext4
system filesystem), removes the apps nobody needs on a phone that
only runs services, and adds the phone app as the home screen:

    python3 -m stargate.build.cleanimage --base BASE.img \\
        --app stargate-launcher.apk --out clean-system.img

The base image is never changed: the work happens on a copy. Only
apps on the removal list below are taken out (browser, camera,
gallery, music, the stock home screens and so on); everything the
system needs to start is left alone, and the report lists what was
removed and what stayed. `--dry-run` only prints that report.

Limits, said plainly: this edits ext4 images. Images in the sparse,
EROFS or "shared blocks" formats are refused with the reason, and
flashing the result is a separate, per-phone step that this tool
never does. Needs debugfs, e2fsck and dumpe2fs (e2fsprogs).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
import tempfile

from . import image as fs

# Where Android keeps preinstalled apps (system-as-root layout and the
# older layout where the image root is /system itself).
APP_DIRS = (
    "/system/app", "/system/priv-app",
    "/system/product/app", "/system/product/priv-app",
    "/system/system_ext/app", "/system/system_ext/priv-app",
    "/app", "/priv-app",
)

# Apps a services-only phone never needs. Matched on the app's folder
# name. Nothing the system needs to start is on this list: providers,
# the system screens, the keyboard, permissions, networking and the
# web view all stay.
REMOVE = frozenset({
    # home screens (the phone app becomes the only one)
    "Launcher3", "Launcher3QuickStep", "Launcher3Go",
    "Launcher3QuickStepGo", "Trebuchet", "TrebuchetQuickStep",
    "Launcher2", "Home", "QuickSearchBox",
    # everyday apps
    "Browser2", "Jelly", "Calendar", "Etar", "Camera2", "Snap",
    "Aperture", "Contacts", "DeskClock", "Dialer", "Email", "Exchange2",
    "Gallery2", "Glimpse", "Music", "Eleven", "Twelve", "MusicFX",
    "AudioFX", "messaging", "Messaging", "ExactCalculator",
    "Calculator", "SoundRecorder", "Recorder", "HTMLViewer",
    "BookmarkProvider", "PartnerBookmarksProvider", "Stk",
    # wallpapers, themes, screensavers, extras
    "LiveWallpapersPicker", "WallpaperPicker", "WallpaperPicker2",
    "WallpaperCropper", "ThemePicker", "Backgrounds", "BasicDreams",
    "PhotoTable", "EasterEgg", "Galaxy4", "HoloSpiralWallpaper",
    "LiveWallpapers", "MagicSmokeWallpapers", "NoiseField",
    "PhaseBeam", "VisualizationWallpapers",
    # printing and developer tools
    "PrintSpooler", "BuiltInPrintService",
    "PrintRecommendationService", "Traceur", "DevCamera",
    "SampleLocationAttribution",
})

APP_FS_DIR = "priv-app/PhoneHome"
APP_FS_NAME = "PhoneHome.apk"
PERMISSIONS_NAME = "privapp-permissions-phonehome.xml"
PERMISSIONS_XML = """<?xml version="1.0" encoding="utf-8"?>
<!-- Lets the phone's one app restart the phone from its Status screen. -->
<permissions>
    <privapp-permissions package="com.stargate.launcher">
        <permission name="android.permission.REBOOT"/>
    </privapp-permissions>
</permissions>
"""

EXT4_MAGIC_OFFSET = 0x438
SPARSE_MAGIC = 0xED26FF3A
EROFS_MAGIC = 0xE0F5E1E2


class CleanError(Exception):
    """The image could not be cleaned; the message says why."""


def check_format(path: str) -> None:
    """Refuse anything that is not a plain, writable ext4 image."""
    with open(path, "rb") as handle:
        head = handle.read(2048)
    if len(head) < 2048:
        raise CleanError("that file is too small to be a system image")
    if struct.unpack_from("<I", head, 0)[0] == SPARSE_MAGIC:
        raise CleanError(
            "this is a sparse image: turn it into a plain one first "
            "(simg2img BASE.img plain.img)")
    if struct.unpack_from("<I", head, 1024)[0] == EROFS_MAGIC:
        raise CleanError(
            "this image uses the read-only EROFS format, which cannot "
            "be edited in place; it needs an ext4 base image")
    if struct.unpack_from("<H", head, EXT4_MAGIC_OFFSET)[0] != 0xEF53:
        raise CleanError("this is not an ext4 system image")
    code, out, err = fs._run(["dumpe2fs", "-h", path])
    if code != 0:
        raise CleanError("cannot read the image: %s" % (out + err)[-300:])
    for line in out.splitlines():
        if line.startswith("Filesystem features:") \
                and "shared_blocks" in line:
            raise CleanError(
                "this image shares blocks between files and cannot be "
                "edited in place; unshare it first "
                "(e2fsck -E unshare_blocks) on a grown copy")


def system_root(image: str) -> str:
    """'/system' for system-as-root images, '' when the root is /system."""
    if fs.fs_exists(image, "/system/build.prop"):
        return "/system"
    if fs.fs_exists(image, "/build.prop"):
        return ""
    raise CleanError("no build.prop found: this does not look like an "
                     "Android system image")


def survey(image: str) -> dict:
    """Which preinstalled apps would go and which would stay."""
    remove = []
    keep = []
    for folder in APP_DIRS:
        for name in fs.fs_list(image, folder):
            entry = folder + "/" + name
            (remove if name in REMOVE else keep).append(entry)
    return {"remove": sorted(remove), "keep": sorted(keep)}


def _remove_tree(image: str, entry: str) -> int:
    """Remove one app folder; returns how many files went."""
    found = fs.fs_walk(image, entry)
    files = [path for path, is_dir in found if not is_dir]
    dirs = sorted((path for path, is_dir in found if is_dir),
                  key=len, reverse=True)
    for path in files:
        fs.debugfs(image, "rm %s" % path, write=True)
    for path in dirs + [entry]:
        fs.debugfs(image, "rmdir %s" % path, write=True)
    if fs.fs_exists(image, entry):
        raise CleanError("could not remove %s" % entry)
    return len(files)


def _label(image: str, entry: str) -> None:
    """Give an entry the system-file security label, NUL included."""
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(fs.SELINUX_LABEL + b"\0")
        value = handle.name
    try:
        fs.debugfs(image, "ea_set -f %s %s security.selinux"
                   % (value, entry), write=True)
    finally:
        os.unlink(value)
    got = fs.debugfs(image, "ea_get %s security.selinux" % entry)
    if "(%d)" % (len(fs.SELINUX_LABEL) + 1) not in got \
            or "system_file" not in got:
        raise CleanError("could not label %s" % entry)


def _add_dir(image: str, entry: str) -> None:
    if fs.fs_exists(image, entry):
        raise CleanError("refusing to replace %s" % entry)
    fs.debugfs(image, "mkdir %s" % entry, write=True)
    fs.debugfs(image, "sif %s mode 040755" % entry, write=True)
    for field in ("uid", "gid"):
        fs.debugfs(image, "sif %s %s 0" % (entry, field), write=True)
    _label(image, entry)


def _add_file(image: str, host_path: str, entry: str) -> None:
    if fs.fs_exists(image, entry):
        raise CleanError("refusing to replace %s" % entry)
    fs.debugfs(image, "write %s %s" % (host_path, entry), write=True)
    fs.debugfs(image, "sif %s mode 0100644" % entry, write=True)
    for field in ("uid", "gid"):
        fs.debugfs(image, "sif %s %s 0" % (entry, field), write=True)
    _label(image, entry)
    back = host_path + ".back"
    try:
        fs.fs_dump(image, entry, back)
        if fs.sha256_file(back) != fs.sha256_file(host_path):
            raise CleanError("%s did not arrive intact" % entry)
    finally:
        if os.path.exists(back):
            os.unlink(back)


def _free_bytes(image: str) -> int:
    blocks, _inodes, block_size = fs.fs_free(image)
    return blocks * block_size


def clean(base: str, app_apk: str, out: str, *, dry_run=False,
          say=None) -> dict:
    """Write the clean image to `out`; returns the report."""
    say = say or (lambda line: None)
    try:
        fs.require_tools()
        check_format(base)
        root = system_root(base)
        plan = survey(base)
        report = {"base": os.path.basename(base), "removed": plan["remove"],
                  "kept": plan["keep"], "added": [], "dry_run": dry_run}
        if dry_run:
            return report
        if not os.path.isfile(app_apk):
            raise CleanError("phone app not found: %s" % app_apk)
        if os.path.exists(out):
            raise CleanError("refusing to overwrite %s" % out)
        say("Copying the base image (the original is not changed).")
        shutil.copyfile(base, out)
        try:
            before = _free_bytes(out)
            files = 0
            for entry in plan["remove"]:
                say("Removing %s" % entry)
                files += _remove_tree(out, entry)
            # debugfs leaves the free-space maps stale after removals.
            fs.e2fsck_fix(out)
            app_dir = "%s/%s" % (root, APP_FS_DIR)
            perm_dir = "%s/etc/permissions" % root
            say("Adding the phone app as the home screen.")
            _add_dir(out, app_dir)
            staged = os.path.join(tempfile.mkdtemp(prefix="clean-"),
                                  APP_FS_NAME)
            shutil.copyfile(app_apk, staged)
            try:
                _add_file(out, staged, app_dir + "/" + APP_FS_NAME)
            finally:
                shutil.rmtree(os.path.dirname(staged), True)
            if not fs.fs_exists(out, perm_dir):
                raise CleanError("the image has no %s folder" % perm_dir)
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".xml", delete=False,
                    encoding="utf-8") as handle:
                handle.write(PERMISSIONS_XML)
                xml_path = handle.name
            try:
                _add_file(out, xml_path,
                          perm_dir + "/" + PERMISSIONS_NAME)
            finally:
                os.unlink(xml_path)
            fs.e2fsck_fix(out)
            fs.e2fsck_clean(out)
            report["added"] = [app_dir + "/" + APP_FS_NAME,
                               perm_dir + "/" + PERMISSIONS_NAME]
            report["files_removed"] = files
            report["bytes_freed"] = _free_bytes(out) - before
            report["sha256"] = fs.sha256_file(out)
            report["bytes"] = os.path.getsize(out)
        except Exception:
            if os.path.exists(out):
                os.unlink(out)
            raise
        return report
    except fs.BuildError as failed:
        raise CleanError(str(failed))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m stargate.build.cleanimage",
        description="Strip a base system image down to the one app.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--app")
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true",
                        help="only list what would be removed and kept")
    args = parser.parse_args(argv)
    if not args.dry_run and not (args.app and args.out):
        parser.error("--app and --out are required (or use --dry-run)")
    try:
        report = clean(args.base, args.app, args.out,
                       dry_run=args.dry_run, say=print)
    except (CleanError, OSError) as failed:
        sys.stderr.write("error: %s\n" % failed)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
