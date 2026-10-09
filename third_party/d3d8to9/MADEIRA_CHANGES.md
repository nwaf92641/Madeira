# Madeira's changes to d3d8to9

All changes are in this directory and are BSD-2-Clause like the rest of it.
New files carry their own header; changed upstream lines are marked
`// Madeira:` in place.

| File | Change | Why |
|---|---|---|
| `source/madeira_d3d8_shader.hpp` (new) | Token-level D3D8 -> D3D9 shader translation: an SM1 instruction walker with the operand count of every SM1 opcode, `translate_vs` (inserts one `dcl_<usage> v#` per declared input after the version token, vs.1.0 -> vs.1.1) and `translate_ps` (ps.1.0-1.4 passed on, ps.1.0 -> ps.1.1). | Upstream goes through D3DX disassembly, regex edits and D3DX reassembly. On Madeira D3DX is Wine's (vkd3d-shader disassembly, a different text format from the one the regexes expect), and the D3D9 below is DXMT's, whose DXSO front end reads SM1 bytecode directly. The regex edits exist to pass Microsoft's D3D9 validator, which DXMT does not have. |
| `source/d3d8to9_device.cpp` | `CreateVertexShader` / `CreatePixelShader` use the token translator; declaration constants (`D3DVSD_CONST`) are kept and loaded with `SetVertexShaderConstantF` at `SetVertexShader` (D3D8 semantics) instead of being baked in as `def`; `GetVertexShaderDeclaration` is implemented (upstream: `D3DERR_INVALIDCALL`); `GetVertexShaderFunction` / `GetPixelShaderFunction` return the application's D3D8 tokens. A refused shader is logged with its reason. | Correctness of the D3D8 API and a reason in the launch log for a shader the runtime refuses. |
| `source/madeira_log.hpp` (new) | `madeira_d3d8::log`: one `[d3d8to9] ...` line through ntdll's `__wine_dbg_output`. | Madeira's launch diagnostics read the session's stderr (`app/Madeira/LaunchDiagnostics.c`). |
| `source/d3d8to9.cpp` | No message box or download page when `d3dx9_43.dll` is missing (logged instead; it is only needed by `CopyRects` between formats); `Direct3DCreate8` logs whether a Direct3D 9 runtime was there; `D3D8GetSWInfo` exported (returns 0, as Wine's does). | A modal dialog on an iPad looks like a frozen game. The export keeps the DLL's export table equal to Windows' and Wine's. |
| `source/d3d8to9_base.cpp` | `CreateDevice` logs the request and its HRESULT. | Places a device failure in the launch record. |
| `res/d3d8.def` | `D3D8GetSWInfo` added. | As above. |
| `source/d3d8to9.hpp` | `PixelShaderFunctions` map. | `GetPixelShaderFunction`. |

Build: `build/d3d8/build.sh` (called from `build/wine-i386/build.sh`).
Tests: `build/host-tests/check-d3d8-shader.py` (translator against DXMT's own
DXSO walker), `build/host-tests/check-d3d8to9-wine.py` (the DLL under a host
WoW64 Wine with a recording Direct3D 9). See `docs/D3D8.md`.
