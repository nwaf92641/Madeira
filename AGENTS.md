# AGENTS.md

Repository memory for Madeira. Read this before touching the JIT path — most of
the traps below have already shipped a bug once.

## What this is

Madeira runs Windows PC games on a non-jailbroken iPhone. Wine (ARM64EC), FEX-Emu
(x86-64 → ARM64) and DXMT (D3D11 → Metal) run as a single Mach process, with
wineserver as a thread rather than a separate process. It is sideloaded — JIT
requires a debugger to attach, so it cannot go through the App Store.

## Layout

- `app/Madeira/` — the iOS app (Swift + the C/C++ JIT and Wine bridge code).
- `app/Madeira/AppLayout.swift` — the tooling-vs-fullscreen decision, pure and
  unit-tested. Read the header before touching `ContentView`'s body.
- `app/Madeira/GamepadMap.swift` — controller → key/mouse mapping, pure and
  unit-tested. `app/Madeira/GamepadBridge.swift` is the only GCController code.
- `app/Madeira/madeira-jit.js` — the debugger-side JIT protocol script.
- `app/Madeira/JITAllocator.c` / `.h` — pool allocation, dual-map, BRK protocol,
  no-footprint (Jetsam) handling, trap handler.
- `app/Madeira/FEXBridge.mm` — the separate 64 MB FEX JIT pool.
- `FEX/`, `wine/`, `research/dxmt` — submodules pointing at iOS forks. Upstream
  clones will not build.
- `build/*/` — native build scripts and prebuilt test binaries
  (`proc-tests`, `x64-tests`, `dxmt-tests`, `net-tests`); there is no
  single top-level build.
- `tools/` — pre-release gates (see below).
- `research/` — investigation notes keyed by `ml###` revision numbers.

## Conventions

- Comments and commit messages reference `ml###` revision ids (e.g. `ml345`).
  Keep the highest id monotonic when adding a substantive change note.
- Two JIT pools exist and are unrelated: the app's ~896 MB pool via
  `jit26_prepare_region` (JITAllocator.c) and FEX's 64 MB pool in
  `FEXBridge.mm`. A fix to one does not affect the other.
- Credit upstream correctly: FEX-Emu prohibits AI-generated contributions (see
  `README.md` and `CONTRIBUTING.md`). Only the Madeira tree is yours to change
  from the app side.

## The embedded JIT script trap (read this twice)

`StikJITHelper.swift` ships `madeira-jit.js` as a **base64 string literal**
(`scriptBase64`). `madeira-jit.js` is NOT a member of the Xcode target, so the
literal — not the file — is what StikDebug actually runs. Editing the `.js` and
rebuilding changes nothing until you regenerate the literal:

```sh
tools/jit-script-sync.py --write    # regenerate literal from madeira-jit.js
tools/jit-script-sync.py            # check (exit 1 if drifted)
```

This is enforced by `tools/check-all.sh`, which is the pre-release gate. Run it
before shipping. (Same idea as `tools/check-prefix-template.sh`, which exists
because the prefix template once shipped absolute host symlinks, and
`tools/check-xcodeproj.py`, which exists because a source file that is not
registered in the Sources phase is silently not part of the app — invisible on
any machine that cannot open Xcode. `tools/test-device-capabilities.sh` covers
the JIT pool and override-parser policy; see Build / test constraints.)

## JIT lifecycle (app 896 MB pool)

1. `jit_check_debugged()` reads `CS_DEBUGGED` via `csops`.
2. `enableJIT` opens StikDebug with the script in the URL; `pollForJIT` waits
   for the flag (bounded — do not make it unbounded again).
3. `allocatePool` pre-pins low address space so the pool lands above
   ~0x119000000 (FEX dispatcher encoding is address-dependent), allocates via
   `brk #0xf00d` (`x16=1`), and applies the no-footprint ledger.
4. `detachDebugger()` emits `brk #0xf00d` with `x16=0`. It is **one-shot** on
   purpose: the early-detach and post-Wine paths both call it, and the second
   call would trap into our own task-level exception port. `enableJIT` re-arms
   it for a fresh attach. Do not replace this with a permanent C-level flag —
   `JITAllocator.c` cannot tell a re-attach from a duplicate call.

### Stop-loop invariants (`madeira-jit.js`)

The script is the ONLY thing servicing traps while StikDebug is attached. Its
comment header is authoritative; the short version:

