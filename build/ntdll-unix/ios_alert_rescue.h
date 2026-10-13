/* ios_alert_rescue.h -- bounded INFINITE alert waits (lost-wake rescue).
 *
 * WHY. Every Win32 lock and condition variable in Wine (SRW locks, condition
 * variables, critical sections without a semaphore, WaitOnAddress, barriers)
 * parks in NtWaitForAlertByThreadId through RtlWaitOnAddress, and the waker
 * finds the parked thread in RtlWaitOnAddress's futex_queues -- a table that is
 * private to EACH COPY of the PE ntdll. Madeira runs several PE ntdll copies in
 * one iOS process (x86-64 guest, ARM64EC, WoW64, every pseudo-process), so a
 * lock word shared across copies can be released by a thread whose copy has no
 * record of the waiter: the value changes, nobody calls NtAlertThreadByThreadId,
 * and the waiter sleeps forever. That is the "[waiters] parked=N over60s=N"
 * line: a thread parked on an INFINITE wait for over a minute (ml439..ml447
 * proved the parked crowd receives ZERO alerts). A parked game thread is a
 * black screen with the process alive.
 *
 * WHAT. An INFINITE wait with a real address is slept in slices (default
 * 1000 ms, madeira.cfg "alert-rescue-ms", 0 = the old unbounded sleep). At the
 * end of a slice the word at the wait address is compared with its value when
 * the thread parked. Unchanged: keep sleeping -- an idle worker never returns
 * early, so nothing changes for the common case. Changed: the condition the
 * thread waits for may already hold and the wake was lost, so the wait returns
 * STATUS_ALERTED. That is a spurious wake, which every caller tolerates: Wine's
 * SRW, critical-section and barrier code re-test the lock word in a loop, and
 * Windows documents spurious returns for WaitOnAddress and
 * SleepConditionVariable*. No lock is forced, nothing is reaped: the caller's
 * own re-test decides.
 *
 * Header-only, no Wine types, so build/host-tests/check-alert-rescue.py compiles
 * it against fake memory. */
#ifndef MADEIRA_IOS_ALERT_RESCUE_H
#define MADEIRA_IOS_ALERT_RESCUE_H

#include <errno.h>
#include <stdint.h>
#include <sys/mman.h>

#define IOS_AR_DEFAULT_MS 1000
#define IOS_AR_MAX_MS     60000
#define IOS_AR_PAGE       0x4000ULL      /* iOS page; also a multiple of 4K hosts */

/* madeira.cfg value -> slice length in ms (0 = disabled). */
static inline int ios_ar_clamp_ms( long long v )
{
    if (v < 0) return IOS_AR_DEFAULT_MS;
    if (v > IOS_AR_MAX_MS) return IOS_AR_MAX_MS;
    return (int)v;
}

/* Read the word at a wait address without faulting. Only the naturally aligned
 * 8/4/2/1 bytes AT the address are read, so the read never crosses a page; the
 * page itself is checked with msync (unmapped -> ENOMEM on Darwin and Linux).
 * Reading wider than the caller's compare size is harmless: a neighbour byte
 * changing only causes one extra (spurious, tolerated) wake.
 * Returns 1 and the value, or 0 when the address cannot be read. */
static inline int ios_ar_probe( const void *addr, unsigned long long *out )
{
    uintptr_t a = (uintptr_t)addr;
    if (a <= 0x10000 || a >= 0x8000000000ULL) return 0;
    if (msync( (void *)(a & ~(IOS_AR_PAGE - 1)), IOS_AR_PAGE, MS_ASYNC )) return 0;
    if (!(a & 7))      *out = *(volatile unsigned long long *)a;
    else if (!(a & 3)) *out = *(volatile unsigned int *)a;
    else if (!(a & 1)) *out = *(volatile unsigned short *)a;
    else               *out = *(volatile unsigned char *)a;
    return 1;
}

/* End of a slice with no alert: 1 = the word moved since the thread parked, so
 * return to the caller and let it re-test; 0 = keep sleeping. */
static inline int ios_ar_should_rescue( int have_snap, unsigned long long snap, const void *addr )
{
    unsigned long long now;
    if (!have_snap) return 0;
    if (!ios_ar_probe( addr, &now )) return 0;
    return now != snap;
}

/* The configured slice: madeira.cfg alert-rescue-ms (default 1000 ms, 0 = off).
 * Only where madeira_cfg.h was included first (the generated ntdll sync.c). */
#ifdef MADEIRA_CFG_H
static inline int ios_ar_cfg_ms( void )
{
    return ios_ar_clamp_ms( madeira_cfg_int("alert-rescue-ms", 1000) );
}
#endif

#endif /* MADEIRA_IOS_ALERT_RESCUE_H */
