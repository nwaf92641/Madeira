#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
#
# Build cnc-ddraw (third_party/cnc-ddraw, MIT) for 32-bit DirectDraw games:
# DirectDraw implemented in system memory, presented through Direct3D 9, which
# on Madeira is DXMT's d3d9.dll (Metal). docs/DIRECTDRAW.md.
#
#   build/ddraw/build.sh                 -> app/Madeira/cnc-ddraw/{ddraw.dll,ddraw.ini}
#   DEST=/tmp/x build/ddraw/build.sh     -> another directory (host tests)
#   TC=/path/to/llvm-mingw/bin build/ddraw/build.sh
#
# Not in the i386 farm: Wine's ddraw.dll stays the default. The app links this
# one over C:\windows\syswow64\ddraw.dll for a launch that sets
# MADEIRA_DDRAW=cnc (recipe "cnc-ddraw", or env.MADEIRA_DDRAW in madeira.cfg)
# and points CNC_DDRAW_CONFIG_FILE at C:\ProgramData\cnc-ddraw\ddraw.ini
# (copied from the ddraw.ini written here).
#
# Unlike DXMT's d3d9 and d3d8to9's d3d8, this DLL is NOT marked as a Wine
# builtin: it is a native DLL in the system directory, which Wine loads as
# native (build/host-tests/check-cnc-ddraw.py shows it).
#
# Upstream's inc/ddraw.h and inc/d3dcaps.h (Microsoft's SDK headers) are not in
# this tree; third_party/cnc-ddraw/madeira/ supplies what the toolchain's
# headers lack (MADEIRA_IMPORT.md, MADEIRA_CHANGES.md).
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$R/third_party/cnc-ddraw"
TC="${TC:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
DEST="${DEST:-$R/app/Madeira/cnc-ddraw}"
OUT="${OUT_DIR:-$R/build/ddraw/out-i386}"
CC="$TC/i686-w64-mingw32-clang"
WINDRES="$TC/i686-w64-mingw32-windres"
STRIP="$TC/i686-w64-mingw32-strip"

[ -x "$CC" ] || { echo "llvm-mingw not found at $TC (docs/BUILDING.md)" >&2; exit 1; }
mkdir -p "$OUT/inc" "$DEST"

# res.rc includes inc/git.h, which upstream's Makefile generates into the
# source tree; it goes into the object directory here.
COMMIT=279a057   # third_party/cnc-ddraw/MADEIRA_IMPORT.md
printf '#define GIT_COMMIT "%s"\n#define GIT_BRANCH "madeira"\n' "$COMMIT" > "$OUT/inc/git.h"

CFLAGS=(-I"$SRC/madeira" -I"$SRC/inc" -I"$OUT/inc" -O2 -Wall -std=c99 -Wno-deprecated-non-prototype)
OBJS=()
for c in "$SRC"/src/*.c "$SRC"/src/*/*.c "$SRC"/madeira/*.c; do
    rel="${c#"$SRC"/}"
    o="$OUT/${rel//\//_}"; o="${o%.c}.o"
    "$CC" "${CFLAGS[@]}" -c "$c" -o "$o"
    OBJS+=("$o")
done
"$WINDRES" -J rc -I"$OUT" -I"$SRC" "$SRC/res.rc" "$OUT/res.o"
# Upstream's link line (Makefile): static, stdcall fixup, its export list.
"$CC" -Wl,--enable-stdcall-fixup -static -shared -o "$OUT/ddraw.dll" "${OBJS[@]}" "$OUT/res.o" \
    "$SRC/exports.def" -lgdi32 -lwinmm -lole32 -lmsimg32 -lavifil32 -luuid
"$STRIP" --strip-unneeded "$OUT/ddraw.dll"
python3 "$R/build/ddraw/make-ini.py" "$OUT/ddraw.ini"

cp -f "$OUT/ddraw.dll" "$DEST/ddraw.dll"
cp -f "$OUT/ddraw.ini" "$DEST/ddraw.ini"
echo "== installed cnc-ddraw (ddraw.dll, ddraw.ini) into $DEST =="
