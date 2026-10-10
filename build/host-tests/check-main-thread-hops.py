#!/usr/bin/env python3
"""No guest thread waits on the iOS main thread without a bound, and DXMT's
query/annotation stubs answer instead of ending the process.

A game whose render thread hops to the main thread while it creates its
swapchain (DXMT's ResizeBuffers -> CAMetalLayer property update) used to wait
with dispatch_sync, with no timeout and nothing logged: "process alive, NO
FRAME PRESENTED". This checks, on the real sources and the real patches:
  - patches/dxmt-*.patch apply in order to the pinned research/dxmt and a
    second run reports them as already applied (apply-madeira-patches.sh);
  - in the patched winemetal_unix.c the only dispatch_sync to the main queue
    is the opt-out (MADEIRA_MAIN_HOP_TIMEOUT_MS=0) and allocation-failure path
    of the bounded execute_on_main, which claims the block exactly once;
  - in app/Madeira the only dispatch_sync to the main queue is the same
    opt-out in Winios.m's winios_metal_layer_for_hwnd;
  - wineserver_stop() no longer joins without a bound;
  - the DXMT methods games call for queries/annotations (Unity, Unreal) no
    longer IMPLEMENT_ME (abort) and DXGI's IMPLEMENT_ME no longer spins;
  - optionally (MADEIRA_LLVM_MINGW=<llvm-mingw dir>) the patched d3d11 sources
    compile for arm64ec.
Nothing here runs on an iPad; the timing behaviour needs the device.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
dxmt = root / 'research/dxmt'
failures = []
def check(cond, what):
    if not cond:
        failures.append(what)

def body_of(src, signature):
    i = src.find(signature)
    if i < 0:
        return None
    j = src.find('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    return None

# ---- app side (always) --------------------------------------------------
app = root / 'app/Madeira'
for f in sorted(list(app.rglob('*.m')) + list(app.rglob('*.mm')) + list(app.rglob('*.c')) + list(app.rglob('*.swift'))):
    if 'SwiftSteam' in f.parts:
        continue
    t = f.read_text(errors='replace')
    for m in re.finditer(r'dispatch_sync\(\s*dispatch_get_main_queue\(\)|DispatchQueue\.main\.sync', t):
        line = t.count('\n', 0, m.start()) + 1
        ok = f.name == 'Winios.m' and 'timeout_ms == 0' in t[max(0, m.start() - 200):m.start()]
        check(ok, 'unbounded main-thread hop: %s:%d' % (f.relative_to(root), line))

w = (app / 'Winios/Winios.m').read_text()
b = body_of(w, 'CAMetalLayer *winios_metal_layer_for_hwnd(void *hwnd)')
check(b and 'dispatch_semaphore_wait(done, dispatch_time(' in b and 'claimFor:2' in b and 'claimFor:1' in b,
      'Winios: the layer hop waits with a deadline and claims the work once')
check(b and 'return nil;' in b and '[madeira-main-hop]' in b, 'Winios: on timeout the swapchain gets no surface, logged')
check('MAIN THREAD unresponsive' in w and 'winios_main_probe(now, t0);' in w, 'Winios: the freeze detector probes the main thread')

ws = (app / 'WineServerBridge.m').read_text()
b = body_of(ws, 'void wineserver_stop(void)')
check(b and 'MADEIRA_WINESERVER_STOP_MS' in b and 'pthread_detach' in b and 'g_wineserver_exited' in b,
      'wineserver_stop: bounded wait, detach on timeout')
check(ws.count('g_wineserver_exited = 1') >= 2, 'wineserver: both thread exits (return, fatal_error) set the flag')

# ---- DXMT patches (needs the submodule) ----------------------------------
if not (dxmt / 'src/winemetal/unix/winemetal_unix.c').is_file():
    print('SKIP-FAIL: research/dxmt is not checked out (git submodule update --init research/dxmt)')
else:
    with tempfile.TemporaryDirectory() as work:
        fake = Path(work)
        (fake / 'build/dxmt-ios').mkdir(parents=True)
        (fake / 'research').mkdir()
        shutil.copytree(root / 'patches', fake / 'patches')
        shutil.copy(root / 'build/dxmt-ios/apply-madeira-patches.sh', fake / 'build/dxmt-ios/')
        r = subprocess.run(['git', 'clone', '-q', str(dxmt), str(fake / 'research/dxmt')], capture_output=True, text=True)
        pin = subprocess.run(['git', '-C', str(dxmt), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        subprocess.run(['git', '-C', str(fake / 'research/dxmt'), 'checkout', '-q', pin], check=True)
        r1 = subprocess.run(['bash', str(fake / 'build/dxmt-ios/apply-madeira-patches.sh')], capture_output=True, text=True)
        r2 = subprocess.run(['bash', str(fake / 'build/dxmt-ios/apply-madeira-patches.sh')], capture_output=True, text=True)
        check(r1.returncode == 0 and 'dxmt-ios-no-hang.patch: applied' in r1.stdout, 'patches apply (%s%s)' % (r1.stdout, r1.stderr))
        check(r2.returncode == 0 and r2.stdout.count('already applied') == len(list((root / 'patches').glob('dxmt-*.patch'))),
              'patches are idempotent (%s%s)' % (r2.stdout, r2.stderr))
        src = fake / 'research/dxmt/src'

        wm = (src / 'winemetal/unix/winemetal_unix.c').read_text()
        b = body_of(wm, 'execute_on_main(dispatch_block_t block) {') or ''
        check('MADEIRA_MAIN_HOP_TIMEOUT_MS' in b and 'dispatch_semaphore_wait(done, dispatch_time(' in b,
              'winemetal: execute_on_main waits with a deadline')
        check(b.count('atomic_compare_exchange_strong(&hop->state, &expect, 1)') == 1 and
              b.count('atomic_compare_exchange_strong(&hop->state, &expect, 2)') == 1,
              'winemetal: the block is claimed once, by the main thread (1) or the caller (2)')
        check('[CATransaction begin]' in b and '[CATransaction commit]' in b,
              'winemetal: the off-main fallback uses an explicit CATransaction')
        check('DISPATCH_TIME_FOREVER' in b and b.index('DISPATCH_TIME_FOREVER') > b.index('&expect, 2)'),
              'winemetal: the caller only waits without bound once the main thread is already running the block')
        outside = wm.replace(b, '')
        check(not re.search(r'dispatch_sync\(\s*dispatch_get_main_queue', outside),
              'winemetal: no other dispatch_sync to the main queue')
        check('if (!win_data) {' in wm, 'winemetal: CreateMetalViewFromHWND survives a window without data')

        ctx = (src / 'd3d11/d3d11_context_impl.cpp').read_text()
        for name in ('IsAnnotationEnabled', 'SetMarkerInt', 'BeginEventInt', 'EndEvent'):
            m = re.search(r'STDMETHODCALLTYPE %s\([^)]*\) override \{([^}]*)\}' % name, ctx)
            check(m and 'IMPLEMENT_ME' not in m.group(1), 'd3d11: %s answers instead of aborting' % name)
        sc = (src / 'd3d11/d3d11_swapchain.cpp').read_text()
        for name in ('GetRestrictToOutput', 'SetBackgroundColor', 'GetBackgroundColor', 'SetRotation', 'GetRotation',
                     'SetSourceSize', 'GetSourceSize'):
            b = body_of(sc, name + '(')
            check(b and 'IMPLEMENT_ME' not in b, 'dxgi swapchain: %s answers instead of aborting' % name)
        dev = (src / 'd3d11/d3d11_device.cpp').read_text()
        for name in ('CheckMultisampleQualityLevels1(', 'CreateQuery1(', 'QueryResourceResidency(', 'CreateSurface(const DXGI_SURFACE_DESC'):
            b = body_of(dev, name)
            check(b and not re.search(r'(?<!was )IMPLEMENT_ME', b), 'd3d11 device: %s answers instead of aborting' % name)
        dp = (src / 'dxgi/dxgi_private.h').read_text()
        check(not re.search(r'while \(1\) \{\s*\\', dp) and 'abort();' in dp, 'dxgi: IMPLEMENT_ME no longer spins forever')

        mingw = os.environ.get('MADEIRA_LLVM_MINGW')
        if mingw and Path(mingw, 'bin/arm64ec-w64-mingw32-clang++').exists():
            cxx = str(Path(mingw, 'bin/arm64ec-w64-mingw32-clang++'))
            inc = ['-I' + str(fake / 'research/dxmt' / d) for d in ('include', 'libs', 'src/util', 'src/dxmt', 'src/dxgi',
                                                                       'src/d3d11', 'src/winemetal', 'src/airconv', 'src/d3d10', 'src')]
            for f in ('d3d11/d3d11_swapchain.cpp', 'd3d11/d3d11_device.cpp', 'd3d11/d3d11_context_imm.cpp',
                      'd3d11/d3d11_context_def.cpp'):
                r = subprocess.run([cxx, '-std=c++20', '-fsyntax-only', '-fblocks', '-DNOMINMAX', '-D_WIN32_WINNT=0xa00',
                                    '-Wno-unused-parameter'] + inc + [str(src / f)], capture_output=True, text=True)
                check(r.returncode == 0, 'arm64ec compile %s: %s' % (f, r.stderr[-1500:]))
            print('compiled the patched d3d11 sources for arm64ec')
        else:
            print('note: MADEIRA_LLVM_MINGW not set; the patched d3d11 sources were not compiled')

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: main-thread hops are bounded, wineserver stop is bounded, DXMT query/annotation stubs answer')
