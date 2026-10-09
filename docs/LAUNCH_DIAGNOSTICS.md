# Launch diagnostics (black screens)

A black screen looks the same to the player whatever the cause: the program
never started, a DLL is missing, Direct3D refused the device or the swapchain,
a shader did not translate, Metal gave no drawable, the window exists but is
not drawn, or the game waits on a video it cannot play. Madeira keeps one
record per launch that says how far the launch got on the way to the first
frame, and what went wrong on the way.

## Where to read it (on the iPad)

Files app › On My iPad › Madeira › `madeira-diagnostics/`

| File | What it is |
| --- | --- |
| `last-launch.txt` | The report for the latest launch, readable as is. |
| `last-launch.json` | The same, for tools. |
| `previous-launch.txt` | The launch before it. |

The full log stays in `Documents/madeira-log.txt`. At the end of a session the
log also gets one line, `[launch-diagnostics] first frame: … verdict: …`, so
the in-app log names the outcome. No Mac or computer is needed.

## What the report says

- **Process** and **Display** are separate lines. "Process: running" only
  means Wine runs the program; "Display: FIRST FRAME PRESENTED" means a frame
  reached Metal (the present counter that DXMT and the D3D12 runtime share
  increased after this launch began). A live process with no frame is never
  reported as success.
- **Stages**, each with the time since launch: launch, wine-started,
  child-process (a launcher started the game), graphics-api, device,
  metal-layer, swapchain, first-present, gdi-window (the app drew a launcher or
  dialog window), process-exit.
- **Where it stopped**: a hint that follows from the furthest stage reached.
- **Problems**, each in one category with the error text that was printed and
  a hint: `process-failure`, `missing-dll`, `graphics-device-failure`,
  `swapchain-failure`, `shader-translation-failure`, `metal-present-failure`,
  `window-visibility`, `video-init-failure`, `unimplemented-function`,
  `dependency-load-failure`, `architecture-mismatch`, `wine-init-failure`,
  `audio-init-failure`, `unclassified`.
- **Verdict**: the most decisive category (Wine init, then missing DLL,
  architecture, unimplemented function, dependency, process, device,
  swapchain, present, window, shader, video, audio), or `none` once a frame
  was presented without problems.

### Which Wine message lands where

| Wine prints | Category |
|---|---|
| `Library X.dll (which is needed by ...) not found` | `missing-dll` |
| `Loading library X (which is needed by Y) failed (error c000007b)` | `architecture-mismatch` (a 32-bit DLL in a 64-bit process or the reverse) |
| `... failed (error c0000139)` | `unimplemented-function` (an export the DLL does not have) |
| `... failed (error <other>)` | `dependency-load-failure` (the DLL exists; its own init or import failed) |
| `Call from ... to unimplemented function dll.fn` | `unimplemented-function` |
| `Could not find dependent assembly` (side-by-side) | `dependency-load-failure` |
| `wine: failed to start ...`, `wine: could not load kernel32.dll` | `wine-init-failure` |
| `err:xaudio2`, `err:xact3`, `err:mmdevapi`, `err:dsound`, `err:winmm`, `err:msacm` | `audio-init-failure` |

## How it is fed

`app/Madeira/LaunchDiagnostics.c` (plain C, host-tested) is fed two ways:

- native calls: `WineProcessBridge.m` (launch, Wine started with the target's
  architecture, the program's exit and NTSTATUS, the child-process wait, the
  present counter sampled every 0.5 s), `IOSDisplayShim.m` (the game layer
  handed to a swapchain);
- every log line, from the tail `LogStore.swift` already runs (also while the
  on-screen log is suppressed). It recognises the messages Wine, DXMT, the
  D3D12 runtime and the app really print, and lines of the form
  `[madeira-diag] stage=<stage> ok=0|1 cat=<category> detail=<text>` that PE-side
  code, which cannot call into the app, writes for it.

The work per log line is a few byte comparisons for lines that cannot match;
the report file is written only when something changed.

This is the stage-by-stage view of one launch. `CompatDiagnosis.swift`
(docs/GAME_COMPATIBILITY.md) still classifies a finished session for the
library's automatic fallback; the two read the same log.

### Lines the patched graphics code writes

With `patches/dxmt-ios-layer-safety.patch` built in (build/dxmt-ios/README.md):

| Line | Meaning |
| --- | --- |
| `[madeira-diag] stage=device ok=1 detail=Direct3D 11 (DXMT), feature level 0x...` | D3D11 device created |
| `[madeira-diag] stage=swapchain ok=1 detail=d3d11 WxH format N` | D3D11 swapchain created |
| `[madeira-diag] stage=swapchain ok=0 cat=swapchain-failure ...` | no Metal layer for the window, or a format DXGI does not allow; CreateSwapChain fails instead of abort() |
| `[madeira-display] CAMetalLayer refused pixel format A (...)` | the layer took a documented format instead; frames are converted |
| `[madeira-diag] stage=present ok=0 cat=metal-present-failure detail=d3d12: ...` | the D3D12 runtime dropped a frame it could neither copy nor convert (app built without MadeiraCtl op 8) |

## Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `MADEIRA_LAYER_WAIT_MS` | 5000 | How long a swapchain created before the game view registered its layer waits for it, instead of getting no surface. 0: no wait. |
| `MADEIRA_WAIT_CHILDREN` | on | `0`: the session ends with the main process even while a game it started still runs. |
| `MADEIRA_WAIT_CHILDREN_MAX_S` | none | Caps that wait, in seconds. |

## Tests

- `build/host-tests/check-launch-diagnostics.py`: real log shapes per category,
  "running" kept apart from "first frame", report files and rotation, noise
  cost.
- `build/host-tests/check-child-slots.py`: launcher children keep the session
  and every exit path frees its slot.
- `build/host-tests/check-layer-format.py`: the layer-format fallback table,
  the D3D12 copy-or-convert decision, and that the DXMT patch applies.

`.github/workflows/host-tests.yml` runs these and the other compiler-only host
tests on every pull request. They run on any machine with a C compiler. What the report says on a device
for a given game has to be checked on an iPad.
