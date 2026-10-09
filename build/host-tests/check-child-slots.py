#!/usr/bin/env python3
"""Launcher child tracking (build/ntdll-unix/process_ios.c) without Wine.

A launcher that starts the game and exits keeps the session while the game
runs (WineProcessBridge.m waits on madeira_live_game_children). That wait was
opt-in because a child that ended through ExitProcess on a worker thread never
released its slot, and the session then never ended. This test compiles the
production slot code (cut from process_ios.c between its own markers) and
checks the cases that decide whether the wait can be on by default:
  - a child is counted while it runs, and helpers / crash reporters are not;
  - the exit path (madeira_child_socket_closed, called by server_ios.c's
    process_exit_wrapper from whichever thread exits) frees the slot;
  - the boot thread's late release cannot free a slot that another child has
    taken since (generation check);
  - a child that never registered its master socket stops counting after
    IOS_CHILD_BOOT_TIMEOUT, a registered one keeps counting;
  - max_age limits which children start a wait;
and that server_ios.c and WineProcessBridge.m still make the calls this relies
on. Needs python3 and a C compiler (AddressSanitizer/UBSan when available).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
proc = (root / 'build/ntdll-unix/process_ios.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()

failures = []
def check(cond, what):
    if not cond:
        failures.append(what)
        print('FAIL:', what)

start = proc.index('#define IOS_CHILD_SLOTS')
end_fn = proc.index('int madeira_live_game_children')
end = proc.index('\n}\n', end_fn) + 3
code = proc[start:end]
# the clock is the test's
code = re.sub(r'static double ios_child_now\(void\)\n\{.*?\n\}\n', 'static double ios_child_now(void) { return fake_now; }\n',
              code, count=1, flags=re.S)
check('fake_now' in code, 'ios_child_now was replaced by the test clock')

harness = r'''
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static double fake_now = 1000.0;
void madeira_child_socket_registered( int fd );
void madeira_child_socket_closed( int fd );
int madeira_live_game_children( char *buf, int len, double max_age );
@CODE@
static UNICODE_STRING us(const char *s, WCHAR *store)
{
    UNICODE_STRING u; size_t i, n = strlen(s);
    for (i = 0; i < n; i++) store[i] = (unsigned char)s[i];
    u.Length = u.MaximumLength = (unsigned short)(n * 2); u.Buffer = store; return u;
}
static int fails;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL: %s\n", m); fails++; } } while (0)
int main(void)
{
    WCHAR st[256]; char names[256]; unsigned g1 = 0, g2 = 0, g3 = 0; int s1, s2, s3, n;
    UNICODE_STRING game = us("C:\\Games\\GTA\\GTA5_Enhanced.exe", st);
    s1 = ios_child_slot_take(&game, 40, &g1);
    madeira_child_socket_registered(40);
    WCHAR st2[256]; UNICODE_STRING crs = us("C:\\Games\\GTA\\crashpad_handler.exe", st2);
    s2 = ios_child_slot_take(&crs, 41, &g2);
    madeira_child_socket_registered(41);
    n = madeira_live_game_children(names, sizeof names, 60.0);
    CHECK(n == 1 && strstr(names, "gta5_enhanced.exe"), "the game child counts, the crash handler does not");

    /* the game ends from a worker thread: only the exit path runs */
    madeira_child_socket_closed(40);
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 0, "the exit path frees the slot (worker-thread ExitProcess)");

    /* another child takes a slot, then the first child's boot thread releases late */
    WCHAR st3[256]; UNICODE_STRING next = us("C:\\Games\\Game.exe", st3);
    s3 = ios_child_slot_take(&next, 40, &g3);  /* fd number reused */
    madeira_child_socket_registered(40);
    CHECK(g3 != g1, "every take gets a new generation");
    ios_child_slot_release(s1, g1);
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 1, "a late release with an old generation frees nothing");
    ios_child_slot_release(s3, g3);
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 0, "the owner's release frees its slot");
    ios_child_slot_release(s3, g3);
    madeira_child_socket_closed(40);
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 0, "double release / close is harmless");

    /* a child that never boots */
    WCHAR st4[256]; UNICODE_STRING stuck = us("C:\\Games\\Stuck.exe", st4);
    unsigned g4; int s4 = ios_child_slot_take(&stuck, 50, &g4);
    WCHAR st5[256]; UNICODE_STRING slow = us("C:\\Games\\Booted.exe", st5);
    unsigned g5; int s5 = ios_child_slot_take(&slow, 51, &g5);
    madeira_child_socket_registered(51);
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 2, "both count while booting");
    fake_now += 121.0;
    n = madeira_live_game_children(names, sizeof names, -1.0);
    CHECK(n == 1 && strstr(names, "booted.exe") && !strstr(names, "stuck.exe"),
          "an unregistered child stops counting after the boot timeout, a registered one keeps counting");
    CHECK(madeira_live_game_children(NULL, 0, 60.0) == 0, "max_age: an old child does not start a wait");
    ios_child_slot_release(s4, g4); ios_child_slot_release(s5, g5);

    /* slots are reusable after many launches */
    for (int i = 0; i < 1000; i++) {
        unsigned g; int s = ios_child_slot_take(&next, 60 + (i % 7), &g);
        CHECK(s >= 0, "a slot is free"); if (s < 0) break;
        if (i % 2) madeira_child_socket_closed(60 + (i % 7)); else ios_child_slot_release(s, g);
    }
    CHECK(madeira_live_game_children(NULL, 0, -1.0) == 0, "no slot leaks across 1000 children");
    (void)s2; (void)g2;
    return fails ? 1 : 0;
}
'''.replace('@CODE@', code)

with tempfile.TemporaryDirectory() as td:
    c = Path(td) / 'slots.c'
    c.write_text(harness)
    exe = Path(td) / 'slots'
    cc = os.environ.get('CC', 'cc')
    built = False
    for extra in (['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g'], ['-O1']):
        r = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wno-unused-function', *extra, str(c), '-o', str(exe), '-lpthread'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print('built with', 'AddressSanitizer/UBSan' if 'address' in extra[0] else 'no sanitizers')
            built = True
            break
    if not built:
        print(r.stderr)
        sys.exit('FAIL: the slot code did not compile')
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    check(r.returncode == 0, 'slot scenarios')

# the callers this relies on
m = re.search(r'void process_exit_wrapper\( int status \)\n\{.*?\n\}\n', server, re.S)
check(m and 'madeira_child_socket_closed( ios_proc_sockets[i].fd )' in m.group(0)
      and m.group(0).index('madeira_child_socket_closed') < m.group(0).index('close( ios_proc_sockets[i].fd )'),
      'process_exit_wrapper releases the child slot before closing the socket')
m = re.search(r'static void ios_register_proc_socket\(void \*peb_id, int fd\)\n\{.*?\n\}\n', server, re.S)
check(m and 'madeira_child_socket_registered( fd )' in m.group(0), 'registration marks the child booted')
check('ios_child_slot_take( &params->ImagePathName, args->socketfd, &args->slot_gen )' in proc,
      'spawn_process keys the slot by the socket the child registers')
check("wc && wc[0] == '0'" in bridge and 'MADEIRA_WAIT_CHILDREN_MAX_S' in bridge,
      'the child wait is on by default, MADEIRA_WAIT_CHILDREN=0 turns it off, and it can be capped')

if failures:
    sys.exit('FAIL: %d check(s)' % len(failures))
print('PASS: launcher children keep the session, every exit path frees its slot, and a stuck child cannot hold it')
