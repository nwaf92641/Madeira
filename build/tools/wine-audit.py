#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""Wine completeness audit: what Madeira really ships, per module and bitness.

For every Wine module (compat/wine-modules.json, from the wine submodule's
configure) this reads:
  - the module's .spec in the wine submodule: exported entries and how many
    are `stub` (an export that only logs and returns / aborts);
  - whether the 32-bit (i386, WoW64) farm installs it: every module minus
    build/wine-i386/build.sh's SKIP_REASON, with the reason;
  - whether the 64-bit ARM64EC farm (app/Madeira/arm64ec-windows, committed)
    and the native ARM64 farm (app/Madeira/aarch64-windows) have a file;
  - known backend limits on iOS (no OpenGL, Vulkan or wined3d backend, the
    64-bit media unix side behind MADEIRA_WG_64BIT, ...).
and classifies it as one of:
  missing-dll | stub-heavy | source-not-built | built-not-shipped-64 |
  backend-unavailable | opt-in-backend | replaced | ships
`--markdown FOCUS` prints the table for a list of modules (the areas of
WINE_COMPLETENESS_AUDIT.md), `--summary` the counts. The i386 farm is not in
git (it is built on a Mac); its membership is what build.sh installs.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINE = ROOT / 'wine'

# Names whose implementation on iOS is not Wine's, or whose backend is absent.
REPLACED = {
    'd3d9': "DXMT's Direct3D 9 (Metal)", 'd3d11': 'DXMT (Metal)', 'dxgi': 'DXMT (Metal)',
    'd3d10core': 'DXMT (Metal)', 'winemetal': 'DXMT (Metal)',
    'd3d8': 'd3d8to9 over DXMT d3d9 (32-bit only)', 'd3d12': "Madeira's D3D12 runtime (Metal)",
    'xtajit': 'FEX (x86 emulation)', 'xtajit64': 'FEX (x86 emulation)',
}
BACKEND_UNAVAILABLE = {
    'opengl32': 'GL-absent stub table: no OpenGL on iOS', 'glu32': 'needs OpenGL',
    'wined3d': 'no backend (no GL / Vulkan)', 'vulkan-1': 'no Vulkan', 'winevulkan': 'no Vulkan',
    'd3d12core': 'vkd3d, needs Vulkan', 'd3drm': 'wined3d', 'd3dim': 'wined3d', 'd3dim700': 'wined3d',
    'ddraw': "wined3d; cnc-ddraw over DXMT d3d9 is opt-in for 32-bit games (MADEIRA_DDRAW=cnc)",
    'ddrawex': 'wined3d', 'winex11.drv': 'no X11', 'winewayland.drv': 'no Wayland',
    'winealsa.drv': 'no ALSA (audio is wineios.drv)', 'winepulse.drv': 'no PulseAudio (audio is wineios.drv)',
    'wineoss.drv': 'no OSS', 'winecoreaudio.drv': 'macOS only (audio is wineios.drv)',
    'wineandroid.drv': 'Android only', 'mshtml': 'needs wine-gecko, not shipped',
}
OPT_IN = {
    'winegstreamer': '64-bit unix side only with MADEIRA_WG_64BIT=1 (FFmpeg-based)',
    'mscoree': 'needs the optional Wine Mono component (docs/WINE_MONO.md)',
}
SPEC_ENTRY = re.compile(r'^\s*(?:@|\d+)\s+(\w+)', re.M)


def spec_stats(module: str) -> tuple[int, int]:
    stem = module[:-4] if module.endswith('.dll') else module
    base = stem.rsplit('.', 1)[0] if '.' in stem else stem
    for spec in (WINE / 'dlls' / stem / f'{base}.spec', WINE / 'dlls' / stem / f'{stem}.spec'):
        if spec.exists():
            kinds = SPEC_ENTRY.findall(spec.read_text(encoding='utf-8', errors='replace'))
            return len(kinds), sum(1 for k in kinds if k == 'stub')
    return 0, 0


def skip_reasons() -> dict[str, str]:
    text = (ROOT / 'build/wine-i386/build.sh').read_text()
    block = re.search(r'SKIP_REASON=\((.*?)\n\)', text, re.S)
    out = {}
    for entry in re.findall(r'"([^"]+)"', block.group(1) if block else ''):
        names, _, why = entry.partition('=')
        for n in names.split('|'):
            n = n.strip().lower()
            out[n[:-4] if n.endswith('.dll') else n] = why
    return out


def farm(path: Path) -> set[str]:
    names = set()
    for f in path.iterdir():
        low = f.name.lower()
        names.add(low[:-4] if low.endswith('.dll') else low)
    return names


def audit() -> dict[str, dict]:
    mods = json.load(open(ROOT / 'compat/wine-modules.json'))
    every = sorted(set(mods['modules']) | set(mods['not_shipped']) | set(REPLACED))
    skip = skip_reasons()
    ec, aa = farm(ROOT / 'app/Madeira/arm64ec-windows'), farm(ROOT / 'app/Madeira/aarch64-windows')
    rows = {}
    for m in every:
        total, stubs = spec_stats(m)
        in_source = (WINE / 'dlls' / m).is_dir() or (WINE / 'programs' / m.removesuffix('.exe')).is_dir()
        r = {'module': m, 'source': in_source, 'exports': total, 'stubs': stubs,
             'i386': m not in skip, 'i386_reason': skip.get(m, ''), 'arm64ec': m in ec, 'aarch64': m in aa}
        if m in REPLACED:
            cls = 'replaced'
        elif not in_source:
            cls = 'missing-dll'
        elif m in BACKEND_UNAVAILABLE:
            cls = 'backend-unavailable'
        elif m in OPT_IN:
            cls = 'opt-in-backend'
        elif not r['i386'] and not r['arm64ec']:
            cls = 'source-not-built'
        elif total >= 20 and stubs * 2 > total:
            cls = 'stub-heavy'
        elif not r['arm64ec']:
            cls = 'built-not-shipped-64'
        else:
            cls = 'ships'
        r['class'] = cls
        r['note'] = REPLACED.get(m) or BACKEND_UNAVAILABLE.get(m) or OPT_IN.get(m) or r['i386_reason']
        rows[m] = r
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--markdown', nargs='*', help='modules to print as a table')
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    rows = audit()
    if a.json:
        print(json.dumps(rows, indent=1))
    if a.summary:
        from collections import Counter
        c = Counter(r['class'] for r in rows.values())
        print(f'{len(rows)} modules: ' + ', '.join(f'{k} {v}' for k, v in sorted(c.items())))
    if a.markdown is not None:
        print('| Module | Exports (stub) | 32-bit farm | 64-bit ARM64EC | ARM64 | Class | Note |')
        print('| --- | --- | --- | --- | --- | --- | --- |')
        for m in a.markdown:
            r = rows.get(m)
            if not r:
                print(f'| {m} | - | - | - | - | missing-dll | not a Wine module |')
                continue
            yn = lambda b: 'yes' if b else 'no'
            print(f"| {m} | {r['exports']} ({r['stubs']}) | {yn(r['i386'])} | {yn(r['arm64ec'])} | {yn(r['aarch64'])} "
                  f"| {r['class']} | {r['note']} |")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
