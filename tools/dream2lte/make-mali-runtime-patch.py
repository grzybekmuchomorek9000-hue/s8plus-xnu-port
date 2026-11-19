#!/usr/bin/env python3
"""Generate the opt-in runtime GPU patch from a post-0124 framebuffer source."""
import argparse
import difflib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('framebuffer', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    path = 'iokit/Kernel/arm/SweetFramebuffer.cpp'
    before = args.framebuffer.read_text()
    after = before
    board = '#if defined(DREAMLTE) && DREAM2LTE_BRINGUP\n'
    for old, new in [
        ('#include <sys/proc.h>\n', '#include <sys/proc.h>\n#include "S8MaliRuntime.h"\n'
         '#include "S8MaliPanel.h"\n#include "S8MaliGlesCaps.h"\n#include "S8MaliCompositor.h"\n'),
        ('\tuint64_t      fMinGap;', board + '\tbool          fMaliAutoRate, fMaliFastRate;\n#endif\n\tuint64_t      fMinGap;'),
        ('\tPE_parse_boot_argn("sweetfb_hz", &hz, sizeof(hz));\n', board +
         '\tfMaliAutoRate = !PE_parse_boot_argn("sweetfb_hz", &hz, sizeof(hz));\n'
         '\tfMaliFastRate = false;\n#else\n\tPE_parse_boot_argn("sweetfb_hz", &hz, sizeof(hz));\n#endif\n'),
        ('\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n',
         board + '\tS8Mali::init();\n\tif (S8Mali::ready) S8Mali::logGlesCaps();\n'
         '\tS8Mali::enablePanel(fPhys, fWidth, fHeight, fRowBytes);\n'
         '\tsweet_mali_publish_compositor(this, fPresentLock);\n'
         '#endif\n\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n'),
        ('\tuint64_t count = ++fb->fVsyncCount;\n',
         '\tuint64_t count = ++fb->fVsyncCount;\n' + board +
         '\tif (count % 600 == 0) {\n\t\tIOLockLock(fb->fPresentLock);\n\t\tS8Mali::tick(count);\n'
         '\t\tIOLockUnlock(fb->fPresentLock);\n\t}\n#endif\n'),
        ('\t\tif (step <= 6) {\n\t\t\tfor (uint32_t y', '\t\tif (step <= 6) {\n' + board +
         '\t\t\tIOLockLock(fb->fPresentLock);\n\t\t\tif (!S8Mali::panelHeld)\n#endif\n\t\t\tfor (uint32_t y'),
        ('\t\t}\n\t\tsender->setTimeoutUS', board + '\t\t\tIOLockUnlock(fb->fPresentLock);\n#endif\n\t\t}\n\t\tsender->setTimeoutUS'),
        ('\tuint32_t mask = get32(rec, 0x104);\n', '\tuint32_t mask = get32(rec, 0x104);\n' +
         board + '\tbool copiedOnGpu = false, copiedOnCpu = false;\n#endif\n'),
        ('\t\tif (src != 0 && bpe == 4) {\n', '\t\tif (src != 0 && bpe == 4) {\n' + board +
         '\t\t\tif (S8Mali::presentPanel(s, fPixels, sx, sy, dx, dy, w, h)) {\n'
         '\t\t\t\tcopiedOnGpu = !S8Mali::panelHeld;\n\t\t\t\ts->release();\n\t\t\t\tcontinue;\n\t\t\t}\n'
         '\t\t\tcopiedOnCpu = true;\n#endif\n'),
        ('\t\ts->release();\n\t}\n\tIOLockUnlock(fPresentLock);', '\t\ts->release();\n\t}\n' + board +
         '\tif (fMaliAutoRate) {\n'
         '\t\tunsigned hz = S8Mali::presentationHz(copiedOnGpu, copiedOnCpu, fMaliFastRate ? 30 : 10);\n'
         '\t\tbool fast = hz == 30;\n'
         '\t\tif (fast != fMaliFastRate) {\n\t\t\tfMaliFastRate = fast;\n'
         '\t\t\tIOLockLock(fLock);\n'
         '\t\t\tfFlushTicks = 60 / hz;\n\t\t\tfMinGap = 26000000ULL / hz;\n'
         '\t\t\tIOLockUnlock(fLock);\n\t\t\tif (S8Mali::frames <= 3 || !S8Mali::ready)\n'
         '\t\t\t\tIOLog("S8 MALI: presentation target %u Hz\\n", hz);\n\t\t}\n\t}\n'
         '#endif\n\tIOLockUnlock(fPresentLock);'),
    ]:
        if after.count(old) != 1:
            raise ValueError(f'expected exactly one anchor: {old!r}')
        after = after.replace(old, new)
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       'a/' + path, 'b/' + path))
    for name in ('S8MaliRuntime.h', 'S8MaliCopyShader.h', 'S8MaliPanel.h',
                 'S8MaliGlesCaps.h', 'S8MaliBuffers.h', 'S8MaliCompositor.h'):
        header = (root / 'xnu/dream2lte' / name).read_text()
        patch += ''.join(difflib.unified_diff([], header.splitlines(True), '/dev/null',
                                            'b/iokit/Kernel/arm/' + name))
    (root / 'xnu/patches/0125-dream2lte-mali-runtime.patch').write_text(patch, newline='\n')


if __name__ == '__main__':
    main()
