#!/usr/bin/env python3
"""Embed the verified offline G71 compiler fixture in the XNU trial."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def shader(artifact, name, symbol, size, preload, expected):
    metadata = json.loads((artifact / (name + '.json')).read_text())
    binary = (artifact / (name + '.bin')).read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    if (metadata != {'gpu_id': '0x60a00001', 'bytes': size, 'work_registers': 64,
                     'preload': preload, 'tls': 0, 'wls': 0}
            or len(binary) != size or digest != expected):
        raise ValueError(f'unexpected {name} compiler artifact')
    words = struct.unpack(f'<{size // 4}I', binary)
    text = f'/* SHA-256 {digest} */\nstatic const uint32_t {symbol}[{len(words)}] = {{\n'
    for i in range(0, len(words), 4):
        text += '\t' + ', '.join(f'0x{w:08x}u' for w in words[i:i+4]) + ',\n'
    return text + '};\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    args = parser.parse_args()
    revision = 'e24dc5bd1e7fe6101bdc866fb16a15a8fcae1aae'
    if (args.artifact / 'mesa-revision.txt').read_text().strip() != revision:
        raise ValueError('unexpected Mesa revision')
    text = (f'/* SPDX-License-Identifier: MIT\n * Generated from mali-copy-shader.c by '
            f'Mesa {revision}, GPU 0x60a00001.\n */\n')
    text += shader(args.artifact, 'g71-copy16', 's8_mali_copy_shader', 160, '0x0',
                   'f69885686735ce54ee3b49bb9e6537c22d6ce42ee2765281ef254d71508ca59b')
    text += shader(args.artifact, 'g71-rectangle', 's8_mali_rectangle_shader', 304, '0x3000000000000000',
                   '819ab9b35721fecc1513f742ffe54062eb920e589e28356c1c2f35ed80ff3fd0')
    output = Path(__file__).resolve().parents[2] / 'xnu/dream2lte/S8MaliCopyShader.h'
    output.write_text(text, newline='\n')


if __name__ == '__main__':
    main()
