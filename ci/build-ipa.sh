#!/bin/bash
# Build an UNSIGNED Madeira .ipa on a macOS runner.
#
# Mirrors docs/BUILDING.md and build/*/build.sh in the documented order, plus
# explicit workarounds for the inputs BUILDING.md lists as "not in the
# repository" and for the steps it marks UNVERIFIED:
#   * LLVM-for-iOS: the CMakeCache-derived recipe needs three fixes (below).
#   * FEX iOS: processor/tuning variables are unset when cross-compiling, and
#     CompileBlock reads Windows-only symbols outside any guard.
#   * Wine: build-macos (the host tree every unix-side script includes) has no
#     recorded recipe; it is constructed here.
#   * app/Madeira/libwineserver.a is not in the repository and its script can
#     only patch an existing archive -> ci/build-wineserver-base.sh rebuilds it.
#   * app/Madeira/x86_64-vcruntime/ holds Microsoft redistributables that cannot
#     be shipped here; an empty folder reference is staged so the bundle builds.
#
# Output: build/ipa-output/Madeira-unsigned.ipa
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$R"
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"
log() { printf '\n========== %s ==========\n' "$*"; }
export PATH="/opt/homebrew/opt/bison/bin:/opt/homebrew/opt/flex/bin:$PATH:/opt/homebrew/opt/llvm/bin"

MINGW_VER=20260421
MINGW_NAME="llvm-mingw-${MINGW_VER}-ucrt-macos-universal"
MINGW="$R/toolchains/${MINGW_NAME}"
LLVM_COMMIT=8dfdcc7b7
LLVM_SRC="$R/toolchains/llvm-project"
LLVM_BUILD="$R/toolchains/llvm-ios-build"
LLVM_HOST="$R/toolchains/llvm-host-build"
WINE_SRC="$R/wine"
WINE_BUILD="$WINE_SRC/build-macos"

log "Host"
sw_vers
uname -m
xcodebuild -version
echo "developer dir: $(xcode-select -p)"
echo "iOS SDK: $(xcrun --sdk iphoneos --show-sdk-path)"
echo "jobs: $JOBS"

# ---------------------------------------------------------------- llvm-mingw
log "Toolchain: llvm-mingw ${MINGW_VER}"
if [ ! -d "$MINGW" ]; then
    mkdir -p "$R/toolchains"
    curl -fL -o /tmp/llvm-mingw.tar.xz \
        "https://github.com/mstorsjo/llvm-mingw/releases/download/${MINGW_VER}/${MINGW_NAME}.tar.xz"
    tar -xJf /tmp/llvm-mingw.tar.xz -C "$R/toolchains"
fi

# -------------------------------------------------------------------- gnutls
log "gnutls-ios (GMP + nettle + GnuTLS)"
if [ ! -f toolchains/gnutls-ios/lib/libgnutls.a ]; then
    bash build/gnutls-ios/build.sh
fi

# -------------------------------------------------------------------- ffmpeg
log "ffmpeg (LGPL-only, static)"
if [ ! -f app/Madeira/libavcodec.a ]; then
    bash build/ffmpeg/build.sh
fi

# ------------------------------------------------------------------ freetype
log "freetype (source not tracked; cloned per build/freetype-ios/build.sh)"
if [ ! -d research/freetype ]; then
    git clone --depth 1 --branch VER-2-13-3 https://github.com/freetype/freetype.git research/freetype
fi
if [ ! -f build/freetype-ios/build/libfreetype.a ]; then
    bash build/freetype-ios/build.sh
fi

# ------------------------------------------------------- LLVM 15 for iOS arm64
log "LLVM for iOS (host tblgen stage, then iOS target libs)"
if [ ! -d "$LLVM_SRC/.git" ]; then
    mkdir -p "$R/toolchains"
    git clone --filter=blob:none https://github.com/llvm/llvm-project.git "$LLVM_SRC"
