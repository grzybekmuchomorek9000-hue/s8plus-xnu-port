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
        ('#include <sys/proc.h>\n', '#include <sys/proc.h>\n#include "S8MaliRuntime.h"\n#include "S8MaliPanel.h"\n'),
        ('\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n',
         board + '\tS8Mali::init();\n\tS8Mali::enablePanel(fPhys, fWidth, fHeight, fRowBytes);\n'
         '#endif\n\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n'),
        ('\tuint64_t count = ++fb->fVsyncCount;\n',
         '\tuint64_t count = ++fb->fVsyncCount;\n' + board +
         '\tif (count % 600 == 0) {\n\t\tIOLockLock(fb->fPresentLock);\n\t\tS8Mali::tick(count);\n'
         '\t\tIOLockUnlock(fb->fPresentLock);\n\t}\n#endif\n'),
        ('\t\tif (step <= 6) {\n\t\t\tfor (uint32_t y', '\t\tif (step <= 6) {\n' + board +
         '\t\t\tIOLockLock(fb->fPresentLock);\n\t\t\tif (!S8Mali::panelHeld)\n#endif\n\t\t\tfor (uint32_t y'),
        ('\t\t}\n\t\tsender->setTimeoutUS', board + '\t\t\tIOLockUnlock(fb->fPresentLock);\n#endif\n\t\t}\n\t\tsender->setTimeoutUS'),
        ('\t\tif (src != 0 && bpe == 4) {\n', '\t\tif (src != 0 && bpe == 4) {\n' + board +
         '\t\t\tif (S8Mali::presentPanel(s, fPixels, sx, sy, dx, dy, w, h)) {\n'
         '\t\t\t\ts->release();\n\t\t\t\tcontinue;\n\t\t\t}\n#endif\n'),
    ]:
        if after.count(old) != 1:
            raise ValueError(f'expected exactly one anchor: {old!r}')
        after = after.replace(old, new)
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       'a/' + path, 'b/' + path))
    for name in ('S8MaliRuntime.h', 'S8MaliCopyShader.h', 'S8MaliPanel.h'):
        header = (root / 'xnu/dream2lte' / name).read_text()
        patch += ''.join(difflib.unified_diff([], header.splitlines(True), '/dev/null',
                                            'b/iokit/Kernel/arm/' + name))
    (root / 'xnu/patches/0125-dream2lte-mali-runtime.patch').write_text(patch, newline='\n')


if __name__ == '__main__':
    main()
