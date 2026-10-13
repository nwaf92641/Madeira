#!/usr/bin/env python3
"""The engine lines from a black-screen report, end to end.

The user-visible log of a game that stays black (INSIDE and many others) shows
  [Wine WATCHDOG 2s] ...            [waiters] parked=1 over60s=1
  ml990: ... refusing to advertise address space this device cannot map
  ... <-- iOS REFUSED A FREE ADDRESS
  ml1036: no hole fits a 896MB pool -- SHRINKING to 624MB
  [file-fail] ... fex-emu\\AppConfig\\INSIDE.exe.json / Config.json
This test pins down what each one is, in the code and in the report:
  A. Source invariants
     - no thread is logged about while it is thread_suspend()ed: between every
       thread_suspend(X) and its thread_resume(X) in build/ntdll-unix there is no
       wine_log_write / dprintf / ERR / printf / malloc (the [Wine WATCHDOG] block
       used to log there, which deadlocks when the suspended game thread holds the
       log or allocator lock, leaving it suspended forever);
     - the watchdog prints a "resumed" line;
     - the [va-scan] failure verdict is range-aware (passes the failed length), so
       a range with a free head and an occupied tail is not called "iOS REFUSED";
  B. Launch diagnostics (app/Madeira/LaunchDiagnostics.c) on those exact lines:
     - every one of them is an engine NOTE, not a problem, and none becomes the
       verdict (the verdict stays on what really stopped the game);
     - "[waiters] ... main_over60s=1" (this build's ntdll) IS a problem:
       engine-stall, with the hint naming the MAIN rows;
     - a [va-scan] FAILED ... STATUS_NO_MEMORY and a pool that could not be placed
       ARE problems;
     - notes are in last-launch.txt and last-launch.json.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
failures = []


def check(c, w):
    if not c:
        failures.append(w)


# ---------------------------------------------------------------- A. source
unsafe = re.compile(r'\b(wine_log_write|dprintf|ERR|WARN|FIXME|printf|fprintf|malloc|calloc|free|LogStore)\s*\(')
for f in sorted((root / 'build/ntdll-unix').glob('*.c')):
    src = f.read_text(errors='replace')
    for m in re.finditer(r'thread_suspend\s*\(\s*([A-Za-z_][A-Za-z_0-9\[\]]*)\s*\)', src):
        var = m.group(1)
        j = src.find('thread_resume', m.end())
        while j != -1 and not re.match(r'thread_resume\s*\(\s*%s\s*\)' % re.escape(var), src[j:]):
            j = src.find('thread_resume', j + 1)
        if j == -1:
            check(False, '%s: thread_suspend(%s) has no thread_resume' % (f.name, var))
            continue
        window = src[m.end():j]
        # the "suspend failed" branch runs only when nothing was suspended
        window = '\n'.join(l for l in window.splitlines() if 'suspend failed' not in l)
        bad = unsafe.search(window)
        line = src[:m.start()].count('\n') + 1
        check(not bad, '%s:%d: %s while thread %s is suspended' % (f.name, line, bad.group(1) if bad else '', var))

srv = (root / 'build/ntdll-unix/server_ios.c').read_text()
check('resumed the sampled thread before logging' in srv, 'watchdog: prints that it resumed the thread')
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
check('ios_va_describe_range( ios_scan_fail_addr, ios_scan_fail_len' in virt and
      'ios_scan_fail_len = size;' in virt, 'va-scan: the failure verdict is range-aware')
check('ios_va_describe( ios_scan_fail_addr' not in virt, 'va-scan: the single-address verdict is gone from the scan')

# ---------------------------------------------------------------- B. report
inc = root / 'app/Madeira'
harness = r'''
#include "LaunchDiagnostics.h"
#include <stdio.h>
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
    const char *dir = argc > 1 ? argv[1] : NULL;
    /* the user's lines, a Unity game that reached the API stage and stopped */
    madeira_diag_reset("C:\\Games\\INSIDE\\INSIDE.exe", dir);
    madeira_diag_stage(MD_STAGE_WINE_STARTED, "__wine_main");
    feed("ml990: init user_space_limit 0x7fffffff0000 -> 0xfc0000000 -- refusing to advertise address space this device cannot map (HighestUserAddress / ullTotalVirtual / lpMaximumApplicationAddress all derive from it)");
    feed("[Wine WATCHDOG 2s] thread alive=0 (0=yes)");
    feed("[Wine WATCHDOG 2s] PC=0x1a2b3c LR=0x1a2b00 SP=0x16f000000 FP=0x16f000010");
    feed("ml1036: no hole fits a 896MB pool \xe2\x80\x94 SHRINKING to 624MB (the alternative is a pool in the guest window or on top of 0x140000000, both of which are fatal)");
    feed("[va-scan] SLOW window=0x7000000000..0x8000000000 size=0x1000000 align=0x10000 bottom-up tries=80 skips=0 | seen=0x0..0x0 views=10 maxgap=0x0 tailgap=0x0 stop=- | firstfail=0x7038000000 errno=12(Cannot allocate memory) FREE hole, next region 0x7038120000 (+0x120000 away) <-- iOS REFUSED A FREE ADDRESS");
    feed("[file-fail] ml665 #3 NtCreateFile status=0xc0000034 disp=1 access=0x80100080 options=0x00000060 unix=(none) name=\\??\\C:\\users\\madeira\\AppData\\Local\\fex-emu\\AppConfig\\INSIDE.exe.json");
    feed("[file-fail] ml665 #4 NtCreateFile status=0xc0000034 disp=1 access=0x80100080 options=0x00000060 unix=(none) name=\\??\\C:\\users\\madeira\\AppData\\Local\\fex-emu\\Config.json");
    feed("[file-fail] ml665 #5 NtCreateFile status=0xc0000034 disp=1 access=0x80100080 options=0x00000060 unix=(none) name=\\??\\C:\\Games\\INSIDE\\INSIDE_Data\\boot.config.local");
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    feed("[waiters] parked=1 over60s=1 rev=ml444");
    dump("user-lines-old-ntdll");

    madeira_diag_reset("C:\\Games\\INSIDE\\INSIDE.exe", dir);
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    feed("[waiters] parked=3 over60s=2 main_over60s=0 rescued=0 rev=ml444+rescue (no game main thread among them: idle workers, not a hang by itself)");
    feed("[alert-rescue] #1 tid=0130 addr=0x7c530f009a: the wait word moved with no alert (a lost wake) -- returning so the caller re-tests it");
    dump("workers-parked");

    madeira_diag_reset("C:\\Games\\Stuck\\stuck.exe", dir);
    feed("info:  Using feature level D3D_FEATURE_LEVEL_11_0");
    feed("[waiters] parked=2 over60s=1 main_over60s=1 rescued=0 rev=ml444+rescue <-- the GAME MAIN THREAD is parked on an INFINITE wait");
    feed("[waiters]  tid=0024 addr=0x7c530f009a age=75s INF w0=00010003 w1=00000000 MAIN");
    feed("[waiters] parked=2 over60s=1 main_over60s=1 rescued=0 rev=ml444+rescue <-- the GAME MAIN THREAD is parked on an INFINITE wait");
    dump("main-parked");

    madeira_diag_reset("C:\\Games\\Big\\big.exe", dir);
    feed("[va-scan] FAILED window=0x7000000000..0x8000000000 size=0x700000000 align=0x10000 bottom-up tries=3 skips=0 | seen=0x0..0x0 views=10 maxgap=0x0 tailgap=0x0 stop=- | firstfail=0x0 errno=0(-)   <-- STATUS_NO_MEMORY (callers see a NULL alloc)");
    dump("no-memory");

    madeira_diag_reset("C:\\Games\\Np\\np.exe", dir);
    feed("BAD POOL: no valid placement after retries. Killing in 10s \xe2\x80\x94 please relaunch.");
    dump("no-pool");
    return 0;
}
'''


def scenarios(out):
    res, cur, mode = {}, None, 'text'
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


with tempfile.TemporaryDirectory() as work:
    (Path(work) / 'h.c').write_text(harness)
    exe = Path(work) / 'h'
    cc = os.environ.get('CC', 'cc')
    base = [cc, '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror', '-I', str(inc),
            str(inc / 'LaunchDiagnostics.c'), str(Path(work) / 'h.c'), '-o', str(exe), '-lpthread']
    if subprocess.run(base + ['-fsanitize=address,undefined', '-g'], capture_output=True).returncode != 0:
        r = subprocess.run(base, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            sys.exit('FAIL: harness does not compile')
    outdir = Path(work) / 'diag'
    r = subprocess.run([str(exe), str(outdir)], capture_output=True, text=True, timeout=60)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit('FAIL: harness exited %d' % r.returncode)
    s = scenarios(r.stdout)

    a = s['user-lines-old-ntdll']
    keys = sorted(n['key'] for n in a['json']['notes'])
    check(keys == sorted(['va-ceiling', 'wine-watchdog', 'jit-pool-size', 'va-scan-detail', 'fex-config-probe',
                          'file-probe', 'waiters']), 'user lines: each is a note (%s)' % keys)
    check(a['json']['problems'] == [], 'user lines: none is a problem (%s)' % a['json']['problems'])
    check(a['verdict'] == 'unclassified' and 'does not prove device creation hung' in a['text'],
          'user lines: the verdict stays on where the game stopped (%s)' % a['verdict'])
    fex = [n for n in a['json']['notes'] if n['key'] == 'fex-config-probe']
    check(fex and fex[0]['count'] == 2 and 'defaults' in fex[0]['means'], 'FEX Config.json / AppConfig probes: one note, two lines, defaults')
    check('Engine notes' in a['text'] and 'not the cause of a black screen' in a['text'], 'notes are in last-launch.txt')
    w = [n for n in a['json']['notes'] if n['key'] == 'waiters']
    check(w and 'older ntdll' in w[0]['means'], 'old [waiters] format: says it cannot tell main from workers')

    a = s['workers-parked']
    check(a['json']['problems'] == [] and {'waiters', 'alert-rescue'} <= {n['key'] for n in a['json']['notes']},
          'idle workers parked + a rescue: notes only')
    a = s['main-parked']
    check(a['verdict'] == 'engine-stall', 'main thread parked: engine-stall verdict (%s)' % a['verdict'])
    p = a['json']['problems']
    check(len(p) == 1 and p[0]['count'] == 2 and 'MAIN' in p[0]['hint'], 'main thread parked: one problem, counted, hint names MAIN rows')
    check(s['no-memory']['verdict'] == 'process-failure' and 'address' in s['no-memory']['json']['problems'][0]['hint'],
          'va-scan FAILED + STATUS_NO_MEMORY is a problem')
    check(s['no-pool']['verdict'] == 'process-failure', 'no JIT pool placement is a problem')
    j = json.loads((outdir / 'last-launch.json').read_text())
    check('notes' in j, 'last-launch.json has the notes array')

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: no thread is logged about while suspended; engine lines are notes unless the game main thread is parked')
