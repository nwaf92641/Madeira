#!/usr/bin/env python3
"""Launch diagnostics (app/Madeira/LaunchDiagnostics.c) on real log shapes; no Wine runs.

Compiles the production file against a small harness and checks, per scenario,
that the stage reached, the verdict category and the hint follow from the lines
the components really print:
  - a Direct3D 11 game that presents: verdict "none", time to first frame set,
    "process running" kept apart from "first frame presented";
  - a missing redistributable (Wine's import_dll line): missing-dll with the
    DLL named, one problem for two threads printing it;
  - a process that lives but never presents: no success claimed, the stall
    hint names the furthest stage (a swapchain exists, no present);
  - each error category from its real message (swapchain refused by a PE-side
    "[madeira-diag]" line, shader conversion, nextDrawable blocked, a
    full-desktop window not drawn, winegstreamer's stub table, OpenGL with no
    backend, a 64-bit media DLL missing, a crash exit);
  - a launcher that starts a child and exits is not called an early exit;
  - the Windows-layer categories from Wine's own messages: a wrong-architecture
    DLL (c000007b), a missing export (c0000139), a nested import missing (the
    missing DLL stays the verdict), a stub called, a side-by-side assembly not
    found, Wine failing to start the program, an XAudio2 failure;
  - the report files are written, and the previous one is kept on reset;
  - the lines the patched DXMT, D3D9, the D3D12 runtime and the display shim
    print (device, swapchain, no layer, refused format, dropped frame);
  - noise lines are ignored and cost little.
Needs python3 and a C compiler (AddressSanitizer/UBSan when available).
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[2]
src = root / 'app/Madeira/LaunchDiagnostics.c'
inc = root / 'app/Madeira'

harness = r'''
#include "LaunchDiagnostics.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static char buf[65536];
static void dump(const char *name)
{
    printf("=== %s verdict=%s\n", name, madeira_diag_category_name(madeira_diag_verdict()));
    madeira_diag_render_text(buf, sizeof buf); fputs(buf, stdout);
    printf("--- json\n");
    madeira_diag_render_json(buf, sizeof buf); fputs(buf, stdout);
    printf("=== end\n");
}
static void feed(const char *l) { madeira_diag_feed_line(l); }

int main(int argc, char **argv)
{
    const char *dir = argv[1];
    (void)argc;

    madeira_diag_reset("C:\\Games\\Thumper\\THUMPER_win8.exe", dir);
    madeira_diag_note_present_count(41);   /* the counter is process-wide: the base is its value at launch */
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("[madeira-diag] stage=api ok=1 detail=Direct3D 11 (DXMT)");
    feed("[madeira-diag] stage=device ok=1 detail=Direct3D 11 feature level 11_1");
    madeira_diag_stage(MD_STAGE_METAL_LAYER, "hwnd 0x10024");
    feed("[madeira-diag] stage=swapchain ok=1 detail=d3d11 1920x1080 DXGI 87 -> layer 80");
    madeira_diag_note_present_count(41);
    madeira_diag_note_present_count(45);
    dump("d3d11-presents");

    madeira_diag_reset("C:\\Games\\Foo\\foo.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:err:module:import_dll Library MSVCP140.dll (which is needed by L\"C:\\\\Games\\\\Foo\\\\foo.exe\") not found");
    feed("0030:err:module:import_dll Library MSVCP140.dll (which is needed by L\"C:\\\\Games\\\\Foo\\\\foo.exe\") not found");
    feed("0024:err:module:loader_init Importing dlls for L\"C:\\\\Games\\\\Foo\\\\foo.exe\" failed, status c0000135");
    madeira_diag_process_exit(-1073741515, 0xC0000135u);
    dump("missing-dll");

    madeira_diag_reset("C:\\Games\\Stuck\\stuck.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("[madeira-d3d12] device created: build 2026-10-01");
    feed("[madeira-d3d12] swapchain: 2560x1440, 3 buffers, format 28, hwnd 0000000000010080");
    dump("alive-no-frame");

    madeira_diag_reset("C:\\Games\\Sc\\sc.exe", dir);
    feed("[madeira-diag] stage=swapchain ok=0 cat=swapchain-failure detail=d3d11 CreateSwapChain: DXGI format 88 has no CAMetalLayer format; refused with DXGI_ERROR_INVALID_CALL");
    dump("swapchain");

    madeira_diag_reset("C:\\Games\\Sh\\sh.exe", dir);
    feed("[madeira-d3d12] device created: x");
    feed("[madeira-d3d12] pixel conversion failed: entry point not found (MSC backend, code 8); 1234 bytes, head 44584243");
    dump("shader-no-frame");
    madeira_diag_note_present_count(1);
    madeira_diag_note_present_count(2);
    dump("shader-with-frame");

    madeira_diag_reset("C:\\Games\\Pr\\pr.exe", dir);
    feed("[madeira-diag] stage=swapchain ok=1 detail=d3d11 1280x720");
    feed("[iOS DXMT] nextDrawable #12 BLOCKED 1000ms (nil=1 slow_total=12)");
    feed("[iOS DXMT] nextDrawable #13 BLOCKED 1001ms (nil=1 slow_total=13)");
    dump("present-blocked");

    madeira_diag_reset("C:\\Games\\Gdi\\gdi.exe", dir);
    feed("[winios] game window hwnd=0x10050 not drawn (covers the guest desktop)");
    dump("window");

    madeira_diag_reset("C:\\Games\\Vid\\vid.exe", dir);
    feed("[madeira-diag] stage=swapchain ok=1 detail=d3d11 1920x1080");
    feed("0040:warn:module:load_builtin_unixlib iOS: no unix .so for module 0x6ffff (unix_path=winegstreamer.so, modname=winegstreamer.dll, mapped=1), using stub table");
    feed("0040:err:module:import_dll Library quartz.dll (which is needed by L\"C:\\\\Games\\\\Vid\\\\vid.exe\") not found");
    dump("video");

    madeira_diag_reset("C:\\Games\\Api\\api.exe", dir);
    feed("0024:err:module:import_dll Library api-ms-win-shell-namespace-l1-1-0.dll (which is needed by L\"C:\\\\Games\\\\Api\\\\api.exe\") not found");
    feed("0024:err:module:import_dll Library msi.dll (which is needed by L\"C:\\\\Games\\\\Api\\\\setup.exe\") not found");
    dump("apiset");

    madeira_diag_reset("C:\\Games\\Net\\net.exe", dir);
    feed("[WineProc] wine-mono: not installed (.NET programs need Documents/Components/wine-mono-11.0.0, docs/WINE_MONO.md)");
    feed("0024:err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not installed");
    dump("dotnet");

    madeira_diag_reset("C:\\Games\\Gl\\gl.exe", dir);
    feed("0050:warn:module:load_builtin_unixlib iOS: module 0x7000 (opengl32.dll) -> GL-absent stub table (attach ok, wgl/gl NOT_SUPPORTED)");
    dump("opengl");

    madeira_diag_reset("C:\\Games\\Cr\\cr.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    madeira_diag_process_exit(-1073741819, 0xC0000005u);
    dump("crash");

    /* The Windows-layer categories, from the lines Wine 11 really prints
     * (dlls/ntdll/loader.c import_dll, exception.c, actctx.c, unix/env.c). */
    madeira_diag_reset("C:\\Games\\Arch\\arch.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:err:module:import_dll Loading library XINPUT1_3.dll (which is needed by L\"C:\\\\Games\\\\Arch\\\\arch.exe\") failed (error c000007b).");
    madeira_diag_process_exit(-1073741701, 0xC000007Bu);
    dump("arch");

    madeira_diag_reset("C:\\Games\\Ep\\ep.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:err:module:import_dll Loading library d3dx9_42.dll (which is needed by L\"C:\\\\Games\\\\Ep\\\\ep.exe\") failed (error c0000139).");
    dump("entry-point");

    madeira_diag_reset("C:\\Games\\Chain\\chain.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:err:module:import_dll Library avicap32.dll (which is needed by L\"C:\\\\windows\\\\system32\\\\devenum.dll\") not found");
    feed("0024:err:module:import_dll Loading library devenum.dll (which is needed by L\"C:\\\\Games\\\\Chain\\\\chain.exe\") failed (error c0000135).");
    dump("chain");

    madeira_diag_reset("C:\\Games\\Stub\\stub.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("wine: Call from 00006FFFFF4A1234 to unimplemented function gameinput.dll.GameInputCreate, aborting");
    madeira_diag_process_exit(-2147483392, 0x80000100u);
    dump("unimplemented");

    madeira_diag_reset("C:\\Games\\Sxs\\sxs.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:fixme:actctx:parse_depend_manifests Could not find dependent assembly L\"Microsoft.VC90.CRT\" (9.0.21022.8)");
    dump("sxs");

    madeira_diag_reset("C:\\Games\\Cc6\\cc6.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("wine: Call from 00006FFFFFD55EF8 to unimplemented function COMCTL32.dll.TaskDialogIndirect, aborting");
    dump("cc6-stub");

    madeira_diag_reset("C:\\Games\\Bad\\bad.exe", dir);
    feed("wine: failed to start L\"\\\\??\\\\C:\\\\Games\\\\Bad\\\\bad.exe\": c000007b");
    dump("wine-init");

    madeira_diag_reset("C:\\Games\\Snd\\snd.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0040:err:xaudio2:IXAudio2Impl_CreateMasteringVoice Failed to create audio client: 80070490");
    dump("audio");

    madeira_diag_reset("C:\\Games\\La\\launcher.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0060:err:process:spawn_process spawn_process: creating child thread for L\"C:\\\\Games\\\\La\\\\game.exe\" (fd=41, unixdir=-1, dup_unixdir=-1)");
    madeira_diag_process_exit(0, 0);
    dump("launcher");

    /* the lines the patched DXMT (patches/dxmt-ios-layer-safety.patch), the
     * D3D12 runtime and IOSDisplayShim.m print */
    madeira_diag_reset("C:\\Games\\Dx\\dx11.exe", dir);
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    feed("info:  [madeira-diag] stage=device ok=1 detail=Direct3D 11 (DXMT), feature level 0xb000");
    feed("info:  [madeira-diag] stage=swapchain ok=1 detail=d3d11 1920x1080 format 87");
    dump("dxmt-d3d11-lines");

    madeira_diag_reset("C:\\Games\\Nl\\nolayer.exe", dir);
    feed("[madeira-display] hwnd=0x10024 asked for a Metal view before the game view registered its layer; waiting up to 5000 ms");
    feed("[madeira-display] view_create_metal_view called before layer registered! (hwnd=0x10024; the swapchain gets no surface)");
    feed("err:   Failed to create metal view for hwnd 0x10024: the window has no Metal layer (game view not registered, or the macdrv_functions export is missing)");
    feed("err:   [madeira-diag] stage=swapchain ok=0 cat=swapchain-failure detail=d3d11: no Metal layer for the swapchain's window, CreateSwapChain fails");
    dump("no-layer");

    madeira_diag_reset("C:\\Games\\D9\\d9.exe", dir);
    feed("info:  [d3d9-modes] CreateDevice 1024x768 X8R8G8B8 refresh=0 windowed=1 count=1 -> hr 0x0");
    dump("d3d9-ok");

    madeira_diag_reset("C:\\Games\\D9\\d9bad.exe", dir);
    feed("info:  [d3d9-modes] CreateDevice 640x480 R5G6B5 refresh=60 windowed=0 count=1 -> hr 0x8876086c");
    dump("d3d9-fail");

    madeira_diag_reset("C:\\Games\\D8\\d8.exe", dir);
    feed("[d3d8to9] Direct3DCreate8(220): translating Direct3D 8 to the Direct3D 9 runtime");
    feed("[d3d8to9] CreateDevice 800x600 fmt 22 windowed behavior 0x40 -> hr 0x0");
    feed("info:  [d3d9-modes] CreateDevice 800x600 X8R8G8B8 refresh=0 windowed=1 count=1 -> hr 0x0");
    dump("d3d8-ok");

    madeira_diag_reset("C:\\Games\\D8\\d8bad.exe", dir);
    feed("[d3d8to9] Direct3DCreate8(220): translating Direct3D 8 to the Direct3D 9 runtime");
    feed("[d3d8to9] CreateDevice 640x480 fmt 23 fullscreen behavior 0x20 -> hr 0x8876086c");
    dump("d3d8-device-fail");

    madeira_diag_reset("C:\\Games\\D8\\d8nod9.exe", dir);
    feed("[d3d8to9] Direct3DCreate8(220): Direct3DCreate9 failed, no Direct3D 9 runtime to translate to");
    dump("d3d8-no-d3d9");

    madeira_diag_reset("C:\\Games\\D8\\d8sh.exe", dir);
    feed("[d3d8to9] Direct3DCreate8(220): translating Direct3D 8 to the Direct3D 9 runtime");
    feed("[d3d8to9] CreateVertexShader: shader rejected (opcode not valid in shader model 1, version 0xfffe0101)");
    dump("d3d8-shader");

    /* the lines third_party/cnc-ddraw/madeira/madeira_log.c prints, as
     * build/host-tests/check-cnc-ddraw.py records them under Wine */
    madeira_diag_reset("C:\\Games\\Dd\\dd.exe", dir);
    feed("[WineProc] cnc-ddraw: syswow64\\ddraw.dll -> cnc-ddraw/ddraw.dll, config C:\\ProgramData\\cnc-ddraw\\ddraw.ini (fresh copy), WINEDLLOVERRIDES ddraw=n,b");
    feed("[cnc-ddraw] renderer direct3d9 (ddraw.ini renderer=direct3d9, C:\\ProgramData\\cnc-ddraw\\ddraw.ini)");
    feed("[cnc-ddraw] Direct3D 9 device 0x0 windowed=1");
    dump("cnc-ok");

    madeira_diag_reset("C:\\Games\\Dd\\ddnodev.exe", dir);
    feed("[cnc-ddraw] renderer direct3d9 (ddraw.ini renderer=direct3d9, C:\\ProgramData\\cnc-ddraw\\ddraw.ini)");
    feed("[cnc-ddraw] Direct3D 9 CreateDevice failed (hr 0x8876086a)");
    feed("[cnc-ddraw] Direct3D 9 renderer could not start, falling back to GDI");
    dump("cnc-device-fail");

    madeira_diag_reset("C:\\Games\\Dd\\ddnod9.exe", dir);
    feed("[cnc-ddraw] renderer direct3d9 (ddraw.ini renderer=direct3d9, C:\\ProgramData\\cnc-ddraw\\ddraw.ini)");
    feed("[cnc-ddraw] Direct3DCreate9 returned NULL");
    feed("[cnc-ddraw] Direct3D 9 renderer could not start, falling back to GDI");
    dump("cnc-no-d3d9");

    madeira_diag_reset("C:\\Games\\Dd\\ddgl.exe", dir);
    feed("[cnc-ddraw] renderer opengl (ddraw.ini renderer=auto, C:\\ProgramData\\cnc-ddraw\\ddraw.ini)");
    dump("cnc-opengl");

    madeira_diag_reset("C:\\Games\\Dd\\ddmissing.exe", dir);
    feed("[WineProc] cnc-ddraw: requested, but this build has no cnc-ddraw/ddraw.dll (build/ddraw/build.sh); Wine's ddraw");
    dump("cnc-not-built");

    madeira_diag_reset("C:\\Games\\Dd\\ddlib.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("0024:err:module:import_dll Library ddraw.dll (which is needed by L\"C:\\\\Games\\\\Dd\\\\ddlib.exe\") not found");
    dump("ddraw-missing");

    madeira_diag_reset("C:\\Games\\D11\\devfail.exe", dir);
    feed("err:   [d3d11-fail] ml752 D3D11CoreCreateDevice failed: no Metal device");
    dump("d3d11-device-fail");

    madeira_diag_reset("C:\\Games\\D12\\cv.exe", dir);
    feed("[madeira-d3d12] device created: x");
    feed("[madeira-display] CAMetalLayer refused pixel format 70 (invalid pixel format 70); the layer uses 80 and the frame is converted when it is drawn");
    feed("[madeira-diag] stage=present ok=0 cat=metal-present-failure detail=d3d12: the drawable (format 80) differs from the back buffer (format 70) and this build has no converting present; frame dropped");
    dump("d3d12-no-convert");

    /* Unity-shaped stall: the prebuilt d3d11.dll logs "Using feature level" and
     * never a [madeira-diag] device line, then the game waits. */
    madeira_diag_reset("C:\\Games\\Un\\INSIDE.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "64-bit (x86-64) program, ARM64EC");
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    dump("unity-api-only");

    madeira_diag_reset("C:\\Games\\Un\\hop.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "64-bit (x86-64) program, ARM64EC");
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    madeira_diag_stage(MD_STAGE_METAL_LAYER, "game view layer");
    feed("[freeze] MAIN THREAD unresponsive for 5.2s (since t+41.0s) while in the foreground; guest threads that hop to it wait (bounded by MADEIRA_MAIN_HOP_TIMEOUT_MS). Thread stacks follow.");
    feed("[madeira-main-hop] winemetal: the main thread did not run a Metal layer update within 2000 ms (1 times); applied on this thread instead -- the main thread is busy or blocked");
    dump("main-hop");

    madeira_diag_reset("C:\\Games\\Un\\unimpl.exe", dir);
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    feed("err:   d3d11_context_impl.cpp:IsAnnotationEnabled is not implemented.");
    dump("dxmt-unimplemented");

    madeira_diag_reset("C:\\Games\\Noise\\noise.exe", dir);
    {
        clock_t c0 = clock();
        for (int i = 0; i < 300000; i++) {
            feed("[frame] present 16.6ms gpu 9.1ms draws 1200");
            feed("0080:trace:file:NtWriteFile (0x40,(nil),(nil),(nil),0x7ff,0x1234,32,(nil),(nil))");
        }
        printf("noise-cpu-ms=%ld\n", (long)((clock() - c0) * 1000 / CLOCKS_PER_SEC));
    }
    dump("noise");
    madeira_diag_flush();
    return 0;
}
'''

def compile_harness(work):
    exe = Path(work) / 'harness'
    (Path(work) / 'harness.c').write_text(harness)
    cc = os.environ.get('CC', 'cc')
    base = [cc, '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror', '-I', str(inc),
            str(src), str(Path(work) / 'harness.c'), '-o', str(exe), '-lpthread']
    for extra in (['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g'], ['-O1']):
        if subprocess.run(base + extra, capture_output=True).returncode == 0:
            print('built with', 'AddressSanitizer/UBSan' if 'address' in extra[0] else 'no sanitizers')
            return exe
    r = subprocess.run(base, capture_output=True, text=True)
    print(r.stderr)
    sys.exit('FAIL: the harness does not compile')


def scenarios(out):
    res = {}
    cur = None
    for line in out.splitlines():
        if line.startswith('=== ') and not line.startswith('=== end'):
            name, verdict = line[4:].split(' verdict=')
            cur = {'verdict': verdict, 'text': [], 'json': []}
            res[name] = cur
            mode = 'text'
        elif line == '=== end':
            cur['json'] = json.loads('\n'.join(cur['json']))
            cur['text'] = '\n'.join(cur['text'])
            cur = None
        elif cur is not None:
            if line == '--- json':
                mode = 'json'
            else:
                cur[mode].append(line)
    return res


failures = []
def check(cond, what):
    if not cond:
        failures.append(what)


with tempfile.TemporaryDirectory() as work:
    exe = compile_harness(work)
    outdir = Path(work) / 'madeira-diagnostics'
    r = subprocess.run([str(exe), str(outdir)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout[-4000:], r.stderr[-4000:])
        sys.exit('FAIL: harness exited %d' % r.returncode)
    s = scenarios(r.stdout)

    a = s['d3d11-presents']
    check(a['verdict'] == 'none', 'd3d11: verdict none')
    check(a['json']['first_frame_presented'] is True and 'time_to_first_frame' in a['json'], 'd3d11: first frame + time')
    check(a['json']['presents'] == 4, 'd3d11: presents counted from the launch base (got %s)' % a['json']['presents'])
    check([x['name'] for x in a['json']['stages']] == ['launch', 'wine-started', 'graphics-api', 'device',
                                                        'metal-layer', 'swapchain', 'first-present'], 'd3d11: stage order')
    check('FIRST FRAME PRESENTED' in a['text'], 'd3d11: text says first frame')

    a = s['missing-dll']
    check(a['verdict'] == 'missing-dll', 'missing dll: verdict (%s)' % a['verdict'])
    p = [x for x in a['json']['problems'] if x['category'] == 'missing-dll']
    check(len(p) == 1 and p[0]['count'] == 2, 'missing dll: two threads are one problem')
    check(p and 'MSVCP140.dll' in p[0]['error'] and 'redistributable' in p[0]['hint'], 'missing dll: name + redistributable hint')
    check(a['json']['crash_status'] == 0xC0000135, 'missing dll: crash status kept')
    check(not a['json']['process_running'], 'missing dll: process not running')

    a = s['alive-no-frame']
    check(a['verdict'] != 'none' and a['json']['first_frame_presented'] is False, 'alive: no success claimed')
    check(a['json']['process_running'] is True, 'alive: process running')
    check('NO FRAME PRESENTED' in a['text'] and 'A swapchain exists but nothing was presented' in a['text'],
          'alive: stall hint names the swapchain stage')

    check(s['swapchain']['verdict'] == 'swapchain-failure', 'swapchain: verdict')
    check('DXGI_ERROR_INVALID_CALL' in s['swapchain']['json']['problems'][0]['error'], 'swapchain: actual error kept')
    check(s['shader-no-frame']['verdict'] == 'shader-translation-failure', 'shader: verdict before a frame')
    check(s['shader-with-frame']['verdict'] == 'none' and
          any(x['category'] == 'shader-translation-failure' for x in s['shader-with-frame']['json']['problems']),
          'shader: kept as a problem after the first frame')
    a = s['present-blocked']
    check(a['verdict'] == 'metal-present-failure' and a['json']['problems'][0]['count'] == 2, 'present: verdict + count')
    check(s['window']['verdict'] == 'window-visibility', 'window: verdict')
    a = s['video']
    cats = sorted(x['category'] for x in a['json']['problems'])
    check(cats == ['missing-dll', 'video-init-failure'], 'video: categories (%s)' % cats)
    check(any('media component' in x['hint'] for x in a['json']['problems']), 'video: quartz gets the media hint')
    a = s['apiset']
    hints = [x.get('hint') or '' for x in a['json']['problems']]
    check(a['verdict'] == 'missing-dll' and any('API set contract' in h and 'windows.storage' in h for h in hints),
          'api set: missing-dll verdict, hint explains schema / host')
    check(any('installer_scripting' in h for h in hints), 'msi: installer / scripting hint')
    a = s['dotnet']
    check(a['verdict'] == 'dependency-load-failure' and 'Documents/Components/wine-mono-11.0.0' in a['json']['problems'][0]['hint'],
          'dotnet: missing Wine Mono is a dependency failure whose hint says where the component goes (%s)' % a['verdict'])
    a = s['opengl']
    check(a['verdict'] == 'graphics-device-failure' and 'OpenGL' in a['json']['problems'][0]['hint'], 'opengl: verdict + hint')
    a = s['crash']
    check(a['verdict'] == 'process-failure' and '0xC0000005' in a['text'], 'crash: verdict + code')
    a = s['arch']
    check(a['verdict'] == 'architecture-mismatch', 'arch: c000007b is a wrong-architecture DLL (%s)' % a['verdict'])
    check('xinput1_3.dll' in a['json']['problems'][0]['error'].lower(), 'arch: names the DLL')
    a = s['entry-point']
    check(a['verdict'] == 'unimplemented-function', 'entry point: c0000139 is a missing export (%s)' % a['verdict'])
    check('entry point' in a['json']['problems'][0]['hint'], 'entry point: hint says so')
    a = s['chain']
    cats = [x['category'] for x in a['json']['problems']]
    check(cats == ['missing-dll', 'dependency-load-failure'], 'chain: nested import missing, then the importer fails (%s)' % cats)
    check(a['verdict'] == 'missing-dll', 'chain: the missing DLL is the verdict, not the importer')
    a = s['unimplemented']
    check(a['verdict'] == 'unimplemented-function', 'stub: verdict (%s)' % a['verdict'])
    check('gameinput.dll.gameinputcreate' in a['json']['problems'][0]['error'].lower(), 'stub: names dll.function')
    a = s['sxs']
    check([x['category'] for x in a['json']['problems']] == ['dependency-load-failure'], 'sxs: dependent assembly')
    check('winsxs' in a['json']['problems'][0]['hint'], 'sxs: hint names winsxs')
    a = s['cc6-stub']
    check(a['verdict'] == 'unimplemented-function', 'comctl32 6 stub: verdict (%s)' % a['verdict'])
    check('Common Controls 6.0' in a['json']['problems'][0]['hint'] and 'winsxs' in a['json']['problems'][0]['hint'],
          'comctl32 6 stub: hint names Common Controls 6 and winsxs')
    check('Common Controls' not in (s['unimplemented']['json']['problems'][0].get('hint') or ''), 'other stubs do not get the Common Controls hint')
    check(s['wine-init']['verdict'] == 'wine-init-failure', 'wine init: failed to start (%s)' % s['wine-init']['verdict'])
    check(s['audio']['verdict'] == 'audio-init-failure', 'audio: xaudio2 error (%s)' % s['audio']['verdict'])
    a = s['launcher']
    check(any(x['name'] == 'child-process' for x in a['json']['stages']), 'launcher: child stage')
    check(not any(x['error'].startswith('exit code') for x in a['json']['problems']), 'launcher: not an early exit')
    a = s['dxmt-d3d11-lines']
    names = [x['name'] for x in a['json']['stages']]
    check(all(n in names for n in ('graphics-api', 'device', 'swapchain')) and a['json']['problems'] == [],
          'dxmt d3d11: API, device and swapchain stages from the patched lines (%s)' % names)
    a = s['no-layer']
    check(a['verdict'] == 'swapchain-failure' and not any(x['name'] == 'swapchain' for x in a['json']['stages']),
          'no layer: swapchain failure, no swapchain stage')
    a = s['d3d9-ok']
    names = [x['name'] for x in a['json']['stages']]
    check(all(n in names for n in ('graphics-api', 'device', 'swapchain')) and a['json']['problems'] == [],
          'd3d9: CreateDevice hr 0 gives API, device and swapchain (%s)' % names)
    check(s['d3d9-fail']['verdict'] == 'graphics-device-failure', 'd3d9: CreateDevice error is a device failure')
    check(s['d3d11-device-fail']['verdict'] == 'graphics-device-failure', 'd3d11: device failure line')
    a = s['d3d8-ok']
    st = {x['name']: x.get('detail', '') for x in a['json']['stages']}
    check('graphics-api' in st and 'Direct3D 8' in st['graphics-api'] and 'device' in st and 'swapchain' in st
          and a['json']['problems'] == [], 'd3d8: the API stage names the Direct3D 8 path, device + swapchain (%s)' % st)
    check(s['d3d8-device-fail']['verdict'] == 'graphics-device-failure', 'd3d8: CreateDevice error is a device failure')
    a = s['d3d8-no-d3d9']
    check(a['verdict'] == 'graphics-device-failure' and 'd3d9.dll' in a['json']['problems'][0]['hint'],
          'd3d8: no Direct3D 9 runtime is a device failure that names d3d9.dll')
    check(s['d3d8-shader']['verdict'] == 'shader-translation-failure', 'd3d8: a refused shader is a shader failure')
    a = s['cnc-ok']
    st = {x['name']: x.get('detail', '') for x in a['json']['stages']}
    check(a['json']['problems'] == [] and 'cnc-ddraw over DXMT' in st.get('graphics-api', '')
          and 'cnc-ddraw' in st.get('device', ''), 'cnc-ddraw: API and device stages name it, no problem (%s)' % st)
    a = s['cnc-device-fail']
    st = {x['name']: x.get('detail', '') for x in a['json']['stages']}
    check(a['verdict'] == 'graphics-device-failure' and 'GDI fallback' in st.get('graphics-api', '')
          and 'renderer=gdi' in a['json']['problems'][0]['hint'],
          'cnc-ddraw: a refused Direct3D 9 device is a device failure, the stage says GDI fallback (%s)' % st)
    a = s['cnc-no-d3d9']
    check(a['verdict'] == 'graphics-device-failure' and 'MADEIRA_GAME_GDI_FULLSCREEN' in a['json']['problems'][0]['hint'],
          'cnc-ddraw: no Direct3D 9 is a device failure that names the GDI fallback switch')
    a = s['cnc-opengl']
    check(a['verdict'] == 'graphics-device-failure' and 'renderer=direct3d9' in a['json']['problems'][0]['hint'],
          'cnc-ddraw: an OpenGL renderer is flagged with the ini fix')
    a = s['cnc-not-built']
    check(a['verdict'] == 'dependency-load-failure' and 'build/ddraw/build.sh' in a['json']['problems'][0]['hint'],
          'cnc-ddraw: requested but not in the bundle is a dependency failure')
    a = s['ddraw-missing']
    check(a['verdict'] == 'missing-dll' and 'MADEIRA_DDRAW' in a['json']['problems'][0]['hint'],
          'ddraw.dll missing: the hint points at cnc-ddraw (%s)' % (a['json']['problems'][:1],))
    check('MADEIRA_DDRAW=cnc' in s['window']['json']['problems'][0]['hint']
          and s['window']['json']['problems'][0]['hint'].rstrip().endswith(').'),
          'window-visibility hint mentions cnc-ddraw and is not cut off')
    a = s['d3d12-no-convert']
    cats = sorted(x['category'] for x in a['json']['problems'])
    check(cats == ['metal-present-failure', 'swapchain-failure'], 'd3d12: refused format + dropped frame (%s)' % cats)
    check(any('converts each frame' in x['hint'] for x in a['json']['problems']), 'd3d12: refused-format hint')

    a = s['unity-api-only']
    check(a['verdict'] == 'none' or a['json']['problems'] == [], 'unity: no invented problem (%s)' % a['verdict'])
    check('neither the end of that nor a swapchain' in a['text'] and 'does not prove device creation hung' in a['text'],
          'unity: a runtime without a device line is not called a device-creation hang')
    check('created no Direct3D device yet' not in a['text'], 'unity: the "no device yet" hint is not used once the runtime started')
    a = s['main-hop']
    check(len(a['json']['problems']) == 2 and all(x['category'] == 'swapchain-failure' for x in a['json']['problems']),
          'main-hop: the freeze probe and the bounded hop are both reported (%s)' % a['json']['problems'])
    check(any('Thread stacks' in x['hint'] or 'thread stacks' in x['hint'] for x in a['json']['problems']),
          'main-hop: hint points at the thread stacks')
    check('asked for the Metal layer and got it' in a['text'], 'main-hop: stall hint for a layer without a swapchain line')
    a = s['dxmt-unimplemented']
    check(a['verdict'] == 'unimplemented-function' and 'abort' in a['json']['problems'][0]['hint'],
          'dxmt: "is not implemented." is an unimplemented-function problem (%s)' % a['verdict'])

    a = s['noise']
    check(a['json']['problems'] == [] and len(a['json']['stages']) == 1, 'noise: nothing recognised')
    ms = [int(l.split('=')[1]) for l in r.stdout.splitlines() if l.startswith('noise-cpu-ms=')]
    check(ms and ms[0] < 8000, 'noise: 600k lines are cheap (%s ms, sanitizers on)' % ms)

    check((outdir / 'last-launch.txt').is_file() and (outdir / 'last-launch.json').is_file(), 'files: report written')
    check((outdir / 'previous-launch.txt').is_file(), 'files: previous report kept')
    if (outdir / 'last-launch.json').is_file():
        j = json.loads((outdir / 'last-launch.json').read_text())
        check(j['exe'].endswith('noise.exe'), 'files: last report is the last launch')
    if (outdir / 'previous-launch.txt').is_file():
        check('unimpl.exe' in (outdir / 'previous-launch.txt').read_text(), 'files: previous is the launch before')

    # The header names every category the request asked for, under one stable name each.
    hdr = (inc / 'LaunchDiagnostics.h').read_text()
    for c in ('MD_CAT_PROCESS', 'MD_CAT_MISSING_DLL', 'MD_CAT_DEVICE', 'MD_CAT_SWAPCHAIN', 'MD_CAT_SHADER',
              'MD_CAT_PRESENT', 'MD_CAT_WINDOW', 'MD_CAT_VIDEO', 'MD_CAT_UNIMPLEMENTED', 'MD_CAT_DEPENDENCY',
              'MD_CAT_ARCH', 'MD_CAT_WINE_INIT', 'MD_CAT_AUDIO', 'MD_CAT_UNCLASSIFIED'):
        check(c in hdr, 'header: %s' % c)

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: launch diagnostics classify real log shapes, keep "running" apart from "first frame", and write the report')
