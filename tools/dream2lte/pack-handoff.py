#!/usr/bin/env python3
"""Package the first S8+ XNU test using its own captured Samsung BOOT header."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('kernel_artifact', type=Path, help='extracted xnu-kernel-dream2lte')
    parser.add_argument('output', type=Path)
    parser.add_argument('--loader', type=Path, help='use a specific built loader revision')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    files = args.capture
    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads((files / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('model'), manifest.get('device')) != ('SM-G955F', 'dream2lte'):
        parser.error('expected a capture from an SM-G955F')
    if (args.kernel_artifact / 'target-board.txt').read_text().strip() != 'dream2lte':
        parser.error('expected the dedicated S8+ GitHub Actions artifact')
    kernel = args.kernel_artifact / 'kernel.development.a5xelte'
    checksums = (args.kernel_artifact / 'SHA256SUMS').read_text().splitlines()
    expected = f'{hashlib.sha256(kernel.read_bytes()).hexdigest()}  {kernel.name}'
    if expected not in checksums:
        parser.error('kernel SHA-256 does not match the build manifest')
    boot = files / 'oneui-boot.img'
    stock = boot.read_bytes()
    loader = args.loader or files / 'xnu-handoff-Image'
    image = loader.read_bytes()
    if (len(stock) != 40960 * 1024 or not stock.startswith(b'ANDROID!')
            or image[0x38:0x3c] != b'ARMd'
            or b'S8PLUS XNU HANDOFF TEST' not in image):
        parser.error('unexpected One UI BOOT backup or S8+ loader')
    cmdline = ('debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress '
               'wdt=-1 wdt_test=no_user_checkin a5touchprobe=0 s8touch=0 a5keys=0 '
               'sweetbattery=0 sweetfb_native=1')
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        adt = work / 'dream2lte.adt'
        payload = work / 'payload.bin'
        candidate = work / 'BOOT.img'
        subprocess.run([sys.executable, str(repo / 'tools/mkadt_dreamlte.py'), str(adt),
                        '--board', 'dream2lte'], check=True)
        subprocess.run([sys.executable, str(repo / 'tools/mkpayload.py'), str(kernel),
                        str(adt), str(payload), '--cmdline', cmdline], check=True)
        subprocess.run([sys.executable, str(repo / 'tools/dreamlte_mkboot.py'), str(boot),
                        str(loader), str(payload), str(candidate)], check=True)
        packed = candidate.read_bytes()
    kernel_size, _, ramdisk_size, _, _, _, _, page = struct.unpack_from('<8I', packed, 8)
    offset = page + (kernel_size + page - 1) // page * page
    if (len(packed) > len(stock) or not packed.startswith(b'ANDROID!')
            or packed[offset:offset + 8] != b'SWEETXNU'
            or ramdisk_size < 1024 or kernel_size != len(image)):
        parser.error('packed BOOT failed header or size validation')
    with args.output.open('xb') as file:
        file.write(packed)
    print(f'{args.output}: {len(packed)} bytes, SHA-256 {hashlib.sha256(packed).hexdigest()}')


if __name__ == '__main__':
    main()
