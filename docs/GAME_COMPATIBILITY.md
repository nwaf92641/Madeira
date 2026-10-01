# Game compatibility

Madeira runs Windows games on Wine with FEX (x86/x86-64 translation), DXMT
(Direct3D to Metal) and its own audio, video and input stacks. Those are the
runtime. This document describes the layer above it: the system that works out
what a particular game needs and sets it up before the game starts.

The goal is that the user does not have to know any of it. Launching a game
should be the same whether it needs the Visual C++ runtime, a DirectX 9 helper
library, a DLL override, a registry key and a Windows version, or nothing at
all.

## The pipeline

```
Windows game (.exe)
   ↓  identify            appid / executable / GOG slug / install path
   ↓  compatibility data  compat.json (bundled seed + Documents update)
   ↓  detect              the executable's PE import table
   ↓  dependencies        runtimes, DirectX, media, audio, fonts, .NET …
   ↓  fixes               DLL overrides, registry, environment, Windows version
   ↓  launch config       MADEIRA_ARGS and the process environment
   ↓  Madeira runtime     Wine · FEX · DXMT · audio · video · input
   ↓  the game
```

Everything above the runtime is data-driven and per-game. Nothing changes the
runtime: a fix is expressed as an override, a registry value, an environment
variable or a launch argument, all scoped to one launch of one game.

## Where the code and data live

| Path | What it is |
| --- | --- |
| `app/Madeira/GameCompat.swift` | The engine: matching, dependency resolution, the plan, and the registry-text merge. Foundation-only and host-testable. |
| `app/Madeira/GameCompatView.swift` | Game details › Compatibility. |
| `compat/dependencies.json` | The dependency catalogue, hand-written: what each Windows component is and how Madeira satisfies it. |
| `compat/games.json` | Curated per-title profiles. |
| `build/tools/gen-game-compat.py` | Builds `app/Madeira/compat.json`; optionally imports Protonfixes game scripts. |
| `app/Madeira/compat.json` | The database the app ships, bundled as a resource. Generated, not edited by hand. |
| `Documents/madeira-compat/` | Optional update, payload DLLs and fonts (below). |

`LibraryEntry.applyEnvironment()` (in `Library.swift`) resolves the plan just
before the JIT pool is taken, and `LibraryModel.importedDLLs(for:)` supplies the
executable's imports, reusing the import reader the renderer badge already
uses.

## Matching

A launch is matched to a profile by, in order of specificity:

1. **Steam App ID** — exact.
2. **Executable name** — case-insensitive.
3. **GOG slug** — exact.
4. **Install-path fragment** — every `path_contains` entry is present.

All matches merge, most specific last, so a specific profile's values win. Most
games have exactly one match or none; a game with no match still benefits from
import detection, below.

## Dependencies

Each catalogue entry says what it is, which DLL imports select it, which files
it needs, and how Madeira can satisfy it:

- **builtin** — Wine or DXMT already provides it; only an override (to keep the
  game on the builtin) is applied.
- **override** — satisfied by overrides, registry or environment alone.
- **payload** — needs native files (a DirectX helper, a font, a runtime DLL).
  The files may be dropped in `Documents/madeira-compat/dlls/` (and
  `dlls/i386/` for 32-bit games) or already ship in the bundle (the 64-bit
  Microsoft VC++ runtime does). When a file is missing the game is still
  launched, and the missing component is reported rather than half-installed.
- **manual** — needs a separate Windows installer (most .NET Framework and
  DirectX redistributables). Madeira does not run those; wine-mono covers many
  .NET titles and the rest are reported.
- **partial** — some titles work (Media Foundation, .NET 4.x under wine-mono).
- **unsupported** — cannot work on iOS (kernel anti-cheat, Denuvo). Reported,
  never attempted, so a prefix is never broken by an unsuitable component.

A dependency can require others (`requires`), which are resolved first. The
user can add extras or exclude any component for one game.

## Automatic detection from the import table

A game's own executable names the DLLs it loads. That is the most reliable
statement of what it needs before it runs, so the engine reads the import table
and turns it into dependencies even when no profile matches:

- `d3dx9_43.dll` → the DirectX 9 helper library;
- `msvcp140.dll`, `vcruntime140.dll` → the Visual C++ runtime;
- `xinput1_3.dll` → the builtin XInput path;
- `xactengine3_7.dll` → the XACT audio engine;
- `physxloader.dll`, `OpenAL32.dll`, `quartz.dll`, `msxml6.dll`, …

Names are matched lowercased and with or without the `.dll` extension. This is
what makes the system general: a title nobody has written a profile for still
gets the right overrides and an accurate report of anything that is missing.

## Isolation between games

Madeira uses one shared prefix, so a fix must not leak to another game:

