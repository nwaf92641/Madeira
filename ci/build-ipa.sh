#!/bin/bash
# Build an UNSIGNED Madeira .ipa on a macOS runner.
#
# This mirrors the chain recorded in docs/BUILDING.md and build/*/build.sh.
# It is deliberately best-effort: several upstream steps are marked UNVERIFIED
# in docs/BUILDING.md (the Wine macOS configure has no recorded recipe, and the
# LLVM-for-iOS build is reconstructed from CMakeCache values), so the first
# runs are expected to need fixing. Everything is cached by the workflow, so a
# retry does not redo the expensive stages.
#
# Output: build/ipa-output/Madeira-unsigned.ipa
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$R"
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"
log() { printf '\n========== %s ==========\n' "$*"; }

MINGW_VER=20260421
MINGW_NAME="llvm-mingw-${MINGW_VER}-ucrt-macos-universal"
MINGW="$R/toolchains/${MINGW_NAME}"
LLVM_COMMIT=8dfdcc7b7
LLVM_SRC="$R/toolchains/llvm-project"
LLVM_BUILD="$R/toolchains/llvm-ios-build"
LLVM_HOST="$R/toolchains/llvm-host-build"

log "Host"
sw_vers
uname -m
xcodebuild -version

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
if [ ! -d build/freetype-ios/build ]; then
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
# The recipe needs Apple ld's -dead_strip, which AddLLVM only selects for Darwin.
ADDLLVM="$LLVM_SRC/llvm/cmake/modules/AddLLVM.cmake"
if ! grep -q 'MATCHES "Darwin|iOS"' "$ADDLLVM"; then
    sed -i '' 's/MATCHES "Darwin"/MATCHES "Darwin|iOS"/' "$ADDLLVM"
fi
if [ ! -x "$LLVM_HOST/bin/llvm-tblgen" ]; then
    cmake -S "$LLVM_SRC/llvm" -B "$LLVM_HOST" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DLLVM_BUILD_TOOLS=OFF -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_ENABLE_ZLIB=OFF -DLLVM_TARGETS_TO_BUILD=host
    cmake --build "$LLVM_HOST" --target llvm-tblgen -j "$JOBS"
fi
if [ ! -f "$LLVM_BUILD/lib/libLLVMCore.a" ]; then
    cmake -S "$LLVM_SRC/llvm" -B "$LLVM_BUILD" -G Ninja \
        -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_SYSROOT=iphoneos \
        -DCMAKE_BUILD_TYPE=Release -DLLVM_HOST_TRIPLE=arm64-apple-ios17.0 \
        -DLLVM_DEFAULT_TARGET_TRIPLE=arm64-apple-ios17.0 -DLLVM_TARGET_ARCH=host \
        -DLLVM_TARGETS_TO_BUILD= -DLLVM_ENABLE_PROJECTS= -DLLVM_BUILD_TOOLS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF -DLLVM_ENABLE_ZLIB=OFF \
        -DLLVM_TABLEGEN="$LLVM_HOST/bin/llvm-tblgen" -DCMAKE_CROSSCOMPILING=ON
    cmake --build "$LLVM_BUILD" -j "$JOBS"
fi

# ---------------------------------------------------------------- FEX (iOS)
log "FEX iOS static libraries"
if [ ! -f FEX/build-ios/FEXCore/Source/libFEXCore.a ]; then
    bash build/fex-ios/build.sh
    # The app target also links libJemallocLibs.a; build it if the default
    # target set did not (the option state is UNVERIFIED from clean).
    cmake --build FEX/build-ios --target JemallocLibs -j "$JOBS" 2>/dev/null || true
fi

# ------------------------------------------------------- Wine (macOS host tree)
log "Wine macOS build tree (wine/build-macos has no recorded recipe; best-effort)"
if [ ! -f wine/build-macos/config.status ]; then
    if [ ! -x wine/configure ]; then
        ( cd wine && ./autogen.sh )
    fi
    mkdir -p wine/build-macos
    ( cd wine/build-macos && ../configure --without-x --disable-tests --enable-winegstreamer )
fi
log "Wine generated headers and host tools"
# The unix-side scripts need config.h plus the generated include tree; build
# whatever the tree needs. This is the heavy Wine step.
make -C wine/build-macos -j "$JOBS"

# ------------------------------------------------------------- Wine unix libs
log "Wine unix libraries (ntdll, wineserver, win32u)"
bash build/ntdll-unix/build.sh
bash build/wineserver/build.sh
bash build/win32u-unix/build.sh

# ---------------------------------------------------------------- DXMT (unix)
log "DXMT unix side + LLVM combined archive"
bash build/dxmt-ios/build.sh
if [ ! -f app/Madeira/libdxmt_combined.a ]; then
    xcrun -sdk iphoneos libtool -static -o app/Madeira/libdxmt_combined.a \
        build/dxmt-ios/obj/*.o "$LLVM_BUILD"/lib/*.a
fi

# ------------------------------------------------------------------- licences
log "Refresh bundled licence copies (the Xcode phase fails when stale)"
bash build/stage-licenses.sh

# --------------------------------------------------------------------- Xcode
log "xcodebuild (unsigned)"
xcodebuild -project app/Madeira.xcodeproj -scheme Madeira -configuration Debug \
    -destination 'generic/platform=iOS' -derivedDataPath build/ci-ipa \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
    DEVELOPMENT_TEAM= PROVISIONING_PROFILE_SPECIFIER= PROVISIONING_PROFILE= \
    build

APP="build/ci-ipa/Build/Products/Debug-iphoneos/Madeira.app"
[ -d "$APP" ] || { echo "ERROR: $APP was not produced"; exit 1; }

log "Package unsigned IPA"
rm -rf build/ipa-output
mkdir -p build/ipa-output/Payload
cp -R "$APP" build/ipa-output/Payload/
( cd build/ipa-output && zip -qry Madeira-unsigned.ipa Payload )
shasum -a 256 build/ipa-output/Madeira-unsigned.ipa | tee build/ipa-output/SHA256SUMS
echo "OK: build/ipa-output/Madeira-unsigned.ipa"
