#!/usr/bin/env python3
"""The 64-bit (ARM64EC) DLL farm: what build/wine-pe/arm64ec-farm.json adds is
really in app/Madeira/arm64ec-windows, is loadable as built, and closes the gaps
it was added for. No toolchain or Wine needed; pure Python over the files.

  - the list is well formed: no duplicates, no name another part of the tree
    owns (ntdll, DXMT's d3d11/dxgi/d3d10core/winemetal, d3d12, xtajit64, ...);
  - every listed module is in the farm, as an ARM64EC PE32+ image (the COFF
    machine of an ARM64EC image is AMD64, 0x8664; what marks it as ARM64EC is
    the code llvm-mingw emits in .hexpthk/.a64xrm, the sections every Wine
    module in the farm has) with FileAlignment == SectionAlignment == 0x10000
    like every Wine module the iOS loader maps, and stripped of debug sections;
    typelibs are resource-only PE files carrying an MSFT or SLTG typelib;
  - import closure: every DLL any farm module imports is in the farm (API set
    names resolve through apisetschema.dll), so no listed module stops at
    "Library X (which is needed by ...) not found";
  - Winlator's Windows components (its wincomponents.json, pinned below): each
    DLL name it installs is answered by the 64-bit farm, or the exception says
    why not (32-bit only on Windows too, not a Wine module, ...);
  - the prefix template's 64-bit COM registrations: the classes games create
    first (filter graph, media engine, XAudio2 2.7, WMI locator, MSXML 6,
    DirectPlay 8, the device enumerator, DxDiag) point at a DLL the farm now
    has, and the number of registered classes with no DLL behind them does not
    grow back;
  - with a Wine checkout (the wine submodule): the entry points the layer test
    (build/x64-tests/compat-layers-x64.c) calls are implemented in Wine's .spec
    files, not "stub" entries.
"""
from __future__ import annotations

import io
import json
import re
import struct
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FARM = ROOT / 'app/Madeira/arm64ec-windows'
LIST = ROOT / 'build/wine-pe/arm64ec-farm.json'
TEMPLATE = ROOT / 'app/Madeira/prefix-template.tar.gz'
WINE = ROOT / 'wine'

OWNED = {'ntdll.dll', 'd3d11.dll', 'dxgi.dll', 'd3d10core.dll', 'winemetal.dll', 'd3d12.dll',
         'd3d12core.dll', 'vulkan-1.dll', 'winevulkan.dll', 'xtajit64.dll', 'wineios.drv'}

