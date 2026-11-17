#!/usr/bin/env python3
"""Exercise the compiled ARM64 GPU driver against a fault-injecting MMIO model.

Requires Zig and Unicorn. This verifies control flow and DMA bounds, not GPU
execution or visual correctness. Real device readback is still mandatory.
"""
import argparse
from pathlib import Path
import struct
import subprocess
import tempfile


STUB = r'''
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef unsigned long uintptr_t;
typedef unsigned long vm_offset_t;
typedef unsigned long vm_size_t;
typedef unsigned long long IOByteCount;
#define NULL 0
#define DREAMLTE 1
static void *kernel_task;
enum { kIODirectionInOut = 3, kIOMemoryPhysicallyContiguous = 16 };
static void bzero(void *p, unsigned long n) {
    volatile unsigned char *v = (volatile unsigned char *)p;
    while (n--) *v++ = 0;
}
static void IOLog(const char *, ...) {}
static uint64_t mach_absolute_time() {
    uint64_t t;
    __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
    return t;
}
static bool PE_parse_boot_argn(const char *, void *v, unsigned) {
    *(int *)v = 1; return true;
}
extern "C" vm_offset_t ml_io_map(vm_offset_t p, vm_size_t) { return p; }
class IOBufferMemoryDescriptor {
public:
    static IOBufferMemoryDescriptor *inTaskWithOptions(void *, unsigned, unsigned, unsigned) {
        static IOBufferMemoryDescriptor m; return &m;
    }
    uint64_t getPhysicalSegment(unsigned, IOByteCount *n) { *n = 7 * 4096; return 0x880100000ULL; }
    void *getBytesNoCopy() { return (void *)0x40000000; }
    void release() {}
};
'''


def segments_and_entry(data):
    entry, phoff = struct.unpack_from('<QQ', data, 24)
    entsize, count = struct.unpack_from('<HH', data, 54)
    result = []
    for i in range(count):
        kind, flags, off, va, _, size, memsize, _ = struct.unpack_from('<II6Q', data, phoff + i * entsize)
        if kind == 1 and memsize:
            result.append((va, data[off:off + size], memsize))
    return entry, result