- **DLL overrides** go through `WINEDLLOVERRIDES`, which is per launch, and are
  merged with whatever the launch already carried (the plan's entries win).
- **Windows version** goes to `HKCU\Software\Wine\AppDefaults\<exe>\Version`,
  which Wine applies to that image only.
- **Launch arguments** are appended to `MADEIRA_ARGS` for the session.
- **Registry values** from a dependency are global by nature (they describe a
  component, not a game) and are written idempotently.

## Registry writes

Registry values are written into the prefix's `user.reg` before
`wineserver_start`, so the server loads them and writes them back on stop. The
file is only touched when the prefix has already been seeded, values are
updated in place rather than duplicated, and a one-time `.madeira-bak` backup is
kept. Writing is skipped while a session is running, because wineserver holds
the registry in memory.

## The user interface

Game details › **Compatibility** shows what will happen for one game: the
matched profile, engine and rating, the resolved dependencies, the DLL
overrides, and anything that could not be satisfied. From here the user can:

- turn automatic compatibility off for a game;
- force a Windows version;
- add extra dependency ids.

Each control writes into the entry's `compat` field, so it is per game and
survives across sessions.

## Adding a game profile

Add an object to `compat/games.json`:

```json
{
  "title": "Example Game",
  "appid": 123456,
  "executables": ["example.exe"],
  "dependencies": ["vcrun2019", "d3dx9"],
  "dll_overrides": {"dinput8": "b"},
  "windows_version": "win10",
  "launch_arguments": "-dx11",
  "registry": [
    {"hive": "HKCU", "key": "Software\\Vendor\\Game", "name": "SafeMode",
     "type": "REG_DWORD", "value": "0"}
  ],
  "env": {"MADEIRA_WG_64BIT": "1"},
  "issues": ["The intro video needs Media Foundation."],
  "source": "protonfixes"
}
```

Then rebuild the database:

```sh
build/tools/gen-game-compat.py
```

`--protonfixes DIR` also imports profiles from a Protonfixes checkout: the App
ID, dependencies (winetricks verbs the catalogue knows), DLL overrides,
environment variables, registry keys and launch arguments, plus the logic
fixes it cannot express as data (recorded as notes). Curated entries always win
over an imported profile with the same key.

Adding a dependency is the same idea in `compat/dependencies.json`; set
`imports` so the import table can select it, `dlls` for the files a payload
needs, and `support` for how it is satisfied. The generator validates the
database: every referenced dependency must exist, override orders and registry
types must be known, and game keys must be unique.

## Updating without a rebuild

The bundled `compat.json` is a seed. A newer database placed at
`Documents/madeira-compat/compat.json` is merged over it at launch — games and
dependencies present in the update win, everything else is kept. That is how a
new game profile or a fixed one can reach a device without rebuilding the app.
Native payload files live beside it, in `Documents/madeira-compat/dlls/` and
`Documents/madeira-compat/fonts/`.

## What was taken from where

The compatibility knowledge comes from the Wine ecosystem rather than from
re-deriving it per game:

- **Winetricks** — the dependency vocabulary: which runtimes, DirectX, media
  and font components games need, and which DLLs and registry keys each one
  sets. The catalogue's component ids are its verbs.
- **Bottles and Bottles dependencies** — the shape of a dependency definition
  (files, overrides, registry, installer) and the DLLs each component provides.
- **Protonfixes** — game-specific fixes: App IDs, winetricks verbs, DLL
  overrides, environment variables, registry keys and launch arguments. The
  generator imports these into per-game profiles.
- **Proton and GE-Proton** — the kinds of fixes that make specific titles work,
  used to shape the catalogue and the profiles.
- **Lutris** — how installers, prefixes and per-game configuration are
  described, which informed the profile model.
- **Wine** — the authority on the overrides, `AppDefaults` keys, registry
  format and DLL behaviour this system emits.
- **Apple Game Porting Toolkit** — the reference for a Wine-adjacent stack on
  Apple silicon; Madeira's stack is its own (FEX, DXMT, Metal), so this informed
  direction rather than code.

What was deliberately **not** carried over: Proton/DXVK/VKD3D (Madeira uses
DXMT and Metal, not Vulkan), winetricks' bash and its `.verb` files (the data
is re-expressed as JSON the app can read), Bottles' installers and runtimes
(Madeira's prefix and runtimes are its own), and anything needing root, a
kernel module or a Linux service (anti-cheat, some DRM), which is represented
as an explicitly unsupported compatibility case instead.

## Testing

`build/host-tests/check-game-compat.py` compiles the engine with a harness and
checks matching, detection, resolution, the plan, the registry merge, the
overlay merge and the bundled database, then checks the source wiring. The
database itself is validated for dangling references and unique keys.
