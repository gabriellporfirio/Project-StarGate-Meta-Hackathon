#!/usr/bin/env python3
"""Path A repack for the M4 spike: add a marker file to the system partition.

Reads the stock emulator system.img (GPT: vbmeta + super with LP extents),
writes a small marker text file into the system logical partition's ext4
filesystem (same size, in place), and splices the filesystem back into a
copy of the image. Everything else is byte-identical.

Standard library only. Requires debugfs/e2fsck on PATH (pre-existing).

Usage:
  path_a_repack.py --stock system.img --out system-stargate-a.img \
      --buildprop build.prop --selection NAME --random HEX
"""
import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

SYS_OFF = 3145728      # system extent offset in system.img (LP extent 0)
FS_BYTES = 840314880   # system ext4 size (205155 blocks x 4096)
MARKER_FS_PATH = "/system/stargate-marker.txt"
SELINUX_LABEL = b"u:object_r:system_file:s0"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def debugfs(fs, request, write=False):
    args = ["debugfs"] + (["-w"] if write else []) + ["-R", request, fs]
    rc, out, err = run(args)
    if rc != 0:
        raise SystemExit(f"debugfs {request!r} failed rc={rc}: {out}{err}")
    if "not found" in (out + err) or "No such file" in (out + err):
        raise SystemExit(f"debugfs {request!r} failed: {out}{err}")
    return out + err


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fix_selinux_nul(fsfile, fspath):
    """Append the NUL terminator debugfs ea_set omits (inline xattr)."""
    out = debugfs(fsfile, f"imap {fspath}")
    m = re.search(r"block (\d+), offset (0x[0-9a-fA-F]+)", out)
    blkno, inoof = int(m.group(1)), int(m.group(2), 16)
    with open(fsfile, "r+b") as f:
        f.seek(blkno * 4096)
        blk = bytearray(f.read(4096))
        ino = blk[inoof:inoof + 256]
        ex_isize, = struct.unpack("<H", ino[128:130])
        ea0 = 128 + ex_isize
        base = ea0 + 4  # skip 4-byte ibody magic
        assert struct.unpack("<I", ino[ea0:ea0 + 4])[0] == 0xEA020000
        p = base
        nl, ni = ino[p], ino[p + 1]
        voff, = struct.unpack("<H", ino[p + 2:p + 4])
        vblock, vsize, _ehash = struct.unpack("<III", ino[p + 4:p + 16])
        name = bytes(ino[p + 16:p + 16 + nl])
        assert (nl, ni, name, vsize) == (7, 6, b"selinux", 25), (nl, ni, name, vsize)
        oldval = bytes(ino[base + voff:base + voff + vsize])
        assert oldval == SELINUX_LABEL, oldval
        new_off = voff - 1
        e_end = p + ((16 + nl + 3) & ~3)
        assert base + new_off >= e_end + 4, "no room for NUL"
        struct.pack_into("<H", blk, inoof + p + 2, new_off)
        struct.pack_into("<I", blk, inoof + p + 8, 26)
        blk[inoof + base + new_off:inoof + base + new_off + 26] = oldval + b"\x00"
        f.seek(blkno * 4096)
        f.write(blk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--buildprop", required=True,
                    help="loose build.prop to cross-check the fs mapping")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--random", required=True, help="hex random for this run")
    a = ap.parse_args()

    marker = f"selection={a.selection}\nrandom={a.random}\n".encode()
    print(f"marker ({len(marker)} bytes):\n{a.selection}\n{a.random}", flush=True)

    tmpd = tempfile.mkdtemp(prefix="patha-")
    fsfile = os.path.join(tmpd, "sysfs.ext4")
    try:
        with open(a.stock, "rb") as f:
            f.seek(SYS_OFF)
            fsdata = f.read(FS_BYTES)
        assert len(fsdata) == FS_BYTES, len(fsdata)
        with open(fsfile, "wb") as f:
            f.write(fsdata)
        stock_fs_sha = hashlib.sha256(fsdata).hexdigest()
        print(f"stock fs region sha256: {stock_fs_sha}", flush=True)

        rc, out, _ = run(["e2fsck", "-n", "-f", fsfile])
        if rc != 0:
            raise SystemExit(f"stock fs e2fsck failed rc={rc}:\n{out}")
        print("stock fs e2fsck: clean", flush=True)

        bp_dump = os.path.join(tmpd, "build.prop")
        debugfs(fsfile, f"dump /system/build.prop {bp_dump}")
        with open(bp_dump, "rb") as f, open(a.buildprop, "rb") as g:
            assert f.read() == g.read(), "build.prop mismatch: wrong extent?"
        print("build.prop cross-check: identical", flush=True)

        marker_host = os.path.join(tmpd, "marker.txt")
        with open(marker_host, "wb") as f:
            f.write(marker)
        debugfs(fsfile, f"write {marker_host} {MARKER_FS_PATH}", write=True)
        debugfs(fsfile, f"sif {MARKER_FS_PATH} mode 0100644", write=True)
        debugfs(fsfile,
                f"ea_set {MARKER_FS_PATH} security.selinux "
                f"{SELINUX_LABEL.decode()}",
                write=True)
        fix_selinux_nul(fsfile, MARKER_FS_PATH)

        got = debugfs(fsfile, f"ea_get {MARKER_FS_PATH} security.selinux")
        assert '(26) = "u:object_r:system_file:s0\\000"' in got, got
        print("xattr: 26 bytes, NUL-terminated, matches build.prop", flush=True)
        cat = debugfs(fsfile, f"cat {MARKER_FS_PATH}")
        assert marker.decode() in cat, cat
        st = debugfs(fsfile, f"stat {MARKER_FS_PATH}")
        assert "Mode:  0644" in st and "User:     0" in st \
            and "Group:     0" in st, st
        print("marker stat: 0644 0:0, content verified", flush=True)

        rc, out, _ = run(["e2fsck", "-n", "-f", fsfile])
        if rc != 0:
            raise SystemExit(f"patched fs e2fsck failed rc={rc}:\n{out}")
        print("patched fs e2fsck: clean", flush=True)
        assert os.path.getsize(fsfile) == FS_BYTES, "fs size changed!"

        with open(fsfile, "rb") as f:
            new_fs = f.read()
        print(f"patched fs region sha256: "
              f"{hashlib.sha256(new_fs).hexdigest()}", flush=True)

        shutil.copyfile(a.stock, a.out)
        with open(a.out, "r+b") as f:
            f.seek(SYS_OFF)
            f.write(new_fs)
        print(f"wrote {a.out}", flush=True)
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


if __name__ == "__main__":
    main()
