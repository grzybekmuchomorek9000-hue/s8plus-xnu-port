#!/usr/bin/env bash
# Build the standalone GLES test with the S7 port's open Mesa transport.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$1" "$2"
work=$(cd "$1" && pwd)
out=$(cd "$2" && pwd)
donor_rev=2aafd7a0a1422a538399c449e160219b8aeba65e

git init -q "$work/donor"
git -C "$work/donor" sparse-checkout set --no-cone '/gpu/mesa/' '/gpu/hakios-gl/LICENSE.hakios'
git -C "$work/donor" fetch -q --depth 1 https://github.com/wardevsa/ios-a5xelte.git "$donor_rev"
git -C "$work/donor" checkout -q --detach FETCH_HEAD

mkdir -p "$work/build"
(
  cd "$work/build"
  curl -fLsS --retry 3 https://archive.mesa3d.org/mesa-25.0.7.tar.xz -o mesa-25.0.7.tar.xz
  curl -fLsS --retry 3 https://dri.freedesktop.org/libdrm/libdrm-2.4.124.tar.xz -o libdrm-2.4.124.tar.xz
  shasum -a 256 -c <<'SUMS'
592272df3cf01e85e7db300c449df5061092574d099da275d19e97ef0510f8a6  mesa-25.0.7.tar.xz
ac36293f61ca4aafaf4b16a2a7afff312aa4f5c37c9fbd797de9e3c0863ca379  libdrm-2.4.124.tar.xz
SUMS
  tar -xf mesa-25.0.7.tar.xz
  tar -xf libdrm-2.4.124.tar.xz
  mv mesa-25.0.7 mesa
  mv libdrm-2.4.124 libdrm
)

# Reuse the Darwin OSMesa/libdrm build. Leave the EAGL injection scripts and
# the S7's installer out of this package. Code generation must target ARMv8.0.
mesa_build="$work/donor/gpu/mesa/build-mesa.sh"
python3 - "$mesa_build" <<'PY'
from pathlib import Path
import sys

script = Path(sys.argv[1])
source = script.read_text()
inline = 'clang -arch arm64 -miphoneos-version-min='
cross = "'-miphoneos-version-min=$MIN_IOS', '-isysroot', '$SDK'"
assert source.count(inline) == 1 and source.count(cross) == 6
source = source.replace(inline, 'clang -arch arm64 -mcpu=cortex-a53 -miphoneos-version-min=')
script.write_text(source.replace(cross, "'-mcpu=cortex-a53', " + cross))
PY
MESA_TAG=mesa-25.0.7 LIBDRM_TAG=libdrm-2.4.124 bash "$mesa_build" "$work/build" "$work/stage"
mkdir -p "$out/lib"
cp "$work/stage/lib/"*.dylib "$out/lib/"

xcrun --sdk iphoneos clang -arch arm64 -mcpu=cortex-a53 -miphoneos-version-min=16.0 \
  -isysroot "$(xcrun --sdk iphoneos --show-sdk-path)" -std=c11 -Wall -Wextra -Werror -O2 \
  -I "$work/build/mesa/include" "$here/gles-layer-test.c" -L "$out/lib" -lOSMesa.8 \
  -Wl,-rpath,@executable_path/lib -o "$out/gles-layer-test"
codesign --force --sign - "$out/gles-layer-test"
codesign --verify --strict "$out/gles-layer-test"
xcrun vtool -show-build "$out/gles-layer-test"
xcrun otool -L "$out/gles-layer-test"
printf '%s\n' "$donor_rev" > "$out/mesa-transport-revision.txt"
cp "$work/donor/gpu/hakios-gl/LICENSE.hakios" "$out/LICENSE.hakios"
(cd "$out" && shasum -a 256 gles-layer-test lib/*.dylib > SHA256SUMS)
