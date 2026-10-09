#!/bin/bash
# Add Wine builtin modules to the 64-bit (ARM64EC) DLL farm,
# app/Madeira/arm64ec-windows, from the list in build/wine-pe/arm64ec-farm.json.
#
#   build/wine-pe/build-arm64ec-farm.sh                 build + install the listed modules not yet in the farm
#   build/wine-pe/build-arm64ec-farm.sh --refresh       also rebuild the listed modules already in the farm
#   build/wine-pe/build-arm64ec-farm.sh --check         install nothing; report what is missing (exit 1 if any)
#   build/wine-pe/build-arm64ec-farm.sh quartz xaudio2_9   only those names (must be in the list)
#
# Why a list and not every module, as build/wine-i386/build.sh does: an arm64ec
# module is mapped straight from its file, so its FileAlignment is 0x10000 and
# even an empty one is about 0.5 MiB; all 658 modules would add about 500 MiB to
# the app. The list says, per group, why each module is needed (arm64ec-farm.json).
#
# What is never installed from Wine here, whatever the list says, and why: the
# names below have another owner in this tree.
#   ntdll.dll                       build/wine-pe/build-ntdll.sh (stripped AND padded for the iOS mapping path)
#   d3d11/dxgi/d3d10core/winemetal  DXMT (Wine's are wined3d frontends with no backend here)
#   d3d12.dll                       the native D3D12 runtime (build/madeira-d3d12); Wine's is vkd3d on Vulkan
#   d3d12core.dll, vulkan-1.dll, winevulkan.dll   need Vulkan, which this port does not have
#   xtajit64.dll                    FEX (build/fex-arm64ec)
#   wineios.drv                     build/wineios-drv
#
# Build tree: wine/build-arm64ec, the tree build-ntdll.sh configures on macOS.
# On a Linux x86_64 host (CI, a container) that tree cannot be configured the
# macOS way, because Wine only enables arm64ec next to an aarch64 host; there
# the script builds Wine's tools natively in wine/build-tools and configures
# wine/build-arm64ec as a cross build for aarch64-linux-gnu (needs
# aarch64-linux-gnu-gcc for configure's host checks; only PE targets are built).
# The PE output is the same either way: same toolchain release, same sources.
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIST="$R/build/wine-pe/arm64ec-farm.json"
DEST="$R/app/Madeira/arm64ec-windows"
B="${WINE_ARM64EC_BUILD:-$R/wine/build-arm64ec}"
JOBS="${JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8)}"

if [ -z "${TC:-}" ]; then
    for c in "$R"/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin \
             "$R"/toolchains/llvm-mingw-20260421-ucrt-ubuntu-*-"$(uname -m)"/bin; do
        [ -x "$c/arm64ec-w64-mingw32-clang" ] && TC="$c" && break
    done
fi
[ -n "${TC:-}" ] && [ -x "$TC/arm64ec-w64-mingw32-clang" ] || {
    echo "llvm-mingw 20260421 (ucrt) not found under toolchains/ (docs/BUILDING.md), or set TC=<its bin dir>" >&2; exit 1; }
export PATH="$TC:$PATH"
STRIP="$TC/llvm-strip"
READOBJ="$TC/llvm-readobj"

REFRESH=0; CHECK=0; ONLY=()
for a in "$@"; do
    case "$a" in
        --refresh) REFRESH=1 ;;
        --check) CHECK=1 ;;
        -*) echo "unknown option $a" >&2; exit 2 ;;
        *) ONLY+=("$a") ;;
    esac
done

# ------------------------------------------------------------- the module list
SEL="$(mktemp)"; trap 'rm -f "$SEL"' EXIT
python3 - "$LIST" "${ONLY[@]+"${ONLY[@]}"}" > "$SEL" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
only = {n.lower() for n in sys.argv[2:]}
owned = {'ntdll.dll', 'd3d11.dll', 'dxgi.dll', 'd3d10core.dll', 'winemetal.dll', 'd3d12.dll',
         'd3d12core.dll', 'vulkan-1.dll', 'winevulkan.dll', 'xtajit64.dll', 'wineios.drv'}
seen = set()
for group in doc['groups']:
    for name in group['modules']:
        low = name.lower()
        if low in owned:
            sys.exit(f"arm64ec-farm.json: {name} (group {group['name']}) has another owner; see the script header")
        if low in seen:
            sys.exit(f"arm64ec-farm.json: {name} is listed twice")
        seen.add(low)
        stem = low.rsplit('.', 1)[0]
        if only and low not in only and stem not in only:
            continue
        print(low)
missing = {m for m in only if m not in seen and not any(s.rsplit('.', 1)[0] == m for s in seen)}
if missing:
    sys.exit("not in arm64ec-farm.json: " + ", ".join(sorted(missing)))
