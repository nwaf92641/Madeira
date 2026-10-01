#!/bin/bash
# Build the 64-bit Windows farms the 64-bit guest loads:
#
#   app/Madeira/arm64ec-windows/   ARM64EC modules for an x86-64 guest (FEX)
#   app/Madeira/aarch64-windows/   native ARM64 modules for an ARM64 guest
#
# Until now every 64-bit module was built and copied by hand -- "make -C
# dlls/<name>" and cp, module by module (docs/BUILDING.md). The 32-bit farm has
# had a script for its whole set since the WoW64 work; the 64-bit farm is where
# the gap shows: 513 of the 725 modules this Wine tree builds are not in it
# (quartz, devenum, gdiplus, msxml3, riched20, the d3dx9 family, the xaudio2
# family, d2d1, the Media Foundation codecs, DirectDraw and d3d8), and a
# program that imports one of them never reaches its first instruction.
#
#   build/wine-arm64ec/build.sh                  whole farm (ARCH=arm64ec)
#   build/wine-arm64ec/build.sh quartz d2d1      named modules only
#   ARCH=aarch64 build/wine-arm64ec/build.sh     the native ARM64 farm
#
# What goes in, and why it is not a hand-written list: as in the 32-bit script,
# the farm is EVERY module the configured tree has a rule for, minus the
# SKIP_REASON list below, each entry with its reason. The import-closure check
# at the end then reads the import tables of what was installed and refuses a
# farm holding a module whose imports are not all there -- exactly the failure a
# hand-copied farm produces (app/Madeira/arm64ec-windows/bthprops.cpl has been
# shipping without bluetoothapis.dll, so the applet cannot load).
#
# The modules the list skips, and why it is not "everything with a unix side":
# the loader replaces a unix lib it does not have with a stub table, so what
# decides a module is whether its DllMain survives that stub returning
# STATUS_NOT_SUPPORTED (build/ntdll-unix/virtual_ios.c). Wine's own source says
# which is which, and the audit that read it is in docs/GAME_COMPATIBILITY.md,
# "The 64-bit farm's missing modules": 494 of the 513 modules the farm lacks
# install as they are, 7 (qcap, avicap32, winedmo, odbc32, winscard, kerberos,
# wpcap) load and lose only the feature behind a device or host library this
# port has no equivalent of, and 8 (localspl, wineps.drv, msv1_0, capi2032,
# ctapi32, sane.ds, opencl, winevulkan) plus the host drivers fail the load and
# are what the list below names. This port implements ntdll, win32u,
# wineios.drv, ws2_32, bcrypt, secur32, crypt32, dwrite and winegstreamer's
# unix side, so those modules are installed with a real backend.
#
#   * DXMT owns d3d9/d3d10core/d3d11/dxgi/winemetal, build/madeira-d3d12 owns
#     d3d12, build/fex-arm64ec and build/fex-wow64 own xtajit/xtajit64, the
#     WoW64 work owns wow64/wow64win and services/rpcss: this script must not
#     overwrite any of them.
#   * XAudio2 and XACT need no unix side and no external library: Wine 11 links
#     them against the bundled libs/faudio (STATICLIB, built with
#     FAUDIO_WIN32_PLATFORM), which talks WASAPI -- mmdevapi over wineios.drv is
#     the endpoint. They build for arm64ec like any other module, and the farm
#     has been without them.
set -euo pipefail

ARCH="${ARCH:-arm64ec}"
case "$ARCH" in
    arm64ec|aarch64) ;;
    *) echo "ARCH must be arm64ec or aarch64 (got: $ARCH)" >&2; exit 2 ;;
esac

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TC="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
B="$R/wine/build-$ARCH"
DEST="$R/app/Madeira/$ARCH-windows"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 8)}"
STRIP="$TC/$ARCH-w64-mingw32-strip"
LOG="$B/madeira-$ARCH-build.log"

[ -x "$STRIP" ] || { echo "llvm-mingw not found at $TC (docs/BUILDING.md)" >&2; exit 1; }
export PATH="$TC:$PATH"

# ---------------------------------------------------------------- configure
# Its own tree, like build-i386: the arm64ec tree is the one
# build/wine-pe/build-ntdll.sh configures, and these flags have to match it
# (--enable-winegstreamer keeps winegstreamer's PE rules although GStreamer is
# absent, since its unix side is build/ntdll-unix/winegstreamer_unixlib_ios.c).
if [ ! -f "$B/config.status" ]; then
    mkdir -p "$B"
    (cd "$B" && ../configure --enable-archs="$ARCH" --without-x --disable-tests \
                             --enable-winegstreamer)
