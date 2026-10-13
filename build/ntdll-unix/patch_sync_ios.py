#!/usr/bin/env python3
"""Produce the iOS sync.c that build.sh compiles, from wine/dlls/ntdll/unix/sync.c.

The wine submodule is a separate repository, so Madeira-side changes to its unix
sync.c are applied here, at build time, to a COPY (build/ntdll-unix/obj/sync_ios.c);
the submodule checkout is never modified. Every edit is anchored on exact text
and asserts that the anchor exists once, so a submodule bump that moves the code
fails the build loudly instead of silently compiling an unpatched file.

Edits:
  1. lost-wake rescue for INFINITE NtWaitForAlertByThreadId waits
     (build/ntdll-unix/ios_alert_rescue.h explains why);
  2. the [waiters] summary names the game's main thread and counts rescues, so
     "parked=1 over60s=1" stops being ambiguous (an idle worker vs a stuck game).

usage: patch_sync_ios.py <wine sync.c> <output .c>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))


def once(s, old, new, what):
    n = s.count(old)
    if n != 1:
        sys.exit('patch_sync_ios: anchor for %s found %d times (expected 1)' % (what, n))
    return s.replace(old, new, 1)


def patch(s):
    # The copy lives in another directory: point the relative include at the file.
    s = once(s, '#include "../../../../build/madeira_cfg.h"',
             '#include "%s"\n#include "%s"\nextern volatile long long ios_alert_rescues;   /* defined with the alert counters */'
             % (os.path.join(REPO, 'build', 'madeira_cfg.h'), os.path.join(HERE, 'ios_alert_rescue.h')),
             'madeira_cfg include')

    # --- 1. rescue state + config, next to the other alert counters
    s = once(s, 'volatile long long ios_alert_wakes, ios_alert_waits, ios_alert_wait_timeouts;\n',
             'volatile long long ios_alert_wakes, ios_alert_waits, ios_alert_wait_timeouts;\n'
             '/* MADEIRA alert-rescue (ios_alert_rescue.h): rescues = INFINITE waits that\n'
             ' * returned because their word moved with no alert (a lost wake). */\n'
             'volatile long long ios_alert_rescues;\n'
             'static int ios_alert_rescue_ms_v = -1;\n'
             'static int ios_alert_rescue_ms(void)\n'
             '{\n'
             '    if (ios_alert_rescue_ms_v < 0)\n'
             '    {\n'
             '        ios_alert_rescue_ms_v = ios_ar_cfg_ms();\n'
             '        fprintf( stderr, "[alert-rescue] INFINITE alert waits re-test their word every %d ms '
             '(madeira.cfg alert-rescue-ms, 0 = off)\\n", ios_alert_rescue_ms_v );\n'
             '    }\n'
             '    return ios_alert_rescue_ms_v;\n'
             '}\n',
             'alert counters')

    # snapshot the word when the thread parks (after the [waiters] registration)
    s = once(s,
             '            ios_alert_waiters[ios_wslot].addr = address ? address : (const void *)0x1;\n'
             '        }\n',
             '            ios_alert_waiters[ios_wslot].addr = address ? address : (const void *)0x1;\n'
             '        }\n'
             '        if (!timeout && ios_alert_rescue_ms())\n'
             '            ios_ar_have = ios_ar_probe( address, &ios_ar_snap );\n',
             'park registration')
    s = once(s,
             '        int ret;\n        int ios_wslot;\n',
             '        int ret;\n        int ios_wslot;\n'
             '        unsigned long long ios_ar_snap = 0;\n        int ios_ar_have = 0;\n',
             'wait locals')

    # sleep INFINITE waits in slices; at the end of a slice, re-test the word
    s = once(s,
             '            else\n'
             '                ret = futex_wait( futex, 0, NULL );\n'
             '            ios_woke = mach_absolute_time();   /* ml1122 */\n',
             '            else if (ios_ar_have)\n'
             '            {\n'
             '                int ms = ios_alert_rescue_ms();\n'
             '                struct timespec slice;\n'
             '                slice.tv_sec = ms / 1000;\n'
             '                slice.tv_nsec = (long)(ms % 1000) * 1000000;\n'
             '                ret = futex_wait( futex, 0, &slice );\n'
             '                if (ret == -1 && errno == ETIMEDOUT)\n'
             '                {\n'
             '                    if (*(volatile LONG *)futex) continue;   /* alert raced the slice end: consume it */\n'
             '                    if (!ios_ar_should_rescue( ios_ar_have, ios_ar_snap, address )) continue;\n'
             '                    {\n'
             '                        long long n = __sync_add_and_fetch( &ios_alert_rescues, 1 );\n'
             '                        if (n <= 16 || !(n % 1024))\n'
             '                            dprintf( 2, "[alert-rescue] #%lld tid=%04x addr=%p: the wait word moved with no '
             'alert (a lost wake) -- returning so the caller re-tests it\\n", n,\n'
             '                                     (int)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread, address );\n'
             '                    }\n'
             '                    if (ios_wslot >= 0) ios_alert_waiters[ios_wslot].addr = NULL;\n'
             '                    return STATUS_ALERTED;\n'
             '                }\n'
             '            }\n'
             '            else\n'
             '                ret = futex_wait( futex, 0, NULL );\n'
             '            ios_woke = mach_absolute_time();   /* ml1122 */\n',
             'futex wait')

    # --- 2. [waiters] summary: name the main thread, count rescues
    s = once(s,
             '    dprintf( 2, "[waiters] parked=%d over60s=%d rev=ml444\\n", parked, over );\n',
             '    dprintf( 2, "[waiters] parked=%d over60s=%d main_over60s=%d rescued=%lld rev=ml444+rescue%s\\n",\n'
             '             parked, over, main_over, (long long)ios_alert_rescues,\n'
             '             over && !main_over ? " (no game main thread among them: idle workers, not a hang by itself)" :\n'
             '             main_over ? " <-- the GAME MAIN THREAD is parked on an INFINITE wait" : "" );\n',
             'waiters summary')
    s = once(s,
             '    int i, parked = 0, over = 0, shown = 0;\n    NtQuerySystemTime( &now );\n',
             '    int i, parked = 0, over = 0, shown = 0, main_over = 0;\n'
             '    unsigned int main_tid = 0;\n'
             '    {\n'
             '        extern uintptr_t ios_srv_game_teb;   /* server_ios.c: the main thread of the last process started */\n'
             '        if (ios_srv_game_teb)\n'
             '            main_tid = (unsigned int)(ULONG_PTR)((TEB *)ios_srv_game_teb)->ClientId.UniqueThread;\n'
             '    }\n'
             '    NtQuerySystemTime( &now );\n',
             'waiters locals')
    s = once(s,
             '        if ((now.QuadPart - (LONGLONG)ios_alert_waiters[i].since) / 10000000 >= 60) over++;\n',
             '        if ((now.QuadPart - (LONGLONG)ios_alert_waiters[i].since) / 10000000 >= 60)\n'
             '        {\n'
             '            over++;\n'
             '            if (main_tid && (unsigned int)(ULONG_PTR)ios_alert_waiters[i].tid == main_tid) main_over++;\n'
             '        }\n',
             'waiters count')
    s = once(s,
             '        dprintf( 2, "[waiters]  tid=%04x addr=%p age=%ds %s w0=%08x w1=%08x\\n",\n'
             '                 (int)(ULONG_PTR)tid, a, (int)age,\n'
             '                 ios_alert_waiters[i].inf ? "INF" : "TMO", w0, w1 );\n',
             '        dprintf( 2, "[waiters]  tid=%04x addr=%p age=%ds %s w0=%08x w1=%08x%s\\n",\n'
             '                 (int)(ULONG_PTR)tid, a, (int)age,\n'
             '                 ios_alert_waiters[i].inf ? "INF" : "TMO", w0, w1,\n'
             '                 main_tid && (unsigned int)(ULONG_PTR)tid == main_tid ? " MAIN" : "" );\n',
             'waiters row')
    return s


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src = open(sys.argv[1], encoding='utf-8', errors='surrogateescape').read()
    out = patch(src)
    with open(sys.argv[2], 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write('/* GENERATED by build/ntdll-unix/patch_sync_ios.py from wine/dlls/ntdll/unix/sync.c -- do not edit */\n')
        f.write('#line 1 "%s"\n' % os.path.abspath(sys.argv[1]))
        f.write(out)


if __name__ == '__main__':
    main()