fi
git -C "$LLVM_SRC" fetch --depth 1 origin "$LLVM_COMMIT" 2>/dev/null || true
git -C "$LLVM_SRC" checkout "$LLVM_COMMIT"
# AddLLVM picks Apple ld's -dead_strip only when CMAKE_SYSTEM_NAME matches
# Darwin; for iOS it falls through to -Wl,--gc-sections, which Apple ld
# rejects. Several places test this, so replace every occurrence (the
# substitution is idempotent: the result no longer matches the pattern).
ADDLLVM="$LLVM_SRC/llvm/cmake/modules/AddLLVM.cmake"
perl -0pi -e 's/MATCHES "Darwin"/MATCHES "Darwin|iOS"/g' "$ADDLLVM"
if [ ! -x "$LLVM_HOST/bin/llvm-tblgen" ]; then
    cmake -S "$LLVM_SRC/llvm" -B "$LLVM_HOST" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DLLVM_BUILD_TOOLS=OFF -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_ENABLE_ZLIB=OFF -DLLVM_TARGETS_TO_BUILD=host
    cmake --build "$LLVM_HOST" --target llvm-tblgen -j "$JOBS"
fi
if [ ! -f "$LLVM_BUILD/lib/libLLVMCore.a" ]; then
    # LLVM_BUILD_UTILS adds an install() rule for llvm-tblgen that is invalid on
    # iOS (no BUNDLE DESTINATION); LLVM_INCLUDE_TOOLS pulls in tools/lto, whose
    # dylib fails to link for iOS (-Wl,-z,defs is not an Apple ld option);
    # LLVM_INCLUDE_UTILS would build utils/TableGen, whose tblgen is linked with
    # -Wl,--gc-sections anyway. Only the static libraries are needed, and the
    # cross build gets its tablegen from the host binary.
    cmake -S "$LLVM_SRC/llvm" -B "$LLVM_BUILD" -G Ninja \
        -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_SYSROOT=iphoneos \
        -DCMAKE_BUILD_TYPE=Release -DLLVM_HOST_TRIPLE=arm64-apple-ios17.0 \
        -DLLVM_DEFAULT_TARGET_TRIPLE=arm64-apple-ios17.0 -DLLVM_TARGET_ARCH=host \
        -DLLVM_TARGETS_TO_BUILD= -DLLVM_ENABLE_PROJECTS= -DLLVM_BUILD_TOOLS=OFF \
        -DLLVM_BUILD_UTILS=OFF -DLLVM_INCLUDE_UTILS=OFF -DLLVM_INSTALL_TOOLCHAIN_ONLY=ON \
        -DLLVM_INCLUDE_TOOLS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF -DLLVM_INCLUDE_DOCS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_ENABLE_ZLIB=OFF \
        -DLLVM_ENABLE_TERMINFO=OFF -DLLVM_ENABLE_LIBXML2=OFF -DLLVM_ENABLE_ZSTD=OFF \
        -DLLVM_TABLEGEN="$LLVM_HOST/bin/llvm-tblgen" -DCMAKE_CROSSCOMPILING=ON
    cmake --build "$LLVM_BUILD" -j "$JOBS"
fi

# ---------------------------------------------------------------- FEX (iOS)
log "FEX iOS static libraries"
if [ ! -f FEX/build-ios/FEXCore/Source/libFEXCore.a ]; then
    # FEXCore also compiles for Darwin, but two of its files use symbols that
    # only the Windows PE builds (FEX_IOS_HOST) define, with no guard at all:
    # CompileBlock reads IosFfsBypassLog/IosCbEntryLog, and the CASPAL reporter
    # calls VirtualQuery/MEMORY_BASIC_INFORMATION. Put both back under the guard
    # and give the reporter a Darwin no-op replacement.
    python3 - "${FEX_IOS_GUARD_PATCH:=1}" <<'PY'
import sys

if sys.argv[1] != "1":
    sys.exit(0)


