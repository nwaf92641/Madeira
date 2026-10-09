# Side-by-side assemblies (C:\windows\winsxs)

Status: landed for 32-bit and 64-bit programs. Tested under a host Wine on
Linux; not yet run on an iPad.

## What was wrong

- `scripts/build-prefix-snapshot.sh` deletes `drive_c/windows/winsxs` from
  `prefix-template.tar.gz`, and Madeira never runs wineboot's fake-DLL install
  (`dlls/setupapi/fakedll.c`), which is what builds the store on a normal
  Wine prefix. So the store is not created on first boot.
- Upstream Madeira already seeded the x86 store for 32-bit targets
  (`madeira_seed_winsxs_x86`, an Objective-C function in
  `WineProcessBridge.m`). 64-bit programs had nothing, and the ARM64EC farm had
  no `comctl32_v6.dll` to put there anyway.

## What that does under Wine (measured, not assumed)

`build/host-tests/check-winsxs.py` runs two probe programs (32-bit and 64-bit)
under a host WoW64 Wine 11.4. Their manifests ask for
Microsoft.Windows.Common-Controls 6.0 (`processorArchitecture="*"`) and
Microsoft.VC80.CRT, and they import and call `TaskDialogIndirect`.

| Prefix | comctl32 | TaskDialogIndirect | msvcr80 |
|---|---|---|---|
| winsxs removed (Madeira's 64-bit case before) | 5.x from system32 | bound to a stub: `unimplemented function COMCTL32.dll.TaskDialogIndirect`, the process aborts | loads from system32 |
| seeded by `madeira_winsxs_seed` | 6.0 from `winsxs\<arch>_microsoft.windows.common-controls_...` | returns | loads from `winsxs\<arch>_microsoft.vc80.crt_...` |

So:
- **No c0150002 under Wine.** Wine's loader does not refuse a process whose
  activation context cannot be built. It logs `Could not find dependent
  assembly` and goes on.
- A VC80/VC90 CRT dependency is not fatal as long as the DLL is in the farm
  (both farms have msvcr80/msvcr90 and msvcp80/msvcp90).
- The real failure is Common Controls 6. The program gets 5.x. Exports that
  only 6.0 has (`TaskDialog`, `TaskDialogIndirect`) become stubs, which abort
  when called. 6.0-only behaviour (visual styles, `SysLink` notifications,
  version checks via `DllGetVersion`) is also gone. Crash reporters, launchers
  and setup tools use TaskDialog.

## What landed

- `app/Madeira/WinSxS.c` / `WinSxS.h` (plain C). One table entry per
  `WINE_MANIFEST` resource in the Wine tree. It writes the manifest and links
  the DLLs the way `fakedll.c` lays them out.
  - The x86 table is the same as upstream's Objective-C one. That function is
    now a thin wrapper.
- `WineProcessBridge.m`:
  - x86 store for a 32-bit target, from `i386-windows` (as before).
  - **New:** an arm64 store for every session, from the session's own 64-bit
    farm.
- Why "arm64" for 64-bit programs:
  - An ARM64EC ntdll has `current_archW = "arm64"`, so `processorArchitecture="*"`
    resolves to `arm64_` directories.
  - Its `lookup_manifest_file` rewrites an `amd64_` lookup to `a??64_`, so an
    explicit `amd64` request finds them too.
- Why it follows the session's farm:
  - A plain ARM64 process (`aarch64-windows` session) cannot load an ARM64EC
    DLL. An assembly whose DLL is not in the current farm is removed, not left
    pointing at the other farm. This is the same per-session model system32
    already uses.
- `build/wine-pe/arm64ec-farm.json`: new group `side_by_side` with
  `comctl32_v6.dll`. Built and installed into `app/Madeira/arm64ec-windows`
  (2 MiB). `compat/wine-modules.json` and `app/Madeira/compat.json` list it as
  provided for 64-bit.
- `LaunchDiagnostics.c`:
  - A `COMCTL32.dll.TaskDialog*` stub call gets a hint that names Common
    Controls 6 and winsxs.
  - The `Could not find dependent assembly` hint now says what actually
    happens.

## Tests

- `build/host-tests/check-winsxs.py`, always runs:
  - The table matches all 10 Wine manifests: identity, `<file>` list and the
    comctl32 window classes.
  - Directory names are the ones `lookup_winsxs` searches for.
  - UTF-8, no BOM.
  - Idempotent.
  - Entries the new farm cannot back are removed; links that stay are
    repointed.
  - A link into an old install is replaced.
  - Bad architectures are refused.
  - Compiled with ASan/UBSan.
- With `HOST_WINE` and `LLVM_MINGW`: the before/after table above, for both
  32-bit and 64-bit.
- `check-launch-diagnostics.py`: the new hint.
- `check-arm64ec-farm.py`, `check-game-compat.py`: the farm and the
  compatibility data with `comctl32_v6`.

## Needs an iPad / Xcode

- `WinSxS.c` is added to the Xcode project by hand (`project.pbxproj`).
  `WineProcessBridge.m` was not compiled here.
- On the device, check:
  - the launch log has `[WineProc] winsxs: arm64: 9/10 assemblies seeded from
    .../arm64ec-windows, 1 not in that farm` (msxml4 is not in the 64-bit
    farm);
  - a 64-bit program with a Common Controls 6 manifest gets
    `...\winsxs\arm64_microsoft.windows.common-controls_...\comctl32.dll`;
  - the ARM64EC loader maps `comctl32_v6.dll` through the symlink like any
    other farm module.
- The host test runs an x86_64 Wine, where the 64-bit store is `amd64`. The
  `arm64` naming rests on `actctx.c` (quoted above), not on a run.
