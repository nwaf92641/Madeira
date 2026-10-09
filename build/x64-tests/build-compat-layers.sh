#!/bin/bash
# Build compat-layers-x64.exe (x86_64 PE) and put it beside the other test
# executables in app/Madeira/arm64ec-windows, where a session can launch it.
# Run on the iPad: launch compat-layers-x64.exe; the log has one
# "[compat-layers] PASS/FAIL/SKIP" line per layer and a summary.
# Run on a desktop Wine (x86_64): wine compat-layers-x64.exe
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -z "${TC:-}" ]; then
    for c in "$R"/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin \
             "$R"/toolchains/llvm-mingw-20260421-ucrt-ubuntu-*-"$(uname -m)"/bin; do
        [ -x "$c/x86_64-w64-mingw32-clang" ] && TC="$c" && break
    done
fi
[ -n "${TC:-}" ] || { echo "llvm-mingw not found (docs/BUILDING.md), or set TC" >&2; exit 1; }
cd "$R/build/x64-tests"
"$TC/x86_64-w64-mingw32-clang" -O2 -Wall -Wno-missing-braces -o compat-layers-x64.exe compat-layers-x64.c \
    -lole32 -loleaut32 -luuid -lstrmiids -lmfuuid -lwbemuuid -ldxguid
[ "${NO_INSTALL:-}" = 1 ] || cp compat-layers-x64.exe "$R/app/Madeira/arm64ec-windows/compat-layers-x64.exe"
ls -l compat-layers-x64.exe