def wrap(path, sentinel, start, end, else_body=""):
    """Enclose [start, end) in #ifdef FEX_IOS_HOST. Idempotent."""
    s = open(path).read()
    if sentinel in s:
        print("%s: already patched" % path)
        return
    assert start in s, "%s: start anchor not found" % path
    assert end in s, "%s: end anchor not found" % path
    s = s.replace(start, "#ifdef FEX_IOS_HOST\n" + start, 1)
    s = s.replace(end, else_body + "#endif\n" + end, 1)
    open(path, "w").write(s)
    print("%s: patched" % path)


wrap("FEX/FEXCore/Source/Interface/Core/Core.cpp",
     "#ifdef FEX_IOS_HOST\n  /* iOS-Madeira ml316",
     "  /* iOS-Madeira ml316: report ExitToX64 FFS bypasses",
     "  /* iOS-Madeira: refuse to compile obviously-invalid guest RIPs.")

wrap("FEX/FEXCore/Source/Utils/ArchHelpers/Arm64.cpp",
     "#ifdef FEX_IOS_HOST\nstatic void IosLogUnimplementedCASPAL",
     "static void IosLogUnimplementedCASPAL(uint32_t Size, uint64_t* GPRs, uint32_t AddressReg) {",
     "static bool HandleCASPAL(uint32_t Instr, uint64_t* GPRs, uint32_t* StrictSplitLockMutex) {",
     else_body="static void IosLogUnimplementedCASPAL(uint32_t, uint64_t*, uint32_t) {}\n")
PY
    # Options as build/fex-ios/build.sh, plus CMAKE_SYSTEM_PROCESSOR (empty when
    # cross-compiling for iOS, which FEX rejects) and generic tuning (TUNE_CPU
    # native reads /proc/cpuinfo, absent on macOS).
    cmake -S FEX -B FEX/build-ios -G Ninja \
        -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_SYSROOT=iphoneos -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 \
        -DCMAKE_SYSTEM_PROCESSOR=arm64 -DCMAKE_BUILD_TYPE=Release \
        -DTUNE_ARCH=generic -DTUNE_CPU=none \
        -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF -DBUILD_FEXCONFIG=OFF \
        -DBUILD_FEX_LINUX_TESTS=OFF -DENABLE_FEX_ALLOCATOR=OFF \
        -DENABLE_ASSERTIONS=OFF -DENABLE_CLANG_THUNKS=ON -DENABLE_CCACHE=ON
    cmake --build FEX/build-ios --target FEXCore FEXCore_Base -j "$JOBS"
    cmake --build FEX/build-ios --target JemallocLibs -j "$JOBS" 2>/dev/null || true
fi

# ------------------------------------------------------- Wine (macOS host tree)
# Every unix-side script (ntdll-unix, win32u-unix, wineserver) includes
# $WINE_BUILD/include/config.h and generated headers from $WINE_BUILD/dlls/*;
# d3d11-triangle also needs $WINE_BUILD/tools/winebuild/winebuild. BUILDING.md
# gives no recipe for this tree, so configure a plain host build. llvm-mingw is
# deliberately NOT on PATH so configure does not try to add PE targets.
log "Wine macOS host tree (configure + host tools)"
if [ ! -f "$WINE_BUILD/config.status" ]; then
    [ -x "$WINE_SRC/configure" ] || ( cd "$WINE_SRC" && ./autogen.sh )
    mkdir -p "$WINE_BUILD"
    # Flag set verified by running configure locally against the pinned fork:
    #   --enable-archs=none --without-mingw  no PE/cross targets; this tree is
    #       host-only (the win32u/ntdll unix sides are compiled separately for
    #       iOS, and llvm-mingw is deliberately not on PATH here).
    #   --without-freetype  Wine aborts configure when the 64-bit freetype dev
    #       files are absent; the iOS win32u build brings its own freetype
    #       (build/freetype-ios) and forces SONAME_LIBFREETYPE undefined.
    #   --without-x --disable-tests --enable-winegstreamer  as the fork's own
    #       PE-side recipe uses.
    ( cd "$WINE_BUILD" && ../configure \
        --enable-archs=none --without-mingw --without-freetype \
        --without-x --disable-tests --enable-winegstreamer )
