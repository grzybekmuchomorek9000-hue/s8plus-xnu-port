#!/usr/bin/env python3
"""Build the SM-G955F loader for the first XNU handoff trial using Zig."""
import argparse
from pathlib import Path
import struct
import subprocess
import tempfile


def linux_image(elf):
    """Flatten ELF PT_LOAD segments at the exact addresses used by the linker."""
    if elf[:6] != b'\x7fELF\x02\x01':
        raise ValueError('expected an ELF64 little-endian loader')
    entry, phoff = struct.unpack_from('<QQ', elf, 24)
    phentsize, phnum = struct.unpack_from('<HH', elf, 54)
    if entry or phentsize != 56 or not 1 <= phnum <= 32 or phoff + phentsize * phnum > len(elf):
        raise ValueError('unexpected ELF entry point or program headers')
    ranges = []
    for i in range(phnum):
        kind, _, offset, vaddr, paddr, filesz, memsz, _ = struct.unpack_from(
            '<II6Q', elf, phoff + phentsize * i)
        if kind != 1:  # PT_LOAD
            continue
        if (vaddr != paddr or filesz > memsz or offset + filesz > len(elf)
                or paddr + memsz > 1024 * 1024):
            raise ValueError('invalid ELF load segment')
        ranges.append((paddr, memsz, elf[offset:offset + filesz]))
    end = 0
    data = bytearray()
    for address, memsz, contents in sorted(ranges):
        if address < end:
            raise ValueError('ELF load segments overlap')
        data.extend(b'\0' * (address - end))
        data.extend(contents.ljust(memsz, b'\0'))
        end = address + memsz
    if len(data) < 64 or data[0x38:0x3c] != b'ARMd':
        raise ValueError('invalid ARM64 Image header')
    image_size = struct.unpack_from('<Q', data, 0x10)[0]
    if not len(data) <= image_size <= 1024 * 1024:
        raise ValueError('invalid ARM64 Image size')
    return bytes(data).ljust(image_size, b'\0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--touch', action='store_true', help='power and configure the S8+ touch bus')
    parser.add_argument('--mali-probe', action='store_true', help='run the optional Mali power and ID probe')
    parser.add_argument('--mali-job', action='store_true', help='submit a diagnostic Mali WRITE_VALUE command')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    if args.mali_job and not args.mali_probe:
        parser.error('--mali-job requires --mali-probe')
    shim = Path(__file__).resolve().parents[2] / 'shim'
    donor = shim / 'dreamlte'
    zig = ['zig', 'cc', '-target', 'aarch64-linux-gnu', '-mcpu=cortex_a53',
           '-O2', '-ffreestanding', '-nostdlib', '-static', '-fno-pic',
           '-fno-stack-protector', '-mgeneral-regs-only', '-mstrict-align',
           '-Wall', '-Wextra', '-Werror', '-Wl,-e,_head', '-Wl,--build-id=none',
           '-Wl,-T,' + str(donor / 'loader.ld')]
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)

        def build(name, *sources):
            elf = work / f'{name}.elf'
            subprocess.run(zig + [str(s) for s in sources] + ['-o', str(elf)],
                           cwd=work, check=True)
            return linux_image(elf.read_bytes())

        touch = ['-DDREAM2LTE_TOUCH', donor / 'pmic.c', donor / 'touch_probe.c'] if args.touch else []
        mali = ['-DDREAM2LTE_MALI_PROBE', shim / 'dream2lte/mali_probe.c'] if args.mali_probe else []
        if args.mali_job:
            mali += ['-DDREAM2LTE_MALI_JOB', shim / 'dream2lte/mali_job_probe.c']
        inner = build('inner', '-DDREAM2LTE_BRINGUP', *touch, *mali, donor / 'start.S',
                      donor / 'loader.c', shim / 'fdt.c')
        if len(inner) % 16:
            parser.error('inner Image is not 16-byte aligned')
        inner_path = work / 'inner-image.bin'
        inner_path.write_bytes(inner)
        # Make the embedded input part of Zig's cache key.
        data = build('outer', f'-DINNER_IMAGE_PATH="{inner_path.as_posix()}"',
                     shim / 'dream2lte/align-loader.S')
        if inner not in data or b'S8PLUS XNU HANDOFF TEST' not in data:
            parser.error('wrapper is missing the S8+ loader')
    with args.output.open('xb') as file:
        file.write(data)
    print(f'{args.output}: {len(data)} bytes; relocation wrapper + {len(inner)}-byte loader')


if __name__ == '__main__':
    main()
