# Single-player simulation parity: the checksum-stream oracle (P4-C)

Status: Phase 4, workstream P4-C, 2026-10-15. Static RE done; Mac tracer, scenario and comparison tool implemented; Mac↔Mac and Mac↔Windows results in §7. Owner: P4-C (simparity).
Code:
- [`mods/simparity/simparity.c`](../../mods/simparity/simparity.c): the Mac tracer, a kit mod plugin (public `pop_mod_api.h` only);
- [`windows/frida/checksum_trace.js`](../../windows/frida/checksum_trace.js): its Windows twin (legion 0027);
- [`tests/menus/simparity.script`](../../tests/menus/simparity.script): the scenario;
- [`tools/simparity/run.py`](../../tools/simparity/run.py) and [`compare.py`](../../tools/simparity/compare.py); `tools/test.py --simparity`.

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
| `RECOMP_EE_SIMPARITY_LOCKSTEP=1` | the server builds a CT only once the simulation has run every PT it was sent (§4.3, §7) |
| `RECOMP_EE_CHECKSUM_UNITS=PT,…` / `_UNITS_EVERY=N` | per-object rows after these PTs |
| `RECOMP_EE_CHECKSUM_OOSDUMP=PT` | the game's own OOS dump after that PT |
| `RECOMP_EE_CHECKSUM_FLAGS=N` / `_MAX_PT=N` | the second checksum column's flags (default 1); stop after PT N |

Hooks: after 0x546c63 (a row per PT), before 0x4bee57 (the seed), before 0x683bc8 (messages to the server), and on request before 0x547365 and 0x4c3703 (`EEServer` vtable +0x28, which `SendCommunicationsTick` asks once per running CT on the server's send thread). Every hook is on an EXE entry: the kit's mod hooks reach the main image's entries only, so engine functions are reached through the EXE's thunks and vtables.

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
```
Pointers never appear in a row (heap addresses differ between platforms); the object id and vtable identify an object.

### 5.4 The scenario (`tests/menus/simparity.script`) and the runner
- Main menu → Single Player → Play Random Map → Start Game with the setup screen's defaults, then nobody at the controls until the deadline. `# menus: manual`: the menu test skips it.
- `tools/simparity/run.py` (`tools/test.py --simparity`): a fresh user directory per run, `seed_user.py`'s registry (legion's options, 800×600) with **Min = Max PTs per Second = 40**, seed 1000, the reports skipped; `ee_boot` in strict mode under `tools/game_ports.py`'s lock; the mod copied per run (a rebuild cannot touch a running game); streams to `analysis/simparity/mac/` (ignored). With two runs or more it compares them.
- `tools/simparity/compare.py A B`: the first PT whose row differs and which fields, rows around it, the CT schedule's first difference, the message types sent up to there, and the objects that differ at the latest common per-object snapshot (matched by vtable and id). `tests/test_simparity.py` checks it on synthetic streams, and that both tracers and the comparer declare the same rows.
