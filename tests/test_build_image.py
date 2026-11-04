"""Offline tests for Route A image packaging (M4 Parts 2 and 3).

Real images are too big for unit tests, so these build small
synthetic ext4 filesystems (mkfs.ext4) wrapped in a fake stock image
and run the same code: fs writes with the SELinux NUL fix, splicing,
fit verdicts, full Route A builds (in_image and the section 4d
after_boot payload), the Assistant lib folder, and the image
contents checks against a stock package baseline.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stargate.build import checks, image, manifest
from tests.test_build_checks import make_apk

REPO = Path(__file__).resolve().parent.parent
LOCK_PATH = "/workspaces/.android-build.lock"

BUILD_PROP_TEXT = b"stock build prop for tests\n"

FILES_PLAN = {
    "selection_result": "fits",
    "handoff": {
        "services": ["files"],
        "unresolved": [{"area": "image_size", "detail": "test"}],
    },
}
ASSISTANT_PLAN = {
    "selection_result": "fits",
    "handoff": {
        "services": ["files", "assistant"],
        "unresolved": [{"area": "image_size", "detail": "test"}],
    },
}

FILES_PLAN_SHA = hashlib.sha256(b"files-plan-bytes").hexdigest()
ASSISTANT_PLAN_SHA = hashlib.sha256(
    b"assistant-plan-bytes").hexdigest()
ASSISTANT_PLAN_AB_SHA = hashlib.sha256(
    b"assistant-after-boot-plan-bytes").hexdigest()

# After-boot model: bigger than the whole synthetic product region
# (16 MB), so the build must take the section 4d path, but small in
# absolute terms so the payload copy stays fast.
BIG_MODEL_BYTES = 20 << 20


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _have_tools(*names) -> bool:
    return all(shutil.which(name) is not None for name in names)


class RouteAPure(unittest.TestCase):
    def test_build_id_deterministic_and_distinct(self):
        first = image.build_id_for_plan(FILES_PLAN, FILES_PLAN_SHA)
        again = image.build_id_for_plan(FILES_PLAN, FILES_PLAN_SHA)
        self.assertEqual(first, again)
        self.assertTrue(first.startswith("m4a-files-"))
        other = image.build_id_for_plan(ASSISTANT_PLAN,
                                        ASSISTANT_PLAN_SHA)
        self.assertNotEqual(first, other)
        reordered = {"selection_result": "fits",
                     "handoff": dict(ASSISTANT_PLAN["handoff"])}
        reordered["handoff"]["services"] = ["assistant", "files"]
        self.assertEqual(
            image.build_id_for_plan(reordered, ASSISTANT_PLAN_SHA),
            other)

    def test_image_names(self):
        self.assertEqual(image.image_name_for_selection(["files"]),
                         "system-stargate-files.img")
        self.assertEqual(
            image.image_name_for_selection(["assistant"]),
            "system-stargate-assistant.img")
        self.assertEqual(
            image.image_name_for_selection(["files", "assistant"]),
            "system-stargate-files-assistant.img")
        self.assertEqual(
            image.image_name_for_selection(["assistant", "files"]),
            "system-stargate-files-assistant.img")
        with self.assertRaises(image.BuildError):
            image.image_name_for_selection(["vpn"])

    def test_fit_numbers(self):
        self.assertIsNone(image.fit_numbers(100, 200, 50, 10, 5))
        over_total = image.fit_numbers(100, 200, 250, 10, 5)
        self.assertIn("in total", over_total)
        over_free = image.fit_numbers(100, 200, 150, 10, 5)
        self.assertIn("free", over_free)
        over_inodes = image.fit_numbers(100, 200, 50, 4, 5)
        self.assertIn("inodes", over_inodes)

    def test_model_targets_from_handover(self):
        with open(REPO / "assistant-manifest.json",
                  encoding="utf-8") as handle:
            handover = json.load(handle)
        fs_dir, targets = image._model_targets(handover)
        self.assertEqual(fs_dir, "/stargate/model")
        self.assertEqual(
            targets["Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte"],
            handover["target_path_route_a"])
        self.assertEqual(targets["tokenizer.model"],
                         handover["tokenizer_path_route_a"])

    def test_model_targets_missing_route_a(self):
        with self.assertRaises(image.BuildError):
            image._model_targets({"files": []})

    def test_model_targets_route_b_from_handover(self):
        with open(REPO / "assistant-manifest.json",
                  encoding="utf-8") as handle:
            handover = json.load(handle)
        targets = image._model_targets_route_b(handover)
        self.assertEqual(
            targets["Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte"],
            handover["target_path_route_b"])
        self.assertEqual(targets["tokenizer.model"],
                         handover["tokenizer_path_route_b"])
        for target in targets.values():
            self.assertTrue(target.startswith("/sdcard/"))

    def test_model_targets_route_b_missing(self):
        with self.assertRaises(image.BuildError):
            image._model_targets_route_b({"files": []})
        with self.assertRaises(image.BuildError):
            image._model_targets_route_b(
                {"files": [],
                 "target_path_route_b": "/product/stargate/m.pte"})

    def test_require_tools_needs_dumpe2fs(self):
        real_which = shutil.which
        with mock.patch.object(
                image.shutil, "which",
                side_effect=lambda name: None
                if name == "dumpe2fs" else real_which(name)):
            with self.assertRaises(image.BuildError) as raised:
                image.require_tools()
        self.assertIn("dumpe2fs", str(raised.exception))

    def test_gzip_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.bin")
            with open(src, "wb") as handle:
                handle.write(b"abc" * 1000)
            first = os.path.join(tmp, "a.gz")
            second = os.path.join(tmp, "b.gz")
            image.gzip_file(src, first)
            image.gzip_file(src, second)
            with open(first, "rb") as handle:
                data_a = handle.read()
            with open(second, "rb") as handle:
                data_b = handle.read()
        self.assertEqual(data_a, data_b)


class RouteAImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _have_tools("debugfs", "e2fsck", "mkfs.ext4",
                           "dumpe2fs"):
            raise unittest.SkipTest("need e2fsprogs on PATH")
        if not os.path.isfile(checks.AAPT2):
            raise unittest.SkipTest("need read-only aapt2")
        cls._lock_handle = open(LOCK_PATH, "w")
        try:
            fcntl.flock(cls._lock_handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise unittest.SkipTest("android build lock is busy")
        cls._tmp = tempfile.TemporaryDirectory(
            prefix="routea-test-")
        root = cls._tmp.name
        cls.root = root
        cls.regions, cls.stock, cls.loose = cls._make_stock(root)
        cls.stock_sha = image.sha256_file(cls.stock)
        cls.stock_size = os.path.getsize(cls.stock)
        cls.launcher_files = os.path.join(root, "launcher.apk")
        cls._bake_launcher(cls.launcher_files, FILES_PLAN,
                           FILES_PLAN_SHA, [])
        cls.launcher_assist = os.path.join(
            root, "launcher-assist.apk")
        cls._bake_launcher(cls.launcher_assist, ASSISTANT_PLAN,
                           ASSISTANT_PLAN_SHA, [])
        cls.files_apk = os.path.join(root, "files.apk")
        make_apk("com.stargate.files", cls.files_apk)
        cls.assist_apk = os.path.join(root, "assistant.apk")
        make_apk("com.stargate.assistant", cls.assist_apk)
        cls.assist_apk_alt = os.path.join(
            root, "assistant-alt.apk")
        make_apk("com.example.test.assistant", cls.assist_apk_alt)
        cls.model_dir = os.path.join(root, "models")
        os.makedirs(cls.model_dir)
        cls.model_data = {
            "tiny.pte": b"tiny-model-bytes-01",
            "tokenizer.model": b"tiny-tokenizer-02",
        }
        for name, data in cls.model_data.items():
            with open(os.path.join(cls.model_dir, name),
                      "wb") as handle:
                handle.write(data)
        cls.big_name = "big.pte"
        big_path = os.path.join(cls.model_dir, cls.big_name)
        with open(big_path, "wb") as handle:
            handle.seek(BIG_MODEL_BYTES - 1)
            handle.write(b"\x00")
        cls.big_sha = image.sha256_file(big_path)
        cls.ab_plan = {
            "selection_result": "fits",
            "handoff": {
                "services": ["files", "assistant"],
                "model": {
                    "id": "tiny-test",
                    "revision": "rev1",
                    "files": [
                        {"name": cls.big_name,
                         "bytes": BIG_MODEL_BYTES},
                        {"name": "tokenizer.model",
                         "bytes": len(
                             cls.model_data["tokenizer.model"])},
                    ],
                },
                "unresolved": [{"area": "image_size",
                                "detail": "test"}],
            },
        }
        cls.launcher_after_boot = os.path.join(
            root, "launcher-after-boot.apk")
        cls._bake_launcher(cls.launcher_after_boot, cls.ab_plan,
                           ASSISTANT_PLAN_AB_SHA,
                           [image.AFTER_BOOT_LABEL])
        cls.assist_libs_apk = os.path.join(
            root, "assistant-libs.apk")
        make_apk("com.stargate.assistant", cls.assist_libs_apk,
                 libs={("arm64-v8a", "libtest.so"): b"so-bytes-arm64",
                       ("x86_64", "libtest.so"): b"so-bytes-x86-64"})
        with tempfile.TemporaryDirectory(
                prefix="baseline-") as work:
            packages, _dumps, _fs = image.list_image_packages(
                cls.stock, cls.regions, work, checks.AAPT2)
            cls.stock_packages = packages

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        fcntl.flock(cls._lock_handle.fileno(), fcntl.LOCK_UN)
        cls._lock_handle.close()

    @classmethod
    def _bake_launcher(cls, out, plan, plan_sha, labels):
        build_id = image.build_id_for_plan(plan, plan_sha)
        config = checks.launcher_config(plan, build_id, labels)
        make_apk("com.stargate.launcher", out, assets={
            "stargate-build.json":
                (json.dumps(config, indent=2, sort_keys=True)
                 + "\n").encode("utf-8")})

    @classmethod
    def _mkfs(cls, path, size_mb):
        proc = subprocess.run(
            ["mkfs.ext4", "-F", "-q", "-b", "4096", "-I", "256",
             "-O", "^has_journal,^metadata_csum,^64bit",
             path, "%dM" % size_mb],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise unittest.SkipTest(
                "mkfs.ext4 cannot run here: %s" % proc.stderr)

    @classmethod
    def _make_stock(cls, root, with_launcher=True):
        work = os.path.join(root, "stock-src")
        os.makedirs(work)
        sys_fs = os.path.join(work, "sys.ext4")
        ext_fs = os.path.join(work, "ext.ext4")
        prod_fs = os.path.join(work, "prod.ext4")
        cls._mkfs(sys_fs, 32)
        cls._mkfs(ext_fs, 16)
        cls._mkfs(prod_fs, 16)
        build_prop_host = os.path.join(work, "build.prop")
        with open(build_prop_host, "wb") as handle:
            handle.write(BUILD_PROP_TEXT)
        image.fs_mkdir(sys_fs, "/system")
        image.fs_write_host_file(sys_fs, build_prop_host,
                                 "/system/build.prop")
        # A duplicate stock package (like NetworkStack/
        # NetworkStackNext on the real image): the checks must
        # tolerate stock duplicates and only police ours.
        dup_apk = os.path.join(work, "dup.apk")
        make_apk("com.android.duptest", dup_apk)
        image.fs_mkdir(sys_fs, "/system/app")
        image.fs_mkdir(sys_fs, "/system/app/DupA")
        image.fs_write_host_file(sys_fs, dup_apk,
                                 "/system/app/DupA/DupA.apk")
        image.fs_mkdir(sys_fs, "/system/app/DupB")
        image.fs_write_host_file(sys_fs, dup_apk,
                                 "/system/app/DupB/DupB.apk")
        image.fs_mkdir(ext_fs, "/priv-app")
        if with_launcher:
            launcher_apk = os.path.join(work, "launcher3.apk")
            make_apk("com.android.launcher3", launcher_apk)
            image.fs_mkdir(ext_fs, "/priv-app/Launcher3QuickStep")
            image.fs_write_host_file(
                ext_fs, launcher_apk,
                "/priv-app/Launcher3QuickStep/"
                "Launcher3QuickStep.apk")
        header = 1 << 20
        sys_size = os.path.getsize(sys_fs)
        ext_size = os.path.getsize(ext_fs)
        prod_size = os.path.getsize(prod_fs)
        regions = {
            "system": (header, sys_size),
            "system_ext": (header + sys_size, ext_size),
            "product": (header + sys_size + ext_size, prod_size),
        }
        stock = os.path.join(work, "stock.img")
        with open(stock, "wb") as handle:
            handle.write(b"\x00" * header)
            for name in ("system", "system_ext", "product"):
                with open({"system": sys_fs, "system_ext": ext_fs,
                           "product": prod_fs}[name], "rb") as part:
                    shutil.copyfileobj(part, handle)
        loose = os.path.join(work, "loose-build.prop")
        shutil.copyfile(build_prop_host, loose)
        return regions, stock, loose

    def _manifest(self, package="com.stargate.assistant",
                  files=None, apk_path=None):
        if files is None:
            files = [{"name": name,
                      "bytes": len(self.model_data[name]),
                      "sha256": _sha(self.model_data[name])}
                     for name in ("tiny.pte", "tokenizer.model")]
        if apk_path is None:
            apk_path = self.assist_apk
        with open(apk_path, "rb") as handle:
            apk_sha = _sha(handle.read())
        pte = files[0]["name"]
        tok = files[1]["name"] if len(files) > 1 else files[0]["name"]
        return {
            "model_id": "tiny-test",
            "source_revision": "rev1",
            "files": files,
            "runtime_version": "test runtime 1.0",
            "assistant_apk": {"package": package,
                              "version": "1.0",
                              "sha256": apk_sha},
            "target_path_route_a":
                "/product/stargate/model/" + pte,
            "tokenizer_path_route_a":
                "/product/stargate/model/" + tok,
            "target_path_route_b":
                "/sdcard/Android/data/%s/files/model/%s"
                % (package, pte),
            "tokenizer_path_route_b":
                "/sdcard/Android/data/%s/files/model/%s"
                % (package, tok),
        }

    def _manifest_ab(self, files=None, **kwargs):
        if files is None:
            files = [{"name": self.big_name, "bytes": BIG_MODEL_BYTES,
                      "sha256": self.big_sha},
                     {"name": "tokenizer.model",
                      "bytes": len(
                          self.model_data["tokenizer.model"]),
                      "sha256": _sha(
                          self.model_data["tokenizer.model"])}]
        return self._manifest(files=files, **kwargs)

    def _ctx(self, plan_sha, assistant_manifest=None,
             launcher_apk=None, **overrides):
        ctx = {
            "plan_path": os.path.join(self.root, "plan.json"),
            "plan_sha256": plan_sha,
            "assistant_manifest": assistant_manifest,
            "assistant_manifest_sha256": "1" * 64,
            "stock_path": self.stock,
            "stock_size": self.stock_size,
            "stock_sha256": self.stock_sha,
            "loose_buildprop": self.loose,
            "regions": self.regions,
            "launcher_apk": launcher_apk or self.launcher_files,
            "files_apk": self.files_apk,
            "assistant_apk": self.assist_apk,
            "model_dir": self.model_dir,
            "partition_limit": self.stock_size,
        }
        ctx.update(overrides)
        return ctx

    def _out(self):
        tmp = tempfile.TemporaryDirectory(
            prefix="routea-out-", dir=self.root)
        self.addCleanup(tmp.cleanup)
        return os.path.join(tmp.name, "build")

    def test_fs_roundtrip(self):
        fs_file = os.path.join(self.root, "roundtrip.ext4")
        self._mkfs(fs_file, 8)
        payload = os.path.join(self.root, "payload.bin")
        with open(payload, "wb") as handle:
            handle.write(b"hello-fs")
        image.fs_mkdir(fs_file, "/mydir")
        image.fs_write_host_file(fs_file, payload,
                                 "/mydir/hello.txt")
        self.assertTrue(image.fs_exists(fs_file, "/mydir"))
        self.assertTrue(
            image.fs_exists(fs_file, "/mydir/hello.txt"))
        self.assertFalse(image.fs_exists(fs_file, "/nope"))
        self.assertEqual(image.fs_list(fs_file, "/"),
                         ["lost+found", "mydir"])
        self.assertEqual(image.fs_list(fs_file, "/missing"), [])
        back = os.path.join(self.root, "back.bin")
        image.fs_dump(fs_file, "/mydir/hello.txt", back)
        with open(back, "rb") as handle:
            self.assertEqual(handle.read(), b"hello-fs")
        out = image.debugfs(
            fs_file, "ea_get /mydir/hello.txt security.selinux")
        self.assertIn('(26) = "u:object_r:system_file:s0\\000"',
                      out)
        image.e2fsck_clean(fs_file)

    def test_fs_clobber_guards(self):
        fs_file = os.path.join(self.root, "guards.ext4")
        self._mkfs(fs_file, 8)
        payload = os.path.join(self.root, "guard.bin")
        with open(payload, "wb") as handle:
            handle.write(b"x")
        image.fs_mkdir(fs_file, "/d")
        with self.assertRaises(image.BuildError):
            image.fs_mkdir(fs_file, "/d")
        image.fs_write_host_file(fs_file, payload, "/d/f")
        with self.assertRaises(image.BuildError):
            image.fs_write_host_file(fs_file, payload, "/d/f")
        with self.assertRaises(image.BuildError):
            image.fs_remove(fs_file, "/d/absent")
        image.fs_remove(fs_file, "/d/f")
        self.assertFalse(image.fs_exists(fs_file, "/d/f"))
        # The real system_ext needs a repair after rm (debugfs
        # leaves bitmaps stale); the helper must accept a clean fs
        # too and keep it clean.
        image.e2fsck_fix(fs_file)
        image.e2fsck_clean(fs_file)

    def test_splice_confinement(self):
        size = 1 << 20
        stock = os.path.join(self.root, "splice-stock.img")
        with open(stock, "wb") as handle:
            handle.write(b"S" * size)
        region = os.path.join(self.root, "splice-region.bin")
        with open(region, "wb") as handle:
            handle.write(b"R" * 4096)
        out = os.path.join(self.root, "splice-out.img")
        image.splice_regions(stock, out, [(4096, region)])
        image.check_outside_untouched(
            stock, out, [(4096, 4096)])
        with open(out, "r+b") as handle:
            handle.seek(0)
            handle.write(b"X")
        with self.assertRaises(image.BuildError):
            image.check_outside_untouched(
                stock, out, [(4096, 4096)])

    def test_verify_stock_image(self):
        image.verify_stock_image(
            self.stock, self.stock_size, self.stock_sha)
        with self.assertRaises(image.BuildError):
            image.verify_stock_image("/nonexistent-stock.img")
        with self.assertRaises(image.BuildError):
            image.verify_stock_image(
                self.stock, self.stock_size + 1, self.stock_sha)
        with self.assertRaises(image.BuildError):
            image.verify_stock_image(
                self.stock, self.stock_size, "0" * 64)

    def test_fs_free_sane(self):
        with tempfile.TemporaryDirectory(
                prefix="free-", dir=self.root) as work_dir:
            fs_file = os.path.join(work_dir, "sys.ext4")
            offset, size = self.regions["system"]
            image.extract_region(self.stock, offset, size,
                                 fs_file)
            free_blocks, free_inodes, block_size = image.fs_free(
                fs_file)
        self.assertEqual(block_size, 4096)
        self.assertGreater(free_blocks, 0)
        self.assertGreater(free_inodes, 0)

    def test_build_files_only_success(self):
        out = self._out()
        ctx = self._ctx(FILES_PLAN_SHA)
        code, stdout, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        build_id = image.build_id_for_plan(
            FILES_PLAN, FILES_PLAN_SHA)
        self.assertIn("build_id=%s" % build_id, stdout)
        image_name = "system-stargate-files.img"
        image_path = os.path.join(out, image_name)
        self.assertTrue(os.path.isfile(image_path))
        self.assertTrue(os.path.isfile(image_path + ".gz"))
        manifest_path = os.path.join(out, "build-manifest.json")
        ok, errors = manifest.validate_file(manifest_path)
        self.assertTrue(ok, errors)
        with open(manifest_path, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["route"], "A")
        self.assertEqual(doc["build_id"], build_id)
        self.assertEqual(doc["selection"], ["files"])
        self.assertEqual(doc["image_bytes"], self.stock_size)
        self.assertEqual(doc["partition_limit_bytes"],
                         self.stock_size)
        self.assertEqual(
            [app["service"] for app in doc["apps"]],
            ["launcher", "files"])
        self.assertIsNone(doc["model_files"])
        self.assertEqual(doc["model_delivery"], "none")
        self.assertIsNone(doc["model_payload_path"])
        self.assertEqual(doc["labels"], [])
        self.assertFalse(os.path.exists(
            os.path.join(out, "model-payload")))
        self.assertEqual(
            manifest.verify_artifact_hashes(doc, out), [])
        expected_config = checks.launcher_config(
            FILES_PLAN, build_id, [])
        code, errors = image.run_image_checks(
            image_path, ["files"], None, None, expected_config,
            regions=self.regions, limit=self.stock_size,
            model_delivery="none",
            stock_packages=self.stock_packages)
        self.assertEqual((code, errors), (0, []))

    def test_build_assistant_success_small_model(self):
        out = self._out()
        package = "com.example.test.assistant"
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA,
            self._manifest(package, apk_path=self.assist_apk_alt),
            launcher_apk=self.launcher_assist,
            assistant_apk=self.assist_apk_alt)
        code, stdout, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("model_delivery=in_image", stdout)
        manifest_path = os.path.join(out, "build-manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["route"], "A")
        self.assertEqual(doc["model_delivery"], "in_image")
        self.assertIsNone(doc["model_payload_path"])
        self.assertEqual(doc["labels"], [])
        self.assertEqual(
            [item["target"] for item in doc["model_files"]],
            ["/product/stargate/model/tiny.pte",
             "/product/stargate/model/tokenizer.model"])
        self.assertFalse(os.path.exists(
            os.path.join(out, "model-payload")))
        self.assertEqual(doc["runtime_version"],
                         "test runtime 1.0")
        self.assertEqual(
            [app["package"] for app in doc["apps"]],
            ["com.stargate.launcher", "com.stargate.files",
             package])
        self.assertEqual(
            manifest.verify_artifact_hashes(doc, out), [])

    def test_build_assistant_after_boot_success(self):
        # The model exceeds the whole product partition, so the
        # build takes the section 4d path: the app in the image, the
        # model in a payload snapshot, product untouched.
        out = self._out()
        ctx = self._ctx(
            ASSISTANT_PLAN_AB_SHA, self._manifest_ab(),
            launcher_apk=self.launcher_after_boot)
        code, stdout, err = image.build_route_a(
            self.ab_plan, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("model_delivery=after_boot", stdout)
        self.assertIn("payload=%s" % os.path.join(
            out, "model-payload"), stdout)
        image_path = os.path.join(
            out, "system-stargate-files-assistant.img")
        self.assertTrue(os.path.isfile(image_path))
        manifest_path = os.path.join(out, "build-manifest.json")
        ok, errors = manifest.validate_file(manifest_path)
        self.assertTrue(ok, errors)
        with open(manifest_path, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["model_delivery"], "after_boot")
        self.assertEqual(doc["model_payload_path"], "model-payload")
        self.assertEqual(doc["labels"], [image.AFTER_BOOT_LABEL])
        self.assertEqual(
            [item["target"] for item in doc["model_files"]],
            ["/sdcard/Android/data/com.stargate.assistant/"
             "files/model/big.pte",
             "/sdcard/Android/data/com.stargate.assistant/"
             "files/model/tokenizer.model"])
        payload = os.path.join(out, "model-payload")
        for name in (self.big_name, "tokenizer.model"):
            full = os.path.join(payload, name)
            self.assertTrue(os.path.isfile(full))
            self.assertEqual(os.stat(full).st_mode & 0o777, 0o444)
        self.assertEqual(
            manifest.verify_artifact_hashes(doc, out), [])
        offset, size = self.regions["product"]
        with tempfile.TemporaryDirectory(
                prefix="prod-", dir=self.root) as work_dir:
            stock_prod = os.path.join(work_dir, "stock-prod.ext4")
            built_prod = os.path.join(work_dir, "built-prod.ext4")
            image.extract_region(self.stock, offset, size,
                                 stock_prod)
            image.extract_region(image_path, offset, size,
                                 built_prod)
            with open(stock_prod, "rb") as handle:
                stock_bytes = handle.read()
            with open(built_prod, "rb") as handle:
                built_bytes = handle.read()
        self.assertEqual(built_bytes, stock_bytes)

    def test_build_assistant_apk_sha_mismatch_is_8(self):
        out = self._out()
        doc = self._manifest()
        doc["assistant_apk"]["sha256"] = "0" * 64
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA, doc,
            launcher_apk=self.launcher_assist)
        code, _stdout, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("does not match its manifest hash", err)
        self.assertFalse(os.path.exists(
            os.path.join(
                out, "system-stargate-files-assistant.img")))
        self.assertFalse(os.path.exists(
            os.path.join(out, "build-manifest.json")))

    def test_build_bad_stock_is_8(self):
        out = self._out()
        ctx = self._ctx(FILES_PLAN_SHA, stock_sha256="0" * 64)
        code, _out, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("hash", err)
        self.assertFalse(os.path.exists(out))

    def test_build_missing_apk_is_8(self):
        out = self._out()
        ctx = self._ctx(
            FILES_PLAN_SHA,
            files_apk=os.path.join(self.root, "absent.apk"))
        code, _out, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("missing build input", err)
        self.assertFalse(os.path.exists(out))

    def test_build_wrong_apk_package_is_8(self):
        out = self._out()
        ctx = self._ctx(FILES_PLAN_SHA,
                        files_apk=self.assist_apk)
        code, _out, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("expected com.stargate.files", err)
        self.assertFalse(os.path.exists(out))

    def test_build_launcher_config_mismatch_is_8(self):
        out = self._out()
        ctx = self._ctx(ASSISTANT_PLAN_SHA,
                        self._manifest(),
                        launcher_apk=self.launcher_files)
        code, _out, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("not built for this plan", err)
        self.assertIn("build_apps.sh", err)
        self.assertFalse(os.path.exists(out))

    def test_build_model_size_mismatch_is_8(self):
        out = self._out()
        files = [{"name": "tiny.pte", "bytes": 5,
                  "sha256": _sha(b"tiny-model-bytes-01")},
                 {"name": "tokenizer.model",
                  "bytes": len(b"tiny-tokenizer-02"),
                  "sha256": _sha(b"tiny-tokenizer-02")}]
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA, self._manifest(files=files),
            launcher_apk=self.launcher_assist)
        code, _out, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("manifest says", err)
        self.assertFalse(os.path.exists(out))

    def test_build_model_hash_mismatch_is_8(self):
        out = self._out()
        files = [{"name": "tiny.pte",
                  "bytes": len(b"tiny-model-bytes-01"),
                  "sha256": "3" * 64},
                 {"name": "tokenizer.model",
                  "bytes": len(b"tiny-tokenizer-02"),
                  "sha256": _sha(b"tiny-tokenizer-02")}]
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA, self._manifest(files=files),
            launcher_apk=self.launcher_assist)
        code, _out, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("approved", err)
        self.assertFalse(os.path.exists(out))

    def test_build_after_boot_bind_mismatch_is_9(self):
        cases = {
            "name": lambda doc: doc["files"].__setitem__(
                0, dict(doc["files"][0], name="other.pte")),
            "size": lambda doc: doc["files"].__setitem__(
                0, dict(doc["files"][0],
                        bytes=doc["files"][0]["bytes"] + 1)),
            "model_id": lambda doc: doc.__setitem__(
                "model_id", "other-model"),
            "revision": lambda doc: doc.__setitem__(
                "source_revision", "other-rev"),
            "hash": lambda doc: doc["files"].__setitem__(
                0, dict(doc["files"][0], sha256="zz")),
        }
        for name, tweak in cases.items():
            with self.subTest(case=name):
                out = self._out()
                doc = self._manifest_ab()
                tweak(doc)
                ctx = self._ctx(
                    ASSISTANT_PLAN_AB_SHA, doc,
                    launcher_apk=self.launcher_after_boot)
                code, _stdout, err = image.build_route_a(
                    self.ab_plan, out, [], ctx)
                self.assertEqual(code, 9)
                self.assertTrue(err.startswith("error: "))
                self.assertFalse(os.path.exists(
                    os.path.join(
                        out, "system-stargate-files-assistant.img")))
                self.assertFalse(os.path.exists(
                    os.path.join(out, "build-manifest.json")))
                self.assertFalse(os.path.exists(
                    os.path.join(out, "model-payload")))

    def test_build_after_boot_source_size_mismatch_is_9(self):
        with tempfile.TemporaryDirectory(
                prefix="models-", dir=self.root) as model_dir:
            for name, data in self.model_data.items():
                with open(os.path.join(model_dir, name),
                          "wb") as handle:
                    handle.write(data)
            with open(os.path.join(model_dir, self.big_name),
                      "wb") as handle:
                handle.write(b"too short")
            out = self._out()
            ctx = self._ctx(
                ASSISTANT_PLAN_AB_SHA, self._manifest_ab(),
                launcher_apk=self.launcher_after_boot,
                model_dir=model_dir)
            code, _stdout, err = image.build_route_a(
                self.ab_plan, out, [], ctx)
        self.assertEqual(code, 9)
        self.assertIn("handoff says", err)
        self.assertFalse(os.path.exists(
            os.path.join(out, "build-manifest.json")))

    def test_build_after_boot_missing_model_is_8(self):
        with tempfile.TemporaryDirectory(
                prefix="models-", dir=self.root) as model_dir:
            out = self._out()
            ctx = self._ctx(
                ASSISTANT_PLAN_AB_SHA, self._manifest_ab(),
                launcher_apk=self.launcher_after_boot,
                model_dir=model_dir)
            code, _stdout, err = image.build_route_a(
                self.ab_plan, out, [], ctx)
            self.assertEqual(code, 8)
            self.assertIn("missing model file", err)
            self.assertFalse(os.path.exists(
                os.path.join(out, "build-manifest.json")))

    def test_build_after_boot_payload_hash_mismatch_is_8(self):
        out = self._out()
        doc = self._manifest_ab()
        doc["files"][0]["sha256"] = "3" * 64
        ctx = self._ctx(
            ASSISTANT_PLAN_AB_SHA, doc,
            launcher_apk=self.launcher_after_boot)
        code, _stdout, err = image.build_route_a(
            self.ab_plan, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("approved hash", err)
        self.assertFalse(os.path.exists(
            os.path.join(out, "build-manifest.json")))

    def test_build_out_not_clean_is_8(self):
        out = self._out()
        os.makedirs(out)
        with open(os.path.join(
                out, "system-stargate-files.img"), "wb") as handle:
            handle.write(b"old")
        ctx = self._ctx(FILES_PLAN_SHA)
        code, _out, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("fresh --out dir", err)

    def test_build_missing_stock_launcher_is_8(self):
        work = os.path.join(self.root, "no-launcher")
        os.makedirs(work)
        regions, stock, loose = self._make_stock(
            work, with_launcher=False)
        out = self._out()
        ctx = self._ctx(
            FILES_PLAN_SHA, stock_path=stock,
            stock_size=os.path.getsize(stock),
            stock_sha256=image.sha256_file(stock),
            loose_buildprop=loose, regions=regions,
            partition_limit=os.path.getsize(stock))
        code, _out, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual(code, 8)
        self.assertIn("stock launcher not found", err)

    def _built_files_image(self):
        out = self._out()
        ctx = self._ctx(FILES_PLAN_SHA)
        code, _stdout, err = image.build_route_a(
            FILES_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        build_id = image.build_id_for_plan(
            FILES_PLAN, FILES_PLAN_SHA)
        expected = checks.launcher_config(FILES_PLAN, build_id, [])
        return os.path.join(out, "system-stargate-files.img"), \
            expected

    def _built_assistant_image(self):
        out = self._out()
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA, self._manifest(),
            launcher_apk=self.launcher_assist)
        code, _stdout, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        build_id = image.build_id_for_plan(
            ASSISTANT_PLAN, ASSISTANT_PLAN_SHA)
        expected = checks.launcher_config(
            ASSISTANT_PLAN, build_id, [])
        return os.path.join(
            out, "system-stargate-files-assistant.img"), expected

    def test_checks_catch_unbuilt_stock_image(self):
        build_id = image.build_id_for_plan(
            FILES_PLAN, FILES_PLAN_SHA)
        expected = checks.launcher_config(FILES_PLAN, build_id, [])
        code, errors = image.run_image_checks(
            self.stock, ["files"], None, None, expected,
            regions=self.regions, limit=self.stock_size,
            model_delivery="none",
            stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("missing app" in item for item in errors))
        self.assertTrue(any("stock launcher still present" in item
                            for item in errors))

    def test_checks_missing_app_is_8(self):
        image_path, _expected = self._built_files_image()
        build_id = image.build_id_for_plan(
            ASSISTANT_PLAN, ASSISTANT_PLAN_SHA)
        expected = checks.launcher_config(
            ASSISTANT_PLAN, build_id, [])
        handover = self._manifest()
        code, errors = image.run_image_checks(
            image_path, ["files", "assistant"],
            handover["files"], "com.stargate.assistant",
            expected, regions=self.regions, limit=self.stock_size,
            model_delivery="in_image",
            assistant_apk_sha256=handover["assistant_apk"]["sha256"],
            stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("missing app" in item for item in errors))

    def test_checks_unselected_app_is_8(self):
        image_path, expected = self._built_files_image()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["system"]
            fs_file = os.path.join(work_dir, "sys.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            image.fs_mkdir(fs_file, "/system/app/Extra")
            image.fs_write_host_file(
                fs_file, self.assist_apk,
                "/system/app/Extra/Extra.apk")
            image.e2fsck_clean(fs_file)
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = image.run_image_checks(
                tampered, ["files"], None, None, expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="none",
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("unselected app" in item
                            for item in errors))

    def test_checks_wrong_model_bytes_is_8(self):
        image_path, expected = self._built_assistant_image()
        handover = self._manifest()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["product"]
            fs_file = os.path.join(work_dir, "prod.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            evil = os.path.join(work_dir, "evil.pte")
            good = self.model_data["tiny.pte"]
            with open(evil, "wb") as handle:
                handle.write(b"X" + good[1:])
            self.assertEqual(os.path.getsize(evil), len(good))
            image.fs_remove(fs_file, "/stargate/model/tiny.pte")
            image.fs_write_host_file(
                fs_file, evil, "/stargate/model/tiny.pte")
            image.e2fsck_clean(fs_file)
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = image.run_image_checks(
                tampered, ["files", "assistant"],
                handover["files"],
                "com.stargate.assistant", expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="in_image",
                assistant_apk_sha256=handover[
                    "assistant_apk"]["sha256"],
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("tiny.pte" in item for item in errors))

    def test_checks_launcher_config_mismatch_is_8(self):
        image_path, _expected = self._built_files_image()
        wrong = {"services": ["files"], "build_id": "forged",
                 "labels": []}
        code, errors = image.run_image_checks(
            image_path, ["files"], None, None, wrong,
            regions=self.regions, limit=self.stock_size,
            model_delivery="none",
            stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("baked config" in item for item in errors))

    def test_checks_size_mismatch_is_8(self):
        image_path, expected = self._built_files_image()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            grown = os.path.join(work_dir, "grown.img")
            shutil.copyfile(image_path, grown)
            with open(grown, "ab") as handle:
                handle.write(b"\x00")
            code, errors = image.run_image_checks(
                grown, ["files"], None, None, expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="none",
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("partition limit" in item
                            for item in errors))

    def test_checks_missing_image_is_8(self):
        code, errors = image.run_image_checks(
            os.path.join(self.root, "absent.img"), ["files"],
            None, None, {},
            regions=self.regions, limit=self.stock_size,
            model_delivery="none",
            stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(errors)

    def _built_after_boot_image(self):
        out = self._out()
        ctx = self._ctx(
            ASSISTANT_PLAN_AB_SHA, self._manifest_ab(),
            launcher_apk=self.launcher_after_boot)
        code, _stdout, err = image.build_route_a(
            self.ab_plan, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        build_id = image.build_id_for_plan(
            self.ab_plan, ASSISTANT_PLAN_AB_SHA)
        expected = checks.launcher_config(
            self.ab_plan, build_id, [image.AFTER_BOOT_LABEL])
        return os.path.join(
            out, "system-stargate-files-assistant.img"), expected, \
            os.path.join(out, "model-payload")

    def _check_after_boot(self, image_path, payload_dir):
        build_id = image.build_id_for_plan(
            self.ab_plan, ASSISTANT_PLAN_AB_SHA)
        expected = checks.launcher_config(
            self.ab_plan, build_id, [image.AFTER_BOOT_LABEL])
        return image.run_image_checks(
            image_path, ["files", "assistant"],
            self._manifest_ab()["files"],
            "com.stargate.assistant", expected,
            regions=self.regions, limit=self.stock_size,
            model_delivery="after_boot",
            payload_dir=payload_dir,
            assistant_apk_sha256=self._manifest_ab()[
                "assistant_apk"]["sha256"],
            stock_packages=self.stock_packages)

    def test_checks_after_boot_payload_tampered_is_8(self):
        image_path, _expected, payload = \
            self._built_after_boot_image()
        target = os.path.join(payload, "tokenizer.model")
        os.chmod(target, 0o644)
        with open(target, "r+b") as handle:
            handle.seek(0)
            handle.write(b"X")
        code, errors = self._check_after_boot(image_path, payload)
        self.assertEqual(code, 8)
        self.assertTrue(any("tokenizer.model" in item
                            for item in errors))

    def test_checks_after_boot_payload_missing_is_8(self):
        image_path, _expected, payload = \
            self._built_after_boot_image()
        target = os.path.join(payload, self.big_name)
        os.chmod(target, 0o644)
        os.unlink(target)
        code, errors = self._check_after_boot(image_path, payload)
        self.assertEqual(code, 8)
        self.assertTrue(any("missing model payload file" in item
                            for item in errors))

    def test_checks_after_boot_model_in_image_is_8(self):
        image_path, _expected, payload = \
            self._built_after_boot_image()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["product"]
            fs_file = os.path.join(work_dir, "prod.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            smuggled = os.path.join(work_dir, "smuggled.pte")
            with open(smuggled, "wb") as handle:
                handle.write(b"smuggled")
            image.fs_mkdir(fs_file, "/stargate")
            image.fs_write_host_file(
                fs_file, smuggled,
                "/stargate/%s" % self.big_name)
            image.e2fsck_clean(fs_file)
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = self._check_after_boot(
                tampered, payload)
        self.assertEqual(code, 8)
        self.assertTrue(any("in the image" in item for item in errors))

    def test_checks_files_only_with_payload_is_8(self):
        image_path, expected = self._built_files_image()
        with tempfile.TemporaryDirectory(
                prefix="payload-", dir=self.root) as payload:
            with open(os.path.join(payload, "tiny.pte"),
                      "wb") as handle:
                handle.write(b"stowaway")
            code, errors = image.run_image_checks(
                image_path, ["files"], None, None, expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="none", payload_dir=payload,
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("model payload in a build without "
                            "the Assistant" in item
                            for item in errors))

    def test_build_assistant_with_libs_success(self):
        out = self._out()
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA,
            self._manifest(apk_path=self.assist_libs_apk),
            launcher_apk=self.launcher_assist,
            assistant_apk=self.assist_libs_apk)
        code, _stdout, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        image_path = os.path.join(
            out, "system-stargate-files-assistant.img")
        with tempfile.TemporaryDirectory(
                prefix="libs-", dir=self.root) as work_dir:
            offset, size = self.regions["system"]
            fs_file = os.path.join(work_dir, "sys.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            for short, want in (("arm64", b"so-bytes-arm64"),
                                ("x86_64", b"so-bytes-x86-64")):
                entry = "/system/app/StargateAssistant/lib/%s/" \
                    "libtest.so" % short
                self.assertTrue(image.fs_exists(fs_file, entry))
                dest = os.path.join(work_dir, "lib-%s.so" % short)
                image.fs_dump(fs_file, entry, dest)
                with open(dest, "rb") as handle:
                    self.assertEqual(handle.read(), want)

    def test_checks_assistant_lib_tampered_is_8(self):
        out = self._out()
        ctx = self._ctx(
            ASSISTANT_PLAN_SHA,
            self._manifest(apk_path=self.assist_libs_apk),
            launcher_apk=self.launcher_assist,
            assistant_apk=self.assist_libs_apk)
        code, _stdout, err = image.build_route_a(
            ASSISTANT_PLAN, out, [], ctx)
        self.assertEqual((code, err), (0, ""))
        image_path = os.path.join(
            out, "system-stargate-files-assistant.img")
        build_id = image.build_id_for_plan(
            ASSISTANT_PLAN, ASSISTANT_PLAN_SHA)
        expected = checks.launcher_config(
            ASSISTANT_PLAN, build_id, [])
        handover = self._manifest(apk_path=self.assist_libs_apk)
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["system"]
            fs_file = os.path.join(work_dir, "sys.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            evil = os.path.join(work_dir, "evil.so")
            with open(evil, "wb") as handle:
                handle.write(b"Xo-bytes-arm64")
            entry = "/system/app/StargateAssistant/lib/arm64/" \
                "libtest.so"
            image.fs_remove(fs_file, entry)
            image.fs_write_host_file(fs_file, evil, entry)
            image.e2fsck_clean(fs_file)
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = image.run_image_checks(
                tampered, ["files", "assistant"],
                handover["files"], "com.stargate.assistant",
                expected, regions=self.regions,
                limit=self.stock_size,
                model_delivery="in_image",
                assistant_apk_sha256=handover[
                    "assistant_apk"]["sha256"],
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("embedded copy" in item for item in errors))

    def test_checks_foreign_apk_is_8(self):
        image_path, expected = self._built_files_image()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["system"]
            fs_file = os.path.join(work_dir, "sys.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            foreign = os.path.join(work_dir, "foreign.apk")
            make_apk("com.example.foreign", foreign)
            image.fs_mkdir(fs_file, "/system/app/Foreign")
            image.fs_write_host_file(
                fs_file, foreign,
                "/system/app/Foreign/Foreign.apk")
            image.e2fsck_clean(fs_file)
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = image.run_image_checks(
                tampered, ["files"], None, None, expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="none",
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("not in the stock baseline" in item
                            for item in errors))

    def test_checks_removed_stock_app_is_8(self):
        image_path, expected = self._built_files_image()
        with tempfile.TemporaryDirectory(
                prefix="tamper-", dir=self.root) as work_dir:
            offset, size = self.regions["system"]
            fs_file = os.path.join(work_dir, "sys.ext4")
            image.extract_region(image_path, offset, size,
                                 fs_file)
            image.fs_remove(fs_file, "/system/app/DupA/DupA.apk")
            tampered = os.path.join(work_dir, "tampered.img")
            image.splice_regions(
                image_path, tampered, [(offset, fs_file)])
            code, errors = image.run_image_checks(
                tampered, ["files"], None, None, expected,
                regions=self.regions, limit=self.stock_size,
                model_delivery="none",
                stock_packages=self.stock_packages)
        self.assertEqual(code, 8)
        self.assertTrue(any("missing from the image" in item
                            for item in errors))


if __name__ == "__main__":
    unittest.main()