def check(elf, scenario):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_LR
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    entry, segments = segments_and_entry(elf)
    uc.mem_map(0x100000, 0x200000)
    for va, data, size in segments:
        assert 0x100000 <= va and va + size < 0x300000
        uc.mem_write(va, data)
    gpu, cpu = 0x13900000, 0x40000000
    uc.mem_map(gpu, 0x5000)
    uc.mem_map(0x16484000, 4096)
    uc.mem_map(cpu, 0x10000)
    uc.mem_map(0x500000, 0x10000)
    uc.mem_write(0x164840e4, struct.pack('<I', 15))
    values = {0: 0x60a00001, 0x14: 36 << 8, 0x1c: 7,
              0xc0: 1 << 9, 0xc4: 1 << 2, 0x120: 1, 0x100: 1}
    if scenario == 'busy': values[0x1010] = 1
    if scenario == 'wrong-id': values[0] = 0
    jobs = []
    clock = 0
    complete = False
    as_ready_after = 0
    last_slot = 0

    def read32(addr): return struct.unpack('<I', uc.mem_read(addr, 4))[0]
    def write32(addr, v): uc.mem_write(addr, struct.pack('<I', v))

    def io(_uc, access, address, size, value, _):
        from unicorn import UC_MEM_READ
        nonlocal complete, as_ready_after, last_slot
        off = address - gpu
        if access == UC_MEM_READ:
            if off == 0x2428 and scenario == 'deadline-completion':
                values[off] = int(clock < as_ready_after)
            write32(address, values.get(off, 0))
            return
        values[off] = value
        if off == 0x1a0 and scenario != 'power-timeout': values[0x160] = value
        if off == 0x180: values[0x140] = value
        if off == 0x1c0: values[0x140] = 0
        if off == 0x1e0:
            values[0x160] = 0
            if scenario == 'late-irq' and complete:
                values[0x1000] = 1 << last_slot
        if off == 0x1004: values[0x1000] = values.get(0x1000, 0) & ~value
        if off == 0x2004: values[0x2000] = values.get(0x2000, 0) & ~value
        if off == 0x2418:
            values[0x2428] = 1 if scenario == 'mmu-timeout' else 0
            if scenario == 'deadline-completion': as_ready_after = clock + 2600000
        if off in (0x1860, 0x18e0) and value == 1:
            slot = (off - 0x1860) // 0x80
            last_slot = slot
            desc = cpu + 4 * 4096
            kind = (read32(desc + 16) >> 1) & 127
            assert (slot, kind) in ((1, 2), (0, 9))
            # Walk the real four-level descriptor emitted by the driver.
            for i in range(3):
                assert struct.unpack('<Q', uc.mem_read(cpu + i * 4096, 8))[0] == (0x880100000 + (i+1)*4096) | 3
            for i in (1, 2):
                assert struct.unpack('<Q', uc.mem_read(cpu + 3*4096 + i*8, 8))[0] == (0x880100000 + (i+3)*4096) | 0x2c1
            assert read32(cpu + 3*4096 + 3*8) == 0  # canary is unmapped
            jobs.append(kind)
            if scenario == 'job-timeout': return
            values[off] = 0
            if scenario == 'mmu-fault':
                values[0x2000] = 1
                write32(desc, 0xc0)
                return
            values[0x1000] = (0 if scenario in ('done-without-irq', 'late-irq') else
                              1 << (slot + 16) if scenario == 'error-irq' else 1 << slot)
            write32(desc, 1)
            target = cpu + 5*4096
            if kind == 2:
                assert read32(desc + 32) == 0x2000
                assert read32(desc + 40) == 6  # Explicit 32-bit store.
                write32(target, 0)
            else:
                assert read32(desc + 40) == 0x1201
                assert read32(desc + 0x220) == 0x000f000f
                assert read32(desc + 0x2a0) == 0x2000
                assert read32(desc + 0x2a8) == 64
                assert read32(desc + 0x400) == 0x00800080
                color = read32(desc + 0x2b0)
                for i in range(256): write32(target + i*4, color)
            if scenario == 'overrun': write32(cpu + 6*4096, 0)
            complete = True

    def code(_uc, address, size, _):
        nonlocal clock
        insn = read32(address)
        if insn & ~31 == 0xd53be020:  # MRS Xt, CNTPCT_EL0
            clock += 26000
            uc.reg_write(UC_ARM64_REG_X0 + (insn & 31), clock)
            uc.reg_write(UC_ARM64_REG_PC, address + 4)

    uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, io, begin=gpu, end=gpu+0x4fff)
    uc.hook_add(UC_HOOK_CODE, code)
    uc.reg_write(UC_ARM64_REG_SP, 0x50fff0)
    uc.reg_write(UC_ARM64_REG_LR, 0x2ff000)
    uc.emu_start(entry, 0x2ff000, count=2000000)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x2ff000, (scenario, 'did not terminate')
    result = uc.reg_read(UC_ARM64_REG_X0)
    success_cases = ('success', 'deadline-completion', 'done-without-irq', 'late-irq')
    assert bool(result) == (scenario in success_cases), (scenario, result)
    if scenario in success_cases: assert jobs == [2, 9, 9, 9] and complete
    if scenario in ('busy', 'wrong-id', 'power-timeout', 'mmu-timeout'): assert not jobs
    if scenario != 'mmu-timeout': assert values.get(0x2400, 0) == 0
    if scenario not in ('busy', 'wrong-id', 'power-timeout', 'mmu-timeout'):
        assert values.get(0x140, 0) == 0 and values.get(0x160, 0) == 0
    print(scenario + ': passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unicorn-path', type=Path)
    args = parser.parse_args()
    if args.unicorn_path:
        import sys
        sys.path.insert(0, str(args.unicorn_path.resolve()))
    root = Path(__file__).resolve().parents[2]
    header = root / 'xnu/dream2lte/S8MaliRuntime.h'
    with tempfile.TemporaryDirectory() as name:
        work = Path(name)
        (work / 'IOKit').mkdir()
        (work / 'IOKit/IOBufferMemoryDescriptor.h').write_text('/* harness stub */\n')
        (work / 'check.cpp').write_text(STUB + '\n#include "' + header.as_posix() + '"\n' +
            'extern "C" int entry() { S8Mali::init(); S8Mali::tick(600); S8Mali::tick(1200); return S8Mali::ready; }\n')
        elf = work / 'check.elf'
        (work / 'check.ld').write_text('SECTIONS { . = 0x110000; .text : { *(.text*) } .rodata : { *(.rodata*) } .data : { *(.data*) } .bss : { *(.bss*) } }\n')
        subprocess.run(['zig', 'c++', '-target', 'aarch64-freestanding', '-mcpu=cortex_a53',
                        '-std=c++17', '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib',
                        '-fno-exceptions', '-fno-rtti', '-fno-stack-protector', '-mgeneral-regs-only',
                        '-Werror', '-Wall', '-Wextra', '-I', str(work), '-Wl,-e,entry',
                        '-Wl,-T,' + str(work/'check.ld'), str(work/'check.cpp'), '-o', str(elf)], check=True)
        data = elf.read_bytes()
        for scenario in ('success', 'deadline-completion', 'done-without-irq', 'late-irq', 'error-irq',
                         'busy', 'wrong-id', 'power-timeout', 'mmu-timeout',
                         'job-timeout', 'mmu-fault', 'overrun'):
            check(data, scenario)


if __name__ == '__main__':
    main()
