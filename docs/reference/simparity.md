# Single-player simulation parity: the checksum-stream oracle (P4-C)

Status: Phase 4, workstreams P4-C (the oracle), P4-D (the divergence) and P4-E (saves), 2026-10-15. Static RE done; the Mac tracer, its Windows twin, the scenario and the comparison tool work; Mac↔Mac, Windows↔Windows and Mac↔Windows results in §7. **The first simulation difference (PT 24301) was the autosave**: on the Mac the save failed, so it skipped a side effect the save has on the computer player's schedule (§9; fixed in the kit). **Saves are portable both ways** (§10): a Windows save loaded on the Mac, and a Mac save loaded on Windows, give the same stream on both platforms. Owners: P4-C (simparity), P4-D (ai-divergence), P4-E (saves).
Code:
- [`mods/simparity/simparity.c`](../../mods/simparity/simparity.c): the Mac tracer, a kit mod plugin (public `pop_mod_api.h` only);
- [`windows/frida/checksum_trace.js`](../../windows/frida/checksum_trace.js): its Windows twin (legion 0027);
- [`tests/menus/simparity.script`](../../tests/menus/simparity.script): the scenario;
- [`tools/simparity/run.py`](../../tools/simparity/run.py) and [`compare.py`](../../tools/simparity/compare.py); `tools/test.py --simparity`;
- [`tests/menus/simparity-load.script`](../../tests/menus/simparity-load.script) (a game from a saved game) and [`tools/simparity/savefile.py`](../../tools/simparity/savefile.py) (reads and compares `.ees` saves).

Labels:
- **[static]**: read from the GOG binaries (the func-db Ghidra projects, Ghidra 12.1.4, and the DB listings);
- **[measured]**: output of the tools here (M4 Max, macOS 15.5) or of legion's runs;
- **[inferred]**: reasoning.

Addresses are EXE VAs (fixed base 0x400000) or engine VAs (`Low-Level Engine.dll` at its preferred 0x10000000, which is also its run-time base on the Mac). Context: [netcode.md](netcode.md) §6 (the OOS check), [PLAN.md](../PLAN.md) §5.2 and §6, [ADR-002](../decisions/002-exact-x87.md), [rmg.md](rmg.md) (M1).

---

## 1. Summary

| | |
|---|---|
| **The checksum** | `EEPhysics::WorldChecksum(char dump, uint flags)` at **0x545ebf**, `__thiscall` on the `EEPhysics` object at `[0x919774]`, `RET 8`. A 32-bit wrapping sum: every object of the list at `[0x918318]` contributes its virtual `+0x4c(flags)`, then `EEPhysics+0x80` (PTs scheduled), `+0x18` (the world RNG state) and `[0x919b68]` (broadcast inputs executed), plus flag-gated terms. Multiplayer calls it with `flags = 1` [static]. |
| **Side effects** | None on the simulation with `dump = 0`: it stores its result in `0x919b6c`, which nothing reads, and calls `TMGetTime` twice for nothing. `dump = 1` also writes the game's own OOS dump file [static]. |
| **The tick** | One physics tick (PT) per call of `EEPhysics` vtable slot 8, **0x546c63**, on the simulation thread (`RTSPhysics::Main`, 0x5532fb), when PTs are left; "after PT N" is the return of that call, N = `EEPhysics+0xb4` [static]. |
| **Seeds** | One number: `srand(TMGetTime()); rand()` at Start Game, carried by `EEMStartLoading` (+0x14). Its execute (0x4bee57) makes it the **world RNG state** (`EEPhysics+0x18`) and the map seed; a Random starting epoch is drawn from it too. Forcing it there fixes all three [static]. |
| **Time in the simulation** | Three paths, each fixable: the PT rate (PTs per second, hence the **step size**) is tuned from measured tick times; the statistics report fires on a wall-clock timer; and a message the simulation sends lands in whichever CT the server's timer thread emits next (§4) [static]. |
| **The oracle** | The tracer calls 0x545ebf after every PT and writes `pt, ct, checksum, rng, inputs, objects`; per-object rows on request. Same rows on both platforms; `compare.py` finds the first PT that differs [measured]. |
| **Results** | Each platform reproduces itself. **Mac = Windows for 24,300 PTs** (15 game minutes, 210 units) without the civilization timer: checksum, RNG, every unit's float position, the OOS dump. With lockstep the civilization pause and click match as well. The first real difference is at PT 24301: the Mac's computer player reviews its strategy and Windows' does not, on hidden AI state (§7, §8) [measured]. |
| **The autosave** | Saving a game has a side effect on the simulation: the computer player's serializer (0x43ded9) clears its "review scheduled" flag (+0xef6), so at its next update it reschedules its strategy review 30 s later. The 10-minute autosave failed on the Mac (`CreateFileA` with no access, kit), the Mac's computer player kept its old schedule and reviewed at PT 24301 when Windows' did not. Fixed in the kit (`ee` 35912bd): the Mac then equals Windows to the end of the game (29,827 PTs), and a LAN game like session 4E, whose out-of-sync had the same cause, stays in sync past two autosaves (38,112 PTs; §9) [measured]. |

## 2. The world checksum (0x545ebf)

### 2.1 Signature and caller [static]
- `__thiscall`, `ECX` = the `EEPhysics` object (the simulation world, `RTSPhysics` subclass; global pointer `[0x919774]`, created at 0x5387fa), two stack arguments `(char dump, uint flags)`, callee pops 8.
- One caller: the simulation's message pump `EEPhysics::ProcessMessages` (0x5470b0), on an `NEMPhysicsTick` (type 0x28) whose "generate checksum" byte (+0x12) is 1, when this machine is the host or a client (`comm->vfunc+0x10/+0xc`). `dump` is the tick's "dump the world" byte (+0x13); `flags` is always 1. The client then sends `NEMWorldDatabaseChecksumResponse` (0x32) with tick = the CT counter `comm+0x10` (netcode.md §6).
- **When:** at the start of a CT, before that CT's inputs run and before its PTs. So the multiplayer value for CT n is the world after the last PT of CT n−1, which is the tracer's row for that PT.

