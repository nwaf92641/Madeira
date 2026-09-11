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

## Build / test constraints in this environment

- The iOS app builds only with `xcodebuild` on macOS. There is no Linux build.
- Swift can still be *checked* without a Mac: `tools/test-device-capabilities.sh`
  installs nothing, adapts the one Apple-only import, shims `sysctl`, type-checks
  `DeviceCapabilities.swift` and asserts the pool/override policy tables. It
  covers that file only — `ContentView.swift` needs SwiftUI and cannot be
  compiled off-device, so changes there still need careful review.
- No `.github` workflows exist. The gates in `tools/` are run manually (or by
  whatever CI you add) — nothing runs them automatically, which is how the
  stale-embedded-script and prefix-symlink bugs shipped.
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
