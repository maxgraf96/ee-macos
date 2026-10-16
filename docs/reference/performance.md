# Performance: responsiveness, frame rate and input

Status: Phase 7, 2026-10-16. The user's LAN games on release 0.7 (`analysis/perf/user-night-game.log`, 80 minutes, 1920×1200 and 1366×768) were stuttery and less responsive than on Windows. Three workstreams cover it, each with its own section here: P7-A (the guest thread scheduler), P7-B (CPU hot paths, map loading) and P7-C (input and frame rate, below).

Labels: **[measured]** = output of the commands here (M4 Max, macOS 15.5, a 120 Hz ProMotion display, other workstreams running in parallel); **[static]** = read from the binaries; **[inferred]** = reasoning.

---

## Scheduling (P7-A)

The game's 14 threads run on one host core at a time: the kit's scheduler (kit `runtime/kernel32.cpp`, kit `docs/architecture.md` "The scheduler") hands one baton between them. This section measures what that cost the user's game, says what was wrong with how the baton moved, and what changed. Kit `feat/sched`, ee-macos `phase7/sched`.

The Windows reference (legion 0070/0075, the same save, Core Ultra 9 275HX, 1920×1080): the whole game uses **0.6 of a core**, nearly all of it the render thread (0.5 core at about 100 frames a second; `TSThread` → EXE+0x10fee6); the simulation uses 13 ms a second, the main thread about 20 ms, the other 45 threads together about 0.1 core [measured, `win-perf-load2057/threads.csv`]. So one core is enough in principle: what the Mac lost was in how the baton moved.

| Report | Cause | Fix | Before → after [measured] |
|---|---|---|---|
| The camera moves unevenly, in bursts, while scrolling | The main loop (one camera step per pass) and the render thread shared the baton by round robin; the loop got 57-198 passes a second (Windows 616) and frames found the camera where the last one left it, then far beyond | Windows' order with a quantum carried over preemptions, one wake-up per handoff, no boost for a `Sleep` that ran out (§A.3) | still frames 15% → 0%; 47% → 2-6% of frames late (§A.4.2) |
| A Huge map takes 20 s to generate (P7-B) | `PaintTiles` takes and drops the engine's `TSCriticalSection` per tile; every `LeaveCriticalSection` reached a checkpoint that gave the 1 ms-old baton to the render thread, which drew a loading-screen frame | Leaving a critical section gives the baton up only to a waiter of a higher priority; the game's own `ABOVE_NORMAL` for its loader is honoured (§A.3) | `PaintTiles` 20.1 s → 0.57 s in the app (Windows 0.14 s) (§A.4.3) |
| A command took about 3 s to show its yellow marker; the Mac "plays catch-up" | Not measured in a LAN game (§A.5). The simulation ticks inside 1 ms slices between the renderer's; handoffs at 35-40 µs, ten times that on a busy Mac; mouse presses that reached the game up to 1.7 s late when the host was loaded [measured, §A.2]; in a LAN game the host waits for the slowest machine's CT acknowledgements (lansession.md §8 item 4) [inferred] | The simulation (`TIME_CRITICAL`) runs its ticks whole; handoffs cost 6 µs whatever the host's load; guest threads at QoS user-interactive (§A.3) | a press reaches the game in 1.7-2.5 ms on average, 6 ms at worst (§A.4.1) |
| Big battles bring it almost to a halt | Not measured: no save of a battle in view. The same causes as above; in single player the Mac also ticks 3-5 times as often as Windows (§A.7) | As above | - |
| The run thread uses a quarter to half a core while the game is idle (P7-B's profile) | SDL waits only in whole milliseconds, so it came straight back from the fraction of one a `Sleep(1)` leaves, and the scheduler handed it the rest again and again: a spin on AppKit's event loop | The runtime sleeps that fraction on the thread's condition variable; the SDL host waits it on the run loop, which input ends (§A.8) | the run thread 22-47% → 2% of a core in the idle game; the process's energy estimate 4.6 → 2.0 W (§A.8) |
| An audio sink gap of 1,477 ms | Not the scheduler: the gap is between two pulls of the host's audio device (kit `host/audio/sdl_sink.cpp`), 3 late pulls in 81 minutes and none starved, at the display-mode changes [measured, the user's log] | - | - |

### A.1 The scheduler as it was [static]

- **Points where the baton could move:** blocking imports (waits, `Sleep`, a contended `EnterCriticalSection`), and every import call once the holder had had the baton for 1 ms. Guest code that made no import call kept it.
- **Who got it:** the next runnable thread by index, round robin. Priorities (`SetThreadPriority`) were accepted and ignored. A thread woken by an event, a timeout or the network waited for everyone ahead of it in index order to use their millisecond.
- **A handoff** was a `notify_all` on one condition variable: every parked guest thread woke, took the scheduler's mutex, saw it was not its turn and slept again.
- **Waits were evaluated** at the next scheduling pass, by whichever thread ran it - including threads that were not the holder.
- **Every import call was a place to give the baton away**, `LeaveCriticalSection`, `_controlfp` and `TlsGetValue` included: once the holder had had it for a millisecond, the next import call passed it on, whether or not anything more urgent was ready. A loop that takes and drops a critical section (P7-B: `PaintTiles`, EXE 0x4d579a/0x4d5acb into engine `TSCriticalSection` 0x1005ecc4/0x1005ece2) handed the baton to the render thread once per millisecond of its work, and the loader computed in 5-8% of its samples.

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

