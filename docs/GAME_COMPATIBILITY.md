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
   ↓  detect              the executable's PE import table, the files beside it, its architecture
   ↓  baseline            compat/baseline.json: what every Windows program gets
   ↓  rules               compat/rules.json: what follows from what the program is
   ↓  profile             what the title specifically needs
   ↓  dependencies        runtimes, DirectX, media, audio, fonts, .NET …
   ↓  fixes               reusable recipes: DLL overrides, registry, environment
   ↓  launch config       MADEIRA_ARGS and the process environment
   ↓  Madeira runtime     Wine · FEX · DXMT · audio · video · input
   ↓  the game

the session ends
   ↓  classify            CompatDiagnosis.swift reads the log and the exit status
   ↓  record              the verdict is kept on the library entry
   ↓  retry               the remedy for that category, or the title's next alternative
```

Everything above the runtime is data-driven and per-launch. Nothing changes the
runtime: a fix is expressed as an override, a registry value, an environment
variable or a launch argument, all scoped to one launch of one game.

## Where the code and data live

| Path | What it is |
| --- | --- |
| `app/Madeira/GameCompat.swift` | The engine: matching, the baseline, the rules, dependency resolution, recipes, fallbacks, remedies, the plan, and the registry-text merge. Foundation-only and host-testable. |
| `app/Madeira/CompatDiagnosis.swift` | Classifies a finished session into the categories a user can act on, and the result recorded on the entry. |
| `app/Madeira/GameCompatView.swift` | Game details › Compatibility. |
| `compat/dependencies.json` | The dependency catalogue, hand-written: what each Windows component is and how Madeira satisfies it. |
| `compat/games.json` | Curated per-title profiles. |
| `compat/recipes.json` | Reusable fixes, referenced by name from a dependency or a profile. |
| `compat/baseline.json` | The universal configuration, applied to every launch, matched or not. |
| `compat/rules.json` | General rules (`when` → what to apply) and the remedies used to retry a failed session. |
| `compat/wine-modules.json` | The modules this runtime provides in each architecture, the names Wine never built, and the ones the iOS build leaves out. Generated from the Wine sources and the built farms by `build/tools/gen-wine-modules.py` (below). |
| `build/tools/gen-game-compat.py` | Builds `app/Madeira/compat.json`; optionally imports Protonfixes game scripts, Winetricks verb metadata and Bottles dependency definitions. |
| `build/tools/gen-wine-modules.py` | Builds `compat/wine-modules.json` from a Wine `configure` output and `build/wine-i386/build.sh`. |
| `app/Madeira/compat.json` | The database the app ships, bundled as a resource. Generated, not edited by hand. |
| `Documents/madeira-compat/` | Optional update, payload DLLs and fonts (below). |

`LibraryEntry.applyEnvironment()` (in `Library.swift`) resolves the plan just
before the JIT pool is taken; `LibraryModel` supplies what the rules match on:
the executable's imports (`importedDLLs(for:)`, reusing the import reader the
renderer badge already uses), the names in its own folder (`folderNames(for:)`)
and its architecture from the PE header (`programBits(for:)`).

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
  Microsoft VC++ runtime does). A file the title ships in its own folder counts
  as supplied — half the library carries its `d3dx9_43.dll` or its `oo2core` —
  and only a file that is nowhere is reported. When it is missing the game is
  still launched, and the component is reported rather than half-installed.
- **manual** — needs a separate Windows installer (most .NET Framework and
  DirectX redistributables). Madeira does not run those; wine-mono covers many
  .NET titles and the rest are reported.
- **partial** — some titles work (Media Foundation, .NET 4.x under wine-mono).
- **unsupported** — cannot work on iOS (kernel anti-cheat, Denuvo). Reported,
  never attempted, so a prefix is never broken by an unsuitable component.

A dependency can require others (`requires`), which are resolved first. The
user can add extras or exclude any component for one game.

The catalogue is also what makes a title work with no profile at all: every
entry lists the DLL names it answers for, so a game nobody has written a
profile for is still recognised from what its executable imports, and the
components behind it are set up or reported. The lists come from the upstream
projects rather than from guessing at DLL names — Winetricks' verbs for the
run times, DirectX, media, audio and font components, Bottles for the files each
one provides, and Winlator's Windows components for the DLL sets a full
DirectX, DirectMusic, DirectShow, DirectPlay, XACT or XAudio install contains,
which is how the gaps between the families (`d3dx10_33` to `d3dx10_41`,
`d3dcsx_42`, the DirectPlay providers, the older XACT engines) are covered.
Wine's own addons are entries too: `wine-mono` and `wine-gecko` are `manual`,
so a managed or browser-based title is reported as needing them instead of
being handed a Microsoft installer it cannot use.

## Reusable fixes (recipes)

Most Wine fixes are a small combination: override these DLLs, set this variable,
write this registry value, add this argument, make sure this component is
present. They are written once in `compat/recipes.json` and referenced by name,
from a dependency or a profile:

```json
{
  "steam-no-overlay": {
    "title": "Disable the Steam overlay",
    "category": "launch",
    "dll_overrides": {"gameoverlayrenderer64": "", "gameoverlayrenderer": ""},
    "notes": "The overlay hook crashes some titles when it is injected."
  }
}
```

A recipe is applied at most once, after the components it belongs to. This is
what keeps the database from repeating the same override in hundreds of
profiles, and it is the seam through which an upstream fix (Protonfixes,
Winetricks, Bottles, Winlator) becomes a Madeira fix without being rewritten
per game.

A recipe can also express a setting for one program rather than a whole prefix:
`{app}` in a registry key or value is replaced by the launched executable's
name, which is how `no-3d-for-helper` and `legacy-d3d-tuning` write Wine's own
per-application `Direct3D` key. Wine reads `renderer`, `csmt`,
`VideoMemorySize`, `VideoPciVendorID` and `VideoPciDeviceID` from exactly that
key, so a helper process, a launcher and the game itself can each see a
different device without a global setting.

## Fallbacks: a second and third attempt

A curated profile can carry `fallbacks`. Each one is a variation that has been
known to make the same title work when the first configuration does not:

```json
"fallbacks": [
  {"name": "Wine's own dinput8", "dll_overrides": {"dinput8": "b"},
   "note": "when the game ships its own proxy DLL"}
]
```

The Compatibility section shows them as the game's configurations, and the
first one is used. An alternative can carry its own `disable` list, which
removes components and recipes the base plan would otherwise apply.

## Automatic detection from the import table

A game's own executable names the DLLs it loads. That is the most reliable
statement of what it needs before it runs, so the engine reads the import table
and turns it into dependencies even when no profile matches:

- `d3dx9_43.dll` → the DirectX 9 helper library;
- `msvcp140.dll`, `vcruntime140.dll` → the Visual C++ runtime;
- `xinput1_3.dll` → the builtin XInput path;
- `xactengine3_7.dll` → the XACT audio engine;
- `physxloader.dll`, `OpenAL32.dll`, `quartz.dll`, `msxml6.dll`, …

Names are matched lowercased and with or without the `.dll` extension. The
catalogue recognises more than 360 names — every `d3dx9_*`, every
`d3dcompiler_*`, the XAudio2 and XACT version families, the Visual C++ and UCRT
runtimes, the media and audio DLLs, the engines' own players (mss32, bink,
smacker, FMOD) and the vendor libraries (NVAPI, AMD AGS, PhysX, Oodle) whose
right answer is a stub override. This is what makes the system general: a title
nobody has written a profile for still gets the right overrides and an accurate
report of anything that is missing.

Two of those families are worth calling out because they are decisions rather
than dependencies:

- **GPU vendor libraries** are pinned to the Wine stub. There is no NVIDIA or
  AMD driver behind Metal, and a game that loads the real one usually crashes;
  the stub answers the queries it makes and the game keeps running. This is
  Protonfixes' "disable NVAPI" fix, expressed as data.
- **Vulkan** is `partial`, not a missing file. A title that merely imports
  `vulkan-1.dll` is told apart from one that refuses to start without a Vulkan
  device, and the report points at the Direct3D renderer instead.

## A program nobody has profiled

Import detection covers the components a program *links*. The rest of the
universal path covers everything else a Windows program can be, so an arbitrary
`.exe` — a game, a launcher, a map editor, an installer — gets a working
configuration without anyone writing a profile for it.

**The baseline** (`compat/baseline.json`) is applied to every launch before
anything else and sets nothing but the Windows version (10, which is what the
modern Windows APIs and store clients check for). It is the floor, not a
default: a profile, a rule, a fallback or the user's own override wins over it.
The Compatibility section says "no profile matches; the universal configuration
applies" when that is what happened, so a title that works by baseline is not
mistaken for one with a broken profile.

**General rules** (`compat/rules.json`) follow from what the program is:

```json
{"id": "legacy-directdraw", "title": "DirectDraw-era Direct3D",
 "when": {"imports": ["ddraw", "d3d8", "d3drm"]},
 "recipes": ["legacy-d3d-tuning"],
 "note": "These titles read the adapter and the video-memory report, and
          arithmetic is done at 32-bit precision by default in Wine's DLLs."}
```

A rule's conditions are matched against the four things the launcher knows
before the program runs: the DLLs it imports, the names in its own folder
(files and directories, for what is loaded at runtime rather than linked — an
anti-cheat, a copy-protection layer, a store client's own files), a fragment of
its name (`crashhandler`, `webhelper`), and its architecture from the PE header
(32 or 64). Every condition present has to hold; within one list any name is
enough. Rules sit above the baseline and below a profile, so a curated title
keeps its own configuration and everything else falls back to the general
answer.

Rules are what make anti-cheat, DRM and the store clients visible without a
profile: `EasyAntiCheat_x64.dll` beside the program names the anti-cheat
component, which is `unsupported`, which stops the retry ladder at the first
attempt instead of spending sessions on it — while an offline title that bundles
the same files still runs.

**Remedies** close the loop for a program that fails: when a session ends, its
diagnosis category picks a variation to try next (`remedies` in
`compat/rules.json`), and that becomes another attempt on the very next launch —
a Direct3D 9 failure is retried with the DirectDraw-era adapter identity, an
audio failure with the DirectShow wave renderer, a launch failure with the
loader forced on or off. The attempts are, in order: the profile, its own
fallbacks, one remedy for each category the last session failed with (at most
two), and then nothing. The user sees a plain "attempt 2 of 3" and the game
details section, and a title that fails for a reason no configuration can
answer (anti-cheat, DRM) is not retried at all.

**What the runtime does not have** is reported rather than guessed.
`compat/wine-modules.json` is generated from the Wine sources and from the two
PE farms the build produces: the modules the runtime provides (648 names in the
32-bit build, including the ones DXMT answers for, and 137 in the 64-bit farm),
the names Wine has never built in any architecture (`mfcore`, the DirectX SDK
compilers `d3dcompiler_44`/`_45` and `dxil`, AMD's `amdxc64`, each with the
reason), and the ones the iOS build leaves out (Indeo's `ir50_32`,
`vulkan-1.dll`, `opencl.dll` and the rest, each with the build script's own
reason).

The two farms are not the same set, so "does not ship it" is answered per
launch rather than once: `quartz`, `winegstreamer`, `wmvcore`, `devenum`,
`xaudio2_7`, `xactengine3_7` and the rest of the media and DirectShow family
are 32-bit only in the farm the app ships today, and a 64-bit title that
imports one is told that — the 64-bit farm is short, not the runtime. The same
is true of the whole `d3dx9_24`–`d3dx9_42` and `d3dx10_*` range, `d2d1`, `msi`,
`msxml3/4/6`, `gdiplus`, `riched20`, `glu32`, `d3d8` and `ddraw`. What the
64-bit farm does carry is the core a game needs first — the Direct3D 9/10/11/12
path (DXMT's `d3d9`, `d3d10core`, `d3d11`, `dxgi`, `d3dx9_43`,
`d3dcompiler_43`/`_47`), `opengl32`, `winmm`, `dsound`,
`dinput8`, the `xinput` family and the Media Foundation PE side.

That shortfall is a build step, not a property of the port, and it has been
measured: `build/wine-arm64ec/build.sh` is the 64-bit counterpart of
`build/wine-i386/build.sh`, installing every module the configured tree has a
rule for minus a policy list, each entry with its reason. The audit behind that
list — which module can be installed as it is, which loads with one failing
feature, and which cannot load at all — is in "The 64-bit farm's missing
modules" below, with the evidence for each decision; the farm itself is
installed by running the script on the build machine (docs/BUILDING.md).

An import that nothing in the catalogue, no rule and no module accounts for,
and that the runtime does not ship either, is listed on the game details screen
with that answer; a name Wine never built is listed with the reason, so "bring
your own copy" is the advice instead of silence. That is the honest answer for a
component nobody has described yet, and it is how the gaps get found.

What a component may claim follows from the same list, and the generator
enforces it: a `builtin` entry may only name modules the runtime really has, a
`partial` one may not claim a name Wine never built without naming the file it
installs itself, and a DLL override is only written for a module the runtime
serves. An override that pins the builtin alone for a module nothing implements
cannot resolve and stops the title's own copy from loading, so imports from
other launchers are corrected when they are read (`*dsound=b` is a Proton
wildcard, not a Wine name) and a pin that cannot resolve is dropped and
recorded on the title instead of being applied. The check is per launch,
because the answer depends on the architecture: a `dinput8=b` a 32-bit title
keeps is dropped for a 64-bit one whose farm carries a different set, with the
reason in the plan. The GPU vendor libraries are the other worked example:
Protonfixes' "disable NVAPI" fix pins them to the builtin because DXVK-NVAPI
answers on Proton, there is no such substitute here, and no override is written
at all, so a title that ships its own copy still loads it.

A title that plays video or uses XMA is a second one. Every decoder here runs
through `winegstreamer`'s unix side, and a 64-bit caller only gets it with
`MADEIRA_WG_64BIT=1`, so `rules.json` sets that switch for a 64-bit program
whose imports name Media Foundation, quartz or the XAudio/XACT engines — and
says in the rule's own note that this build's 64-bit farm does not carry
`winegstreamer.dll` yet, so the title is told what it is short of. A 32-bit
(WoW64) caller has the unix side by default.

Two families are deliberately not reported:

- **API sets.** A modern program imports a dozen `api-ms-win-*.dll` and
  `ext-ms-win-*.dll` names that are not files anywhere: the loader resolves them
  to the module that implements the contract, `ucrtbase` for the C runtime sets
  and `kernelbase` for the core ones. They are carried as prefixes in the
  database (`api_set_prefixes`), so a program with a clean configuration is
  reported as clean instead of being buried under names nobody can act on.
- **The names DXMT answers for.** `d3d9`, `d3d10core`, `d3d11`, `dxgi` and
  `winemetal` are DXMT's, not Wine's, and the iOS build deliberately skips
  Wine's own copies, so they count as provided.

`build/tools/gen-wine-modules.py --configure <wine>/configure` rewrites
`compat/wine-modules.json` (and `--check` fails when it is out of date), which is
what keeps the 32-bit list honest when the runtime's Wine moves;
`build/tools/gen-game-compat.py` reads the 64-bit half from the farm
directories themselves, so the database describes what is shipped rather than
what a previous build script intended to ship.

## The 64-bit farm's missing modules, and how each class is closed

A 64-bit program imports its DLLs from `app/Madeira/arm64ec-windows` (or, for a
native ARM64 guest, `app/Madeira/aarch64-windows`); a name that is not in that
directory cannot be loaded, whatever the 32-bit farm carries. The farm had been
assembled by hand, module by module, so the first question of the universal
stage was what it is missing and what closing each gap takes.

The audit read every module Wine 11.4 builds — `dlls/*/Makefile.in`'s `MODULE`,
`IMPORTLIB`, `IMPORTS`, `DELAYIMPORTS` and `UNIXLIB` — resolved each import the
way Wine's build does (`d3dcompiler` is an `IMPORTLIB` alias for
`d3dcompiler_47.dll`, `$(X_LIBS)` comes from `configure.ac`'s
`WINE_EXTLIB_FLAGS`), and compared the result against the farms' real PE import
tables (`build/tools/pe-imports.py`, which is also what the host test and the
build's own gate use). The loader's behaviour came from this port, not from a
guess: `build/ntdll-unix/virtual_ios.c` replaces a unix lib it does not have
with a stub table whose every entry returns `STATUS_NOT_SUPPORTED`, so a module
loads exactly when its `DllMain` does not fail on that status.

725 modules in the tree, 648 in the 32-bit list, 278 files in the two 64-bit
farms (137 module names plus 37 programs): **513 tree modules the 64-bit farm
does not carry**, and they fall into three classes.

| Class | Count | What it takes |
| --- | --- | --- |
| Installable as they are: `quartz`, `devenum`, `d3dx9_24`–`d3dx9_42`, `d3dx10_*`, `d3dx11_*`, `d2d1`, `dwrite`, the `xaudio2_*`/`xapofx`/`x3daudio`/`xactengine*` series, `msi`/`msiexec`/`mspatcha`/`sxs`, `msxml3/4/6`, `gdiplus`, `riched20`, `glu32`, `ddraw`, `d3d8`, `dinput`, `hid`, `wintrust`, the `mf*` Media Foundation set, `evr`, `windowscodecs`, `wm*`, `ir50_32` | 494 | build the PE and install it: every load-time import is satisfied by the farm plus what the same build installs |
| Load, with one failing feature: `qcap`, `avicap32`, `winedmo`, `odbc32`, `winscard`, `kerberos`, `wpcap` | 7 | install the PE: the capture device, DMO decoder, database client, pcsc, gssapi or libpcap behind its unix side does not exist here, the call fails, and the loader says so in one line |
| Cannot load at all: `localspl`, `wineps.drv`, `msv1_0`, `capi2032`, `ctapi32`, `sane.ds`, `opencl`, `winevulkan` | 8 | a unix side, ported the way `winegstreamer`'s was; until then they are named skips in the build policy, with the reason. The host display and audio drivers (`winemac`, `winex11`, `winewayland`, `wineandroid`, `winealsa`, `winepulse`, `wineoss`, `winecoreaudio`) are the same class by construction |

(`winegstreamer` itself belongs to the second class with its unix side already
ported — the FFmpeg-backed subset `build/ntdll-unix/winegstreamer_unixlib_ios.c`
— so its PE is installable and fully served; it is the one module there whose
feature does not fail.)

Two rules produce the classification, and both are properties of the tree:

- **The import closure.** A module can only be installed if every one of its
  load-time imports is installed too, and `IMPORTS` is load-time while
  `DELAYIMPORTS` is not. That is what makes `devenum` (imports `avicap32`),
  `mfsrcsnk`/`mfmp4srcsnk`/`mfasfsrcsnk` (import `winedmo`) and `msi` (imports
  `odbccp32`) installable once those three are, and it is why a policy that
  skips one of them has to skip whatever imports it.
- **Whether `DllMain` survives without its unix side.** `localspl`
  (`dlls/localspl/localmon.c:100`), `wineps.drv` (`dlls/wineps.drv/init.c:301`),
  `msv1_0` (`dlls/msv1_0/main.c:1623`), `capi2032`
  (`dlls/capi2032/cap20wxx.c:41`), `ctapi32` (`dlls/ctapi32/ctapi32.c:105`),
  `sane.ds` (`dlls/sane.ds/sane_main.c:43`) and `opencl`
  (`dlls/opencl/pe_wrappers.c:285`) return FALSE and become modules that will
  not load; `qcap` (lazy: `dlls/qcap/vfwcapture.c:904`), `winedmo`
  (`dlls/winedmo/main.c:112`), `winscard` (`dlls/winscard/winscard.c:975`),
  `odbc32` (`dlls/odbc32/proxyodbc.c:8231`) and `wpcap`
  (`dlls/wpcap/wpcap.c:1478`) ignore or defer it, so they load and only the
  feature that needs the host fails.

Nothing here is a per-game fix. The modules are the ones Wine's source says a
program may import, so installing them is what lets an unprofiled title run,
and the same classification is what tells a title honestly when a name exists
but its feature does not.

Three things keep it true rather than remembered:

- `build/wine-arm64ec/build.sh` installs the farm and then runs the closure
  check over what it installed, with the tool above: a module whose load-time
  imports are not all present fails the build instead of shipping. That gap is
  real — `app/Madeira/arm64ec-windows/bthprops.cpl` has been in the farm
  importing `bluetoothapis.dll`, which no farm carried, while the database
  listed the module as provided: a guest opening the Bluetooth applet got a
  load failure and the app had no name for it.
- `build/host-tests/check-pe-imports.py` checks the committed farms the same
  way: each module's PE machine is checked against the directory it sits in
  (ARM64EC images report `x86-64`), no load-time import may be unresolved (the
  one known gap is named in the test with the rebuild that closes it), the
  delay-load gaps must stay within the recorded set, and `compat.json`'s
  `wine_modules_64` must be exactly what the farm directories hold — so the
  database cannot drift from the bundle.
- The build script's policy is the classification's third column: every entry
  carries its reason, and the test fails if a module classed as installable is
  silently skipped instead.

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

## Filesystem and save paths

Windows programs write to the Windows profile inside the shared prefix, so the
shell folders have to be real directories there: `%USERPROFILE%` is
`drive_c/users/<user>`, and `Documents`, `Saved Games`, `AppData\Local`,
`AppData\LocalLow` and `AppData\Roaming` are what a title resolves before it
writes a save or a config file. The shipped template once carried them as
absolute symlinks into the build machine's home directory, which dangle on
every device: every shell-folder lookup failed, and a title that writes to My
Documents could not produce a log at all. They are ordinary directories now,
and `scripts/build-prefix-snapshot.sh` runs `tools/check-prefix-template.sh` on
the archive it just wrote and deletes it when it carries such a link, so the
class of bug cannot ship again.

Nothing else needs configuring for the common cases. A title that writes beside
its executable works because `drive_c` is writable and there is no UAC
virtualisation to satisfy (in Wine, a write to `C:\Program Files` simply
succeeds). When a save or config write fails anyway, the session is classified
as `save_path` and the details screen reports it — there is no automatic remedy,
because no Windows or Wine setting makes a location writable that the device
does not allow writing to, and pretending otherwise would be a retry that cannot
succeed.

## When a game does not work

Searching for a fix is the thing this system exists to remove, so a failure is
not left for the user to interpret. When a session ends, `CompatDiagnosis.swift`
reads the log (the head, where the DLL loader reports what it could not find,
and the tail, where the crash is), the process exit status and whether a frame
ever reached the screen, and classifies it:

| Class | What it means |
| --- | --- |
| `dependency` | A Windows component or DLL is missing. |
| `dll` | A plugin shipped with the game failed to load. |
| `dx9`, `dx11`, `dx12` | A graphics path failed. |
| `media`, `audio`, `input` | Playback, sound, controller or mouse. |
| `registry`, `save_path` | A value or a folder the game writes to. |
| `wine_fex` | Wine or the x86 emulator hit an unimplemented call. |
| `drm`, `anticheat` | Cannot work on Madeira; reported, never retried. |
| `launch` | The process started but never reached a frame. |

The result is stored on the library entry: the game details show what happened
and the next thing that will be tried, and the library row carries a badge after
a failure. If the title has a fallback and this attempt failed, the next launch
uses it — that is the automatic retry. A fatal classification (anti-cheat, DRM)
stops the ladder, because no configuration will change it, and the user can go
back to the first configuration from the same section.

Madeira's own bracketed log lines (`[xinput]`, `[jit]`, `[render]`, …) are
stripped before classification, so the app's diagnostics can never be mistaken
for a game's failure.

## The user interface

Game details › **Compatibility** shows what will happen for one game: the
matched profile, engine and rating, the resolved dependencies, the fixes being
applied, the DLL overrides, the configurations (fallbacks) it can use, how the
last session ended, and anything that could not be satisfied. From here the user
can:

- turn automatic compatibility off for a game;
- choose another configuration (attempt) for the game, or go back to the first
  one after an automatic retry;
- force a Windows version;
- add extra dependency ids.

Each control writes into the entry itself, so it is per game and survives across
sessions.

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
  "recipes": ["dinput-proxy-off"],
  "fallbacks": [
    {"name": "Builtin DirectInput", "dll_overrides": {"dinput8": "b"},
     "note": "when the game ships its own proxy DLL"}
  ],
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
fixes it cannot express as data (recorded as notes). Every store-specific
directory is imported, not only Steam — `gamefixes-gog`, `gamefixes-egs`,
`gamefixes-umu` and the rest — because they key their fixes by the same App ID.
Curated entries always win over an imported profile with the same key.
`--winetricks DIR` widens the dependency catalogue from the verb metadata,
`--bottles DIR` from the Bottles dependency definitions, and `--winlator DIR`
from a Winlator checkout.

Adding a dependency is the same idea in `compat/dependencies.json`; set
`imports` so the import table can select it, `dlls` for the files a payload
needs, and `support` for how it is satisfied. Adding a fix that several
profiles need belongs in `compat/recipes.json` instead. The generator validates
the database: every referenced dependency, recipe, import name, override order
and registry type must be known, and game keys must be unique.

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
  generator imports these into per-game profiles, including the store-specific
  directories (GOG, EGS, Ubisoft, Amazon, Humble, itch.io, Zoom Platform) and
  the generic `gamefixes-umu` fallback.
- **Proton and GE-Proton** — the kinds of fixes that make specific titles work,
  used to shape the catalogue and the profiles.
- **Lutris** — how installers, prefixes and per-game configuration are
  described, which informed the profile model.
- **Wine** — the authority on the overrides, `AppDefaults` keys, registry
  format, DLL behaviour, and which DLL names a builtin module serves under
  (the source of the version families such as `d3dcompiler_*` and
  `xaudio2_*`). Its sources are also what says which modules exist at all:
  `gen-wine-modules.py` reads the module lists from the `configure` output and
  from both PE farms, so a name Wine never built is told apart from one this
  build simply does not carry.
- **Winlator** — the closest project to Madeira: Wine on Android, so its fixes
  are aimed at a Wine that is *not* Proton's, with no Linux, no Vulkan
  requirement and no root. Its per-executable loader configuration
  (`assets/box64/default.box64rc`) supplied the per-title overrides that are
  not in Protonfixes (a native `winmm`, DirectDraw and Direct3D 8 set to
  builtin, launched with the Windows XP version), its `wincomponents.json`
  supplied the DLL lists behind the DirectX, DirectMusic, DirectShow,
  DirectPlay, XACT, XAudio and Visual C++ components, and its container
  settings supplied the services and Direct3D registry values. Each of those
  is translated: the box64 memory-ordering switch becomes FEX's
  `FEX_TSOENABLED`, the Winlator-only `WINE_D3D_CONFIG` and `WINVERSION`
  variables become Wine's own `Direct3D` registry key and `windows_version`,
  and the Vulkan, Mesa and Zink settings are recorded as unavailable fixes on
  the title rather than imported.
- **DXMT** — Madeira's own Direct3D 11 implementation, and the source of the
  per-title rendering switches that are already applied inside it
  (`dxgi.customVendorId`, `d3d11.defuseFma`, `dxgi.forceSDR` and the rest).
  They are recipes here so that a profile can apply the same switch to a title
  DXMT does not know, and so the reasoning is visible in the database.
- **Apple Game Porting Toolkit** — the reference for a Wine-adjacent stack on
  Apple silicon; Madeira's stack is its own (FEX, DXMT, Metal), so this informed
  direction rather than code.

What was deliberately **not** carried over: Proton/DXVK/VKD3D (Madeira uses
DXMT and Metal, not Vulkan), winetricks' bash and its `.verb` files (the data
is re-expressed as JSON the app can read), Bottles' installers and runtimes
(Madeira's prefix and runtimes are its own), Protonfixes' Python (the fixes are
read out of the scripts into data; a fix that is a Python function Madeira has
no equivalent for is recorded as an unavailable fix on the title rather than
dropped), and anything needing root, a kernel module or a Linux service
(anti-cheat, some DRM), which is represented as an explicitly unsupported
compatibility case instead.

The same rule applies to Winlator, which is a source rather than a base for the
same reason Proton is: its Wine is patched and its graphics stack is its own.
The variables that only exist there are not imported as if they existed here —
`WINEVMEMMAXSIZE` (its Wine patch for a large address space) and
`WINE_DO_NOT_OPEN_SC_MANAGER` (its patch to stop a title opening the service
control manager, a race Madeira fixes in `patches/wine-rpcss-scm-bootstrap.patch`
instead) are recorded on the title with the Madeira reason. Its loader switches
without a FEX counterpart (`BOX64_DYNAREC_WEAKBARRIER`, `BOX64_DYNAREC_DIRTY`,
`BOX64_SKIPCPU`, `BOX64_DYNAREC_BIGBLOCK`, `BOX64_EXIT`) are recorded the same
way, as are the Mesa, Zink and Vulkan settings its containers use. What is left
is what transfers: overrides by DLL name, the Windows version, launch
arguments, the loader's memory-ordering mode, registry values Wine itself
defines, and the DLL lists of its Windows components.

One import is adapted rather than copied, because it would have been a bug: a
Protonfixes fix is a fix *on Proton*, and Proton renders through Vulkan. An
appended `-vulkan` or an id Tech `+r_renderAPI 1` selects a device Madeira
cannot create, so the generator drops those tokens and records them on the
title, leaving the rest of the command alone. Red Dead Redemption 2 is the
worked example: upstream appends `-fullscreen -vulkan`, and Madeira keeps
`-fullscreen`, replaces the renderer with the game's Direct3D 12 path and says
so in the profile's notes.

A second import is adapted for the same reason in the other direction:
Winlator's fix for some titles is to *disable* Wine's built-in streaming
decoder, which is right there because its prefixes carry native codecs. On
Madeira `winegstreamer` is the decoder itself, so importing the override would
have removed a title's video instead of repairing it. The recipe exists
(`winegstreamer-off`) but the title carries it as a fallback attempt instead of
as its default, with the reason in its notes.

## Testing

`build/host-tests/check-game-compat.py` compiles the engine and the diagnosis
with a harness and checks matching, detection, dependency resolution, recipes,
fallbacks and the alternatives a title offers, the plan, the registry merge,
session classification, the overlay merge and the bundled database (including
that every referenced dependency and recipe exists, that import names are
normalised, that the curated profiles were regenerated, that a component only
claims modules this runtime has in the architecture it is used from, that the
names Wine never built are listed with their reason, that a payload the title
ships beside itself counts as supplied, and that a builtin pin the launching
architecture cannot resolve is dropped and explained), then checks the source
wiring and the Xcode project. `check-frontend.py` compiles `LibraryEntry` with
the same engine, so the launch path and the compatibility plan cannot drift
apart.

The database itself is validated by the generator as well (`validate()` on every
run, and `--check` to compare the committed database without writing), so a
hand-edited profile that names a dependency or a recipe that does not exist
fails the build rather than the game launch.