- Only genuine `BRK` instructions are skipped (`pc+4`). Everything else that
  escalates to us is a real fault and is handed back to the process as a unix
  signal — never blindly `pc+4`, that silently corrupts threads (ml344/ml345).
- Soft-signal stops (`metatype 5`, `EXC_SOFT_SIGNAL`) forward the original
  signal from `metadata[1]` and are never guarded.
- Never detach on a fault. With the StikDebug window still open the task
  exception port stays registered but unserviced, parking threads forever
  (ml345). Kill the inferior instead — a visible death beats a parked thread.
- Every reply is parsed defensively (`parseHexBigInt`); a `_M` error string must
  never be turned into a pool base address. An uncaught throw ends the script,
  which StikDebug treats as session teardown — JIT vanishes with no crash
  report. Logs are budgeted (`ulog`) because each `log()` drives a SwiftUI
  update and scene-update stalls are what the StikDebug watchdog kills.

## Is performance pinned to A15? (asked; answer verified — do not re-investigate blindly)

No. The gameplay translator does not use the hardcoded A15 block. Evidence:

- `FEXBridge.mm` ~430-450 builds a `FEXCore::HostFeatures` with A15 values
  (`CPUMIDRs.resize(8, 0x611F0250)`, `DCacheLineSize = 64`, ...). That block is
  real but its scope is tiny: this FEXCore instance only runs the in-app self
  test (`fex_test_execute()` → an embedded x86-64 ELF that returns 42) and
  allocates the shared pool. It never translates the game. The only things it
  publishes to the guest are `MADEIRA_JIT_WRITE_OFFSET` and `MADEIRA_FEX_ARENA`.
- The game is translated by `app/Madeira/arm64ec-windows/xtajit64.dll`, a
  separate FEXCore. Its symbols include
  `FEX::Windows::CPUFeatures::FetchHostFeatures(bool, HostTypeEnum)`, which in
  upstream FEX reads the live CPU (`ReadRegU64(Key, "CP 4000")` per core into
  `CPUMIDRs`, plus `mrs ctr_el0` / `mrs midr_el1`). Verified the DLL contains no
  `0x611F0250` bytes and no fixed MIDR table — it detects the real chip.
- `CPUMIDRs` could not cap codegen even if pinned: upstream FEX says so outright
  (`// Skip CPUMIDRs as it doesn't affect codegen.` in
  `FEXCore/include/FEXCore/Core/HostFeatures.h`). Its only jobs are errata
  workarounds and the guest hybrid-CPUID flag, and no Apple part has errata
  there.
- Nothing sets `FEX_HOSTFEATURES` or `FEX_FORCESVEWIDTH`. Core count is not
  pinned either — Wine derives `peb->NumberOfProcessors` from the host.

What *does* make every device behave alike, and is the real lever to pull. As of
ml787 the first two have a device-aware default plus a no-rebuild override:

- Desktop resolution was hardcoded: `1024x768` (`ContentView.swift:1159`) and
  `960x540` for the services path (`:1431`). Pixel work was therefore identical
  on an A15 and an A18, so a faster GPU bought nothing. The defaults are still
  those two — they are load-bearing for window fitting and unvalidated
  elsewhere — and `Documents/madeira-resolution.txt` (`WIDTHxHEIGHT`) overrides
  them per run.
- The JIT pool was a fixed `896 MB` (`ContentView.swift:1862`), so the
  translation cache was the same size regardless of device RAM.
  `DeviceCapabilities.recommendedPoolMB()` now derives it from the measured
  jetsam budget: exactly 896 MB at or below the 4096 MB budget of the device
  this was developed against (so nothing already validated moves), scaling
  above it and capping at 1792 MB. `Documents/madeira-pool.txt` still overrides.
- FEX has no per-microarchitecture tuning; it targets an ARMv8 baseline plus
  detected features. A15→A18 ISA gains are minor, so the translator gains
  little from the newer part. What switches exist are compiled in, so
  `Documents/madeira-fex.txt` exports `KEY=VALUE` pairs as `FEX_<KEY>` — the
  same no-rebuild channel as `madeira-dxmt.txt` on the renderer side.

To confirm on-device, read the startup log line the fork emits:
`FEX: HostFeatures={} (ml538: ...)`. Identical content on two different chips
would be the smoking gun; it should differ.

