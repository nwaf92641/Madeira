#!/bin/bash
# Reconstruct the base archive that build/wineserver/build.sh patches.
#
# build/wineserver/build.sh can only REPLACE objects inside an existing
# app/Madeira/libwineserver.a; that base is not in the repository (its
# provenance includes objects whose source is marked lost). Compile every
# wine/server/*.c with the exact flags that script uses, under the object
# names its REPLACEMENTS table expects, so the official patch step can then
# apply the iOS overrides on top.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINE_SRC="$R/wine"
WINE_BUILD="$WINE_SRC/build-macos"
SDK=$(xcrun --sdk iphoneos --show-sdk-path)
OBJ="$R/build/wineserver/obj"
APP_LIB="$R/app/Madeira/libwineserver.a"
mkdir -p "$OBJ"

CFLAGS=(-arch arm64 -isysroot "$SDK" -miphoneos-version-min=17.0 -O2
  -I"$WINE_SRC/include" -I"$WINE_SRC/include/wine"
  -I"$WINE_BUILD/include"
  -I"$R/build/wineserver" -I"$WINE_SRC/server"
  -I"$R/build/ntdll-unix/shims"
  -I"$R/build/madsync" -DHAVE_LINUX_NTSYNC_H=1
  -include "$R/build/wineserver/config_ios.h"
  -include stdarg.h
  -include "$R/build/wineserver/unicode_fix.h"
  -include "$R/build/wineserver/wineserver_ios_kill.h"
  -DBINDIR=\"/usr/local/bin\" -DDATADIR=\"/usr/local/share\"
  -D__WINESRC__ -DWINE_IOS=1
  -Dmain=wineserver_main
  -Wno-implicit-function-declaration)

# server/*.c that build/wineserver/build.sh replaces with an iOS variant must
# still exist in the base under the plain object name (main.o, request.o, ...).
objs=()
fail=0
for src in "$WINE_SRC"/server/*.c; do
    base=$(basename "$src" .c)
    out="$OBJ/$base.o"
    if xcrun -sdk iphoneos clang "${CFLAGS[@]}" -c "$src" -o "$out" 2>"$OBJ/err-$base.txt"; then
        objs+=("$out")
    else
        echo "  FAILED: $base"
        sed -n '1,12p' "$OBJ/err-$base.txt"
        fail=1
    fi
done
[ "$fail" = 0 ] || { echo "ERROR: some server sources failed; see $OBJ/err-*.txt"; exit 1; }

# The archive also carries the kill wrapper (build.sh expects it in-place).
xcrun -sdk iphoneos clang -arch arm64 -isysroot "$SDK" -miphoneos-version-min=17.0 -O2 \
    -I"$R/build/wineserver" -DWINE_IOS=1 -Wno-implicit-function-declaration \
    -c "$R/build/wineserver/wineserver_ios_kill.c" -o "$OBJ/wineserver_ios_kill.o"
objs+=("$OBJ/wineserver_ios_kill.o")

ar rcs "$APP_LIB" "${objs[@]}"
echo "base libwineserver.a: $(wc -c < "$APP_LIB" | tr -d ' ') bytes, ${#objs[@]} objects"
