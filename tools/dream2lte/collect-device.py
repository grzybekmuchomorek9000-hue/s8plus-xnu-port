#!/usr/bin/env python3
"""Read-only TWRP capture for an SM-G955F port. Never flashes or reboots."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', help='ADB serial if several devices are connected')
    parser.add_argument('--out', type=Path, default=Path('research/dream2lte-capture'))
    args = parser.parse_args()
    base = [args.adb] + (['-s', args.serial] if args.serial else [])

    def adb(*cmd):
        result = subprocess.run(base + list(cmd), capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace').strip() or 'ADB command failed')
        return result.stdout

    if args.out.exists():
        raise RuntimeError('Output already exists; use a new --out directory to preserve earlier evidence.')
    model = adb('shell', 'getprop ro.product.model').decode().strip()
    device = adb('shell', 'getprop ro.product.device').decode().strip()
    recovery = adb('shell', 'getprop ro.twrp.version').decode().strip()
    if not recovery:
        raise RuntimeError('Boot into TWRP first. Android properties from a custom ROM may be spoofed.')
    compatible = adb('exec-out', 'cat /proc/device-tree/compatible')
    if 'dream2lte' not in device.lower() and b'dream2' not in compatible.lower():
        raise RuntimeError(f'Not identified as dream2lte: model={model}, device={device}')
    args.out.mkdir(parents=True)
    manifest = {'model': model, 'device': device, 'twrp': recovery, 'files': {}, 'errors': {}}

    def capture(name, command):
        try:
            data = adb('exec-out', command)
            (args.out / name).write_bytes(data)
            manifest['files'][name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            manifest['errors'][name] = str(exc)

    capture('compatible.bin', 'cat /proc/device-tree/compatible')
    capture('model.bin', 'cat /proc/device-tree/model')
    capture('cmdline.txt', 'cat /proc/cmdline')
    capture('iomem.txt', 'cat /proc/iomem')
    capture('cpuinfo.txt', 'cat /proc/cpuinfo')
    capture('partitions.txt', 'cat /proc/partitions')
    capture('mounts.txt', 'cat /proc/mounts')
    capture('fdt.bin', 'cat /sys/firmware/fdt')
    # Binary output is captured directly; no PowerShell text redirection.
    capture('device-tree.tar', 'tar -C /proc/device-tree -cf - .')
    capture('by-name.txt', 'ls -l /dev/block/by-name /dev/block/platform/*/by-name /dev/block/platform/*/*/by-name 2>/dev/null; true')
    capture('framebuffer.txt', 'for f in /sys/class/graphics/fb0/name /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel /sys/class/graphics/fb0/stride; do echo "$f"; cat "$f" 2>/dev/null; done; true')
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Capture saved in {args.out.resolve()}')
    print('No partitions were written. Keep the capture private: the live DT may contain identifiers.')
    if manifest['errors']:
        print('Some reads failed; see manifest.json. Do not infer missing hardware values.')
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        sys.exit(str(exc))
