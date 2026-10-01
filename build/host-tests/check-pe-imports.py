#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
r"""The 64-bit farms load: every import a shipped module needs is in the farm.

A module that imports a DLL the farm does not carry never reaches its first
instruction, and the farm is assembled by a build script (build/wine-i386,
build/wine-arm64ec) whose policy list is where that is decided.  This checks the
artefacts, not the policy:

  * every file in app/Madeira/arm64ec-windows is an ARM64EC image (and every file
    in aarch64-windows a native ARM64 one), so a copy from the wrong tree is
    caught here rather than by the guest;
  * no module has a load-time import that no farm provides -- the one allowed
    gap is named with its reason and the rebuild that closes it;
  * the delay-load gaps are a subset of the recorded set: they are code paths
    that will fail later, and the recorded set is what the module list already
    knows about (gdiplus, shdocvw, mlang, ...), so a new one is a farm change to
    look at, not a silent one;
  * app/Madeira/compat.json's wine_modules_64 is exactly what the farm
    directories hold, so the engine cannot claim the 64-bit runtime serves a
    module that is not shipped (or miss one that is).

Run from anywhere; the farms are read from the repository.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app/Madeira'
TOOL = ROOT / 'build/tools/pe-imports.py'
SCRIPT = ROOT / 'build/wine-arm64ec/build.sh'
DB_PATH = APP / 'compat.json'

# The 64-bit farms, and the COFF machine each one must hold.  An ARM64EC image
# reports IMAGE_FILE_MACHINE_AMD64 (its x86-64 half is what the loader sees)
# with the hybrid metadata the ARM64EC loader reads.
FARMS = {
    'arm64ec-windows': 'x64',
    'aarch64-windows': 'arm64',
}
FARM32 = 'i386-windows'

# A module the farm is knowingly without.  bthprops.cpl is the Bluetooth
# Control Panel applet: it imports bluetoothapis.dll at load time, and the farm
# predates the build script that would install the pair.  It is the only gap of
# this kind, and app/Madeira/arm64ec-windows is rebuilt wholesale by
# build/wine-arm64ec/build.sh, which installs bluetoothapis.dll (it is a stock
# Wine module) rather than leaving the applet unloadable.
ALLOWED_LOAD_GAPS = {
    'bluetoothapis.dll':
        'imported by bthprops.cpl; a stock Wine module the hand-built farm never '
        'copied. build/wine-arm64ec/build.sh installs every module the tree has a '
        'rule for, which includes it',
}
# Delay-load gaps are not fatal, so they are recorded, not forbidden.  Each is a
# stock Wine module the 64-bit farm does not carry yet: shell32 reaching for
# gdiplus/shdocvw is the one a game can trip over.
RECORDED_DELAY_GAPS = {
    'advpack.dll', 'cabinet.dll', 'cryptsp.dll', 'evr.dll', 'gdiplus.dll',
    'glu32.dll', 'inetcomm.dll', 'mlang.dll', 'shdocvw.dll',
}
# Names the farm script must refuse to install, whatever the tree builds: each
# is produced and installed by its own component (DXMT, Madeira's D3D12, FEX,
# the WoW64 work), and copying Wine's build over it would replace a working
# implementation with one that has no backend in this port.
MUST_NOT_INSTALL = {
    'd3d9.dll', 'd3d10core.dll', 'd3d11.dll', 'dxgi.dll', 'winemetal.dll',
    'd3d12.dll', 'madeira_d3d12.dll', 'xtajit.dll', 'xtajit64.dll',
    'wow64.dll', 'wow64win.dll',
}
# The policy is where the classification lives.  A module whose PE cannot load
# here (its DllMain fails when the unix side is absent, or it is a host display
# or audio driver) has to stay named in it: dropping one means deciding it can
# build, and then the reason goes too.
MUST_STAY_SKIPPED = {
    # DllMain fails the load without the unix side
    'localspl.dll', 'wineps.drv', 'msv1_0.dll', 'capi2032.dll', 'ctapi32.dll',
    'sane.ds', 'opencl.dll', 'winevulkan.dll',
    # host display and audio drivers: no iOS implementation behind them
    'winemac.drv', 'winex11.drv', 'winewayland.drv', 'wineandroid.drv',
    'winealsa.drv', 'winepulse.drv', 'wineoss.drv', 'winecoreaudio.drv',
}
# ... and the other half of the classification: a module whose unix side is a
# device or a host library, but whose DllMain tolerates the stub table, loads
# and fails only its own feature.  Those are installed -- this is the farm
# gaining DirectShow device enumeration, the DMO decoders, ODBC's installer API,
# MSI, XAudio2 and the d3dx9 family rather than refusing them.
MUST_BE_INSTALLED = {
    'qcap.dll', 'avicap32.dll', 'winedmo.dll', 'odbc32.dll', 'odbccp32.dll',
    'kerberos.dll', 'winscard.dll', 'wpcap.dll', 'devenum.dll', 'msi.dll',
    'quartz.dll', 'xaudio2_7.dll', 'd3dx9_43.dll', 'd3dcompiler_47.dll',
    'winegstreamer.dll', 'winspool.drv', 'ir50_32.dll',
}
failures = 0


def require(condition: bool, label: str) -> None:
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def load_tool():
    spec = importlib.util.spec_from_file_location('pe_imports', TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules['pe_imports'] = module
    spec.loader.exec_module(module)
    return module


def policy_entries(text: str) -> list[tuple[list[str], str]]:
    """[(names, reason)] from the farm script's SKIP_REASON array."""
    block = re.search(r'SKIP_REASON=\((.*?)\n\)', text, re.S)
    entries: list[tuple[list[str], str]] = []
    for raw in re.findall(r'"([^"]+)"', block.group(1) if block else ''):
        head, _, reason = raw.partition('=')
        entries.append(([name.strip() for name in head.split('|') if name.strip()],
                        reason.strip()))
    return entries


