# Community reverse-engineering knowledge, verified against the GOG binaries (2026-10-14)

Gathered by the Phase 1 agent *community-re*. Every address claim was checked statically against our GOG modules (hashes in [PLAN §2.4](../PLAN.md)) with pefile and capstone.

- **Machine-readable output:** [`db/community-symbols.json`](../../db/community-symbols.json), 261 entries.
- **Verifier:** `analysis/community-re-verify/` (gitignored; `python -I verify.py --write` regenerates the JSON).
- **Labels:**
  - **[verified]**: a static check passed (bytes, function hash, export, disassembly pattern or code reference).
  - **[unverified]**: plausible location, but the claim is a runtime observation with no static check.
  - **[inferred]**: our own deduction, usually from two sources agreeing.
- **Addressing:**
  - EXE addresses are VAs (fixed base `0x400000`, no relocations).
  - DLL addresses are RVAs, because the engine and plugins are relocated at run time. A "preferred VA" assumes base `0x10000000`.
- **Legal hygiene:** this file and the JSON hold only facts (addresses, offsets, names, our own descriptions).
  - Many sources have no licence. No code was copied, and no game bytes or instruction listings are tracked.
  - Signatures used for checking stay in the gitignored verifier.

## 1. Headline

| | Count |
|---|---|
| Address/offset claims about the GOG base game, as DB entries | 251 |
| … of which **[verified]** | **240** |
| … **[unverified]** (runtime observations: struct semantics, crash sites, BSS globals with no direct code reference) | 9 |
| … **[inferred]** | 2 |
| **Mismatches** | **0** (two source errors corrected, §8) |
| Claims for other builds (AoC, NeoEE, no-CD), recorded as n/a | 10 groups, ~90 addresses |
| AoC counterpart addresses recorded on base-game entries | 64 |

**Most valuable findings:**
1. **64 simulation-thread x87 hot functions in the EXE match byte for byte** (FNV-1a over the whole function).
   - RTTI vtable lookup ties most of them to the physics-model classes: `EEPhysicsModel` and `EEPMLand` / `Hovercraft` / `Naval` / `AirAnimal` / `SeaAnimal` / `Projectile` / `Building` / `Airplane` / `Missile` / `Helicopter`.
   - 00xJS has proved them translatable to SSE2 bit-exactly at 53-bit precision.
2. **36 engine maths functions** (`UMCos`, `UMArcCos`, `UMArcTan2`, `GETransformation::*`, `GE3D*`, culling, projection) match after relocation normalisation.
   - Each has a documented operation-order and rounding description.
   - The trig tables are static data in the DLL (§3.2).
3. **The determinism hooks for a checksum oracle:**
   - the per-object update call (`0x547b5c` → `0x547e0e`);
   - the per-tick world-update call (`0x546d6b` → `0x547aad`);
   - the CPU-share global (`0x919424`);
   - the tick-rate clamp in `NESynchronousServer::TuneForSlowCPU`.
4. **Struct layouts for the engine's core graphics classes**, checked by displacement presence in the hashed functions: `GEModel`, `GEViewport`, `GEMaterial`, `GEBoundingBox`, `GETerrainMesh`, `TSReadWriteLock`, `FSMemoryFile`, `UIHotKey`.
5. **Player/unit field offsets, player table `0x918374`, camera and resolution globals and immediates.**
   - Two independent tools (Reborn-DLL; ZoomChanger + Cheat Engine tables) agree on each.
6. **Renderer and network plugin contracts:**
   - `PIMGetType` returns `'rast'`/`'ntwk'`.
   - The `DX7TnLRasterizer` vtable has 82 slots.
   - FVF `0x152`, 2442-vertex ring buffer.
   - TCP plugin ports 33334/33335 and datagram types.
   - The WON LAN logic in the EXE uses 8585/33335/33336.

## 2. Source inventory

All repositories were cloned into `analysis/community-re/` (gitignored); commits are pinned in the JSON.

