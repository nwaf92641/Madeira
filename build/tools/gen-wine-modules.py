#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""List the modules Wine provides, so an import Wine resolves is not reported
as missing.

The compatibility engine needs to tell three kinds of import apart: one a
catalogue component answers for, one Wine provides as a builtin, and one
nothing provides (a DLL the game must ship itself). The first is data
(`compat/dependencies.json`); this tool produces the second.

Input is a Wine build's `configure` (or a Wine checkout, whose `configure` is
regenerated from the module list) — the generated file lists every module
directory, so one file gives the whole set without a full source checkout:

    build/tools/gen-wine-modules.py --configure /path/to/wine/configure
    build/tools/gen-wine-modules.py --wine /path/to/wine        # a checkout

Writes compat/wine-modules.json. Re-run it when the Wine the runtime is built
from moves; the recorded version makes the age of the list visible.

Upstream Wine builds modules the iOS runtime does not ship, and the build
script already says which and why (`build/wine-i386/build.sh`, SKIP_REASON).
Those names are subtracted and kept in the document as `not_shipped`, so an
import of one (`vulkan-1.dll`, `ir50_32.dll`, `opencl.dll`) is reported rather
than quietly treated as provided.

API set names are the third case. A modern Windows program imports a dozen
`api-ms-win-*.dll` names that are not files anywhere: the loader resolves them
to the module that implements the contract (ucrtbase for the C runtime sets,
kernelbase for the core ones). They are recorded as prefixes, because there are
hundreds of them and a program may invent its own from the same schema.

What this tool does not write is the 64-bit module set: that is whatever the
app's farm directories hold, and `build/tools/gen-game-compat.py` reads them
directly (`farm_modules()`), so a DLL added to a farm reaches the engine
without a Wine checkout. `build/host-tests/check-pe-imports.py` checks the two
against each other.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'compat' / 'wine-modules.json'
BUILD_SCRIPT = ROOT / 'build' / 'wine-i386' / 'build.sh'

# 16-bit and VxD modules: real Wine modules, but no Windows program Madeira can
# load imports them, and they would only pad the list.
SKIP_SUFFIX = ('.dll16', '.drv16', '.exe16', '.vxd')
# Wine names DXMT answers for. The i386 build skips Wine's own copies of these
# because DXMT installs its implementation under the same name (the script says
# so), so the name is provided and must not be listed as missing.
DXMT_OWNED = {'d3d9', 'd3d10core', 'd3d11', 'dxgi', 'winemetal'}
# API set contract names, resolved by the loader to the module that implements
# them. Kept as prefixes: they are not files in any Wine tree, and there are
# hundreds of them.
API_SET_PREFIXES = ['api-ms-win-', 'ext-ms-win-']
MODULE_DIR = re.compile(r'dlls/([A-Za-z0-9_.+-]+)(?:/[A-Za-z0-9_.+-]+)*')

# Windows components this runtime does not build at all, with the reason. They
# differ from `not_shipped` above: those are Wine modules the iOS build drops,
# these are names Wine has never had (a Windows split or a redistributable
# library). A program that imports one by name cannot be served — the engine
# says so instead of assuming the title ships it — and the reason is what the
# catalogue and the docs quote.
NOT_BUILT = {
    'mfcore':
        'Windows 8 split the Media Foundation platform in two; Wine implements '
        'the platform API in mfplat.dll and has no mfcore module',
    'd3dcompiler_44':
        'a DirectX SDK shader compiler; Wine builds d3dcompiler_33 through 43, 46 and 47',
    'd3dcompiler_45':
        'a DirectX SDK shader compiler; Wine builds d3dcompiler_33 through 43, 46 and 47',
    'd3d11_1':
        'Windows 8 renamed the Direct3D 11.1 interfaces into a second DLL; Wine '
        'exports them from d3d11.dll, which is the file a title should link',
    'dxgi1_2': 'Windows 8 split the DXGI 1.2 interfaces into this DLL; Wine exports them from dxgi.dll',
    'dxgi1_3': 'Windows 8.1 split the DXGI 1.3 interfaces into this DLL; Wine exports them from dxgi.dll',
    'dxgi1_4': 'Windows 10 split the DXGI 1.4 interfaces into this DLL; Wine exports them from dxgi.dll',
    'dxil':
        'the DirectX Shader Compiler\'s validator, a redistributable that ships '
        'with the title rather than with Windows; neither Wine nor DXMT builds it',
    'dxcompiler':
        'the DirectX Shader Compiler, a redistributable that ships with the '
        'title rather than with Windows; neither Wine nor DXMT builds it',
    'mscoreei':
        '.NET\'s installation shim; Wine has one managed entry point, mscoree.dll',
    'xactengine2_1': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_2': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_3': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_5': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_6': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_8': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'xactengine2_10': 'Wine builds xactengine2_0, 2_4, 2_7, 2_9 and the 3.x series only',
    'nvapi':
        'NVIDIA\'s driver interface; there is no NVIDIA driver behind Metal, and '
        'DXMT\'s nvapi module is not in the farms this app ships',
    'nvapi64':
        'NVIDIA\'s driver interface; there is no NVIDIA driver behind Metal, and '
        'DXMT\'s nvapi64 module is not in the farms this app ships',
    'nvngx': 'the DLSS entry point behind NVAPI; DXMT builds one and this app does not ship it',
    'nvngx_dlss': 'the DLSS runtime a title ships itself; it needs an NVIDIA driver, which Metal is not',
    'nvngx_dlssg': 'the DLSS frame-generation runtime; it needs an NVIDIA driver, which Metal is not',
    'amdxc64': 'AMD\'s DX11 shader compiler driver component; there is no AMD driver behind Metal',
    'amdxcffx64': 'AMD\'s FidelityFX driver component; there is no AMD driver behind Metal',
    'amfrt64': 'AMD\'s Media Framework runtime; there is no AMD driver behind Metal',
    'atiadlxx': 'AMD\'s display library; there is no AMD driver behind Metal',
    'atiumd64': 'AMD\'s user-mode display driver; there is no AMD driver behind Metal',
    'atiumd6a': 'AMD\'s video component; there is no AMD driver behind Metal',
}