def check_build_policy() -> None:
    """The farm script is the policy: every skip says why, and covers the audit."""
    require(SCRIPT.exists(), 'the 64-bit farm has the build script the 32-bit one has')
    if not SCRIPT.exists():
        return
    text = SCRIPT.read_text(encoding='utf-8')
    entries = policy_entries(text)
    require(bool(entries) and all(reason for _, reason in entries),
            f'every policy skip in the farm script gives its reason ({len(entries)} entries)')
    flattened = [name for names, _ in entries for name in names]
    require(len(flattened) == len(set(flattened)),
            f'{len(flattened)} skips, no name twice')
    covered = set(flattened)
    for label, required in (("the components' own modules", MUST_NOT_INSTALL),
                            ('the modules that cannot load', MUST_STAY_SKIPPED)):
        missing = sorted(required - covered)
        require(not missing, f'the farm script leaves {label} to their owners'
                + (f' (not skipped: {", ".join(missing)})' if missing else ''))
    refused = sorted(MUST_BE_INSTALLED & covered)
    require(not refused,
            'the modules that load with a failing feature are installed, not refused'
            + (f' (refused: {", ".join(refused)})' if refused else ''))
    require('--enable-archs=' in text and '--enable-winegstreamer' in text,
            'the farm script configures the tree per architecture, with winegstreamer')
    require('ARCH=arm64ec' in text or 'ARCH:-arm64ec' in text,
            'the arm64ec farm is the default the x86-64 sessions need')
    require('pe-imports.py' in text and '--fail-on-load-gap' in text,
            'the farm script checks the import closure of what it installs')
    require('build-ntdll.sh' in text and 'ntdll.dll' in text,
            'the farm script leaves ntdll to the strip/pad step that owns it')


def main() -> int:
    tool = load_tool()
    farm_paths = [APP / name for name in FARMS]
    present = [path for path in farm_paths if path.is_dir()]
    require(present == farm_paths, f'both 64-bit farms are present ({len(present)} of {len(FARMS)})')

    per_farm = {}
    for name, expected in FARMS.items():
        modules = tool.modules_in(APP / name)
        per_farm[name] = modules
        if not modules:
            require(False, f'{name} holds modules')
            continue
        wrong = sorted(module for module, path in modules.items()
                       if tool.imports_of(path).arch != expected)
        require(not wrong,
                f'{name}: {len(modules)} modules, all {expected} images'
                + (f' (not {expected}: {", ".join(wrong[:6])})' if wrong else ''))
        require(len(modules) >= 100, f'{name}: {len(modules)} modules is a farm, not a sample')

    farm32 = tool.modules_in(APP / FARM32)
    if farm32:
        wrong = sorted(module for module, path in farm32.items()
                       if tool.imports_of(path).arch != 'i386')
        require(not wrong, f'{FARM32}: {len(farm32)} modules, all i386 images')
    else:
        print(f'note: {FARM32} is not built here (gitignored, docs/BUILDING.md)')

    check_build_policy()

    load_gaps, delay_gaps = tool.check_farms(present, ALLOWED_LOAD_GAPS)
    for name, users in sorted(load_gaps.items()):
        require(False, f'load-time import {name} missing (imported by {", ".join(users)})')
    if not load_gaps:
        require(True, 'no module has an unresolved load-time import'
                      f' ({len(ALLOWED_LOAD_GAPS)} allowed gap, named in this test)')

    unexpected = {name: users for name, users in delay_gaps.items()
                  if name not in RECORDED_DELAY_GAPS}
    for name, users in sorted(unexpected.items()):
        require(False, f'new delay-load gap {name} (imported by {", ".join(users)})')
    if not unexpected:
        closed = len(RECORDED_DELAY_GAPS) - len(delay_gaps)
        require(True, f'{len(delay_gaps)} delay-load gaps, all in the recorded set'
                      + (f' ({closed} closed)' if closed else ''))

    if DB_PATH.exists():
        data = json.loads(DB_PATH.read_text(encoding='utf-8'))
        # The compat list is about modules a program imports; a program in the
        # farm (an .exe) is what the launch path probes, not an import target.
        shipped = sorted(
            module[:-4] if module.endswith('.dll') else module
            for name in FARMS for module, path in per_farm.get(name, {}).items()
            if not module.endswith('.exe')
        )
        claimed = sorted(name.lower() for name in data.get('wine_modules_64') or [])
        missing = sorted(set(shipped) - set(claimed))
        extra = sorted(set(claimed) - set(shipped))
        require(not missing and not extra,
                f'compat.json wine_modules_64 is the farm ({len(claimed)} names)'
                + (f'; not in the data: {missing[:6]}' if missing else '')
                + (f'; not in a farm: {extra[:6]}' if extra else ''))
    else:
        require(False, 'app/Madeira/compat.json is present')

    print(f'\n{"FAIL" if failures else "PASS"}: PE imports ({failures} failure(s))')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
