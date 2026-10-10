# Direct3D 11 games that stop at "device creation" with no frame

Symptom (INSIDE and other Unity games, other D3D11 titles): the process runs,
`Documents/madeira-diagnostics/last-launch.txt` says `NO FRAME PRESENTED`, and
the last stage ticked is `graphics-api  Direct3D 10/11 (DXMT)`.

This page separates what the code proves from what still needs an iPad log.

## Confirmed in the code

1. **The report could not show a created D3D11 device.** The DXMT DLLs the
   app ships are prebuilt and committed (`app/Madeira/arm64ec-windows/d3d11.dll`,
   which x86-64 games load, and `aarch64-windows/d3d11.dll`). Neither contains
   the `[madeira-diag] stage=device` / `stage=swapchain` lines that
   `patches/dxmt-ios-layer-safety.patch` adds (checked with `strings`: 0
   matches; both still contain the old `abort()` message "no exported symbols
   needed by DXMT"). So for every D3D11 game `last-launch.txt` stopped at
   `graphics-api` ("Using feature level") whether or not the device was
   created, and the old hint then said "created no Direct3D device yet". The
   stall hint now says that this state does not prove device creation hung,
   and a Metal layer handed to a swapchain gets its own hint.
   CI (`ci/build-ipa.sh`) rebuilds DXMT's unix side from source with the
   patches, but not these PE DLLs. **PE-side fixes (below) reach a device only
   after `d3d11.dll` / `dxgi.dll` / `winemetal.dll` are rebuilt** (build/dxmt-ios/README.md,
   "PE side") and copied into both DLL folders.

2. **An unbounded hop to the iOS main thread sits on the swapchain path.**
   DXMT's swapchain constructor calls `ResizeBuffers` ->
   `Presenter::changeLayerProperties` -> `MetalLayer_setProps`, and the first
   `Present` calls `Presenter::synchronizeLayerProperties` (with the D3D11
   device mutex held) -> `setProps` / `setColorSpace`. On the unix side all
   three went through `execute_on_main`, which was `dispatch_sync(main)` with
   no timeout. Desktop sessions had a second one (`winios_metal_layer_for_hwnd`).
   Whenever the main thread does not drain its queue, the game's render thread
   waits forever inside CreateSwapChain or the first Present, and nothing is
   logged.

3. **Several D3D11/DXGI methods ended the process.** `IMPLEMENT_ME` in
   `d3d11_private.h` is `abort()` (exit code 3). Among them were methods
   engines call routinely: the `ID3D11DeviceContext2` annotations
   (`IsAnnotationEnabled`, `BeginEventInt`, `SetMarkerInt`, `EndEvent`),
   `IDXGISwapChain1` `Get/SetBackgroundColor`, `Get/SetRotation`,
   `GetRestrictToOutput`, `IDXGISwapChain2` `Get/SetSourceSize`,
   `CheckMultisampleQualityLevels1` with flags, `QueryResourceResidency`.
   DXGI's own `IMPLEMENT_ME` was an empty endless loop (a silent hang; unused
   today).

4. **Session end could wait forever.** `wineserver_stop()` joined the server
   thread without a bound.

## Not proven (needs a device log)

- That INSIDE's stall is the main-thread hop. Nothing in Madeira's own code
  makes the main thread wait on a guest thread (input is queued, logging goes
  to a file, the game layer wait is bounded and never on main), so a true
  main/guest deadlock cycle was not found; a busy or stalled main thread is
  enough to cause the hang described in (2). The new `[freeze] MAIN THREAD
  unresponsive` and `[madeira-main-hop]` lines decide it.
- That INSIDE calls one of the aborting methods. A Unity player with its own
  crash handler may stay alive inside that handler rather than exit; the
  `... is not implemented.` line now becomes an `unimplemented-function`
  problem in the report.

## What changed

| Where | Change |
| --- | --- |
| `patches/dxmt-ios-no-hang.patch` (winemetal unix, built by CI) | `execute_on_main` waits at most `MADEIRA_MAIN_HOP_TIMEOUT_MS` (default 2000 ms, `0` = old unbounded behaviour). The block is claimed exactly once: by the main thread, or on timeout by the caller, which applies it inside an explicit `CATransaction` and logs `[madeira-main-hop]`. The caller never returns before the block ran, so the presenter's props it reads stay valid. `CreateMetalViewFromHWND` checks `win_data`. |
| same patch (PE, needs the DLL rebuild) | The methods in (3) answer as Windows does for an HWND swapchain; `CreateQuery1` and `IDXGIDevice::CreateSurface` return `E_NOTIMPL`; DXGI `IMPLEMENT_ME` aborts with a message instead of spinning. Tiled resources, `Flush1`, `SwapDeviceContextState`, `Read/WriteToSubresource` still abort: they need real implementations. |
| `app/Madeira/Winios/Winios.m` | Desktop-mode layer hop bounded the same way; on timeout the swapchain gets no surface (DXMT then fails CreateSwapChain instead of hanging). The freeze detector now probes the main thread every 0.25 s and logs `[freeze] MAIN THREAD unresponsive for N s` (foreground only, once per episode) followed by every thread's stack, and the recovery. |
| `app/Madeira/WineServerBridge.m` | `wineserver_stop()` waits at most `MADEIRA_WINESERVER_STOP_MS` (default 5000) for the server thread, then detaches it and logs `[session-cleanup]`. |
| `app/Madeira/LaunchDiagnostics.c` | Classifies `[madeira-main-hop]`, `[freeze] MAIN THREAD unresponsive` and DXMT's `<file>.cpp:<method> is not implemented.`; honest hints for "runtime loaded, no device line" and "layer handed out, no swapchain line". |
| `build/host-tests/check-main-thread-hops.py`, `check-launch-diagnostics.py` | Regression tests (below). |

## How to verify on the iPad

1. Build an IPA from this branch (the CI build includes the winemetal and app
   changes). For the PE-side changes, rebuild the DXMT PE DLLs on a Mac first.
2. Launch the game; when it stalls, wait ~30 s, then open
   `Documents/madeira-diagnostics/last-launch.txt` and `Documents/madeira-log.txt`.
3. Read it as follows:
   - `[madeira-main-hop]` problem / lines: the render thread waited for the
     main thread; it now continues. If the game then presents, this was the
     hang. Repeats with `[freeze] MAIN THREAD unresponsive` and the following
     `[thread-stacks]` block show what the main thread was doing.
   - `unimplemented-function` naming a `d3d11_*.cpp` method: the game called a
     method DXMT does not implement; with an old DLL that is an abort.
   - Stage `metal-layer` ticked without a swapchain line: swapchain creation or
     the first Present did not finish.
   - Only `graphics-api` and none of the above: the device may well exist; the
     game waits on something else (loading, audio, a video). Look at the
     `[thread-stacks]` dumps (periodic when the compositor is up, and after a
     main-thread stall) for the game's
     main and render threads.
4. Overrides for experiments in `madeira.cfg`: `env.MADEIRA_MAIN_HOP_TIMEOUT_MS`
   (0 restores the old wait), `env.MADEIRA_WINESERVER_STOP_MS`.

Host tests check the sources, the patch order and idempotency, and (with
`MADEIRA_LLVM_MINGW` pointing at llvm-mingw) compile the patched d3d11 sources
for arm64ec. They do not exercise the timing on iOS: that needs the device.
