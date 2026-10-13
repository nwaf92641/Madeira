# Engine stalls behind a black screen (all games, not one title)

A game whose process is alive but never presents a frame showed these lines in
its log. This page says what each one is in the code, what was wrong, what was
changed, and what still needs an iPad.

| Line in the log | What it is | Verdict |
|---|---|---|
| `[Wine WATCHDOG 2s] ...` | `server_init_process_done` (build/ntdll-unix/server_ios.c) samples the game thread's registers once, 2 s after start, on every launch. | **Real bug, fixed.** The block logged while the thread was suspended. |
| `[waiters] parked=1 over60s=1` | The [waiters] monitor (wine/dlls/ntdll/unix/sync.c, `ios_alert_waiter_dump`): threads in an INFINITE `NtWaitForAlertByThreadId` for over 60 s. | One idle worker looks exactly like this. Now says whether the game's main thread is among them; lost wakes are rescued. |
| `ml990: ... refusing to advertise address space this device cannot map` | `ios_clamp_user_space_limit` (virtual_ios.c): programs are told the device's real VA ceiling instead of 128 TB. | Informational, correct. Nothing is refused. |
| `... <-- iOS REFUSED A FREE ADDRESS` | `[va-scan]` detail for one fixed-address attempt (virtual_ios.c). | **Wording bug, fixed.** It looked at the first page only. |
| `ml1036: no hole fits a 896MB pool -- SHRINKING to 624MB` | StikJITHelper sizes the RX JIT pool to the largest hole left at launch. | **Real weakness, fixed.** The image-load placeholder had one candidate place only. |
| `[file-fail] ... fex-emu\AppConfig\INSIDE.exe.json`, `...\fex-emu\Config.json` | FEX (ARM64EC/WoW64 build) looking for its optional config files (FEX/Source/Common/Config.cpp, FEXCore Config.cpp `GetApplicationConfig`). | Expected answer (STATUS_OBJECT_NAME_NOT_FOUND = use defaults). Bounded to 64 lines. Not an error. |

## 1. The watchdog left the game thread suspended (root cause, every game)

`sample_thread` did `thread_suspend(game)`, then `wine_log_write(...)` six
times, then `thread_resume(game)`. `wine_log_write` takes `g_wine_log_mutex`,
writes a stdio `FILE` (its lock), and calls `wine_ui_log`, which crosses into
Swift (`String(cString:)`, `LogStore.handleRawLine`): the malloc zone lock and
LogStore's lock. At t+2 s a game is loading and is very often inside one of those
itself (it logs and allocates constantly). If it was, the watchdog blocked on
that lock forever, `thread_resume` was never reached, and the game thread stayed
suspended for the rest of the session: process alive, nothing more from that
thread, `NO FRAME PRESENTED`. Random, game-independent, always at t+2 s.

Every other sampler in server_ios.c ([thread-sample], the RIP profile, [PROF])
already copies state, resumes, then formats. The watchdog now does the same:
between suspend and resume only `thread_get_state`, plain loads of globals and
`vm_read_overwrite` (a Mach trap) run. The log gets a new last line:
`[Wine WATCHDOG 2s] resumed the sampled thread before logging (kr=0) -- a one-shot register sample at t+2s, not an error`.

`build/host-tests/check-engine-stalls.py` scans every `thread_suspend(X)` ...
`thread_resume(X)` window in build/ntdll-unix and fails on any logging or
allocation call inside one (it fails on the old server_ios.c).

## 2. Lost wakes left threads parked forever

Every Win32 lock in Wine parks in `NtWaitForAlertByThreadId` through
`RtlWaitOnAddress`, and the waker finds the waiter in `futex_queues`, a table
private to each copy of the PE ntdll. Madeira runs several PE ntdll copies in
one iOS process; a lock word shared across copies can be released by a thread
whose copy never recorded the waiter. The value changes, no alert is sent, the
waiter sleeps forever (ml439..ml447 measured the parked crowd receiving zero
alerts).

