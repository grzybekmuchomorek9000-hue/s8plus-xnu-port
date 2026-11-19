#!/usr/bin/env python3
"""Exercise the compiled ARM64 GPU driver against a fault-injecting MMIO model.

Requires Zig and Unicorn. This verifies control flow and DMA bounds, not GPU
execution or visual correctness. Real device readback is still mandatory.
"""
import argparse
import hashlib
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
#define DREAM2LTE_BRINGUP 1
static void *kernel_task;
static bool invalidRenderPage;
enum { kIODirectionInOut = 3, kIOMemoryPhysicallyContiguous = 16, kIOMemoryMapperNone = 0x800 };
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
    unsigned region;
    static IOBufferMemoryDescriptor *inTaskWithOptions(void *, unsigned options, unsigned size, unsigned) {
        static IOBufferMemoryDescriptor runtime{0}, panel{1}, tables{5}, render[3] = {{6},{7},{8}};
        static unsigned allocated;
        if (!(options & kIOMemoryPhysicallyContiguous))
            return allocated < 3 ? &render[allocated++] : NULL;
        if (size == 7 * 4096) return &runtime;
        return size == 32 * 4096 ? &tables : &panel;
    }
    uint64_t getPhysicalSegment(unsigned off, IOByteCount *n, unsigned = 0) {
        if (region < 2) {
            *n = (region ? 18 : 7) * 4096;
            return region ? 0x880200000ULL : 0x880100000ULL;
        }
        if (region == 5) {
            *n = 32 * 4096;
            return 0x880300000ULL;
        }
        *n = 4096;
        if (region >= 6) {
            if (invalidRenderPage && region == 7 && off == 4096) return 0;
            return 0x882000000ULL + ((region - 6) << 20) + 2ULL * off;
        }
        if (region == 4) return 0xcc000000;
        return (region == 2 ? 0x881000000ULL : 0x885000000ULL) + 2ULL * off;
    }
    void *getBytesNoCopy() {
        if (region == 5) return (void *)(uintptr_t)0x41000000;
        if (region >= 6) return (void *)(uintptr_t)(0x42000000 + ((region - 6) << 20));
        return (void *)(uintptr_t)(0x40000000 + (region == 1 ? 0x10000 : region * 0x2000000));
    }
    IOByteCount getLength() { return region >= 6 ? 7 * 4096 : 17428480; }
    void retain() {}
    void release() {}
};
#define OSDynamicCast(type, obj) (obj)
struct SweetSurface {
    unsigned width, height, offset, bytesPerRow, planeCount, parentID, pixelFormat;
    unsigned bytesPerElement, elementWidth, elementHeight;
    IOBufferMemoryDescriptor *memory;
};
'''

ENTRY = r'''
extern "C" int entry(unsigned mode) {
    invalidRenderPage = mode == 9;
    S8Mali::init();
    if (mode == 5) {
        S8Mali::logGlesCaps();
        uint64_t value = 0;
        return S8Mali::ready &&
               S8Mali::getGpuProperty(0, value) && value == 0x60a0 &&
               S8Mali::getGpuProperty(1, value) && value == 1 &&
               S8Mali::getGpuProperty(2, value) && value == 0x17771777 &&
               S8Mali::getGpuProperty(3, value) && value == 0x100000002ULL &&
               S8Mali::getGpuProperty(17, value) && value == 3 &&
               S8Mali::getGpuProperty(22, value) && value == 1 << 9 &&
               S8Mali::getGpuProperty(38, value) && value == 2 &&
               S8Mali::getGpuProperty(44, value) && value == 31 &&
               !S8Mali::getGpuProperty(45, value);
    }
    if (mode == 3 || mode == 6 || mode == 9)
        return (S8Mali::compositionVerified ? 1 : 0) | (S8Mali::ready ? 2 : 0);
    if (!mode) {
        S8Mali::tick(600); S8Mali::tick(1200);
        return S8Mali::ready;
    }
    S8Mali::enablePanel(0xcc000000, 1440, 2960, 5760);
    IOBufferMemoryDescriptor memory[2] = {{2},{3}};
    SweetSurface s = {1440,2960,0,5888,0,0,0x42475241,4,1,1,&memory[0]};
    if (mode == 2) s.offset = 4;
    if (mode == 4) memory[0].region = 4;
    int flags = S8Mali::presentPanel(&s, 0x48000000, 0,0,0,0,1440,2960) ? 1 : 0;
    s.memory = &memory[1];
    if (S8Mali::presentPanel(&s, 0x48000000, 0,0,0,0,1440,2960)) flags |= 2;
    if (S8Mali::panelHeld) flags |= 4;
    if (S8Mali::presentationHz(true, false, 10) != (S8Mali::panelHeld ? 10u : 30u) ||
        S8Mali::presentationHz(false, false, 30) != (S8Mali::panelHeld ? 10u : 30u) ||
        S8Mali::presentationHz(true, true, 30) != 10 ||
        S8Mali::presentationHz(false, true, 30) != 10) return 8;
    return flags;
}
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


