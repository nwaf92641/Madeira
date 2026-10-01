# Working in this repository

Madeira is the iOS/iPadOS runtime that runs Windows games directly (Wine for
the Windows API, FEX for x86 translation, DXMT for Direct3D to Metal, its own
audio, video, input and controller paths). The runtime is the product; do not
replace it when adding a feature.

## Build and test

- The iOS app is built from `app/Madeira.xcodeproj` (SwiftUI, one app target).
  New Swift sources must be added to the target's Sources phase and get a file
  reference in `project.pbxproj`; the host tests check this.
- Host tests live in `build/host-tests/check-*.py`. They are plain scripts:
  `python3 build/host-tests/check-<name>.py`, exit code 0 on pass. Several
  compile a production Swift file with `swiftc` against a small harness, so
  `swiftc` must be on `PATH` (or set `SWIFTC`). Run the whole set with:

  ```sh
  for f in build/host-tests/check-*.py; do python3 "$f" >/dev/null || echo "FAIL $f"; done
  ```

  Some checks need out-of-tree inputs (a Wine/FEX checkout, `research/`
  submodules) and print `SKIP-FAIL`/`Wine source not found` without them; that
  is environmental, not a regression.
- Generators live in `build/tools/`. They write generated Swift/JSON into
  `app/Madeira/`; run them after editing their inputs, and run the matching
  `check-*` script, which usually verifies the generated file is current.

## Conventions

- Swift 6-ish, Foundation-first in model/engine files so they stay host
  testable: no UIKit in anything `build/host-tests` compiles.
- Comments explain *why* (an invariant, a workaround, a Wine behaviour), never
  what the next line does. Match the surrounding tone: full sentences, plain
  words, no changelog.
- Docs live in `docs/*.md`, one per subject, and are expected to be updated
  with the code. `docs/GAME_COMPATIBILITY.md` describes the compatibility
  engine and is the reference for adding games and dependencies.
- Do not commit `research/` clones, `__pycache__` or build output.

## Game compatibility (the part that changes most)

- Data, not code: `compat/dependencies.json`, `compat/games.json`,
  `compat/recipes.json` are the sources; `build/tools/gen-game-compat.py`
  builds `app/Madeira/compat.json` (run it with `--protonfixes DIR
  --winetricks DIR --bottles DIR --winlator DIR` to import upstream fixes,
  `--check` to compare without writing). Never hand-edit
  `app/Madeira/compat.json`.
- Upstream projects (`/workspace/refs/protonfixes`, `winetricks`,
  `bottles-deps`, `winlator-app`, `winlator`) are sources of fixes, not code to
  copy. Anything that depends on Linux, Vulkan, Proton's or Winlator's own
  runtime, root access or a kernel module is recorded as an unavailable fix on
  the title instead of being ported. A fix that only means something on that
  runtime (a Wine patch of theirs, a loader switch FEX does not have) gets the
  same treatment, with the Madeira counterpart named where one exists.
- A fix is expressed as a DLL override, a registry value, an environment
  variable or a launch argument, applied per launch and per game. `{app}` in a
  registry key or value is replaced by the launched executable's name, which is
  how Wine's per-application keys are written.
