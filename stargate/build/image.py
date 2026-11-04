"""Route A image packaging for `stargate build` (M4 Parts 2 and 3).

Repacks the stock emulator system image with the spike's proven
same-size method (spike/m4/path_a_repack.py, docs/m4-spike.md):
verify the stock bytes, extract the system/system_ext/product ext4
regions, check them with e2fsck, cross-check build.prop, add the
StarGate apps and files with debugfs (mode 0644/0755, 0:0, the
system_file label with the 1-byte NUL fix), remove the stock launcher
APK so ours is the home screen, and splice the regions back into a
copy of the stock image. Same size, in place; every other byte
identical (verified in a closed loop).

The Assistant's model (~1.1 GB) does not fit the 292 MB product
partition, so a Route A Assistant build delivers it after boot
(spec 4d, owner decision 1 Nov 22:57): the Assistant APK goes into
the image like the Files app (with its native libraries in a lib/
folder beside it, since the approved APK is not page-aligned and
must not be changed or re-signed), while the model files are copied
into an immutable payload snapshot under the build outputs and
hashed there. The image holds no model file. Every output carries
the label "apps in image; model added after boot". A small model
that does fit the product partition still goes in the image
(model_delivery in_image); only the real 1.1 GB model takes the
after_boot path.

Standard library only. Needs debugfs/e2fsck/dumpe2fs on PATH
(pre-existing) plus the read-only aapt2 for package checks.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile

from . import checks as checks_module
from . import manifest as manifest_module

EXIT_OK = 0
EXIT_BUILD_FAILED = 8
EXIT_BIND_FAILED = 9

# Stock image: system-images;android-34;default;arm64-v8a (spike Q1/Q2,
# re-verified in M4a Part 2: same bytes, same layout).
STOCK_PATH = "/workspaces/m4-tools/img-stock/arm64-v8a/system.img"
LOOSE_BUILDPROP = "/workspaces/m4-tools/img-stock/arm64-v8a/build.prop"
STOCK_SHA256 = \
    "250ee9bb4e87b7beb5755a3b6984822bcf4335cd2d80701ac2e5ce9fb9da105d"
STOCK_SIZE = 1622147072
# The AVD image slot holds a stock-size image and the spike's method
# keeps it exactly: the partition limit is the stock size.
PARTITION_LIMIT_BYTES = STOCK_SIZE

# Logical-partition extents inside system.img (offset, bytes).
REGIONS = {
    "system": (3145728, 840314880),
    "system_ext": (862978048, 149532672),
    "product": (1012924416, 305967104),
}

# In-filesystem paths (the system fs is system-as-root; the product
# and system_ext filesystems mount at /product and /system_ext).
LAUNCHER_DIR = "/system/priv-app/StargateLauncher"
LAUNCHER_APK_NAME = "StargateLauncher.apk"
FILES_DIR = "/system/app/StargateFiles"
FILES_APK_NAME = "StargateFiles.apk"
ASSISTANT_DIR = "/system/app/StargateAssistant"
ASSISTANT_APK_NAME = "StargateAssistant.apk"
BUILD_ID_PATH = "/system/stargate-build-id.txt"
MODEL_FS_DIR = "/stargate/model"
STOCK_LAUNCHER_DIR = "/priv-app/Launcher3QuickStep"

SELINUX_LABEL = b"u:object_r:system_file:s0"

# Spec 4d label: every after_boot output, the manifest, the launcher
# and the docs carry it; the image is never called a pure image build.
AFTER_BOOT_LABEL = "apps in image; model added after boot"

# Payload snapshot dir under the build outputs (relative, read-only
# files). This snapshot is what the owner downloads (spec 4d).
PAYLOAD_DIR_NAME = "model-payload"

# APK lib/ ABI dir to the on-device short name beside a system APK
# (stock PrintSpooler uses lib/arm64/ on this image).
ABI_SHORT_NAMES = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "armeabi": "arm",
    "x86": "x86",
    "x86_64": "x86_64",
}

# Default build inputs. The launcher and Files APKs come from
# android/build_apps.sh (same plan and build ID); the Assistant APK
# is the verified read-only Part 3 snapshot (never the sibling
# m4b-tools/out/ folder), and the model files are read from M4b's
# download dir into the payload snapshot.
LAUNCHER_APK_PATH = "/workspaces/m4a-tools/out/stargate-launcher.apk"
FILES_APK_PATH = "/workspaces/m4a-tools/out/stargate-files.apk"
ASSISTANT_APK_PATH = \
    "/workspaces/m4a-tools/inputs/assistant-1936549b.apk"
MODEL_DIR = "/workspaces/m4b-downloads"

IMAGE_NAMES = {
    "files": "system-stargate-files.img",
    "assistant": "system-stargate-assistant.img",
    "assistant+files": "system-stargate-files-assistant.img",
}

_DUMPE2FS_RE = re.compile(r"^([^:]+):\s*(.*?)\s*$")


class BuildError(Exception):
    """A Route A build failure (maps to exit 8)."""


class BindError(Exception):
    """A section 4d payload binding failure (maps to exit 9)."""


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def require_tools() -> None:
    """Raise BuildError when a needed binary is missing from PATH."""
    for tool in ("debugfs", "e2fsck", "dumpe2fs"):
        if shutil.which(tool) is None:
            raise BuildError("missing required tool on PATH: %s"
                             % tool)


def _debugfs_raw(fs_path: str, request: str, write=False) -> str:
    """Run one debugfs request, return all output (no missing check)."""
    args = ["debugfs"] + (["-w"] if write else []) \
        + ["-R", request, fs_path]
    code, out, err = _run(args)
    if code != 0:
        raise BuildError("debugfs %r failed: %s%s"
                         % (request, out, err))
    return out + err


def debugfs(fs_path: str, request: str, write=False) -> str:
    """Run one debugfs request. Raises BuildError on failure.

    debugfs exits 0 even when a path lookup fails, so the spike's
    markers are checked too.
    """
    out = _debugfs_raw(fs_path, request, write)
    if "not found" in out or "No such file" in out:
        raise BuildError("debugfs %r failed: %s" % (request, out))
    return out


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def e2fsck_clean(fs_path: str) -> None:
    """Raise BuildError unless e2fsck finds the filesystem clean."""
    code, out, err = _run(["e2fsck", "-n", "-f", fs_path])
    if code != 0:
        raise BuildError("e2fsck failed for %s: %s%s"
                         % (fs_path, out, err))


def e2fsck_fix(fs_path: str) -> None:
    """Repair with e2fsck -y; debugfs rm leaves bitmaps stale.

    rc 0 (clean) and 1 (corrected) both pass; anything else fails.
    """
    code, out, err = _run(["e2fsck", "-y", "-f", fs_path])
    if code not in (0, 1):
        raise BuildError("e2fsck -y failed for %s (rc=%d): %s%s"
                         % (fs_path, code, out, err))


def fs_free(fs_path: str):
    """Free (blocks, inodes, block_size) from dumpe2fs."""
    code, out, err = _run(["dumpe2fs", "-h", fs_path])
    if code != 0:
        raise BuildError("dumpe2fs failed for %s: %s%s"
                         % (fs_path, out, err))
    values = {}
    for line in out.splitlines():
        match = _DUMPE2FS_RE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    try:
        return (int(values["Free blocks"]), int(values["Free inodes"]),
                int(values["Block size"]))
    except (KeyError, ValueError):
        raise BuildError("cannot parse dumpe2fs for %s" % fs_path)


def extract_region(image_path: str, offset: int, size: int,
                   dest_path: str) -> None:
    """Copy one byte region out of an image; checks the ext4 magic."""
    with open(image_path, "rb") as handle:
        handle.seek(offset)
        data = handle.read(size)
    if len(data) != size:
        raise BuildError("short read at offset %d in %s"
                         % (offset, image_path))
    if data[0x438:0x43A] != b"\x53\xef":
        raise BuildError("no ext4 magic at offset %d in %s"
                         % (offset, image_path))
    with open(dest_path, "wb") as handle:
        handle.write(data)


def fix_selinux_nul(fs_path: str, fs_entry: str,
                    label: bytes = SELINUX_LABEL) -> None:
    """Append the NUL terminator debugfs ea_set omits (inline xattr).

    Same algorithm as the spike's proven path_a_repack.py: shift the
    selinux value down one byte in the inode body and store it with a
    trailing NUL. Block and inode sizes come from the superblock.
    """
    out = debugfs(fs_path, "imap %s" % fs_entry)
    match = re.search(r"block (\d+), offset (0x[0-9a-fA-F]+)", out)
    if match is None:
        raise BuildError("cannot locate inode for %s" % fs_entry)
    blkno, inoof = int(match.group(1)), int(match.group(2), 16)
    with open(fs_path, "r+b") as handle:
        # Superblock sits at byte 1024; s_log_block_size is at
        # SB+24 and s_inode_size at SB+88.
        handle.seek(1024 + 24)
        log_block = struct.unpack("<I", handle.read(4))[0]
        block_size = 1024 << log_block
        handle.seek(1024 + 88)
        inode_size = struct.unpack("<H", handle.read(2))[0]
        handle.seek(blkno * block_size)
        blk = bytearray(handle.read(block_size))
        ino = blk[inoof:inoof + inode_size]
        ex_isize, = struct.unpack("<H", ino[128:130])
        ea0 = 128 + ex_isize
        base = ea0 + 4  # skip 4-byte ibody magic
        if struct.unpack("<I", ino[ea0:ea0 + 4])[0] != 0xEA020000:
            raise BuildError("no inline xattr on %s" % fs_entry)
        pos = base
        nlen, nidx = ino[pos], ino[pos + 1]
        voff, = struct.unpack("<H", ino[pos + 2:pos + 4])
        _vblock, vsize, _ehash = struct.unpack(
            "<III", ino[pos + 4:pos + 16])
        name = bytes(ino[pos + 16:pos + 16 + nlen])
        if (nlen, nidx, name, vsize) != (7, 6, b"selinux",
                                        len(label)):
            raise BuildError("unexpected xattr on %s" % fs_entry)
        oldval = bytes(ino[base + voff:base + voff + vsize])
        if oldval != label:
            raise BuildError("xattr value mismatch on %s" % fs_entry)
        new_off = voff - 1
        entry_end = pos + ((16 + nlen + 3) & ~3)
        if base + new_off < entry_end + 4:
            raise BuildError("no room for NUL on %s" % fs_entry)
        struct.pack_into("<H", blk, inoof + pos + 2, new_off)
        struct.pack_into("<I", blk, inoof + pos + 8, vsize + 1)
        blk[inoof + base + new_off:
            inoof + base + new_off + vsize + 1] = oldval + b"\x00"
        handle.seek(blkno * block_size)
        handle.write(blk)


def fs_exists(fs_path: str, fs_entry: str) -> bool:
    """True when the entry exists in the filesystem."""
    out = _debugfs_raw(fs_path, "stat %s" % fs_entry)
    return "Inode:" in out


def fs_list(fs_path: str, fs_dir: str) -> list:
    """Bare names directly under a directory ([] when missing)."""
    try:
        out = debugfs(fs_path, "ls -l %s" % fs_dir)
    except BuildError:
        return []
    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 9 and re.match(r"^[0-7]{5,6}$", parts[1]):
            names.append(parts[-1])
    return sorted(set(names) - {".", ".."})


def fs_walk(fs_path: str, fs_dir: str = "/") -> list:
    """Every entry under a directory: (path, is_dir) pairs, sorted.

    Follows the directory tree breadth-first with one debugfs call
    per directory; symlinks and other non-directories are reported
    as files (never followed).
    """
    found = []
    queue = [fs_dir]
    while queue:
        current = queue.pop(0)
        try:
            out = debugfs(fs_path, "ls -l %s" % current)
        except BuildError:
            continue
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 9:
                continue
            mode, name = parts[1], parts[-1]
            if name in (".", ".."):
                continue
            if re.match(r"^[0-7]{5,6}$", mode) is None:
                continue
            entry = current.rstrip("/") + "/" + name
            if mode.startswith("40"):
                found.append((entry, True))
                queue.append(entry)
            else:
                found.append((entry, False))
    return sorted(found)


def fs_mkdir(fs_path: str, fs_dir: str) -> None:
    """Create a directory (0755 0:0, system_file label, verified)."""
    if fs_exists(fs_path, fs_dir):
        raise BuildError("refusing to clobber %s" % fs_dir)
    debugfs(fs_path, "mkdir %s" % fs_dir, write=True)
    debugfs(fs_path, "sif %s mode 040755" % fs_dir, write=True)
    debugfs(fs_path, "ea_set %s security.selinux %s"
            % (fs_dir, SELINUX_LABEL.decode()), write=True)
    fix_selinux_nul(fs_path, fs_dir)
    got = debugfs(fs_path, "ea_get %s security.selinux" % fs_dir)
    if "(26)" not in got or "system_file" not in got:
        raise BuildError("xattr verify failed for %s" % fs_dir)
    stat = debugfs(fs_path, "stat %s" % fs_dir)
    if "Mode:  0755" not in stat or "User:     0" not in stat \
            or "Group:     0" not in stat:
        raise BuildError("mode verify failed for %s" % fs_dir)


def fs_write_host_file(fs_path: str, host_src: str, fs_dest: str,
                       mode: str = "0100644") -> None:
    """Write a host file into the fs (0644 0:0, label, verified)."""
    if fs_exists(fs_path, fs_dest):
        raise BuildError("refusing to clobber %s" % fs_dest)
    debugfs(fs_path, "write %s %s" % (host_src, fs_dest), write=True)
    debugfs(fs_path, "sif %s mode %s" % (fs_dest, mode), write=True)
    debugfs(fs_path, "ea_set %s security.selinux %s"
            % (fs_dest, SELINUX_LABEL.decode()), write=True)
    fix_selinux_nul(fs_path, fs_dest)
    got = debugfs(fs_path, "ea_get %s security.selinux" % fs_dest)
    if "(26)" not in got or "system_file" not in got:
        raise BuildError("xattr verify failed for %s" % fs_dest)
    stat = debugfs(fs_path, "stat %s" % fs_dest)
    if "Mode:  0644" not in stat or "User:     0" not in stat \
            or "Group:     0" not in stat:
        raise BuildError("mode verify failed for %s" % fs_dest)
    size = os.path.getsize(host_src)
    match = re.search(r"Size: (\d+)", stat)
    if match is None or int(match.group(1)) != size:
        raise BuildError("size verify failed for %s" % fs_dest)


def fs_remove(fs_path: str, fs_entry: str) -> None:
    """Remove one file that must be there (stock content)."""
    if not fs_exists(fs_path, fs_entry):
        raise BuildError("expected %s in the image" % fs_entry)
    debugfs(fs_path, "rm %s" % fs_entry, write=True)
    if fs_exists(fs_path, fs_entry):
        raise BuildError("cannot remove %s" % fs_entry)


def fs_dump(fs_path: str, fs_entry: str, host_dest: str) -> None:
    """Copy one file out of the fs; raises BuildError when empty."""
    debugfs(fs_path, "dump %s %s" % (fs_entry, host_dest))
    if not os.path.isfile(host_dest):
        raise BuildError("cannot dump %s" % fs_entry)


def verify_stock_image(path: str, want_size: int = STOCK_SIZE,
                       want_sha: str = STOCK_SHA256) -> None:
    """Raise BuildError unless the stock image has its exact bytes."""
    if not os.path.isfile(path):
        raise BuildError("stock image is missing: %s" % path)
    size = os.path.getsize(path)
    if size != want_size:
        raise BuildError("stock image %s has %d bytes, expected %d"
                         % (path, size, want_size))
    digest = sha256_file(path)
    if digest != want_sha:
        raise BuildError("stock image %s hash %s does not match %s"
                         % (path, digest, want_sha))


def build_id_for_plan(plan: dict, plan_sha256: str) -> str:
    """Deterministic build ID: services plus 8 hex of the plan SHA."""
    services = sorted(plan["handoff"]["services"])
    return "m4a-%s-%s" % ("-".join(services), plan_sha256[:8])


def image_name_for_selection(selection: list) -> str:
    """Fixed output image name per service selection."""
    key = "+".join(sorted(selection))
    try:
        return IMAGE_NAMES[key]
    except KeyError:
        raise BuildError("unknown selection for image name: %r"
                         % (selection,))


def gzip_file(src_path: str, dest_path: str) -> None:
    """Deterministic gzip (mtime=0) for transfer artifacts."""
    with open(src_path, "rb") as raw:
        with open(dest_path, "wb") as out:
            with gzip.GzipFile(filename="", mode="wb", mtime=0,
                               fileobj=out) as packed:
                shutil.copyfileobj(raw, packed, 1 << 20)


def splice_regions(stock_path: str, out_path: str,
                   regions: list) -> None:
    """Copy the stock image, overwrite the (offset, fs_file) regions.

    Raises BuildError unless the output keeps the stock size exactly.
    """
    shutil.copyfile(stock_path, out_path)
    with open(out_path, "r+b") as handle:
        for offset, fs_file in regions:
            with open(fs_file, "rb") as region:
                data = region.read()
            handle.seek(offset)
            handle.write(data)
    if os.path.getsize(out_path) != os.path.getsize(stock_path):
        raise BuildError("repacked image size changed")


def check_outside_untouched(stock_path: str, out_path: str,
                            patched: list) -> None:
    """Raise BuildError when bytes outside patched ranges differ."""
    patched = sorted(patched)
    size = os.path.getsize(stock_path)
    if os.path.getsize(out_path) != size:
        raise BuildError("repacked image size changed")
    spans = []
    cursor = 0
    for offset, length in patched:
        if offset > cursor:
            spans.append((cursor, offset - cursor))
        cursor = max(cursor, offset + length)
    if cursor < size:
        spans.append((cursor, size - cursor))
    with open(stock_path, "rb") as want:
        with open(out_path, "rb") as got:
            for offset, length in spans:
                want.seek(offset)
                got.seek(offset)
                left = length
                while left:
                    chunk = min(left, 1 << 20)
                    at = offset + (length - left)
                    if want.read(chunk) != got.read(chunk):
                        raise BuildError(
                            "bytes changed outside the patched "
                            "regions (offset %d)" % at)
                    left -= chunk


def fit_numbers(free_bytes: int, total_bytes: int, need_bytes: int,
                free_inodes: int, need_inodes: int):
    """Pure fit verdict. Returns None when everything fits, else text."""
    if need_bytes > total_bytes:
        return ("needs %d bytes but the partition holds only %d "
                "in total" % (need_bytes, total_bytes))
    if need_bytes > free_bytes:
        return ("needs %d bytes but only %d are free"
                % (need_bytes, free_bytes))
    if need_inodes > free_inodes:
        return ("needs %d inodes but only %d are free"
                % (need_inodes, free_inodes))
    return None


def _app_search_dirs():
    """(region, fs_dir) pairs that hold installable apps."""
    return [("system", "/system/app"),
            ("system", "/system/priv-app"),
            ("system_ext", "/app"),
            ("system_ext", "/priv-app"),
            ("product", "/app"),
            ("product", "/priv-app")]


def _extract_all(image_path: str, regions: dict, work_dir: str):
    """Extract every region; returns {region: fs_file}."""
    fs_files = {}
    for region, (offset, size) in regions.items():
        fs_file = os.path.join(work_dir, region + ".ext4")
        extract_region(image_path, offset, size, fs_file)
        fs_files[region] = fs_file
    return fs_files


def list_packages_from_fs(fs_files: dict, work_dir: str,
                            aapt2: str):
    """Every app in extracted regions: (packages, dumps).

    Each entry is (package, version, region, fs apk path) with its
    real package name read from inside the dumped APK; dumps holds
    the host APK path per entry index. Raises BuildError when an
    APK cannot be read.
    """
    packages = []
    dumps = []
    for region, app_dir in _app_search_dirs():
        if region not in fs_files:
            continue
        fs_file = fs_files[region]
        for app in fs_list(fs_file, app_dir):
            for name in fs_list(fs_file, app_dir + "/" + app):
                if not name.endswith(".apk"):
                    continue
                dest = os.path.join(
                    work_dir, "pkg%04d.apk" % len(dumps))
                entry = app_dir + "/" + app + "/" + name
                fs_dump(fs_file, entry, dest)
                try:
                    package, version = checks_module.apk_package(
                        dest, aapt2)
                except OSError as exc:
                    raise BuildError(
                        "cannot read package of %s:%s: %s"
                        % (region, entry, exc))
                packages.append((package, version, region, entry))
                dumps.append(dest)
    return packages, dumps


def list_image_packages(image_path: str, regions: dict,
                        work_dir: str, aapt2: str):
    """Every app in the image: (package, version, region, fs apk path).

    Each APK is dumped out of the image and its real package name is
    read from inside it. Returns (packages, dumps, fs_files) where
    dumps maps each package entry index to its host APK path.
    Raises BuildError when an APK cannot be read.
    """
    fs_files = _extract_all(image_path, regions, work_dir)
    packages, dumps = list_packages_from_fs(
        fs_files, work_dir, aapt2)
    return packages, dumps, fs_files


def _is_stock_launcher_entry(entry) -> bool:
    """True for a stock launcher APK baseline entry."""
    _package, _version, region, fs_entry = entry
    return region == "system_ext" \
        and fs_entry.startswith(STOCK_LAUNCHER_DIR + "/") \
        and fs_entry.endswith(".apk")


def _ours_expected_entries(assistant_package):
    """(region, fs apk path) where each owned package must sit."""
    expected = {
        checks_module.EXPECTED_PACKAGES["launcher"]:
            ("system", LAUNCHER_DIR + "/" + LAUNCHER_APK_NAME),
        checks_module.EXPECTED_PACKAGES["files"]:
            ("system", FILES_DIR + "/" + FILES_APK_NAME),
    }
    if assistant_package is not None:
        expected[assistant_package] = (
            "system", ASSISTANT_DIR + "/" + ASSISTANT_APK_NAME)
    return expected


def _apk_lib_entries(apk_path: str):
    """{(abi, leaf): (bytes, sha256)} for lib/ .so entries in an APK."""
    found = {}
    with zipfile.ZipFile(apk_path, "r") as archive:
        for item in archive.infolist():
            parts = item.filename.split("/")
            if len(parts) != 3 or parts[0] != "lib" \
                    or not parts[2].endswith(".so"):
                continue
            data = archive.read(item.filename)
            found[(parts[1], parts[2])] = (
                len(data), hashlib.sha256(data).hexdigest())
    return found


def _assistant_lib_writes(assistant_apk: str, work_dir: str) -> list:
    """[(short_abi, leaf, host_path)] for the APK's lib/ .so entries.

    Extracts each embedded native library to a host file, ready for
    the lib/ folder beside the system APK (the approved APK is not
    page-aligned and must not be changed or re-signed, so its libs
    ship beside it, as stock prebuilts do). Raises BuildError on an
    unreadable APK, an unknown ABI, or two ABIs colliding on one
    folder entry.
    """
    try:
        with zipfile.ZipFile(assistant_apk, "r") as archive:
            blobs = [(item.filename, archive.read(item.filename))
                     for item in archive.infolist()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError("cannot list assistant APK native libs: %s"
                         % exc)
    seen = {}
    for filename, data in blobs:
        parts = filename.split("/")
        if len(parts) != 3 or parts[0] != "lib" \
                or not parts[2].endswith(".so"):
            continue
        short = ABI_SHORT_NAMES.get(parts[1])
        if short is None:
            raise BuildError("assistant APK has libs for unknown "
                             "ABI %r" % (parts[1],))
        key = (short, parts[2])
        if key in seen and seen[key] != data:
            raise BuildError("assistant APK maps two ABIs to "
                             "lib/%s/%s" % key)
        seen[key] = data
    writes = []
    for (short, leaf), data in sorted(seen.items()):
        dest = os.path.join(work_dir, "lib-%s-%s" % (short, leaf))
        with open(dest, "wb") as handle:
            handle.write(data)
        writes.append((short, leaf, dest))
    return writes


def run_image_checks(image_path: str, selection: list,
                     expected_model_files, assistant_package,
                     expected_config: dict,
                     model_fs_dir: str = MODEL_FS_DIR,
                     regions: dict = None, aapt2: str = None,
                     limit: int = PARTITION_LIMIT_BYTES,
                     model_delivery: str = "in_image",
                     payload_dir: str = None,
                     assistant_apk_sha256: str = None,
                     stock_packages: list = None):
    """Section 5 checks on a repacked image. Returns (exit, errors).

    selection: handoff services. expected_model_files: the manifest
    file list of {name, bytes, sha256}, or None without the
    Assistant. expected_config: the launcher config dict baked into
    its APK. model_delivery: in_image, after_boot or none.
    payload_dir: the after_boot snapshot dir (host path) or None.
    assistant_apk_sha256: the approved Assistant APK hash, checked
    against the APK dumped from the image when given.
    stock_packages: the recorded stock baseline, entries of
    (package, version, region, fs apk path); required, so every APK
    in the image is policed, not just ours. Everything is read from
    the real image bytes.
    """
    if regions is None:
        regions = REGIONS
    if aapt2 is None:
        aapt2 = checks_module.AAPT2
    errors = []
    if stock_packages is None:
        return EXIT_BUILD_FAILED, \
            ["no stock package baseline recorded for this check"]
    if model_delivery not in ("in_image", "after_boot", "none"):
        return EXIT_BUILD_FAILED, \
            ["unknown model_delivery %r" % (model_delivery,)]
    try:
        size = os.path.getsize(image_path)
    except OSError:
        return EXIT_BUILD_FAILED, ["image is missing: %s" % image_path]
    if size != limit:
        errors.append("image has %d bytes, partition limit is %d"
                      % (size, limit))
    with tempfile.TemporaryDirectory(prefix="imgcheck-") as work_dir:
        try:
            found, dumps, fs_files = list_image_packages(
                image_path, regions, work_dir, aapt2)
        except BuildError as exc:
            return EXIT_BUILD_FAILED, errors + [str(exc)]
        packages = [item[0] for item in found]
        wanted = checks_module.expected_packages_for_selection(
            selection, assistant_package)
        ours_places = _ours_expected_entries(assistant_package)
        for package in wanted:
            if package not in packages:
                errors.append("missing app %s for this selection"
                              % package)
        for pos, item in enumerate(found):
            package, version, region, entry = item
            if package in wanted:
                want_region, want_entry = ours_places[package]
                if (region, entry) != (want_region, want_entry):
                    errors.append(
                        "app %s is at %s:%s, expected %s:%s"
                        % (package, region, entry, want_region,
                           want_entry))
                if not version:
                    errors.append("app %s has no version" % package)
        ours = [package for package in packages
                if package in wanted]
        if len(set(ours)) != len(ours):
            errors.append("duplicate package in this build")
        baseline = [tuple(item) for item in stock_packages]
        kept = [item for item in baseline
                if not _is_stock_launcher_entry(item)]
        built_stock = [tuple(item) for item in found
                       if item[0] not in wanted
                       and not _is_stock_launcher_entry(tuple(item))]
        missing = list(kept)
        for item in built_stock:
            if item in missing:
                missing.remove(item)
            elif item[0].startswith("com.stargate."):
                errors.append("unselected app %s in this build"
                              % item[0])
            else:
                errors.append(
                    "app %s %s at %s:%s is not in the stock baseline"
                    % item)
        for item in missing:
            errors.append(
                "stock app %s %s at %s:%s is missing from the image"
                % item)
        for item in found:
            if _is_stock_launcher_entry(tuple(item)):
                errors.append("stock launcher still present: %s"
                              % item[3])
        ours_dumps = [dumps[pos] for pos, item in enumerate(found)
                      if item[0] in wanted]
        dumps_by_package = {}
        for pos, item in enumerate(found):
            dumps_by_package.setdefault(item[0], []).append(
                dumps[pos])
        launcher_dump = dumps_by_package.get(
            checks_module.EXPECTED_PACKAGES["launcher"], [])
        if launcher_dump:
            try:
                with zipfile.ZipFile(launcher_dump[0], "r") as arc:
                    raw = arc.read("assets/stargate-build.json")
                baked = json.loads(raw.decode("utf-8"))
            except (KeyError, ValueError,
                    zipfile.BadZipFile) as exc:
                errors.append("launcher baked config unreadable: %s"
                              % exc)
            else:
                if baked != expected_config:
                    errors.append(
                        "launcher baked config %r does not match "
                        "this build %r" % (baked, expected_config))
        wants_assistant = "assistant" in selection
        assistant_dumps = dumps_by_package.get(
            assistant_package, []) if wants_assistant else []
        if wants_assistant and assistant_dumps:
            dump = assistant_dumps[0]
            if assistant_apk_sha256 is not None:
                if sha256_file(dump) != assistant_apk_sha256:
                    errors.append(
                        "assistant APK in the image does not match "
                        "its manifest hash")
            try:
                permissions = checks_module.apk_permissions(
                    dump, aapt2)
            except OSError as exc:
                errors.append("cannot read assistant APK "
                              "permissions: %s" % exc)
                permissions = None
            if permissions:
                errors.append(
                    "assistant APK requests permissions %r; a "
                    "local Assistant needs none" % (permissions,))
            errors.extend(_check_assistant_libs(
                dump, fs_files.get("system"), work_dir))
        sys_fs = fs_files.get("system")
        if sys_fs is not None:
            if not wants_assistant \
                    and fs_exists(sys_fs, ASSISTANT_DIR):
                errors.append("assistant files in a build without "
                              "the Assistant")
            if "files" not in selection \
                    and fs_exists(sys_fs, FILES_DIR):
                errors.append("files app in a build without Files")
        if model_delivery == "none":
            if expected_model_files is not None:
                errors.append("model files recorded for a build "
                              "without the Assistant")
            if payload_dir is not None and _dir_has_files(
                    payload_dir):
                errors.append("model payload in a build without "
                              "the Assistant")
            model_host = os.path.join(work_dir, "model")
            os.makedirs(model_host)
            prod_fs = fs_files.get("product")
            if prod_fs is not None:
                for name in fs_list(prod_fs, model_fs_dir):
                    fs_dump(prod_fs, model_fs_dir + "/" + name,
                            os.path.join(model_host, name))
            errors.extend(checks_module.check_model_files(
                model_host, ours_dumps, None))
        elif model_delivery == "in_image":
            model_host = os.path.join(work_dir, "model")
            os.makedirs(model_host)
            prod_fs = fs_files.get("product")
            if prod_fs is not None:
                for name in fs_list(prod_fs, model_fs_dir):
                    fs_dump(prod_fs, model_fs_dir + "/" + name,
                            os.path.join(model_host, name))
            errors.extend(checks_module.check_model_files(
                model_host, ours_dumps, expected_model_files))
        else:
            errors.extend(_check_after_boot_model(
                expected_model_files, payload_dir, dumps,
                fs_files, work_dir))
    if errors:
        return EXIT_BUILD_FAILED, errors
    return EXIT_OK, []


def _dir_has_files(path: str) -> bool:
    """True when the dir exists and holds at least one file."""
    if not os.path.isdir(path):
        return False
    for _root, _dirs, files in os.walk(path):
        if files:
            return True
    return False


def _check_assistant_libs(assistant_dump: str, sys_fs,
                          work_dir: str) -> list:
    """Native libs beside the APK match the APK's embedded libs.

    Both sides are read from the real image bytes: the lib/ entries
    inside the dumped Assistant APK, and the files under
    /system/app/StargateAssistant/lib/<abi>/ in the image.
    """
    errors = []
    try:
        in_apk = _apk_lib_entries(assistant_dump)
    except (OSError, zipfile.BadZipFile) as exc:
        return ["cannot list assistant APK native libs: %s" % exc]
    want = {}
    for (abi, leaf), (_size, _digest) in in_apk.items():
        short = ABI_SHORT_NAMES.get(abi)
        if short is None:
            errors.append("assistant APK has libs for unknown ABI "
                          "%r" % (abi,))
            continue
        want[(short, leaf)] = in_apk[(abi, leaf)]
    in_image = {}
    lib_dir = ASSISTANT_DIR + "/lib"
    if sys_fs is not None and fs_exists(sys_fs, lib_dir):
        for short in fs_list(sys_fs, lib_dir):
            for leaf in fs_list(sys_fs, lib_dir + "/" + short):
                dest = os.path.join(
                    work_dir, "lib-%s-%s" % (short, leaf))
                try:
                    fs_dump(sys_fs,
                            lib_dir + "/" + short + "/" + leaf,
                            dest)
                except BuildError as exc:
                    errors.append(str(exc))
                    continue
                with open(dest, "rb") as handle:
                    data = handle.read()
                in_image[(short, leaf)] = (
                    len(data), hashlib.sha256(data).hexdigest())
    for key in sorted(set(want) | set(in_image)):
        if key not in in_image:
            errors.append("assistant lib %s/%s is missing from "
                          "the image" % key)
        elif key not in want:
            errors.append("unexpected assistant lib %s/%s in "
                          "the image" % key)
        elif in_image[key] != want[key]:
            errors.append("assistant lib %s/%s does not match "
                          "the APK's embedded copy" % key)
    return errors


def _check_after_boot_model(expected_model_files, payload_dir: str,
                            dumps: list, fs_files: dict,
                            work_dir: str) -> list:
    """After_boot model checks: nothing in the image, payload exact.

    The image must hold no model file: no entry anywhere in any
    region named like a payload file, and no such asset in any APK
    in the image. The payload snapshot must hold exactly the
    expected files with their approved bytes and hashes.
    """
    errors = []
    if not isinstance(expected_model_files, list) \
            or not expected_model_files:
        return ["after_boot build has no expected model files"]
    want = {}
    for item in expected_model_files:
        if isinstance(item, dict) and isinstance(item.get("name"),
                                                 str):
            want[item["name"]] = item
    names = set(want)
    for region, fs_file in sorted(fs_files.items()):
        for entry, is_dir in fs_walk(fs_file):
            if is_dir:
                continue
            leaf = entry.rsplit("/", 1)[-1]
            if leaf in names:
                errors.append("model file %s in the image "
                              "(%s:%s); the model is delivered "
                              "after boot" % (leaf, region, entry))
    for pos, dump in enumerate(dumps):
        try:
            assets = checks_module.apk_assets(dump)
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append("cannot list assets of image APK %d: %s"
                          % (pos, exc))
            continue
        for asset in assets:
            if asset.rsplit("/", 1)[-1] in names:
                errors.append("model file %s hidden in image APK "
                              "assets" % asset)
    if payload_dir is None or not os.path.isdir(payload_dir):
        errors.append("after_boot build is missing its model "
                      "payload snapshot")
        return errors
    present = {}
    for root, _dirs, files in os.walk(payload_dir):
        for name in files:
            full = os.path.join(root, name)
            present[os.path.relpath(full, payload_dir)] = full
    for name in sorted(present):
        if name not in want:
            errors.append("unexpected model payload file %s" % name)
            continue
        full = present[name]
        try:
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            errors.append("model payload file %s is unreadable: %s"
                          % (name, exc))
            continue
        if len(data) != want[name]["bytes"]:
            errors.append("model payload file %s has %d bytes, "
                          "expected %r"
                          % (name, len(data), want[name]["bytes"]))
            continue
        if hashlib.sha256(data).hexdigest() != want[name]["sha256"]:
            errors.append("model payload file %s content does not "
                          "match its approved hash" % name)
    for name in want:
        if name not in present:
            errors.append("missing model payload file %s" % name)
    return errors


def _model_targets(assistant_manifest: dict):
    """(fs_model_dir, {name: device_target}) from the manifest.

    Every model file lands in the one Route A folder the manifest
    names; files it names explicitly keep their exact target.
    """
    route_a = assistant_manifest.get("target_path_route_a")
    if not isinstance(route_a, str) or not route_a.startswith(
            "/product/"):
        raise BuildError("assistant manifest has no Route A "
                         "target_path_route_a under /product/")
    folder = route_a.rsplit("/", 1)[0]
    known = {route_a.rsplit("/", 1)[-1]: route_a}
    tok_a = assistant_manifest.get("tokenizer_path_route_a")
    if isinstance(tok_a, str) and tok_a.startswith(folder + "/"):
        known[tok_a.rsplit("/", 1)[-1]] = tok_a
    targets = {}
    for item in assistant_manifest["files"]:
        targets[item["name"]] = known.get(
            item["name"], folder + "/" + item["name"])
    return folder[len("/product"):], targets


def _model_targets_route_b(assistant_manifest: dict):
    """{name: device_target} for an after_boot payload from the manifest.

    Every model file lands in the app's own folder
    (/sdcard/Android/data/<package>/files/model/); files the
    manifest names explicitly keep their exact Route B target.
    """
    route_b = assistant_manifest.get("target_path_route_b")
    if not isinstance(route_b, str) or not route_b.startswith(
            "/sdcard/"):
        raise BuildError("assistant manifest has no Route B "
                         "target_path_route_b under /sdcard/")
    folder = route_b.rsplit("/", 1)[0]
    known = {route_b.rsplit("/", 1)[-1]: route_b}
    tok_b = assistant_manifest.get("tokenizer_path_route_b")
    if isinstance(tok_b, str) and tok_b.startswith(folder + "/"):
        known[tok_b.rsplit("/", 1)[-1]] = tok_b
    targets = {}
    for item in assistant_manifest["files"]:
        targets[item["name"]] = known.get(
            item["name"], folder + "/" + item["name"])
    return targets


def _mkdir_p(fs_file: str, fs_dir: str) -> None:
    """Create every missing level of an absolute directory."""
    path = ""
    for part in fs_dir.strip("/").split("/"):
        path += "/" + part
        if not fs_exists(fs_file, path):
            fs_mkdir(fs_file, path)


def _launcher_asset_config(apk_path: str) -> dict:
    """The baked stargate-build.json asset inside a launcher APK."""
    try:
        with zipfile.ZipFile(apk_path, "r") as archive:
            raw = archive.read("assets/stargate-build.json")
    except KeyError:
        raise BuildError("launcher APK %s has no baked "
                         "stargate-build.json asset" % apk_path)
    except zipfile.BadZipFile as exc:
        raise BuildError("launcher APK %s is unreadable: %s"
                         % (apk_path, exc))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise BuildError("launcher APK %s baked config is not JSON"
                         % apk_path)


def _apk_package(apk_path: str, aapt2: str):
    """(package, version) or BuildError when aapt2 cannot read it."""
    try:
        return checks_module.apk_package(apk_path, aapt2)
    except OSError as exc:
        raise BuildError("cannot read package of %s: %s"
                         % (apk_path, exc))


def build_route_a(plan: dict, out_dir: str, labels: list,
                  ctx: dict):
    """Route A build for run_build. Returns (exit_code, stdout, err)."""
    try:
        return _build_route_a(plan, out_dir, labels, ctx)
    except BindError as exc:
        return EXIT_BIND_FAILED, "", "error: %s\n" % exc
    except BuildError as exc:
        return EXIT_BUILD_FAILED, "", "error: %s\n" % exc


def _inputs(ctx: dict):
    """Build input paths, with test overrides from ctx."""
    return {
        "stock": ctx.get("stock_path", STOCK_PATH),
        "stock_size": ctx.get("stock_size", STOCK_SIZE),
        "stock_sha256": ctx.get("stock_sha256", STOCK_SHA256),
        "loose_buildprop": ctx.get("loose_buildprop",
                                   LOOSE_BUILDPROP),
        "regions": ctx.get("regions", REGIONS),
        "launcher_apk": ctx.get("launcher_apk", LAUNCHER_APK_PATH),
        "files_apk": ctx.get("files_apk", FILES_APK_PATH),
        "assistant_apk": ctx.get("assistant_apk",
                                 ASSISTANT_APK_PATH),
        "model_dir": ctx.get("model_dir", MODEL_DIR),
        "aapt2": ctx.get("aapt2", checks_module.AAPT2),
        "limit": ctx.get("partition_limit", PARTITION_LIMIT_BYTES),
    }


def _build_route_a(plan: dict, out_dir: str, labels: list, ctx: dict):
    require_tools()
    paths = _inputs(ctx)
    verify_stock_image(paths["stock"], paths["stock_size"],
                       paths["stock_sha256"])
    handoff = plan["handoff"]
    selection = list(handoff["services"])
    wants_assistant = "assistant" in selection
    assistant_manifest = ctx.get("assistant_manifest")
    if wants_assistant and not isinstance(assistant_manifest, dict):
        raise BuildError("assistant selected but no assistant "
                         "manifest was bound")
    build_id = build_id_for_plan(plan, ctx["plan_sha256"])
    image_name = image_name_for_selection(selection)
    image_path = os.path.join(out_dir, image_name)
    gz_path = image_path + ".gz"
    apk_dir = os.path.join(out_dir, "apk")
    manifest_path = os.path.join(
        out_dir, manifest_module.MANIFEST_NAME)
    for taken in (image_path, gz_path, manifest_path):
        if os.path.exists(taken):
            raise BuildError("output %s already exists (use a fresh "
                             "--out dir)" % taken)
    need_apks = {"launcher": paths["launcher_apk"]}
    if "files" in selection:
        need_apks["files"] = paths["files_apk"]
    if wants_assistant:
        need_apks["assistant"] = paths["assistant_apk"]
    missing = sorted(path for path in need_apks.values()
                     if not os.path.isfile(path))
    if missing:
        raise BuildError("missing build input: %s"
                         % ", ".join(missing))
    try:
        assistant_package = checks_module \
            .assistant_package_from_manifest(assistant_manifest) \
            if wants_assistant else None
    except (ValueError, AttributeError, TypeError) as exc:
        raise BuildError("assistant manifest has no usable package: "
                         "%s" % exc)
    versions = {}
    for service, apk_path in need_apks.items():
        package, version = _apk_package(apk_path, paths["aapt2"])
        if service == "assistant":
            want = assistant_package
        else:
            want = checks_module.EXPECTED_PACKAGES[service]
        if package != want:
            raise BuildError("%s holds package %s, expected %s"
                             % (apk_path, package, want))
        if not version:
            raise BuildError("%s has no version" % apk_path)
        versions[service] = version
    # The launcher config, the model sources and the model targets
    # all depend on the delivery decision (in_image vs after_boot),
    # which needs the product partition's measured free space, so
    # they are settled inside, after extraction.
    with tempfile.TemporaryDirectory(prefix="routea-") as work_dir:
        regions = paths["regions"]
        fs_files = _extract_all(paths["stock"], regions, work_dir)
        for fs_file in fs_files.values():
            e2fsck_clean(fs_file)
        sys_fs = fs_files.get("system")
        ext_fs = fs_files.get("system_ext")
        prod_fs = fs_files.get("product")
        if sys_fs is None or ext_fs is None:
            raise BuildError("stock image is missing the system "
                             "or system_ext region")
        if wants_assistant and prod_fs is None:
            raise BuildError("stock image is missing the product "
                             "region")
        # The stock baseline is recorded from these pristine regions,
        # before any write, so the section 5 checks police every APK
        # in the image against it, not just ours.
        stock_baseline, _stock_dumps = list_packages_from_fs(
            fs_files, work_dir, paths["aapt2"])
        dump = os.path.join(work_dir, "build.prop")
        fs_dump(sys_fs, "/system/build.prop", dump)
        with open(dump, "rb") as handle:
            in_fs = handle.read()
        with open(paths["loose_buildprop"], "rb") as handle:
            loose = handle.read()
        if in_fs != loose:
            raise BuildError("build.prop mismatch: wrong image?")
        model_need = sum(item["bytes"]
                         for item in assistant_manifest["files"]) \
            if wants_assistant else 0
        delivery = "none"
        if wants_assistant:
            free_blocks, free_inodes, _bs = fs_free(prod_fs)
            verdict = fit_numbers(
                free_blocks * 4096, regions["product"][1],
                model_need, free_inodes,
                2 + len(assistant_manifest["files"]))
            delivery = "in_image" if verdict is None else "after_boot"
        if delivery == "after_boot":
            labels = list(labels) + [AFTER_BOOT_LABEL]
        expected_config = checks_module.launcher_config(
            plan, build_id, labels)
        baked = _launcher_asset_config(need_apks["launcher"])
        if baked != expected_config:
            raise BuildError(
                "launcher APK was not built for this plan (baked %r, "
                "need %r); rebuild it with android/build_apps.sh "
                "--plan <this plan> --build-id %s"
                % (baked, expected_config, build_id))
        model_srcs = {}
        if delivery == "after_boot":
            # Local import: gate owns the builder, so a top-level
            # import would be circular.
            from .gate import bind_assistant_manifest
            bind_errors = bind_assistant_manifest(
                handoff["model"], assistant_manifest)
            if bind_errors:
                raise BindError(bind_errors[0])
            for item in assistant_manifest["files"]:
                src = os.path.join(paths["model_dir"], item["name"])
                if not os.path.isfile(src):
                    raise BuildError("missing model file: %s" % src)
                size = os.path.getsize(src)
                if size != item["bytes"]:
                    raise BindError(
                        "model file %s has %d bytes, handoff says %r"
                        % (src, size, item["bytes"]))
                model_srcs[item["name"]] = src
        elif delivery == "in_image":
            for item in assistant_manifest["files"]:
                src = os.path.join(paths["model_dir"], item["name"])
                if not os.path.isfile(src):
                    raise BuildError("missing model file: %s" % src)
                size = os.path.getsize(src)
                if size != item["bytes"]:
                    raise BuildError(
                        "model file %s has %d bytes, manifest says %r"
                        % (src, size, item["bytes"]))
                model_srcs[item["name"]] = src
        if delivery == "in_image":
            model_fs_dir, model_targets = _model_targets(
                assistant_manifest)
        elif delivery == "after_boot":
            model_fs_dir, model_targets = \
                MODEL_FS_DIR, _model_targets_route_b(
                    assistant_manifest)
        else:
            model_fs_dir, model_targets = MODEL_FS_DIR, {}
        build_id_text = "build_id=%s\n" % build_id
        sys_new_files = [(need_apks["launcher"], LAUNCHER_DIR,
                          LAUNCHER_APK_NAME)]
        if "files" in selection:
            sys_new_files.append((need_apks["files"], FILES_DIR,
                                  FILES_APK_NAME))
        if wants_assistant:
            sys_new_files.append((need_apks["assistant"],
                                  ASSISTANT_DIR, ASSISTANT_APK_NAME))
        lib_writes = _assistant_lib_writes(
            need_apks["assistant"], work_dir) \
            if wants_assistant else []
        lib_fs_files = [(host, ASSISTANT_DIR + "/lib/" + short, leaf)
                        for short, leaf, host in lib_writes]
        abi_dirs = {short for short, _leaf, _host in lib_writes}
        blocks = sum((os.path.getsize(src) + 4095) // 4096
                     for src, _d, _n in
                     sys_new_files + lib_fs_files)
        blocks += 1 + len(sys_new_files)
        if lib_writes:
            blocks += 1 + len(abi_dirs)
        free_blocks, free_inodes, _bs = fs_free(sys_fs)
        need_inodes = 2 * len(sys_new_files) + 1
        if lib_writes:
            need_inodes += 1 + len(abi_dirs) + len(lib_writes)
        verdict = fit_numbers(
            free_blocks * 4096, regions["system"][1],
            blocks * 4096, free_inodes, need_inodes)
        if verdict is not None:
            raise BuildError("system partition cannot take these "
                             "apps: %s" % verdict)
        if delivery == "in_image":
            for item in assistant_manifest["files"]:
                digest = sha256_file(model_srcs[item["name"]])
                if digest != item["sha256"]:
                    raise BuildError(
                        "model file %s does not match its approved "
                        "hash" % item["name"])
        for _src, fs_dir, _name in sys_new_files + lib_fs_files:
            _mkdir_p(sys_fs, fs_dir)
        for src, fs_dir, name in sys_new_files + lib_fs_files:
            fs_write_host_file(sys_fs, src, fs_dir + "/" + name)
        build_id_host = os.path.join(work_dir, "build-id.txt")
        with open(build_id_host, "w", encoding="utf-8") as handle:
            handle.write(build_id_text)
        fs_write_host_file(sys_fs, build_id_host, BUILD_ID_PATH)
        e2fsck_clean(sys_fs)
        stock_apks = [name for name in
                      fs_list(ext_fs, STOCK_LAUNCHER_DIR)
                      if name.endswith(".apk")]
        if not stock_apks:
            raise BuildError("stock launcher not found where the "
                             "stock image keeps it")
        for name in stock_apks:
            fs_remove(ext_fs, STOCK_LAUNCHER_DIR + "/" + name)
        # debugfs rm frees the inode but leaves block bitmaps and
        # free counts stale (the spike never removed files), so the
        # standard repair runs here, then the clean check as usual.
        e2fsck_fix(ext_fs)
        e2fsck_clean(ext_fs)
        if delivery == "in_image":
            _mkdir_p(prod_fs, model_fs_dir)
            for name, src in sorted(model_srcs.items()):
                fs_write_host_file(
                    prod_fs, src, model_fs_dir + "/" + name)
            e2fsck_clean(prod_fs)
        os.makedirs(out_dir, exist_ok=True)
        written = []
        try:
            payload_rel = None
            payload_host = None
            if delivery == "after_boot":
                payload_rel = PAYLOAD_DIR_NAME
                payload_host = os.path.join(out_dir, payload_rel)
                if os.path.exists(payload_host):
                    raise BuildError(
                        "output %s already exists (use a fresh "
                        "--out dir)" % payload_host)
                os.makedirs(payload_host)
                want_sha = {item["name"]: item["sha256"]
                            for item in assistant_manifest["files"]}
                for name in sorted(model_srcs):
                    dest = os.path.join(payload_host, name)
                    shutil.copyfile(model_srcs[name], dest)
                    os.chmod(dest, 0o444)
                    written.append(dest)
                    if sha256_file(dest) != want_sha[name]:
                        raise BuildError(
                            "model payload file %s does not match "
                            "its approved hash" % name)
            splice = [(regions[name][0], fs_files[name])
                      for name in ("system", "system_ext",
                                   "product")
                      if name in fs_files and
                      (name != "product" or
                       delivery == "in_image")]
            splice_regions(paths["stock"], image_path, splice)
            written.append(image_path)
            check_outside_untouched(
                paths["stock"], image_path,
                [(off, os.path.getsize(f)) for off, f in splice])
            gzip_file(image_path, gz_path)
            written.append(gz_path)
            os.makedirs(apk_dir, exist_ok=True)
            apk_names = {"launcher": "stargate-launcher.apk",
                         "files": "stargate-files.apk",
                         "assistant": "stargate-assistant.apk"}
            apk_records = []
            for service, src in need_apks.items():
                dest = os.path.join(apk_dir, apk_names[service])
                shutil.copyfile(src, dest)
                written.append(dest)
                apk_records.append(
                    {"service": service,
                     "package": assistant_package
                     if service == "assistant"
                     else checks_module.EXPECTED_PACKAGES[service],
                     "version": versions[service],
                     "path": "apk/" + apk_names[service],
                     "sha256": sha256_file(dest)})
            assistant_apk = assistant_manifest.get("assistant_apk") \
                if wants_assistant else None
            assistant_sha = assistant_apk.get("sha256") \
                if isinstance(assistant_apk, dict) else None
            code, check_errors = run_image_checks(
                image_path, selection,
                assistant_manifest["files"]
                if wants_assistant else None,
                assistant_package, expected_config,
                model_fs_dir, regions, paths["aapt2"],
                paths["limit"],
                model_delivery=delivery,
                payload_dir=payload_host,
                assistant_apk_sha256=assistant_sha,
                stock_packages=stock_baseline)
            if code != 0:
                raise BuildError("section 5 image check failed: %s"
                                 % "; ".join(check_errors))
            image_sha = sha256_file(image_path)
            image_bytes = os.path.getsize(image_path)
            doc = manifest_module.make_manifest(
                plan_sha256=ctx["plan_sha256"],
                selection=selection,
                selection_result=plan["selection_result"],
                route="A", build_id=build_id, labels=labels,
                artifacts=[
                    {"path": image_name, "bytes": image_bytes,
                     "sha256": image_sha},
                    {"path": image_name + ".gz",
                     "bytes": os.path.getsize(gz_path),
                     "sha256": sha256_file(gz_path)}],
                apps=apk_records,
                model_files=[{"name": item["name"],
                              "bytes": item["bytes"],
                              "sha256": item["sha256"],
                              "target": model_targets[item["name"]]}
                             for item in assistant_manifest["files"]]
                if wants_assistant else None,
                model_delivery=delivery,
                model_payload_path=payload_rel,
                runtime_version=assistant_manifest.get(
                    "runtime_version") if wants_assistant else None,
                assistant_manifest_sha256=ctx.get(
                    "assistant_manifest_sha256")
                if wants_assistant else None,
                unresolved=handoff["unresolved"],
                image_bytes=image_bytes,
                partition_limit_bytes=paths["limit"])
            try:
                manifest_module.write_manifest(doc, out_dir)
            except ValueError as exc:
                raise BuildError("cannot write build manifest: %s"
                                 % exc)
            written.append(manifest_path)
        except BuildError:
            for path in written:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise
    stdout = ("build_id=%s\nimage=%s\nimage_sha256=%s\n"
              "image_bytes=%d partition_limit=%d\nmanifest=%s\n"
              "model_delivery=%s\n") % (
                  build_id, image_path, image_sha, image_bytes,
                  paths["limit"], manifest_path, delivery)
    if payload_rel is not None:
        stdout += "payload=%s\n" % os.path.join(out_dir, payload_rel)
    return EXIT_OK, stdout, ""
