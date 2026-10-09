#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""C:\\windows\\winsxs seeding (app/Madeira/WinSxS.c), with and without a Wine.

Part 1, always (needs a C compiler):
  - The table in WinSxS.c matches every WINE_MANIFEST resource in the wine
    submodule: same assemblies, names, versions, public keys, <file> lists and
    the Common Controls <windowClass> list; processorArchitecture is the one
    asked for; the directory name is the one ntdll's lookup_winsxs searches for.
  - Behaviour on a fake farm: an assembly whose first DLL is missing is left
    out; when the farm changes (an ARM64EC session, then a plain ARM64 one),
    the arm64_ entries the new farm cannot back are removed, not left pointing
    at the other farm; a link left by an earlier install is replaced; seeding
    twice gives the same tree; a wrong architecture is refused.

Part 2, with a host WoW64 Wine (HOST_WINE) and llvm-mingw (LLVM_MINGW):
  two probe programs (32-bit and 64-bit) whose manifests ask for
  Common-Controls 6.0 (processorArchitecture="*") and Microsoft.VC80.CRT, and
  which import and call TaskDialogIndirect.
  - In a prefix without winsxs (what Madeira had for every 64-bit program):
    comctl32 5.x is loaded, and the TaskDialogIndirect call aborts with
    "unimplemented function COMCTL32.dll.TaskDialogIndirect".
    msvcr80 still loads, from system32: no c0150002 under Wine.
  - After madeira_winsxs_seed (x86 from lib/wine/i386-windows, amd64 from
    lib/wine/x86_64-windows): comctl32 6 is loaded from winsxs, the call
    returns, msvcr80 comes from winsxs.
  The host is x86_64, so Wine's 64-bit side is amd64 here; on the iPad the
  64-bit store is arm64 (WinSxS.h says why) and that needs a device.
  Prints SKIP for part 2 without HOST_WINE/LLVM_MINGW.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'app/Madeira/WinSxS.c'
WINE = ROOT / 'wine'
LLVM_MINGW = Path(os.environ.get('LLVM_MINGW', '/nonexistent'))
HOST_WINE = Path(os.environ.get('HOST_WINE', '/nonexistent'))
NS = '{urn:schemas-microsoft-com:asm.v1}'

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures += 1


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


DRIVER = r'''
#include "WinSxS.h"
#include <stdio.h>
int main(int argc, char **argv)
{
    madeira_winsxs_stats st;
    if (argc != 4) return 2;
    int r = madeira_winsxs_seed(argv[1], argv[2], argv[3], &st);
    printf("ret=%d seeded=%d absent=%d failed=%d total=%d\n", r, st.seeded, st.absent, st.failed, st.total);
    return r ? 1 : 0;
}
'''


def identity(path: Path) -> dict:
    root = ET.parse(path).getroot()
    ai = root.find(NS + 'assemblyIdentity')
    files = []
    for f in root.findall(NS + 'file'):
        files.append((f.get('name'), [c.text for c in f.findall(NS + 'windowClass')]))
    return {'name': ai.get('name'), 'version': ai.get('version'), 'arch': ai.get('processorArchitecture'),
            'key': ai.get('publicKeyToken'), 'type': ai.get('type'), 'files': files}


def wine_manifests() -> list[Path]:
    out = []
    for rc in sorted(WINE.glob('dlls/*/*.rc')):
        for m in re.finditer(r'^WINE_MANIFEST\w*\s+\S+\s+(\S+)', rc.read_text(errors='replace'), re.M):
            out.append(rc.parent / m.group(1))
    return out


def tree(base: Path) -> dict:
    res = {}
    for p in sorted(base.rglob('*')):
        rel = str(p.relative_to(base))
        res[rel] = ('link', os.readlink(p)) if p.is_symlink() else ('dir',) if p.is_dir() else ('file', p.read_bytes())
    return res


