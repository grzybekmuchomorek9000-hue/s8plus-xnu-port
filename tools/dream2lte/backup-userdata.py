#!/usr/bin/env python3
"""Make and verify a raw USERDATA backup from the SM-G955F in TWRP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PARTITION = "/dev/block/sda28"
PARTITION_BYTES = 106064856 * 512
CHUNK = 4 * 1024**2
GIB = 1024**3


def allow_sparse(output):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    from msvcrt import get_osfhandle

    set_sparse = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
    set_sparse.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    set_sparse.restype = wintypes.BOOL
    returned = wintypes.DWORD()
    if not set_sparse(get_osfhandle(output.fileno()), 0x900C4, None, 0,
                      None, 0, ctypes.byref(returned), None):
        raise ctypes.WinError(ctypes.get_last_error())


def shell(adb, command, timeout=30):
    return subprocess.check_output(
        [*adb, "shell", command], text=True, timeout=timeout
    ).strip()


def verify_target(adb):
    identity = shell(
        adb, "getprop ro.product.model; getprop ro.product.device; getprop ro.twrp.version"
    ).splitlines()
    if len(identity) != 3 or identity[:2] != ["SM-G955F", "dream2lte"]:
        raise RuntimeError("The connected device must be an SM-G955F running TWRP.")
    links = shell(
        adb,
        "for path in /dev/block/by-name/USERDATA /dev/block/platform/*/by-name/USERDATA "
        '/dev/block/platform/*/*/by-name/USERDATA; do [ ! -b "$path" ] || readlink -f "$path"; done',
    ).splitlines()
    if set(links) != {PARTITION}:
        raise RuntimeError(f"Unexpected USERDATA links: {links}")
    size = int(shell(adb, "cat /sys/class/block/sda28/size")) * 512
    if size != PARTITION_BYTES:
        raise RuntimeError(f"Unexpected USERDATA size: {size} bytes.")

    related = set(
        shell(
            adb,
            "cat /sys/class/block/sda28/dev; "
            "for path in /sys/class/block/sda28/holders/*/dev; "
            'do [ ! -f "$path" ] || cat "$path"; done',
        ).splitlines()
    )
    for entry in shell(adb, "cat /proc/self/mountinfo").splitlines():
        fields = entry.split()
        mountpoint = fields[4]
        if fields[2] in related or mountpoint in {"/data", "/sdcard"}:
            raise RuntimeError(f"Unmount {mountpoint} in TWRP before backing up USERDATA.")
    return identity[2]


def hash_prefix(file, size):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = file.read(min(remaining, CHUNK))
        if not block:
            raise RuntimeError("The backup file ended before its expected size.")
        digest.update(block)
        remaining -= len(block)
    return digest


def read_partition(adb, path, resume):
    zeros = bytes(CHUNK)
    with path.open("r+b" if resume else "xb") as output:
        allow_sparse(output)
        copied = 0
        if resume:
            size = path.stat().st_size
            if size > PARTITION_BYTES:
                raise RuntimeError("The partial file is larger than USERDATA.")
            copied = size - (size % 4096)
        digest = hash_prefix(output, copied)
        output.truncate(copied)
        next_report = (copied // GIB + 1) * GIB
        if copied:
            print(f"Resuming at {copied / GIB:.2f} GiB.", flush=True)
        command = [
            *adb, "exec-out",
            f"dd if={PARTITION} bs={CHUNK} iflag=skip_bytes skip={copied} status=none 2>/dev/null",
        ]
        with subprocess.Popen(command, stdout=subprocess.PIPE) as reader:
            try:
                while block := reader.stdout.read(CHUNK):
                    copied += len(block)
                    if copied > PARTITION_BYTES:
                        raise RuntimeError("ADB sent more bytes than the partition contains.")
                    if block == zeros[:len(block)]:
                        output.seek(len(block), os.SEEK_CUR)
                    else:
                        output.write(block)
                    digest.update(block)
                    if copied >= next_report:
                        print(f"Copied {copied / GIB:.1f} / {PARTITION_BYTES / GIB:.1f} GiB", flush=True)
                        if shutil.disk_usage(path.parent).free < 2 * GIB:
                            raise RuntimeError("Available space is too low to continue the backup.")
                        next_report += GIB
                if reader.wait() != 0 or copied != PARTITION_BYTES:
                    raise RuntimeError(f"Incomplete partition read ({copied} bytes).")
                output.truncate(copied)
                output.flush()
                os.fsync(output.fileno())
            finally:
                if reader.poll() is None:
                    reader.kill()
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True, help="ADB serial of the target S8+")
    parser.add_argument("--out", type=Path, required=True, help="New raw image path")
    parser.add_argument("--resume", action="store_true", help="Continue this phone's existing .part file")
    args = parser.parse_args()
    adb = [args.adb, "-s", args.serial]
    recovery = verify_target(adb)
    image = args.out
    partial = image.with_name(image.name + ".part")
    receipt = image.with_name(image.name + ".json")
    if image.exists() or receipt.exists() or partial.exists() != args.resume:
        raise RuntimeError("Backup path is in use. Preserve the old files and use a new path.")
    if shutil.disk_usage(image.parent).free < 2 * GIB:
        raise RuntimeError("There is not enough free disk space to begin the backup.")

    copied_digest = read_partition(adb, partial, args.resume)
    print("Checking the SHA-256 read directly from the phone.", flush=True)
    if verify_target(adb) != recovery:
        raise RuntimeError("The device changed during the backup.")
    checksum = shell(adb, f"sha256sum {PARTITION}", timeout=1800)
    if not re.fullmatch(r"[0-9a-f]{64}\s+" + PARTITION, checksum):
        raise RuntimeError("The phone did not return a usable USERDATA checksum.")
    if checksum.split()[0] != copied_digest:
        raise RuntimeError("The on-device checksum did not match the backup.")
    with partial.open("rb") as stored:
        stored_digest = hash_prefix(stored, PARTITION_BYTES)
    if stored_digest.hexdigest() != copied_digest:
        raise RuntimeError("The saved backup checksum did not match the phone.")
    partial.rename(image)
    with receipt.open("x", encoding="utf-8") as output:
        json.dump(
            {
                "model": "SM-G955F",
                "serial": args.serial,
                "partition": PARTITION,
                "bytes": PARTITION_BYTES,
                "sha256": copied_digest,
            },
            output,
            indent=2,
        )
        output.write("\n")
    print(f"Verified backup: {image.resolve()}\nSHA-256: {copied_digest}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        sys.exit(str(exc))