## iPad fullscreen: one size class is not enough (fixed ml790)

There is exactly one tooling layout and one fullscreen layout, chosen by
`LayoutPolicy.resolve` in `AppLayout.swift`. Do not go back to branching on
`verticalSizeClass == .compact`:

- That is true ONLY for an iPhone in landscape. An iPad reports `.regular`
  vertically in **both** orientations, so every iPad was permanently stuck in the
  tooling layout with a 240pt game strip and no way to enlarge it. An iPad has no
  rotation to discover fullscreen with. This was reported as "the iPad can't make
  the game fullscreen".
- The idiom is therefore an explicit input to the policy, not inferred from the
  size class. A compact size class on an iPad means a multitasking pane, and the
  tooling rows still fit there. `Info.plist` also sets `UIRequiresFullScreen` so
  that pane case cannot arise in a shipped build.
- Immersion is forced on an iPhone in landscape and chosen everywhere else, via
  the expand button in the nav bar. `LayoutPolicy.needsExitAffordance` decides
  whether the immersive layout draws its own way out — it must be false where
  immersion was forced, because there is nowhere to return to.

Two things about the fullscreen layout are load-bearing:

- The exit control is a 44pt strip **above** the surface, never a button over
  it. `MetalHostView.shared` is added as a subview of the *window*, so anything
  SwiftUI draws over the game rect is invisible. A 4:3 iPad leaves no pillarbox
  to hide in, so an overlaid button would be dead on the one device it is for.
- The touch-controls overlay lives in its own window and cannot read
  `ContentView`'s state. It learns whether the game is up from `GameChromeState`
  (`immersive`, `topInset`) — not from `w > h`, which is wrong in portrait
  fullscreen — and starts below the strip so it does not swallow taps meant for
  the exit button. `TouchControlsModel.hitsInteractive` must keep that offset.

`tools/test-app-ui.sh` asserts the whole table. It is cheap; extend it rather
than reasoning about this again.

## Controllers: no XInput, and a keyboard/pointer bridge instead (checked ml790)

Do not claim gamepad support exists in the guest. It does not:

- There is no XInput, DInput or HID gamepad plumbing anywhere. The `wine/`
  submodule is not checked out in this environment, and nothing in `build/`
  (the Madeira-side glue that IS here: `win32u-unix/`, `wineserver/`,
  `ntdll-unix/`) presents a gamepad device. `build/wineios-drv/wineios.c` is
  only a PE stub for the audio driver.
- `ControlAction.pad(...)` and the mapping panel's "gamecontroller" tab are
  therefore deliberately inert (ml645), and the panel says so. Wiring them up
  needs work in the `wine/` submodule plus `build/`, not in this target.
- The touch-controls overlay is what "the controller" in the app actually means:
  on-screen buttons and a thumbstick that post through `winios_post_key`.

What DOES work for a physical controller is `GamepadBridge`, added ml790. It
maps a real controller onto virtual keys and relative pointer motion through
`winios_post_key` / `winios_pointer` — the same two calls the key buttons, the
on-screen stick and the S2 trackpad already use — so any game that accepts
keyboard and mouse accepts it, mouse-look included. A game that accepts ONLY
XInput still will not see it, and no change on this side fixes that.

- Defaults are on when a controller is connected (a plugged-in gamepad that is
  ignored is the more surprising behaviour) and rebindable in
  `Documents/madeira-gamepad.txt`: `ENABLED = 0`, `MOUSE_SPEED = 1.0`, and
  `<BUTTON> = 0xNN | VK0xNN | LMB | RMB | NONE`. Movement (left stick and d-pad →
  arrow keys) is deliberately not rebindable — it is the contract every Windows
  game already has, and the same eight-way snap `JoystickKeyView` uses.
- The look axis y is negated in exactly one place: `GamepadMap` is written in
  up-positive stick coordinates but posts mouse coordinates, which count upward
  as negative. Getting that wrong looks like a broken game, not a broken bridge.
- On disconnect the bridge releases everything it was holding. There is no other
  event that could lift a key the vanished controller was holding.

`tools/test-app-ui.sh` covers the mapping, including the sign. The
`GamepadBridge` glue itself cannot be compiled or run off-device; it still needs
one on-iPad confirmation.

## Build / test constraints in this environment

