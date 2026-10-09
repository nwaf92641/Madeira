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

The two architectures do not ship the same set. A 32-bit program runs under
WoW64 against the i386 farm, which build/wine-i386/build.sh fills with every
module of the configured tree minus SKIP_REASON, so `modules` is that set. A
64-bit program runs against the ARM64EC farm, app/Madeira/arm64ec-windows,
which is built group by group (build/wine-pe/arm64ec-farm.json) and is a
committed directory: about half of Wine's modules are not in it. The 64-bit
list is therefore read from that directory, not assumed:

    modules_64          Wine module names (and the DXMT-owned names) that have
                        a file in the ARM64EC farm.
    not_in_64bit_farm   `modules` minus `modules_64`: real Wine modules a
                        64-bit program cannot load here today.

    api_sets            Wine's API set schema (dlls/apisetschema/apisetschema.spec):
                        contract name, without its last version number (the
                        loader ignores it, see apiset_key()), -> host module,
                        "" when Wine defines the contract with no host. An
                        API set import is only resolved when its contract is
                        here and the host is a module of the program's farm.

`--farm64` names another farm directory; the list is regenerated whenever a
group is added to the farm, and `--check` fails while it is out of date.
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
FARM64 = ROOT / 'app' / 'Madeira' / 'arm64ec-windows'
# File extensions a farm file carries; the module name is the file name
# without '.dll' (Wine names the other kinds with their extension: 'appwiz.cpl',
# 'wineios.drv', 'stdole2.tlb').
FARM_EXT = ('.dll', '.drv', '.cpl', '.acm', '.ax', '.ocx', '.tlb', '.exe', '.sys', '.ds', '.msstyles')

# 16-bit and VxD modules: real Wine modules, but no Windows program Madeira can
# load imports them, and they would only pad the list.
SKIP_SUFFIX = ('.dll16', '.drv16', '.exe16', '.vxd')
# Wine names DXMT answers for. The i386 build skips Wine's own copies of these
# because DXMT installs its implementation under the same name (the script says
# so), so the name is provided and must not be listed as missing.
DXMT_OWNED = {'d3d9', 'd3d10core', 'd3d11', 'dxgi', 'winemetal',
              # Madeira's d3d8 (third_party/d3d8to9 over DXMT's d3d9) replaces
              # Wine's wined3d frontend in the 32-bit farm; build/d3d8/build.sh.
              'd3d8'}
# API set contract names, resolved by the loader to the module that implements
# them. Kept as prefixes: they are not files in any Wine tree, and there are
# hundreds of them.
API_SET_PREFIXES = ['api-ms-win-', 'ext-ms-win-']
APISET_LINE = re.compile(r'^apiset[ \t]+(\S+)[ \t]*=[ \t]*(\S*)', re.M)


def apiset_key(name: str) -> str:
    """The part of an API set name the loader compares: lowercase, without
    '.dll' and without the last '-<n>' (Wine's ntdll and Windows both match
    'api-ms-win-core-file-l1-2-4' against the schema's 'api-ms-win-core-file-l1-2')."""
    lower = name.lower()
    if lower.endswith('.dll'):
        lower = lower[:-4]
    return lower.rsplit('-', 1)[0]


def api_sets_from_schema(path: Path) -> dict[str, str]:
    """{contract key: host module name ('' when the schema gives no host)}."""
    if not path.exists():
        return {}
    sets: dict[str, str] = {}
    for name, host in APISET_LINE.findall(path.read_text(encoding='utf-8')):
        host = host.lower()
        if host.endswith('.dll'):
            host = host[:-4]
        sets[apiset_key(name)] = host
    return dict(sorted(sets.items()))


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


def farm_names(path: Path) -> set[str]:
    """Module names of the files in a farm directory, as `modules` spells them.

    Only names are read (the farm check, build/host-tests/check-arm64ec-farm.py,
    verifies the files themselves). A missing directory is an error rather
    than an empty farm: an empty 64-bit list would report every import of
    every 64-bit game as missing.
    """
    if not path.is_dir():
        raise SystemExit(f'{path} is not a directory; pass --farm64 with the ARM64EC farm')
    names: set[str] = set()
    for entry in path.iterdir():
        lower = entry.name.lower()
        if not lower.endswith(FARM_EXT):
            continue
        names.add(lower[:-4] if lower.endswith('.dll') else lower)
    return names


def split_by_farm(modules: list[str], farm: set[str]) -> tuple[list[str], list[str]]:
    """(modules_64, not_in_64bit_farm): the listed modules the 64-bit farm has
    a file for, and the ones it has not. A farm file that is not a listed
    module (a test program, FEX's own DLL, Madeira's d3d12) is not added: the
    list answers 'does Wine provide this import', and only Wine's modules and
    the DXMT-owned names are that."""
    have = [name for name in modules if name.lower() in farm]
    missing = [name for name in modules if name.lower() not in farm]
    return have, missing


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
    parser.add_argument('--farm64', type=Path, default=FARM64,
                        help='the ARM64EC farm directory a 64-bit program loads from')
    parser.add_argument('--check', action='store_true', help='compare without writing')
    arguments = parser.parse_args()

    configure = arguments.configure or configure_from_checkout(arguments.wine)
    schema = (arguments.wine or configure.parent) / 'dlls' / 'apisetschema' / 'apisetschema.spec'
    api_sets = api_sets_from_schema(schema)
    if len(api_sets) < 200:
        raise SystemExit(f'{schema}: only {len(api_sets)} API sets; pass --wine with a Wine checkout')
    upstream, version = modules_from_configure(configure)
    not_shipped = not_shipped_from_build(arguments.skip_from)
    dropped = set(not_shipped)
    modules = [name for name in upstream if name not in dropped]
    if len(modules) < 400:
        raise SystemExit(f'{configure} lists only {len(modules)} modules; is it a Wine configure?')
    modules_64, not_in_64 = split_by_farm(modules, farm_names(arguments.farm64))
    document = {
        'wine': version or 'unknown',
        'note': 'Modules the Wine runtime provides as builtins (dlls/*), minus the '
                'ones the iOS build does not ship. "modules" is the 32-bit (WoW64, '
                'i386 farm) set; "modules_64" is what the 64-bit ARM64EC farm '
                '(app/Madeira/arm64ec-windows) actually holds, and '
                '"not_in_64bit_farm" the rest, which a 64-bit program cannot load. '
                'An import matching the list for its architecture needs no '
                'compatibility entry; one in "not_shipped" (or, for a 64-bit '
                'program, in "not_in_64bit_farm") does not exist here and is reported.',
        'modules': modules,
        'not_shipped': not_shipped,
        'modules_64': modules_64,
        'not_in_64bit_farm': not_in_64,
        'api_set_prefixes': list(API_SET_PREFIXES),
        'api_sets': api_sets,
    }
    text = json.dumps(document, indent=2, sort_keys=False) + '\n'

    if arguments.check:
        current = OUT.read_text(encoding='utf-8') if OUT.exists() else ''
        if current != text:
            raise SystemExit(f'{OUT} is not current; re-run without --check')
        print(f'PASS: {OUT.name} is current ({len(modules)} modules, {len(modules_64)} in the '
              f'64-bit farm, Wine {document["wine"]})')
        return 0

    OUT.write_text(text, encoding='utf-8')
    print(f'wrote {OUT.relative_to(ROOT)}: {len(modules)} modules ({len(modules_64)} in the '
          f'64-bit farm, {len(not_in_64)} not), Wine {document["wine"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
