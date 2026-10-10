#!/usr/bin/env python3
"""Lost-wake rescue for INFINITE alert waits (build/ntdll-unix/ios_alert_rescue.h).

1. patch_sync_ios.py applies to the wine submodule's unix/sync.c: every anchor is
   found once, and the generated copy has the sliced wait, the re-test, the
   STATUS_ALERTED return, the forward declaration, and the [waiters] summary that
   names the main thread.
2. The header, compiled against real memory with two threads:
   - a waiter whose word is changed by another thread WITHOUT an alert (the lost
     wake) returns within two slices;
   - a waiter whose word never changes (an idle worker) never returns early;
   - unmapped and out-of-range addresses are refused, not dereferenced;
   - 2-, 4- and 8-byte aligned words and odd addresses are read at their width;
   - the madeira.cfg value is clamped (negative -> default, huge -> 60 s, 0 = off).
Needs python3 and a C compiler. Nothing here proves on-device behaviour.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
hdr = root / 'build/ntdll-unix/ios_alert_rescue.h'
script = root / 'build/ntdll-unix/patch_sync_ios.py'
sync = root / 'wine/dlls/ntdll/unix/sync.c'
failures = []


def check(cond, what):
    if not cond:
        failures.append(what)


harness = r'''
#define _GNU_SOURCE
#include "ios_alert_rescue.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* iOS addresses are all below 0x8000000000 (the probe refuses anything above);
 * a Linux PIE's statics are not, so the words live in a mapping placed low
 * (below the AddressSanitizer shadow, which starts at 0x7fff8000). */
static volatile unsigned long long *wp;
#define word8 (*wp)
static volatile int stop_changer;

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

/* the wait loop exactly as patch_sync_ios.py builds it, minus the futex */
static int wait_like_ntdll(const void *addr, int slice_ms, int max_slices)
{
    unsigned long long snap = 0;
    int have = ios_ar_probe(addr, &snap), s;
    for (s = 0; s < max_slices; s++) {
        usleep(slice_ms * 1000);
        if (ios_ar_should_rescue(have, snap, addr)) return s + 1;
    }
    return 0;
}

static void *lost_wake(void *arg) { (void)arg; usleep(30000); word8 = word8 + 1; return NULL; }   /* no alert */

int main(void)
{
    pthread_t t;
    unsigned long long v;
    double t0;
    int r;
    char *page;

    {
        void *m = mmap((void *)0x20000000ULL, 0x10000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (m == MAP_FAILED || (uintptr_t)m >= 0x8000000000ULL) { puts("no-low-mapping"); return 2; }
        wp = (volatile unsigned long long *)((char *)m + 0x40);
    }
    /* lost wake: rescued within two slices */
    word8 = 0x1234;
    pthread_create(&t, NULL, lost_wake, NULL);
    t0 = now_s();
    r = wait_like_ntdll((const void *)&word8, 50, 40);
    pthread_join(t, NULL);
    printf("lost-wake slices=%d secs=%.3f\n", r, now_s() - t0);

    /* idle: never returns early */
    r = wait_like_ntdll((const void *)&word8, 20, 10);
    printf("idle slices=%d\n", r);

    /* widths */
    word8 = 0x1122334455667788ULL;
    r = ios_ar_probe((const void *)&word8, &v);      printf("w8=%d %llx\n", r, v);
    r = ios_ar_probe((const char *)&word8 + 4, &v);  printf("w4=%d %llx\n", r, v);
    r = ios_ar_probe((const char *)&word8 + 2, &v);  printf("w2=%d %llx\n", r, v);
    r = ios_ar_probe((const char *)&word8 + 1, &v);  printf("w1=%d %llx\n", r, v);

    /* refused addresses */
    printf("null=%d low=%d high=%d\n", ios_ar_probe(NULL, &v), ios_ar_probe((void *)0x1000, &v),
           ios_ar_probe((void *)0x9000000000ULL, &v));
    page = mmap((void *)0x21000000ULL, 0x8000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    munmap(page, 0x8000);
    printf("unmapped=%d\n", (uintptr_t)page < 0x8000000000ULL ? ios_ar_probe(page + 0x40, &v) : 0);
    printf("rescue-unmapped=%d\n", ios_ar_should_rescue(1, 5, page + 0x40));
    printf("rescue-nosnap=%d\n", ios_ar_should_rescue(0, 5, (const void *)&word8));

    printf("clamp %d %d %d %d\n", ios_ar_clamp_ms(-1), ios_ar_clamp_ms(0), ios_ar_clamp_ms(250), ios_ar_clamp_ms(999999));
    return 0;
}
'''

with tempfile.TemporaryDirectory() as work:
    # 1. the build-time patch
    out = Path(work) / 'sync_ios.c'
    r = subprocess.run([sys.executable, str(script), str(sync), str(out)], capture_output=True, text=True)
    check(r.returncode == 0, 'patch_sync_ios.py applies to wine/dlls/ntdll/unix/sync.c (%s)' % r.stderr.strip())
    if r.returncode == 0:
        g = out.read_text(errors='replace')
        check('extern volatile long long ios_alert_rescues;' in g and 'volatile long long ios_alert_rescues;\n' in g,
              'generated sync: rescue counter declared before use and defined')
        check('ios_ar_have = ios_ar_probe( address, &ios_ar_snap );' in g, 'generated sync: snapshot at park')
        check('ret = futex_wait( futex, 0, &slice );' in g, 'generated sync: INFINITE waits sleep in slices')
        i = g.find('ret = futex_wait( futex, 0, &slice );')
        seg = g[i:i + 1500]
        check('if (*(volatile LONG *)futex) continue;' in seg, 'generated sync: an alert that raced the slice end is consumed')
        check('ios_ar_should_rescue( ios_ar_have, ios_ar_snap, address )' in seg and 'return STATUS_ALERTED;' in seg,
              'generated sync: moved word -> STATUS_ALERTED (a tolerated spurious wake)')
        check(seg.index('ios_alert_waiters[ios_wslot].addr = NULL;') < seg.index('return STATUS_ALERTED;'),
              'generated sync: the [waiters] registration is cleared before returning')
        check('main_over60s=%d rescued=%lld' in g and '" MAIN"' in g, 'generated sync: [waiters] names the main thread')
        check(str(root / 'build/madeira_cfg.h') in g, 'generated sync: madeira_cfg.h included by absolute path')
        check('ret = futex_wait( futex, 0, NULL );' in g, 'generated sync: alert-rescue-ms=0 keeps the unbounded sleep')
        # submodule untouched
        st = subprocess.run(['git', '-C', str(root / 'wine'), 'status', '--porcelain', 'dlls/ntdll/unix/sync.c'],
                            capture_output=True, text=True)
        check(st.stdout.strip() == '', 'the wine submodule file itself is not modified')
    b = (root / 'build/ntdll-unix/build.sh').read_text()
    check('patch_sync_ios.py' in b and '"$OBJ_DIR/sync_ios.c" "sync"' in b, 'build.sh compiles the generated sync')
    check('rm -f "$OBJ_DIR/$name.o"' in b, 'build.sh removes a stale object before compiling (a failed unit cannot ship old code)')

    # 2. the header
    (Path(work) / 'h.c').write_text(harness)
    exe = Path(work) / 'h'
    cc = os.environ.get('CC', 'cc')
    base = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(hdr.parent), str(Path(work) / 'h.c'), '-o', str(exe), '-lpthread']
    built = False
    for extra in (['-fsanitize=address,undefined', '-g'], ['-O1']):
        if subprocess.run(base + extra, capture_output=True).returncode == 0:
            built = True
            break
    if not built:
        rr = subprocess.run(base, capture_output=True, text=True)
        print(rr.stderr)
        sys.exit('FAIL: harness does not compile')
    rr = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    lines = rr.stdout.splitlines()
    if rr.returncode != 0 or not any(l.startswith('lost-wake') for l in lines):
        print(rr.stdout[-2000:], rr.stderr[-2000:])
        sys.exit('FAIL: harness exited %d' % rr.returncode)
    lw = [l for l in lines if l.startswith('lost-wake')][0]
    slices = int(lw.split('slices=')[1].split()[0])
    check(1 <= slices <= 2, 'lost wake rescued within two slices (%s)' % lw)
    check('idle slices=0' in lines, 'an unchanged word never returns early')
    check('w8=1 1122334455667788' in lines, '8-byte word read at its width')
    check('w4=1 11223344' in lines, '4-byte word read at its width')
    check('w2=1 5566' in lines, '2-byte word read at its width')
    check('w1=1 77' in lines, 'odd address read as one byte')
    check('null=0 low=0 high=0' in lines, 'null, low and out-of-range addresses refused')
    check('unmapped=0' in lines and 'rescue-unmapped=0' in lines, 'unmapped page refused, no rescue')
    check('rescue-nosnap=0' in lines, 'no snapshot, no rescue')
    check('clamp 1000 0 250 60000' in lines, 'cfg clamp (%s)' % [l for l in lines if l.startswith('clamp')])

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: INFINITE alert waits re-test their word per slice: a lost wake returns, an idle wait does not')
