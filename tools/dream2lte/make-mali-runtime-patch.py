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
    for old, new in [
        ('#include <sys/proc.h>\n', '#include <sys/proc.h>\n#include "S8MaliRuntime.h"\n'),
        ('\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n',
         '#if defined(DREAMLTE)\n\tS8Mali::init();\n#endif\n\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n'),
        ('\tuint64_t count = ++fb->fVsyncCount;\n',
         '\tuint64_t count = ++fb->fVsyncCount;\n#if defined(DREAMLTE)\n\tS8Mali::tick(count);\n#endif\n'),
    ]:
        if after.count(old) != 1:
            raise ValueError(f'expected exactly one anchor: {old!r}')
        after = after.replace(old, new)
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       'a/' + path, 'b/' + path))
    header = (root / 'xnu/dream2lte/S8MaliRuntime.h').read_text()
    patch += ''.join(difflib.unified_diff([], header.splitlines(True), '/dev/null',
                                        'b/iokit/Kernel/arm/S8MaliRuntime.h'))
    (root / 'xnu/patches/0125-dream2lte-mali-runtime.patch').write_text(patch, newline='\n')


if __name__ == '__main__':
    main()