`build/ntdll-unix/patch_sync_ios.py` (applied at build time to a copy of the
submodule's unix/sync.c; the submodule is not modified) sleeps INFINITE waits in
slices (`madeira.cfg alert-rescue-ms`, default 1000, 0 = old behaviour). At the
end of a slice the word at the wait address is compared with its value at park:
unchanged, keep sleeping (idle workers never return early); changed, return
`STATUS_ALERTED`. That is a spurious wake, which every caller tolerates (Wine's
SRW, critical-section and barrier code re-test in a loop; Windows documents
spurious returns for WaitOnAddress and SleepConditionVariable). Logged as
`[alert-rescue] #N tid=... addr=...: the wait word moved with no alert (a lost wake)`.

The [waiters] summary now reads
`[waiters] parked=N over60s=N main_over60s=M rescued=R ...`, and the per-thread
rows end in `MAIN` for the game's main thread. `main_over60s=0` means idle
workers; `main_over60s>0` is reported as an **engine-stall** problem.

Tests: `build/host-tests/check-alert-rescue.py` (patch applies, generated code
has the slice/re-test/return; two-thread run: a lost wake returns within two
slices, an idle wait never returns early).

Also: `build/ntdll-unix/build.sh` now deletes a unit's object before compiling
it. `obj/` is restored from the CI cache, so a unit that failed to compile used
to ship last run's object silently.

## 3. "iOS REFUSED A FREE ADDRESS" accused iOS wrongly

The scan recorded the first failing address but not the length it tried, and
described it with the single-address form, which says "REFUSED A FREE ADDRESS"
whenever the first page is free, even when the rest of the range is occupied
(the bug `ios_va_describe_range` was written to retire). The length is now
recorded and the range form used: `PARTIALLY OCCUPIED`, `OCCUPIED`, or
`FREE RANGE` (only then is iOS really refusing). The scan itself always moved on
to the next candidate; only `[va-scan] FAILED ... STATUS_NO_MEMORY` is an
allocation that failed, and the report now treats that line as a problem.

## 4. JIT pool shrunk to 624 MB

`madeira_early_va_claim` (JITAllocator.c, a constructor before main) held a
placeholder only at `0x148000000`, right above the executable window. One early
mapping there (ml1135: a ~31 MB region at ~0x157d00000) meant no placeholder,
and the pool was sized from whatever survived until launch. Now, when that run
is taken, the constructor holds the largest legal hole instead
(`JITPoolPlacement.h`: above 0x119000000, below the guest window 0x7000000000,
outside the executable window, 256..1024 MB, 16 MB aligned, `VM_FLAGS_FIXED`,
`PROT_NONE`). StikJITHelper already releases the placeholder wherever it is and
plugs lower holes, and logs `[jit-pool-placement] ... held the largest legal hole`.
The shrink was real VA scarcity, not a false refusal; 624 MB is above the
~500 MB below which FEX's code cache starts rolling over, so it caused stutters
at worst, not a black screen. Test: `build/host-tests/check-jit-pool-placement.py`.

## 5. FEX config probes

Not changed: FEX asks for `Config.json` and `AppConfig\<program>.json` and uses
defaults when they are absent, which is correct. The report lists them as a
note ("FEX looking for its optional configuration ... Not an error").

## What last-launch.txt shows now

Problems keep their meaning; the lines above move to a new section:

```
Engine notes (logged, but not the cause of a black screen by themselves):
- wine-watchdog       one-shot register sample at t+2 s ...
- va-ceiling          real address-space ceiling instead of 128 TB ...
- jit-pool-size       pool sized to the largest hole ...
- va-scan-detail      one fixed-address attempt; only [va-scan] FAILED matters
- fex-config-probe    FEX optional configuration, defaults used
- waiters             parked threads, none of them the main thread ...
- alert-rescue        a lost wake was rescued
```

and `Verdict: Engine stall (game thread parked) [engine-stall]` only when the
game's main thread is among the >60 s parkers. The JSON has a `notes` array.

## Needs an iPad (not verifiable here)

1. A game that used to stay black: does `[Wine WATCHDOG 2s] resumed the sampled thread`
   appear, and does the game now get past t+2 s? (If it was hit by the watchdog
   deadlock, it now starts.)
2. `[alert-rescue]` lines: how many, on which address, and whether the game
   presents after them.
3. `[waiters] ... main_over60s=` on a game that still stays black: 0 means the
   main thread is not parked on a lock (look at the D3D/swapchain stages and
   `[madeira-main-hop]`); >0 names the lock word in the MAIN row.
4. `[jit-pool-placement]` and the pool size on a launch where the run above the
   window was taken.
5. The DXMT PE part of patches/dxmt-ios-no-hang.patch still needs the d3d11.dll
   rebuild on a Mac (docs/D3D11_DEVICE_STALL.md).
