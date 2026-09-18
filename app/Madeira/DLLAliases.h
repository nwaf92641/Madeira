/* DLL name aliases, in the spirit of Winlator's `direct3d` component.
 *
 * Wine builds one module per DirectX SDK generation and never ships the whole
 * ladder for the guest: the bundle here carries d3dx9_43, d3dcompiler_43 and
 * d3dcompiler_47, and nothing else. A title that imports `d3dx9_35.dll` or
 * `d3dcompiler_40.dll` by name therefore fails to load, which is a failed game
 * start rather than a degraded one -- the single most common "this game does
 * not work" on a Wine-based stack.
 *
 * The pairs below point the names a title asks for at a module that is already
 * in system32. This is sound for these families specifically because their
 * export names carry no version: `D3DXMatrixMultiply` and `D3DCompile` are the
 * same symbols in every build, so a caller linked against the SDK of 2005
 * resolves against the newest one. Wine itself models d3dx9_24..42 as
 * forwarders to its single implementation for the same reason, and
 * d3dcompiler_44/45 are not modules Wine builds at all -- they are SDK labels
 * that only ever existed in Microsoft's redistributable.
 *
 * Two rules the gate enforces (tools/check-dll-aliases.py):
 *   1. an alias target must be a module this bundle actually ships, in both
 *      architectures, because the alias is a symlink to a file in system32;
 *   2. an alias name must not be a module this bundle ships -- shadowing a
 *      real implementation with a link to a different one would be a silent
 *      regression, not an alias.
 *
 * Keep this file to the table and this comment: the gate parses it, and the
 * runtime loop in WineProcessBridge.m reads nothing else.
 */
#ifndef MADEIRA_DLL_ALIASES_H
#define MADEIRA_DLL_ALIASES_H

typedef struct {
    const char *name;    /* the name a title imports */
    const char *target;  /* a module present in the session's system32 */
} MadeiraDLLAlias;

static const MadeiraDLLAlias kMadeiraDLLAliases[] = {
    /* d3dx9: one SDK build per release, and titles import the one they were
     * linked against. Target d3dx9_43, which this bundle ships and which is
     * itself the newest of the family. */
    { "d3dx9_24.dll", "d3dx9_43.dll" },
    { "d3dx9_25.dll", "d3dx9_43.dll" },
    { "d3dx9_26.dll", "d3dx9_43.dll" },
    { "d3dx9_27.dll", "d3dx9_43.dll" },
    { "d3dx9_28.dll", "d3dx9_43.dll" },
    { "d3dx9_29.dll", "d3dx9_43.dll" },
    { "d3dx9_30.dll", "d3dx9_43.dll" },
    { "d3dx9_31.dll", "d3dx9_43.dll" },
    { "d3dx9_32.dll", "d3dx9_43.dll" },
    { "d3dx9_33.dll", "d3dx9_43.dll" },
    { "d3dx9_34.dll", "d3dx9_43.dll" },
    { "d3dx9_35.dll", "d3dx9_43.dll" },
    { "d3dx9_36.dll", "d3dx9_43.dll" },
    { "d3dx9_37.dll", "d3dx9_43.dll" },
    { "d3dx9_38.dll", "d3dx9_43.dll" },
    { "d3dx9_39.dll", "d3dx9_43.dll" },
    { "d3dx9_40.dll", "d3dx9_43.dll" },
    { "d3dx9_41.dll", "d3dx9_43.dll" },
    { "d3dx9_42.dll", "d3dx9_43.dll" },

    /* d3dcompiler: 33..43 are the pre-D3D11 generation and 46/47 the D3D11 one.
     * The bundle ships one of each, so the split follows the generations rather
     * than pointing all of them at the newest: d3dcompiler_47 is a different
     * implementation (it adds D3DCompile2 and drops the old reflection entry
     * points), while 33..43 differ only in SDK label. 44 and 45 are not in the
     * table because no build of them has ever existed to alias. */
    { "d3dcompiler_33.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_34.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_35.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_36.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_37.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_38.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_39.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_40.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_41.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_42.dll", "d3dcompiler_43.dll" },
    { "d3dcompiler_44.dll", "d3dcompiler_47.dll" },
    { "d3dcompiler_45.dll", "d3dcompiler_47.dll" },
    { "d3dcompiler_46.dll", "d3dcompiler_47.dll" },
};

#define MADEIRA_DLL_ALIAS_COUNT \
    (sizeof(kMadeiraDLLAliases) / sizeof(kMadeiraDLLAliases[0]))

#endif /* MADEIRA_DLL_ALIASES_H */
