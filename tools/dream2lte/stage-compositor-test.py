#!/usr/bin/env python3
"""Stage the renderer diagnostic on an offline, mounted iOS build volume."""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import struct


def verified_probe(bundle):
    name = 'mali-compositor-test'
    expected = {}
    for line in (bundle / 'SHA256SUMS').read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        if path in expected:
            raise ValueError('duplicate entry in checksum manifest')
        expected[path] = digest
    data = (bundle / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected.get(name):
        raise ValueError('compositor test does not match its build manifest')
    magic, cpu, subtype, kind, count, length = struct.unpack_from('<6I', data)
    if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x0100000c, 0, 2) or length > len(data) - 32:
        raise ValueError('expected an ARM64 Mach-O executable')
    pos, end = 32, 32 + length
    ios = signed = False
    for _ in range(count):
        command, size = struct.unpack_from('<2I', data, pos)
        if size < 8 or pos + size > end:
            raise ValueError('invalid Mach-O load command')
        if command == 0x32 and size >= 24:
            ios = struct.unpack_from('<I', data, pos + 8)[0] == 2
        if command == 0x1d and size >= 16:
            signed = True
        pos += size
    if pos != end or not ios or not signed:
        raise ValueError('expected a signed iOS diagnostic')
    return data


def stage(root, bundle):
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise PermissionError('staging a system launch job requires a root-owned mount')
    data = verified_probe(bundle)
    root = root.resolve(strict=True)
    if not (root / 'sbin/launchd').is_file() or not (root / 'System/Library/xpc').is_dir():
        raise ValueError('expected a mounted iOS build volume')
    label = 'org.dream2lte.mali-compositor-test'
    program = '/usr/local/libexec/dream2lte-mali-compositor-test'
    log = '/private/var/log/dream2lte-mali-compositor-test.log'
    job = plistlib.dumps({
        'Label': label,
        'ProgramArguments': [program],
        'RunAtLoad': True,
        'LaunchOnlyOnce': True,
        'StandardOutPath': log,
        'StandardErrorPath': log,
    })
    paths = (
        (root / program.lstrip('/'), data, 0o755),
        (root / f'Library/LaunchDaemons/{label}.plist', job, 0o644),
    )
    for path, _, _ in paths:
        if path.resolve() != path or path.exists():
            raise FileExistsError(f'will not replace or follow {path}')
    log_dir = root / 'private/var/log'
    if log_dir.resolve() != log_dir:
        raise ValueError('unexpected log directory symlink')
    log_dir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, contents, mode in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as file:
                written.append(path)
                file.write(contents)
            path.chmod(mode)
    except Exception:
        for path in reversed(written):
            path.unlink()
        raise
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='mount point of the offline iOS build volume')
    parser.add_argument('bundle', type=Path, help='extracted dream2lte-gles build artifact')
    args = parser.parse_args()
    for path, _, _ in stage(args.root, args.bundle):
        print(path)


if __name__ == '__main__':
    main()