- The iOS app builds only with `xcodebuild` on macOS. There is no Linux build.
- Swift can be checked without a Mac in three grades, all wired into
  `tools/check-all.sh`:
  - `tools/test-device-capabilities.sh` type-checks `DeviceCapabilities.swift`
    (installs nothing, adapts the one Apple-only import, shims `sysctl`) and
    asserts its pool/override tables.
  - `tools/test-app-ui.sh` type-checks and asserts `AppLayout.swift` and
    `GamepadMap.swift`. Both are deliberately Foundation-only so this stays
    possible — keep framework imports out of them.
  - `tools/check-swift-syntax.sh` runs `swiftc -parse` over every app source.
    This catches syntax only, NOT types: a misspelled property still parses.
    `ContentView.swift` needs SwiftUI and cannot be type-checked off-device, so
    type errors there are still caught by nothing until a Mac or a build.
- `.github/workflows/gates.yml` runs `tools/check-all.sh` on every push and PR,
  on `macos-15` because two gates need `swiftc`.
- `scripts/make-ipa.sh` builds and packages the unsigned `Madeira-unsigned.ipa`
  on a Mac. It runs `tools/check-build-inputs.sh` first, because a clean clone
  cannot be linked. `--output`, `--configuration`, `--keep-build`.
- `.github/workflows/ipa.yml` builds the IPA on a hosted `macos-15` runner with
  no Mac and no pre-published binaries: FEX (`scripts/build-fex-ios.sh`), the
  GnuTLS stack, the Wine unix libs, LLVM 15 for iOS
  (`build/dxmt-ios/build-llvm.sh`) and DXMT (`build/dxmt-ios/build-all.sh`) are
  compiled from the pinned submodules, checked with
  `tools/validate-ios-bundle.py`, and cached between runs. It needs the iOS 26
  SDK because `ContentView.swift` calls `glassEffect()`. Triggered by pushes
  touching `scripts/`, `build/`, `tools/`, `patches/` or the workflow itself,
  and by `workflow_dispatch`.
- `build/dxmt-ios/build-all.sh` rebuilds DXMT's four PE DLLs (`d3d11`, `dxgi`,
  `winemetal`, `d3d10core`) **only when they are missing**: they are committed,
  they were built against the same Wine revision the submodule pins, and a
  from-scratch rebuild would swap shipped binaries for a second opinion. The
  caches must never carry them either — a restore would write over the
  checked-in copies.
- `scripts/prepare-wine-ios.sh` downloads llvm-mingw, configures Wine for macOS
  (`wine/build-macos`, including the generated headers) and builds FreeType.
  `build/wineserver/build.sh`, `build/ntdll-unix/build.sh` and
  `build/win32u-unix/build.sh` then compile the Wine unix libs for iOS from
  source — `libwineserver.a` included, so there is no patch-an-existing-archive
  step any more and no base archive to supply.
- `scripts/publish-build-libs.sh` (ml792) tars archives for a `build-libs`
  release. It predates the self-building workflow and is now optional; the
  workflow does not consume it.
- The CI recipes above and `tools/validate-ios-bundle.py` /
  `tools/ar-macho-symbols.py` came from the sibling fork
  `LT-NP/uncrashed-ipad` (branch `fix/ci-ios-build`), which proved them on a
  hosted runner. They are GPL-licensed like the rest of the project; the
  `uncrashed` name survives only in two LLVM marker filenames.
- `node` and `python3` are available and are the way to sanity-check
  `madeira-jit.js` (syntax + unit-test the pure helpers).
- `build/*-tests` ship prebuilt `.exe`/binaries; they are not runnable on the
  build host.

## Release readiness

- Bundle id must stay `com.madeira.emulator` in the Xcode project, matching
  `app/source.json` and `scripts/deploy-thumper.sh`. A mismatch silently points
  sideload tooling at the wrong app.
- `Madeira.entitlements` deliberately carries `get-task-allow` (needed for the
  debugger to attach), `increased-memory-limit` and `allow-jit`.
  `source.json` also requests `extended-virtual-addressing`, which free Apple
  IDs cannot provision — it is injected post-install (see GetMoreRam notes in
  `ARCHITECTURE_ANALYSIS.md`), so do not add it to the entitlements file or a
  free-account signature will fail.
- `app/source.json` (`downloadURL`, `iconURL`, `size`) is still placeholder
  data and must be filled in before an official release.
