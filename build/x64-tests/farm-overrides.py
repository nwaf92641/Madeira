#!/usr/bin/env python3
"""Print a WINEDLLOVERRIDES value that makes a desktop x86_64 Wine see only the
64-bit modules Madeira's ARM64EC farm (app/Madeira/arm64ec-windows) ships.

Every Wine module (compat/wine-modules.json "modules") that the farm does not
hold is set to "" (disabled), so LoadLibrary / CoCreateInstance / an import of
it fail the way they do in a 64-bit session on the iPad. Host-only drivers a
desktop Wine needs to run at all (winex11/wayland, audio drivers) are kept.

    WINEDLLOVERRIDES="$(build/x64-tests/farm-overrides.py)" wine compat-layers-x64.exe
    build/x64-tests/farm-overrides.py --rev origin/main     # the farm as of a commit

This approximates the farm: the desktop modules are x86_64 builds of the same
Wine sources, not the ARM64EC binaries, and DXMT / the D3D12 runtime are not
involved. It shows which layers are missing, not that the iPad build works.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
HOST_ONLY = {'winex11.drv', 'winewayland.drv', 'winealsa.drv', 'winepulse.drv', 'wineoss.drv',
             'winecoreaudio.drv', 'wineandroid.drv', 'winevulkan', 'vulkan-1', 'opengl32', 'glu32'}

def farm_files(rev):
    if rev:
        out = subprocess.run(['git', '-C', str(R), 'ls-tree', '--name-only', rev, 'app/Madeira/arm64ec-windows/'],
                             capture_output=True, text=True, check=True).stdout
        return {os.path.basename(l).lower() for l in out.splitlines() if l}
    return {f.lower() for f in os.listdir(R / 'app/Madeira/arm64ec-windows')}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rev', help='take the farm listing from this git revision')
    ap.add_argument('--list', action='store_true', help='print the disabled names one per line')
    a = ap.parse_args()
    farm = farm_files(a.rev)
    stems = {f.rsplit('.', 1)[0] for f in farm} | farm
    modules = json.load(open(R / 'compat/wine-modules.json'))['modules']
    off = sorted(m for m in modules if m not in stems and m not in HOST_ONLY and m.lower() not in HOST_ONLY)
    if a.list:
        print('\n'.join(off))
    else:
        print(','.join(off) + '=')

if __name__ == '__main__':
    sys.exit(main())
