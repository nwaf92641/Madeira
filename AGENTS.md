## Build: what actually works, and what does not

`docs/BUILDING.md` is the authoritative record. Read it first. Its own
conclusion is that **the app target does not build from a clean checkout**: the
recipe depends on inputs that are not in the repository. Do not expect a
first build to succeed; each failure is a new missing input, not a repeat.

Build order: gnutls -> ffmpeg -> freetype -> LLVM (iOS) -> FEX (iOS) ->
Wine (host tree, then ntdll-unix / win32u-unix / wineserver) -> DXMT ->
Madeira Dock -> Xcode.

### Inputs that are not in the repository

| Input | Notes |
|---|---|
| `toolchains/llvm-mingw-*` | mstorsjo release tarball, download in CI |
| `toolchains/llvm-project` + `llvm-ios-build` + `llvm-host-build` | commit `8dfdcc7b7`; recipe reconstructed from CMakeCache |
| `research/freetype` | not a submodule; `git clone --branch VER-2-13-3` |
| `wine/build-macos` | **no recipe anywhere**. Every unix-side script includes `$WINE_BUILD/include/config.h` and headers from `$WINE_BUILD/dlls/*`; `d3d11-triangle` needs `tools/winebuild/winebuild`. |
| `app/Madeira/libwineserver.a` | **not in the repo**. `build/wineserver/build.sh` can only *replace* objects inside an existing archive (some originals are marked "source lost"). `ci/build-wineserver-base.sh` rebuilds a base from `wine/server/*.c`. |
| `app/Madeira/x86_64-vcruntime/` | Microsoft VC++ x64 redistributables; cannot be redistributed. Referenced as a folder resource, so the folder must exist (may be empty). |
| `FEX/build-ios`, `app/Madeira/lib{ntdll_unix,win32u_unix,dxmt_combined,av*}.a` | git-ignored build outputs |

Metal Shader Converter is **not** missing: the vendored headers and
`app/Madeira/d3d12/libmetalirconverter.dylib` are tracked, so
`build/madeira-d3d12/deps.sh` resolves without Apple's installer.

### Toolchain gotchas (each cost a failed CI run)

* Xcode must be >= 16 (`std::atomic_ref`); pick it explicitly, `macos-15` ships
  several.
* LLVM for iOS: `LLVM_BUILD_UTILS` does **not** gate `utils/`; `LLVM_INCLUDE_UTILS`
  does. `LLVM_INCLUDE_TOOLS=OFF` avoids tools/lto (`-Wl,-z,defs` is not an Apple
  ld flag).
* `llvm/cmake/modules/AddLLVM.cmake` selects Apple ld's `-dead_strip` only when
  `CMAKE_SYSTEM_NAME MATCHES "Darwin"`; for iOS it falls through to
  `-Wl,--gc-sections`, which Apple ld rejects. Several occurrences -> replace all.
* FEX iOS: `CMAKE_SYSTEM_PROCESSOR` is empty when cross-compiling (FEX aborts),
  and `TUNE_CPU=native` reads `/proc/cpuinfo`. Use `arm64` and `none`.
* `FEX_IOS_HOST` belongs to the **Windows PE** builds. FEXCore compiles for
  Darwin too, and has code that uses its symbols unguarded.
