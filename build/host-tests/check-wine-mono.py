#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""Wine Mono as an optional component (app/Madeira/WineMono.c, docs/WINE_MONO.md).

Part 1, always (needs a C compiler):
  - MADEIRA_WINE_MONO_VERSION is the WINE_MONO_VERSION the wine submodule's
    mscoree asks for (a mismatch would link a folder mscoree never accepts).
  - madeira_wine_mono_link on temporary trees: no component -> nothing linked;
    a complete component -> C:\\windows\\mono\\mono-2.0 links to it, and linking
    again is a no-op; the component removed -> the dangling link is removed;
    a component missing libmono-2.0-x86_64.dll -> "incomplete", not linked;
    another version only -> "wrong version"; a real install in the prefix is
    left alone; a link left by an earlier container path is replaced.

Part 2, with a host Wine (HOST_WINE, an x86_64 Wine 11.x build or install;
WINE_PREFIX_TEMPLATE, a prefix to copy) and the unpacked component
(WINE_MONO_DIR=/path/to/wine-mono-11.0.0):
  - before the link, csc.exe (a .NET program) stops at "Wine Mono is not
    installed", the line the launch diagnostics turn into the hint;
  - after madeira_wine_mono_link, csc.exe compiles a C# program and that
    program runs and prints its line.
  This is Wine's mscoree on x86_64; on the iPad mscoree is ARM64EC and Mono's
  x86_64 JIT output runs under FEX, which needs a device (docs/WINE_MONO.md).
  Prints SKIP for part 2 without HOST_WINE / WINE_MONO_DIR.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'app/Madeira/WineMono.c'
HDR = ROOT / 'app/Madeira/WineMono.h'
failures: list[str] = []


def check(cond: bool, what: str) -> None:
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


HARNESS = r'''
#include "WineMono.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv)
{
    madeira_wine_mono_state s = madeira_wine_mono_link(argv[1], argc > 2 ? argv[2] : NULL);
    printf("%d\n", (int)s);
    return 0;
}
'''


def build_harness(tmp: Path) -> Path:
    exe = tmp / 'monolink'
    (tmp / 'h.c').write_text(HARNESS)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-I', str(HDR.parent),
                    '-o', str(exe), str(tmp / 'h.c'), str(SRC)], check=True)
    return exe


def run(exe: Path, prefix: Path, comps: Path | None) -> tuple[int, str]:
    args = [str(exe), str(prefix)] + ([str(comps)] if comps else [])
    r = subprocess.run(args, capture_output=True, text=True)
    return int(r.stdout.strip() or -99), r.stderr


def make_component(path: Path, x86_64: bool = True) -> None:
    (path / 'bin').mkdir(parents=True)
    (path / 'lib/mono/4.5').mkdir(parents=True)
    (path / 'bin/libmono-2.0-x86.dll').write_bytes(b'MZ')
    if x86_64:
        (path / 'bin/libmono-2.0-x86_64.dll').write_bytes(b'MZ')