def seed(drv: Path, prefix: Path, farm: Path, arch: str):
    r = run([str(drv), str(prefix), str(farm), arch])
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def part1(work: Path) -> Path | None:
    cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    if not cc:
        print('SKIP: no C compiler')
        return None
    (work / 'driver.c').write_text(DRIVER)
    drv = work / 'seed'
    b = run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-D_DEFAULT_SOURCE', '-fsanitize=address,undefined',
             '-g', '-I', str(SRC.parent), str(SRC), str(work / 'driver.c'), '-o', str(drv)])
    check(b.returncode == 0, 'WinSxS.c compiles with -Wall -Wextra -Werror under ASan/UBSan')
    if b.returncode:
        print(b.stderr)
        return None

    # A farm that has every file the table names.
    full = work / 'farm-full'
    full.mkdir()
    names = set(re.findall(r'\{ "([\w.]+\.dll)", "([\w.]+\.dll)" \}', SRC.read_text()))
    for _, in_farm in names:
        (full / in_farm).write_bytes(b'MZ' + in_farm.encode())
    pfx = work / 'pfx'
    rc, out, err = seed(drv, pfx, full, 'arm64')
    check(rc == 0 and 'ret=0' in out, f'seeding from a full farm succeeds ({out})')
    m = re.search(r'seeded=(\d+) absent=(\d+) failed=(\d+) total=(\d+)', out)
    sources = wine_manifests()
    check(m and int(m.group(1)) == int(m.group(4)) == len(sources),
          f'one assembly per WINE_MANIFEST resource in the wine tree ({len(sources)}: '
          f'{", ".join(str(s.relative_to(WINE)) for s in sources)})')
    check('[WineProc] winsxs: arm64:' in err, 'one summary line for the launch log')

    winsxs = pfx / 'drive_c/windows/winsxs'
    written = {p.name: identity(p) for p in (winsxs / 'manifests').glob('*.manifest')}
    for src in sources:
        want = identity(src)
        key = None
        for fname, got in written.items():
            if (got['name'], got['version']) == (want['name'], want['version']):
                key = fname
        rel = src.relative_to(WINE)
        if not key:
            check(False, f'{rel}: an assembly for {want["name"]} {want["version"]}')
            continue
        got = written[key]
        check(got['key'] == want['key'] and got['type'] == want['type'] and want['arch'] == '' and got['arch'] == 'arm64',
              f'{rel}: identity matches, processorArchitecture filled in as arm64')
        check(got['files'] == want['files'],
              f'{rel}: <file> list{" and windowClass list" if any(c for _, c in want["files"]) else ""} match')
        dirname = f'arm64_{want["name"].lower()}_{want["key"]}_{want["version"]}_none_deadbeef'
        check(key == dirname + '.manifest' and (winsxs / dirname).is_dir(),
              f'{rel}: named {dirname} (the form lookup_winsxs searches)')
        for fname, _ in want['files']:
            link = winsxs / dirname / fname
            check(link.is_symlink() and Path(os.readlink(link)).parent == full,
                  f'{rel}: {fname} is a link into the farm')
    text = (winsxs / 'manifests' / next(k for k in written if 'common-controls' in k)).read_bytes()
    check(not text.startswith(b'\xef\xbb\xbf') and b'\r' not in text and text.startswith(b'<?xml'),
          'manifests are UTF-8 without BOM, LF line ends')

    before = tree(pfx)
    seed(drv, pfx, full, 'arm64')
    check(tree(pfx) == before, 'seeding twice gives the same tree')

    # A partial farm, as a plain ARM64 session has (no comctl32_v6, no CRTs).
    part = work / 'farm-aarch64'
    part.mkdir()
    (part / 'gdiplus.dll').write_bytes(b'MZ')
    rc, out, _ = seed(drv, pfx, part, 'arm64')
    check('seeded=2 absent=8' in out, f'a farm with only gdiplus backs the two GDI+ assemblies ({out})')
    left = sorted(p.name for p in (winsxs / 'manifests').glob('arm64_*'))
    check(left == sorted(f'arm64_microsoft.windows.gdiplus_6595b64144ccf1df_{v}_none_deadbeef.manifest'
                         for v in ('1.0.6000.16386', '1.1.7601.23038')),
          f'arm64_ entries the new farm cannot back are removed, not left pointing at the old farm ({left})')
    check(not (winsxs / 'arm64_microsoft.windows.common-controls_6595b64144ccf1df_6.0.2600.2982_none_deadbeef').exists(),
          'the Common Controls directory went with its manifest')
    gd = winsxs / 'arm64_microsoft.windows.gdiplus_6595b64144ccf1df_1.1.7601.23038_none_deadbeef/gdiplus.dll'
    check(Path(os.readlink(gd)).parent == part, 'the link that stays was repointed to the new farm')

    # Another architecture next to it is not touched.
    rc, out, _ = seed(drv, pfx, full, 'x86')
    check(len(list((winsxs / 'manifests').glob('arm64_*'))) == 2 and
          len(list((winsxs / 'manifests').glob('x86_*'))) == len(sources),
          'x86 and arm64 stores live side by side and are seeded independently')

    # A dangling link from an earlier install (the bundle path changes).
    cc6 = winsxs / 'x86_microsoft.windows.common-controls_6595b64144ccf1df_6.0.2600.2982_none_deadbeef/comctl32.dll'
    cc6.unlink()
    cc6.symlink_to('/nonexistent/old-bundle/comctl32_v6.dll')
    seed(drv, pfx, full, 'x86')
    check(os.readlink(cc6) == str(full / 'comctl32_v6.dll'), 'a link into a previous install is replaced')

    for bad in ('amd64x', 'ARM64', ''):
        rc, out, _ = seed(drv, pfx, full, bad)
        check(rc != 0, f'architecture {bad!r} is refused')
    return drv