fi
log "Wine host build (winebuild, then best-effort the rest)"
# configure creates include/config.h and the dlls/{ntdll,win32u} directories;
# tools/winebuild/winebuild is a host tool the PE-side scripts use. All three
# were confirmed locally. The rest of the tree is not needed by the iOS build,
# so a failure there is tolerated as long as the required outputs exist.
make -C "$WINE_BUILD" -j "$JOBS" tools/winebuild/winebuild
set +e
make -C "$WINE_BUILD" -j "$JOBS"
set -e
for need in "$WINE_BUILD/include/config.h" "$WINE_BUILD/tools/winebuild/winebuild" \
            "$WINE_BUILD/dlls/ntdll" "$WINE_BUILD/dlls/win32u"; do
    [ -e "$need" ] || { echo "ERROR: Wine host build did not produce $need"; exit 1; }
done

# ------------------------------------------------------------- Wine unix libs
log "Wine unix libraries (ntdll, win32u, wineserver)"
if [ ! -f app/Madeira/libntdll_unix.a ]; then
    bash build/ntdll-unix/build.sh
fi
if [ ! -f app/Madeira/libwin32u_unix.a ]; then
    bash build/win32u-unix/build.sh
fi
if [ ! -f app/Madeira/libwineserver.a ]; then
    bash ci/build-wineserver-base.sh
fi
bash build/wineserver/build.sh

# ---------------------------------------------------------------- DXMT (unix)
log "DXMT unix side + combined archive"
bash build/dxmt-ios/build.sh
[ -f app/Madeira/libdxmt_combined.a ] || {
    xcrun -sdk iphoneos libtool -static -o app/Madeira/libdxmt_combined.a \
        build/dxmt-ios/obj/*.o "$LLVM_BUILD"/lib/*.a
}

# ------------------------------------------------------------- Madeira Dock
log "Madeira Dock (dockhost.exe)"
bash build/madeira-dock/build.sh

# ------------------------------------------------- inputs not in the repo
log "Stage inputs that are not in the repository"
# Microsoft's x86-64 VC++ runtime DLLs are redistributable only under Microsoft's
# terms, so they are not shipped here. The project references the folder as a
# bundle resource; an empty one keeps the resource phase valid. 64-bit guest
# programs built with MSVC will miss their runtime until the DLLs are added.
mkdir -p app/Madeira/x86_64-vcruntime
touch app/Madeira/x86_64-vcruntime/.gitkeep

log "Refresh bundled licence copies (the Xcode phase fails when stale)"
bash build/stage-licenses.sh

# --------------------------------------------------------------------- Xcode
log "xcodebuild (unsigned, Debug)"
rm -rf build/ci-derived
xcodebuild -project app/Madeira.xcodeproj -scheme Madeira -configuration Debug \
    -destination 'generic/platform=iOS' -derivedDataPath build/ci-derived \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
    CODE_SIGN_ENTITLEMENTS= DEVELOPMENT_TEAM= PROVISIONING_PROFILE_SPECIFIER= \
    PROVISIONING_PROFILE= ENABLE_USER_SCRIPT_SANDBOXING=NO \
    build

APP="build/ci-derived/Build/Products/Debug-iphoneos/Madeira.app"
[ -d "$APP" ] || { echo "ERROR: $APP was not produced"; exit 1; }

log "Package unsigned IPA"
rm -rf build/ipa-output
mkdir -p build/ipa-output/Payload
cp -R "$APP" build/ipa-output/Payload/
( cd build/ipa-output && zip -qry Madeira-unsigned.ipa Payload )
shasum -a 256 build/ipa-output/Madeira-unsigned.ipa | tee build/ipa-output/SHA256SUMS
echo "OK: build/ipa-output/Madeira-unsigned.ipa"
