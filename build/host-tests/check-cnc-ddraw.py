#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""cnc-ddraw (third_party/cnc-ddraw, MIT): the DLL build/ddraw/build.sh makes,
the ddraw.ini it ships, and the DLL driven by a 32-bit DirectDraw program under
a host WoW64 Wine with a recording Direct3D 9 underneath.

Part 1 (needs LLVM_MINGW only):
  - the build works without Microsoft's SDK headers (MADEIRA_IMPORT.md);
  - the DLL exports DirectDrawCreate/Ex and the enumerators, is NOT marked as a
    Wine builtin (it must load as a native DLL), imports only DLLs the i386
    Wine farm has (compat/wine-modules.json) and nothing OpenGL or Vulkan;
  - ddraw.ini: [ddraw] renderer=direct3d9 (cnc-ddraw's "auto" never picks
    Direct3D 9 under Wine: it tries OpenGL, which iOS lacks), per-game opengl renderers rewritten, the ~290 game
    sections kept, readable by cnc-ddraw's own parser (part 2 proves that).

Part 2 (LLVM_MINGW + HOST_WINE + WINEBUILD, as check-d3d8to9-wine.py):
  a private copy of the Wine install whose i386 farm has a builtin-marked
  recording d3d9.dll in place of Wine's (DXMT's d3d9 is builtin-marked the same
  way on Madeira), a prefix with Wine's null display driver (no X needed), and
  cnc-ddraw's ddraw.dll in C:\\windows\\syswow64 -- as the app links it -- with
  CNC_DDRAW_CONFIG_FILE pointing at the shipped ddraw.ini. A 32-bit program sets
  640x480x8, creates the primary surface and a palette, draws, flips.
  Checks: cnc-ddraw (not Wine's ddraw) is the module that loaded, it logs
  "[cnc-ddraw] renderer direct3d9", Direct3D 9 gets CreateDevice, textures,
  a vertex buffer and Present calls; with ddraw=n,b missing from
  WINEDLLOVERRIDES Wine's own ddraw loads instead (why the recipe sets it);
  with renderer=auto cnc-ddraw never picks Direct3D 9 under Wine; with Direct3DCreate9
  failing it logs the fallback to GDI.

What it does not prove: DXMT or Metal. Whether DXMT's d3d9 accepts cnc-ddraw's
device (D3DCREATE_MULTITHREADED|HARDWARE_VERTEXPROCESSING|PUREDEVICE, managed
textures, ps_2_0 palette shader) and what it looks like is an iPad check
(docs/DIRECTDRAW.md).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LLVM_MINGW = Path(os.environ.get('LLVM_MINGW', '/nonexistent'))
HOST_WINE = Path(os.environ.get('HOST_WINE', '/nonexistent'))
WINEBUILD = os.environ.get('WINEBUILD') or shutil.which('winebuild') or '/nonexistent'
TC = LLVM_MINGW / 'bin'
CC = TC / 'i686-w64-mingw32-clang'
CXX = TC / 'i686-w64-mingw32-clang++'
D3D9_H = LLVM_MINGW / 'generic-w64-mingw32/include/d3d9.h'

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures += 1


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors='replace', **kw)