- **Windows' order for one processor.** A ready thread's class is 8 + its priority offset (15 for time-critical); the higher class runs, ties go first-come. A thread whose wait is satisfied (an event, a semaphore, a socket) is boosted by one for a quantum, the run thread by two when input arrives; a timeout (a `Sleep` over) and a critical section boost nothing, as on Windows. A thread ready for 20 ms is raised to 15 for a quantum. The holder gives way at once to a higher class and after its 2 ms quantum to its own class; a preempted thread keeps what was left of its quantum. That is how Empire Earth was written to run in 2001: the simulation, time-critical, wakes on its timer, runs its ticks whole and sleeps; the renderer and the main loop share the rest.
- **Cheap imports do not give the baton away.** An import call is a checkpoint, but it moves the baton only when the quantum is up, a timed wait is due or a thread that outranks the holder has become ready. `LeaveCriticalSection` releases and keeps running (its waiter takes the section at the next pass in which it is free) unless the waiter outranks the leaver, as the simulation does when it waits for its world lock. An uncontended `EnterCriticalSection`, `SetEvent`, `ResetEvent`, `ReleaseSemaphore`, `ReleaseMutex`, `InterlockedXxx`, `_controlfp`, `TlsGetValue` never block and never yield by themselves.
- **Preemption points in translated code.** `RECOMP_SAFEPOINT` at every function entry and loop head (10,955 loop heads in the EXE, 3,590 in the DLLs); a ticker thread raises a flag every millisecond, a thread made ready that outranks the holder raises it at once. It acts only in a thread's own guest code (not in code a shim called back, not under a mod hook).
- **One wake-up per handoff.** Each thread sleeps on its own condition variable; a thread that gave way while runnable spins 50 µs for the baton first. Guest threads run at QoS user-interactive.
- **Waits are granted by the holder only,** and `SetEvent`/`ReleaseSemaphore`/`ReleaseMutex` grant them on the spot, as Windows does.
- `RECOMP_SCHED_POLICY=rr` brings back the old order (with the new handoff), so one build can be compared both ways; `RECOMP_SCHED_SLEEP_BOOST=1` boosts timeouts again.

### A.4 Measured after [measured]

All on the user's 20:57 save in the windowed app on Metal, 1920×1200, behind other windows, on this M4 Max with other workstreams' builds running (load averages noted where they mattered). **0.7** is release 0.7's scheduler; **rr** the new handoff, QoS, waits and critical sections with the old round-robin order (`RECOMP_SCHED_POLICY=rr`, the same build as **new**); **new** this change.

#### A.4.1 Clicks and the idle game (no frame limit, `heavy.py`)

25 rounds of a left and a right click on the map 4 s apart, then 60 s idle. In-game 10 s windows, the load and the quit left out; frame intervals from the presenter's `RECOMP_FRAME_TIMINGS` over the last 185 s. A build from before the critical-section, timeout and quantum changes (`ab2-*`, `ab1-rr1`); the last column is the final build with P7-C's 60 fps limit (`clicks-final`).

| | 0.7 | rr | new | new, 60 cap |
|---|---:|---:|---:|---:|
| handoffs per second | 2,397 | 2,293 | 1,110 | 1,431 |
| handoff cost: mean, worst window's p99 | 39.5 µs, 256 µs | 5.7 µs, 32 µs | 6.3 µs, 32 µs | 6.3 µs, 32 µs |
| nothing runnable (the core idle) | 10.8% | 22.4% | 25.2% | 51.0% |
| simulation: its measured tick time (`+0xd8`) | 4.3 ms | 2.1 ms | 1.7 ms | 1.4 ms |
| PT rate the server chose (mean) | 72 | 99 | 100 | 73 |
| simulation's share of the core | 26.4% | 21.5% | 21.4% | 15.6% |
| renderer's share of the core | 45.0% | 51.8% | 49.9% | 27.2% |
| longest wait for the baton while ready: simulation | 14.6 ms | 14.3 ms | 4.2 ms | 2.8 ms |
| main thread (the UI: message loop, DirectInput reads) | 22.1 ms | - | 22.0 ms | 21.9 ms |
| input thread (`ISMouse`) | 19.4 ms | - | 22.0 ms | 22.4 ms |
| network (the server's send thread, engine+0x55d2e, time-critical) | 15.3 ms | - | 10.3 ms | - |
| audio (Miles Scavenger; music intensity) | 12.7; 9.8 ms | - | 21.3; 10.4 ms | 21.0; 0.1 ms |
| longest stretch without a scheduling point (in game) | 14.6 ms | 14.6 ms | 4.6 ms | 6.4 ms |
| mouse press, host event → the game's first read: mean, max | 6.1 ms, 35.5 ms | 2.8 ms, 9.5 ms | 2.5 ms, 5.9 ms | **1.7 ms, 5.4 ms** |
| main loop passes per second (its `Sleep(1)` per pass) | 90-130 | 120-235 | 215-235 | - |
| frames per second | 36.7 | 35.4 | 41.5 | - |
| frame interval p50 / p90 / p99 | 25.2 / 46.4 / 60.4 ms | 25.4 / 48.9 / 59.0 ms | 18.1 / 42.7 / 59.2 ms | - |

(rr's longest waits for the main, input, network and audio threads are left out: that run still had the script's frame dumps, each a 130 ms stall. The 60-cap run's frame intervals are left out: an app behind other windows loses frames at the presenter, §A.4.2.)

- **The simulation runs its ticks whole.** Its wait for the baton is 0.01 ms on average and 3-4 ms at worst, so the tick time the game measures is the tick's own: 1.4-1.7 ms instead of 4.3.
- **Handoffs cost a sixth**, and with the 60 cap the core is idle half the time.
- **No thread waits longer than the starvation limit** (20 ms plus a quantum); 0.7's round robin had no bound, and the time-critical simulation waited up to 15 ms for the renderer.
- **Input reaches the game in under 6 ms.**
- **A busy host no longer collapses the game.** The 0.7 run made while another workstream's 16-job build ran (load average 35-44, `before-2057`) took 126 µs per handoff on average and 4 ms at the 99th percentile; it ran 24 PT/s, a mouse press took up to **1.7 s** to reach the game, and frames were 27 a second with a 229 ms 99th percentile. The new scheduler under the same kind of load (26-37, `ab1-after1`) kept 7-8 µs per handoff and a 32 µs 99th percentile, 122 PT/s and a 12 ms worst press. The guest threads' QoS (only the run thread had one before) and the single wake-up are what changed it.

#### A.4.2 Panning at the 60 fps limit (the acceptance test)

P7-C's tooling (ee-macos `phase7/input` 4980bb5): the input probe (`mods/inputprobe`) and `tools/input/evenness.py` (per held arrow key: main-loop passes, frames, the camera's move from one rendered frame to the next, the share of frames it did not move, and `--check`: at most 2% still, per-frame move p5 ≥ 0.5 and p95 ≤ 1.5 of the median). The scenario (`pan.script`): the save loaded, 20 s, the pointer mid-map, then Right, Left, Up, Down held 3 s each, twice. 0.7 is P7-C's own app (kit `feat/input`: the 60 fps limit, time-scaled steps); rr and new are P7-A merged with P7-C (scratch builds, not for merging).

The performance overlay's red bar is a new frame shown more than 1.2 frame budgets after the one before (kit `host/performance_overlay.cpp`, `frame_pacing.h`: 20 ms at the 60 cap). An app behind other windows loses frames at the presenter (an occluded window: 860 of 2,806 sealed frames dropped in the 0.7 run), so the same test is made where the guest makes its frames: the interval between two `RTSGraphics::Render` calls (the probe's `R` rows). `RECOMP_SCHED_TRACE_TIMELINE` says which threads held the baton during each late interval.