### 2.2 What it sums [static]
```
sum = Σ over objects o in the list ([0x918318], next = **(o+8)):  o->vfunc[0x4c/4](flags)
    + (flags & 0x200 ? Σ players, Σ selected units: unit+0x24 × player+0x45c : 0)
    + (flags & 0x40  ? Σ players: f(player+0x9cc) + f(player+0x9d0) (0x5432d3) : 0)
    + EEPhysics+0x80 + EEPhysics+0x18 + [0x919b68]
    + (flags & 4     ? 0x511de6([0x9183bc]) : 0)
    + (flags & 0x100 ? [0x917cd8] (the continent manager's checksum) : 0)
    + (flags & 0x10  ? 0x40cebd()->vfunc+8() : 0)
```
- The object list holds the game's units (the OOS dump prints each one's owner, type, tile and hit points). `EEComplexUnit`'s `+0x4c` (0x5ec507) with flag 1 adds **hit points (+0x3c) + tile x (+0x1c) + tile y (+0x20)**; flag 2 adds its physics model's fields (`[+0x30]`: update count +0x304, the transform's translation +0x13c/+0x14c as raw float bits, +0x15c, +0x210, the target's id); flag 8 sums attached objects' `+0x14`; flag 0x20 copies containers (allocations).
- **So the multiplayer check is coarse:** with flags 1 a unit's float position enters only through its tile, but the world RNG state is in every value, so a different number of random draws shows at once.
- The OOS dump labels (`OUT OF SYNCH DUMP: …`) name the fields: `EEPhysics+0xb4` "Dumped on PT", `+0x80` "Current PT", `+0x18` "this->mRandomNumberGenerator.GetSeed()", `[0x919b68]` "Number of user-input messages processed".

