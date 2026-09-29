# AGENTS.md

Madeira: an iOS application that runs Windows x86/x86-64 games (Steam) through
Wine + FEX (ARM64EC / WoW64) and DXMT (D3D11/D3D12 via Metal). The app is
`app/Madeira.xcodeproj`; the emulation stack is built by the scripts in `build/`.

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
  Darwin too, and has code that uses its symbols unguarded:
  `Interface/Core/Core.cpp` (IosFfsBypassLog/IosCbEntryLog) and
  `Utils/ArchHelpers/Arm64.cpp` (VirtualQuery/MEMORY_BASIC_INFORMATION). Either
  guard both or the Darwin build fails; prefer guarding over defining the macro.
* `wine/server/` archives need `llvm-objcopy` for the symbol-rename sweep.
* Generated headers are not tracked, and the Xcode phase is where their absence
  surfaces last. `build/dxmt-ios/build.sh` needs
  `shader-headers/air_{msad,samplepos,tessellation}.h` as well as
  `dxmt_command.h`/`version.h`; meson produces the three from
  `research/dxmt/src/airconv/shaders/*.metal` with `xcrun metal` +
  `xxd -n <name> -i` (`src/airconv/meson.build:59-71`), and the script now runs
  that chain itself.
* `libJemallocLibs.a` must exist **and** define `rpm_cas_snapshot_take`: FEXCore
  calls it unguarded (`Interface/Core/Core.cpp`, the ml622 CAS sampler), FEX's
  `CMakeLists.txt` never builds `External/rpmalloc` on APPLE
  (`ENABLE_FEX_ALLOCATOR` is forced FALSE there for the whole platform), and the
  pinned `rpmalloc.c` cannot compile for iOS anyway (its diagnostics call
  `WriteFile`/`GetStdHandle` with no `#ifdef`). With the allocator disabled 0 is
  the real function's "nothing pending" answer, so
  `build/fex-ios/rpm_cas_snapshot_stub.c` supplies it and the archive is merged
  with `libtool`.
* Beware cache guards that skip work instead of doing it. A saved
  `FEX/build-ios` has `libFEXCore.a`, so a block guarded on that alone never
  builds missing sibling targets (`JemallocLibs`) and never re-applies the FEX
  source patches even though submodules are re-checked out unpatched every run.

## CI

`.github/workflows/build-ipa.yml` + `ci/build-ipa.sh` produce an unsigned IPA on
`macos-15` (`Madeira-unsigned-ipa`, ~74 MB). The cache is split (restore/save) so
failed runs still keep progress; build the chain in `ci/build-ipa.sh`, not ad hoc.

The pipeline is green end to end (run 36607403696, commit `64864b7`): LLVM for
iOS -> FEX -> Wine host tree + the three unix libraries -> DXMT -> Dock ->
`xcodebuild` -> unsigned IPA. When it breaks, read the stage banners in the log
first: each stage prints `=== ... ===`, the unix-side scripts print
`succeeded/failed` counts and dump the per-file `.err` files, and
`ci/build-ipa.sh` asserts on the exact archive it expects to hand to Xcode.
