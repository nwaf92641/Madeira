# Wine Mono (.NET) as an optional component

Wine starts a managed (.NET) program through `mscoree`, which needs **Wine Mono**:
the Mono runtime, the .NET class libraries and `bin/libmono-2.0-x86{,_64}.dll`.
A desktop Wine installs it into each prefix from `share/wine/mono`. Madeira does
not ship it and does not run that install, so until now every .NET program
(.NET Framework, XNA and FNA games, many tools and launchers) stopped at:

    err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not installed

## What the app does

`app/Madeira/WineMono.c` runs at every launch (from `WineProcessBridge.m`, after
the side-by-side store). When `Documents/Components/wine-mono-11.0.0` holds an
unpacked Wine Mono, it links `C:\windows\mono\mono-2.0` in the prefix to it.
That is the first place mscoree looks (`get_mono_path_local` in
`wine/dlls/mscoree/metahost.c`). Nothing is copied. Removing the folder in the
Files app is enough: the next launch removes the dangling link. A real install
inside the prefix (a directory, not a link) is left alone.

The launch log says what happened, in one line:

| Line | Meaning |
| --- | --- |
| `[WineProc] wine-mono: 11.0.0 linked from …` | the component is in use |
| `[WineProc] wine-mono: not installed (…)` | no component; .NET programs cannot start |
| `[WineProc] wine-mono: found wine-mono-X, but this Wine needs wine-mono-11.0.0` | wrong version |
| `[WineProc] wine-mono: … is incomplete (…)` | the folder lacks `bin/libmono-2.0-x86.dll`, `bin/libmono-2.0-x86_64.dll` or `lib/mono` |
| `[WineProc] wine-mono: the prefix has its own install …` | left alone |

When a program still stops at "Wine Mono is not installed", the launch
diagnostics report a `dependency-load-failure` whose hint says where the
component goes.

## Installing it

1. Download `wine-mono-11.0.0-x86.tar.xz` (41 MB) from
   <https://dl.winehq.org/wine/wine-mono/11.0.0/>. It contains both the x86 and the
   x86_64 runtime. SHA-256, as downloaded on 2026-10-09:
   `0cd723aa28897f7d7d2702eed6c72e4262255980e212bc2b3c94bfa234abe5fd`.
2. Unpack it. The result is a folder `wine-mono-11.0.0` of about 235 MB, with
   `bin/`, `lib/`, `etc/` and `support/`.
3. Copy that folder into Madeira's `Documents/Components/` with the Files app.

The version has to be **11.0.0**: that is `WINE_MONO_VERSION` in the Wine this
app is built from (`wine/dlls/mscoree/mscoree_private.h`).
`build/host-tests/check-wine-mono.py` fails if the two disagree. Winlator ships
the 10.1.0 MSIs because its Wine is older.

## Why it is not in the app

- **Size:** 235 MB unpacked, more than the whole 64-bit DLL folder.
- **Not proven on the iPad:** Mono's JIT emits x86_64 code that FEX then
  translates. Earlier device work found a FEX miscompile in a Unity game's
  Mono (ml623, `MADEIRA_WINEMONO_BRIDGE`), so this path is not proven on the
  iPad. It is an opt-in component until a device run shows it works.
- **What is skipped:** the `support/` installer (fake .NET Framework
  directories and registry keys) is not run. A program that checks the
  registry for an installed .NET Framework, instead of just starting, may still
  refuse to run.

## Tested

`build/host-tests/check-wine-mono.py`:

- **Part 1** (any host with a C compiler): the version check, and every link
  state on temporary trees.
- **Part 2** (`HOST_WINE`, `WINE_PREFIX_TEMPLATE`, `WINE_MONO_DIR`), under
  Wine 11.4 x86_64 on Linux:
  - before the link, `csc.exe` (itself a .NET program) stops at "Wine Mono is
    not installed";
  - after the link, it compiles a C# program, which then runs as a 64-bit
    process.

Not tested: ARM64EC mscoree with FEX on an iPad, and XNA / FNA games.

## Licence

Madeira does not ship or modify Wine Mono. The user puts the unmodified
upstream release into `Documents/Components`; Madeira links that folder into
the prefix. Wine Mono's own `COPYING` lists its licences: Mono under LGPL or
MIT X11 (ICSharpCode.SharpZipLib GPL with an exception), mono-basic MIT X11,
FNA MS-PL and MIT with zlib-licensed FAudio, FNA3D, MojoShader and SDL3, and
winforms, wpf, monoDX, System.Speech and the remaining code MIT. See also
`docs/LICENSING.md`.

Upstream also carries `toolchain-arm64ec.cmake`; an ARM64EC build of Mono's
runtime would avoid FEX for the runtime itself. Not tried here.