fi
[ -f "$B/Makefile" ] || { echo "$B/Makefile is missing; configure $B first" >&2; exit 1; }

# ------------------------------------------------------------------ targets
EXT_RE='\.(dll|exe|drv|cpl|acm|ax|ocx|tlb|msstyles|com)$'
# 16-bit modules (.dll16/.exe16/...) fall out of EXT_RE: there is no NE loader.
#
# Two kinds of entry, both needing a reason:
#   * a module this script must not install over -- it is produced by another
#     component of the app, and Wine's copy would replace a working
#     implementation with one that has no backend in this port;
#   * a module that cannot load here.  The rule is the tree's, not a guess: a
#     module whose PE calls __wine_init_unix_call() in DllMain and fails the
#     load when it returns an error cannot be served, because the iOS fallback
#     binds a stub table (build/ntdll-unix/virtual_ios.c) whose entries return
#     STATUS_NOT_SUPPORTED.  A module that calls it lazily, or ignores the
#     status, loads and fails only its own feature -- those are installed, and
#     the failing feature is the loud line the loader prints when it is used
#     (that is how avicap32, odbc32, qcap, winedmo and winscard, all of which
#     the farm should carry, are decided).
SKIP_REASON=(
  "cng.sys|fltmgr.sys|hidclass.sys|hidparse.sys|http.sys|ksecdd.sys|mouhid.sys|mountmgr.sys|ndis.sys|netio.sys|nsiproxy.sys|scsiport.sys|tdi.sys|usbd.sys|winebth.sys|winebus.sys|winehid.sys|winexinput.sys|wineusb.sys|wmilib.sys=kernel drivers; a guest loads no .sys through a PE farm, and the bus drivers need host device access iOS does not give Wine"
  "ntoskrnl.exe|winedevice.exe|plugplay.exe|svchost.exe=the driver world; a session with no drivers has nothing for it to start"
  "services.exe|rpcss.exe=hand-built and tracked for the SCM/COM milestone (docs/WOW64.md, .gitignore); this script does not overwrite them"
  "wineboot.exe=the app seeds its own prefix (PrefixExtractor + app/Madeira/prefix-template.tar.gz); wineboot would redo it"
  "wow64.dll|wow64win.dll|wow64cpu.dll|xtajit.dll|xtajit64.dll=the WoW64 and FEX half (build/fex-wow64, build/fex-arm64ec, docs/WOW64.md), built and hand-managed there"
  "winemac.drv|winex11.drv|winewayland.drv|wineandroid.drv=host display drivers: init.c fails the load without the host API, and the display goes through win32u + Winios (build/win32u-unix)"
  "winealsa.drv|winepulse.drv|wineoss.drv|winecoreaudio.drv=host audio drivers: same, and the guest's endpoint is wineios.drv (build/wineios-drv), spoken through mmdevapi"
  "winevulkan.dll|vulkan-1.dll=this port has no Vulkan; graphics go through DXMT"
  "vkd3d.dll|d3d12core.dll=the D3D12 runtime here is Madeira's own (build/madeira-d3d12), not vkd3d"
  "d3d11.dll|dxgi.dll|d3d10core.dll|winemetal.dll=DXMT-owned (research/dxmt); Wine's are wined3d frontends and wined3d has no backend in this port"
  "d3d9.dll=DXMT-owned: its shim and its emulated frontend are installed by DXMT's build, not from Wine's d3d9"
  "d3d12.dll|madeira_d3d12.dll=d3d12 is app/Madeira/d3d12 (build/madeira-d3d12), tracked with its licence; not Wine's d3d12"
  "tftrace-x64.dll|dockhost.exe=Madeira's own instrumented builds (research/madeira-dock), not Wine's"
  "msv1_0.dll=its DllMain returns FALSE when the unix side is absent (dlls/msv1_0/main.c:1623) and that unix side is the host's NTLM helper, which iOS has no equivalent of"
  "capi2032.dll=ISDN CAPI: dlls/capi2032/cap20wxx.c:41 returns !__wine_init_unix_call(), and the unix side needs a CAPI driver"
  "ctapi32.dll=CTAPI smartcard terminals: dlls/ctapi32/ctapi32.c:105 returns FALSE without the unix side, and the unix side needs a CTAPI daemon"
  "sane.ds|gphoto2.ds=sane.ds fails the load (dlls/sane.ds/sane_main.c:43 returns FALSE): its unix side is a SANE scanner backend.  gphoto2.ds is installed beside it, since it loads and a missing camera merely fails the acquisition"
  "opencl.dll=dlls/opencl/pe_wrappers.c:285 returns !__wine_init_unix_call(); there is no OpenCL device behind Metal for the unix side to bind"
  "localspl.dll|wineps.drv=printing: both fail the load without their unix side (dlls/localspl/localmon.c:100, dlls/wineps.drv/init.c:301 return FALSE).  winspool.drv and the rest of the print stack do load (wspool.c:117 ignores the failure) and are installed, so a program that probes for a printer sees the API and no devices"
  "wow32.dll|winevdm.exe|vga.dll|hal.dll|w32skrnl.dll=the 16-bit layer; there is no 16-bit loader in a PE farm"
  "winemenubuilder.exe=writes host desktop menu entries; there is no host desktop"
  "wineconsole.exe=host console window; conhost.exe is the guest-side one, already in the bundle"
  "winedbg.exe=4.5 MiB of debugger only the crash dialog the app does not show spawns; dbghelp.dll is installed and is the part a game's own crash handler uses"
  "aero.msstyles=7.4 MiB of theme data nothing in the prefix selects"
)
SKIP=()
for e in "${SKIP_REASON[@]}"; do IFS='|' read -r -a n <<< "${e%%=*}"; SKIP+=("${n[@]}"); done
is_in() { local x="$1"; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }

