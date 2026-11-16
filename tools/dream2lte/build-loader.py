#!/usr/bin/env python3
"""Build the SM-G955F loader for the first XNU handoff trial using Zig."""
import argparse
from pathlib import Path
import struct
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    shim = Path(__file__).resolve().parents[2] / 'shim'
    donor = shim / 'dreamlte'
    with tempfile.TemporaryDirectory() as directory:
        elf = Path(directory) / 'loader.elf'
        raw = Path(directory) / 'Image'
        subprocess.run([
            'zig', 'cc', '-target', 'aarch64-linux-gnu', '-mcpu=cortex_a53',
            '-O2', '-ffreestanding', '-nostdlib', '-static', '-fno-pic',
            '-fno-stack-protector', '-mgeneral-regs-only', '-mstrict-align',
            '-Wall', '-Wextra', '-Werror', '-DDREAM2LTE_BRINGUP',
            '-Wl,-e,_head', '-Wl,--build-id=none', '-Wl,-T,' + str(donor / 'loader.ld'),
            str(donor / 'start.S'), str(donor / 'loader.c'), str(shim / 'fdt.c'),
            '-o', str(elf),
        ], check=True)
        subprocess.run(['zig', 'objcopy', '-O', 'binary', str(elf), str(raw)], check=True)
        data = raw.read_bytes()
    image_size = struct.unpack_from('<Q', data, 0x10)[0]
    if data[0x38:0x3c] != b'ARMd' or not len(data) <= image_size <= 1024 * 1024:
        parser.error('invalid ARM64 Image header or image size')
    # Include the zeroed BSS and stack reserved by the linker.
    with args.output.open('xb') as file:
        file.write(data.ljust(image_size, b'\0'))
    print(f'{args.output}: {image_size} bytes')


if __name__ == '__main__':
    main()