### 2.3 Calling it ourselves [static]
- **No effect on the simulation with dump 0:** the only store is the result into `0x919b6c` (one write xref, no reads); `TMGetTime` is called twice and its results dropped; a `UWideString` temporary is built and destroyed on the stack; `EEComplexUnit`'s flag-1 path builds an empty local container and frees it (`free(NULL)` twice), no allocation.
- **Where:** on the simulation thread between PTs, which is where the game calls it; the simulation thread is the only writer of the world, and it holds the world's write lock across its PTs. The tracer calls it right after each PT, from an after-hook on 0x546c63.
- **dump = 1** opens `OOS yyyy.mm.dd - hhh.mmm.sss.log` (time of day) in the working directory, writes every object's dump (`vfunc+0x44`), the players' groups and the continent manager, and calls `ESEndRecording` (the engine's debug-log recorder; no simulation state). The tracer's `RECOMP_EE_CHECKSUM_OOSDUMP=PT` uses it as the deep per-object breakdown.
- Flags other than 1 are only for diagnosis: flag 2 dereferences `[unit+0x30]` without a null check in `EEComplexUnit`, and other classes have their own `+0x4c`.

## 3. The simulation tick [static]

- **Thread:** `RTSPhysics::Main` (0x5532fb, legion's `EXE+0x15332b`) waits on a `TMTimer` event (`TSEvent::Wait(3000)`), sets PC=53 and round-to-nearest, takes the world lock (`vfunc+0x14`), runs one or two PTs (`vfunc+0x20`, 0x546c63) while `+0x60` (PTs owed) is positive and the CT budget allows, and unlocks (`+0x28`). Behind the server by more than one CT, it runs unpaced (`+0x60 = 100000`).
- **One PT** (0x546c63):
  1. 0x547365, the statistics report timer (§4.3);
  2. with no PT left in the budget (`+0xb8 == 0`), the message pump 0x5470b0: the next `NEMPhysicsTick` sets the budget (`+0xb8`, and `+0x80 += PTs`), the PT rate (0x553663, §4.1), counts the CT (`comm+0x10`), then runs the CT's inputs;
  3. game time `+0x94 += dt (+0x98)`; one draw of the world RNG (`+0xb0 = (int)URandom::Generate(+0x18)`); `+0xb8 −= 1`;
  4. the world: 0x53066a, 0x547b6b, 0x4a396b (continents), 0x547742, 0x547a03, **0x547aad (every object: `Object_SimUpdate` 0x547e0e)**, 0x4fc94e, 0x40cebd, 0x4a920d, 0x42f395, 0x5a1ed2 (victory), 0x545c8f;
  5. bookkeeping: `+0xb4 += 1` (**PTs executed: the tracer's N**), measured PT time into `+0xd8`/`+0xdc` (§4.1), the autosave check.
- **Message pump** 0x5470b0: also reached from 0x546c3b (vtable slot 7, the idle path) and 0x545cee (loading).
- **EEPhysics fields:** `+0x18` world RNG (`URandom` {state, 0x343fd, 2³¹, 0x269ec3}, state 1 at construction), `+0x50` seconds per PT, `+0x54` ms per PT (the timer), `+0x58` PTs per second, `+0x80` PTs scheduled, `+0x88`/`+0x8c`/`+0x98` rate and dt (game ms per PT), `+0x94` game time, `+0xb4` PTs executed, `+0xb8` PTs left in the CT, `+0xe6` paused.

## 4. Where wall-clock time reaches the simulation

### 4.1 The PT rate, hence the step size [static]
- dt per PT is `1000 × speed / PTps` game ms (0x5476c1: `+0x88 = PTps / [0x9183c0]`, `+0x8c = 1/+0x88`, `+0x98 = +0x8c × 1000`). PTps comes from each CT (`NEMCommunicationsTick` byte "PTs per second") via 0x553663, which also sets the simulation timer.
- The server sets it in `NESynchronousServer::TuneForSlowCPU` (engine 0x10055099), from every CT on: `PTps = trunc(1000 / (avg PT ms × 100/CPU share))`, clamped to `[Min PTs per Second, Max PTs per Second]` (registry, options+0x564/+0x560 = 0x91940c/0x919408, read with no clamp at 0x528d17/0x528d9f; the server constructor 0x10053460 via `EEServer` 0x4c2536). The average PT time is what the simulation measures with `TMGetTime` around each PT (`+0xd8`/`+0xdc`, copied into `comm[5]/[6]`). CPU share is `PT Rate Tuning Threshold (percent)` (30).
- PTs per CT = `trunc(PTps × interval / 1000)`, interval = the CT send interval (350 ms at start; tuned by `TuneForSlowNetwork`, which has no clients to measure in single player [inferred]).
- **So by default two single-player runs take different step sizes.** In multiplayer the host's values travel in every CT and all machines use them, which is why multiplayer agrees.
- **Pin:** `Min PTs per Second = Max PTs per Second = P` makes the clamp return P whatever was measured. The oracle uses P = 40: 14 PTs per 350-ms CT, 25 game ms per PT at standard speed.

### 4.2 The seed [static]
| Value | Source |
|---|---|
| The game seed | `srand(TMGetTime()); rand()` (0..32767) at Start Game (0x5c9cb5/0x5c9ccf; the lobby host at 0x67e1d6), sent as `EEMStartLoading` +0x14 |
| World RNG `EEPhysics+0x18` | the seed, in `EEMStartLoading`'s execute (0x4bee9d); no other writer (every other reference is `LEA ECX,[EAX+0x18]` to draw from it) |
| Map seed `[[0x91977c]+0x54]` | the same, 0x4beea9 (rmg.md §5.1) |
| Random starting epoch (Starting Epoch 14) | `URandomIntegers(map seed).Generate(0, ending epoch)` in world creation (0x52690c) |
| msvcrt `rand` elsewhere | the statistics interval (§4.3), a random UI sound (0x5c6f4b), the lobby; nothing in the simulation |

- **Forcing:** the tracers write the seed into the message at 0x4bee57's entry. The M1 tooling forced it at the map generator instead (`rmg_capture.js`: 0x66d402 argument 5 and `[[0x91977c]+0x54]`; legion's `seed_force.js` the same), which fixes the map but leaves the world RNG on the game's own random seed: right for M1 (Default.dll only), not for a simulation oracle.

### 4.3 Messages the simulation sends itself [static]
A message the simulation hands to the server (`comm->vfunc+0x18` = the EXE thunk 0x683bc8, `NESynchronousServer::ProcessLocalUserInput`) runs when a later CT carries it back. CTs are emitted by the server's `Send` thread on its own timer (engine 0x10055d2e), so **which CT a message lands in depends on thread timing**, not on the PT it was sent from [inferred from the code; measured in §7]. In an idle single-player game the senders on the PT path are:
| Function | Message | Trigger |
|---|---|---|
| 0x547365 (every PT) | `EEMReportStatistics` (0xa6), the local player's stats 0x14/0x15 | **wall clock**: every 2.5–5 s of `TMGetTime`, the interval drawn once with `srand(TMGetTime()); rand() % 2500 + 2500`, when the stats changed. Its execute (0x4bb713) writes the stats on every machine; it counts as a broadcast input (`[0x919b68]`, in the checksum) |
| 0x54778a (while `+0xe3`) | `EEMCivTimerExpired` (0xa7) | game time (`+0xd0`) against the civilization timer's thresholds |
| 0x547a03 (every PT) | `EEMExitShiftMode` (0x89) | every 4000 game ms, only with the local player's flag `+0xb49` |
| 0x5478bb | (`+0x70` pending list) | load/save handling |

- The tracers skip 0x547365 on request (`RECOMP_EE_SIMPARITY_NO_REPORTS`, `no_reports`), as a plain `RET`. It is the one wall-clock **trigger**; the others are game-time triggers whose landing CT can still vary.
- **The AI does not send messages** [static]: no sender is reachable from `EEComputerPlayer`'s 56 virtuals through direct calls to depth 6 (1,475 functions). It acts on the world directly, inside the PT, so its decisions are part of the deterministic simulation.

### 4.4 Other clocks on the simulation thread [static]
- `TMGetTime` inside the world update: 0x558f45 (reachable from the AI) throttles an alert **sound** for the local player (0x53bb1d); 0x4d15a2/0x4d164b time a tree walk and only yield; 0x5f92ff stamps a copied unit group at +0x40 when a command executes (its reader is not traced: none in an idle game) [static].
- None of `GetTickCount`, `QueryPerformanceCounter`, `time` or `GetSystemTimeAsFileTime` is reachable from the PT path (2,831 functions, direct calls to depth 8) [static].

### 4.5 Game speed, and covering more game per second [static]
- Speed is the float `[0x9183c0]` (1.0 = standard). It scales dt (§4.1), not the PT rate: the simulation stays paced by its timer, so a faster speed covers more game time per wall second **with a larger step**. It is set from the save game, from an `EEM` message (0x4b6bd4) and by the in-game control (0x5ad29b: `slider × 0.1 + 0.1`).
- There is no observer or all-AI mode in the base game's setup; the oracle uses a human player who does nothing.
- The scenario keeps the standard speed; a faster one is a single registry value on both sides if wanted, at the cost of a step size players rarely use at the start.

## 5. The oracle

### 5.1 The Mac tracer (`mods/simparity/`)
A kit mod plugin, built with `ee_boot` into `build/recomp/ee-mods/simparity/` (`simparity.dylib` and `mod.toml`). It uses only the public `pop_mod_api.h`: `hook_install_ex` on EXE entries (no game-view snapshot), `guest_call` with `ECX` for the thiscall checksum, guest reads and writes. **Opt-in:** `app/ee_standins.cpp` sets `RECOMP_MODS_DIR` to the `ee-mods/` beside the executable only when `RECOMP_EE_CHECKSUM_TRACE` is set; without it the plugin is never loaded, and loaded without it installs nothing.

| Variable | Effect |
|---|---|
| `RECOMP_EE_CHECKSUM_TRACE=FILE` | the stream (§5.3) |
| `RECOMP_EE_SIMPARITY_SEED=N` | force the game seed at 0x4bee57 (§4.2) |
| `RECOMP_EE_SIMPARITY_NO_REPORTS=1` | skip the wall-clock statistics report (0x547365, a plain `RET`) |
| `RECOMP_EE_SIMPARITY_LOCKSTEP=1` | the server builds a CT only once the simulation has run every PT it was sent (at most 60 s), so a message the simulation sends during CT n runs in CT n+1 (§4.3, §7); **not with autosaves** (§9.3) |
| `RECOMP_EE_SIMPARITY_NO_CIV_TIMER=1` | skip the civilization timer (0x54778a, a plain `RET`): no `EEMCivTimerExpired`, no "Choose Civilization" screen, no pause |
| `RECOMP_EE_CHECKSUM_UNITS=PT,…` / `_UNITS_EVERY=N` | per-object rows after these PTs |
| `RECOMP_EE_CHECKSUM_OOSDUMP=PT` | the game's own OOS dump after that PT |
| `RECOMP_EE_CHECKSUM_FLAGS=N` / `_MAX_PT=N` | the second checksum column's flags (default 1); stop after PT N |
| `RECOMP_EE_CHECKSUM_DRAWS=A-B` / `_TRACE_FNS=F,…` | `R` rows for PTs A..B (every call of a function that draws from the world RNG); `# call` lines for these functions in that window |
| `RECOMP_EE_CHECKSUM_MEM=PT,A,L;…` | `# mem` lines after PT: L bytes of the object whose pointer is at A |
| `RECOMP_EE_SIMPARITY_AI_SCHED=1` | `# sched` lines: a computer player schedules its next strategy review (0x43dcf4) |
| `RECOMP_EE_SIMPARITY_PLAYERS=N` | `P` rows after every Nth PT: each player's 12 words at +0xaf0 (resources and counters, which no checksum covers) |
| `RECOMP_EE_SIMPARITY_CALLS=F[/N],…` | `K` rows: every call of these functions at any PT, with N stack arguments (default 3, the rest 0), the player slot ECX is (or −1) and EAX |
| `RECOMP_EE_SIMPARITY_MEMFN=F,L;…` | `# memfn` lines: L bytes of ECX's object on every entry of F |
| `RECOMP_EE_SIMPARITY_AUTOSAVE=1` | `# autosave` lines: the host's autosave counter (`EEPhysics+0xcc`) restarting, i.e. a save posted (§9.1), and the counter past the interval with nothing posted (a blocking flag set) |

Hooks:
- always: after 0x546c63 (a row per PT), before 0x4bee57 (the seed), before 0x683bc8 (messages to the server), before and after 0x545ebf (the game's own calls, `W` rows; the tracer's own calls are not counted);
- on request: before 0x547365 and 0x54778a (skipped), before 0x4c3703 only when `SendCommunicationsTick` calls it (`EEServer` vtable +0x28, asked once per running CT on the server's send thread; the return address engine 0x10054c6b), and with lockstep the civilization screen's timeline (0x4b4f9c, 0x662f64; §7.4).

Every hook is on an EXE entry: the kit's mod hooks reach the main image's entries only, so engine functions are reached through the EXE's thunks and vtables. **Multiplayer:** with only `RECOMP_EE_CHECKSUM_TRACE` set (no seed, no skipped timers, no lockstep) the tracer changes nothing in the game, and a Mac host's `W` rows are its own per-CT checksums, which are never on the wire (P5-E). `ee_boot` finds the mod beside itself; the windowed app (`build/EmpireEarth.app`) does not, so with the app set `RECOMP_MODS_DIR=<checkout>/build/recomp/ee-mods` as well.

### 5.2 The Windows twin (`windows/frida/checksum_trace.js`)
The same hooks with Frida (`Interceptor.attach`/`replace`; `NativeFunction(…, 'thiscall')` for 0x545ebf and the per-object `+0x4c`), the same rows written with Frida's `File`. Run with `windows/frida/run.py` attached at the main menu (legion request 0027).

### 5.3 Rows
```
G,seed_orig,seed,humans                    a game starts (EEMStartLoading): the game's seed, the one used
C,ct,total_pt,pts,ptps,dt_bits,speed_bits  a CT's PTs arrived (EEPhysics+0x80 changed)
T,pt,ct,cs,cs2,rng,inputs,units            after each PT: checksum (flags 1), second checksum, world RNG,
                                           [0x919b68], objects in the list
S,pt,ct,type,slot                          a message handed to the server (type = message byte +8)
U,pt,i,vtable,id,x,y,hp,cs,cs2             one object: vtable, id (+0x24), tile x/y (+0x1c/+0x20),
                                           hit points (+0x3c), its +0x4c(1)
W,ct,pt,dump,flags,cs                      the game's own checksum call (multiplayer, once per CT)
R,pt,fn,caller,rng_in,rng_out              a call of a function that draws from the world RNG (a window)
P,pt,slot,w0,..,w11                        a player's 12 words at +0xaf0: resources and counters
K,pt,fn,caller,self,a0,a1,a2,eax           a logged call: self = the player slot ECX is, or -1
```
`compare.py` compares `P` rows from word 2: resources 0 and 1 are never written for the world (slot 0) and the human player (slot 1), so they hold heap garbage that differs between runs and machines (the Mac 0x81818181/0, Windows 0/random) [measured]. `K` rows are compared without a2 and EAX, which are often stack garbage or a void return.
`#` lines are comments: the options, the PT-rate settings the server got, lockstep waits, the civilization screen's timeline.
Pointers never appear in a row (heap addresses differ between platforms); the object id and vtable identify an object.

### 5.4 The scenario (`tests/menus/simparity.script`) and the runner
- Main menu → Single Player → Play Random Map → Start Game with the setup screen's defaults (Continental, Small, one computer player, the standard speed: ×1.5, 37.5 game ms per PT at 40 PT/s), then nobody at the controls but for one click: 5 game minutes in (PT 8000) the civilization timer opens "Choose Civilization" with Ancient Greece preselected, which pauses a single-player game, and the script clicks OK (400, 520) once. `--minutes` counts from there. `# menus: manual`: the menu test skips it.
- With `--no-civ-timer` the runner leaves the civilization block out of the script, and the game has no screen, no pause and no click.
- `tools/simparity/run.py` (`tools/test.py --simparity`): a fresh user directory per run, `seed_user.py`'s registry (legion's options, 800×600) with **Min = Max PTs per Second = 40**, seed 1000, the reports skipped; `ee_boot` in strict mode under `tools/game_ports.py`'s lock; the mod copied per run (a rebuild cannot touch a running game); streams to `analysis/simparity/mac/` (ignored). With two runs or more it compares them. `--autosave-ms MS` sets the registry's `AutoSave In Milliseconds` (seed_user's 600000); `--against STREAM` compares every run with a stored stream, hidden rows included; when `K` rows of the save (0x540aca) are logged, a run fails if a save returned non-zero or fewer saves happened than were due.
- Two streams whose saves landed at different PTs (§9.3) are reported as such, and a difference that follows such a save is not counted against them.
- `--load SAVE` starts from a saved game instead (`tests/menus/simparity-load.script`: Single Player → Play Saved Game → the first entry → Load, with SAVE alone in the user directory's `Data\Saved Games`); `--minutes` then counts from the load. `--keep-saves` copies the saves a run wrote to `OUT/mac-<k>-saves/`. `--autosave-watch` turns on the `# autosave` lines, and then a run fails if an autosave was posted and never ran, or was due and not posted (§10.5). `--trace-match TEXT` writes `ee_boot`'s import trace of the matching calls to `OUT/mac-<k>-imports.txt`.
- `tools/test.py --simparity` without options: two 3-minute games, no civilization timer, an autosave every minute (`P` and `K` rows on), compared with each other and with legion's `win-as-1` when `analysis/simparity/win/win-as-1/stream.csv` is here.
- `tools/simparity/compare.py A B`: the first PT whose row differs and which fields, rows around it, the CT schedule's first difference, the message types sent up to there, and the objects that differ at the latest common per-object snapshot (matched by vtable and id). `tests/test_simparity.py` checks it on synthetic streams, and that both tracers and the comparer declare the same rows.

## 6. The Windows side (legion)
| Request | Runs | Output (`C:\EEPort\captures\simparity\`) |
|---|---|---|
| 0027 | `win-1`, `win-2`: seed 1000, no input in game, to the civilization pause (PT 8022); `win-2` with the OOS dump at PT 4000 | `stream.csv`, screenshots, `OOS … .log` |
| 0030 → 0033 | `win-ok-1`, `win-ok-2`: the same, then OK once on the civilization screen, 10 minutes more (to PT 24187/24183) | the same |
| 0034 | `win-nc-1`, `win-nc-2`: no civilization timer, 12 minutes (to PT 28863/28851); `win-ls-1`: the OK variant with lockstep (to PT 32163) | the same |
| 0035 + 0036 | `win-ph-1`, `win-ph-2`: no civilization timer, `flags2=3`, per-object rows around PT 24301, the OOS dump at PT 24300, `R` rows for PTs 24290–24305 | the same |
| 0045 | `win-ai-1`: as `win-ph`, with `P` rows every PT, `K` rows of the computer player's resource adds, the computer player on every review entry (`memfn`), `# sched`, `# call` lines in PT 24301 | the same |
| 0046 | `win-as-1`: an autosave every minute (registry 60000), 5 minutes; `win-long-1`: the default autosave, 25 minutes; both with `P` rows every 10 PTs and `K` rows of the schedule, the review, the AI's serializer and the save | the same |
| 0047 + 0049 | `win-long-2`: as `win-long-1` with the easiest computer player (`Difficulty Level` and `Computer Player Difficulty Level` 0), so the idle player lives past the second autosave; the lockstep runs of 0047 were dropped (§9.3) | the same |
| 0049 | `win-isl-1`: as `win-long-2` on Small Islands, 30 minutes, three autosaves (PT 24011, 48007, 72017) | the same |
| 0065 | `win-load-48007`, `win-load-24011`: `win-isl-1`'s second and first autosaves loaded (Play Saved Game), 12 and 5 minutes | the same |
| 0066 | `win-as-2`: `win-as-1` again, its save files kept (`saves\`); `win-load-mac-2423`: the Mac's first one-minute autosave loaded, 5 minutes | the same |
| 0067 | `win-as-3`…: `win-as-2` again until the first autosave lands at PT 2423, the Mac's, for the file comparison (§10.4) | the same |

Streams are fetched to `analysis/simparity/win/` (ignored) with `scp`. Legion's Frida run writes CRLF line ends, which `compare.py` ignores.

## 7. Results

### 7.1 The setup [measured]
- **PT rate:** without the pin, the Mac's first single-player games ran at 44 PT/s falling to 14 as the measured tick time grew (TuneForSlowCPU), legion's at 40–44. With Min = Max = 40 both run 40 PT/s, 14 PTs per CT, dt `0x42160000` (37.5 game ms), speed `0x3fc00000` (×1.5, the "Standard" game speed), from the first CT to the last.
- **Seed:** the games drew 1510 (Mac) and 26343 (legion) themselves; forced to 1000 at 0x4bee57 both play the same map, epoch and AI from PT 1.
- **Loading** takes 30–70 CTs on the Mac and about 110 on legion before PT 1; `compare.py` counts CTs from the first that brings PTs.

### 7.2 Mac vs Windows [measured]
| Comparison | Common PTs | Result |
|---|---|---|
| Mac (no lockstep) vs `win-1` | 8022 | identical but for three 14-PT windows (PT 6413–6426, 7211–7224, 8009–8022): the civilization timer's message (sent at PT 6400, 7200, 8000) runs in the next CT on Windows and two CTs later on the Mac. The world RNG never differs; from PT 6427, 7225 on the streams are equal again |
| Mac with lockstep vs `win-1`, `win-2` | 8022 | **identical**: checksum, world RNG, executed inputs, objects, CT schedule |
| per-object rows, Mac vs `win-1` | 8 snapshots (PT 1000…8000), 88–129 objects | **identical** (every object's id, tile, hit points and checksum) |
| the game's OOS dump at PT 4000, Mac vs `win-2` | 10,643 lines | **identical** but for "Number of Messages in Server Queue" (0 vs 1: whether the next CT is already queued) |

So the translated simulation, x87 included, evolves bit for bit as Windows' for at least 8022 PTs (5 game minutes, 129 units, the AI building and gathering, about 200 seconds) [measured].

### 7.3 Each platform against itself [measured]
| Comparison | Common PTs | Result |
|---|---|---|
| Mac vs Mac, no lockstep (to the pause) | 8064 | identical |
| Mac vs Mac, lockstep, through the civilization pause and the OK click | 15,787 | identical |
| `win-1` vs `win-2` (to the pause) | 8022 | identical |
| `win-ok-1` vs `win-ok-2` (through the pause and the click) | 24,183 | identical |

With the PT rate pinned, the seed forced and the wall-clock report skipped, each platform reproduces itself exactly. Without the pin no two runs could agree (§4.1).

### 7.4 The pause with lockstep, on both platforms [measured]
- With lockstep, Windows (`win-ls-1`) behaves exactly as the Mac: the simulation stops in the middle of the CT whose first PT runs the civilization expiry (13 PTs left on Windows, 10–11 on the Mac), the gate waits its full 60 s, then the CT goes out, the simulation finishes it and the next, and the pause lands after PT 8036; the OK click runs at PT 8037.
- **Mac (lockstep, OK) vs `win-ls-1`: all 15,946 common PTs agree**, and so do the 15 per-object snapshots up to PT 15000 (171 objects).
- Without lockstep the platforms differ only in where the pause lands: Windows after PT 8022 (four runs out of four), the Mac after 8064.

### 7.5 What still differs between the platforms: where a pause lands without lockstep
- After the civilization timer expires (its message runs at PT 8009 on both), Windows pauses after PT 8022 in all four runs, the end of that CT; the OK click's message (type 0x71) then runs at PT 8023.
- The Mac pauses later: after PT 8064 without lockstep (the server three CTs ahead) and after PT 8036 with lockstep. With lockstep the simulation stalls at PT 8012, mid-CT, until the gate gives up, so the pause is asked for while CT 604 is already on its way.
- The pause comes from the UI thread (0x662f64 opens the screen and calls `NESynchronousServer::PauseGame`, which applies from the server's next CT), so where it lands depends on how fast the UI reacts compared with the simulation. That is thread timing, not simulation.
- Consequence: the OK-variant streams agree up to PT 8022 and differ from 8023, the CT the click runs in.

### 7.6 The first simulation difference: PT 24301 [measured]
- **Setup:** with no civilization timer the game sends no messages at all. Windows (`win-nc-1`, `win-nc-2`) and the Mac (two runs each) then each reproduce themselves for the whole 12 minutes (28,851 and 28,617 PTs). **Mac vs Windows agree for 24,300 PTs** (15 game minutes, 210 units).
- **At PT 24301** both create two units (210 → 212). The objects' part of the checksum is the same on both sides (checksum − RNG = `0x16053`), but the world RNG is not: from the same state, Windows draws 30 numbers in that PT and the Mac 105 (LCG steps counted). A typical PT draws about 20.
- **The Mac's draw trace** (`R` rows) puts the 75 extra draws in one place: 75 calls of `RTSPhysics::RandomInt` (0x5531c2, an integer from the world RNG) from 0x42f2cc, `random index below n`, the generator of the game's three `std::random_shuffle` instantiations (0x42f275, 0x47956d, 0x44e16e; element sizes 8, 4, 8). So in PT 24301 the Mac shuffles a 76-element sequence that Windows does not shuffle, or shuffles with one element at most.
- **No float drifted before it:** in `win-ph-1` and the Mac's run with the physics column (`flags2=3`: every unit's model, i.e. float position bits, update count and target), the second checksum agrees through PT 24300, and every object still agrees at PT 24301 (212 objects, both columns). Only the world RNG differs at the end of PT 24301.
- **Both draw traces** (`R` rows, PTs 24290–24305) are identical but for one insertion. In PT 24301, after the same two float draws, the Mac runs seven `random_shuffle`s (75 draws) before the AI's draws at 0x46370e (`EECPAttackSubGroupUnit` vtable slot 8), which both platforms then make with the same values shifted. The Mac's call trace names them: one from 0x42f912 (a computer player's strategy review: it lists candidate strategies from conditions and shuffles them), then six from 0x4874f6.
- **Why the Mac reviews and Windows does not:** the computer player's update (0x43f91a, every PT, `now` = game ms) calls the review only when `now >= this[0xedc]`, the time scheduled at the previous review by 0x43dcf4: `next = (int)(float)(now + this[0xc5c] * 1000)` (an `FIADD`, an `FSTP m32` and a nearest-even `FISTP`, all exact in the kit's x87 model). So the scheduled time or its inputs (the review interval at +0xc5c, which comes from a global table at 0x8d0990, or the time of the previous review) differ, and none of those is in any checksum. **Found (P4-D, §9):** Windows rescheduled at PT 24031 after the autosave; the Mac's save had failed.

## 8. Open items
1. ~~**PT 24301**~~: the autosave (§9). Fixed in the kit; proof in §9.4.
2. **The x87 stack.** The kit's `fpush` does not model a push onto a full stack: x87 with invalid masked loads the real indefinite (a NaN) and sets C1/IE, while the kit keeps the value. The tracer now logs any PT after which the x87 stack is not empty (`# fpu:`). A leak would make that difference reachable.
3. **The civilization pause without lockstep** (§7.5) lands on different PTs on the two platforms; with lockstep on both it lands on the same one. A deterministic pause needs either lockstep on every machine, or holding the UI's `PauseGame` (0x662f64) until the simulation has finished its CT; the latter deadlocks if the UI holds the world lock.
4. **Coverage:** one map (Continental, Small), one computer player, standard speed, about 15 game minutes. Next: more players, other scripts and epochs, a faster speed, and the LAN path, where the CT stream is the host's and the `W` rows carry every machine's per-CT checksum (P5-E).
5. **A regression test.** `tools/test.py --simparity` now plays two 3-minute Mac games with an autosave every minute: every save must succeed, and they must agree with each other and, with legion's `win-as-1` stream present, with Windows, up to a save that landed in a different CT (§5.4, §9.3). The stream is game state and stays out of git.
6. **Saving changes the simulation.** Besides the review flag (§9.1), each save shifts the computer player's update phase by a few PTs (§9.3). That is the game's behaviour on every machine, harmless while every machine saves at the same PT, which the host's CT stream guarantees in multiplayer. A machine whose save fails, or a mod that saves on its own, goes out of sync.
7. **A Mac autosave that did not happen** [measured, cause open]. In `isl1` the Mac's second autosave (due after PT 48000) never ran: no save message, no CT pause, no file, while Windows saved at that point and two more traced Mac runs of the same game (`isl2`, `isl3`) did.
   - That run logged neither the post (0x547676) nor its handler (0x545b79), so it is not known which step was skipped. Every other autosave the Mac was due to make in these runs ran: 30 of 31 in all. The 23 traced end to end (`isl2`, `isl3`, `as30`) were each posted, handled on the simulation thread (message 0x50a through `TSThread::Receive`, i.e. the kit's `PeekMessageA`), executed and written.
   - Suspects: the post, which goes through the kit's `PostThreadMessageA`; and the blocking flags at 0x546f13 (`[0x918ea0]`, `[[0x918e24]+0x237]`), whose writers 0x5a1cda, 0x5a45ca and 0x4ffdd9 never ran in the traced runs.
   - **No out-of-sync can follow from it in multiplayer:** only the host posts the autosave, so a host that skips one skips it for every machine. In single player the oracle shows it as a difference after PT 48000. `run.py` counts saves against those due, but tolerates one missing at the end.

## 9. The autosave: the cause of PT 24301 and of the LAN out-of-sync (P4-D)

### 9.1 The mechanism [static, measured]
1. **The trigger, host only.** At the end of each PT (0x546c63) the host (`comm->vfunc+0x10() == 1`) adds the timer's ms per PT (`EEPhysics+0x54`, 25 ms at the oracle's 40 PT/s) to `EEPhysics+0xcc`. Once that passes `AutoSave In Milliseconds` (`[0x91943c]`, 600000 by default), it resets the counter and **0x547676** posts the simulation's own thread the message 0x50a (`TSThread::Post`). At 40 PT/s that is after PT 24000, 48000, … [static; measured: PT 2401 with a 60000-ms interval].
2. **The message.** The simulation thread handles 0x50a after its PTs (`RTSPhysics::Main` → 0x546b26 → **0x545b79**). That function names the save `AutoSave <date> - <time>` and queues an `EEMLoadOrSaveGame` (vtable 0x8293a0) with `NESynchronousServer::SendToAllAndWait`, which only appends it to the server's list (engine 0x10054e7c). The next CT carries it to every machine [static].
3. **The save, on every machine.** `EEMLoadOrSaveGame`'s execute (0x4b876d) queues itself on `EEPhysics+0x70` (0x546f57). At the CT's last PT (`+0xb8 == 0`), 0x5478bb saves (0x4b890f → **0x540aca**). 0x540aca opens `data\Saved Games\AutoSave ….ees` (0x540b4a: `CreateFileA(name, 0, 0, NULL, CREATE_NEW, …)`, then the write) and serializes the world (0x540da3), every player included [static; measured with `K` rows].
4. **The side effect.** `EEComputerPlayer`'s serializer, **0x43ded9**, clears the live object's "review scheduled" flag (`+0xef6 = 0`, at 0x43df6f, unconditionally) and then writes it. At the player's next update, 0x43f91a sees the flag clear and calls 0x43dcf4(now): `next review = now + 30 s` (`+0xc5c` = 30.0). So **every successful save moves the computer player's next strategy review** to 30 s after the save [static; measured].
5. **On the Mac the save failed.** The kit treated `CreateFileA` with access 0 as a read and returned `INVALID_HANDLE_VALUE` for a file that did not exist yet, so 0x540b4a returned 1 and 0x540aca never serialized anything. The game showed "Error Creating File" (lansession.md §9.2 item 4). The computer player's flag stayed set, so its review stayed where it was [measured: `as2`, `K` rows: 0x540b4a → 1, no 0x540da3, no 0x43ded9].

So in the oracle's game:
- Windows saved at the end of a CT near PT 24010 (`win-long-1`: after PT 24010) and rescheduled at PT 24031 (`now` 901162 → next 931162, i.e. PT 24841);
- the Mac kept the review due at 910912 and ran it at PT 24301.

The review's `random_shuffle`s are the 75 extra draws of §7.6. Nothing else differed [measured, `win-ai-1` vs `mac/ai1`]:
- `P` rows (all players' resources, every PT) and `K` rows (every resource deposit) agree through PT 24300;
- the computer player's object (`memfn`) agrees at all 30 reviews up to PT 23491. It was compared word by word, a word passing when its Mac and Windows values map one to one across the reviews, which lets pointers through; the words that failed are all heap pointers.

### 9.2 The fix
- **Kit** `ee` 35912bd (P5-F's f4f711c, `runtime/kernel32.cpp`): `CREATE_NEW`, `CREATE_ALWAYS` and `TRUNCATE_EXISTING` create or truncate whatever the access asked for, as on Windows. `loader_tests` covers it.
- **ee-macos:** `run.py` fails a run whose save returned non-zero, or that saved fewer times than were due, and `tools/test.py --simparity` saves every minute (§5.4). The game's own behaviour (a save that touches the simulation) is left as it is: Windows peers do the same.

### 9.3 Where a save lands in single player [measured]
- In single player the save message lands in whichever CT the server builds after the simulation thread has queued it. Whether the server's send thread or the simulation thread gets there first is thread timing, as for the messages of §4.3.
- `win-as-1` against the Mac's `as3` (an autosave every minute, no lockstep): the first save landed at the same CT (after PT 2422) on both, and both rescheduled at PT 2431. The second landed one CT earlier on Windows (after PT 4802) than on the Mac (after 4816); the computer players then rescheduled at 4807 and 4834, and the streams differ from PT 5617.
- Each save also moves the computer player's update phase. Its next update came 4–26 PTs after the save, depending on the save's PT, not every 10 PTs from PT 1. The rule is the same on both platforms for the same save PT.
- **In multiplayer this cannot differ:** only the host decides, and its CT stream carries the message to every machine.
- **Lockstep does not help, and hangs.** A gate that also waited for the posted save to be queued hung the Mac's game for good at the first autosave (PT 2403; the gate gave up after 60 s and the simulation never resumed) [measured]. The gate runs inside the server's `SendCommunicationsTick`, and `SendToAllAndWait` needs the server's critical section (engine 0x10054e7c), so the save cannot be queued while the gate waits [static]. The simulation thread never got going again after the gate gave up, so a second lock is involved, presumably the world's. The gate was removed. Plain lockstep likely has the same exposure whenever the simulation thread handles the save while PTs of its CT are left [inferred], so the autosave runs here are without lockstep.
- So in single player a difference that follows a save which landed at different PTs is timing, and `run.py` reports it as such (§5.4). A save that lands in a different CT changes nothing when the computer player's next update is the same PT, as at the first autosave of `long1`/`win-long-1` below.

### 9.4 Proof
All [measured], seed 1000, no civilization timer, 40 PT/s, Mac kit `ee` 35912bd:

| Comparison | Common PTs | Result |
|---|---|---|
| Mac `long1` (the default autosave) vs `win-long-1` | **29,827** (18.6 game minutes, to the end of the game: the idle human player is defeated at PT 29,827 on both) | **identical**: checksum, world RNG, objects, executed inputs, every player's resources (`P` rows, every 10 PTs). The autosave serialized the computer player on both and both rescheduled at PT 24031 with the same times. The save itself landed one CT apart (after PT 24024 on the Mac, 24010 on Windows; §9.3), which this time did not reach the simulation |
| the same Mac run vs `win-nc-1`, `win-nc-2`, `win-ph-1`, `win-ph-2`, `win-ai-1` (P4-C's and 0045's streams) | 26,431–28,863 | **identical** over each stream's whole length, PT 24301 included |
| Mac `long2` vs `win-long-2` (the easiest computer player) | **29,503** (to the idle player's defeat, on both at that PT) | **identical**, `P` rows included; the save landed after PT 24024 / 24010, the review rescheduled at PT 24031 on both |
| Mac `isl1` vs `win-isl-1` (Small Islands, the easiest computer player: the idle player is never attacked; `--reg 'Game Options\Map Type=Small Islands'`) | **48,330** (30.2 game minutes) | **identical** through the first autosave (Mac after PT 24024, Windows after 24010, both rescheduled at 24031). Windows saved again after PT 48006 and 72016; **the Mac's second autosave did not happen** (no save message, no CT pause), and the streams differ from PT 48331, the computer player's next review (§8 item 7) |
| Mac `isl2` (the same, traced) vs `win-isl-1` | **48,816** | **identical** through both autosaves' posts; the second save landed one CT later on the Mac (after PT 48020 against 48006), the reschedule followed it (PT 48034 against Windows' earlier one), and the streams differ from PT 48817: single-player save timing (§9.3) |
| Mac `as30`: an autosave every 30 s, 22,867 PTs, traced | | 19 of 19 autosaves posted, handled, executed and written; the game kept 10 files, deleting the oldest |
| **LAN**, session 5G (lansession.md §10): legion hosts, a computer player, the Mac joins headless; both tracers passive | **38,112** (24 minutes, two autosaves at PT 22,790 and 35,722 on both) | **identical** on every PT, `P` and all 144 `K` rows; the Mac's `0x32` equals legion's world checksum on all 4,152 ticks; no out-of-sync |
| Mac `as3` vs `win-as-1` (an autosave every minute, no lockstep) | 11,183 | the first save at the same PT on both, and identical to PT 5616; then the second save landed one CT apart and the streams differ from PT 5617 (§9.3) |

## 10. Saves: loads on both platforms, the files, the autosave that did not run (P4-E)

Saving changes the simulation (§9), so a save has to be written, loaded and timed alike on every machine. Mac runs: ee-macos `phase4/saves`, kit `ee` 85caa75; Windows: legion 0065–0067. All [measured] unless labelled.

### 10.1 What a load does
- **The path.** Single Player → Play Saved Game → a file → Load (`tests/menus/simparity-load.script`, `run.py --load`). The game sends `EEMStartLoading` as for a new game (a `G` row with its own seed), then the load (0x540a15) reads the file. The stream resumes at the save's PT: the first `T` row is the loaded state, before any PT runs.
- **The file restores the world RNG**, and the load then draws from it itself: on `win-isl-1`'s 48007 save the first draw starts from `39eaf325`, the RNG the original game had after PT 48006, and the load makes 50 draws before the first PT (`R` rows: 13 each from 0x61ad41, 0x5ecd26 and `RandomInt` from 0x61b11b, the rest from the computer player's code at 0x4304f2–0x43065d and four other sites). That load also has 2 more objects than the original (285 against 283); a load of the Mac's PT-2423 save has the original's 95.
- **So a loaded game does not continue the stream of the game that saved it**: with the RNG 50 draws on, the checksum differs from the first PT. It is the game's own behaviour, the same on Windows (0065: 285 objects; every checksum equal to the Mac's load, §10.2). The computer player's plans are unaffected at first: its reschedule (PT 48007) and its resource deposits from PT 48044 come at the same PTs, with the same arguments, as in `win-isl-1`.
- **Deterministic, seed-free:** two loads of the same file give the same stream, with seed 1000 forced at `EEMStartLoading` or with the game's own seed.
- **The autosave counter is not saved:** after a load the next autosave is posted 24,001 PTs after the load PT (after PT 72007 for the 48006 load), on both platforms.
- **A foreign save loads silently:** Windows loads the Mac's file (renamed) with no message, and the Mac loads Windows' files.

### 10.2 Parity: the same save loaded on both platforms
| Save | Loaded on the Mac vs on Windows | Common PTs | Result |
|---|---|---|---|
| Windows `win-isl-1`, after PT 48006 (`AutoSave … 12h.11m.22s`; Small Islands, the easiest computer player) | `mac-load-48007` vs `win-load-48007`, 12 minutes | **28,715** (PT 48006–76720) | **identical**: checksum, world RNG, objects, inputs, CT schedule, `P` rows. The post-load autosave was posted after PT 72007 on both and landed one CT apart (after PT 72030 on the Mac, 72016 on Windows; §10.3), which did not reach the simulation in the time left |
| Windows `win-isl-1`, after PT 24010 (`… 12h.01m.21s`) | `mac-load-24011` vs `win-load-24011`, 5 minutes | **11,962** (PT 24010–35971) | **identical**, `P` and `K` rows included |
| Mac `mac-as1`, after PT 2422 (Continental, the one-minute autosave of the `win-as-1` game) | `mac-load-mac-2423` vs `win-load-mac-2423`, 5 minutes | **11,989** (PT 2422–14410) | **identical**, `P` and `K` rows included |

So a save carries the game between platforms both ways, and the translated load path behaves as Windows': the 50 draws, the extra objects and every later PT.

### 10.3 Where an autosave lands in single player
- The post is deterministic (§9.1): after the same PT on both platforms, from the game's start or from a load.
- Where it lands is the next CT the server builds after the simulation thread queued it (§9.3), and that differs:

  | Interval | Mac | Windows |
  |---|---|---|
  | 10 minutes | one CT later, every time: after PT 24024 against 24010 (`long1`, `long2`, `isl1–3`), 48020 against 48006, 72030 against 72016 after a load | the next CT |
  | one minute | the first after PT 2422 (4 runs out of 4), later ones a CT after Windows (4830 against 4802) | after PT 2422 or 2408 (`win-as-1`, `win-as-2`) |

- **Consequence:** in single player a Mac game and a Windows game agree until a save lands in different CTs and the computer player's next review falls at a different PT (§9.3). In multiplayer the host's CT carries the save, so every machine saves at the same PT.
- [inferred] The Mac's server runs further ahead of its simulation than Windows' does. That is thread timing in the kit's cooperative scheduler, not simulation.
