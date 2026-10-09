#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""d3d8.dll (third_party/d3d8to9) loaded and driven by a 32-bit program under a
host WoW64 Wine, with a recording Direct3D 9 underneath.

What it proves: the DLL build/d3d8/build.sh produces (the same script the i386
farm build calls, builtin-marked) loads in a 32-bit Wine process, resolves its
imports, and turns the Direct3D 8 calls of a small program into the expected
Direct3D 9 calls: Direct3DCreate8 -> Direct3DCreate9, CreateDevice, Clear /
BeginScene / EndScene / Present, a vertex shader created from a D3D8
declaration + vs.1.1 function (the D3D9 runtime receives a vs_1_1 with one dcl
per declared input, and the declaration constant is loaded at SetVertexShader),
a ps.1.1 pixel shader, the D3D8 getters returning the D3D8 tokens, a vs_2_0
blob refused with a "[d3d8to9]" line, and Direct3DCreate8 returning NULL with a
"[d3d8to9]" line when Direct3D 9 is missing.

What it does not prove: anything about DXMT or Metal. The Direct3D 9 here is a
mock (generated from the toolchain's d3d9.h) that records calls; DXMT's
acceptance of the translated shaders is check-d3d8-shader.py, and rendering
needs an iPad (docs/D3D8.md).

How the DLLs are found is the way Madeira finds them: the test builds a private
copy of an installed Wine whose lib/wine/i386-windows holds our d3d8.dll and a
builtin-marked d3d9.dll in place of Wine's (Madeira's i386 farm skips Wine's
d3d8 and installs this one; DXMT's d3d9 is builtin-marked the same way). Note:
a builtin-marked DLL dropped next to the .exe with WINEDLLOVERRIDES=d3d8=n is
refused by the loader (c0000135), so placement in the farm is what matters.

Needs: llvm-mingw (LLVM_MINGW=<dir with bin/>), an installed Wine built with
--enable-archs=i386,x86_64 (HOST_WINE=<install prefix with bin/wine and
lib/wine/i386-windows>, e.g. `make install-lib prefix=...`), and winebuild
(WINEBUILD=<path>, else from PATH). Optional HOST_WINEPREFIX reuses a prefix.
Not in CI: it needs a Wine build. Prints SKIP and exits 0 without them.
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
LLVM_MINGW = Path(os.environ.get('LLVM_MINGW', '/nonexistent'))
HOST_WINE = Path(os.environ.get('HOST_WINE', '/nonexistent'))
WINEBUILD = os.environ.get('WINEBUILD') or shutil.which('winebuild') or '/nonexistent'
TC = LLVM_MINGW / 'bin'
CXX = TC / 'i686-w64-mingw32-clang++'
D3D9_H = LLVM_MINGW / 'generic-w64-mingw32/include/d3d9.h'

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures += 1


def interface_methods(header: str, name: str) -> list[str]:
    """The interface's methods, one per entry, declarations joined across lines."""
    start = header.index(f'DECLARE_INTERFACE_IID_({name},')
    body = header[header.index('{', start) + 1:header.index('};', start)]
    body = re.sub(r'/\*.*?\*/', ' ', body, flags=re.S)
    body = ' '.join(body.split())
    return [d.strip() + ' PURE;' for d in body.split('PURE;') if d.strip().startswith('STDMETHOD')]


def method_name(decl: str) -> tuple[str, str]:
    m = re.match(r'STDMETHOD_\(\s*([^,]+?)\s*,\s*(\w+)\)', decl)
    if m:
        return m.group(2), m.group(1)
    m = re.match(r'STDMETHOD\((\w+)\)', decl)
    return m.group(1), 'HRESULT'


def positional(decl: str) -> str:
    """The declaration with its parameters renamed a0, a1, ... so the hand-written
    bodies do not depend on the parameter names a given d3d9.h uses."""
    head, _, rest = decl.partition(')(')
    args = rest[:rest.rindex(')')]
    args = args.replace('THIS_', '').replace('THIS', '').strip()
    params = []
    for i, a in enumerate(x.strip() for x in args.split(',') if x.strip()):
        m = re.match(r'(.*?)(\w+)\s*$', a)
        params.append(f'{m.group(1).rstrip()} a{i}' if m and m.group(1).strip() else f'{a} a{i}')
    return f'{head})({", ".join(params)})'


def mock_class(header: str, iface: str, cls: str, custom: dict[str, str], extra: str = '') -> str:
    out = [f'struct {cls} : public {iface} {{', '  LONG ref = 1;', extra]
    for decl in interface_methods(header, iface):
        name, ret = method_name(decl)
        sig = positional(decl)
        if name in custom:
            out.append(f'  {sig} {{ {custom[name]} }}')
        else:
            default = {'HRESULT': 'return E_NOTIMPL;', 'void': 'return;'}.get(ret, f'return ({ret})0;')
            out.append(f'  {sig} {{ mock_log("unexpected {iface}::{name}"); {default} }}')
    out.append('};')
    return '\n'.join(out)


UNKNOWN = {
    'QueryInterface': 'if (a1) *a1 = nullptr; return E_NOINTERFACE;',
    'AddRef': 'return InterlockedIncrement(&ref);',
    'Release': 'LONG r = InterlockedDecrement(&ref); if (!r) delete this; return r;',
}


def mock_source(header: str) -> str:
    vs = mock_class(header, 'IDirect3DVertexShader9', 'MockVS', {
        **UNKNOWN,
        'GetFunction': 'UINT n = (UINT)(tokens.size() * 4); if (!a0) { *a1 = n; return D3D_OK; } '
                       'memcpy(a0, tokens.data(), n); *a1 = n; return D3D_OK;',
    }, '  std::vector<DWORD> tokens;')
    ps = mock_class(header, 'IDirect3DPixelShader9', 'MockPS', {
        **UNKNOWN,
        'GetFunction': 'UINT n = (UINT)(tokens.size() * 4); if (!a0) { *a1 = n; return D3D_OK; } '
                       'memcpy(a0, tokens.data(), n); *a1 = n; return D3D_OK;',
    }, '  std::vector<DWORD> tokens;')
    decl = mock_class(header, 'IDirect3DVertexDeclaration9', 'MockDecl', {**UNKNOWN})
    caps = lambda c: ('memset(pCaps, 0, sizeof(*pCaps)); pCaps->DeviceType = D3DDEVTYPE_HAL; '
            'pCaps->VertexShaderVersion = D3DVS_VERSION(1, 1); pCaps->PixelShaderVersion = D3DPS_VERSION(1, 4); '
            'pCaps->MaxSimultaneousTextures = 8; pCaps->MaxTextureBlendStages = 8; pCaps->MaxStreams = 16; '
            'pCaps->MaxVertexShaderConst = 96; pCaps->MaxPrimitiveCount = 0xFFFFF; pCaps->MaxVertexIndex = 0xFFFFF; '
            'pCaps->MaxTextureWidth = 4096; pCaps->MaxTextureHeight = 4096; return D3D_OK;').replace('pCaps', c)
    device = mock_class(header, 'IDirect3DDevice9', 'MockDevice', {
        **UNKNOWN,
        'GetDeviceCaps': caps('a0'),
        'SetRenderState': 'return D3D_OK;',
        'SetFVF': 'mock_log("SetFVF 0x%lx", (unsigned long)a0); return D3D_OK;',
        'Clear': 'mock_log("Clear count=%lu flags=0x%lx color=0x%08lx", (unsigned long)a0, (unsigned long)a2, (unsigned long)a3); return D3D_OK;',
        'BeginScene': 'mock_log("BeginScene"); return D3D_OK;',
        'EndScene': 'mock_log("EndScene"); return D3D_OK;',
        'Present': 'mock_log("Present"); return D3D_OK;',
        'CreateVertexShader': 'auto *s = new MockVS(); const DWORD *p = a0; '
                              'while (*p != 0x0000FFFF) s->tokens.push_back(*p++); s->tokens.push_back(*p); '
                              'std::string hex; char b[16]; for (DWORD t : s->tokens) { snprintf(b, sizeof b, " %08lx", (unsigned long)t); hex += b; } '
                              'mock_log("CreateVertexShader%s", hex.c_str()); *a1 = s; return D3D_OK;',
        'CreatePixelShader': 'auto *s = new MockPS(); const DWORD *p = a0; '
                             'while (*p != 0x0000FFFF) s->tokens.push_back(*p++); s->tokens.push_back(*p); '
                             'std::string hex; char b[16]; for (DWORD t : s->tokens) { snprintf(b, sizeof b, " %08lx", (unsigned long)t); hex += b; } '
                             'mock_log("CreatePixelShader%s", hex.c_str()); *a1 = s; return D3D_OK;',
        'CreateVertexDeclaration': 'int n = 0; while (a0[n].Stream != 0xFF) n++; '
                                   'mock_log("CreateVertexDeclaration elements=%d", n); *a1 = new MockDecl(); return D3D_OK;',
        'SetVertexShader': 'mock_log("SetVertexShader %s", a0 ? "shader" : "null"); return D3D_OK;',
        'SetPixelShader': 'mock_log("SetPixelShader %s", a0 ? "shader" : "null"); return D3D_OK;',
        'SetVertexDeclaration': 'return D3D_OK;',
        'SetVertexShaderConstantF': 'mock_log("SetVertexShaderConstantF c%u = %g %g %g %g", a0, '
                                    'a1[0], a1[1], a1[2], a1[3]); return D3D_OK;',
    })
    d3d9 = mock_class(header, 'IDirect3D9', 'MockD3D9', {
        **UNKNOWN,
        'RegisterSoftwareDevice': 'return D3D_OK;',
        'GetAdapterCount': 'return 1;',
        'GetAdapterModeCount': 'return a1 == D3DFMT_X8R8G8B8 ? 1 : 0;',
        'EnumAdapterModes': 'a3->Width = 640; a3->Height = 480; a3->RefreshRate = 60; a3->Format = D3DFMT_X8R8G8B8; return D3D_OK;',
        'GetAdapterDisplayMode': 'a1->Width = 640; a1->Height = 480; a1->RefreshRate = 60; a1->Format = D3DFMT_X8R8G8B8; return D3D_OK;',
        'GetAdapterIdentifier': 'memset(a2, 0, sizeof(*a2)); strcpy(a2->Description, "Madeira mock d3d9"); return D3D_OK;',
        'CheckDeviceType': 'return D3D_OK;',
        'CheckDeviceFormat': 'return D3D_OK;',
        'CheckDeviceMultiSampleType': 'return D3D_OK;',
        'CheckDepthStencilMatch': 'return D3D_OK;',
        'GetDeviceCaps': caps('a2'),
        'GetAdapterMonitor': 'return nullptr;',
        'CreateDevice': 'mock_log("CreateDevice %ux%u fmt %u windowed=%d", a4->BackBufferWidth, '
                        'a4->BackBufferHeight, (unsigned)a4->BackBufferFormat, '
                        '(int)a4->Windowed); *a5 = new MockDevice(); return D3D_OK;',
    })
    return r'''
#include <windows.h>
#include <d3d9.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <string>
#include <vector>
static void mock_log(const char *f, ...) {
  char line[4096]; int n = snprintf(line, sizeof line, "[mock-d3d9] ");
  va_list a; va_start(a, f); n += vsnprintf(line + n, sizeof line - n - 2, f, a); va_end(a);
  line[n++] = '\n'; DWORD w; WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, n, &w, nullptr);
}
''' + '\n'.join([vs, ps, decl, device, d3d9]) + r'''
extern "C" IDirect3D9 *WINAPI Direct3DCreate9(UINT sdk) {
  mock_log("Direct3DCreate9 %u", sdk);
  if (GetEnvironmentVariableA("MOCK_D3D9_FAIL", nullptr, 0)) { mock_log("Direct3DCreate9 -> NULL (MOCK_D3D9_FAIL)"); return nullptr; }
  return new MockD3D9();
}
'''


PROGRAM = r'''
#include <windows.h>
#include <d3d8.h>
#include <cstdio>
#include <cstring>
static void out(const char *f, ...) {
  char line[1024]; va_list a; va_start(a, f); int n = vsnprintf(line, sizeof line - 1, f, a); va_end(a);
  line[n++] = '\n'; DWORD w; WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, n, &w, nullptr);
}
int main() {
  IDirect3D8 *d3d = Direct3DCreate8(D3D_SDK_VERSION);
  if (GetEnvironmentVariableA("MOCK_D3D9_FAIL", nullptr, 0)) { out("[app] Direct3DCreate8 %s", d3d ? "object" : "NULL"); return 0; }
  if (!d3d) { out("[app] Direct3DCreate8 NULL"); return 1; }
  out("[app] adapters=%u", d3d->GetAdapterCount());
  D3DADAPTER_IDENTIFIER8 id; HRESULT hr = d3d->GetAdapterIdentifier(0, 0, &id);
  out("[app] identifier hr=0x%lx '%s'", (unsigned long)hr, id.Description);
  WNDCLASSA wc = {}; wc.lpfnWndProc = DefWindowProcA; wc.lpszClassName = "d3d8smoke"; RegisterClassA(&wc);
  HWND wnd = CreateWindowA("d3d8smoke", "d3d8", WS_OVERLAPPEDWINDOW, 0, 0, 640, 480, nullptr, nullptr, nullptr, nullptr);
  D3DPRESENT_PARAMETERS pp = {}; pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
  pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferWidth = 640; pp.BackBufferHeight = 480; pp.hDeviceWindow = wnd;
  IDirect3DDevice8 *dev = nullptr;
  hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
  out("[app] CreateDevice hr=0x%lx", (unsigned long)hr);
  if (FAILED(hr)) return 1;
  out("[app] Clear hr=0x%lx", (unsigned long)dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xff336699, 1.0f, 0));
  out("[app] BeginScene hr=0x%lx", (unsigned long)dev->BeginScene());
  out("[app] EndScene hr=0x%lx", (unsigned long)dev->EndScene());
  out("[app] Present hr=0x%lx", (unsigned long)dev->Present(nullptr, nullptr, nullptr, nullptr));

  float k[4] = {1.0f, 2.0f, 3.0f, 4.0f};
  DWORD decl[] = { D3DVSD_STREAM(0), D3DVSD_REG(0, D3DVSDT_FLOAT3), D3DVSD_REG(5, D3DVSDT_D3DCOLOR),
                   D3DVSD_CONST(10, 1), *(DWORD *)&k[0], *(DWORD *)&k[1], *(DWORD *)&k[2], *(DWORD *)&k[3],
                   D3DVSD_END() };
  DWORD vs[] = { 0xFFFE0101, 9, 0xC0010000, 0x90E40000, 0xA0E40000, 1, 0xD00F0000, 0x90E40005,
                 1, 0xC00E0000, 0xA0E4000A, 0x0000FFFF };
  DWORD vh = 0;
  hr = dev->CreateVertexShader(decl, vs, &vh, 0);
  out("[app] CreateVertexShader hr=0x%lx handle=%s", (unsigned long)hr, vh ? "set" : "none");
  out("[app] SetVertexShader hr=0x%lx", (unsigned long)dev->SetVertexShader(vh));
  DWORD buf[64], size = sizeof buf;
  hr = dev->GetVertexShaderFunction(vh, buf, &size);
  out("[app] GetVertexShaderFunction hr=0x%lx same=%d", (unsigned long)hr, size == sizeof vs && !memcmp(buf, vs, sizeof vs));
  size = 0; hr = dev->GetVertexShaderDeclaration(vh, nullptr, &size);
  DWORD size2 = sizeof buf; HRESULT hr2 = dev->GetVertexShaderDeclaration(vh, buf, &size2);
  out("[app] GetVertexShaderDeclaration hr=0x%lx size=%lu same=%d", (unsigned long)hr2, (unsigned long)size,
      size == sizeof decl && size2 == sizeof decl && !memcmp(buf, decl, sizeof decl));

  DWORD ps[] = { 0xFFFF0101, 66, 0xB00F0000, 2, 0x800F0000, 0xB0E40000, 0xA1E40000, 0x0000FFFF };
  DWORD ph = 0;
  hr = dev->CreatePixelShader(ps, &ph);
  out("[app] CreatePixelShader hr=0x%lx", (unsigned long)hr);
  size = sizeof buf; hr = dev->GetPixelShaderFunction(ph, buf, &size);
  out("[app] GetPixelShaderFunction hr=0x%lx same=%d", (unsigned long)hr, size == sizeof ps && !memcmp(buf, ps, sizeof ps));

  DWORD bad[] = { 0xFFFE0200, 1, 0xC00F0000, 0x90E40000, 0x0000FFFF };
  DWORD bh = 0;
  hr = dev->CreateVertexShader(decl, bad, &bh, 0);
  out("[app] CreateVertexShader(vs_2_0) hr=0x%lx", (unsigned long)hr);

  dev->DeleteVertexShader(vh); dev->DeletePixelShader(ph);
  out("[app] device release=%lu", (unsigned long)dev->Release());
  out("[app] d3d release=%lu", (unsigned long)d3d->Release());
  DestroyWindow(wnd);
  out("[app] done");
  return 0;
}
'''


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    winebuild = Path(WINEBUILD)
    if not CXX.exists() or not (HOST_WINE / 'bin/wine').exists() or not (HOST_WINE / 'lib/wine/i386-windows').is_dir() \
            or not winebuild.exists():
        print('SKIP: set LLVM_MINGW (llvm-mingw root), HOST_WINE (installed WoW64 Wine prefix) and WINEBUILD')
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        app = work / 'app'
        app.mkdir()
        env = dict(os.environ, TC=str(TC), DEST=str(app), OUT_DIR=str(work / 'd3d8-obj'), WINEBUILD=str(winebuild))
        b = run([str(ROOT / 'build/d3d8/build.sh')], env=env)
        check(b.returncode == 0 and (app / 'd3d8.dll').exists(), 'build/d3d8/build.sh builds the i386 d3d8.dll')
        if b.returncode:
            print(b.stdout + b.stderr)
            return 1
        dll = (app / 'd3d8.dll').read_bytes()
        check(b'Wine builtin DLL' in dll[:0x80], 'the DLL is marked as a Wine builtin, like DXMT\'s d3d9')
        dump = run([str(TC / 'llvm-objdump'), '-p', str(app / 'd3d8.dll')]).stdout
        exports = set(re.findall(r'^\s+\d+\s+0x[0-9a-f]+\s+(\w+)', dump, re.M))
        check({'Direct3DCreate8', 'ValidatePixelShader', 'ValidateVertexShader', 'DebugSetMute',
               'D3D8GetSWInfo'} <= exports, f'the five d3d8.dll exports Windows and Wine have ({sorted(exports)})')
        imports = {x.lower() for x in re.findall(r'DLL Name: (\S+)', dump)}
        check('d3d9.dll' in imports and not any(x.startswith(('vulkan', 'd3d11', 'dxgi', 'wined3d')) for x in imports),
              f'it imports d3d9.dll and nothing Vulkan or wined3d ({sorted(imports)})')

        header = D3D9_H.read_text()
        (work / 'mock.cpp').write_text(mock_source(header))
        (work / 'mock.def').write_text('LIBRARY d3d9.dll\nEXPORTS\n  Direct3DCreate9\n')
        m = run([str(CXX), '-std=c++17', '-O1', '-shared', '-o', str(app / 'd3d9.dll'), str(work / 'mock.cpp'),
                 '-static', '-Wl,--enable-stdcall-fixup', str(work / 'mock.def')])
        check(m.returncode == 0, 'the recording Direct3D 9 (generated from d3d9.h) builds')
        if m.returncode:
            print(m.stderr[-3000:])
            return 1
        (work / 'app.cpp').write_text(PROGRAM)
        p = run([str(CXX), '-std=c++17', '-O1', '-o', str(app / 'd3d8-smoke.exe'), str(work / 'app.cpp'),
                 '-static', '-ld3d8'])
        check(p.returncode == 0, 'the Direct3D 8 test program builds against the d3d8 import library')
        if p.returncode:
            print(p.stderr[-3000:])
            return 1

        b9 = run([str(winebuild), '--builtin', str(app / 'd3d9.dll')])
        check(b9.returncode == 0, 'the recording d3d9.dll is builtin-marked like DXMT\'s')

        # A private copy of the installed Wine (hard links where the filesystem allows):
        # Wine takes its DLL directory from the real path of ntdll.so, so symlinks
        # would lead it back to HOST_WINE. Only the two DLLs under test differ.
        def link_or_copy(src, dst):
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        tree = work / 'wine'
        for sub in ('bin', 'lib/wine'):
            shutil.copytree(HOST_WINE / sub, tree / sub, symlinks=True, copy_function=link_or_copy)
        if (HOST_WINE / 'share').is_dir():
            (tree / 'share').symlink_to(HOST_WINE / 'share')
        farm = tree / 'lib/wine/i386-windows'
        for name in ('d3d8.dll', 'd3d9.dll'):
            (farm / name).unlink(missing_ok=True)  # drops our link only, never HOST_WINE's file
            shutil.move(app / name, farm / name)
        wine = tree / 'bin/wine'

        prefix = Path(os.environ.get('HOST_WINEPREFIX') or (work / 'prefix'))
        wenv = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=',
                    DISPLAY='')
        wenv.pop('WINEDLLPATH', None)
        r = run([str(wine), str(app / 'd3d8-smoke.exe')], env=wenv, cwd=app, timeout=600)
        log = r.stdout + r.stderr
        print('--- session (stdout+stderr, filtered) ---')
        print('\n'.join(l for l in log.splitlines() if '[app]' in l or '[mock-d3d9]' in l or '[d3d8to9]' in l))
        print('---')
        if os.environ.get('VERBOSE') or r.returncode:
            print(f'exit {r.returncode}\n{log[-4000:]}')
        check('[d3d8to9] Direct3DCreate8(220): translating Direct3D 8' in log,
              'Madeira\'s d3d8.dll, found as a builtin in the i386 farm, is the one that ran (not Wine\'s wined3d d3d8)')
        check('[mock-d3d9] Direct3DCreate9 32' in log, 'Direct3DCreate8 created a Direct3D 9 object')
        check("identifier hr=0x0 'Madeira mock d3d9'" in log, 'adapter identity comes from the Direct3D 9 below')
        check('[mock-d3d9] CreateDevice 640x480 fmt 22 windowed=1' in log and '[app] CreateDevice hr=0x0' in log,
              'CreateDevice reached Direct3D 9 with the presentation parameters and succeeded')
        check(re.search(r'\[d3d8to9\] CreateDevice 640x480 fmt 22 windowed behavior 0x20 -> hr 0x0', log) is not None,
              'the launch-log line for the device is printed')
        for call in ('Clear', 'BeginScene', 'EndScene', 'Present'):
            check(f'[app] {call} hr=0x0' in log and f'[mock-d3d9] {call}' in log, f'{call} reaches Direct3D 9')
        vsline = next((l for l in log.splitlines() if l.startswith('[mock-d3d9] CreateVertexShader')), '')
        check(vsline.split()[2:12] == ['fffe0101', '0000001f', '80000000', '900f0000', '0000001f', '8000000a',
                                         '900f0005', '00000009', 'c0010000', '90e40000'],
              'the D3D9 runtime gets vs_1_1 with dcl_position v0 and dcl_color v5 before the D3D8 instructions')
        check('[mock-d3d9] CreateVertexDeclaration elements=2' in log, 'the D3D8 declaration became a 2-element D3D9 declaration')
        check('[mock-d3d9] SetVertexShaderConstantF c10 = 1 2 3 4' in log,
              'the declaration constant (D3DVSD_CONST) is loaded at SetVertexShader')
        check('GetVertexShaderFunction hr=0x0 same=1' in log, 'GetVertexShaderFunction returns the D3D8 tokens')
        check('GetVertexShaderDeclaration hr=0x0 size=36 same=1' in log,
              'GetVertexShaderDeclaration returns the D3D8 declaration (upstream: INVALIDCALL)')
        check('[mock-d3d9] CreatePixelShader ffff0101 00000042 b00f0000 00000002 800f0000 b0e40000 a1e40000 0000ffff' in log
              and '[app] CreatePixelShader hr=0x0' in log, 'ps.1.1 (negated constant included) is passed through unchanged')
        check('GetPixelShaderFunction hr=0x0 same=1' in log, 'GetPixelShaderFunction returns the D3D8 tokens')
        check('CreateVertexShader(vs_2_0) hr=0x8876086c' in log
              and '[d3d8to9] CreateVertexShader: shader rejected (unsupported shader version' in log,
              'a vs_2_0 blob is refused with D3DERR_INVALIDCALL and a launch-log line')
        check('[app] done' in log and r.returncode == 0, 'the program ran to the end and released everything')
        unexpected = sorted({l.split('unexpected ')[1] for l in log.splitlines() if 'unexpected ' in l})
        print('INFO: Direct3D 9 methods called that the mock does not model: ' + (', '.join(unexpected) or 'none'))

        r2 = run([str(wine), str(app / 'd3d8-smoke.exe')], env=dict(wenv, MOCK_D3D9_FAIL='1'), cwd=app, timeout=600)
        log2 = r2.stdout + r2.stderr
        check('[app] Direct3DCreate8 NULL' in log2
              and '[d3d8to9] Direct3DCreate8(220): Direct3DCreate9 failed, no Direct3D 9 runtime' in log2,
              'without Direct3D 9, Direct3DCreate8 is NULL and the launch log says why')
    print(f'{"PASS" if not failures else "FAIL"}: d3d8to9 under WoW64 Wine ({failures} failure(s))')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
