#!/usr/bin/env python3
"""JIT pool placeholder fallback (app/Madeira/JITPoolPlacement.h, JITAllocator.c).

The image-load constructor used to hold a placeholder only at the end of the
0x140000000 executable window; one early mapping there (ml1135: ~31MB at
0x157d00000) meant no placeholder, a pool sized from leftovers ("SHRINKING to
624MB") and placement by chance. jpp_pick() chooses the largest legal hole.
Checks, on fake address maps:
  - the ph-rdr90 map: an intruder right above the window -> the big hole above
    the intruder is chosen, capped at 1024MB, 16MB aligned;
  - the executable window is never part of the hole, even when the free run
    spans it;
  - nothing below the FEX low bound 0x119000000 or inside the guest window;
  - no hole of 256MB -> no placeholder (the old path, then the census, decides);
  - ties keep the lower hole; overlapping/unsorted-tail regions do not crash;
  - JITAllocator.c only falls back when the run above the window failed, uses
    VM_FLAGS_FIXED (never overwrites), and StikJITHelper logs the fallback.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
inc = root / 'app/Madeira'
failures = []


def check(c, w):
    if not c:
        failures.append(w)


harness = r'''
#include "JITPoolPlacement.h"
#include <stdio.h>
#define MB (1ul << 20)
#define LO 0x119000000ul
#define HI 0x7000000000ul
#define WL 0x140000000ul
#define WH 0x148000000ul
static void run(const char *name, const struct jpp_region *r, unsigned n)
{
    unsigned long b = 0, s = 0;
    int ok = jpp_pick(r, n, LO, HI, WL, WH, 256 * MB, 1024 * MB, 16 * MB, &b, &s);
    printf("%s ok=%d base=0x%lx size=%lu\n", name, ok, b, s / MB);
}
int main(void)
{
    /* ph-rdr90: app image low, window held, a 31MB intruder at 0x157d00000,
     * runtime up to 0x16fa24000, then malloc zones from 0x200000000 to the guest window */
    struct jpp_region a[] = {
        { 0x100000000ul, 0x22000000ul },          /* app image + early allocs to 0x122000000 */
        { WL, WH - WL },                         /* the window placeholder */
        { 0x157d00000ul, 31 * MB },
        { 0x200000000ul, HI - 0x200000000ul },
    };
    /* the free run spans the window: must split, never include it */
    struct jpp_region b[] = { { 0x100000000ul, 0x20000000ul }, { 0x1a0000000ul, HI - 0x1a0000000ul } };
    /* only small holes */
    struct jpp_region c[] = { { 0x100000000ul, 0x120000000ul - 0x100000000ul }, { 0x12c000000ul, HI - 0x12c000000ul } };
    /* two equal holes: lower wins */
    struct jpp_region d[] = { { 0x100000000ul, 0x20000000ul }, { 0x160000000ul, 0x40000000ul },
                              { 0x1c0000000ul + 0x10000000ul, 0x10000000ul }, { 0x200000000ul + 0x10000000ul, HI - 0x210000000ul } };
    /* overlapping regions and an unaligned free start */
    struct jpp_region e[] = { { 0x100000000ul, 0x50000000ul }, { 0x150000000ul, 0x1234567ul }, { 0x151000000ul, 0x300000ul },
                              { 0x151234567ul, 0x1000ul }, { 0x300000000ul, HI - 0x300000000ul } };
    /* empty map below the guest window: one huge hole, capped */
    run("rdr90", a, 4);
    run("span", b, 2);
    run("small", c, 2);
    run("tie", d, 4);
    run("overlap", e, 5);
    run("empty", 0, 0);
    return 0;
}
'''

with tempfile.TemporaryDirectory() as work:
    (Path(work) / 'h.c').write_text(harness)
    exe = Path(work) / 'h'
    cc = os.environ.get('CC', 'cc')
    base = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(inc), str(Path(work) / 'h.c'), '-o', str(exe)]
    if subprocess.run(base + ['-fsanitize=address,undefined', '-g'], capture_output=True).returncode != 0:
        r = subprocess.run(base, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            sys.exit('FAIL: harness does not compile')
    out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30).stdout
    res = {}
    for l in out.splitlines():
        name, rest = l.split(' ', 1)
        kv = dict(x.split('=') for x in rest.split())
        res[name] = (int(kv['ok']), int(kv['base'], 16), int(kv['size']))

    W_LO, W_HI, LO, HI = 0x140000000, 0x148000000, 0x119000000, 0x7000000000

    def legal(b, s):
        return b >= LO and b + (s << 20) <= HI and (b + (s << 20) <= W_LO or b >= W_HI) and b % (16 << 20) == 0

    ok, b, s = res['rdr90']
    check(ok and b == 0x15a000000 and s == 1024,   # intruder ends 0x159c00000, 16MB aligned up
          'rdr90: the 2.6GB hole above the intruder is chosen, capped at 1024MB (%s)' % (res['rdr90'],))
    check(legal(b, s), 'rdr90: legal placement')
    ok, b, s = res['span']
    check(ok and legal(b, s) and b == W_HI, 'span: the free run is split around the window, the larger part kept (%s)' % (res['span'],))
    check(res['small'][0] == 0, 'small: no hole of 256MB -> no placeholder (%s)' % (res['small'],))
    ok, b, s = res['tie']
    check(ok and b == 0x1a0000000 and s == 768 and legal(b, s),
          'tie: the window splits the first run (512+384MB), the lower of the two 768MB holes wins (%s)' % (res['tie'],))
    ok, b, s = res['overlap']
    check(ok and legal(b, s) and b == 0x152000000, 'overlap: overlapping regions and an unaligned start (%s)' % (res['overlap'],))
    ok, b, s = res['empty']
    check(ok and s == 1024 and b == W_HI, 'empty map: capped at 1024MB right above the window (%s)' % (res['empty'],))

    c = (inc / 'JITAllocator.c').read_text()
    i = c.find('madeira_early_va_claim')
    body = c[i:c.find('\n}\n', i)]
    check('if (!madeira_early_pool_size) {' in body and body.count('if (!madeira_early_pool_size)') == 2,
          'JITAllocator.c: the fallback runs only when the run above the window failed')
    check('jpp_pick(' in body and 'VM_FLAGS_FIXED' in body.split('jpp_pick(')[1], 'JITAllocator.c: fallback reserves with VM_FLAGS_FIXED (never overwrites)')
    check('VM_PROT_NONE' in body.split('jpp_pick(')[1], 'JITAllocator.c: the placeholder costs no memory (PROT_NONE)')
    check('madeira_early_pool_fallback' in (inc / 'JITAllocator.h').read_text(), 'JITAllocator.h exports the fallback flag')
    check('[jit-pool-placement]' in (inc / 'StikJITHelper.swift').read_text(), 'StikJITHelper logs the fallback')

if failures:
    for f in failures:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: the JIT pool placeholder takes the largest legal hole when the run above the window is taken')
