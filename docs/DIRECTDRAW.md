# DirectDraw

Status: partial. What landed is one opt-in switch
(`MADEIRA_GAME_GDI_FULLSCREEN=1`). A cnc-ddraw port is planned and not done.
Nothing here has run on an iPad.

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

## Plan: cnc-ddraw over DXMT's Direct3D 9 (not done)

Winlator ships cnc-ddraw 6.6 as its DirectDraw wrapper. The evidence says it
fits Madeira, but only with its Direct3D 9 renderer:

- License: MIT (https://github.com/FunkyFr3sh/cnc-ddraw). Its Detours copy
  is MIT and is not part of the C build.
- It builds unchanged with this repository's llvm-mingw:
  `make CC=i686-w64-mingw32-clang WINDRES=i686-w64-mingw32-windres` produced
  a 390 KB `ddraw.dll` in under 2 s (checked on 2026-10-09, commit 279a057).
- Renderers: GDI, OpenGL, Direct3D 9.
  - OpenGL has no backend here.
  - GDI would hit the same full-desktop rule as Wine's ddraw.
  - `renderer=direct3d9` loads `d3d9.dll`, which is DXMT's. It presents
    through Metal, so Winios already treats its window as a game picture.
  - Its palette and upscaling shaders are ps_2_0; DXMT's DXSO frontend
    accepts SM1 to SM3.
- Its `auto` renderer avoids Direct3D 9 under Wine (`if (!IsWine() &&
  d3d9_is_available())` in `src/dd.c`). Madeira must therefore ship a
  `ddraw.ini` with `renderer=direct3d9`.
- It keeps every surface in system memory, so the `DDSCAPS_VIDEOMEMORY`
  failure above does not apply.
- It does not implement Direct3D through ddraw. A game that uses
  IDirect3D2 through 7 (Direct3D 7 and earlier) needs something else: there is
  no non-Vulkan answer in Winlator either, since it uses wined3d on
  OpenGL or DXVK's ddraw.

Steps:

1. Vendor it under `third_party/cnc-ddraw` at a pinned commit, the way
   `third_party/d3d8to9` was done (verbatim import commit, then changes listed
   in `MADEIRA_CHANGES.md`). Build it in a new `build/ddraw/build.sh`.
2. Ship it opt-in per game, not as the farm's `ddraw.dll`. Replacing Wine's
   ddraw for all programs would break the Direct3D 7 titles Wine's ddraw can
   at least enumerate.
   - Install it into a separate directory (e.g. `i386-windows/cnc-ddraw/`).
   - Let the game's compat plan copy `ddraw.dll` and `ddraw.ini`
     (`renderer=direct3d9`, `fullscreen=true`, `windowed=false`) beside the
     executable. Native loads before builtin.
   - Needs a recipe action that copies files. The engine has `env`
     recipes only today.
3. Host test: the same harness as `build/host-tests/check-d3d8to9-wine.py`
   (recording d3d9, WoW64 Wine). It needs a window, so it needs Wine with a
   display driver (X11 or a null driver that creates windows). The current
   host Wine build is X-less.
4. iPad: display-mode changes (`ChangeDisplaySettings` to 640x480 and back)
   under Winios, mouse-coordinate scaling (cnc-ddraw hooks `GetCursorPos`
   and friends; code patching under FEX), and frame pacing.

## Tests here

- `build/host-tests/check-launch-diagnostics.py` still passes with the new
  hint.
- `build/host-tests/check-config-catalog.py` lists the new switch.
- Winios.m was not compiled. It is Objective-C/UIKit and needs Xcode.
