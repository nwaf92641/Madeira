# DirectDraw

Status: partial. Two opt-in paths landed: `MADEIRA_GAME_GDI_FULLSCREEN=1`
(Wine's ddraw, drawn through GDI) and cnc-ddraw over DXMT's Direct3D 9
(`MADEIRA_DDRAW=cnc`, recipe `cnc-ddraw`). cnc-ddraw is built and tested
under a host WoW64 Wine with a recording Direct3D 9; nothing here has run on
an iPad, and DXMT's acceptance of cnc-ddraw's device is the open question.

## What happens today

1. Wine's `ddraw.dll` first asks for a 3D-capable `wined3d`. There is no
   OpenGL or Vulkan backend on iOS, so that fails. It then retries with
   `WINED3D_NO3D` (`wine/dlls/ddraw/ddraw.c` around line 5136,
   `wine/dlls/ddraw/main.c` line 469). This 2D path blits the primary surface
   to the window through GDI.
2. NO3D has two limits:
   - Creating a Direct3D device (Direct3D 3/5/6/7 through ddraw) fails.
   - A surface that asks for `DDSCAPS_VIDEOMEMORY` fails with
     `DDERR_NODIRECTDRAWHW` (`surface.c` around line 6959).

   Pure 2D games that let DirectDraw pick the memory type get through. Games
   that insist on video memory do not.
3. Win32u gives a game session's windows real surfaces (`driver_ios.c`,
   `MADEIRA_GAME_WINDOWS`). Before this change, though, Winios hid every
   window that covers the whole guest desktop (`winios_game_window_shown` in
   `app/Madeira/Winios/Winios.m`). The aim was that a Direct3D game's own
   window never covers its Metal picture. A full-screen DirectDraw game's
   window is exactly such a window, so its picture was drawn nowhere. The
   launch log says `[winios] game window ... not drawn (covers the guest
   desktop)`. The desktop session draws it.

## What landed

`MADEIRA_GAME_GDI_FULLSCREEN=1` (environment / `madeira.cfg` `env.` key, in
the config catalog) draws a full-desktop game window as long as it does not
present through Metal.

- A window whose swapchain later registers
  (`winios_note_game_metal_hwnd`) is still removed as before.
- It is off by default because a Direct3D game's window can paint GDI bits
  before its swapchain registers, which would cover the picture briefly.
- The launch-diagnostics hint for "full-desktop window not drawn" now names
  this switch.
- Not compiled or run here: Winios.m needs Xcode and an iPad.

## cnc-ddraw over DXMT's Direct3D 9 (landed, opt-in)

Winlator ships cnc-ddraw 6.6 as its DirectDraw wrapper. Madeira uses it with
its Direct3D 9 renderer only.

Why it fits:

