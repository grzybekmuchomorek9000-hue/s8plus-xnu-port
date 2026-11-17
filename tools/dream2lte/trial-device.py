#!/usr/bin/env python3
"""Verified BOOT-only trial or read-only persistent log retrieval in S8+ TWRP.

Never modifies recovery, partition layout, or USERDATA. 'flash' reboots only
after staged image and full BOOT readback match. Caller supplies both hashes.
"""
import argparse
import hashlib
from pathlib import Path
import re
import struct
import subprocess

BOOT_SIZE = 41943040


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', type=Path, required=True)
    sub = p.add_subparsers(dest='operation', required=True)
    flash = sub.add_parser('flash')
    flash.add_argument('image', type=Path)
    flash.add_argument('--image-sha256', required=True)
    flash.add_argument('--current-boot-sha256', required=True)
    log = sub.add_parser('log')
    log.add_argument('output', type=Path, help='new filename without extension')
    args = p.parse_args()
    adb = [str(args.adb.resolve())]

    def command(*argv, raw=False, timeout=120):
        r = subprocess.run(adb + list(argv), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        if r.returncode:
            raise RuntimeError(r.stderr.decode(errors='replace'))
        return r.stdout if raw else r.stdout.decode().strip()

    devices = [line.split() for line in command('devices').splitlines()[1:] if line.strip()]
    if len(devices) != 1 or devices[0][1] != 'recovery':
        raise RuntimeError('Expected exactly one phone in TWRP recovery')
    adb += ['-s', devices[0][0]]
    identity = command('shell', 'getprop ro.product.model; getprop ro.product.device; getprop ro.twrp.version').splitlines()
    if len(identity) != 3 or identity[:2] != ['SM-G955F', 'dream2lte'] or not identity[2]:
        raise RuntimeError('Unexpected model or missing TWRP')
    if command('shell', 'readlink -f /dev/block/platform/11120000.ufs/by-name/BOOT') != '/dev/block/sda7':
        raise RuntimeError('Unexpected BOOT partition')
    if command('shell', 'cat /sys/class/block/sda7/size') != str(BOOT_SIZE // 512):
        raise RuntimeError('Unexpected BOOT size')
    print('Verified SM-G955F/dream2lte in TWRP', flush=True)
    if args.operation == 'log':
        binary = args.output.with_suffix('.bin')
        text = args.output.with_suffix('.txt')
        if binary.exists() or text.exists():
            raise RuntimeError('Log output already exists')
        if command('shell', 'readlink -f /dev/block/platform/11120000.ufs/by-name/USERDATA') != '/dev/block/sda28':
            raise RuntimeError('Unexpected USERDATA partition')
        sectors = int(command('shell', 'cat /sys/class/block/sda28/size'))
        if sectors != 106064856 or sectors % 8:
            raise RuntimeError('Unexpected USERDATA size')
        blob = command('exec-out', 'dd', 'if=/dev/block/sda28', 'bs=4096',
                       f'skip={sectors // 8 - 1025}', 'count=1025', 'status=none', raw=True)
        if len(blob) != 1025 * 4096:
            raise RuntimeError('Short ring read')
        magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', blob)
        if magic != b'SWEETLOG' or version != 1 or size != 4 * 1024 * 1024:
            raise RuntimeError('Invalid log header')
        ring = blob[4096:]
        decoded = ring[:total] if total <= size else ring[total % size:] + ring[:total % size]
        with binary.open('xb') as f: f.write(blob)
        with text.open('x', encoding='utf-8') as f: f.write(decoded.decode('utf-8', 'replace').replace('\r', ''))
        print(f'Captured {total} bytes, flush {seq}: {text}')
        return
    for h in (args.image_sha256, args.current_boot_sha256):
        if not re.fullmatch('[0-9a-f]{64}', h):
            raise RuntimeError('Expected lowercase SHA-256')
    data = args.image.read_bytes()
    if digest(data) != args.image_sha256 or not data.startswith(b'ANDROID!') or not 0 < len(data) <= BOOT_SIZE:
        raise RuntimeError('Unexpected image digest/format/length')
    live = command('shell', 'sha256sum /dev/block/sda7').split()[0]
    if live != args.current_boot_sha256:
        raise RuntimeError('Live BOOT differs from the explicitly expected image')
    padded = data.ljust(BOOT_SIZE, b'\0')
    expected = digest(padded)
    path = args.image.with_name(args.image.stem + '-trial-padded.img')
    if path.exists():
        if digest(path.read_bytes()) != expected: raise RuntimeError('Conflicting padded output')
    else:
        with path.open('xb') as f: f.write(padded)
    remote = '/tmp/s8plus-mali-trial.img'
    command('push', '-Z', str(path.resolve()), remote)
    if command('shell', 'sha256sum ' + remote).split()[0] != expected:
        raise RuntimeError('Staged transfer digest mismatch')
    # Check again immediately before the only persistent device write.
    if command('shell', 'sha256sum /dev/block/sda7').split()[0] != live:
        raise RuntimeError('BOOT changed during staging')
    print('Writing verified 40 MiB BOOT image', flush=True)
    command('shell', 'dd if=' + remote + ' of=/dev/block/sda7 bs=1048576 conv=fsync,notrunc')
    command('shell', 'sync')
    actual = command('shell', 'sha256sum /dev/block/sda7').split()[0]
    if actual != expected:
        raise RuntimeError('BOOT readback mismatch; phone remains in TWRP')
    command('shell', 'rm ' + remote)
    print('Full BOOT readback verified:', expected, flush=True)
    command('reboot')
    print('Requested iOS boot', flush=True)


if __name__ == '__main__':
    main()
