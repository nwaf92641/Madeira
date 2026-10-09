# Madeira's changes to cnc-ddraw

Upstream: https://github.com/FunkyFr3sh/cnc-ddraw at 279a057 (imported as
described in `MADEIRA_IMPORT.md`). Madeira's changes are under the same MIT
license as cnc-ddraw. Every changed line in upstream files carries a
`/* Madeira */` comment.

## New files (`madeira/`)

| File | What |
|---|---|
| `madeira/ddraw.h` | Wrapper over the toolchain's `ddraw.h` (`#include_next`). Adds the `DDCAPS_DX1` structure, which upstream gets from Microsoft's SDK header and mingw-w64's header lacks. Its layout is the DirectX 3 `DDCAPS` one (up to `dwReserved3`). |
| `madeira/d3dcaps.h` | Includes `d3dtypes.h` before the toolchain's `d3dcaps.h`, which uses its types without including it. |
| `madeira/madeira_log.h`, `madeira/madeira_log.c` | `madeira_log()`: one `[cnc-ddraw] ...` line through ntdll's `__wine_dbg_output` (which reaches the Madeira log whatever `WINEDEBUG` says), or `OutputDebugStringA` outside Wine. `madeira_renderer_name()` names the selected renderer. |

The build puts `madeira/` ahead of `inc/` on the include path, so upstream
sources compile without Microsoft's headers. `build/host-tests/check-cnc-ddraw.py`
(with `CNC_DDRAW_UPSTREAM` set to an upstream checkout) compiles every source
file both ways and checks that the disassembly is identical.

## Changes in upstream files

| File | Change |
|---|---|
| `src/dd.c` | After the renderer is chosen: `[cnc-ddraw] renderer <name> (ddraw.ini renderer=<setting>, <ini path>)`. At both places where the Direct3D 9 renderer gives up: `[cnc-ddraw] Direct3D 9 renderer could not start, falling back to GDI`. |
| `src/render_d3d9.c` | `d3d9_create` says which step failed: `d3d9.dll could not be loaded (error N)`, `Direct3DCreate9 returned NULL`, `Direct3D 9 CreateDevice failed (hr 0x...)` (the last behaviour flags' result), device created but textures/shaders or states failed, or success with `Direct3D 9 device WxH windowed=N`. The single `return device && resources && states` became three checks with the same result. |

Behaviour is unchanged apart from the log lines.

## Not used

- Upstream's `Makefile` (it writes `inc/git.h` into the source tree):
  `build/ddraw/build.sh` compiles the same sources with the same link line
  and puts `git.h` in the object directory.
- The run-time ddraw.ini writer: Madeira ships a ddraw.ini made from its text
  by `build/ddraw/make-ini.py`.