# brunodev85/winlator-app @ 3981d86, app/src/main/assets/wincomponents/wincomponents.json:
# the DLL names of the Windows components Winlator installs on demand. There
# they are Microsoft's redistributable DLLs; here Wine's builtins answer them.
WINLATOR_COMPONENTS = {
    'direct3d': ['d3dcompiler_33', 'd3dcompiler_34', 'd3dcompiler_35', 'd3dcompiler_36', 'd3dcompiler_37',
                 'd3dcompiler_38', 'd3dcompiler_39', 'd3dcompiler_40', 'd3dcompiler_41', 'd3dcompiler_42',
                 'd3dcompiler_43', 'd3dcompiler_46', 'd3dcompiler_47', 'd3dcsx_42', 'd3dcsx_43', 'd3dx10',
                 'd3dx10_33', 'd3dx10_34', 'd3dx10_35', 'd3dx10_36', 'd3dx10_37', 'd3dx10_38', 'd3dx10_39',
                 'd3dx10_40', 'd3dx10_41', 'd3dx10_42', 'd3dx10_43', 'd3dx11_42', 'd3dx11_43', 'd3dx9_24',
                 'd3dx9_25', 'd3dx9_26', 'd3dx9_27', 'd3dx9_28', 'd3dx9_29', 'd3dx9_30', 'd3dx9_31', 'd3dx9_32',
                 'd3dx9_33', 'd3dx9_34', 'd3dx9_35', 'd3dx9_36', 'd3dx9_37', 'd3dx9_38', 'd3dx9_39', 'd3dx9_40',
                 'd3dx9_41', 'd3dx9_42', 'd3dx9_43'],
    'directsound': ['dsound'],
    'directmusic': ['dmband', 'dmcompos', 'dmime', 'dmloader', 'dmscript', 'dmstyle', 'dmsynth', 'dmusic',
                    'dmusic32', 'dswave'],
    'directshow': ['amstream', 'qasf', 'qcap', 'qdvd', 'qedit', 'quartz'],
    'directplay': ['dplaysvr.exe', 'dplayx', 'dpmodemx', 'dpnet', 'dpnhpast', 'dpnhupnp', 'dpnsvr.exe', 'dpwsockx'],
    'xaudio': ['x3daudio1_0', 'x3daudio1_1', 'x3daudio1_2', 'x3daudio1_3', 'x3daudio1_4', 'x3daudio1_5',
               'x3daudio1_6', 'x3daudio1_7', 'xactengine2_0', 'xactengine2_1', 'xactengine2_10', 'xactengine2_2',
               'xactengine2_3', 'xactengine2_4', 'xactengine2_5', 'xactengine2_6', 'xactengine2_7', 'xactengine2_8',
               'xactengine2_9', 'xactengine3_0', 'xactengine3_1', 'xactengine3_2', 'xactengine3_3', 'xactengine3_4',
               'xactengine3_5', 'xactengine3_6', 'xactengine3_7', 'xapofx1_0', 'xapofx1_1', 'xapofx1_2',
               'xapofx1_3', 'xapofx1_4', 'xapofx1_5', 'xaudio2_0', 'xaudio2_1', 'xaudio2_2', 'xaudio2_3',
               'xaudio2_4', 'xaudio2_5', 'xaudio2_6', 'xaudio2_7', 'xaudio2_8', 'xaudio2_9'],
    'vcrun2005': ['atl80', 'msvcm80', 'msvcp80', 'msvcr80', 'vcomp'],
    'vcrun2010': ['msvcp100', 'msvcr100', 'vcomp100', 'atl100'],
    'wmdecoder': ['wmvcore', 'wmasf', 'wmadmod', 'wmvdecod'],
}
# Names the 64-bit farm deliberately does not answer, and why. Anything else
# Winlator installs must be in the farm.
WINLATOR_EXCEPTIONS = {
    **{n: 'DirectMusic: Windows never shipped it for 64-bit (Winlator installs it into syswow64 only); '
          'the 32-bit farm has it' for n in WINLATOR_COMPONENTS['directmusic']},
    **{f'd3dx9_{v}': '2004-2006 SDK; 64-bit games of that period are too rare for 1.4 MiB each; '
                     'the 32-bit farm has it' for v in range(24, 31)},
    **{n: 'XACT2: 2006-2007 SDKs, 32-bit games; the 32-bit farm has the versions Wine builds'
       for n in WINLATOR_COMPONENTS['xaudio'] if n.startswith('xactengine2_')},
    'xapofx1_0': 'not a Wine module (Wine builds xapofx1_1..1_5)',
    'd3dcsx_42': 'not a Wine module in this Wine', 'd3dcsx_43': 'not a Wine module in this Wine',
    'd3dx10': 'not a Wine module (an unversioned name Winlator\'s archive carries)',
    'dpmodemx': 'not a Wine module (modem service provider)',
    'dplaysvr.exe': 'a Wine program; no 64-bit Wine program is shipped yet',
    'dpnsvr.exe': 'a Wine program; no 64-bit Wine program is shipped yet',
}

