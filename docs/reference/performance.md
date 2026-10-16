# Performance: responsiveness, frame rate and input

Status: Phase 7, 2026-10-16. The user's LAN games on release 0.7 (`analysis/perf/user-night-game.log`, 80 minutes, 1920×1200 and 1366×768) were stuttery and less responsive than on Windows. Three workstreams cover it, each with its own section here: P7-A (the guest thread scheduler), P7-B (CPU hot paths, map loading) and P7-C (input and frame rate, below).

Labels: **[measured]** = output of the commands here (M4 Max, macOS 15.5, a 120 Hz ProMotion display, other workstreams running in parallel); **[static]** = read from the binaries; **[inferred]** = reasoning.

---

## Scheduling (P7-A)

The game's 14 threads run on one host core at a time: the kit's scheduler (kit `runtime/kernel32.cpp`, kit `docs/architecture.md` "The scheduler") hands one baton between them. This section measures what that cost the user's game, says what was wrong with how the baton moved, and what changed. Kit `feat/sched`, ee-macos `phase7/sched`.

| Report | Cause | Fix |
|---|---|---|
| A command (select citizens, click a resource) took about 3 s to show its yellow marker; the Mac "plays catch-up" with the host | The marker comes from the simulation, which runs the command in the CT the host put it in. The Mac's simulation thread ran its ticks in 1 ms slices between the renderer's and everybody else's, so a tick took 2-3 times its own CPU time; the game measured that and tuned its tick rate down, and in a LAN game the host slows to the slowest machine (lansession.md §8 item 4) while the Mac runs behind the host's CTs [inferred; §A.1, §A.2] | The scheduler follows the game's thread priorities as Windows does on one processor: the simulation is `THREAD_PRIORITY_TIME_CRITICAL` and now runs its ticks whole (§A.3) |
| Stutter; the camera stutters when objects come into view | A thread computing without an import call (the texture loader decoding a texture between `Lock` and `Unlock`, 14 ms) could not be interrupted; a woken thread waited for the round robin to reach it; every handoff woke all 14 threads | Preemption points in translated code, wake boosts, FIFO order, one wake-up per handoff (§A.3) |
| Big battles bring it almost to a halt | The guest has one core where Windows gives it three (§A.6) | Not solvable by scheduling alone; the design for parallel guest threads is §A.6 |
| An audio sink gap of 1,477 ms | Not the scheduler: the gap is between two pulls of the host's audio device (kit `host/audio/sdl_sink.cpp`), 3 late pulls in 81 minutes and none starved, at the display-mode changes [measured, the user's log] | - |

### A.1 The scheduler as it was [static]

- **Points where the baton could move:** blocking imports (waits, `Sleep`, a contended `EnterCriticalSection`), and every import call once the holder had had the baton for 1 ms. Guest code that made no import call kept it.
- **Who got it:** the next runnable thread by index, round robin. Priorities (`SetThreadPriority`) were accepted and ignored. A thread woken by an event, a timeout or the network waited for everyone ahead of it in index order to use their millisecond.
- **A handoff** was a `notify_all` on one condition variable: every parked guest thread woke, took the scheduler's mutex, saw it was not its turn and slept again.
- **Waits were evaluated** at the next scheduling pass, by whichever thread ran it - including threads that were not the holder.

### A.2 Measured before [measured]

Instrumentation: `RECOMP_SCHED_TRACE=FILE` (kit `runtime/sched_trace.h`; per thread and per 10 s window: holds, waits for the baton while ready, why the baton was given up, the longest stretch without a scheduling point, handoff cost, idle time, and the latency of a mouse press from the host's event to the game's first read of it). `app/ee_sched.cpp` names the threads by their `TSThread` body and adds the simulation's counters (PTs executed, the PT rate the server chose, the game's own measured PT time `EEPhysics+0xd8`).

