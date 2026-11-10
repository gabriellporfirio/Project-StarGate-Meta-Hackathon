"""The clean-image builder, run on a small made-up ext4 image.

No real Android image is needed (or available) here: the test makes a
tiny ext4 filesystem laid out like a system image, cleans it, and
reads the result back with debugfs.
"""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from stargate.build import cleanimage
from stargate.build import image as fs

TOOLS = all(shutil.which(name) for name in
            ("mke2fs", "debugfs", "e2fsck", "dumpe2fs"))


def make_base(folder: str) -> str:
    tree = os.path.join(folder, "tree")
    for app in ("app/Browser2", "app/Camera2", "priv-app/Launcher3",
                "priv-app/Settings", "priv-app/SystemUI",
                "app/webview", "product/app/Gallery2"):
        path = os.path.join(tree, "system", app)
        os.makedirs(os.path.join(path, "oat", "arm64"))
        name = os.path.basename(app)
        with open(os.path.join(path, name + ".apk"), "wb") as handle:
            handle.write(os.urandom(40000))
        with open(os.path.join(path, "oat", "arm64", name + ".odex"),
                  "wb") as handle:
            handle.write(os.urandom(9000))
    os.makedirs(os.path.join(tree, "system", "etc", "permissions"))
    with open(os.path.join(tree, "system", "build.prop"), "w") as handle:
        handle.write("ro.build.version.sdk=34\n")
    base = os.path.join(folder, "base.img")
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-I", "256",
                    "-O", "^has_journal", "-d", tree, base, "8M"],
                   check=True, capture_output=True)
    return base


@unittest.skipUnless(TOOLS, "e2fsprogs is not on this computer")
class CleanImage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cleanimage-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.base = make_base(self.dir)
        self.apk = os.path.join(self.dir, "app.apk")
        with open(self.apk, "wb") as handle:
            handle.write(os.urandom(120000))

    def test_dry_run_lists_and_changes_nothing(self):
        before = fs.sha256_file(self.base)
        report = cleanimage.clean(self.base, None, None, dry_run=True)
        self.assertEqual(report["removed"], [
            "/system/app/Browser2", "/system/app/Camera2",
            "/system/priv-app/Launcher3", "/system/product/app/Gallery2"])
        self.assertEqual(report["kept"], [
            "/system/app/webview", "/system/priv-app/Settings",
            "/system/priv-app/SystemUI"])
        self.assertEqual(fs.sha256_file(self.base), before)

    def test_clean_removes_bloat_adds_the_app_and_stays_healthy(self):
        before = fs.sha256_file(self.base)
        out = os.path.join(self.dir, "clean.img")
        report = cleanimage.clean(self.base, self.apk, out)
        self.assertEqual(fs.sha256_file(self.base), before)
        for gone in ("/system/app/Browser2", "/system/app/Camera2",
                     "/system/priv-app/Launcher3",
                     "/system/product/app/Gallery2"):
            self.assertFalse(fs.fs_exists(out, gone), gone)
        for kept in ("/system/app/webview/webview.apk",
                     "/system/priv-app/Settings/Settings.apk",
                     "/system/priv-app/SystemUI/SystemUI.apk",
                     "/system/build.prop"):
            self.assertTrue(fs.fs_exists(out, kept), kept)
        dumped = os.path.join(self.dir, "back.apk")
        fs.fs_dump(out, "/system/priv-app/PhoneHome/PhoneHome.apk", dumped)
        self.assertEqual(fs.sha256_file(dumped), fs.sha256_file(self.apk))
        xml = os.path.join(self.dir, "back.xml")
        fs.fs_dump(out, "/system/etc/permissions/"
                   + cleanimage.PERMISSIONS_NAME, xml)
        with open(xml, encoding="utf-8") as handle:
            self.assertIn("android.permission.REBOOT", handle.read())
        fs.e2fsck_clean(out)
        self.assertEqual(report["files_removed"], 8)
        self.assertGreater(report["bytes_freed"], 0)
        self.assertEqual(os.path.getsize(out), os.path.getsize(self.base))

    def test_existing_output_is_never_overwritten(self):
        out = os.path.join(self.dir, "clean.img")
        with open(out, "w") as handle:
            handle.write("mine")
        with self.assertRaises(cleanimage.CleanError):
            cleanimage.clean(self.base, self.apk, out)
        with open(out) as handle:
            self.assertEqual(handle.read(), "mine")

    def test_other_formats_are_refused_with_the_reason(self):
        sparse = os.path.join(self.dir, "sparse.img")
        with open(sparse, "wb") as handle:
            handle.write(struct.pack("<I", cleanimage.SPARSE_MAGIC)
                         + b"\0" * 4096)
        with self.assertRaises(cleanimage.CleanError) as caught:
            cleanimage.clean(sparse, self.apk, sparse + ".out")
        self.assertIn("sparse", str(caught.exception))
        erofs = os.path.join(self.dir, "erofs.img")
        with open(erofs, "wb") as handle:
            handle.write(b"\0" * 1024
                         + struct.pack("<I", cleanimage.EROFS_MAGIC)
                         + b"\0" * 4096)
        with self.assertRaises(cleanimage.CleanError) as caught:
            cleanimage.clean(erofs, self.apk, erofs + ".out")
        self.assertIn("EROFS", str(caught.exception))
        junk = os.path.join(self.dir, "junk.img")
        with open(junk, "wb") as handle:
            handle.write(b"x" * 8192)
        with self.assertRaises(cleanimage.CleanError):
            cleanimage.clean(junk, self.apk, junk + ".out")


class RemovalList(unittest.TestCase):
    def test_nothing_needed_to_start_is_on_the_list(self):
        for needed in ("Settings", "SystemUI", "SettingsProvider",
                       "Shell", "PackageInstaller", "PermissionController",
                       "webview", "WebViewGoogle", "LatinIME",
                       "NetworkStack", "TelephonyProvider", "Telecom",
                       "ContactsProvider", "MediaProvider", "Provision",
                       "ExtServices", "DocumentsUI", "framework-res"):
            self.assertNotIn(needed, cleanimage.REMOVE)


if __name__ == "__main__":
    unittest.main()