| | 0.7 | rr (2 runs) | new (final build; the run before it) |
|---|---:|---:|---:|
| main-loop passes per second, by hold | 57-198 | 144-500 | 249-504; 229-486 |
| frames per second, by hold | 38-58 | 56-60 | 52-60; 58-60 |
| still frames, worst hold | **15.0%** | 7.8%; 6.3% | **0.0%**; 0.0% |
| even (move ÷ speed × interval) p5 / p95, worst hold | 0.00 / 2.09 | 0.00 / 1.85 | 0.83 / 1.21; 0.85 / 1.18 |
| holds passing `evenness.py --check` | 1 of 8 | 7, 6 of 8 | 7 of 8; 7 of 7 |
| late frames (> 20 ms, the red bars) | **47.0%** | 3.6%; 3.2% | 5.9%; **2.2%** |
| frame interval p50 / p95 / p99 / max | 19.3 / 37.8 / 50.7 / 101 ms | 16.8 / 19.2 / 23.9 / 46 ms (the first) | 16.8 / 22.0 / 26.1 / 80 ms; 16.7 / 19.0 / 23.8 / 75 ms |
| baton during the late intervals | (no trace in that build) | simulation 49-52%, renderer 47-50% | renderer 52%, simulation 46%; simulation 61%, renderer 37% |

(The failing "new" hold is one at 1.50 times the median exactly; in the earlier run one hold reached the map's edge, where the camera stops, and is not counted. The camera's speed is 24.3/s in every run: P7-C's step scaling, against Windows' 30.5.)

- **The bursts are gone:** no frame in any hold finds the camera where the previous one left it, and the per-frame move keeps within 0.83-1.21 of even motion.
- **Late frames fall from one in two to one in twenty or fewer**, and what is left is the renderer's own frame and the simulation's ticks. The 0.7 numbers moved most with the old order alone (rr), i.e. with the cheaper handoff and the critical-section and QoS changes; the new order then removes the still-frame bursts that rr still had, at about the same share of late frames.
- `RECOMP_SCHED_SLEEP_BOOST=1` (a boost for a `Sleep` that ran out, as in the first version of this change) gave the same: 0.0% still, 5.5% late.

#### A.4.3 Map loading

A Huge random map (`Game Options\Map Size` = 4, the registry's other options), the game's own timings:

| | 0.7 | new | Windows (legion 0075) |
|---|---:|---:|---:|
| `EERandomMap::PaintTiles`, the app on Metal (60 cap) | 20.07 s | **0.57 s** | 0.14 s |
| `EERandomMap::CreateRandomMap`, the app | 22.61 s | **1.81 s** | 0.35 s (all of map generation) |
| `PaintTiles` headless (`ee_boot`, 800×600, the build before the critical-section change) | 15.58 s | 0.61 s | |

The loader paints each tile under the engine's `TSCriticalSection` while the render thread draws the loading screen. 0.7 gave the baton to the renderer at the first import call after every millisecond (P7-B's profile: the loader computing in 5-8% of its samples, `LeaveCriticalSection` 38% and `EnterCriticalSection` 52% of the rest). Now the game's own `THREAD_PRIORITY_ABOVE_NORMAL` for the loader (it sets it while it loads, §A.2) is honoured, and leaving the section keeps the baton. The remaining 4× against Windows is CPU (P7-B's hot paths).

### A.5 Determinism [measured]

- **`tools/test.py --simparity`** (its default scenario): the two Mac games agree with each other on all 7,085 PTs. Against legion's `win-as-1` they agree to PT 5616 and differ from 5617, after the second autosave landed one CT apart (after PT 4816 on the Mac, 4802 on Windows): exactly the result 0.7 had (simparity.md §9.3, `as3`), the single-player save timing that multiplayer does not have. The first save, loaded (run 3), agrees with Windows' load of the Mac's save (`win-load-mac-2423`) on all 4,791 common PTs. Every autosave was written. Repeated on the build with the critical-section and timeout changes (`simparity-final`): the same, Mac against Mac on all 7,078 common PTs, against Windows to PT 5616, the loaded save on all 4,788. The last commit (a preempted thread keeps its quantum) only moves who runs when, and was checked with `--boot` and `runtime_tests`.
- **The M1 replay** (`tools/m1/run_matrix.py --cases A06,C05,S00b --no-fetch --build both`, on bundles and linked builds translated with the preemption points): 27 of 27 packs pass on both builds.
- **`--boot`** (11 milestones, 14 threads, render at CW 007f, clean exit; also on the final build), **`--menus`** (5 of 5) and **`--lobby`** (2 of 2) pass.
- The kit's suites: `tools/test.py` (487 passed), `--native` (41 of 41), `check_repo`, `check_game_literals`, `format.py`. `runtime_tests` gained two scheduling checks (a time-critical thread runs first; a thread computing without an import gives the baton back at a preemption point, after 2.6 ms) and passes under both policies; its two `SetTimer` checks across a clock wrap fail on `ee` 614be22 too.

Not measured: a LAN game against legion (the host's lag indicator, the Mac's CT acknowledgements, the command delay). Legion was in use.

### A.6 What one processor cannot do: parallel guest threads (design, not built)

Everything above shares **one** core between the game's threads, where Windows can give them several. Legion's per-thread CPU time says Empire Earth hardly uses more than one there: **0.6 of a core** in this save, 0.5 of it the render thread at 100 frames a second (the start of this section). On the Mac, at the 60 fps limit, the core is idle half the time in the same save (§A.4.1). So parallel guest threads are not needed for this game as far as anything measured shows; what is not measured is a big battle in view (legion offered to repeat 0070 with the camera on the action). The design is kept here for a game, or a battle, that does need it.

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