The scenario: the user's autosave `AutoSave 2026.10.15 - 20h.57m.31s` (Huge map, 2 humans + 1 computer, late game, PT 170,000) loads in single player. The windowed app on Metal at 1920×1200, silent, behind other windows (`RECOMP_SCRIPT_BACKGROUND`), Single Player → Play Saved Game → Load, 30 s, then 25 rounds of a left click and a right click on the map 4 s apart, then 60 s idle. The driver is `heavy.py` (P7-A scratch tools; the save stays in `analysis/perf/saves/`).

The numbers are in the table of §A.4, column "0.7".

What the trace showed:

- **Handoffs were expensive and frequent:** 2,400 a second at 35-40 µs each, about 9% of the core, because every one woke every thread. On a busy host (another workstream's 16-job build, load average 35-44) a handoff took 200-270 µs on average and 4 ms at the 99th percentile; the game fell to 24 PT/s, a mouse press took up to **1.7 s** to reach the game, and one frame in 30 took over 100 ms (`before-2057`).
- **The simulation was sliced.** Its ticks ran in 1 ms pieces between the renderer's (45% of the core) and the others', so the tick time the game measures (`EEPhysics+0xd8`, used by `TuneForSlowCPU`, simparity.md §4.1) read 4-5 ms for under 2 ms of work, and the server chose 72-100 PT/s. In a LAN game the client's slow ticks are what slows the host (lansession.md §8 item 4: "Speed follows the slowest machine").
- **The longest stretches without a scheduling point** in game were 10-15 ms of guest code (the texture loader between `Lock` and `Unlock`, the simulation between two `operator new` calls) and 15 ms inside a shim (`GetDC` on the render thread). Nobody could get the baton meanwhile.
- **Thread priorities:** the game sets the simulation (`RTSPhysics::Main`) and the network client's receive thread to `THREAD_PRIORITY_TIME_CRITICAL` (15), and raises the texture loader to `ABOVE_NORMAL` while a game loads [measured: `SetThreadPriority` in the trace]. The old scheduler ignored all three.

### A.3 The scheduler now

Kit `docs/architecture.md` "The scheduler" and `runtime/README.md` "Threads" have the rules; in short:

- **Windows' order for one processor.** A ready thread's class is 8 + its priority offset (15 for time-critical); the higher class runs, ties go first-come. A thread woken from a wait is boosted by one for a quantum, the run thread by two when input arrives; a thread ready for 20 ms is raised to 15 for a quantum. The holder gives way at once to a higher class and after its 2 ms quantum to its own class. That is how Empire Earth was written to run in 2001: the simulation, time-critical, wakes on its timer, runs its ticks whole and sleeps; it tunes its tick rate to use 30% of the processor; the renderer and the main loop share the rest.
- **Preemption points in translated code.** `RECOMP_SAFEPOINT` at every function entry and loop head (10,955 loop heads in the EXE, 3,590 in the DLLs); a ticker thread raises a flag every millisecond, a thread made ready that outranks the holder raises it at once. It acts only in a thread's own guest code (not in code a shim called back, not under a mod hook).
- **One wake-up per handoff.** Each thread sleeps on its own condition variable; a thread that gave way while runnable spins 50 µs for the baton first. Guest threads run at QoS user-interactive.
- **Waits are granted by the holder only,** and `SetEvent`/`ReleaseSemaphore`/`ReleaseMutex` grant them on the spot, as Windows does.
- `RECOMP_SCHED_POLICY=rr` brings back the old order (with the new handoff), so one build can be compared both ways.

### A.4 Measured after [measured]

Three runs of the same scenario on a quiet host (load average 3-14): **0.7** is release 0.7's scheduler with the trace added (`ab2-before1`); **rr** is the new handoff, QoS and wait handling with the old round-robin order (`RECOMP_SCHED_POLICY=rr`, `ab1-rr1`); **new** is this change (`ab2-after1`). In-game 10 s windows, the load and the quit left out; frame intervals from the presenter's `RECOMP_FRAME_TIMINGS` over the last 185 s (the clicks and the idle minute).

| | 0.7 | rr | new |
|---|---:|---:|---:|
| handoffs per second | 2,397 | 2,293 | 1,110 |
| handoff cost: mean, worst window's p99 | 39.5 µs, 256 µs | 5.7 µs, 32 µs | 6.3 µs, 32 µs |
| nothing runnable (the core idle) | 10.8% | 22.4% | 25.2% |
| simulation: its measured tick time (`+0xd8`) | 4.3 ms | 2.1 ms | 1.7 ms |
| PT rate the server chose (mean) | 72 | 99 | 100 |
| PTs executed per second (median window) | 74 | 87 | 88 |
| simulation's share of the core | 26.4% | 21.5% | 21.4% |
| longest wait for the baton while ready: simulation | 14.6 ms | 14.3 ms | 4.2 ms |
| main thread (the UI: message loop, DirectInput reads) | 22.1 ms | - | 22.0 ms |
| input thread (`ISMouse`) | 19.4 ms | - | 22.0 ms |
| network (the engine's other time-critical `TSThread`, not yet named) | 15.3 ms | - | 10.3 ms |
| audio (Miles Scavenger; music intensity) | 12.7; 9.8 ms | - | 21.3; 10.4 ms |
| renderer | 15.6 ms | 14.6 ms | 22.1 ms |
| longest stretch without a scheduling point (any thread) | 14.6 ms | 14.6 ms | 4.6 ms |
| mouse press, host event → the game's first read: mean, max | 6.1 ms, 35.5 ms | 2.8 ms, 9.5 ms | **2.5 ms, 5.9 ms** |
| main loop passes per second (its `Sleep(1)` per pass) | 90-130 | 120-235 | 215-235 |
| frames per second | 36.7 | 35.4 | **41.5** |
| frame interval p50 / p90 / p99 | 25.2 / 46.4 / 60.4 ms | 25.4 / 48.9 / 59.0 ms | **18.1** / 42.7 / 59.2 ms |
| frame intervals under 12 ms; over 100 ms | 679; 8 | 747; 3 | 2,267; 4 |

(rr's longest waits for the main, input, network and audio threads are left out: that run still had the script's frame dumps, each a 130 ms stall, in two windows.)

What it shows:

- **The simulation runs its ticks whole.** Its wait for the baton is 0.01 ms on average and 4 ms at worst, so the tick time the game measures is the tick's own, 1.7 ms instead of 4.3, and the game tunes itself to 100-124 PT/s where 0.7 settled at 72. The rate still drops to 44 for a few seconds when a tick gets heavy (`+0xdc` rises), on both builds: the game's own tuning.
- **Handoffs cost a sixth and happen half as often**, and the core is idle a quarter of the time instead of a tenth: about 10% of a core given back.
- **No thread waits longer than the starvation limit** (20 ms plus a quantum) for the baton; 0.7's round robin happened to give similar worst cases here, but a ready thread had no bound at all, and the time-critical simulation could wait 15 ms for the renderer.
- **Input reaches the game in under 6 ms**, and the main loop makes twice the passes (it is boosted when it wakes from its `Sleep(1)`), which is what P7-C's held arrow keys depend on.
- **More frames and steadier ones:** the median frame interval falls from 25 ms to 18 ms and three times as many intervals are under 12 ms. The 99th percentile (59 ms) does not move: that is the core's limit, the renderer and a heavy tick on one core.
- **A busy host no longer collapses the game.** The 0.7 run made while another workstream's 16-job build ran (load average 35-44, `before-2057`) took 126 µs per handoff on average and 4 ms at the 99th percentile; it ran 24 PT/s, a mouse press took up to **1.7 s** to reach the game, and frames were 27 a second with a 229 ms 99th percentile. The new scheduler under the same kind of load (26-37, `ab1-after1`) kept 7-8 µs per handoff and a 32 µs 99th percentile, 122 PT/s and a 12 ms worst press. The guest threads' QoS (only the run thread had one before) and the single wake-up are what changed it.

**Map loading.** A Huge random map, headless (`ee_boot`, 800×600, `Game Options\Map Size` = 4, the registry's other options), the same machine minutes apart:

| | 0.7 | new |
|---|---:|---:|
| `EERandomMap::PaintTiles` (the game's own timing) | 15.58 s | **0.61 s** |
| `EERandomMap::CreateRandomMap` | 17.80 s | **1.77 s** |

P7-B found the loader thread computing in only a third of its samples, the rest spent waiting for the baton while the render thread drew the loading screen. The game raises the texture loader to `THREAD_PRIORITY_ABOVE_NORMAL` while it loads (§A.2): honouring that gives the loader the core whenever it wants it, and the renderer the gaps. On Windows the two ran on different cores.

### A.5 Determinism [measured]

- **`tools/test.py --simparity`** (its default scenario; `simparity-after`): the two Mac games agree with each other on all 7,085 PTs. Against legion's `win-as-1` they agree to PT 5616 and differ from 5617, after the second autosave landed one CT apart (after PT 4816 on the Mac, 4802 on Windows): exactly the result 0.7 had (simparity.md §9.3, `as3`), the single-player save timing that multiplayer does not have. The first save, loaded (run 3), agrees with Windows' load of the Mac's save (`win-load-mac-2423`) on all 4,791 common PTs. Every autosave was written.
- **The M1 replay** (`tools/m1/run_matrix.py --cases A06,C05,S00b --no-fetch --build both`, on bundles and linked builds translated with the preemption points): 27 of 27 packs pass on both builds.
- **`--boot`** (11 milestones, 14 threads, render at CW 007f, clean exit), **`--menus`** (5 of 5) and **`--lobby`** (2 of 2) pass.
- The kit's suites: `tools/test.py` (487 passed), `--native` (41 of 41), `check_repo`, `check_game_literals`, `format.py`. `runtime_tests` gained two scheduling checks (a time-critical thread runs first; a thread computing without an import gives the baton back at a preemption point, after 2.8 ms) and passes under both policies; its two `SetTimer` checks across a clock wrap fail on `ee` 614be22 too.

Not measured: a LAN game against legion (the host's lag indicator, the Mac's CT turn rate). Legion was in use. That is the check that would confirm the 3-second command delay is gone in multiplayer (the table at the top of this section says why it should be).

### A.6 What one processor cannot do: parallel guest threads (design, not built)

Everything above shares **one** core between the game's threads. On Windows the simulation, the renderer and the main loop run on three. In this save the renderer holds the baton about half the time and the simulation a quarter, with a fifth idle: one M4 core is enough for an idle late game at 1920×1200 [measured, §A.4]. A big battle needs more simulation per tick; the game then lowers its tick rate (TuneForSlowCPU, simparity.md §4.1), and in a LAN game the host slows everybody to the Mac (lansession.md §8, item 4). The scheduler can decide who waits; only more cores make the waiting go away. That is option (b), sketched here because it is too big for this pass.

**The idea: a big lock for the runtime, not for the guest.** Translated code runs on as many cores as there are runnable guest threads. Every import call takes one process-wide **runtime lock** for the length of the shim and gives it up when the shim calls back into guest code (`guest_call`) or blocks (a wait, `Sleep`). That keeps today's guarantee for host code exactly: host code between two scheduling points runs alone, which is what the baton gives the shims now, and a callback can already be interleaved at its import calls today. The baton, the ticker and the safepoints go; the per-thread condition variables, the wait descriptors and the class order stay, as the lock's wait queue and the wake-up order.

**What has to change, in rising order of effort:**

1. **Locked instructions as atomics.** The translator emits `LOCK CMPXCHG`, `LOCK XADD`, `LOCK INC/DEC/ADD/OR/AND`, `XCHG` with memory (implicitly locked) and `CMPXCHG8B` as plain read-modify-write today. They become `__atomic_*` builtins on `g_mem + addr` (sequentially consistent: an x86 locked instruction is a full barrier). `InterlockedIncrement`/`Decrement`/`Exchange`/`CompareExchange` are imports and become atomics in the shim. The engine uses `InterlockedIncrement`/`Decrement` (DB imports).
2. **Memory ordering.** x86 is TSO; arm64 is not, and Apple's TSO mode is not available to an ordinary process. Two choices:
   - *Barriers at synchronisation only*: the locked instructions above (full barriers), and the runtime lock's acquire/release at every import call, which covers critical sections, events, waits and the heap. The world is written only by the simulation thread under its own lock (simparity.md §2.3) and read by the renderer under it, through `EnterCriticalSection`. What it does not cover is guest code that publishes data through a plain flag (the DirectInput threads' "ready" field, boot.md §5): on arm64 a reader can see the flag before the data. Rare, but it would be a new class of bug that Windows never shows.
   - *Acquire/release for every guest access* (`LDAPR`/`STLR`): TSO-equivalent for practical purposes, at a cost to be measured (Apple cores are fast at both; a guess is 10-20% of translated code). A translator switch makes the two comparable on the same build.
3. **Shims that reach guest memory or state without an import.** The runtime entry points generated code calls directly (`bundle.py`'s list of undefined symbols: about 25, e.g. `recomp_call`, the x87 helpers, `recomp_seh_*`, the mod hook slots, the profiler) must be thread-safe or take the lock. The module registry and dispatch tables are read-only after load; the mod hook dispatch (Lua is single-threaded) must take the lock; SEH and the profiler are per thread already.
4. **The rest of the host.** Host threads that signal guest events (`guest_event_signal_from_host`) or apply input (`deliver_pending_input`) take the runtime lock instead of waiting for a checkpoint. The presenter only consumes sealed frames (architecture.md) and is unaffected.
5. **Determinism.** Unchanged in kind: Windows runs these threads in parallel, the simulation is single-threaded and lockstep, and the parity streams were taken from parallel Windows runs. `tools/test.py --simparity` and the LAN checksum comparison are the gates, as here.

**Risk and size.** (1) and the lock are a few days. (2) is the risky part: the first variant can introduce bugs that show only on arm64 and only sometimes; the second costs speed everywhere. (3)-(4) are an audit with a finite list. The likely first step: run only the render thread outside the baton (it talks to the rest through critical sections and Direct3D calls, all imports), measure, and widen from there.

### A.7 Left to do

- **The LAN check** (§A.5): host on legion, the Mac joining, the 20:57 save's state or a long game; watch legion's lag indicator, the Mac's CT acknowledgements and the command delay.
- **The Windows side of §A.2** (legion 0070, queued with 0075 run 1): per-thread CPU time of the same save (`windows/frida/thread_cpu.js`). It says how far one core is from the three Windows uses, i.e. how soon §A.6 is needed.
- **Multimedia timers fire only from the message pump.** The simulation's pacing timer is a `timeSetEvent` in event mode (engine 0x1005ebc0: `TIME_CALLBACK_EVENT_SET`, periodic), and the kit signals it in `host_pump_timers`, which runs only inside `PeekMessage`/`GetMessage`; a late pump also moves the next due time (`next_due = now + period`). An event-mode timer needs no guest thread, so the scheduler could fire it at its due time like a wait's deadline, as Windows' timer thread does. Not done: in this save the simulation kept up regardless.
- **Long shims hold the baton**: `_AIL_set_named_sample_file` 128 ms during the load (main thread), `GetDC` on the render thread 3-15 ms. Hot paths, P7-B's.
- **Firewall prompts.** macOS asks again for every rebuilt ad-hoc-signed binary that opens a port; a developer `ee_boot` signed with a stable self-signed identity would keep the approval across builds.