def part1() -> None:
    wine_version = re.search(r'#define WINE_MONO_VERSION "([^"]+)"',
                             (ROOT / 'wine/dlls/mscoree/mscoree_private.h').read_text())
    ours = re.search(r'#define MADEIRA_WINE_MONO_VERSION "([^"]+)"', HDR.read_text())
    if wine_version:
        check(ours and ours.group(1) == wine_version.group(1),
              f'MADEIRA_WINE_MONO_VERSION matches the wine submodule ({wine_version.group(1)})')
    else:
        print('SKIP: wine submodule not checked out; version not compared')
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        exe = build_harness(tmp)
        prefix, comps = tmp / 'pfx', tmp / 'Documents/Components'
        (prefix / 'drive_c/windows').mkdir(parents=True)
        link = prefix / 'drive_c/windows/mono/mono-2.0'
        state, err = run(exe, prefix, comps)
        check(state == 2 and not link.exists() and 'not installed' in err, 'no component: nothing linked, logged')
        version = ours.group(1) if ours else '11.0.0'
        component = comps / f'wine-mono-{version}'
        make_component(component)
        state, err = run(exe, prefix, comps)
        check(state == 0 and link.is_symlink() and os.readlink(link) == str(component), 'component: linked')
        check((link / 'bin/libmono-2.0-x86_64.dll').exists(), 'the link reaches what mscoree looks for')
        state, err = run(exe, prefix, comps)
        check(state == 0 and os.readlink(link) == str(component), 'linking again keeps the link')
        os.unlink(link)
        os.symlink('/nonexistent/old-container/Documents/Components/wine-mono-' + version, link)
        state, err = run(exe, prefix, comps)
        check(state == 0 and os.readlink(link) == str(component), 'a link from an earlier container path is replaced')
        shutil.rmtree(component)
        state, err = run(exe, prefix, comps)
        check(state == 2 and not os.path.lexists(link), 'component removed: the dangling link is removed')
        make_component(component, x86_64=False)
        state, err = run(exe, prefix, comps)
        check(state == 4 and not os.path.lexists(link) and 'incomplete' in err,
              'a component without libmono-2.0-x86_64.dll is incomplete, not linked')
        shutil.rmtree(component)
        make_component(comps / 'wine-mono-10.1.0')
        state, err = run(exe, prefix, comps)
        check(state == 3 and 'wine-mono-10.1.0' in err and not os.path.lexists(link),
              'another version only: reported, not linked')
        make_component(component)
        real = prefix / 'drive_c/windows/mono/mono-2.0'
        real.mkdir(parents=True)
        state, err = run(exe, prefix, comps)
        check(state == 1 and real.is_dir() and not real.is_symlink(), 'a real install in the prefix is left alone')
        state = int(subprocess.run([str(exe), '', str(comps)], capture_output=True, text=True).stdout.strip())
        check(state == -1, 'no prefix: refused')


def part2() -> None:
    wine = os.environ.get('HOST_WINE')
    mono = os.environ.get('WINE_MONO_DIR')
    template = os.environ.get('WINE_PREFIX_TEMPLATE')
    if not (wine and mono and template and Path(mono, 'lib/mono/4.5/csc.exe').exists()):
        print('SKIP: part 2 needs HOST_WINE, WINE_PREFIX_TEMPLATE and WINE_MONO_DIR (an unpacked wine-mono)')
        return
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        exe = build_harness(tmp)
        prefix = tmp / 'pfx'
        shutil.copytree(template, prefix, symlinks=True)
        shutil.rmtree(prefix / 'drive_c/windows/mono', ignore_errors=True)
        comps = tmp / 'Components'
        comps.mkdir()
        os.symlink(mono, comps / Path(mono).name)
        env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='err+all,fixme-all', DISPLAY='')
        csc = 'Z:' + str(comps / Path(mono).name / 'lib/mono/4.5/csc.exe').replace('/', '\\')
        src = tmp / 'hello.cs'
        src.write_text('class P { static void Main() { System.Console.WriteLine("madeira-dotnet-ok " + '
                       'System.Environment.Is64BitProcess); } }\n')
        out = 'Z:' + str(tmp / 'hello.exe').replace('/', '\\')
        cmd = [wine, csc, '/nologo', '/out:' + out, 'Z:' + str(src).replace('/', '\\')]
        before = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
        check('Wine Mono is not installed' in before.stderr and not (tmp / 'hello.exe').exists(),
              'before: a .NET program stops at "Wine Mono is not installed"')
        state, err = run(exe, prefix, comps)
        check(state == 0, 'madeira_wine_mono_link links the component into the prefix')
        after = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
        check((tmp / 'hello.exe').exists(), 'after: csc.exe (managed) compiles a C# program'
              + ('' if (tmp / 'hello.exe').exists() else ' -- ' + after.stdout[-400:] + after.stderr[-800:]))
        if (tmp / 'hello.exe').exists():
            r = subprocess.run([wine, out], env=env, capture_output=True, text=True, timeout=600)
            check('madeira-dotnet-ok True' in r.stdout, f'after: the compiled program runs as a 64-bit process ({r.stdout.strip()!r})')
        subprocess.run([str(Path(wine).parent / 'server/wineserver') if (Path(wine).parent / 'server/wineserver').exists()
                        else 'wineserver', '-k'], env=env, capture_output=True)


def main() -> int:
    part1()
    part2()
    if failures:
        print(f'\nFAIL: wine-mono ({len(failures)} failure(s))')
        return 1
    print('\nPASS: Wine Mono component linking')
    return 0


if __name__ == '__main__':
    sys.exit(main())