### A.7 The tick rate: why the Mac ticks 3-5 times as often as Windows [static, measured]

In the 20:57 save in single player the game chose 73-124 PTs a second on the Mac (§A.4.1; P7-B saw 83-104) and 22.9 on Windows (legion 0075). P7-B's three questions:

- **(a) What the tuner measures.** Each tick (EXE 0x546c63) reads `TMGetTime` (engine 0x1005e750: `QueryPerformanceCounter` in milliseconds when the counter exists, else `timeGetTime`) at its start and its end. Their difference, summed until it reaches a second and divided by the count, is `EEPhysics+0xd8`, the mean tick time; the time from one tick's start to the next one's is `+0xdc` [static]. `TuneForSlowCPU` sets the rate from the mean tick time and the 30% CPU share (simparity.md §4.1): 1.4-1.7 ms on the Mac gives the 125 cap, 1000 / 22.9 / 3.33 ≈ 13 ms on Windows. Windows' simulation thread used 13 ms of CPU a second at 22.9 ticks, about 0.6 ms a tick (0070), so **Windows' measured tick is about 20 times its CPU time**: the tick waits inside on Windows, most likely for a lock the render thread holds while it draws at 100 frames a second on another core [inferred; a Frida probe of the tick's critical-section waits on legion would settle it]. The kit's clock is not what differs: `QueryPerformanceCounter` is `host_millis` × 1000 at 1 MHz, which the game turns into the same milliseconds, and the time-critical simulation now runs its tick without interruption, so the Mac's number is the tick's own. 0.7's sliced ticks read 4-5 ms (72-100 PT/s). Nothing in the timing HLE is wrong, so nothing was changed.
- **(b) Multiplayer.** Every CT carries the host's PTs per second and PTs to perform (`NEMCommunicationsTick`, netcode.md §5.1), and every machine runs them (simparity.md §4.1); a client's response carries no tick time ("avg PT time, always 0", netcode.md §5.1). So a Mac client in a Windows-hosted game ticks at the host's ~23 a second, and only a Mac host chooses its own, higher, rate [static, documented].
- **(c) The stutter.** In single player, yes: 15-21% of the core goes to ticks where Windows' rate would need about 5%, and the simulation held the baton for 46-61% of the late intervals while panning (§A.4.2). In a Windows-hosted LAN game the host's rate applies and the simulation's share is small.

### A.8 The run thread's wait: idle CPU and energy [measured]

P7-B's profiles of the idle game had the run (main) thread using 26-40% of a core while it waited for the baton. The time was in `sched_checkpoint_at` → SDL (`SDL_GetWindowFlags`, `CFRunLoopRunSpecific`, mach port insert/extract traps): off the baton, but a busy wait. Kit `feat/sched-idle`, ee-macos `phase7/sched-idle`.

**The cause [static, measured].** With nothing runnable, the baton holder waits until the earliest deadline. On the windowed host the run thread hands that wait to SDL (kit `host_set_idle_waiter` → `idle_wait` → `SDL_WaitEventTimeout`), so input can end it. SDL waits only in whole milliseconds. The game's threads loop on `Sleep(1)`, so the earliest deadline is nearly always less than a millisecond away and SDL came back at once. The scheduler found the deadline not yet due and asked again. Each pass was one turn of AppKit's run loop plus the window state and the controls, and that is P7-B's profile. Kit `runtime_tests` with an SDL-shaped waiter counts 106,860 calls for 20 `Sleep(1)` calls. The event pump does not run at every checkpoint. The clock's tick runs `pump()` at most once a millisecond. A baton wait polls once each time the run thread gives the baton up, then waits on its condition variable for up to 2 ms. `sample` hides the spin: it suspends the process every millisecond, and in the sampled 5 s the run thread fell from 47% to 5% of a core, so the run-thread figures below come from the kernel's per-thread times.

**The fix.** The runtime no longer hands a sub-millisecond remainder back to the host; it sleeps it on the thread's condition variable. The SDL host waits that fraction itself on the run loop (`os_wait_run_loop`: `CFRunLoopRunInMode`, which input and posted events end, 0.05 ms after a post in a standalone test), so input in that fraction is taken at once. Not done: limiting the pump to once every 2 ms (tried in the first version of this change, and dropped). It saved under a point of the run thread's core in the idle game and added 1.3 ms to the mean click latency.

**Measured.** `tools/perf/idle_cpu.py` (these runs were made with its scratch predecessor: the same script and counters): the main menu for 40 s, then the 20:57 save loaded, 20 s to settle and 60 s left alone. 1920×1200, behind other windows, silent, at the 60 fps limit. The PT rate is pinned at 124: left to itself the game tunes it from its tick time, which moved between 25 and 124 within one run of either build. CPU is user plus system time. The energy figure is the kernel's estimate for the process (`proc_pid_rusage` `ri_energy_nj`), not a power meter. **Before** is P7-C's app (kit b835958, ee-macos 747c097). **After** is this change. Four interleaved pairs were made while the Mac was otherwise quiet. Three of them used this change's first version, which also limited the pump to every 2 ms; the final one is the same within a point. The energy rows are from the one pair measured with it, the final change.

| idle late game (124 PT/s, 60 fps) | before | after |
|---|---:|---:|
| whole process, % of a core | 75.0-77.4 | **63.4-65.1** |
| the run thread | 21.6-29.4 (47.2 in one more run) | **1.9-2.4** |
| simulation + renderer | 38-42 | 49-53 |
| energy (the kernel's estimate) | 4.6 W | **2.0 W** |
| the cores' mean clock; instructions retired a second | 4.19 GHz; 11.0 G | 3.56 GHz; 6.1 G |
| frames sealed a second | 59.8 | 59.7 |

| main menu (60 fps) | before | after |
|---|---:|---:|
| whole process, % of a core | 59.6-63.4 | 58.9-62.2 |
| the run thread | 11.1-14.5 | **6.1-7.4** |
| energy (the kernel's estimate) | 173 mW | **87 mW** |
| cycles on performance cores; instructions a second | 19%; 1.84 G | 6%; 1.27 G |

- **The run thread no longer spins:** about 2% of a core in the idle game. What it still does is its own loop and the event pump.
- **The process's CPU time falls by less than the run thread's,** and at the menu not at all. The game's own threads take longer, because the cores run slower without the spin: 3.6 GHz instead of 4.2 in the game [inferred: the spin kept the performance cluster at its top clock]. The work done is the same: the same PTs, the same 60 frames, and 45% fewer instructions in the game. The energy estimate halves in both phases.
- What is left in the game is the simulation and the renderer, about 50% of a core at 124 PTs and 60 frames a second, plus 1,400-1,900 interrupt wake-ups a second. A thousand of those are the preemption ticker (§A.9).
- Runs made while the user's own jobs loaded the Mac (load average 6-12, the cores at 1.8-2.4 GHz) moved by more than the change and are left out of the tables. The before app could not hold 60 frames in one of them. In the one made at the game's own PT rate, the process fell from 96% to 83% of a core and the run thread from 8.8% to 2.8%.

**Clicks and panning are unchanged** (`heavy.py` and `pan.script` from §A.4, the PT rate pinned at 124 as above). That rate is heavier than the 73-112 the game chose in §A.4, so every number here is higher than there.

| | before | after |
|---|---:|---:|
| mouse press → the game's first read: mean (max), two runs each | 7.1 (61), 3.7 (47) ms | 4.0 (25), 3.6 (19) ms |
| energy during the click run | 3.1, 3.3 W | 2.0, 2.1 W |
| panning: late frames (> 20 ms); holds passing `evenness.py --check` | 17.7%; 5 of 8 | 15.4%; 4 of 8 |
| panning: per-frame move ÷ even, worst p5 / p95; still frames | 0.59 / 1.47; 0% | 0.56 / 1.36; 0% |

At the game's own PT rate, the clicks came out at 1.2 ms on average before and 2.9-4.5 ms after. In those runs the game chose 73 PT/s for the before build and 115-124 for the after builds, and the latency follows the simulation's share of the core. Pinned, the two builds are the same within the run-to-run spread. One version of this change, with the pump limited to every 2 ms, panned at the game's own rate with 3.4% late frames and 7 of 8 holds passing, against 3.2% and 8 of 8 before. The final build's own run at that rate was made under one of the loaded periods above and is to be repeated.

### A.9 Left to do

- **The LAN check** (§A.5): host on legion, the Mac joining, the 20:57 save's state or a long game; watch legion's lag indicator, the Mac's CT acknowledgements and the command delay.
- **A big battle in view**, Windows and Mac: legion's 0070 play phase looked at open sea. The same per-thread CPU (`windows/frida/thread_cpu.js`) and the Mac's trace in a save with a battle on screen would say whether §A.6 is ever needed.
- **Windows' tick wait** (§A.7 a): a probe of the simulation's critical-section waits inside a tick on legion.
- **Multimedia timers fire only from the message pump.** The simulation's pacing timer is a `timeSetEvent` in event mode (engine 0x1005ebc0: `TIME_CALLBACK_EVENT_SET`, periodic), and the kit signals it in `host_pump_timers`, which runs only inside `PeekMessage`/`GetMessage`; a late pump also moves the next due time (`next_due = now + period`). An event-mode timer needs no guest thread, so the scheduler could fire it at its due time like a wait's deadline, as Windows' timer thread does. Not done: in this save the simulation kept up regardless.
- **Long shims hold the baton**: `_AIL_set_named_sample_file` 128 ms during the load (main thread), `GetDC` on the render thread 3-15 ms. Hot paths, P7-B's.
- **Firewall prompts.** macOS asks again for every rebuilt ad-hoc-signed binary that opens a port; a developer `ee_boot` signed with a stable self-signed identity would keep the approval across builds. The app opens the game's ports (UDP 33334, TCP 33335) at the main menu, in single player too.
- **The preemption ticker wakes a thousand times a second** (kit `sched_ticker_main`), when nothing runs too: 0.4% of a core, but a wake-up every millisecond in an idle game (§A.8). It could park while the holder waits with nothing runnable, and while there is only one guest thread.
- **The panning run at the game's own PT rate on the final build** (§A.8), on a quiet Mac.

---

## Input and frame rate (P7-C)

The user reported three things after last night's games: W, A, S or D pressed in game with nothing selected made "weird sounds"; a held arrow key scrolled the camera only a tiny amount compared with Windows; and the app ran at the display's 120 Hz (74 fps on average in the log) where 60 is plenty.

| Report | Cause | Fix |
|---|---|---|
| Held arrow keys scroll slowly | One fixed camera step per pass of the main thread's loop (§1.1). On Windows the loop makes about 600 passes a second (legion 0080); in the app it made 240–500 at 800×600 and 190 in the user's own late game at 1920×1200, because the render thread holds the cooperative baton for much of each frame. The camera moved at a third of Windows' speed in the user's game | Each step of a held key is scaled by the time since its previous step, in Windows passes, so the speed is Windows' at any loop rate (`app/ee_input.cpp`); the 60 fps cap also gives the main thread more passes |
| W/A/S/D sounds | A, B, D and F without modifiers are "select the closest aircraft" (`EEUIESelectClosestAircraft`); with no aircraft it plays the hotkey alarm (`sounds\hotkeyalarm.wav`, 50 ms) once per press. W and S do nothing. Windows does exactly the same (legion 0080, §2) | None needed for the sounds. A quick tap of any hotkey was lost when the game polled DirectInput less often than the key was held: the kit's keyboard buffer now replays every key transition |
| 120 fps | The kit's Frame limit only paced games whose frame clock it hooks; Empire Earth paces itself by `Flip` and the vertical blank, so it presented at the display's rate (Windows renders as fast as it can: 161 fps on legion) | The limit now holds a `Flip` or `Present` that waits for the vertical blank; Empire Earth's default is 60 (`game.toml [settings] frame_limit`) |
| The "RPF … DROP" graph | A desktop host's default overlay is the graph, and every profile saved it | `game.toml [settings] performance_overlay = "off"`; an overlay nobody chose follows the default (§3.2) |

### 1. Held arrow keys

#### 1.1 How Empire Earth scrolls by keyboard [static]

- `RegisterInGameHotkeys` (EXE 0x64c8b0) binds Up, Down, Left and Right (DIK 0xc8, 0xd0, 0xcb, 0xcd) to `EEUIEKeyboardScroll` (vtable 0x8348fc, fire 0x5c9070) as the hotkey's **continuous** event, and `[` / `]` (0x1a, 0x1b) to `EEUIEHotKeyZoom` (0x5c913c) the same way. The events in `UIForm::AddHotKey(key, mod1, mod2, mod3, just pressed, pressed, just released, double pressed, group, bool)`: A and D take the first slot (once per press), the arrows the second (every update while held), F10's options the third.
- `UIHotKey::FireEvents` (engine 0x10075547) fires the continuous event whenever the key's entity reads down (`ISKey::GetValue`, bit 0 of the key's transition counter that `ISKeyboard::Refresh` keeps from `GetDeviceData`).
- Who calls it: the main thread's loop (EXE 0x6603c4) drains its `TSThread` messages, then calls 0x661889: `gISKeyboard->Refresh`, `gISMouse->Refresh`, the cursor, then `UIManager::UpdateUI` (vtable +0x28), which reaches the hotkeys. The pass ends in `Sleep(1)`. So **the camera moves one step per pass**, and the speed is set by how often the loop runs.
- One step: `(zoom + 0.5) × (scroll speed + 0.5) × 0.05` (zoom at 0x918db0, 0 in to 1 out; the speed at 0x9193ac, 0.17 from the registry's Side Scroll Speed 200), passed to the camera (0x918cd0) at 0x51e2e6, which scales it again by `zoom + 0.5` and turns it into the view's axes. At the parity game's start (zoomed fully out, 1.0) one step moves the camera's look-at point (0x918d48, 0x918d4c) 0.050 [measured].
- The edge of the screen scrolls by elapsed time instead (0x4050fc multiplies by a time field), which is why only the keys were slow.

#### 1.2 Measured

Main-loop passes a second are `U` rows of the input probe (§5), frames `R` rows; "one step" is the 0.7 behaviour (`RECOMP_EE_ORIGINAL_KEY_SCROLL=1`), "scaled" this change.

| Run | Limit | Steps | Passes/s idle (holding a key) | Frames/s | A held arrow key: camera distance in 1 s |
|---|---|---|---|---|---|
| **Windows (legion 0080, the parity game)** | – | one per pass | **616** (592–615 steps/s while held) | **161** (140–200 while scrolling) | **30.5** (Down held 2 s: 61.5) |
| Mac headless, parity game (`ee_boot`, software renderer) | none | one | 20 | 18 | 1.0 |
| … the same, `tests/menus/keyboard.script` | none | scaled | 18 | 17 | Up 21.0, Down 21.9 |
| Mac app, 800×600, a fresh game | none | one | 240 | 117 | – |
| … the same game type | 60 | one | 480 | 60 | – |
| Mac app, 800×600, three more fresh games | none / 60 / 60 | one / one / scaled | 353 (371–405) / 402 (464–499) / 506 (497–504) | 63.6 / 60.0 / 59.7 | (each reached the map's edge within the 2 s hold) |
| Mac app, 1920×1200, **the user's save** (`AutoSave 20h.57m.31s`) | none | one | 83 (220) | 25.6 | **11.1** |
| … the same save | 60 | scaled at 485 a second | 135 (157) | 51.0 | 24.1 |
| … the same save | 60 | scaled at 603 a second (this change) | 159 (207) | 54.7 | **30.1–30.3** |
| … the same save, with P7-A's scheduler (kit `ee` e84d11f) | 60 | scaled at 603 | 344–357 (225–301) | 58 | **30.1–30.4** |
| Mac app, 800×600, a fresh game, with P7-A's scheduler | 60 | scaled at 603 | 783 (613–627) | 60 | 30.1–30.3 |

One step at the parity game's start (zoom 1.0, speed 0.17) moves the look-at point 0.050: (1.0 + 0.5) × (0.17 + 0.5) × 0.05. In the user's own game, at the user's resolution, the 0.7 app moved the camera a third as far as Windows (9.1–11.1 against 30.5); the loop's rate is whatever time the render thread leaves the main thread, so it varied from run to run by a factor of two [measured]. Scaled, the distance in a second is the same at 18 passes a second (headless) and at 157 (the app) [measured].

#### 1.3 The fix

- **The 60 fps limit (§3)** is most of it: the render thread waits for its next 60 Hz slot in the scheduler, the main thread runs through that wait, and the loop's passes went from about 240 to about 480 a second in a light game (Windows: about 600).
- **Scaled steps** (`app/ee_input.cpp`, every Empire Earth host): before each step of a held key the hook measures the time since that key's previous step and multiplies the step at its one call site (0x51e2e6 from 0x5c9131; the zoom at 0x51f2f3 from 0x5c9258) by that time in Windows passes (603 a second, legion 0080; the first build used legion.md's 485). A key held for a second moves the camera as far as on Windows however often the loop runs, and a loop at Windows' rate scales by one. The first step of a press is one step; a gap over 100 ms counts as 100 ms, so a stall does not jump the camera. `RECOMP_EE_ORIGINAL_KEY_SCROLL=1` turns it off. The camera is local: no simulation state, nothing on the network (`tools/test.py --simparity` agrees with Windows as before).

#### 1.4 Evenness: the camera from frame to frame

The rate is not the whole of it: the user, watching the app, saw the camera move unevenly and in bursts. The render thread draws the camera as it finds it, so what the player sees is the camera's move from one rendered frame to the next. `tools/input/evenness.py` measures it from the probe's `R` rows (the look-at point each frame starts from) while a key is held: the move per frame (p5, median, p95), the share of frames that did not move, and the move over speed × the frame's own interval (1.0 is motion exactly even in time). On Windows, at 161 frames and about 600 passes a second, each frame finds the camera 3–4 steps further on [inferred from legion 0080's counts].

| Mac app run | Limit | Steps | Passes/s while held | Frames/s | Speed | Per-frame move p5 / median / p95 | Even p5 / p95 | Still frames |
|---|---|---|---|---|---|---|---|---|
| 800×600, a fresh game (`--app-frames`), **with P7-A's scheduler** | 60 | scaled at 603 | 613–627 | 60 | **30.1–30.3** | 0.47 / 0.50 / 0.54 | 0.96 / 1.04–1.05 | 0 % |
| the same, "original", with P7-A's scheduler | none | scaled at 603 | 517–530 | 119–120 | 30.2–30.3 | 0.21 / 0.25 / 0.29 | 0.88 / 1.10 | 0 % |
| **the user's save, 1920×1200, with P7-A's scheduler** (two runs) | 60 | scaled at 603 | 225–301 | 57–59 | **30.1–30.4** | 0.27–0.41 / 0.50–0.51 / 0.67–0.78 | 0.56–0.94 / 1.07–1.30 | **0 %** |
| 800×600, a fresh game (`--app-frames`, this change) | 60 | scaled at 603 | 443–491 | 59–60 | 29.9–30.2 | 0.45 / 0.51 / 0.55 | 0.95 / 1.04–1.06 | 0 % |
| the same, "original" | none | scaled at 603 | 362–403 | 107–117 | 30.3 | 0.20 / 0.26 / 0.31–0.50 | 0.90 / 1.10 | 0 % |
| 800×600, a fresh game | 60 | scaled at 485 | 507–517 | 60 | 24.2–24.4 | 0.37 / 0.41 / 0.44 | 0.96 / 1.05 | 0 % |
| the same | none | scaled at 485 | 376–452 | 118–120 | 24.0–24.3 | 0.15 / 0.20 / 0.25 | 0.85 / 1.13 | 0 % |
| the same | 60 | one per pass | 452 | 60 | 22.4–22.6 | 0.30 / 0.35 / 0.45 | 0.82 / 1.24 | 0 % |
| the user's save, 1920×1200 (this change) | 60 | scaled at 603 | 207 | 58 | 30.1–30.3 | 0.38–0.40 / 0.52 / 0.65–0.68 | 0.81–0.86 / 1.20–1.21 | 0–0.9 % |
| the user's save, 1920×1200 | 60 | scaled at 485 | 188–195 | 58–59 | 24.3 | 0.21–0.31 / 0.41 / 0.53–0.64 | 0.50–0.73 / 1.38–1.39 | 1.0–1.9 % |
| the same (0.7) | none | one per pass | 184–191 | 61 | 9.1–9.5 | 0.05 / 0.15 / 0.25–0.28 | 0.31 / 1.60–1.63 | 0.9–3.6 % |

In a light game the 60 fps limit and scaled steps give motion even to ±5 % frame to frame. In the user's game the main loop gets three to four passes per frame, at uneven times: the per-frame move ranged from 0.8 to 1.2 of even in one run (passing the check) and from 0.5 to 1.4 in another, with up to one frame in 50 that did not move (other workstreams' runs shared the machine). That is the burstiness the user saw, and it varies with what else runs. With P7-A's scheduler merged (kit `ee` e84d11f) the main loop makes 225–300 passes a second during the hold in the user's save, no frame stands still, the speed is Windows' (30.1–30.4), and three of the four holds pass `--check` (the fourth's p95 is 1.53 of its median, against 1.5): what is left is three to five passes a frame at uneven times, and in a light game the loop runs at Windows' rate (613–627 a second) and the motion is even to ±5 %. Scaled steps cannot do better than the passes they get; even motion there needs the main thread to run more often, between frames (P7-A's scheduler). `tools/test.py --app-frames` checks it at the 60 fps default (at most 2 % still frames, the per-frame move's p5 at least half its median and p95 at most 1.5 times it, the speed within 15 % of Windows'), and `tools/input/evenness.py --check` applies the same to any probe, so it serves the scheduler work as its acceptance test.