# CLSIDs a game creates first for each layer, and the DLL the template names.
KEY_CLASSES = {
    '{E436EBB3-524F-11CE-9F53-0020AF0BA770}': 'quartz.dll',         # CLSID_FilterGraph
    '{62BE5D10-60EB-11D0-BD3B-00A0C911CE86}': 'devenum.dll',        # CLSID_SystemDeviceEnum
    '{B44392DA-499B-446B-A4CB-005FEAD0E6D5}': 'mfmediaengine.dll',  # CLSID_MFMediaEngineClassFactory
    '{5A508685-A254-4FBA-9B82-9A24B00306AF}': 'xaudio2_7.dll',      # CLSID_XAudio2 (2.7)
    '{4590F811-1D3A-11D0-891F-00AA004B2E24}': 'wbem\\wbemprox.dll', # CLSID_WbemLocator
    '{88D96A05-F192-11D4-A65F-0040963251E5}': 'msxml6.dll',         # CLSID_DOMDocument60
    '{286F484D-375E-4458-A272-B138E2F80A6A}': 'dpnet.dll',          # CLSID_DirectPlay8Peer
    '{A65B8071-3BFE-4213-9A5B-491DA4461CA7}': 'dxdiagn.dll',        # CLSID_DxDiagProvider
    '{C1F400A0-3F08-11D3-9F0B-006008039E37}': 'qedit.dll',          # CLSID_SampleGrabber
}
# Registered 64-bit classes whose DLL is not in the farm: 458 before the list
# existed; what remains is mostly mshtml/ieframe, DirectMusic, scripting and
# the shell. A change that drops a listed module shows up here.
MAX_UNRESOLVED_CLASSES = 270

# Entry points compat-layers-x64.c calls: dll -> names that must be real in the .spec.
SPEC_ENTRIES = {
    'xaudio2_9': ['XAudio2Create'], 'xaudio2_8': ['XAudio2Create'],
    'd3d10_1': ['D3D10CreateDevice1'], 'd3d10': ['D3D10CreateDeviceAndSwapChain', 'D3D10CreateDevice'],
    'd3dx9_36': ['D3DXMatrixMultiply', 'D3DXCreateTextureFromFileInMemory'],
    'd3dx10_43': ['D3DX10CheckVersion'], 'd3dcompiler_43': ['D3DCompile'],
    'd2d1': ['D2D1CreateFactory'], 'wmvcore': ['WMCreateSyncReader', 'WMCreateReader'],
    'evr': ['MFCreateVideoPresenter'], 'mfplay': ['MFPCreateMediaPlayer'],
    'gdiplus': ['GdiplusStartup'], 'xmllite': ['CreateXmlReader'], 'usp10': ['ScriptGetProperties'],
    'avifil32': ['AVIFileInit', 'AVIFileOpenW'], 'msvfw32': ['ICOpen', 'ICDecompress'],
    'msvcr100': ['_snprintf', 'malloc', '_beginthreadex'], 'vcomp100': ['omp_get_max_threads'],
}

failures: list[str] = []


def check(cond: bool, what: str) -> None:
    if not cond:
        failures.append(what)