def d3d8_harness():
    spec = importlib.util.spec_from_file_location('d3d8h', ROOT / 'build/host-tests/check-d3d8to9-wine.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mock_source(h, header: str) -> str:
    """A recording Direct3D 9 covering what render_d3d9.c calls."""
    U = h.UNKNOWN
    tex = h.mock_class(header, 'IDirect3DTexture9', 'MockTex', {
        **U,
        'LockRect': 'if (!bits) bits = (BYTE *)calloc((size_t)w * h, 4); a1->pBits = bits; a1->Pitch = (INT)w * 4; '
                    'locks++; return D3D_OK;',
        'UnlockRect': 'return D3D_OK;',
        'GetLevelDesc': 'memset(a1, 0, sizeof(*a1)); a1->Width = w; a1->Height = h; a1->Format = fmt; return D3D_OK;',
        'GetLevelCount': 'return 1;',
    }, '  UINT w = 0, h = 0; D3DFORMAT fmt = D3DFMT_UNKNOWN; BYTE *bits = nullptr; int locks = 0;\n'
       '  ~MockTex() { mock_log("texture %ux%u fmt %u released after %d lock(s)", w, h, (unsigned)fmt, locks); free(bits); }')
    vb = h.mock_class(header, 'IDirect3DVertexBuffer9', 'MockVB', {
        **U,
        'Lock': 'if (!data) data = (BYTE *)calloc(size ? size : 1, 1); *a2 = data; return D3D_OK;',
        'Unlock': 'return D3D_OK;',
    }, '  UINT size = 0; BYTE *data = nullptr;\n  ~MockVB() { free(data); }')
    ps = h.mock_class(header, 'IDirect3DPixelShader9', 'MockPS', {**U})
    caps = ('memset(@CAPS@, 0, sizeof(*@CAPS@)); @CAPS@->DeviceType = D3DDEVTYPE_HAL; '
            '@CAPS@->VertexShaderVersion = D3DVS_VERSION(3, 0); @CAPS@->PixelShaderVersion = D3DPS_VERSION(3, 0); '
            '@CAPS@->MaxSimultaneousTextures = 8; @CAPS@->MaxTextureBlendStages = 8; @CAPS@->MaxStreams = 16; '
            '@CAPS@->MaxTextureWidth = 8192; @CAPS@->MaxTextureHeight = 8192; '
            '@CAPS@->DevCaps = D3DDEVCAPS_HWTRANSFORMANDLIGHT | D3DDEVCAPS_PUREDEVICE; return D3D_OK;')
    present = ('presents++; if (presents <= 3 || presents % 50 == 0) mock_log("Present #%d", presents); return D3D_OK;')
    dev = h.mock_class(header, 'IDirect3DDevice9', 'MockDevice', {
        **U,
        'Release': 'LONG r = InterlockedDecrement(&ref); if (!r) { mock_log("device released after %d Present(s)", presents); delete this; } return r;',
        'GetDeviceCaps': caps.replace('@CAPS@', 'a0'),
        'TestCooperativeLevel': 'return D3D_OK;',
        'Reset': 'mock_log("Reset %ux%u windowed=%d", a0->BackBufferWidth, a0->BackBufferHeight, (int)a0->Windowed); return D3D_OK;',
        'SetRenderState': 'return D3D_OK;',
        'SetSamplerState': 'return D3D_OK;',
        'SetTextureStageState': 'return D3D_OK;',
        'SetViewport': 'return D3D_OK;',
        'SetFVF': 'return D3D_OK;',
        'SetTexture': 'return D3D_OK;',
        'SetStreamSource': 'return D3D_OK;',
        'SetPixelShader': 'return D3D_OK;',
        'SetPixelShaderConstantF': 'return D3D_OK;',
        'Clear': 'return D3D_OK;',
        'BeginScene': 'return D3D_OK;',
        'EndScene': 'return D3D_OK;',
        'DrawPrimitive': 'draws++; return D3D_OK;',
        'Present': present,
        'CreateTexture': 'auto *t = new MockTex(); t->w = a0; t->h = a1; t->fmt = a4; '
                         'mock_log("CreateTexture %ux%u fmt %u pool %u", a0, a1, (unsigned)a4, (unsigned)a5); *a6 = t; return D3D_OK;',
        'CreateVertexBuffer': 'auto *b = new MockVB(); b->size = a0; mock_log("CreateVertexBuffer %u bytes fvf 0x%lx", a0, (unsigned long)a2); '
                              '*a4 = b; return D3D_OK;',
        'CreatePixelShader': 'mock_log("CreatePixelShader ps_%lu_%lu", (unsigned long)((a0[0] >> 8) & 0xff), (unsigned long)(a0[0] & 0xff)); '
                             '*a1 = new MockPS(); return D3D_OK;',
    }, '  int presents = 0, draws = 0;')
    d3d9 = h.mock_class(header, 'IDirect3D9', 'MockD3D9', {
        **U,
        'GetAdapterCount': 'return 1;',
        'GetAdapterIdentifier': 'memset(a2, 0, sizeof(*a2)); strcpy(a2->Description, "Madeira mock d3d9"); return D3D_OK;',
        'GetAdapterDisplayMode': 'a1->Width = 640; a1->Height = 480; a1->RefreshRate = 60; a1->Format = D3DFMT_X8R8G8B8; return D3D_OK;',
        'CheckDeviceType': 'return D3D_OK;',
        'CheckDeviceFormat': 'return D3D_OK;',
        'GetDeviceCaps': caps.replace('@CAPS@', 'a2'),
        'GetAdapterMonitor': 'return nullptr;',
        'CreateDevice': 'mock_log("CreateDevice %ux%u fmt %u windowed=%d behavior 0x%lx", a4->BackBufferWidth, '
                        'a4->BackBufferHeight, (unsigned)a4->BackBufferFormat, (int)a4->Windowed, (unsigned long)a3); '
                        'if (GetEnvironmentVariableA("MOCK_D3D9_NODEVICE", nullptr, 0)) return D3DERR_NOTAVAILABLE; '
                        '*a5 = new MockDevice(); return D3D_OK;',
    })
    return r'''
#include <windows.h>
#include <d3d9.h>
#include <cstdio>
#include <cstdarg>
#include <cstdlib>
#include <cstring>
static void mock_log(const char *f, ...) {
  char line[1024]; int n = snprintf(line, sizeof line, "[mock-d3d9] ");
  va_list a; va_start(a, f); n += vsnprintf(line + n, sizeof line - n - 2, f, a); va_end(a);
  line[n++] = '\n'; DWORD w; WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, n, &w, nullptr);
}
''' + '\n'.join([tex, vb, ps, dev, d3d9]) + r'''
extern "C" IDirect3D9 *WINAPI Direct3DCreate9(UINT sdk) {
  mock_log("Direct3DCreate9 %u", sdk);
  if (GetEnvironmentVariableA("MOCK_D3D9_FAIL", nullptr, 0)) { mock_log("Direct3DCreate9 -> NULL (MOCK_D3D9_FAIL)"); return nullptr; }
  return new MockD3D9();
}
'''


PROGRAM = r'''
#include <windows.h>
#include <ddraw.h>
#include <stdio.h>
#include <stdarg.h>
static void out(const char *f, ...) {
  char line[1024]; va_list a; va_start(a, f); int n = vsnprintf(line, sizeof line - 1, f, a); va_end(a);
  line[n++] = '\n'; DWORD w; WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, n, &w, NULL);
}
static void pump(DWORD ms) {
  DWORD end = GetTickCount() + ms; MSG m;
  while ((LONG)(end - GetTickCount()) > 0) {
    while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageA(&m); }
    Sleep(10);
  }
}
int main(void) {
  HMODULE dd = LoadLibraryA("ddraw.dll");
  char path[MAX_PATH] = ""; GetModuleFileNameA(dd, path, sizeof path);
  out("[app] ddraw.dll %s (%s)", path, GetProcAddress(dd, "GameHandlesClose") ? "cnc-ddraw" : "not cnc-ddraw");
  HRESULT (WINAPI *create)(GUID *, IDirectDraw **, IUnknown *) = (void *)GetProcAddress(dd, "DirectDrawCreate");
  IDirectDraw *ddraw = NULL;
  HRESULT hr = create ? create(NULL, &ddraw, NULL) : E_FAIL;
  out("[app] DirectDrawCreate hr=0x%lx", (unsigned long)hr);
  if (FAILED(hr)) return 1;
  WNDCLASSA wc = {0}; wc.lpfnWndProc = DefWindowProcA; wc.lpszClassName = "ddsmoke"; RegisterClassA(&wc);
  HWND wnd = CreateWindowExA(WS_EX_TOPMOST, "ddsmoke", "ddraw", WS_POPUP | WS_VISIBLE, 0, 0, 640, 480, NULL, NULL, NULL, NULL);
  out("[app] SetCooperativeLevel hr=0x%lx", (unsigned long)IDirectDraw_SetCooperativeLevel(ddraw, wnd, DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN));
  out("[app] SetDisplayMode hr=0x%lx", (unsigned long)IDirectDraw_SetDisplayMode(ddraw, 640, 480, 8));
  DDSURFACEDESC sd = {0}; sd.dwSize = sizeof sd; sd.dwFlags = DDSD_CAPS | DDSD_BACKBUFFERCOUNT;
  sd.ddsCaps.dwCaps = DDSCAPS_PRIMARYSURFACE | DDSCAPS_FLIP | DDSCAPS_COMPLEX; sd.dwBackBufferCount = 1;
  IDirectDrawSurface *prim = NULL, *back = NULL;
  hr = IDirectDraw_CreateSurface(ddraw, &sd, &prim, NULL);
  out("[app] CreateSurface(primary) hr=0x%lx", (unsigned long)hr);
  if (FAILED(hr)) return 1;
  DDSCAPS bc = {DDSCAPS_BACKBUFFER};
  out("[app] GetAttachedSurface hr=0x%lx", (unsigned long)IDirectDrawSurface_GetAttachedSurface(prim, &bc, &back));
  PALETTEENTRY pal[256]; for (int i = 0; i < 256; i++) { pal[i].peRed = i; pal[i].peGreen = 255 - i; pal[i].peBlue = i / 2; pal[i].peFlags = 0; }
  IDirectDrawPalette *p = NULL;
  out("[app] CreatePalette hr=0x%lx", (unsigned long)IDirectDraw_CreatePalette(ddraw, DDPCAPS_8BIT | DDPCAPS_ALLOW256, pal, &p, NULL));
  out("[app] SetPalette hr=0x%lx", (unsigned long)IDirectDrawSurface_SetPalette(prim, p));
  for (int frame = 0; frame < 30; frame++) {
    DDSURFACEDESC ld = {0}; ld.dwSize = sizeof ld;
    hr = IDirectDrawSurface_Lock(back, NULL, &ld, DDLOCK_WAIT, NULL);
    if (frame == 0) out("[app] Lock hr=0x%lx %lux%lu pitch %ld", (unsigned long)hr, ld.dwWidth, ld.dwHeight, ld.lPitch);
    if (SUCCEEDED(hr)) {
      for (DWORD y = 0; y < ld.dwHeight; y++) memset((BYTE *)ld.lpSurface + y * ld.lPitch, (int)((y + frame) & 0xff), ld.dwWidth);
      IDirectDrawSurface_Unlock(back, NULL);
    }
    hr = IDirectDrawSurface_Flip(prim, NULL, DDFLIP_WAIT);
    if (frame == 0) out("[app] Flip hr=0x%lx", (unsigned long)hr);
    pump(30);
  }
  pump(300);
  IDirectDrawPalette_Release(p);
  IDirectDrawSurface_Release(prim);
  IDirectDraw_RestoreDisplayMode(ddraw);
  IDirectDraw_Release(ddraw);
  DestroyWindow(wnd);
  out("[app] done");
  return 0;
}
'''


def section(ini: str, name: str) -> str:
    m = re.search(r'^\[' + re.escape(name) + r'\]\r?\n(.*?)(?=^\[|\Z)', ini, re.M | re.S)
    return m.group(1) if m else ''


def setting(body: str, key: str) -> str | None:
    m = re.search(r'^' + re.escape(key) + r'=(.*?)\r?$', body, re.M)
    return m.group(1) if m else None


def part1(work: Path, out: Path) -> bool:
    env = dict(os.environ, TC=str(TC), DEST=str(out), OUT_DIR=str(work / 'obj'))
    b = run([str(ROOT / 'build/ddraw/build.sh')], env=env)
    ok = b.returncode == 0 and (out / 'ddraw.dll').exists() and (out / 'ddraw.ini').exists()
    check(ok, 'build/ddraw/build.sh builds ddraw.dll and ddraw.ini (no Microsoft SDK headers in the tree)')
    if not ok:
        print(b.stdout[-3000:] + b.stderr[-3000:])
        return False
    src = ROOT / 'third_party/cnc-ddraw'
    check(not (src / 'inc/ddraw.h').exists() and not (src / 'inc/d3dcaps.h').exists(),
          'upstream\'s inc/ddraw.h and inc/d3dcaps.h (Microsoft, all rights reserved) are not in the tree')
    check(not (src / 'inc/git.h').exists(), 'the build writes nothing into third_party (git.h goes to the object directory)')

    dll = (out / 'ddraw.dll').read_bytes()
    check(dll[:2] == b'MZ' and b'Wine builtin DLL' not in dll[:0x80],
          'ddraw.dll is a native DLL, not builtin-marked (Wine must load it as native from syswow64)')
    dump = run([str(TC / 'llvm-objdump'), '-p', str(out / 'ddraw.dll')]).stdout
    check('file format coff-i386' in run([str(TC / 'llvm-objdump'), '-f', str(out / 'ddraw.dll')]).stdout,
          'ddraw.dll is a 32-bit (i386) DLL')
    exports = set(re.findall(r'^\s+\d+\s+0x[0-9a-f]+\s+(\w+)', dump, re.M))
    want = {'DirectDrawCreate', 'DirectDrawCreateEx', 'DirectDrawCreateClipper', 'DirectDrawEnumerateA',
            'DirectDrawEnumerateExA'}
    check(want <= exports, f'DirectDraw exports present ({len(exports)} exports)')
    imports = {x.lower() for x in re.findall(r'DLL Name: (\S+)', dump)}
    mods = json.loads((ROOT / 'compat/wine-modules.json').read_text())
    i386 = {m.lower() if '.' in m else m.lower() + '.dll' for m in mods.get('modules', [])}
    if i386:
        missing = sorted(x for x in imports if x not in i386 and not x.startswith('api-ms-'))
        check(not missing, f'every import is a module the i386 Wine farm has ({sorted(imports)}; missing: {missing})')
    check(not any(x.startswith(('opengl32', 'vulkan', 'd3d9', 'd3d11', 'dxgi', 'wined3d')) for x in imports),
          'no static import of OpenGL, Vulkan or Direct3D: d3d9.dll is loaded at run time (DXMT on Madeira)')

    raw = (out / 'ddraw.ini').read_bytes()
    check(b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''), 'ddraw.ini has CRLF line endings')
    ini = raw.decode('utf-8')
    dd = section(ini, 'ddraw')
    check(setting(dd, 'renderer') == 'direct3d9', '[ddraw] renderer=direct3d9 (auto never picks Direct3D 9 under Wine)')
    for k, v in (('fullscreen', 'true'), ('maintas', 'true'), ('singlecpu', 'false'), ('no_compat_warning', 'true')):
        check(setting(dd, k) == v, f'[ddraw] {k}={v}')
    sections = re.findall(r'^\[([^\]]+)\]', ini, re.M)
    check(len(sections) > 250 and 'ddraw' in sections, f'cnc-ddraw\'s per-game sections are kept ({len(sections)} sections)')
    check(not re.search(r'^renderer=opengl', ini, re.M), 'no section asks for OpenGL (there is none on iOS)')
    check(len(re.findall(r'^renderer=gdi', ini, re.M)) >= 40, 'per-game renderer=gdi entries are kept')
    upstream = (src / 'src/config.c').read_text(encoding='utf-8')
    check('[ddraw]' in upstream and 'cfg_create_ini' in upstream, 'the ini is derived from cnc-ddraw\'s own cfg_create_ini')
    # Equivalence with Microsoft's headers (optional: CNC_DDRAW_UPSTREAM=<a cnc-ddraw checkout>, whose inc/
    # still has ddraw.h and d3dcaps.h). Every object file is compiled twice -- with madeira/'s shims over the
    # toolchain headers, and with upstream's SDK headers -- and the disassembly must match: same structure
    # layouts, same constants, same code.
    up = Path(os.environ.get('CNC_DDRAW_UPSTREAM', '/nonexistent'))
    if (up / 'inc/ddraw.h').exists() and (up / 'inc/d3dcaps.h').exists():
        ms = work / 'ms-inc'
        ms.mkdir()
        for h in ('ddraw.h', 'd3dcaps.h'):
            shutil.copy2(up / 'inc' / h, ms / h)
        shutil.copy2(src / 'madeira/madeira_log.h', ms / 'madeira_log.h')
        gitinc = work / 'obj/inc'
        differ = []
        files = sorted(src.glob('src/*.c')) + sorted(src.glob('src/*/*.c'))
        for c in files:
            dis = []
            for inc in ([f'-I{src / "madeira"}', f'-I{src / "inc"}'], [f'-I{ms}', f'-I{src / "inc"}']):
                o = work / 'eq.o'
                r = run([str(CC), *inc, f'-I{gitinc}', '-O2', '-std=c99', '-w', '-c', str(c), '-o', str(o)])
                if r.returncode:
                    break   # a compile error counts as a difference
                d = run([str(TC / 'llvm-objdump'), '-d', '-r', '--no-show-raw-insn', str(o)]).stdout
                dis.append('\n'.join(d.splitlines()[2:]))
            if len(dis) != 2 or dis[0] != dis[1]:
                differ.append(str(c.relative_to(src)))
        check(not differ, f'all {len(files)} source files compile to the same code with Microsoft\'s ddraw.h/d3dcaps.h '
                          f'as with madeira/\'s shims (differ: {differ})')
    else:
        print('INFO: set CNC_DDRAW_UPSTREAM to a cnc-ddraw checkout to compare against Microsoft\'s headers')

    # How the app turns it on (WineProcessBridge.m, compat data, settings catalogue).
    bridge = (ROOT / 'app/Madeira/WineProcessBridge.m').read_text()
    check('getenv("MADEIRA_DDRAW")' in bridge and 'cnc-ddraw/ddraw.dll' in bridge and 'CNC_DDRAW_CONFIG_FILE' in bridge
          and '"ddraw=n,b"' in bridge.replace('@"ddraw=n,b"', '"ddraw=n,b"'),
          'WineProcessBridge links syswow64\\ddraw.dll, sets CNC_DDRAW_CONFIG_FILE and ddraw=n,b for MADEIRA_DDRAW=cnc')
    i = bridge.index('madeira_link_syswow64(fm, prefix, bundlePath);\n                madeira_apply_cnc_ddraw') \
        if 'madeira_link_syswow64(fm, prefix, bundlePath);\n                madeira_apply_cnc_ddraw' in bridge else -1
    check(i >= 0, 'it runs right after the i386 farm is relinked, so a launch without the switch gets Wine\'s ddraw back')
    comp = json.loads((ROOT / 'app/Madeira/compat.json').read_text())
    r = comp['recipes'].get('cnc-ddraw', {})
    check(r.get('env', {}).get('MADEIRA_DDRAW') == 'cnc' and r.get('dll_overrides', {}).get('ddraw') == 'n,b',
          'recipe cnc-ddraw sets MADEIRA_DDRAW=cnc and ddraw=n,b')
    dep = comp['dependencies'].get('cnc-ddraw', {})
    check(dep.get('support') == 'builtin' and 'cnc-ddraw' in dep.get('recipes', []),
          'dependency cnc-ddraw is builtin and applies the recipe')
    check(not any('cnc-ddraw' in (x.get('recipes') or []) for x in comp.get('rules', [])) and
          not any('cnc-ddraw' in (x.get('recipes') or []) for x in comp.get('remedies', {}).values()),
          'opt-in only: no rule or automatic remedy applies it to every DirectDraw game')
    src_recipes = json.loads((ROOT / 'compat/recipes.json').read_text())['recipes']
    check(src_recipes.get('cnc-ddraw') == r, 'compat/recipes.json and the bundled compat.json agree')
    cat = (ROOT / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
    check('key: "env.MADEIRA_DDRAW"' in cat, 'Settings lists env.MADEIRA_DDRAW')
    pbx = (ROOT / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
    check('path = "cnc-ddraw"' in pbx and 'cnc-ddraw in Resources' in pbx, 'the app bundles the cnc-ddraw folder')
    check('build/ddraw/build.sh' in (ROOT / 'build/wine-i386/build.sh').read_text(), 'the i386 farm build builds it')
    return True


def part2(work: Path, out: Path) -> None:
    winebuild = Path(WINEBUILD)
    if not (HOST_WINE / 'bin/wine').exists() or not (HOST_WINE / 'lib/wine/i386-windows').is_dir() or not winebuild.exists():
        print('SKIP: part 2 (set HOST_WINE to an installed WoW64 Wine and WINEBUILD)')
        return
    h = d3d8_harness()
    app = work / 'app'
    app.mkdir()
    (work / 'mock.cpp').write_text(mock_source(h, D3D9_H.read_text()))
    (work / 'mock.def').write_text('LIBRARY d3d9.dll\nEXPORTS\n  Direct3DCreate9\n')
    m = run([str(CXX), '-std=c++17', '-O1', '-shared', '-o', str(work / 'd3d9.dll'), str(work / 'mock.cpp'),
             '-static', '-Wl,--enable-stdcall-fixup', str(work / 'mock.def')])
    check(m.returncode == 0, 'the recording Direct3D 9 builds')
    if m.returncode:
        print(m.stderr[-3000:])
        return
    check(run([str(winebuild), '--builtin', str(work / 'd3d9.dll')]).returncode == 0, 'it is builtin-marked like DXMT\'s d3d9')
    (work / 'app.c').write_text(PROGRAM)
    p = run([str(CC), '-O1', '-o', str(app / 'ddsmoke.exe'), str(work / 'app.c'), '-luuid'])
    check(p.returncode == 0, 'the 32-bit DirectDraw test program builds')
    if p.returncode:
        print(p.stderr[-3000:])
        return

    def link_or_copy(s, d):
        try:
            os.link(s, d)
        except OSError:
            shutil.copy2(s, d)
    tree = work / 'wine'
    for sub in ('bin', 'lib/wine'):
        shutil.copytree(HOST_WINE / sub, tree / sub, symlinks=True, copy_function=link_or_copy)
    if (HOST_WINE / 'share').is_dir():
        (tree / 'share').symlink_to(HOST_WINE / 'share')
    farm = tree / 'lib/wine/i386-windows'
    (farm / 'd3d9.dll').unlink(missing_ok=True)
    shutil.copy2(work / 'd3d9.dll', farm / 'd3d9.dll')
    wine = tree / 'bin/wine'

    prefix = work / 'prefix'
    base = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all', DISPLAY='', WAYLAND_DISPLAY='',
                WINEDLLOVERRIDES='mscoree,mshtml=')
    base.pop('WINEDLLPATH', None)
    src_prefix = os.environ.get('HOST_WINEPREFIX')
    if src_prefix and Path(src_prefix).is_dir():
        shutil.copytree(src_prefix, prefix, symlinks=True)
    run([str(wine), 'wineboot', '-u'], env=base, timeout=900)
    run([str(tree / 'bin/wineserver'), '-w'], env=base, timeout=300)
    r = run([str(wine), 'reg', 'add', r'HKCU\Software\Wine\Drivers', '/v', 'Graphics', '/d', 'null', '/f'], env=base, timeout=300)
    check(r.returncode == 0, 'prefix uses Wine\'s null display driver (headless)')
    run([str(tree / 'bin/wineserver'), '-w'], env=base, timeout=300)

    syswow = prefix / 'drive_c/windows/syswow64'
    (syswow / 'ddraw.dll').unlink(missing_ok=True)
    (syswow / 'ddraw.dll').symlink_to(out / 'ddraw.dll')   # as WineProcessBridge links it
    cfgdir = prefix / 'drive_c/ProgramData/cnc-ddraw'
    cfgdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / 'ddraw.ini', cfgdir / 'ddraw.ini')
    cnc = dict(base, WINEDLLOVERRIDES='mscoree,mshtml=;ddraw=n,b', CNC_DDRAW_CONFIG_FILE=r'C:\ProgramData\cnc-ddraw\ddraw.ini')

    def session(env, label):
        r = run([str(wine), str(app / 'ddsmoke.exe')], env=env, cwd=app, timeout=600)
        log = r.stdout + r.stderr
        print(f'--- {label} (filtered) ---')
        print('\n'.join(l for l in log.splitlines() if l.startswith(('[app]', '[mock-d3d9]')) or '[cnc-ddraw]' in l))
        print('---')
        if os.environ.get('VERBOSE') or r.returncode:
            print(f'exit {r.returncode}\n{log[-4000:]}')
        return r, log

    r, log = session(cnc, 'cnc-ddraw, renderer=direct3d9')
    check('[app] ddraw.dll C:\\windows\\system32\\ddraw.dll (cnc-ddraw)' in log,
          'cnc-ddraw is the ddraw.dll a 32-bit process loads from the system directory')
    check('[app] DirectDrawCreate hr=0x0' in log and '[app] SetDisplayMode hr=0x0' in log
          and '[app] CreateSurface(primary) hr=0x0' in log, 'DirectDrawCreate, SetDisplayMode 640x480x8 and the primary surface succeed')
    check('[app] Lock hr=0x0 640x480' in log and '[app] Flip hr=0x0' in log, 'the back buffer locks at 640x480 and flips')
    check(re.search(r'\[cnc-ddraw\] renderer direct3d9 \(ddraw\.ini renderer=direct3d9, C:\\ProgramData\\cnc-ddraw\\ddraw\.ini\)', log)
          is not None, 'the launch log names the renderer and the ini it came from')
    check('[mock-d3d9] Direct3DCreate9 32' in log and re.search(r'\[mock-d3d9\] CreateDevice \d+x\d+ fmt \d+ windowed=', log) is not None,
          'Direct3D 9 is created and gets CreateDevice')
    check(re.search(r'\[cnc-ddraw\] Direct3D 9 device \d+x\d+ windowed=\d', log) is not None, 'cnc-ddraw logs its Direct3D 9 device')
    check('[mock-d3d9] CreateTexture 1024x1024 fmt 50 pool 1' in log,
          'the 8-bit surface goes up as a 1024x1024 L8 managed texture (palette lookup in a ps_2_0 shader)')
    check('[mock-d3d9] CreateTexture 256x256 fmt 21 pool 1' in log or '[mock-d3d9] CreateTexture 256x256' in log,
          'the palette goes up as a texture')
    check('[mock-d3d9] CreatePixelShader ps_2_0' in log, 'the palette shader is ps_2_0')
    check('[mock-d3d9] CreateVertexBuffer' in log, 'a vertex buffer for the full-screen quad')
    check('[mock-d3d9] Present #3' in log, 'frames reach Present')
    check('falling back to GDI' not in log, 'no fallback to GDI')
    check('[app] done' in log and r.returncode == 0, 'the program ran to the end')
    unexpected = sorted({l.split('unexpected ')[1] for l in log.splitlines() if 'unexpected ' in l})
    print('INFO: Direct3D 9 methods called that the mock does not model: ' + (', '.join(unexpected) or 'none'))

    _, log = session(dict(cnc, WINEDLLOVERRIDES='mscoree,mshtml='), 'no ddraw=n,b override')
    check('(not cnc-ddraw)' in log and '[cnc-ddraw]' not in log,
          'without ddraw=n,b Wine prefers its builtin ddraw over the native one in syswow64 (the recipe sets the override)')

    auto_ini = (out / 'ddraw.ini').read_bytes().decode('utf-8').replace('\r\nrenderer=direct3d9\r\n', '\r\nrenderer=auto\r\n', 1)
    (cfgdir / 'auto.ini').write_text(auto_ini, encoding='utf-8', newline='')
    _, log = session(dict(cnc, CNC_DDRAW_CONFIG_FILE=r'C:\ProgramData\cnc-ddraw\auto.ini'), 'renderer=auto')
    m = re.search(r'\[cnc-ddraw\] renderer (\w+) \(ddraw\.ini renderer=auto', log)
    check(m is not None and m.group(1) in ('opengl', 'gdi') and '[mock-d3d9] CreateDevice' not in log,
          f'renderer=auto never picks Direct3D 9 under Wine (got {m.group(1) if m else "?"}: OpenGL when opengl32.dll '
          'loads, else GDI) -- why the shipped ini says direct3d9')

    _, log = session(dict(cnc, MOCK_D3D9_FAIL='1'), 'Direct3DCreate9 fails')
    check('[cnc-ddraw] Direct3DCreate9 returned NULL' in log and '[cnc-ddraw] Direct3D 9 renderer could not start, falling back to GDI' in log,
          'when Direct3D 9 is missing cnc-ddraw says so and falls back to GDI')
    _, log = session(dict(cnc, MOCK_D3D9_NODEVICE='1'), 'CreateDevice fails')
    check('[cnc-ddraw] Direct3D 9 CreateDevice failed (hr 0x8876086a)' in log and 'falling back to GDI' in log,
          'a CreateDevice failure is logged with its HRESULT before the GDI fallback')


def main() -> int:
    if not CC.exists():
        print('SKIP: set LLVM_MINGW (llvm-mingw root)')
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        out = work / 'cnc-ddraw'
        out.mkdir()
        if part1(work, out):
            part2(work, out)
    print(f'{"PASS" if not failures else "FAIL"}: cnc-ddraw ({failures} failure(s))')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