- License: MIT (https://github.com/FunkyFr3sh/cnc-ddraw), imported at
  279a057 into `third_party/cnc-ddraw` without Microsoft's SDK headers
  (`MADEIRA_IMPORT.md`; Madeira's changes in `MADEIRA_CHANGES.md`).
- Renderers: GDI, OpenGL, Direct3D 9. OpenGL has no backend here; GDI hits
  the full-desktop rule above; `renderer=direct3d9` loads `d3d9.dll`, which
  is DXMT's, and presents through Metal, so Winios treats its window as a
  game picture.
- Its `auto` renderer never picks Direct3D 9 under Wine (`if (!IsWine() &&
  d3d9_is_available())` in `src/dd.c`; under the host Wine it picked OpenGL).
  Madeira's `ddraw.ini` therefore says `renderer=direct3d9`.
- It keeps every surface in system memory, so the `DDSCAPS_VIDEOMEMORY`
  failure above does not apply. An 8-bit mode goes up as a 1024x1024 `L8`
  texture plus a 256x256 palette texture, converted by a ps_2_0 shader.
- It does not implement Direct3D through ddraw (IDirect3D2..7). Those games
  are still Wine's ddraw's (and fail without wined3d); see "Not covered".

### How it is built and shipped

- `build/ddraw/build.sh` compiles the sources with llvm-mingw (i686), the
  upstream link line, and `madeira/` ahead of `inc/` on the include path.
  It writes `ddraw.dll` (about 450 KB, native, not builtin-marked) and
  `ddraw.ini` to `app/Madeira/cnc-ddraw/` (a bundled folder reference,
  git-ignored like `i386-windows/`). `build/wine-i386/build.sh` calls it after
  DXMT's d3d9 and d3d8to9.
- `build/ddraw/make-ini.py` makes `ddraw.ini` from cnc-ddraw's own defaults
  (the text of `cfg_create_ini`, so all ~290 per-game sections are kept) and
  changes, each marked `; Madeira: was ...`:
  - `[ddraw]` `renderer=direct3d9` (was `auto`), `fullscreen=true` and
    `maintas=true` (the game fills the screen with its aspect ratio kept),
    `singlecpu=false` (FEX threads), `no_compat_warning=true`;
  - the 8 per-game `renderer=opengl` entries become `direct3d9`; the 47
    `renderer=gdi` ones stay (upstream chose GDI for those games).
- It is not the farm's `ddraw.dll`: Wine's stays the default, so Direct3D 7
  games and every other program are unchanged.

### How a launch turns it on

`MADEIRA_DDRAW=cnc` (the `cnc-ddraw` recipe in `compat/recipes.json`, the
builtin `cnc-ddraw` dependency, or `env.MADEIRA_DDRAW = cnc` in
`madeira.cfg` / Settings, "DirectDraw (32-bit games)"). Right after the
i386 farm is linked into `C:\windows\syswow64`, `WineProcessBridge.m`
(`madeira_apply_cnc_ddraw`):

1. links `syswow64\ddraw.dll` to `cnc-ddraw/ddraw.dll`. The next launch
   without the switch relinks Wine's (the whole farm is relinked every launch);
2. copies `ddraw.ini` to `C:\ProgramData\cnc-ddraw\ddraw.ini` once (edits
   are kept; delete it to get Madeira's back) and sets
   `CNC_DDRAW_CONFIG_FILE` to it, as Winlator does;
3. sets `ddraw=n,b` in `WINEDLLOVERRIDES`, replacing any other ddraw entry.
   Without it Wine loads its builtin ddraw and ignores the native file
   (measured, see the test). A `ddraw.dll` the game ships beside itself still
   wins over both.
4. logs `[WineProc] cnc-ddraw: ...`, or, when the bundle has no cnc-ddraw,
   says so and leaves Wine's ddraw.

The recipe also sets `MADEIRA_GAME_GDI_FULLSCREEN=1`, so cnc-ddraw's GDI
fallback (used when Direct3D 9 cannot start) is still drawn.

There is no global rule: a DirectDraw import alone does not switch a game to
cnc-ddraw, because some DirectDraw games also use Direct3D 7 through ddraw.

### Launch log

cnc-ddraw prints (through `__wine_dbg_output`, so whatever `WINEDEBUG` says):

- `[cnc-ddraw] renderer direct3d9 (ddraw.ini renderer=direct3d9, C:\ProgramData\cnc-ddraw\ddraw.ini)`
- `[cnc-ddraw] Direct3D 9 device WxH windowed=N`
- on failure: `d3d9.dll could not be loaded`, `Direct3DCreate9 returned NULL`,
  `Direct3D 9 CreateDevice failed (hr 0x...)`, textures/shaders or states
  failed, then `Direct3D 9 renderer could not start, falling back to GDI`.

`LaunchDiagnostics.c` turns these into the graphics-API and device stages
("DirectDraw (cnc-ddraw over DXMT's Direct3D 9)") or a device / dependency
problem with a hint; a missing `ddraw.dll` and the "full-desktop window not
drawn" hints now mention `MADEIRA_DDRAW=cnc`.

### Tests (Linux)

`build/host-tests/check-cnc-ddraw.py`:

- Part 1 (llvm-mingw): the build works without Microsoft's headers and writes
  nothing into `third_party/`; the DLL is i386, native, exports DirectDraw,
  imports only modules the i386 farm has and no OpenGL/Vulkan/Direct3D; the
  ini checks above; the app wiring (bridge, recipe, dependency, Settings,
  Xcode folder, i386 build). With `CNC_DDRAW_UPSTREAM=<checkout>` every
  source file is compiled with Microsoft's `ddraw.h`/`d3dcaps.h` and with
  Madeira's shims and the disassembly compared (identical, 2026-10-09).
- Part 2 (`HOST_WINE`, `WINEBUILD`): host WoW64 Wine 11.4 with the null
  display driver, a builtin-marked recording `d3d9.dll` in the i386 DLL
  directory, cnc-ddraw linked into `syswow64` as the app does it. A 32-bit
  program sets 640x480x8, creates a flipping primary surface and a palette,
  draws 30 frames. Observed: cnc-ddraw is the loaded `ddraw.dll`; it logs
  `renderer direct3d9`; Direct3D 9 gets `CreateDevice` (behaviour 0x56:
  multithreaded, hardware vertex processing, pure device, FPU preserve),
  a vertex buffer, `L8` and palette textures, two ps_2_0 shaders, and
  `Present`s. Without `ddraw=n,b` Wine's ddraw loads; with `renderer=auto`
  cnc-ddraw picks OpenGL; with `Direct3DCreate9` or `CreateDevice` failing it
  logs the reason and falls back to GDI.

    LLVM_MINGW=... HOST_WINE=... WINEBUILD=... [HOST_WINEPREFIX=...] \
    CNC_DDRAW_UPSTREAM=/path/to/cnc-ddraw python3 build/host-tests/check-cnc-ddraw.py

Under the host Wine (no display modes), cnc-ddraw switches itself to a
borderless window (its Wayland workaround), so the device was windowed with
a 0x0 back buffer (window size).

### Needs an iPad (not done)

1. DXMT's d3d9 (the i386 shim -> `d3d9-emulated.dll`) accepting cnc-ddraw's
   device: `D3DCREATE_PUREDEVICE | D3DCREATE_MULTITHREADED`, managed `L8`
   and `X8R8G8B8` textures, `LockRect` every frame, the ps_2_0 palette shader,
   `D3DFVF_XYZRHW | D3DFVF_TEX1` quads. Expected log: `[cnc-ddraw] renderer
   direct3d9` then `[cnc-ddraw] Direct3D 9 device ...` and a first-present
   stage. If the device fails, the log names the step and the HRESULT.
2. Display-mode changes (`ChangeDisplaySettings` to 640x480 and back) under
   Winios; whether cnc-ddraw stays exclusive or takes its borderless path.
3. Mouse-coordinate scaling (cnc-ddraw hooks `GetCursorPos`, `ClipCursor`
   and friends by patching import tables; under FEX) and touch input mapping.
4. Frame pacing and CPU cost (texture upload of the whole surface each frame
   through FEX).
5. A real title (Command & Conquer / Red Alert / StarCraft-era 2D games).

### Not covered

- Direct3D 3-7 through ddraw (IDirect3D2..7): no non-Vulkan answer here or in
  Winlator (wined3d on OpenGL, or DXVK's ddraw).
- 64-bit DirectDraw programs: cnc-ddraw is 32-bit; there are almost none.

## Tests here

- `build/host-tests/check-cnc-ddraw.py` (above).
- `build/host-tests/check-launch-diagnostics.py`: cnc-ddraw log lines and the
  hints that name it.
- `build/host-tests/check-config-catalog.py` lists the new switch.
- Winios.m and WineProcessBridge.m were not compiled here. They are
  Objective-C/UIKit and need Xcode.
