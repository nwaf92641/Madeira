#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
#
# Build d3d8.dll for the 32-bit (i386, WoW64) farm: Direct3D 8 translated to
# Direct3D 9 (third_party/d3d8to9, BSD-2-Clause, with Madeira's changes), which
# on Madeira is DXMT's d3d9.dll (Metal). Called by build/wine-i386/build.sh
# after the DXMT section; can be run alone.
#
#   build/d3d8/build.sh                 -> app/Madeira/i386-windows/d3d8.dll
#   DEST=/tmp/x build/d3d8/build.sh     -> another directory (host tests)
#   TC=/path/to/llvm-mingw/bin WINEBUILD=/path/to/winebuild build/d3d8/build.sh
#
# Why not Wine's d3d8: it is a wined3d frontend, and wined3d has no backend in
# this port (no OpenGL, --without-vulkan), so Direct3DCreate8 returned NULL for
# every Direct3D 8 game (docs/D3D8.md). Why not Winlator's D8VK: it is DXVK's
# d3d8 over DXVK's d3d9, i.e. Vulkan.
#
# The DLL is marked as a Wine builtin (winebuild --builtin), like DXMT's d3d9:
# Madeira's farm directories are the builtin search path, and a module found
# there must carry the builtin marker to be loaded as one.
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$R/third_party/d3d8to9"
TC="${TC:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
DEST="${DEST:-$R/app/Madeira/i386-windows}"
WINEBUILD="${WINEBUILD:-$R/wine/build-i386/tools/winebuild/winebuild}"
OUT="${OUT_DIR:-$R/build/d3d8/out-i386}"
CXX="$TC/i686-w64-mingw32-clang++"
STRIP="$TC/i686-w64-mingw32-strip"

[ -x "$CXX" ] || { echo "llvm-mingw not found at $TC (docs/BUILDING.md)" >&2; exit 1; }
if [ ! -x "$WINEBUILD" ]; then
    WINEBUILD="$(command -v winebuild || true)"
fi
[ -n "$WINEBUILD" ] && [ -x "$WINEBUILD" ] || {
    echo "winebuild not found (build the i386 Wine tree first, or set WINEBUILD)" >&2; exit 1; }

mkdir -p "$OUT" "$DEST"
SOURCES=(
  d3d8to9.cpp d3d8to9_base.cpp d3d8to9_device.cpp d3d8to9_index_buffer.cpp
  d3d8to9_surface.cpp d3d8to9_swap_chain.cpp d3d8to9_texture.cpp
  d3d8to9_vertex_buffer.cpp d3d8to9_volume.cpp d3d8types.cpp interface_query.cpp
)
OBJS=()
for s in "${SOURCES[@]}"; do
    o="$OUT/${s%.cpp}.o"
    "$CXX" -std=c++17 -O2 -DNDEBUG -DD3D8TO9NOLOG -Wall -Wno-delete-non-virtual-dtor \
        -Wno-unknown-pragmas -c "$SRC/source/$s" -o "$o"
    OBJS+=("$o")
done
# libc++ and the mingw runtime are linked statically, so the DLL imports only
# system modules the farm has (d3d9, gdi32, user32, kernel32, ...).
"$CXX" -shared -o "$OUT/d3d8.dll" "${OBJS[@]}" "$SRC/res/d3d8.def" \
    -static -Wl,--enable-stdcall-fixup -ld3d9 -lgdi32 -luser32
"$STRIP" --strip-unneeded "$OUT/d3d8.dll"
"$WINEBUILD" --builtin "$OUT/d3d8.dll"
cp -f "$OUT/d3d8.dll" "$DEST/d3d8.dll"
echo "== installed d3d8.dll (d3d8to9 over d3d9) into $DEST =="