| Source | Licence | Targets | What it gives us | Status here |
|---|---|---|---|---|
| [00xJS/Empire-Earth](https://github.com/00xJS/Empire-Earth) (Sep 2026) | **none** (all rights reserved) | GOG EXE, LLE, both DX7 renderers; AoC | Most of the useful RE: x87 hot-function table with hashes, engine maths semantics, unit-limit/selection/draw-cap patches, render loop, world lock, simhash/tick hooks, renderer and window workarounds | 171 verified, 4 unverified, 1 inferred |
| [EE-modders/Reborn-DLL](https://github.com/EE-modders/Reborn-DLL) (2022) | none at repo level (GPL-3.0 header in one file) | GOG EXE (`2002.09.12.v2.00`) | Camera globals, resolution immediates, version string, `EEData`/map pointers, SP unit cap | 26 verified, 2 unverified, 1 inferred |
| [EE-modders/Empire-Earth-Library](https://github.com/EE-modders/Empire-Earth-Library) | GPL-3.0 | GOG EXE, LLE (AoC partial) | Game-type byte, `Game_Start` hook, `UShutdown`, campaign-unlock site | 6 verified |
| [EE-modders/Empire-Earth-Stats](https://github.com/EE-modders/Empire-Earth-Stats), [Empire-Earth-Discord](https://github.com/EE-modders/Empire-Earth-Discord) | GPL-3.0 | GOG EXE | Lobby flag, player-name/rasterizer/data-version pointers, per-frame `TMPoll` call | 4 verified, 2 unverified |
| [Hexorg/CheatEngineTables](https://github.com/Hexorg/CheatEngineTables) (`empire_earth_*.ct`) | none | GOG EXE ("v2.00" table matches GOG too) | Player table, resources, population, HP/build/spend code sites | 10 verified, 1 unverified |
| [j-frei/EmpireEarthZoomChanger](https://github.com/j-frei/EmpireEarthZoomChanger) | LGPL-3.0 | GOG EXE (by file size 6,321,664), AoC, no-CD, NeoEE | Camera constant block, audio fade constants | 8 verified |
| [McGregory99/empire-earth-web](https://github.com/McGregory99/empire-earth-web) (Oct 2026, paused) | **none** | GOG EXE, LLE, TCP plugin, TnL renderer | Clean-room `TCP Protocol.dll` reimplementation verified call by call; renderer plugin contract and vtable sizes; LAN/lobby port analysis; command-line switches | 15 verified |
| [povka/EmpireEarthArchipelago](https://github.com/povka/EmpireEarthArchipelago) | MIT (LICENSE file) | **AoC only** | AoC player/settings/tech-tree layout, SSA/DCL/DB/SST notes, "Steam = GOG sections" | n/a (format notes used) |
| [SoucupB/EEApi](https://github.com/SoucupB/EEApi) | MIT | **AoC only** | AoC unit offsets and hook RVAs | n/a |
| [coderbot16/scn](https://github.com/coderbot16/scn) wiki | none | EE1 + AoC | SCN scenario header and lump format | format only |
| [coderbot16/cem](https://github.com/coderbot16/cem), [cemconv](https://github.com/coderbot16/cemconv) | MIT | CEM v1/v2/v5 | Model format | format only |
| EE-modders [SSA-tool](https://github.com/EE-modders/SSA-tool) (none), [SST-tool](https://github.com/EE-modders/SST-tool) (GPL-3.0), [CEM-tool](https://github.com/EE-modders/CEM-tool) (GPL-3.0), [Studio-2](https://github.com/EE-modders/Empire-Earth-Studio-2) (GPL-3.0), [DB-Editor](https://github.com/EE-modders/Empire-Earth---DB-Editor) (GPL-3.0), [toolbox](https://github.com/EE-modders/Empire-Earth-toolbox) + wiki (none), [eerv](https://github.com/EE-modders/eerv) (none), [civ](https://github.com/EE-modders/civ) (none) | as listed | data files | SSA, PK01, SST, CEM, DB schemas (`EEEditorData/*.dats`), TAI, RMV, CIV | format only |
| [EE-modders/Empire-Earth-Setup](https://github.com/EE-modders/Empire-Earth-Setup) | GPL-3.0 | registry | Default `HKCU\Software\SSSI\Empire Earth` values (§3.8) | facts only |
| [lozza/Empire-Earth-Linux-Appimage](https://github.com/lozza/Empire-Earth-Linux-Appimage) | GPL-3.0 | GOG installer `2.0.0.2974_gog_v3 (78415)` | Needs native DirectMusic DLLs (Wine's crashed) | context |
| [save-ee/save-ee-lobby](https://github.com/save-ee/save-ee-lobby) | GPL (LICENSE.md; GitHub says NOASSERTION) | WON lobby | Lobby protocol fragments (out of scope; for netcode reference) | not mined |

**Checked and irrelevant:**
- plowsec/empire-earth-remake: own Rust/Godot engine, no licence.
- zatstv/compilador-empire-earth: a student compiler.
- DanoxUnlimited/empire-project: empty.
- rhntn/*: Turkish forks of EE-modders tools.
- alex-robert-fr/EE-SSA-tool: a C SSA extractor, GPL-3.0.

**Web search:**
- No public forum RE threads, Ghidra/IDA databases or GOG Cheat-Engine tables beyond the above turned up.
- dreXmod, `neoee.dll` and the NeoEE EXEs are closed source (see [neoee.md](neoee.md)).

## 3. What the community knows (by topic)

### 3.1 Threads, world lock and the simulation tick (→ sync, runtime)
- **World lock** is a `TSReadWriteLock` in the engine:
  - `WriteLock`, `WriteUnlock` and `ReadLock` at LLE RVAs `0x5ee30`, `0x5ee46` and `0x5eddd`; they use `this+0`, `+4` and `+0xc`. [verified]
  - The simulation thread takes it for writing once per tick (the loader does too).
  - The render thread reads it once per frame; that call returns to EXE `0x4feda6`, which identifies the render thread [verified]. The main thread also reads it.
  - The lock hands its auto-reset event to whichever thread waits. [unverified]
- **Render loop:**
  - Loop head at EXE `0x50fe42`; each pass ends with `Sleep(1)` at `0x50fee6` and loops while the thread object's byte at `+0x59` is 0. [verified]
  - `Sleep` uses the original IAT in `.rdata` (`0x82318c`). GOG's rebuilt `.idata` import descriptors still point their FirstThunks at the original IAT at `0x823000+`. [verified]
- **Per-tick hooks:**
  - The world update for one physics tick is `0x547aad`, called from `0x546d6b`.
  - It walks objects and calls the per-object update `0x547e0e` (thiscall, no stack args) from `0x547b5c`. [verified]
- **Tick rate:**
  - The EXE reads a CPU-share percentage (default 30) from global `0x919424` at `0x4c253b`, once, when the synchronous server is created. [verified location]
  - `NESynchronousServer::TuneForSlowCPU` (LLE export) sets ticks/s from the measured average tick time. 00xJS gives ⌊10·share / avg ms⌋, with an "if 0 then 1" clamp of the average at `+0x2c` (signature at `+0x27`) [verified].
  - Game speed does not depend on the tick rate: each tick covers proportionally more game time. So **step size depends on measured timing**. [unverified; consistent with the registry `Min/Max PTs per Second` in PLAN §2.3]
  - **Discrepancy:** 00xJS describes a 33 ms pacing timer, with tick rates of 25–30/s under Rosetta. PLAN measured ~41 ticks/s on real Windows. Treat any "30/s cap" as unverified.
- **Engine timers:** `TMTimer` is winmm `timeSetEvent`, and the simulation waits on a periodic event timer. [unverified]
  - This matches PLAN's measurement that each tick waits on an event, then takes the world write lock, then calls `NESynchronousServer::GetCurrentCommunicationsTick`.
- **Simulated object fields** used by 00xJS's per-tick hash [unverified, runtime]:
  - `+0x1c` id;
  - `+0x130` 4×4 transform (`+0x13c` world x, `+0x14c` world y are its translation entries);
  - `+0x230..+0x24c` velocities;
  - `+0x264` state.

### 3.2 Determinism-critical maths: x87 hot functions (→ sync, decompilation priorities)
- **Measured precision** (00xJS, consistent with PLAN): simulation thread at 53-bit/round-nearest (`0x027f`), render thread at 24-bit (`0x007f`).
  - The render thread's precision is set by the engine, so the D3D layer must not touch the control word.
- **00xJS's proof method:**
  1. Pin the tick time (patch the `TuneForSlowCPU` clamp, CPU share 100).
  2. Hash every object after its update.
  3. Compare 43 checkpoints over 2,100 ticks of a 1,080-unit battle: identical with and without the SSE2 translation.
- **[inferred] Caveat for our x87 model:** "x87 at PC=53 equals SSE2 double" holds only while no intermediate leaves the double exponent range.
  - x87 registers keep a 15-bit exponent even at PC=53.
  - The exact-x87 layer (PLAN §5.2) must still model that edge, but in practice 00xJS saw no divergence.
- **EXE hot functions (64, all [verified] by full-function hash):**
  - Physics-model virtuals:
    - `EEPhysicsModel` slots `0x34`/`0x38`/`0x3c`/`0x40`/`0x48`/`0x54`: `0x5488d1`, `0x5498c4` ("contacts": units pushing each other apart [inferred from 00xJS's label]), `0x549179`, `0x548a3c`, `0x549e61`, `0x549ff3`;
    - `EEPMLand` (also used by `EEPMHovercraft`) slots `0x04`/`0x10`/`0x30`/`0x4c`/`0x50`;
    - `EEPMNaval` `0x04`/`0x08`/`0x10`/`0x30`/`0x40`/`0x4c`/`0x54`;
    - `EEPMAirAnimal` `0x04`/`0x10` (slot `0x10` shared with `EEPMAirplane`); `EEPMSeaAnimal` `0x04`/`0x24`; `EEPMProjectile` `0x04`/`0x30`; `EEPMBuilding` `0x30`.
  - Plus 13 helpers, including `0x54a55b` "clamp" (50 callers), `0x46123f` "speed" (42 callers), `0x5f043b` (54) and `0x5efd6d` (53).
  - Plus 26 unit-update functions, one of them `EEUCGate` slot `0xb4`.
  - The largest are `0x624df8` (2,307 B), `0x62d91e` (2,225 B), `0x549179` (1,867 B), `0x548a3c` (1,853 B) and `0x54f84c` (1,834 B).
  - The JSON gives sizes, RTTI classes, caller counts and AoC counterparts.
- **Engine functions** (LLE, all [verified]; RVAs in JSON):
  - `GETransformation::Apply` ×4: point, vector, 2D point, matrix product.
  - `GEVector::CrossProduct`/`Normalize`/`GetMagnitudeSquared`; `GE3DPoint::DistanceTo`/`DistanceToSquared`/`+=`/`-=`; `GE2DPoint::DistanceToSquared`.
  - `GE3DPlane::IsPointOnOrAbove/BelowPlane`; `GE3DLine::Intersects` ×3 (plane, sphere, line), `ComputeDirection`, `GetPointAt`.
  - Constructors of `GETransformation`, `GE3DPlane`, `GE3DLine` and `GEBoundingBox`; `GEBoundingBox::ComputeDimensions`; `GETransformation::SetOrientationYPR`.
  - `GETerrainMesh::GetMeshZ`; `GEViewport::Normalize{Perspective,Orthographic}CameraPoint`; `GERasterizer::GetPixelSize`/`IsPerspectiveModelVisible`; `GEModel::PrepareTransforms`; `GEPhysicalModel::Intersects` ×3.
  - The trig functions `UMCos` (`0x7df28`), `UMArcCos` (`0x7dcc4`) and `UMArcTan2` (`0x7debb`).
- **Trig semantics** [verified constants read through the operands 00xJS names]:
  - **`UMCos`** wraps the angle into [−π, π] by repeated ±2π float steps; the value stays in an FPU register between steps.
    - Huge un-wrapped headings therefore cost one loop iteration per turn.
    - It then takes |x|, multiplies by 16384/π, rounds to float, converts with `fistp` (current rounding mode) and indexes a **16,385-entry float table at LLE RVA `0xd9130`** spanning [0, π].
    - sin is computed by callers as `UMCos(π/2 − x)`.
  - **`UMArcCos`**:
    - below 0.9995 it uses a **table at RVA `0xe9134`** indexed by `fistp(x·32768)`;
    - above that, a 3-term arctan series (1/3, 1/5, 1/7) with the DLL's own msvcrt `sqrt`;
    - negatives use π − acos(−x), with x clamped at −1.
  - **`UMArcTan2`** = ±`UMArcCos(b/√(a²+b²))`, with ±π/2 for b = 0 or NaN.
  - Both tables are **pre-initialised data in the DLL file**, not computed at start-up. A native port can share the exact tables; it needs neither libm nor a generator.
- **Measured rates** (00xJS, big battle under Rosetta; PLAN has Windows numbers):
  - `UMArcCos` + `UMArcTan2`: ~77k calls/s; `GetMeshZ`: ~46k/s; ray/sphere picking: ~11k/s.
  - Animation-smoothing blend and terrain vertex fill dominate the render thread.

### 3.3 Struct maps (→ function DB types, decompilation)
**Player and world (EXE)** [verified locations, meanings from runtime observation]:
- `g_Players[]` at `0x918374`: player-object pointers by player number. [0] is Gaia; [1] at `0x918378` is the local human in a standard skirmish.
  - The table is indexed at `0x5008fe`. AoC has it at `0x930DB4`.
- Player object fields:
  - `+0xAF8` food, `+0xAFC` wood, `+0xB00` stone, `+0xB04` gold, `+0xB08` iron (int32);
  - `+0xB10` population (added to at `0x5549e9`);
  - `+0xB41` "fast build" flag; `+0x49C` civ points;
  - `+0x9BC` → selection object (`+0x24` list, `[0]` first unit). [unverified chain]
- Resource spending is a small helper that subtracts the cost (`0x55be27`); the HUD reads values at `0x64fda4`.
- `g_dbWorldTable` at `0x917bb0`:
  - It is loaded by `0x4d6c57` (thiscall `Load(FSFileSpec&)`, called with the object in ECX from `0x536b24`).
  - Its record array is at `+8` (`0x917bb8`, which Reborn calls "EEData", non-zero once DBs load).
  - Records are 0x1c bytes: `+0xc` index, `+0x10` int min, `+0x14` int max. This matches the DB-Editor `dbworld.dats` schema [inferred].
- `g_CurrentMap` at `0x9183bc`: non-zero while a map is loaded (627 code references).
- `g_InWonLobby` at `0x944254`.

**Units / simulated objects** [offsets verified as used by the cited code; meanings runtime]:
- `+0x18` owner player;
- `+0x2c` type/class pointer [inferred: dereferenced by the construction code at `0x616f3c`; EEApi names it for AoC];
- `+0x3c` hit points: written on damage at `0x6176b8`, and during construction at `0x616f3c`, which raises HP toward the max;
- `+0x27c` training progress (`0x5efc31`);
- `+0x298` (checked by "god mode"); `+0x29c` max HP; `+0x340` flag byte;
- plus the §3.1 sim fields.

**Engine graphics classes (LLE)** [verified: each displacement occurs in the hashed function]:
- `GETransformation`: 4×4 floats; rows 0–2 are 3×4 with translation in `m[3]`, `m[7]`, `m[11]`; bottom row 0 0 0 1.
- `GE3DPlane`: {point[3], normal[3]}. `GE3DLine`: {origin[3], direction[3] at `+0xc`}.
- `GEBoundingBox`: min `+0`, max `+0xc`; extents x `+0x18`, z `+0x1c`, y `+0x20`.
- `GEViewport`:
  - side planes at `+0x50`/`+0x68`/`+0x80`/`+0x98` (normal at `+0xc` each);
  - view transform `+0xf0`;
  - orthographic factors `+0x23c`/`+0x240`/`+0x244`; perspective factors `+0x248`/`+0x24c`/`+0x250`;
  - near distance `+0x264`, pixel factor `+0x26c`, far distance `+0x270`.
- `GEModel`:
  - local transform `+0x8`, parent world `+0x48`, world `+0x88`, centre `+0xc8`;
  - animation-frame transforms `+0xf0` (64 B each), children `+0xfc`, keyframe vertex arrays `+0x104`, radii `+0x124`, scale `+0x128`;
  - blend factor t `+0x130`, frame A `+0x134`, frame B `+0x138`, child count `+0x13c`;
  - clip flags `+0x158` (near), `+0x159` (far), `+0x15a..+0x15d` (side planes).
- `GEMaterial`: first vertex `+0x34`, count `+0x38`.
- `GETerrainMesh`: height-row table pointer at `+0x17b578` (12-byte rows; AoC `+0x18f47c`, so the classes grew in AoC).
- `TSReadWriteLock`: `+0`, `+4`, `+0xc` (as above).
- `FSMemoryFile`: `+0x6c` current size.
- `U2DSparseArrayPointContainer`: row table `+8`/`+0xc`, point count `+0x18`; segment {min, max, ?, next}.
- `UIHotKey`: `+0x08` key entity; `+0x0c`/`+0x10`/`+0x14` further entities tested first; `+0x34` enabled byte [inferred].
- `DX7Screen` (renderers): `+0x41` caps bit, `+0x42` usable-Z flag, set by its capability probe.

**Render lists (EXE)** [verified]:
- `g_RenderLists` at `0x8d2cb8` (static initializer at `0x50009e`, constructor `0x506333`).
- `+0x78`: a fixed pool of **512 unit draw slots × 0x148 bytes**, each built by `0x506442`. `+0x3f880`: slots in use (`0x912538`).
- Nine code sites hold the pool address or size: `0x5007b1`, `0x5007ca`, `0x500996` (the 512 check), `0x503c68`, `0x503ecf`, `0x503fcb`, `0x504157`, `0x5043b4` and `0x50dc59`.

### 3.4 Unit limit and selection messages (→ netcode, gameplay parity)
- **Game Unit Limit:**
  - The range is **dbworld record 26** (min 50, max 1200).
  - The game-settings copy enforces it and resets out-of-range values to 50 (00xJS) [unverified].
  - The random-map setup list has its own loop bound of 1200 at `0x67b000` [verified]. The registry default is `Game Options\Game Unit Limit` = 1200.
  - Reborn's SP "MaxUnits" writes `[0x917bb8]+0x2EC`. That is record 26's max (26·0x1c + 0x14 = 0x2EC) [inferred: two sources, same field].
  - The limit is shared by all players. SCN files store `max_units` and the wiki says it is divided among players.
  - **In MP the limit is a lobby setting all peers must agree on**; never raise it on one side only.
- **Drag-select command:**
  - The selected unit ids are packed into a **255-byte field** of one game command.
  - The packer is `0x4bd15a` (cdecl: units vector, player, out), which stops at **63 units**.
  - The unpacker is `0x4bd2c4`. It runs where the command is built *and* where it executes, and uses vector clear `0x526aff` and push_back `0x40fecc` [all verified].
  - The vector is VC6 `std::vector` (begin at `+4`, end at `+8`).
  - 00xJS's bigger-selection "ticket" trick is process-local and so single-player only.
  - Original behaviour, which we must keep: at most 63 units per selection command.

### 3.5 Camera, zoom and resolution (→ renderer, Mac quality goals)
- **Static camera constants** (`.rdata`; editable in the file) [verified values]:
  - A block of five floats (0.1, 0.4, 10.0, π/180, π/4) precedes the fields.
  - Editor constant 0.1 (`0x82c6e4`); game constant 0.0 (`0x82c6e8`, must stay).
  - **Game max zoom −20.5 (`0x82c700`)**; **game rear clip / fog 35.0 (`0x82c704`)**.
  - Editor zoom −42.0 (`0x82c708`); editor rear clip 58.0 (`0x82c70c`).
  - Setting the editor constant to 0.0 gives the editor the game camera.
- **Positional audio fade:** start 27.0, end 35.0, slope 0.125 at `0x833afc`.
  - Sounds fade out at the rear-clip distance, so the ZoomChanger moves both together. Recommended for 1080p: zoom −40.5, clip 55.
- **Runtime camera globals:**
  - zoom state 0..1 (`0x918db0`); zoom style 0 normal / 1 "epic" / 2 isometric (`0x918dc4`);
  - FOV float (`0x91328c`; crashes at ≥0.9 per Reborn);
  - max pitch 0..−1 = 0..90° (`0x913284`); current pitch `0x9183c8` [verified referenced].
  - Current/last Z at `0x918d40`/`0x918d08` [unverified: no direct code reference; fields of an adjacent object].
  - Values beyond the defaults (fog ≥ 70, FOV ≥ 0.9) crash per Reborn [unverified].
- **Resolution:**
  - Settings globals `0x9193fc` (width), `0x9193f8` (height) and `0x9193f4` (bit depth) are filled from the registry `Game Window Width/Height` and `Game Bit Depth` [verified referenced].
  - Hard-coded immediates [verified]:
    - start-up mode probe 640×480 (`0x537523`/`0x537528`; the call sequence at `0x537518` passes the bit-depth global to the mode search `0x53590d`);
    - Bink height limit 480 (`0x53753c`);
    - **main menu 1024×768** at start-up (`0x538b3d`/`0x538b38`) and on return (`0x65face`/`0x65fac9`, guarded by `0x65fac2`);
    - **Scenario Editor 800×600** (`0x6601eb`/`0x6601e6`).
  - Menus are therefore fixed 4:3 surfaces; 00xJS scales them to the display. Our Metal presenter should do the same, letterboxing rather than stretching.

### 3.6 Renderer behaviour and workarounds (→ DirectDraw7/D3D7 HLE)
These causes were found under Wine. The *game* behaviour they reveal is what our HLE must satisfy.
1. **Imports and interfaces:**
   - Both DX7 renderers import only `DirectDrawCreateEx`, `DirectDrawEnumerateA`, `DirectDrawEnumerateExA` and 9 Bink functions [verified].
   - `IDirect3D7` is therefore reached only through `IDirectDraw7::QueryInterface` [inferred].
   - The device is created on the **back buffer obtained via `GetAttachedSurface`**, not on the primary [unverified, 00xJS trace].
2. **Exclusive fullscreen + deactivation:**
   - With `DDSCL_FULLSCREEN|EXCLUSIVE`, a lost surface on deactivation makes the game tear the device down and re-enumerate [unverified, 00xJS trace]. Under Wine that repeats forever.
   - → Our HLE should avoid spurious `DDERR_SURFACELOST`, or make restore cheap and idempotent.
3. **Mode list:**
   - Two searches (`0x537518` with 640×480, `0x535497` with the caller's size) assert `theMode < theModeCount` (`Empire Earth.cpp:2506`) if the mode is missing [sites and string verified].
   - The game's mode list is **bounded**: too many or duplicate modes crash it [unverified].
   - → Enumerate a short list that always contains 640×480, 800×600 and 1024×768 at 16 and 32 bpp.
4. **True page flipping on 2D screens** [unverified, 00xJS observation]:
   - After `Flip`, the back buffer must hold the page that was on screen.
   - The render loop saves and restores the area under the cursor with two 32×32 surfaces.
   - A second (cursor) thread draws on the primary between frames.
   - → Model real two-surface flip semantics and lock the cursor thread's primary blits against the flip.
5. **Text and blits** [unverified]:
   - Text is drawn with GDI on a per-frame Lock/`GetDC` of the back buffer.
   - Scenario Editor test games do a same-format `Blt` into a texture.
   - Small CPU blits (cursor save/restore) dominate on the flip chain. Keep surfaces in system memory where possible.
6. **Probes:**
   - Every `DX7Screen` (about a dozen per start) runs a 16×16 `TestWindowClass` window capability probe from its constructor (`+0x179`), in both renderers.
   - Its only outputs are the flags at `+0x41`/`+0x42` [verified call site + probe prologue].
7. **Draw path** (TnL renderer):
   - one FVF `0x152` (36-byte vertex: xyz, normal, diffuse, uv);
   - one **2442-vertex ring vertex buffer** per screen (DISCARD on wrap, NOOVERWRITE appends);
   - indexed triangle/line lists, fixed-function lighting, linear vertex fog, texture stage 0 only [verified immediates];
   - the draw calls are among `IDirect3DDevice7` slots 25, 26 and 29–32 [unverified].
   - Draws must not read past their vertex range: Metal/Vulkan bounds checks return zeros where Windows reads memory.
8. **512-unit draw cap:**
   - The screen scan fills 512 slots; units found after that are not drawn that frame [sites verified]. The scan runs back to front, so the *nearest* units vanish and crowds blink [unverified].
   - It is render-only [inferred], so lifting it should not affect sync.
9. **Animation Smoothing** (registry `Animation Smoothing`):
   - The keyframe blend `CopyAndSmoothStdVertices` / `...ColorVertices` is byte-identical in both renderers [verified].
   - It cost half the frame with 150+ units under Rosetta. Natively it is only a CPU cost to keep in mind.
   - `DrawTerrainMaterial` differs between the two renderers [verified].
10. **Splash window** `Loading Game Window`: its `WM_PAINT` handler never validates the region [string verified, behaviour unverified]. Our user32 must not starve other messages when a window keeps an invalid region.
11. **Render-loop `Sleep(1)`** every frame (`0x50fee6`) costs ~1 ms per frame. Keep it as is: it is harmless natively and affects pacing.
12. **Rasterizer plugin contract** (empire-earth-web):
    - Four C exports: `PIMGetName`, `PIMGetType` = `'rast'` (a `.data` global; verified), `PIMIsValid` (only AL tested by LLE at `0x574ed`/`0x50dc6`; verified) and `RPIMGetRasterizer`.
    - `GERasterizer` has 78 virtual slots and `DX7TnLRasterizer` 82 [verified by RTTI vtable count].
    - Also `GEScreen` 20, `GESurface` 32, `GEMovie` 8, `GEVertexArray` 16, `GEPolygonArray` 10.
    - The plugin reads ~12 engine classes' fields inline.
    - The engine loads plugins with `GEInitialize` (`0x4a2cc`) → `PIMModule::GetModule` (`0x575d2`) [verified exports].

### 3.7 Networking (→ netcode agent)
- **`TCP Protocol.dll`** (empire-earth-web's call-for-call clean reimplementation; facts only, no code):
  - Exports and type: 30 `NPIM*` + 3 `PIM*` exports; `PIMGetType` = `'ntwk'` [verified]; name `TCP/IP Protocol` [verified].
  - Ports: game traffic on **33334** (UDP datagrams and TCP listener); bulk transfer on **TCP 33335** [verified immediates].
  - Every UDP datagram is a header byte plus payload. The payload is XORed with an RC4 keystream keyed by 7 bytes of a 261-byte table, starting at offset (header − 1).
    - The table comes from the engine's `URandom` seeded with −1, so every peer derives the same one.
    - Header 0 means clear text; the sender picks the header at random (1..254).
  - Discovery: probe type `0x15` (service field 65 B, 67 B total); reply `0x16` (adds a 69-byte game-name field, 136 B total); 1 s listen.
  - Join: types `0x22`/`0x37` register a UDP client (unencrypted, ≥4 B).
  - Status codes 0/1/3/7/8/9/10/13.
  - Fixed 6,400-byte `GetAdaptersInfo` buffer. [inferred] That fits 10 `IP_ADAPTER_INFO` (640 B each), so our iphlpapi HLE must report at most 10 adapters.
  - Original defects worth keeping for fidelity:
    - a failed LAN scan leaks its socket and keeps 33334 bound;
    - `NPIMPublish` restarts Winsock;
    - unbounded client registration;
    - `NPIMReceiveFrom` can succeed without writing outputs.
- **Who calls the plugin** [unverified, empire-earth-web static analysis and `+snoop` A/B]:
  - LAN search, Host Game and Dissolve **never** call it. It is used only from the lobby→game hand-off: host `EELobbyGame` → `EEServer` → `NESynchronousServer` → `NPIMPublish`, and client `NESynchronousClient::Connect` → `NPIMConnect`/`Send`/`Receive`.
  - `NPIMEnumerateNetworkAdapters` is called from the Game Settings dialog: the inlined vtable call at `0x5c8101`, in the function at `0x5c8026` [verified].
- **LAN lobby** is the EXE's own WON `LanLogic` over `wsock32` [unverified, empire-earth-web static analysis]. Ports come from `WONLobby.cfg`: **LanBroadcastPort 8585, LobbyPort 33336, EEFileTransferPort 33335** [verified in our install's file]. 33335 is used by both the EXE and the plugin.
- **Record/playback:** `NERecordPlayback` in LLE is dead code in this EXE [unverified].

### 3.8 Version strings, command line, registry, hooks
- **Build and version strings:**
  - Build string `2002.09.12.v2.00` at `0x8a9030` [verified]; mods use it to detect the supported EXE. AoC's is `2002.8.17.v1.00`.
  - The menu's data-version string pointer is at `0x913264`; it is set at `0x5d16f2`.
  - Game identification: the first `.text` byte is `0xE8` in EE and `0x55` in AoC [verified].
- **Command line** (`0x522a36`, strings verified):
  - `-loadgame "<save>"` (straight into a match from `Data\Saved Games`), `-hostgame`, `-joinbyip`, `-update_saved_games`.
  - Case-insensitive prefix match; tokens split on `-`, `/` and `"`.
  - **`-loadgame` is the hook for automated regression runs.**
- **Engine/EXE hook points used by mods:**
  - `Game_Start` `0x5360ca` (fastcall);
  - `UShutdown` LLE `0x80634`;
  - per-frame `TMPoll::Poll` call at `0x64e29e`;
  - hotkey registration function `0x64c8b0..0x64dbad` (61 hotkeys);
  - `UIHotKey::FireEvents`; `gISKeyboard` (vtable slot 0 = `GetEntity` by DirectInput scan code [unverified]);
  - campaign-validity test `0x5c142d`.
- **Registry** `HKCU\Software\SSSI\Empire Earth` (community setup defaults; feeds our registry-to-file HLE):
  - Display: `Rasterizer Name` (`Direct3D` or `Direct3D Hardware TnL`), `Game Window Width/Height`, `Game Bit Depth` 32, `Texture Bit Depth` 32, `Wait for VSync` 0, `Animation Smoothing`, `UseCandidateWindow`.
    - Texture ≠ Game bit depth gives a white, unreadable main menu.
  - Sound and UI: `Music Enabled`, `Music Volume`, `Sound Volume`, `Take JPG Screenshots`, `AutoSave In Milliseconds` (1,200,000).
  - `Game Options\…`: `Map Type` (a string), `Map Size`, `Starting Resources`, `Starting/Ending Epoch`, `Game Unit Limit`, `Wonders For Victory`, `Game Variant`, `Difficulty Level`, `Game Speed`, `Reveal Map`, `Allow Custom Civs`, `Lock Teams`, `Lock Speed`, `Cheat Codes`.
  - Installer paths: `Installed From Volume/Directory`.

## 4. Known original bugs and community fixes
| Bug (original behaviour) | Cause | Community fix | Relevance |
|---|---|---|---|
| Crowds of >512 units blink | Fixed 512-slot draw pool (§3.3) | 00xJS moves the pool to 10,000 slots (all 9 sites) | Optional native improvement; render-only |
| Late-game memory explosion and crash in a big match | `U2DSparseArrayPointContainer::BuildPointList` (LLE `0x5fc87`) appends a point per y in each segment. Insert/Remove take no lock, so a racing update corrupts a segment list and the loop eats the 4 GB address space. Symptom: NULL write in a `std::deque` push (`0x451828`) | Guard that validates rows before the call | **Race in the original**: our runtime keeps the same threads. Watch for it; a guard is harmless |
| Huge-map saves crash | `FSMemoryFile::IncreaseFileSize` grows by a small fixed step and copies each time, fragmenting the address space | Grow by doubling (output unchanged) | Our 4 GB arena has the same limit; same fix is safe |
| Game aborts at start if 640×480/800×600 is not enumerated | Mode-search assert (`Empire Earth.cpp:2506`) | Inject classic modes | Our mode list must contain them |
| Deadlock on app switch (under Wine) | Deactivation handler spins on the cursor thread, which needs the UI lock; DirectInput is polled under that lock (`0x661889`) | Defer `WM_ACTIVATEAPP(FALSE)` | Our DirectInput HLE must not dispatch window messages inside `GetDeviceState` |
| Splash `WM_PAINT` storm (Wine) | Handler never validates | Subclass and validate | Same care in our user32 |
| Drag-select max 63 units | Message field limit | Process-local tickets (SP only) | Keep for MP compatibility |
| Gate crash (NeoEE/dreXmod fix) | Unknown, closed source | — | Unknown; watch in playtests |

## 5. Data-file formats (pointers)
- **SSA (`rass`)**:
  - 16-byte header: magic, u32 major 1, minor 0, data-start offset relative to the end of the header.
  - Then entries: `u32 len`, path (NUL-terminated), `u32 start`, `u32 end` (inclusive), `u32 size = end − start + 1`.
  - Our `Data/data.ssa` has **3,739 entries** (2,095 `.sst`, 758 `.wav`, 730 `.cem`, 58 `.tai`, 39 `.dat`) [verified by parsing].
  - Sources: SSA-tool wiki, Studio-2, Archipelago `ssa_extract.py`.
- **PK01**: `'PK01'`, u32 uncompressed size, 4 reserved bytes, then a PKWARE DCL "implode" stream (Mark Adler's `blast.c`). Used for most SSA entries and for scenario lumps.
- **SST**: 15-byte header (revision, number of resolutions, tiles, 3 pad, u32 width, u32 height, 1 byte kind), then an uncompressed type-2 TGA body. Kind 3 means a JFIF body; revision 1 is DDS (DOMW). Sources: SST-tool + wiki, Archipelago notes.
- **CEM**: magic `SSMF`, u16 major/minor (EE1 is v2; v1 rare; v5 is DOMW): materials, triangle selections, vertex-frame animation, LODs, tag points. Sources: coderbot16/cem (MIT), CEM-tool wiki.
- **SCN/EES**:
  - u32 size, then a header of Latin-1 strings: name, description, version, player list, date, per-player settings, game settings (speed, variant, map size, epochs, resources, max units, wonders, AI difficulty, lock/reveal/cheats flags), campaign/scenario names, lump-type list, scenario texts, uptime.
  - Then lumps (version, type, data, optionally PK01). Types: 2 seed, 3 camera, 4 terrain, 5 player data, 6 triggers, 7 misc, 10 gfx effects, 11 calamities.
  - Terrain and Objects lumps are undocumented. Source: coderbot16/scn wiki.
- **DB `.dat`** (39 tables in `data.ssa`, e.g. `dbobjects`, `dbtechtree`, `dbworld`): fixed-size records, often preceded by a u32 count.
  - Field-by-field schemas (with UNKNOWNs) are in DB-Editor `EEEditorData/{Vanilla,AOC}/*.dats`.
  - AoC `dbobjects.dat` uses 1,948-byte records (Archipelago); base-game size unchecked.
- **Others**:
  - `.rmv` random-map scripts and `.tai` unit-AI scripts are plain text.
  - Elevation `.dat` is raw int16 LE.
  - `.civ` has an online editor.
  - Music is DirectMusic (`.sgt`/`.sty`/`.dls`); video is Bink 1.
- **No conversion needed:** the game parses all of these itself (PLAN §2.6). These notes matter only for tooling (scenario corpus, asset inspection).

## 6. Corrections found while verifying
- 00xJS cites the second display-mode search as "Empire Earth.exe+0x535497". That is a **VA**, not module+offset; the first site (+0x137518) really is an RVA.
- 00xJS's hotkey span "0x64cc60–0x64dbad" starts mid-instruction. The enclosing function starts at **`0x64c8b0`** (no frame pointer, one caller) and its only `ret` is at `0x64dbad`.
- `PIMGetType` in both plugins returns a `.data` global (initialised to the type FourCC), not an immediate.
- EEStats' "VSync enabled" check reads the address of `0x944254`, not its value (always true). The address is really the lobby flag.
- The Cheat Engine table labelled "v2.00" matches the GOG EXE byte for byte at all five sites. GOG is the de-protected 2.00 binary, so those offsets also hold for 2.00 [inferred].

## 7. What feeds which project area
| Area | Use these |
|---|---|
| **Function DB (Phase 1)** | All 82 EXE function entries and 47 LLE ones as named seeds. Strong anchors: `World_UpdateAllObjects` `0x547aad`, `Object_SimUpdate` `0x547e0e`, render loop `0x50fe42`, `ParseCommandLine` `0x522a36`, `DbWorldTable_Load` `0x4d6c57`, selection pack/unpack, `RegisterInGameHotkeys` `0x64c8b0`, `Game_Start` `0x5360ca`, mode search `0x53590d`. RTTI classes for physics virtuals; `x87-hot` tags; thread attribution (sim vs render) for the 100 x87-tagged functions |
| **Decompilation priorities** | (1) LLE maths: `UM*` trig + `GETransformation`/`GE3D*` (small, named, hot, exact semantics already written up). (2) `EEPhysicsModel`/`EEPM*` virtuals and the 26 unit-update functions (simulation core, biggest x87 cost). (3) Draw-slot scan and `DX7Rasterizer` (clean plugin boundary, 82-slot vtable). (4) `TCP Protocol.dll` (37 KB; behaviour fully specified by empire-earth-web's transcripts). (5) Selection message codec and `dbworld` loader |
| **Renderer (DD7/D3D7 → Metal)** | §3.5 fixed menu/editor sizes, §3.6 items 1–12, FVF and ring buffer, CW untouched, letterboxed 4:3 menus |
| **Sync / exact x87** | §3.1 tick hooks and `TuneForSlowCPU` (pin tick time for differential runs); §3.2 trig tables are static data; 00xJS-style per-object hash at `0x547b5c`/`0x546d6b` as a **per-tick checksum oracle** for Mac-vs-Windows runs from the same `-loadgame` save (complements the `ESChecksum` LAN oracle in PLAN §2.3) |
| **Netcode** | §3.7 (ports, datagram cipher and types, who calls the plugin, WON LanLogic ports, adapter-buffer limit); §3.4 (63-unit selection commands, unit limit as a shared setting) |
| **Runtime / HLE** | LAA EXE (4 GB arena), original IAT in `.rdata` at `0x823000`, `Sleep(1)` pacing, world-lock semantics, registry key list (§3.8), user32 paint/activation hazards (§4) |
| **Testing** | `-loadgame` automation; ZoomChanger/Reborn constants as known-answer checks for the static-data loader |

## 8. Out of scope (recorded as n/a)
- **AoC (`EE-AOC.exe`)** is out of scope. Recorded only:
  - Archipelago: player table `0x930DB4`, settings block `0x931630…`, tech tree at player+`0x9CC`, banner/outcome functions;
  - EEApi unit offsets;
  - 00xJS's 52 AoC x87 functions and its armies/crowds/render-loop sites, kept as `aoc_counterpart_va` on the base-game entries;
  - EEStats AoC strings.
- **NeoEE and no-CD EXEs** (ZoomChanger offsets): n/a.
- **Steam:** Archipelago reports the Steam AoC EXE is section-identical to GOG's (only appended data differs). [inferred] The Steam base-game EXE is likely identical too; still to be confirmed against PLAN §2.4.

## 9. Reproducing
- **Clones:** `analysis/community-re/<owner>_<repo>` (commits are in the JSON `sources`).
- **Verifier:** `analysis/community-re-verify/{pehelp,claims,verify}.py`. Run it with the scratch venv (pefile 2024.8.26, capstone 5.0.7) as `python -I verify.py --write`. It also writes `verify-report.txt`, the full evidence with disassembly (gitignored).
- **How checks work:**
  - Function hashes reproduce 00xJS's scheme: FNV-1a over the function bytes.
  - For DLLs, each base-relocated dword is replaced by the four bytes it points at; IAT targets become zero; recipe call displacements are zeroed.
  - RTTI class names come from MSVC complete-object locators in front of the vtables.