def pe_info(path: Path):
    d = path.read_bytes()
    if d[:2] != b'MZ':
        return None
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    if d[pe:pe + 4] != b'PE\0\0':
        return None
    machine, nsec = struct.unpack_from('<HH', d, pe + 4)
    opt_size = struct.unpack_from('<H', d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', d, opt)[0]
    sa, fa = struct.unpack_from('<II', d, opt + 32)
    ndd_off = opt + (108 if magic == 0x20b else 92)
    ndd = struct.unpack_from('<I', d, ndd_off)[0]
    imp_rva = struct.unpack_from('<I', d, ndd_off + 4 + 8)[0]
    dly_rva = struct.unpack_from('<I', d, ndd_off + 4 + 13 * 8)[0] if ndd > 13 else 0
    secs = []
    so = opt + opt_size
    for i in range(nsec):
        name = d[so + 40 * i: so + 40 * i + 8].rstrip(b'\0').decode('latin-1')
        vsz, va, rsz, rptr = struct.unpack_from('<IIII', d, so + 40 * i + 8)
        secs.append((name, va, vsz, rptr, rsz))

    def off(rva):
        for _n, va, vsz, rptr, rsz in secs:
            if va <= rva < va + max(vsz, rsz):
                return rva - va + rptr
        return None

    imports = []
    o = off(imp_rva) if imp_rva else None
    while o is not None and o + 20 <= len(d):
        name_rva = struct.unpack_from('<I', d, o + 12)[0]
        if not name_rva:
            break
        no = off(name_rva)
        if no is None:
            break
        imports.append(d[no:d.index(b'\0', no)].decode('latin-1'))
        o += 20
    # Delay imports count too: wmvcore delay-imports winegstreamer, and a
    # missing delay-load target aborts the game at first call (an
    # unimplemented-function crash), not at load.
    delay = []
    o = off(dly_rva) if dly_rva else None
    while o is not None and o + 32 <= len(d):
        name_rva = struct.unpack_from('<I', d, o + 4)[0]
        if not name_rva:
            break
        no = off(name_rva)
        if no is None:
            break
        delay.append(d[no:d.index(b'\0', no)].decode('latin-1'))
        o += 32
    return {'machine': machine, 'magic': magic, 'sa': sa, 'fa': fa,
            'sections': [s[0] for s in secs], 'imports': imports, 'delay': delay}


def main() -> int:
    doc = json.loads(LIST.read_text())
    listed: list[str] = []
    for g in doc['groups']:
        check(bool(g.get('reason')), f"group {g['name']}: has a reason")
        for m in g['modules']:
            listed.append(m.lower())
    check(len(listed) == len(set(listed)), 'list: no duplicates')
    check(not (set(listed) & OWNED), 'list: no owned names (%s)' % sorted(set(listed) & OWNED))

    farm = {p.name.lower(): p for p in FARM.iterdir() if p.is_file()}
    missing = [m for m in listed if m not in farm]
    check(not missing, 'farm: listed modules present (missing %s)' % missing)

    infos = {}
    for name, path in farm.items():
        if name.endswith('.tlb'):
            continue
        info = pe_info(path)
        if info:
            infos[name] = info
    for m in listed:
        if m not in farm:
            continue
        if m.endswith('.tlb'):
            data = farm[m].read_bytes()
            check(data[:2] == b'MZ' and (b'MSFT' in data or b'SLTG' in data),
                  f'{m}: a PE file carrying a typelib (MSFT or SLTG)')
            continue
        i = infos.get(m)
        check(i is not None, f'{m}: a PE image')
        if not i:
            continue
        # A module without code (resource-only like mferror, export forwarders
        # like usp10 and icmp, which Wine 11 implements in gdi32/iphlpapi) has
        # no EC sections to show.
        has_code = '.text' in i['sections']
        check(i['machine'] == 0x8664 and i['magic'] == 0x20b and
              (not has_code or {'.hexpthk', '.a64xrm'} <= set(i['sections'])),
              f'{m}: ARM64EC PE32+ (machine {i["machine"]:#x}, sections {i["sections"]})')
        check(i['fa'] == i['sa'] == 0x10000, f'{m}: FileAlignment == SectionAlignment == 0x10000 ({i["fa"]:#x}/{i["sa"]:#x})')
        check(not any(s.startswith('.debug') for s in i['sections']), f'{m}: stripped of debug sections')

    # Import closure over the whole farm. Test executables (x64 PE) are included.
    unresolved = {}
    for name, i in infos.items():
        for imp in i['imports'] + i['delay']:
            low = imp.lower()
            if low.startswith(('api-ms-win-', 'ext-ms-win-')) or low in farm:
                continue
            unresolved.setdefault(name, []).append(imp)
    new_unresolved = {k: v for k, v in unresolved.items() if k in listed}
    check(not new_unresolved, 'closure: listed modules import only farm DLLs (%s)' % new_unresolved)
    if unresolved:
        print('note: imports that do not resolve in the farm (modules not from the list):',
              {k: v for k, v in unresolved.items() if k not in listed})

    # Winlator coverage
    uncovered = []
    for comp, names in WINLATOR_COMPONENTS.items():
        for n in names:
            file = n if '.' in n else n + '.dll'
            if file.lower() in farm:
                continue
            if n in WINLATOR_EXCEPTIONS:
                continue
            uncovered.append(f'{comp}:{n}')
    check(not uncovered, 'winlator: every component DLL is answered or excepted (%s)' % uncovered)
    for n, why in WINLATOR_EXCEPTIONS.items():
        file = n if '.' in n else n + '.dll'
        check(file.lower() not in farm, f'winlator exception {n} is stale: the farm has it now')
        check(bool(why), f'winlator exception {n}: has a reason')

    # Prefix template: 64-bit COM classes
    if TEMPLATE.is_file():
        with tarfile.open(TEMPLATE) as t:
            member = next(m for m in t.getmembers() if m.name.endswith('/system.reg') or m.name == 'system.reg')
            reg = t.extractfile(member).read().decode('utf-8', 'replace')
        blocks = re.findall(r'^\[Software\\\\Classes\\\\CLSID\\\\(\{[^}]+\})\\\\InprocServer32\][^\n]*\n(.*?)(?=^\[|\Z)',
                            reg, re.M | re.S)
        paths = {}
        for clsid, body in blocks:
            m = re.search(r'^@="([^"]+)"', body, re.M)
            if m:
                paths[clsid.upper()] = m.group(1).replace('\\\\', '\\').lower()
        prefix = 'c:\\windows\\system32\\'
        # system32\wbem is linked from the farm by madeira_link_wbem (WineProcessBridge.m)
        linked_subdirs = {'wbem'}

        def resolvable(p: str) -> bool:
            if not p.startswith(prefix):
                return True
            rel = p[len(prefix):]
            parts = rel.split('\\')
            if len(parts) > 2 or (len(parts) == 2 and parts[0] not in linked_subdirs):
                return False
            return parts[-1] in farm

        for clsid, dll in KEY_CLASSES.items():
            p = paths.get(clsid)
            check(p == prefix + dll.lower(), f'template: {clsid} -> {dll} (got {p})')
            check(p is not None and resolvable(p), f'template: {clsid} ({dll}) resolves in the 64-bit farm')
        bad = sorted(c for c, p in paths.items() if not resolvable(p))
        check(len(bad) <= MAX_UNRESOLVED_CLASSES,
              f'template: {len(bad)} registered 64-bit classes without a DLL (limit {MAX_UNRESOLVED_CLASSES})')
        print(f'template: {len(paths)} 64-bit classes registered, {len(bad)} without a DLL in the farm')
    else:
        print('SKIP: prefix template not found')

    # Wine .spec: entry points are implemented
    if (WINE / 'dlls').is_dir():
        for dll, names in SPEC_ENTRIES.items():
            spec = WINE / 'dlls' / dll / f'{dll}.spec'
            if not spec.is_file():
                check(False, f'spec: {spec} missing')
                continue
            text = spec.read_text()
            for n in names:
                m = re.search(r'^\s*(?:@|\d+)\s+(\w+)(?:\s+-[\w=,\s-]+?)?\s+' + re.escape(n) + r'\b', text, re.M)
                check(m is not None and m.group(1) != 'stub', f'spec: {dll}.{n} implemented (got {m.group(1) if m else None})')
    else:
        print('SKIP: wine submodule not checked out; .spec checks not run')

    if failures:
        for f in failures:
            print('FAIL:', f)
        return 1
    print(f'PASS: {len(listed)} listed modules in the 64-bit farm, ARM64EC, mappable, import-closed; '
          f'Winlator components answered; key COM classes resolve')
    return 0


if __name__ == '__main__':
    sys.exit(main())
