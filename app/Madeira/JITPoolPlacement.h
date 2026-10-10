/* JITPoolPlacement.h -- pick the JIT pool placeholder hole at image load.
 *
 * ml1040 holds a PROT_NONE placeholder for the RX JIT pool from a constructor
 * that runs before main, so the app's own runtime allocations cannot fragment
 * the address space the pool needs. It only ever tried ONE place: the run
 * starting exactly at the end of the 0x140000000 executable window. When
 * anything at all was mapped there before the constructor (ml1135 / ph-rdr90: a
 * ~31MB mapping at ~0x157d00000), no placeholder was held, the pool was left to
 * whatever survived until the user tapped launch, and the hole census then
 * found only a fragment: "SHRINKING to 624MB" of the 896MB asked for (and on
 * worse launches 432-448MB, code-cache rollovers and "[jit-pool] EXHAUSTED").
 *
 * The fallback here takes the LARGEST free hole anywhere the pool may legally
 * live -- above the FEX low bound 0x119000000, below the guest window
 * 0x7000000000, outside the executable window -- at the moment the constructor
 * runs, when the address space is least fragmented. StikJITHelper already
 * releases madeira_early_pool_base (wherever it is) immediately before the
 * debugger allocates, and plugs every lower hole that would win first-fit, so
 * no other change is needed for the pool to land in it.
 *
 * Pure function over a sorted region list: build/host-tests/check-jit-pool-placement.py
 * compiles it with fake maps. */
#ifndef MADEIRA_JIT_POOL_PLACEMENT_H
#define MADEIRA_JIT_POOL_PLACEMENT_H

struct jpp_region { unsigned long base, size; };

static inline unsigned long jpp_align_up(unsigned long v, unsigned long a) { return (v + a - 1) & ~(a - 1); }
static inline unsigned long jpp_align_down(unsigned long v, unsigned long a) { return v & ~(a - 1); }

/* Consider the free gap [gs, ge): clip to [lo, hi), keep the larger usable part
 * on either side of the exclusion [xl, xh), align, cap at max. */
static inline void jpp_consider(unsigned long gs, unsigned long ge, unsigned long lo, unsigned long hi,
                                unsigned long xl, unsigned long xh, unsigned long max, unsigned long align,
                                unsigned long *best_base, unsigned long *best_size)
{
    unsigned long parts[2][2];
    int np = 0, i;
    if (gs < lo) gs = lo;
    if (ge > hi) ge = hi;
    if (ge <= gs) return;
    if (xh > xl && gs < xh && ge > xl) {          /* split around the exclusion */
        if (gs < xl) { parts[np][0] = gs; parts[np][1] = xl; np++; }
        if (ge > xh) { parts[np][0] = xh; parts[np][1] = ge; np++; }
    } else { parts[np][0] = gs; parts[np][1] = ge; np++; }
    for (i = 0; i < np; i++) {
        unsigned long b = jpp_align_up(parts[i][0], align), e = jpp_align_down(parts[i][1], align), sz;
        if (e <= b) continue;
        sz = e - b;
        if (sz > max) sz = max;
        if (sz > *best_size) { *best_base = b; *best_size = sz; }   /* ties keep the lower hole */
    }
}

/* regions: mapped ranges sorted by base (overlaps tolerated). Returns 1 with the
 * chosen [base, base+size) when a hole of at least min exists, else 0. */
static inline int jpp_pick(const struct jpp_region *r, unsigned n, unsigned long lo, unsigned long hi,
                           unsigned long xl, unsigned long xh, unsigned long min, unsigned long max,
                           unsigned long align, unsigned long *out_base, unsigned long *out_size)
{
    unsigned long cursor = lo, best_base = 0, best_size = 0;
    unsigned i;
    for (i = 0; i < n; i++) {
        unsigned long rb = r[i].base, re = r[i].base + r[i].size;
        if (re <= cursor) continue;
        if (rb >= hi) break;
        if (rb > cursor) jpp_consider(cursor, rb, lo, hi, xl, xh, max, align, &best_base, &best_size);
        cursor = re;
    }
    if (cursor < hi) jpp_consider(cursor, hi, lo, hi, xl, xh, max, align, &best_base, &best_size);
    if (best_size < min) return 0;
    *out_base = best_base;
    *out_size = best_size;
    return 1;
}

#endif /* MADEIRA_JIT_POOL_PLACEMENT_H */