PY
mapfile -t WANT < "$SEL"
[ ${#WANT[@]} -gt 0 ] || { echo "nothing selected" >&2; exit 2; }

present="$(ls "$DEST" | tr '[:upper:]' '[:lower:]')"
in_farm() { echo "$present" | grep -qx "$1"; }

TODO=()
for n in "${WANT[@]}"; do
    if in_farm "$n" && [ "$REFRESH" = 0 ]; then continue; fi
    TODO+=("$n")
done

if [ "$CHECK" = 1 ]; then
    if [ ${#TODO[@]} -gt 0 ]; then printf 'not in the farm: %s\n' "${TODO[@]}"; exit 1; fi
    echo "all ${#WANT[@]} listed modules are in app/Madeira/arm64ec-windows"; exit 0
fi
[ ${#TODO[@]} -gt 0 ] || { echo "all ${#WANT[@]} listed modules are already in the farm (--refresh rebuilds them)"; exit 0; }

# ------------------------------------------------------------------ configure
if [ ! -f "$B/config.status" ]; then
    mkdir -p "$B"
    if [ "$(uname -s)" = Darwin ]; then
        (cd "$B" && ../configure --enable-archs=arm64ec --without-x --disable-tests --enable-winegstreamer)
    else
        T="${WINE_TOOLS_BUILD:-$R/wine/build-tools}"
        if [ ! -x "$T/tools/winebuild/winebuild" ]; then
            mkdir -p "$T"
            (cd "$T" && "$R/wine/configure" --without-x --without-freetype --without-gnutls --without-vulkan \
                                            --disable-tests --enable-win64 --without-mingw && make -j"$JOBS" __tooldeps__)
        fi
        command -v aarch64-linux-gnu-gcc >/dev/null || {
            echo "aarch64-linux-gnu-gcc is needed to configure the arm64ec tree on Linux" >&2; exit 1; }
        (cd "$B" && "$R/wine/configure" --host=aarch64-linux-gnu --with-wine-tools="$T" --enable-archs=arm64ec \
                                        --without-x --without-freetype --without-gnutls --without-vulkan \
                                        --disable-tests --enable-winegstreamer)
    fi
fi

# widl resolves importlib("stdole2.tlb") in <tree>/dlls/stdole2.tlb/aarch64-windows/,
# the aarch64 half of an arm64ec build; a tree with only the arm64ec half needs
# the 64-bit typelib there (the layout is the same for both).
make -C "$B" -j"$JOBS" dlls/stdole2.tlb/arm64ec-windows/stdole2.tlb >/dev/null
mkdir -p "$B/dlls/stdole2.tlb/aarch64-windows"
[ -e "$B/dlls/stdole2.tlb/aarch64-windows/stdole2.tlb" ] || \
    ln -s ../arm64ec-windows/stdole2.tlb "$B/dlls/stdole2.tlb/aarch64-windows/stdole2.tlb"

# ---------------------------------------------------------------------- build
target_of() {
    local n="$1" t
    # dlls/<name>/ for libraries, programs/<name>/ for the few Wine programs a
    # 64-bit session needs in system32 (msiexec.exe runs x64 MSI custom actions).
    t="$(grep -oE "^(dlls|programs)/[^/]+/arm64ec-windows/$n:" "$B/Makefile" | head -1 | tr -d ':' || true)"
    [ -z "$t" ] && t="$(grep -oiE "^(dlls|programs)/[^/]+/arm64ec-windows/$n:" "$B/Makefile" | head -1 | tr -d ':' || true)"
    echo "$t"
}
TARGETS=(); NOTARGET=()
for n in "${TODO[@]}"; do
    t="$(target_of "$n")"
    if [ -n "$t" ]; then TARGETS+=("$t"); else NOTARGET+=("$n"); fi
done
if [ ${#NOTARGET[@]} -gt 0 ]; then
    printf 'no arm64ec rule in %s for: %s\n' "$B/Makefile" "${NOTARGET[@]}" >&2; exit 1
fi
LOG="$B/madeira-arm64ec-farm.log"
echo "== building ${#TARGETS[@]} arm64ec modules (log: $LOG) =="
set +e
make -C "$B" -k -j"$JOBS" "${TARGETS[@]}" > "$LOG" 2>&1
set -e
FAILED=()
for t in "${TARGETS[@]}"; do [ -f "$B/$t" ] || FAILED+=("$t"); done
if [ ${#FAILED[@]} -gt 0 ]; then
    printf 'FAIL %s\n' "${FAILED[@]}"; echo "== ${#FAILED[@]} modules failed; nothing installed ==" ; exit 1
fi

# ---------------------------------------------------- import closure, then install
# Every DLL a new module imports or delay-imports must be in the farm after the
# install (API set names resolve through apisetschema.dll). A missing delay-load
# target is only noticed at the first call, as an unimplemented-function abort. Checked before anything is copied, so
# a list that would leave a module unloadable installs nothing.
after="$( (echo "$present"; for t in "${TARGETS[@]}"; do basename "$t"; done) | tr '[:upper:]' '[:lower:]' | sort -u)"
missing=0
for t in "${TARGETS[@]}"; do
    while IFS= read -r imp; do
        l="$(echo "$imp" | tr '[:upper:]' '[:lower:]')"
        case "$l" in api-ms-win-*|ext-ms-win-*) continue ;; esac
        echo "$after" | grep -qx "$l" && continue
        echo "missing import: $(basename "$t") -> $imp"; missing=$((missing + 1))
    done < <("$READOBJ" --coff-imports "$B/$t" 2>/dev/null | awk '/^(Import|DelayImport) \{/ { want = 1; next } want && /^  Name: / { sub(/^  Name: /, ""); print; want = 0 }')
done
[ "$missing" = 0 ] || { echo "== $missing imports would not resolve; add them to arm64ec-farm.json. Nothing installed ==" >&2; exit 1; }

for t in "${TARGETS[@]}"; do
    b="$(basename "$t")"
    # A module already in the farm keeps its spelling (WLDAP32.dll, X3DAudio1_7.dll).
    existing="$(ls "$DEST" | grep -ix "$b" || true)"
    out="$DEST/${existing:-$b}"
    case "$b" in
        *.tlb) cp -f "$B/$t" "$out.tmp" ;;
        *) "$STRIP" --strip-debug -o "$out.tmp" "$B/$t" ;;
    esac
    mv -f "$out.tmp" "$out"
done
echo "== installed ${#TARGETS[@]} Wine modules into app/Madeira/arm64ec-windows ($(du -sh "$DEST" | cut -f1) total) =="