PROBE_C = r'''
#include <windows.h>
#include <commctrl.h>
#include <shlwapi.h>
#include <stdio.h>
static HRESULT CALLBACK cb(HWND h, UINT n, WPARAM w, LPARAM l, LONG_PTR d)
{
    if (n == TDN_CREATED) PostMessageW(h, TDM_CLICK_BUTTON, IDOK, 0);
    return S_OK;
}
int main(void)
{
    char path[MAX_PATH] = "";
    HMODULE cc = GetModuleHandleA("comctl32.dll"), crt;
    DLLGETVERSIONPROC gv = cc ? (DLLGETVERSIONPROC)GetProcAddress(cc, "DllGetVersion") : NULL;
    DLLVERSIONINFO vi = { sizeof(vi) };
    TASKDIALOGCONFIG c = { sizeof(c) };
    int button = 0;
    HRESULT hr;
    if (gv) gv(&vi);
    GetModuleFileNameA(cc, path, sizeof path);
    printf("comctl32=%s\nversion=%lu\n", path, vi.dwMajorVersion);
    crt = LoadLibraryA("msvcr80.dll");
    path[0] = 0;
    if (crt) GetModuleFileNameA(crt, path, sizeof path);
    printf("msvcr80=%s\n", crt ? path : "(not loaded)");
    fflush(stdout);
    c.pszContent = L"probe";
    c.dwCommonButtons = TDCBF_OK_BUTTON;
    c.pfCallback = cb;
    hr = TaskDialogIndirect(&c, &button, NULL, NULL);
    printf("taskdialog=returned hr=%08lx\n", hr);
    return 0;
}
'''

MANIFEST = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency><dependentAssembly>
    <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
  </dependentAssembly></dependency>
  <dependency><dependentAssembly>
    <assemblyIdentity type="win32" name="Microsoft.VC80.CRT" version="8.0.50727.762" processorArchitecture="{arch}" publicKeyToken="1fc8b3b9a1e18e3b"/>
  </dependentAssembly></dependency>