### 2. W, A, S and D with nothing selected

- **Static.** With no modifier, A, B, D and F fire `EEUIESelectClosestAircraft` (0x5d9090) with types 1, 0, 3, 2; it walks the player's units for an aircraft of that kind and, with none, looks sound 0x3c2 up (0x405a58) and plays it through `SESoundManager::Play`. Alt+A/S/D/G set unit stances; Ctrl+letters select unit groups. Plain W and S are not bound. Text typed in game reaches the game as `WM_CHAR` (`UIManager::UpdateTranslatedKey`), which does nothing with no text field focused.
- **Mac [measured, headless and app].** Each tap of A or D: one `WM_KEYDOWN`, one `WM_CHAR`, one aircraft fire and sound 0x3c2 looked up twice (by 0x5d925d and 0x5d9268) and played once: `AIL_start_sample` at volume 127, centred, 22,050 Hz. Sound 0x3c2 is **`sounds\hotkeyalarm.wav`** in `data.ssa`: 1,093 bytes of 8-bit PCM, 50 ms, peak 0.98 of full scale (the mixer's trace, `RECOMP_HOST_TRACE_AUDIO`, says "1093 bytes ... peak 0.977"; the archive's entry is the only one that size). The app's own mix (`RECOMP_HOST_AUDIO_CAPTURE`, music and ambient sounds off) has the whole 50 ms tick at each press and nothing else: no discontinuity, no repeat. W and S: the messages and nothing else. The windowed app's typing path (the key, then the character as input-method text) gives one `WM_CHAR` per press, not two. DirectInput has one down and one up record per press; a held key's autorepeat is not a transition, and the hotkey fires once per press (`D` held 1.5 s: one alarm). So of the four suspects, none: no key arrives twice (a), autorepeat floods nothing (b), the scan codes are right (A 0x1e, D 0x20, W 0x11, S 0x1f) (c), and the mixer plays the sample whole (d).
- **Windows [measured, legion 0080].** The same: A and D make one `SESoundManager::Play` each, of the engine's sound id 0 (the 0x3c2 entry's handle) from EXE 0x5d928f (the return address after the alarm's `Play` in `EEUIESelectClosestAircraft`), within 2 ms of the key-down, and the aircraft hotkey fires once per press, also when the key is held for 16 autorepeats. The loopback is about −19 dBFS in the 300 ms after A or D against −41 dBFS of background; W and S stay at the background. A tap is `WM_KEYDOWN`, `WM_CHAR`, `WM_KEYUP`; a hold 16 characters.
- **What changed.** Nothing in the sounds. But the probe showed the kit's DirectInput keyboard losing a press: it built its buffer by comparing the key states at each poll, and Empire Earth polls once per main-loop pass, so a key pressed and released between two passes (a quick tap, or a typed character) never reached the game. Windows' buffer has both records. The kit now keeps every key transition and replays them (kit CHANGELOG).

### 3. The frame rate

#### 3.1 The 60 fps limit

- Empire Earth flips with `Flip(NULL, DDFLIP_DONOTWAIT)`, without `DDFLIP_NOVSYNC` (graphics.md §11; the registry's Wait for VSync is 1). GOG's wrapper does not wait (`dxcfg.ini` `vsync=application`): legion renders 161 frames a second in game [measured, 0080]. The kit returns from such a `Flip` at the presenter's next refresh, so the game presented up to 120 frames a second on a ProMotion display. The settings page's Frame limit only paced games whose frame clock the kit hooks (Populous).
- **Kit:** a `Flip` without `DDFLIP_NOVSYNC`, or a DXGI `Present` with a sync interval, now also keeps to the Frame limit (`host/frame_pacing.h` `paced_refresh_delay`, called from `host_present_refresh_delay`): it waits for the first refresh at least 1/limit after the previous frame's, on a deadline that advances by 1/limit, so 60 on a 120 Hz display is every other refresh, and a late frame starts a new phase rather than catching up. The wait is the scheduler's (`guest_sleep_ms`): the game's other threads run through it.
- **Empire Earth:** `game.toml [settings]` lists the Frame limit row and makes 60 the default (`frame_limit = 60`); the player chooses 40, 60, 120 or "original" (the display's rate) on the settings page (⌘,).
- **The animation clock is untouched.** The game's simulation and animations run on its own clocks (`TMGetTime`, the PTs the communications tick schedules), not on frames: 42.9 PTs a second at 80 frames a second, 42.8 at 60 (the probe's `P` rows over 20 s) [measured].

| Mac app, 800×600, a fresh game | Frames/s in game | Main-loop passes/s in game | CPU, the whole 63 s run (user + system) |
|---|---|---|---|
| no limit (the display's 120 Hz) | 117 | 240 | 122 s (1.9 cores) |
| 60 | 60 | 480 | 94 s (1.5 cores) |
| no limit, another game | 80 | 303 | 2.2 cores over 10 s in game |
| 60, the same game type | 60 | 439 | 1.8 cores |
| 60, with P7-A's scheduler (`--app-frames`) | 60 | 783 | 29 s for the whole run (against 66–88 s before it) |
| no limit, with P7-A's scheduler | 120 | 592 | 35 s |
| the user's save, 1920×1200, 60, with P7-A's scheduler | 58 | 344–357 | 0.9 cores in game (2.1 before it) |

Other pairs (different maps, §1.2, other workstreams' runs in parallel) gave 2.1 against 2.0 cores at 64 against 60 frames a second, and the user's save at 1920×1200 1.3 cores at 26 frames a second against 2.1 at 51: what the render thread no longer spends, the main thread's busier loop and the scheduler take (P7-A's section). The limit's point is the loop's time and a steady 60; the CPU saved is up to a fifth.

#### 3.2 The performance overlay

- A desktop host's default is the frame-time graph (`platform_ui_default_overlay()` 2). `game.toml [settings] performance_overlay = "off"` makes a fresh profile start without it; **F11** cycles counters, graph, off.
- The kit saves every declared setting, defaults included, so the user's profile held the graph as if chosen. The kit now marks a choice made on the settings page or with F11 (`host.display/overlay_chosen`), and an unmarked graph follows the default: the user's profile loses the graph once.
- `frame_limit` needs no such care: profiles saved the windowed host's default, 60, all along.

### 4. Tests

| Test | What |
|---|---|
| `tools/test.py --menus keyboard` (`tests/menus/keyboard.script`, headless) | Legion's parity game, nothing selected: Up and then Down held 1.0 s each, the camera's look-at point peeked before and after: `run_menus.py` wants Windows' 30.5 within 25 % (27.4 both ways headless, 30.3 with P7-A's scheduler; one step per pass gave 1.0). `]` and `[` held 0.2 s: the zoom goes in and back out, within 0..1. W, A, S and D tapped, then typed: through the input probe, one `WM_KEYDOWN` and one `WM_CHAR` per press, one aircraft fire and the alarm's two lookups for A and D, nothing for W and S, no sound looked up again and again |
| `tools/test.py --app-frames` (`tests/menus/run_app.py`, the app on Metal in the background) | The app's own frame stats in game: at most 61 presents a second with the default limit, the display's rate with "original"; the main loop's passes and the CPU time for both; Up and Down held 2 s each, and at the default limit the camera's evenness (§1.4) must pass `evenness.py --check` |
| kit `host_tests` | `paced_refresh_delay`: frames on refresh boundaries, averaging the limit at 120, 144, 100 and 60 Hz, never closer than one refresh, no burst after a late frame; the key transition journal |
| kit `dx_tests` | a key tapped between two polls is a down and an up record, and still nothing with no journal |
| kit `mods_tests` (`display_game_defaults_and_overlay_choice`) | the game's defaults for a fresh profile; an unchosen graph follows "off", a chosen one stays |
| kit `tests/test_game_config.py` | `[settings] frame_limit` and `performance_overlay` |

### 5. Tools

- **`mods/inputprobe`** (ee-macos): a mod plugin, loaded only with `RECOMP_EE_INPUT_PROBE=FILE` (`app/ee_standins.cpp` points `RECOMP_MODS_DIR` at `build/recomp/ee-mods`; in the app set `RECOMP_MODS_DIR` too). One CSV row per main-loop pass (`U`), keyboard scroll step (`S`, with the camera), zoom key (`Z`), aircraft hotkey (`A`), sound lookup (`N`, id and caller), keyboard or IME message to the game window (`M`), rendered frame (`R`, with the camera's look-at point) and, once a second, the physics ticks run (`P`), each with host milliseconds. It only reads.
- **`tools/input/evenness.py PROBE.csv [--check] [--windows-speed 30.5]`**: the camera's evenness per held key (§1.4).
- **`windows/frida/input_probe.js`**: the Windows twin (legion 0080): the same EXE functions counted per 100 ms, `SESoundManager::Play`, the window's key messages, the camera every 50 ms.