def not_shipped_from_build(path: Path) -> list[str]:
    """The modules build/wine-i386/build.sh refuses to install, by name.

    The script states them as `"name|name=reason"` entries in one array, which
    is the same list a human reads when asking why a DLL is missing; parsing it
    keeps this document and the build from disagreeing.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding='utf-8')
    block = re.search(r'SKIP_REASON=\((.*?)\n\)', text, re.S)
    if not block:
        return []
    names: list[str] = []
    for entry in re.findall(r'"([^"]+)"', block.group(1)):
        head = entry.split('=', 1)[0]
        for name in head.split('|'):
            name = name.strip()
            if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name):
                continue
            # The build script names installed files ("vulkan-1.dll"); the
            # module list uses the module name without the DLL extension.
            if name.lower().endswith('.dll'):
                name = name[:-4]
            if name.lower() in DXMT_OWNED:
                continue
            names.append(name)
    return sorted(set(names))


def modules_from_configure(path: Path) -> tuple[list[str], str]:
    text = path.read_text(encoding='utf-8', errors='replace')
    version = ''
    match = re.search(r'for Wine (\d+\.\d+)', text)
    if match:
        version = match.group(1)
    found: set[str] = set()
    for name in MODULE_DIR.findall(text):
        if name.endswith(SKIP_SUFFIX):
            continue
        found.add(name)
    return sorted(found), version


def configure_from_checkout(root: Path) -> Path:
    configure = root / 'configure'
    if not configure.exists():
        raise SystemExit(f'{configure} is missing; run ./configure in the Wine tree first')
    return configure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--configure', type=Path, help='a Wine build configure')
    source.add_argument('--wine', type=Path, help='a Wine checkout (uses its configure)')
    parser.add_argument('--skip-from', type=Path, default=BUILD_SCRIPT,
                        help='build script whose SKIP_REASON list is not shipped')
    parser.add_argument('--check', action='store_true', help='compare without writing')
    arguments = parser.parse_args()

    configure = arguments.configure or configure_from_checkout(arguments.wine)
    upstream, version = modules_from_configure(configure)
    not_shipped = not_shipped_from_build(arguments.skip_from)
    dropped = set(not_shipped)
    modules = [name for name in upstream if name not in dropped]
    for name in NOT_BUILT:
        if name in modules:
            raise SystemExit(f'not_built lists {name!r}, which this Wine build provides')
    if len(modules) < 400:
        raise SystemExit(f'{configure} lists only {len(modules)} modules; is it a Wine configure?')
    document = {
        'wine': version or 'unknown',
        'note': 'Modules the Wine runtime provides as builtins (dlls/*), minus the '
                'ones the iOS build does not ship. An import matching one of these '
                'needs no compatibility entry; one in "not_shipped" does not exist '
                'here and is reported.',
        'modules': modules,
        'not_shipped': not_shipped,
        'not_built_note':
            'Windows components this runtime does not build at all, with the '
            'reason. Unlike not_shipped, which is the iOS build dropping a module '
            'Wine has, these are names Wine has never had; a program importing one '
            'cannot be served, and the engine reports it as unavailable rather '
            'than as a file the title will ship.',
        'not_built': [{'name': name, 'reason': NOT_BUILT[name]} for name in sorted(NOT_BUILT)],
        'api_set_prefixes': list(API_SET_PREFIXES),
    }
    text = json.dumps(document, indent=2, sort_keys=False) + '\n'

    if arguments.check:
        current = OUT.read_text(encoding='utf-8') if OUT.exists() else ''
        if current != text:
            raise SystemExit(f'{OUT} is not current; re-run without --check')
        print(f'PASS: {OUT.name} is current ({len(modules)} modules, Wine {document["wine"]})')
        return 0

    OUT.write_text(text, encoding='utf-8')
    print(f'wrote {OUT.relative_to(ROOT)}: {len(modules)} modules, Wine {document["wine"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