</assembly>
'''


def part2(work: Path, drv: Path) -> None:
    tc = LLVM_MINGW / 'bin'
    wine = HOST_WINE / 'bin/wine'
    if not (tc / 'x86_64-w64-mingw32-clang').exists() or not wine.exists() \
            or not (HOST_WINE / 'lib/wine/i386-windows/comctl32_v6.dll').exists():
        print('SKIP: Wine part: set LLVM_MINGW (llvm-mingw root) and HOST_WINE (installed WoW64 Wine)')
        return
    exes = {}
    for bits, triple, arch in ((32, 'i686', 'x86'), (64, 'x86_64', 'amd64')):
        (work / f'm{bits}.manifest').write_text(MANIFEST.format(arch=arch))
        (work / f'r{bits}.rc').write_text(f'1 24 "m{bits}.manifest"\n')
        (work / 'probe.c').write_text(PROBE_C)
        r1 = run([str(tc / f'{triple}-w64-mingw32-windres'), f'r{bits}.rc', '-o', f'r{bits}.o'], cwd=work)
        r2 = run([str(tc / f'{triple}-w64-mingw32-clang'), 'probe.c', f'r{bits}.o', '-o', f'probe{bits}.exe',
                  '-lcomctl32', '-lshlwapi'], cwd=work)
        check(r1.returncode == 0 and r2.returncode == 0, f'{bits}-bit probe builds (manifest: CC 6.0 "*", VC80.CRT {arch})')
        exes[bits] = work / f'probe{bits}.exe'

    prefix = work / 'wpfx'
    base = os.environ.get('HOST_WINEPREFIX')
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
    if base:
        shutil.copytree(base, prefix, symlinks=True)
    else:
        r = run([str(wine), 'wineboot', '-i'], env=env, timeout=600)
        check(r.returncode == 0, 'a fresh prefix boots')
    # Bring the prefix up to date first: a pending update (wine.inf newer than
    # .update-timestamp) would run wineboot again on the first probe and
    # rebuild the store this test removes.
    run([str(wine), 'wineboot', '-u'], env=env, timeout=600)
    run([str(HOST_WINE / 'bin/wineserver'), '-w'], env=env, timeout=600)
    real = prefix / 'drive_c/windows/winsxs'
    check(real.is_dir(), 'wineboot built winsxs in the host prefix (Madeira strips it: build-prefix-snapshot.sh)')
    shutil.rmtree(real, ignore_errors=True)

    def probe(bits):
        r = run([str(wine), str(exes[bits])], env=env, timeout=120)
        return r.stdout + r.stderr

    for bits, arch in ((32, 'x86'), (64, 'amd64')):
        out = probe(bits)
        if os.environ.get('VERBOSE'):
            print(out)
        check(re.search(r'comctl32=C:\\windows\\system32\\COMCTL32.dll\s+version=5', out, re.I) is not None,
              f'{bits}-bit, no winsxs: comctl32 5 from system32')
        check('unimplemented function COMCTL32.dll.TaskDialogIndirect' in out and 'taskdialog=returned' not in out,
              f'{bits}-bit, no winsxs: the TaskDialogIndirect call aborts (bound to a stub)')
        check(re.search(r'msvcr80=C:\\windows\\system32\\msvcr80.dll', out, re.I) is not None,
              f'{bits}-bit, no winsxs: msvcr80 still loads from system32 (no c0150002 under Wine)')

    run([str(HOST_WINE / 'bin/wineserver'), '-k'], env=env)
    for bits, arch, farm in ((32, 'x86', 'i386-windows'), (64, 'amd64', 'x86_64-windows')):
        rc, out, _ = seed(drv, prefix, HOST_WINE / 'lib/wine' / farm, arch)
        check(rc == 0 and 'absent=0 failed=0' in out, f'seed {arch} from lib/wine/{farm} ({out})')
    for bits, arch in ((32, 'x86'), (64, 'amd64')):
        out = probe(bits)
        check(re.search(rf'comctl32=C:\\windows\\winsxs\\{arch}_microsoft.windows.common-controls_[^\\]+\\COMCTL32.dll\s+version=6',
                        out, re.I) is not None, f'{bits}-bit, seeded: comctl32 6 from winsxs\\{arch}_...')
        check('taskdialog=returned' in out and 'unimplemented function' not in out,
              f'{bits}-bit, seeded: TaskDialogIndirect returns')
        check(re.search(rf'msvcr80=C:\\windows\\winsxs\\{arch}_microsoft.vc80.crt_', out, re.I) is not None,
              f'{bits}-bit, seeded: msvcr80 comes from the VC80.CRT assembly')
    run([str(HOST_WINE / 'bin/wineserver'), '-k'], env=env)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        drv = part1(work)
        if drv:
            part2(work, drv)
    print(('PASS' if not failures else 'FAIL') + f': winsxs seeding ({failures} failure(s))')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