TARGETS=()
while IFS= read -r t; do
    b="$(basename "$t")"
    is_in "$b" "${SKIP[@]}" && continue
    if [ $# -gt 0 ]; then
        want=0
        for a in "$@"; do [ "$b" = "$a" ] || [ "${b%.*}" = "$a" ] && want=1; done
        [ "$want" = 1 ] || continue
    fi
    TARGETS+=("$t")
done < <(grep -oE '^dlls/[^/]+/'"$ARCH"'-windows/[^/ :]+|^programs/[^/]+/'"$ARCH"'-windows/[^/ :]+' \
              "$B/Makefile" | grep -E "$EXT_RE" | sort -u)
[ ${#TARGETS[@]} -gt 0 ] || { echo "no $ARCH targets matched in $B/Makefile" >&2; exit 2; }
echo "== ${#TARGETS[@]} $ARCH modules (skipped by policy: ${#SKIP[@]} names)"

# -------------------------------------------------------------------- build
cd "$B"
set +e
make -k -j"$JOBS" "${TARGETS[@]}" > "$LOG" 2>&1
set -e
FAILED=()
for t in "${TARGETS[@]}"; do
    [ -f "$t" ] && continue
    if ! make -j1 "$t" >> "$LOG" 2>&1 || [ ! -f "$t" ]; then FAILED+=("$t"); fi
done
if [ ${#FAILED[@]} -gt 0 ]; then
    printf 'FAIL %s\n' "${FAILED[@]}"
    echo "== ${#FAILED[@]} modules failed; nothing installed (log: $LOG) =="
    echo "== a module that cannot build for $ARCH belongs in SKIP_REASON with its reason =="
    exit 1
fi

mkdir -p "$DEST"
for t in "${TARGETS[@]}"; do
    b="$(basename "$t")"
    # ntdll is build/wine-pe/build-ntdll.sh's: it is stripped and padded to
    # SizeOfImage + 0x50000, which the iOS mapping path depends on.
    [ "$b" = "ntdll.dll" ] && continue
    cp -f "$t" "$DEST/$b.tmp"
    case "$b" in *.tlb) ;; *) "$STRIP" --strip-debug "$DEST/$b.tmp" ;; esac
    mv -f "$DEST/$b.tmp" "$DEST/$b"
done
echo "== installed ${#TARGETS[@]} Wine modules into app/Madeira/$ARCH-windows"

# ---------------------------------------------------------- import closure
# A module whose imports are not all in the farms cannot load, and the program
# that imports *it* dies at startup instead. Read the tables of what is there
# and fail rather than ship a farm with a hole in it.
python3 "$R/build/tools/pe-imports.py" \
    --farm "$R/app/Madeira/arm64ec-windows" \
    --farm "$R/app/Madeira/aarch64-windows" \
    --fail-on-load-gap
echo "== import closure checked; the delay-load lines above are code paths that"
echo "== fail when used, not modules that cannot load"
# The farm is what compat.json claims to ship, so the database has to follow it
# (and check-pe-imports compares the two).
echo "== now re-run build/tools/gen-game-compat.py and"
echo "== build/host-tests/check-pe-imports.py: the module list moved"