def check(elf, scenario, reference):
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
    uc.mem_map(0x41000000, 32 * 4096)
    for base in (0x42000000, 0x42100000, 0x42200000):
        uc.mem_map(base, 7 * 4096)
    is_panel = scenario.startswith('panel-')
    panel_frames = 0
    if is_panel:
        uc.mem_map(cpu + 0x10000, 18 * 4096)
        for region, pitch in ((0x44000000, 5888), (0x46000000, 5888), (0x48000000, 5760)):
            uc.mem_map(region, (pitch * 2960 + 4095) & ~4095)
            if region != 0x48000000:
                row = bytes((i ^ (region >> 24)) & 255 for i in range(pitch))
                frame = b''.join(bytes([y & 255]) + row[1:] for y in range(2960))
                uc.mem_write(region, frame)
    uc.mem_map(0x500000, 0x10000)
    uc.mem_write(0x164840e4, struct.pack('<I', 15))
    values = {0: 0x60a00001, 0x14: 36 << 8, 0x1c: 7,
              0xc0: 1 << 9, 0xc4: (1 << 2) | (1 << 4), 0x120: 1, 0x100: 1}
    if scenario == 'missing-compute': values[0xc4] = 1 << 2
    if scenario == 'compute-slot2': values[0xc4], values[0xc8] = 1 << 2, 1 << 4
    if scenario == 'busy': values[0x1010] = 1
    if scenario == 'wrong-id': values[0] = 0
    if scenario == 'gles-capabilities':
        values.update({0x100: 0x17771777, 0x110: 2, 0x114: 1, 0x120: 0x100001, 0x300: 3})
    jobs = []
    clock = 0
    complete = False
    as_ready_after = 0
    last_slot = 0

    def read32(addr): return struct.unpack('<I', uc.mem_read(addr, 4))[0]
    def write32(addr, v): uc.mem_write(addr, struct.pack('<I', v))

    def blend_row(front, back, output, width):
        front_row = uc.mem_read(front, width*4)
        back_row = uc.mem_read(back, width*4)
        out = bytearray(width*4)
        for j in range(width*4):
            alpha = front_row[j | 3]
            out[j] = round(front_row[j] + back_row[j] * (255-alpha) / 255)
        uc.mem_write(output, bytes(out))

    def io(_uc, access, address, size, value, _):
        from unicorn import UC_MEM_READ
        nonlocal complete, as_ready_after, last_slot, panel_frames
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
            if value == 5 and jobs:
                if scenario == 'panel-flush-timeout' and jobs[-1] == 'panel':
                    values[0x2428] = 1
                if scenario == 'render-flush-timeout' and jobs[-1] == 'render':
                    values[0x2428] = 1
            if scenario == 'deadline-completion': as_ready_after = clock + 2600000
        if off in (0x1860, 0x18e0, 0x1960) and value == 1:
            slot = (off - 0x1860) // 0x80
            last_slot = slot
            desc = cpu + 4 * 4096
            kind = (read32(desc + 16) >> 1) & 127
            assert (slot, kind) in ((1, 2), (2 if scenario == 'compute-slot2' else 1, 4), (0, 9))
            # Walk the real four-level descriptor emitted by the driver.
            for i in range(3):
                assert struct.unpack('<Q', uc.mem_read(cpu + i * 4096, 8))[0] == (0x880100000 + (i+1)*4096) | 3
            for i in (1, 2):
                assert struct.unpack('<Q', uc.mem_read(cpu + 3*4096 + i*8, 8))[0] == (0x880100000 + (i+3)*4096) | 0x2c1
            assert read32(cpu + 3*4096 + 3*8) == 0  # canary is unmapped
            rectangle = kind == 4 and read32(desc + 0x530) != 0
            blend = rectangle and read32(desc + 0x718) != 0
            render = blend and read32(desc + 0x700) >= 0x02000000
            panel = rectangle and not blend and read32(desc + 0x700) == 0x02000000
            if kind == 4 and reference:
                packet = bytes(uc.mem_read(desc, 192) + uc.mem_read(desc + 0x500, 64) +
                               uc.mem_read(desc + 0x600, 32))
                name = ('copy16' if not rectangle else 'blend' if blend else 'panel' if panel else 'rectangle')
                expected = reference[name]
                if packet != expected:
                    diff = [(i, a, b) for i, (a, b) in enumerate(zip(packet, expected)) if a != b]
                    raise AssertionError((name, 'driver vs Mesa packet bytes', diff[:12]))
            jobs.append('render' if render else 'blend' if blend else 'panel' if panel else 'rect' if rectangle else kind)
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
            elif kind == 4 and not rectangle:
                assert read32(desc + 32) == read32(desc + 36) == 0
                assert read32(desc + 40) == 3 << 26 and read32(desc + 64) == 3
                assert struct.unpack('<Q', uc.mem_read(desc + 120, 8))[0] == 0x1500
                assert struct.unpack('<Q', uc.mem_read(desc + 176, 8))[0] == 0x1600
                assert struct.unpack('<Q', uc.mem_read(desc + 0x500, 8))[0] == 0x1800
                assert read32(desc + 0x604) == 31
                shader = bytes(uc.mem_read(desc + 0x800, 160))
                assert hashlib.sha256(shader).hexdigest() == 'f69885686735ce54ee3b49bb9e6537c22d6ce42ee2765281ef254d71508ca59b'
                assert read32(target + 0x200) == 0xdeadbeef
                if scenario != 'copy-no-store':
                    uc.mem_write(target + 0x200, bytes(uc.mem_read(target, 16)))
                if scenario == 'copy-overrun': write32(target + 0x210, 0)
                if scenario == 'copy-source-damaged': write32(target, 0)
            elif rectangle:
                size, digest = (
                    (672, 'be818f7610edc1690310232271fa8723250740318884c437be6b933efc8ef1b1')
                    if blend else
                    (304, '819ab9b35721fecc1513f742ffe54062eb920e589e28356c1c2f35ed80ff3fd0')
                )
                shader = bytes(uc.mem_read(desc + 0x800, size))
                assert hashlib.sha256(shader).hexdigest() == digest
                assert read32(desc + 0x530) == 3 << 12 and read32(desc + 40) == 8 << 26
                assert (read32(desc + 120), read32(desc + 176), read32(desc + 0x500)) == (0x1500, 0x1600, 0x1800)
                src, dst, sp, dp, w, h = struct.unpack('<6I', uc.mem_read(desc + 0x700, 24))
                inv, shifts = struct.unpack('<II', uc.mem_read(desc + 32, 8))
                fields = [0, shifts & 31, (shifts >> 5) & 31, (shifts >> 10) & 63,
                          (shifts >> 16) & 63, (shifts >> 22) & 63, 32]
                dims = [((inv >> a) & ((1 << (b-a))-1)) + 1 for a, b in zip(fields, fields[1:])]
                assert dims == [8, 4, 1, (w+7)//8, (h+3)//4, 1] and shifts >> 28 == 5
                if render:
                    back, bp = struct.unpack('<II', uc.mem_read(desc + 0x718, 8))
                    assert (src, back, dst) == (0x02001000, 0x02009000, 0x02011000)
                    assert (sp, bp, dp, w, h) == (4096, 4112, 4080, 13, 7)
                    for n in range(32):
                        l2 = struct.unpack('<Q', uc.mem_read(cpu + 8192 + (16+n)*8, 8))[0]
                        assert l2 == (0x880300000 + n*4096) | 3
                    for n in range(32 * 512):
                        pte = struct.unpack('<Q', uc.mem_read(0x41000000 + n*8, 8))[0]
                        offset = n % 8
                        region = n // 8
                        if not offset or region > 2:
                            assert not pte, ('unallocated GPU page was mapped', n)
                        else:
                            phys = 0x882000000 + region*0x100000 + (offset-1)*8192
                            flags = 0x2c1 if region == 2 else 0x241
                            assert pte == phys | flags | (3 << 53)
                    for y in range(h):
                        blend_row(0x42000000 + y*sp, 0x42100000 + y*bp, 0x42200000 + y*dp, w)
                    if scenario == 'render-padding':
                        write32(0x42200000 + dp + w*4, 0)
                elif src == 0x2040:
                    assert (dst, sp, dp, w, h) == (0x2800, 80, 68, 13, 7)
                    if scenario == 'rect-wrong-pitch': dp = sp
                    if blend:
                        back, bp = struct.unpack('<II', uc.mem_read(desc + 0x718, 8))
                        assert (back, bp) == (0x2480, 76)
                        for y in range(h):
                            blend_row(target + src-0x2000 + y*sp, target + back-0x2000 + y*bp,
                                      target + dst-0x2000 + y*dp, w)
                        if scenario == 'blend-math-error': write32(target + dst-0x2000, 0)
                        if scenario == 'blend-input-damage': write32(target + back-0x2000, 0)
                        if scenario == 'blend-boundary': write32(target + dst-0x2000 + w*4, 0)
                    else:
                        for y in range(h):
                            row = bytes(uc.mem_read(target + src-0x2000 + y*sp, w*4))
                            uc.mem_write(target + dst-0x2000 + y*dp, row)
                        if scenario == 'rect-tail': write32(target + dst-0x2000 + w*4, 0)
                else:
                    assert is_panel and (src, dst, sp, dp, w, h) == (0x2000000, 0x4000000, 5888, 5760, 1440, 2960)
                    index = panel_frames
                    panel_frames += 1
                    for va, base, length, physical_step, flags in (
                            (src, (0x881000000, 0x885000000)[index], sp*h, 8192, 0x241),
                            (dst, 0xcc000000, dp*h, 4096, 0x281)):
                        for offset in range(0, length, 4096):
                            l2 = struct.unpack('<Q', uc.mem_read(cpu + 8192 + ((va+offset) >> 21)*8, 8))[0]
                            assert l2 & 0xfff == 3
                            leaf = cpu + 0x10000 + (l2 & ~0xfff) - 0x880200000
                            pte = struct.unpack('<Q', uc.mem_read(leaf + (((va+offset) >> 12) & 511)*8, 8))[0]
                            assert pte == ((base + offset//4096*physical_step) | flags | (3 << 53))
                    source = bytes(uc.mem_read(0x44000000 + index*0x2000000, sp*h))
                    output = b''.join(source[y*sp:y*sp+dp] for y in range(h))
                    uc.mem_write(0x48000000, output)
                    if scenario == 'panel-readback-failure': write32(0x48000000 + dp*h - 4, 0)
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
    for va, data, _ in segments:
        for i in range(0, len(data)-3, 4):
            if struct.unpack_from('<I', data, i)[0] & ~31 == 0xd53be020:
                uc.hook_add(UC_HOOK_CODE, code, begin=va+i, end=va+i)
    uc.reg_write(UC_ARM64_REG_SP, 0x50fff0)
    uc.reg_write(UC_ARM64_REG_LR, 0x2ff000)
    special = {'panel-offset': 2, 'panel-invalid-mapping': 4, 'gles-capabilities': 5,
               'render-success': 6, 'render-padding': 6, 'render-flush-timeout': 6,
               'render-invalid-page': 9}
    mode = special.get(scenario, int(is_panel))
    if scenario.startswith('blend-'):
        mode = 3
    uc.reg_write(UC_ARM64_REG_X0, mode)
    uc.emu_start(entry, 0x2ff000, count=10000000)
    assert uc.reg_read(UC_ARM64_REG_PC) == 0x2ff000, (scenario, 'did not terminate')
    result = uc.reg_read(UC_ARM64_REG_X0)
    if mode == 5:
        assert result == 1 and jobs == [2, 9, 4, 'rect', 'blend', 'render']
        print(scenario + ': passed')
        return
    if is_panel:
        expected = {'panel-frames': (3, 2), 'panel-offset': (0, 0),
                    'panel-invalid-mapping': (0, 0),
                    'panel-readback-failure': (0, 1), 'panel-flush-timeout': (7, 1)}
        assert (result, panel_frames) == expected[scenario], (scenario, result, panel_frames)
        if panel_frames and scenario != 'panel-flush-timeout':
            assert values.get(0x2400, 0) == values.get(0x2428, 0) == 0
        print(scenario + ': passed')
        return
    if mode == 3:
        success = scenario == 'blend-success'
        assert jobs == [2, 9, 4, 'rect', 'blend'] + (['render'] if success else [])
        assert result == (3 if success else 2), (scenario, result)
        print(scenario + ': passed')
        return
    if mode in (6, 9):
        expected = {'render-success': 3, 'render-padding': 2, 'render-flush-timeout': 0,
                    'render-invalid-page': 2}
        assert result == expected[scenario], (scenario, result)
        assert jobs == [2, 9, 4, 'rect', 'blend'] + (['render'] if mode == 6 else [])
        held = scenario == 'render-flush-timeout'
        assert any(uc.mem_read(0x41000000, 32 * 4096)) == held
        assert bool(values.get(0x2400, 0)) == held
        print(scenario + ': passed')
        return
    success_cases = ('success', 'deadline-completion', 'done-without-irq', 'late-irq', 'compute-slot2')
    assert bool(result) == (scenario in success_cases), (scenario, result)
    if scenario in success_cases: assert jobs == [2, 9, 4, 'rect', 'blend', 'render', 9, 9] and complete
    if scenario == 'missing-compute': assert jobs == [2, 9]
    if scenario.startswith('copy-'): assert jobs == [2, 9, 4]
    if scenario.startswith('rect-'): assert jobs == [2, 9, 4, 'rect']
    if scenario in ('busy', 'wrong-id', 'power-timeout', 'mmu-timeout'): assert not jobs
    if scenario != 'mmu-timeout': assert values.get(0x2400, 0) == 0
    if scenario not in ('busy', 'wrong-id', 'power-timeout', 'mmu-timeout'):
        assert values.get(0x140, 0) == 0 and values.get(0x160, 0) == 0
    print(scenario + ': passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unicorn-path', type=Path)
    parser.add_argument('--mesa-artifact', type=Path, help='compare job, shader state and storage with Mesa packers')
    args = parser.parse_args()
    if args.unicorn_path:
        import sys
        sys.path.insert(0, str(args.unicorn_path.resolve()))
    reference = {}
    if args.mesa_artifact:
        copy = (args.mesa_artifact / 'copy16-packets.bin').read_bytes()
        rectangles = (args.mesa_artifact / 'rectangle-packets.bin').read_bytes()
        blend = (args.mesa_artifact / 'blend-packets.bin').read_bytes()
        if len(copy) != 288 or len(rectangles) != 576 or len(blend) != 576:
            raise ValueError('unexpected reference packet lengths')
        reference = {'copy16': copy, 'rectangle': rectangles[:288], 'panel': rectangles[288:],
                     'blend': blend[:288]}
    root = Path(__file__).resolve().parents[2]
    header = root / 'xnu/dream2lte/S8MaliRuntime.h'
    with tempfile.TemporaryDirectory() as name:
        work = Path(name)
        (work / 'IOKit').mkdir()
        (work / 'IOKit/IOBufferMemoryDescriptor.h').write_text('/* harness stub */\n')
        panel = header.with_name('S8MaliPanel.h')
        caps = header.with_name('S8MaliGlesCaps.h')
        headers = ''.join(f'\n#include "{f.as_posix()}"' for f in (header, panel, caps))
        (work / 'check.cpp').write_text(STUB + headers + '\n' + ENTRY)
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
                         'job-timeout', 'mmu-fault', 'overrun', 'missing-compute',
                         'copy-no-store', 'copy-overrun', 'copy-source-damaged', 'rect-wrong-pitch',
                         'rect-tail', 'compute-slot2', 'blend-success', 'blend-math-error',
                         'blend-input-damage', 'blend-boundary', 'panel-frames', 'panel-offset',
                         'panel-readback-failure', 'panel-flush-timeout', 'panel-invalid-mapping',
                         'gles-capabilities', 'render-success', 'render-padding', 'render-flush-timeout',
                         'render-invalid-page'):
            check(data, scenario, reference)


if __name__ == '__main__':
    main()
