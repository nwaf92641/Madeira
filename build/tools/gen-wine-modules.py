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
