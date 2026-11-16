#!/usr/bin/env python3
"""Build a local SM-G955F display probe from its captured BOOT partition."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    capture = args.capture
    manifest = json.loads((capture / 'manifest.json').read_text(encoding='utf-8'))
    cmdline = (capture / 'cmdline.txt').read_bytes().split()
    fb = (capture / 'framebuffer.txt').read_text().splitlines()
    expected_fb = ['/sys/class/graphics/fb0/virtual_size', '1440,5920',
                   '/sys/class/graphics/fb0/bits_per_pixel', '32',
                   '/sys/class/graphics/fb0/stride', '5760']
    if (manifest.get('model') != 'SM-G955F' or manifest.get('device') != 'dream2lte'
            or b's3cfb.bootloaderfb=0xcc000000' not in cmdline
            or fb[-6:] != expected_fb):
        parser.error('capture does not match the SM-G955F display configuration')
    stock = capture / 'oneui-boot.img'
    data = stock.read_bytes()
    if len(data) != 40960 * 1024 or data[:8] != b'ANDROID!':
        parser.error('expected a complete 40 MiB BOOT backup from this capture')
    kernel, _, ramdisk, _, second, _, _, page, dt_size, _ = struct.unpack_from('<10I', data, 8)
    if page != 2048 or second or not dt_size:
        parser.error('unsupported Android BOOT header')
    dt_pos = page + sum((size + page - 1) // page * page for size in (kernel, ramdisk, second))
    dt = data[dt_pos:dt_pos + dt_size]
    if len(dt) != dt_size or not dt.startswith(b'DTBH'):
        parser.error('missing Samsung DTBH')
    if args.output.exists():
        parser.error('output already exists')
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        elf = work / 'probe.elf'
        image = work / 'Image'
        initrd = work / 'initrd'
        built = work / 'probe-boot.img'
        linker = work / 'probe.ld'
        rd_pos = page + (kernel + page - 1) // page * page
        # Keep S-Boot's normal ramdisk handoff. The probe never executes it.
        initrd.write_bytes(data[rd_pos:rd_pos + ramdisk])
        linker.write_text('ENTRY(_head)\nSECTIONS { . = 0; .text : { *(.text.head) } }\n')
        subprocess.run(['zig', 'cc', '-target', 'aarch64-linux-gnu', '-nostdlib',
                        '-static', '-Wl,-e,_head', '-Wl,--build-id=none', '-Wl,-T,' + str(linker),
                        str(repo / 'shim/dream2lte/probe.S'), '-o', str(elf)], check=True)
        subprocess.run(['zig', 'objcopy', '-O', 'binary', str(elf), str(image)], check=True)
        payload = image.read_bytes()
        if payload[0x38:0x3c] != b'ARMd' or struct.unpack_from('<Q', payload, 0x10)[0] != len(payload):
            parser.error('invalid ARM64 probe header')
        subprocess.run([sys.executable, str(repo / 'tools/dreamlte_mkboot.py'),
                        str(stock), str(image), str(initrd), str(built)], check=True)
        output = built.read_bytes()
        new_dt_pos = page + sum((size + page - 1) // page * page for size in (len(payload), ramdisk))
        if output[new_dt_pos:new_dt_pos + dt_size] != dt or len(output) > len(data):
            parser.error('packed image failed DTBH or partition size check')
        with args.output.open('xb') as f:
            f.write(output)
    print(f'Local display probe: {args.output}')


if __name__ == '__main__':
    main()
