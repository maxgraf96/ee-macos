# Random-map generator (RMG): pipeline, context, capture and replay (M1)

Status: static RE complete, capture and replay tooling drafted (2026-10-14). Not yet run on legion. Owner: workstream G (m1-re).
Code:
- [`windows/frida/rmg_capture.js`](../../windows/frida/rmg_capture.js)
- [`tests/m1/replay/`](../../tests/m1/replay/README.md)

Labels:
- **[static]**: read from the binaries in Ghidra (func-db projects, Ghidra 12.1.4);
- **[measured]**: tool output;
- **[inferred]**: reasoning.

Addresses:
- EXE addresses are VAs (fixed base 0x400000).
- DLL addresses are module+RVA, with the preferred VA (base 0x10000000) in brackets.
- "ctx+X" is an offset in the generator context (§4).

Context: [phase2.md](../phase2.md) defines M1 and the oracle; this file is its spec.

---

## 1. Summary

| | |
|---|---|
| **Who generates** | `EERandomMap` in the EXE (no RTTI methods named; code at EXE `0x66ae3b`–`0x670800`) drives a plugin of type `'pamr'`. The only such plugin is `Default.dll` ("Default"). |
| **Plugin interface** | 11 cdecl exports `EERMPIM*`. Each takes a pointer to one **0x3d0-byte context** (`SetRandomMapValues` also takes the script name), returns 0 on success, and touches nothing but the context, its own globals and the heap [static]. |
| **Per generation** | The EXE loads `Default.dll`, creates a fresh `EERandomMap` (context included) per attempt, and runs `SetRandomMapValues` plus 9–10 stage exports, interleaved with EXE work. It checks playability and retries with a derived seed, up to 11 attempts, then unloads the plugin. |
| **RNG** | The engine's `URandom`/`URandomIntegers`: an LCG with MSVC `rand` constants, **state inside the context** (ctx+0x00, ctx+0x10), plus a local `URandom` in the height-map fractal seeded from ctx+0x3b0. No msvcrt `rand` and no time inside the plugin. |
| **Seed** | The host draws `srand(TMGetTime()); rand()` (0..32767) at "Start Game" and broadcasts it in `EEMStartLoading`. The scenario editor uses its own dialog seed (0..1,000,000). Retries use `ctx.URandom.Generate() × 2000001`. |
| **Files** | `<game>\Data\Random Map Scripts\<Map Type>.rmv` plus `#include`s, read by `Default.dll` itself with msvcrt `fopen`/`fgets` (not the engine's archive layer). |
| **Outputs** | Height map, land/water/flat/tree grids, areas, and resource and object lists, all in the context. The EXE turns them into terrain mesh heights, tile painting and world objects. |
| **M1 oracle** | Per-call, address-exact capture: the context's heap closure, Default `.data`, the stack window, and the allocator and file sequences. Replay is address-exact too (§6–§7). Test matrix: 60 cases (§8). |

## 2. Pipeline

### 2.1 Plugin discovery and loading [static]
1. **Startup.** `EERandomMap::Initialize` (EXE `0x66d6ac`, called from `0x5371bf` in `Game_Start` `0x5360ca`):
   - sets `PIMModule::sType = 'pamr'` (0x726d6170);
   - enumerates the plugins with the engine's `PIMModule::GetModule` (engine+0x575d2 [0x100575d2]), logging `EERandomMap::Initialize: %s random map generator detected.`;
   - scans `Data\Random Map Scripts\*.rmv`, reading each file's header with `fgets` (EXE side) to fill a table at `DAT_00943524`: 0x38-byte entries, script name at +0x04, generator name (`UseMapGenerator "Default"`) at +0x2c.
2. **`PIMModule::GetModule(bool first, PIMModule *&out)`** walks `*.dll` in `PIMModule::sDirectory` (`FSFileSpec::GetFirstFile`/`GetNextFile`). For each file it:
   - calls `LoadLibraryExA(path, 0, LOAD_WITH_ALTERED_SEARCH_PATH)`;
   - calls `UMSetFloatingPointPrecision(_PC_53)` (so CW is `0x027f` after every plugin load);
   - resolves `PIMGetName`/`PIMGetType`/`PIMIsValid`;
   - keeps the module if `PIMGetType() == sType`, else calls `FreeLibrary`.

   The `PIMModule` object (0x10 bytes) holds the HMODULE at +0x0c, and its vtable slot 0 returns the name `UString*`.
3. **Per generation**, `FUN_0066d402` (§2.2) repeats the scan with `sType = 'pamr'` until `_stricmp(PIMGetName, table[script].generator) == 0`. It then constructs `EERandomMap` objects with `FUN_0066ae3b`, which resolves the 11 exports by name with `GetProcAddress` into the object (§4.1). After the last attempt it calls `PIMModule::Unload`, i.e. `FreeLibrary`.

So `Default.dll` is **mapped fresh for every generation**: its DllMain runs, and its globals persist across that generation's attempts only. legion confirms it is not resident at the menu [measured, legion.md §5.2].

`Default.dll` facts [static]:
- 0x1d000 bytes; `.data` at RVA 0x19000, vsize 0x1a08;
- imports: 41 functions from the engine, 16 from msvcrt (`malloc`, `free`, `operator new`/`delete`, `fopen`, `fgets`, `fclose`, `printf`, `sscanf`, `sqrt`, `strlen`, CRT init), 7 from sxlrt233 (`std::basic_string` helpers);
- no EH frames, 76 x87 functions;
- RTTI: `EEClumps` and four subclasses (`EEFlatClumps`, `EELandClumps`, `EEIslandClumps`, `EETreeClumps`), `HMUFractal : U2DFractal`, `UMemoryPool<…>`.

`PIMGetName` returns a static `UString` "Default"; `PIMGetType` returns `'pamr'`; `PIMIsValid` returns 1 [static; measured in the spike].

### 2.2 One generation: `FUN_0066d402` [static]
Signature: cdecl, 7 arguments: `(settings*, players*, playerIndex, UString *mapType, uint seed, world*, float out[3])`.
- **Callers:**
  - `FUN_0052599b`, the world creation for a new game. Here `settings` is the game-setup object (+0x3a8 Map Type `UString`, +0x3dc map size, +0x3e0 starting epoch) and `seed = *(*(0x91977c)+0x54)`.
  - `FUN_00596426`, the scenario editor's *Create Random Map* (`EESEACreateRandomMap`).
- **Steps:**
  1. Look up the script index by name (`_mbsicmp`) and load the plugin (§2.1).
  2. **Attempt loop**, `tries` from 1:
     1. `operator new(0x410)`, then `EERandomMap(PIMModule*, settings, players)`.
     2. `CreateRandomMap(playerIndex, scriptIndex, seed, world, out)` (`FUN_0066c48f`, §2.3).
     3. `ok = FUN_0066b631(ctx, world)`: the playability check.
     4. `seed = round(ctx.URandom.Generate() × 2000001.0)`, with 2000001 clamped to 2000000. This uses the attempt's own `URandom` at ctx+0x10.
     5. Delete the `EERandomMap`.
     6. Repeat while `!ok && tries <= 10`.
  3. Log `Playable random map created after %d tries`; `PIMModule::Unload`.
- `out[3]` is the start camera position: the centre of the map, or of player `playerIndex-1`'s area (ctx+0xe0 element +0x20/+0x24), with z from the terrain.

### 2.3 One attempt: `CreateRandomMap` (`FUN_0066c48f`) [static]
Steps 2 and 4–10 are `EERMPIM*` calls; steps 1, 3, 11 and 12 are EXE work.

**1. `FUN_0066f4a6`, setup.**
- `this+0x404 = world`.
- `FUN_0066dc35(seed)` fills the context from the game (§4.2): seeds, map size class, starting epoch, player count, grid dimensions, the script directory (`gStartupVolume` + `gStartupDirectory` + `Data\Random Map Scripts\`), player and team lists, and symbol tables.
- Assigns the script name to a local `UString`.

**2. `EERMPIMSetRandomMapValues(ctx, &scriptName)`.** If it returns 0, the EXE continues with `FUN_0066e07e`, `FUN_0066c836` and `FUN_0066c616`:
- `FUN_0066e07e` sizes the 2-D grids to (N+1)², where N = ctx+0x380 is the smallest power of two in 64…1024 that is ≥ max(2·width, 2·height), and initialises them.
- `FUN_0066c836` (*CreateValidMap*) marks the valid tiles in the grid at ctx+0xf0 from the world's tile table.
- `FUN_0066c616` builds the player/team adjacency [inferred].

**3. `FUN_0066b434`, the stage sequence.** It stops at the first non-zero return. `FUN_006620a1` (progress bar) runs after each stage.

**4. `EERMPIMGeneratePlayerPositions(ctx)`.**

**5. `EERMPIMGenerateNeutralPositions(ctx)`.**

**6. `EERMPIMCreatePlayerLandWaterMap(ctx)`.**

**7. `EERMPIMCreateNeutralLandWaterMap(ctx)`.** Returns 0 and does nothing.

**8. EXE `FUN_0066b369`.** Collects candidate points (tiles that exist in the world and pass the ctx+0xf0 and ctx+0xc0 grid tests) into a list.

**9. `EERMPIMCreateFlatMap(ctx)`, `EERMPIMCreateHeightMap(ctx)`, `EERMPIMGenerateShallows(ctx)`.**

**10. `EERMPIMFilterHeightMap(ctx)`**, only if the byte at ctx+0x3cd (`EERandomMap+0x3d1`) is non-zero (EXE `0x66cc8c`).

**11. EXE terrain:**
- `FUN_0066cca2`;
- `FUN_00513923(ctx+0x3c8)`;
- `FUN_0066f59d`: height grid → `GETerrainMesh::SetZ`, water colours, `ComputeShorelines`, vertex normals;
- the playability check `FUN_0066b631`; if it fails, return 9, so the next two steps are skipped and the attempt is retried;
- `FUN_0066eb76` (*PaintTiles*, an EXE-side `U2DFractal`);
- `FUN_00512fb0`;
- `FUN_0066e482`.

**12. `EERMPIMGenerateResources(ctx)`, `EERMPIMCreateTrees(ctx)`.** Then the EXE does:
- `FUN_0066b921`: places the objects (start units, resources, trees, animals) from the context's lists, using the context's `URandomIntegers`;
- `FUN_0066f0d5`;
- `world+0x181730 = ctx+0x3c4`.

So a successful attempt makes **10 calls**, or **11** with `FilterHeightMap`. An attempt rejected by the in-pipeline playability check makes 8–9. The EXE runs in between, so **each call must be captured and replayed on its own**: the input to call k is not the output of call k−1.

## 3. Exports [static]

| Export | RVA | Worker | Signature | Role |
|---|---|---|---|---|
| `PIMGetName` | 0x6f97 | — | `UString* ()` | static `UString` "Default" (`.data` +0x1430) |
| `PIMGetType` | 0x6f9d | — | `ulong ()` | `'pamr'` |
| `PIMIsValid` | 0x6fa3 | — | `bool ()` | 1 |
| `EERMPIMSetRandomMapValues` | 0x70ee | 0xc268 | `int (ctx*, const UString &name)` | builds the parser's symbol tables from the context, parses `<dir>\<name>.rmv` and its includes (§5.3), normalises the elevation parameters, computes the area bounding box (ctx+0x220..0x22c), remaps ids in the ctx+0x1d0 list |
| `EERMPIMGeneratePlayerPositions` | 0x70cd | 0x2438 | `int (ctx*)` | appends one area record (0x3c bytes) per player to ctx+0xe0, positioned by an iterative solver; shapes from `GETransformation`; copies team data from ctx+0x110 |
| `EERMPIMGenerateNeutralPositions` | 0x70a7 | 0x2173 | `int (ctx*)` | appends neutral land areas (type −15, count ctx+0x398) and water areas (type −16, count ctx+0x394) |
| `EERMPIMCreatePlayerLandWaterMap` | 0x6fbf | 0x149a | `int (ctx*)` | grows land/water clumps per area into the grids, via a per-cell callback (0x6fd5) |
| `EERMPIMCreateNeutralLandWaterMap` | 0x6fbc | — | `int (ctx*)` | `return 0` |
| `EERMPIMCreateFlatMap` | 0x6fa6 | 0x115e | `int (ctx*)` | `EEFlatClumps` per area (flat tiles), temporary objects only |
| `EERMPIMCreateHeightMap` | 0x6fb1 | 0xa301 | `int (ctx*)` | local `HMUFractal(N = ctx+0x380)`, `ComputeFractal(ctx+0x27c, ctx+0x2e4, seed = ctx+0x3b0)`; the override (0xa121) writes each value into the height grid ctx+0xb0 |
| `EERMPIMGenerateShallows` | 0x70e3 | 0x2c87 | `int (ctx*)` | if byte ctx+0x3cf and ctx+0x30c > 0: shallow paths between areas |
| `EERMPIMFilterHeightMap` | 0x709c | 0xa38a | `int (ctx*)` | smooths the height grid (elevation range ctx+0x300..ctx+0x2f4) |
| `EERMPIMGenerateResources` | 0x70d8 | 0x2bc6 | `int (ctx*)` | 7 resource passes, then animals and fish |
| `EERMPIMCreateTrees` | 0x7091 | 0x1b86 | `int (ctx*)` | `EETreeClumps` per area (forests), using the list at ctx+0x1d4 |

All the exports are 3–15-byte cdecl wrappers that pass EAX through. `TMGetTime` and `ESDebugOut` (→ `OutputDebugStringA`) appear only in timing and debug prints, which never feed the outputs [static].

## 4. The context

### 4.1 `EERandomMap` (0x410 bytes, heap) [static]
| Offset | Content |
|---|---|
| +0x000 | vtable `0x835f2c` (`EERandomMap`; slot 0 = deleting destructor `0x66b019`) |
| +0x004 | **context**, 0x3d0 bytes: the pointer every export receives |
| +0x3d4 … +0x3fc | 11 export pointers: FlatMap, HeightMap, NeutralLandWaterMap, PlayerLandWaterMap, Trees, FilterHeightMap, NeutralPositions, PlayerPositions, Resources, Shallows, SetRandomMapValues |
| +0x400 | `PIMModule*` |
| +0x404 | world (`0x181748`-byte object; +0x1816f4/+0x1816f8 width/height in tiles, +0x1816fc grid side, +0x1816d4 tile table) |
| +0x408 | player list (`vector<T*>*`) |
| +0x40c | game-setup object |

The plugin never sees +0x3d0 onwards: the flags it reads go up to ctx+0x3cf [static].

### 4.2 Context layout (ctx = `EERandomMap`+4, 0x3d0 bytes)
Constructed by `FUN_0066b035`, destroyed by `FUN_0066b253` [static]. The types come from the constructor's per-member allocator helpers and the destructor's per-element destructors.

| ctx+ | Size | Type | Meaning | Written by |
|---|---|---|---|---|
| 0x000 | 0x10 | `URandomIntegers` {state, a=0x343fd, m=0x80000000, c=0x269ec3} | RNG A, state = map seed; the scripts' `Between()` (the parser's random ranges), `URandomIntegers::Generate` in stages, EXE object placement | EXE ctor, `FUN_0066dc35`; consumed by all |
| 0x010 | 0x10 | `URandom` (same LCG) | RNG B, state = map seed; `URandom::Generate` in stages; source of the retry seed | same |
| 0x020 | 0x48 | `FSFileSpec` {vtbl; `UString` volume +0x04, directory +0x14, file +0x24; int +0x34; int +0x38; byte +0x3c; +0x40, +0x44} | script directory | EXE (`FUN_0066dc35`, vtable +0x70 volume, +0x68 directory) |
| 0x068 | 0x38 | — | not touched by constructor or plugin as far as seen [inferred] | |
| 0x0a0 … 0x21c | 24 × 0x10 | `std::vector` (Dinkumware 2.33: allocator byte, `_First`, `_Last`, `_End`) | §4.3 | |
| 0x220 … 0x22c | 4 × 4 | float | bounding box of the area definitions | `SetRandomMapValues` |
| 0x230, 0x240 | 0x10 each | float[4] | ring/area parameters for the neutral land and water position solvers | `SetRandomMapValues` |
| 0x250 | 4 | int | `$MapSize`: size class 0–5, from the world width (thresholds 0x2b/0x3b/0x4c/0x6a/0xa1/0xdd) | EXE |
| 0x254 | 4 | int | `$StartEpoch`: game-setup +0x3e0 | EXE |
| 0x258 | 4 | int | `$PlayerCount`: entries in the player list − 1 | EXE |
| 0x25c … 0x2a8 | | float | elevation and land parameters. 0x26c = 1/(max−min) after normalisation; 0x270, 0x27c (fractal roughness), 0x294, 0x29c, 0x2a0 normalised to [0,1] | `SetRandomMapValues` |
| 0x2b8 … 0x2e4 | | float | tree and land percentages (0x2b8, 0x2c0, 0x2d8, 0x28c, 0x290, 0x2bc), fractal chaos 0x2e4, 0x2e0 | `SetRandomMapValues` |
| 0x2f4, 0x300 | 4 | int | max/min integer elevation (`kMaxIntElevation`/`kMinIntElevation`) | `SetRandomMapValues` |
| 0x30c | 4 | int | shallows parameter | `SetRandomMapValues` |
| 0x37c, 0x384 | 4 | int | 2 × world width, 2 × world height | EXE |
| 0x380 | 4 | int | N, the grid and fractal side (power of two, 64–1024; Tiny/Small 128, Medium/Large 256, Huge/Gigantic 512) | EXE `FUN_0066e07e` |
| 0x388, 0x370, 0x38c, 0x390 | 4 | int | clump and solver parameters | `SetRandomMapValues` |
| 0x394, 0x398 | 4 | int | number of neutral water / land areas | `SetRandomMapValues` |
| 0x3b0 | 4 | uint | **map seed** (the fractal seed) | EXE |
| 0x3b4 | 4 | int | flag read by the land/water callback | EXE |
| 0x3b8 | 4 | int | value from `FUN_00513ae5()` (tile count [inferred]) | EXE |
| 0x3c4 | 4 | | copied to world+0x181730 after the pipeline | |
| 0x3c8 | 4 | | passed to `FUN_00513923` after filtering | |
| 0x3cd | 1 | bool | run `FilterHeightMap` | `SetRandomMapValues` (`FilterHeightMap` keyword) |
| 0x3cf | 1 | bool | run shallows | `SetRandomMapValues` |

Many scalars between 0x25c and 0x3c8 are script parameters (`#define (kXxx, …)` constants that the parser binds to fields). The names above are the ones confirmed in code; the rest are left to the capture's per-call `summary` and diffs. **Where a field is not named here, the oracle does not need its meaning**: the capture is untyped (§6.2).

### 4.3 The 24 vectors [static]
"vec<vec>" means the elements are themselves `std::vector`s (their buffers are separate heap blocks). Grid element types come from the code that reads or writes them.

| ctx+ | Element | Content | Notes |
|---|---|---|---|
| 0x0a0 | vec<vec<u8>> | grid, (N+1)² | land/water mask read by the height fractal |
| 0x0b0 | vec<vec<float>> | **height map**, (N+1)² | written by `CreateHeightMap`, smoothed by `FilterHeightMap`, read by EXE `FUN_0066f59d` |
| 0x0c0 | vec<vec<u32>> | grid, (N+1)² | per-cell area/owner id |
| 0x0d0 | vec<vec<u8>> [inferred] | grid, (N+1)² | passed to the tree clumps; read by EXE placement |
| 0x0e0 | 0x3c: {vec<8-byte> +0x00; vec<8-byte> +0x10; floats +0x20/+0x24 (centre); int +0x2c (owner/type: player index, −15 neutral land, −16 water); bytes +0x38/+0x39} | **areas** | players, then neutrals |
| 0x0f0 | vec<vec<u8>> | valid-tile map, (N+1)² | EXE `CreateValidMap` |
| 0x100 | 0x18: {`UString` +0x00; u32 +0x10; u32 +0x14} | named entries; the parser defines symbols from them | EXE |
| 0x110 | 0x20: {vec +0x00; vec +0x10} | team groups | EXE |
| 0x120 | 0x10 POD | | read by EXE `FUN_0066f0d5` |
| 0x130 | 8 POD | | |
| 0x140, 0x150 | 0x14: {`UString` +0x00; int +0x10} | parser symbol tables (name = value) | EXE |
| 0x160 | 0x18 POD | placement rules (`SetRandomMapValues` reads +0x04/+0x08/+0x0c/+0x15/+0x16) | read by EXE placement |
| 0x170 | 0x14 POD | ring placements (`Place…InRing (type, min, max)`) [inferred] | read by EXE placement |
| 0x180 | 0x80 POD | terrain-painting rules | EXE *PaintTiles* |
| 0x190 | 0x0c POD | terrain painting | EXE *PaintTiles* |
| 0x1a0 | vec<vec<…>> | | |
| 0x1b0 | u32 | | read by EXE `FUN_0066f0d5` |
| 0x1c0 | 0x18: {vec +0x00; 8 bytes} | forest types [inferred] | |
| 0x1d0 | u32 | ids, remapped to indices into ctx+0x1c0 by `SetRandomMapValues` | read by `CreateTrees` |
| 0x1e0 | 0x14 POD | | |
| 0x1f0 | u32 | | |
| 0x200 | 8 POD | | |
| 0x210 | 0x0c POD | | |

`UString` (engine) is {vtable, `char *data`, length, capacity}. `data` is an `operator new(capacity)` block; the vtable points into the engine image [static].

### 4.4 Inputs and outputs
- **Before `SetRandomMapValues`:** the EXE has filled the RNGs, the directory, the size, epoch and player scalars, and the symbol and player lists (0x100, 0x110, 0x140, 0x150, …). Everything else is zero or empty.
- **After it:** the script parameters are in place. The EXE then sizes and initialises the grids (`FUN_0066e07e`) and marks valid tiles.
- **Every stage** reads the RNGs and advances them; both RNG states are outputs of every call.
- **What the stages write:**
  - *PlayerPositions* and *NeutralPositions*: the areas;
  - the land/water and flat stages: the grids;
  - *HeightMap* and *FilterHeightMap*: the height grid;
  - *Resources* and *Trees*: lists and grids.

  The capture measures the exact write set of each call (entry-vs-exit diff), which is the authoritative input/output split [to be measured].
- **Default.dll `.data`** holds the parser's state: symbol tables built in `SetRandomMapValues`, and a `UMemoryPool` for sparse point lists. It is input and output of every call.

## 5. Seeds, RNG, files, dependencies, world creation

### 5.1 Seeds [static]
| Path | Seed source |
|---|---|
| New game (skirmish; LAN host) | The host, on *Start Game* (`FUN_005c9b91`; the WON lobby path `FUN_0067e1d6`), calls `srand(TMGetTime())` and puts `rand()` (0–32767, msvcrt, per-thread) into an `EEMStartLoading` message (type 0x5d, seed at +0x14). That message's handler (vtable `0x8285b0` slot 10, `0x4bee57`) stores it in `*(0x91977c)+0x54` on every peer. `FUN_0052599b` passes it to `FUN_0066d402`. Single player is hosted locally, so it takes the same path. |
| Scenario editor | `FUN_00593a23`: the typed seed (`atoi`), or `URandomIntegers(TMGetTime()).Generate(0, 1000000)` |
| Saved game | `FUN_0052e7c8` restores the seed stored at game+0x4e4 |
| Default | `TMGetTime()` when the object at `0x91977c` is constructed |
| Retry *k* | `round(URandom@ctx+0x10.Generate() × 2000001)` after attempt *k−1* |

Inside an attempt, the seed is written to ctx+0x00 (`URandomIntegers` state), ctx+0x10 (`URandom` state) and ctx+0x3b0 (`FUN_0066dc35`).

### 5.2 RNG [static]
The engine's `URandom` is {state, a, m, c} with a = 214013, m = 2³¹, c = 2531011:
- `NextInSeries`: `state = (a·state + c) mod m` (32-bit multiply, unsigned modulo);
- `Generate()`: `(float)(state / (long double)m)`, an x87 division at the thread's PC, rounded to float on return;
- `Generate(lo, hi)`: `lo + (hi−lo)·Generate()`;
- `URandomIntegers::Generate(lo, hi)`: `FISTP(round-to-nearest)(Generate()·(hi+1−lo) + lo)`, clamped to `hi`;
- the `URandom(0)` constructor stores 1, but the EXE overwrites the state directly, so seed 0 is a real LCG state.

`U2DFractal::ComputeFractal(roughness, chaos, seed)` (diamond-square) uses a local `URandom` seeded with its third argument and calls the virtual `GenerateFractalValue`. `HMUFractal`'s override returns its value in ST(0) (`FST`, no pop) and also stores it in ctx+0xb0[row][col].

msvcrt `rand` is **not** used inside the plugin. Bit-exactness therefore depends on:
- the integer LCG;
- the x87 division and float rounding at PC53 (ADR-002 covers both);
- `sqrt`;
- the engine's `UMCos`/`UMArcTan2` tables;
- `GETransformation`;
- msvcrt `sscanf("%f")` in the parser (MSVC `_fltin` rounding: workstream D).

### 5.3 Scripts and file I/O [static, file listing measured]
- **Directory.** `Data\Random Map Scripts\` holds 7 scripts: Continental, Highlands, Large Islands, Mediterranean, Plains, Small Islands, Tournament Islands. Each `#include`s:
  - `Common\Map Climate`, which picks `kGrassMap` / `kWinterMap` / `kDesertMap` via `PERCENT(55)` / `PERCENT(45)`;
  - `Common Conditions`;
  - `Common Terrain Painting`;
  - `(Tourney) Common Initial Units`;
  - `Common Forests`;
  - six size files (`<Script>\<Script> Tiny…Gigantic`, about 51 KB each), each wrapped in `#if NOTDEFINED(...)`.
- **Script language.** A preprocessor: `#define (name, value)`, `#if`/`#elif`/`#else`/`#endif`, `DEFINED`/`NOTDEFINED`, `PERCENT(n)` and `Between(a, b)`, both drawing from the context RNG. Commands include `PlaceResourceInRing`, `ResourcePlacementLimit`, `HeightMapSmoothness`, `FilterHeightMap`, `UseMapGenerator`.
- **Predefined symbols:**
  - `$MapSize`, `$StartEpoch`, `$PlayerCount` from ctx+0x250/0x254/0x258;
  - `TinyMap` … `GiganticMap`;
  - `PaleoEpoch` … `NanoEpoch`;
  - `IslandMap`, `WaterlessMap`, `Is2Players` … `Is16Players`;
  - names from the ctx+0x100/0x140/0x150 lists.

  **So map size, player count, starting epoch and seed all change the parse.**
- **Reading.** `FUN_1000de77` copies the context's `FSFileSpec` and sets the file to `<name>.rmv`. It opens volume + directory + file with msvcrt `fopen(path, "r")` and reads with `fgets(buf, 0x200)`. `#include` recurses. The path is absolute: the game's startup volume and directory, e.g. `C:\EEPORT\GAME\EMPIRE EARTH\Data\Random Map Scripts\…` on legion. Text mode, so CRLF becomes LF in msvcrt.

The engine's FS/archive layer (`.ssa`) is **not** involved. The spike's synthetic run read 5 files and 781 lines for Continental [measured, spike].

### 5.4 What `Default.dll` calls [static]
- **Engine:**
  - `UString`: ctor, dtor, `=`, `+`, `[]`, `<`, `Find`, `Tokenize`, `NestedTokenize`, `sprintf`;
  - `FSFileSpec`/`FSBasicFile` copy and get/set;
  - `URandom::Generate`/`NextInSeries`, `URandomIntegers::Generate`;
  - `U2DFractal` ctor/dtor/`ComputeFractal`;
  - `GETransformation` ctor/`Apply`/`SetOrientationYPR`;
  - `GEVector::Normalize`/`GetMagnitude`;
  - `GE2DPoint` `=`/copy/`DistanceToSquared`/`InterpolateTo`;
  - `UMCos`, `UMArcTan2`;
  - `TMGetTime`, `ESDebugOut`.

  None of these keeps mutable global state except through `operator new` and the debug output. The maths tables are engine data.
- **sxlrt233:** `basic_string::assign`, `_Tidy`, `_Nullstr`, `npos`, `_Throw`, `_Raise_handler`.
- **msvcrt:** as listed in §2.1.

### 5.5 Back into the world [static]
| Context data | EXE consumer | World effect |
|---|---|---|
| height grid 0xb0, N, elevation range | `FUN_0066f59d` | `GETerrainMesh::SetZ` per vertex, water colour set, shorelines, normals |
| grids 0xa0/0xc0/0xd0/0xf0 | `FUN_0066cca2`, `FUN_0066b631`, `FUN_0066e482` | land/water tiles, playability |
| painting rules 0x180/0x190, seed 0x3b0 | `FUN_0066eb76` *PaintTiles* | tile textures (EXE-side fractal) |
| areas 0xe0, lists 0x100/0x160/0x170/0x1b0/0x120, RNG A | `FUN_0066b921`, `FUN_0066f0d5` | start units, resources, trees, animals |
| area of the local player | `CreateRandomMap` `out[3]` | start camera |
| ctx+0x3c4 | — | world+0x181730 |

M1 covers the plugin. The EXE side is covered later by the world checksum (netcode.md).

## 6. Capture format (`ee-rmg-capture/1`)

### 6.1 Files
Written under run.py's `--out` (`C:\EEPort\captures\rmg\<case>\`; copied to `analysis/m1-captures/<case>/`, never into git):

| Path | Content |
|---|---|
| `calls/NNNNNN.json` | one manifest per `EERMPIM*` call (§6.3), numbered `seq` across the session |
| `generations/NNN.json` | per `FUN_0066d402` call: original and forced seed, map type, attempts (`ctx`, `first_seq`, script index, seed), playability results, return value |
| `loads.json` | `Default.dll` loads and unloads (time, base, thread) |
| `blobs/xx/<sha256>.bin` | raw bytes, content-addressed, in chunks of `PARAMS.chunk` (4096) bytes. A region's bytes are the concatenation of its chunks. |
| `rmg_capture.json` | `snapshot()`: parameters, counters, the call index, warnings |
| `modules.json`, `meta.json`, `events.jsonl` | from `modules.js` and run.py |

### 6.2 What is captured, and why it is complete

**The heap index.** From `FUN_0066d402` entry (or `Default.dll` load) to the end of the generation, every block that msvcrt's allocators return, on any thread, goes into a live map (address → size). Frees remove it. The hooks are on msvcrt's exported `operator new`/`new[]`/`malloc`/`calloc`/`realloc` and `operator delete`/`delete[]`/`free`.

Everything the context can reach is allocated in that window [static]:
- the plugin's DllMain globals;
- the `EERandomMap`;
- every `UString` copy and vector the EXE puts into the context.

**At entry** to each export:
- **Scanned roots:** `[ctx, ctx+0x3d0)`, Default `.data`, and for `SetRandomMapValues` the 16-byte `UString` argument (on the EXE stack).
- **Closure.** Every aligned dword of a root or of a captured block that points into a live block pulls in **the whole block**, recursively.
- **Foreign values.** Values that point into other private RW memory (non-module) are counted and sampled as `foreign`. Most are expected to be stale addresses in uninitialised memory: vector capacity tails and recycled blocks. They are information, not errors. If one is really dereferenced, the Windows replay shows it: an access violation at module+RVA, or a mismatch. `heapwalk=true` then adds the busy msvcrt heap blocks they hit, one level deep, from a `HeapWalk`.
- **Captured but not scanned:**
  - the stack window `[ESP−stack_below, ESP+stack_above)` (0x8000/0x200), so that reads of uninitialised stack replay identically;
  - for diagnosis, the engine's `.data`, which is not restored.
- **CPU state:** x87 CW/SW/TW, via `fnstenv` and `fldenv` on the calling thread.

**During the call**, on the calling thread only, and only when the caller is `Default.dll`, `Low-Level Engine.dll`, `sxlrt233.dll` or `msvcp60.dll`:
- every allocator and free call, in order, with its arguments, its result, and the **initial contents** of each new block (`alloc_content`);
- `fopen` (path bytes, mode, result, plus the whole file as blobs), `fgets` (fp, n, result and the returned string including the NUL) and `fclose`;
- `printf` formats and `OutputDebugStringA` lines (information only);
- calls to any other msvcrt stream function, flagged as warnings.

msvcrt-internal allocations (stream buffers and the like) are excluded by the caller rule: the replay never runs them.

**At exit:** EAX, x87 CW/SW/TW, and the same closure from the same roots without the stack. These are the output regions.

**Why untyped.** The closure needs no layout. §4 is documentation, and is used only for the per-call `summary` (seed, size class, epoch, players, N, flags, element counts of the 24 vectors). Conservative scanning can only add blocks: garbage that happens to equal a live block's address pulls in a harmless extra region. Blocks freed between calls are never pulled in, because they are not live.

### 6.3 Call manifest (`calls/NNNNNN.json`)
```jsonc
{
  "schema": "ee-rmg-capture/1", "case": "A01",
  "seq": 7, "gen": 0, "attempt": 0, "export": "EERMPIMCreateFlatMap", "tid": 4321,
  "esp": "0x19f3a0", "args": ["0x0a4f1004"], "t": [12345, 12410],          // ms since attach
  "modules": [ { "name": "Default.dll", "base": "0x0f230000", "size": 118784, "sizeOfImage": "0x1d000",
                 "timestamp": "0x...", "preferred": "0x10000000", "sha256": "ab0e7910...",
                 "data": { "rva": "0x19000", "size": 6664 }, "path": "..." }, ... ],  // + sxlrt233, engine, msvcrt, msvcp60
  "entry": {
    "esp": "...", "args": [...], "fpu": { "cw": "0x27f", "sw": "0x0", "tw": "0xffff" },
    "summary": { "seed": 12345, "map_size_class": 1, "start_epoch": 0, "player_count_minus_1": 1,
                 "dims": [116, 128, 116, 0], "filter": 1, "shallows": 0, "vectors": { "0xe0": [2, 2], ... } },
    "script": "Continental",                               // SetRandomMapValues only
    "regions": [ { "kind": "ctx|module_data|arg|heap|heap_hw|stack|module_data_diag",
                   "addr": "0x...", "size": 976, "restore": true, "scan": true,
                   "module": "Default.dll", "via": "0x...", "from": "ctx",   // heap: where the pointer was
                   "chunks": ["<sha256>", ...] }, ... ],
    "bytes": 123456, "tracked_blocks": 54321,
    "foreign": { "count": 0, "sample": [["at", "value", "kind"], ...] }
  },
  "allocs": [ { "op": "new", "a": "0x18", "b": "0x0", "ret": "0x0b2c1230", "size": 24,
                "caller": "Default.dll+0x10e4", "chunks": ["..."] },        // initial contents
              { "op": "delete", "a": "0x0b2c1230", "caller": "Low-Level Engine.dll+0x81630" }, ... ],
  "files": [ { "op": "fopen", "path": "C:\\...\\Continental.rmv", "path_hex": "...", "mode": "r",
               "ret": "0x...", "size": 6924, "content": ["..."] },
             { "op": "fgets", "fp": "0x...", "n": 512, "ret": "0x...", "data_hex": "...00" },
             { "op": "fclose", "fp": "0x...", "ret": "0x0" } ],
  "printf": [], "debug": ["CMUCreateFlatMap: time ..."], "warnings": [],
  "exit": { "eax": "0x0", "fpu": {...}, "summary": {...}, "regions": [...], "bytes": 0, "foreign": {...} }
}
```
- **Ordering.** `seq` increases across the session. `gen` counts `FUN_0066d402` calls. `attempt` counts `SetRandomMapValues` calls within a generation. Calls of one attempt are contiguous.
- **Allocator ops:** `new`, `new[]`, `malloc` (`a` = size); `calloc` (`a` = count, `b` = size); `realloc` (`a` = pointer, `b` = size); `delete`, `delete[]`, `free` (`a` = pointer).
- **RNG state** needs no field of its own: it lives in the context, at ctx+0x00 and ctx+0x10, and in the HMUFractal local, which is created inside the call.

### 6.4 Size [inferred]
- Grids are (N+1)² cells, one heap block per row: N = 512 on Huge and Gigantic, so the height grid alone is 1 MB.
- Unchanged blocks and pages deduplicate across calls through the chunk hashes.
- Expect roughly 5–40 MB per case (Tiny to Gigantic), mostly first-call content, so about 1 GB for the matrix.

### 6.5 Limits
- **Address-exact by design.** A capture replays only at its own addresses (§7). That is the point: no pointer fixing, and stack garbage, padding and freed-memory reuse all reproduce.
- **Memory touched without a pointer path from the roots is invisible.** Examples would be engine globals mutated by a previous call, or EXE objects reached through a global. §5.4 says the plugin has none; the Windows replay proves it per call.
- **`Default.dll` loaded before attach** (e.g. attaching during a game) loses the DllMain-time blocks: a warning, then replay failures. Attach at the menu.

## 7. Replay driver

Code and build: [`tests/m1/replay/`](../../tests/m1/replay/README.md).

### 7.1 Model
- **Modules.** The three modules are mapped at the captured bases and their DllMains run. Imports are bound to the real system DLLs, except:
  - the msvcrt allocators and `fopen`/`fgets`/`fclose`, which go to replay shims;
  - `OutputDebugStringA`, which becomes a no-op.

  The shims pass through to a private heap until the call starts.
- **State.** Every restorable entry region is written at its address: ctx, Default `.data`, `UString` argument, heap blocks and stack window. The arguments are written at `[ESP+4]`. Default's `.data` is restored over the freshly initialised image, so earlier attempts' parser state is reproduced.
- **Call.** The export is called with ESP equal to the captured ESP and the x87 reset to the captured CW.
- **Shims during the call:**
  - each allocator call must equal the next recorded one; it returns the recorded address, already filled with the recorded initial contents;
  - each file call must equal the next recorded one; `fgets` writes the recorded line.

  The first divergence stops the replay with the event index and both values.
- **Checks:**
  - EAX and the CW;
  - all events consumed;
  - every exit region byte-identical, with first offset, count and label (e.g. `heap<-ctx+0xb4` is a row of the height grid).

### 7.2 Pack (`.rrp`)
`rmg_pack.py pack` turns each manifest into one binary pack:
- a 128-byte header: seq, gen, attempt, tid, ESP, CW in/out, SW/TW in, EAX, args, flags, export name;
- sections `MODS` (name, base, `SizeOfImage`, `TimeDateStamp`, `.data`), `INRG` (restorable regions with bytes), `ALOG`, `FLOG`, `OUTR` (expected regions with bytes) and `DBGS`.

Struct definitions are in `rmg_replay.h`. `rmg_pack.py` refuses calls with capture warnings or unreadable regions unless `--force`, and `--verify` re-hashes every blob. `rmg_pack.py list` prints a call table. The pack builder knows no game layout, only the region labels.

### 7.3 Windows (`rmg_replay.exe`, 32-bit, LAA)
- **One child process per pack.** The parent creates the child suspended. It checks with `VirtualQueryEx` that every needed range (64 KB-aligned) is free:
  - the module images;
  - ESP−1 MB … ESP;
  - every region;
  - every block allocated during the call.

  It then reserves those ranges before the child's loader runs, and resumes the child. If a range is taken (the child's own image, stack or ntdll), it retries with a new child, up to 10 times.
- **The child:**
  - maps the PEs itself, verifying `TimeDateStamp` and `SizeOfImage` against the capture;
  - applies HIGHLOW relocations;
  - binds imports, resolving `sxlrt233`/engine imports from its own mappings;
  - patches `msvcp60.dll`'s msvcrt allocator imports;
  - runs the DllMains;
  - switches stacks, with the TEB stack bounds following, and calls the export.

  A vectored exception handler reports crashes as module+RVA.
- **Output:** one `PASS`/`FAIL` line per pack, `MISMATCH` details, then `n/m packs passed`. Exit 0, 1 or 2.

### 7.4 Mac (kit)
Implemented: `ee_m1_replay` / `ee_m1_replay_static`, `tools/test.py --m1-replay`, and the matrix runner `tools/m1/run_matrix.py`. See [m1-replay.md](m1-replay.md).

The same `rmg_replay_core.c` behind an `rr_platform` implemented by the kit harness. The interface, and what it needs from workstreams A and B, are in the replay README:
- exact-address guest mapping in the 4 GB arena;
- module load at given bases with import overrides;
- a guest call at a given ESP and CW;
- strict traps.

Under test there: the translated DLLs, the x87 model, and the msvcrt HLE for the non-shimmed functions (`sscanf`, `sqrt`, `strlen`, `printf`).

## 8. M1 test matrix (60 cases)

### 8.1 Controls
- **Map Type and Map Size.** Registry `HKCU\Software\SSSI\Empire Earth\Game Options`:
  - `Map Type` (REG_SZ: the script name);
  - `Map Size` (DWORD: 0 Tiny 42², 1 Small 58², 2 Medium 75², 3 Large 105², 4 Huge 160², 5 Gigantic 220² tiles; `FUN_00524e1f`).

  The setup screen starts from these; the game rewrites them on exit, so set them only while the game is stopped. Check the setup screen with `grab` before *Start Game*.
- **Starting Epoch.** Registry `Starting Epoch` (DWORD), or the setup screen. The value reaches ctx+0x254 (`$StartEpoch`), and the capture's `summary.start_epoch` records it. The mapping from registry value to epoch is not verified [open].
- **Players.** Setup screen only: default 1 human + 1 computer. Add computer players with the UI (closed-loop `move`/`click`/`grab`, legion.md §6). `summary.player_count_minus_1` records the result.
- **Seed.** `--param seed=N` makes `rmg_capture.js` overwrite `FUN_0066d402`'s seed argument (and the global at `*(0x91977c)+0x54`) on entry. Retry seeds then follow deterministically. Real skirmish seeds are 0–32767; anything up to 2³²−1 is valid input.

### 8.2 Cases
Seeds in A are `1000 + 37·k`, k = case number, so they are distinct and within the skirmish range.

| Group | Cases | Script × size × players × epoch × seed |
|---|---|---|
| A: scripts × sizes | 42 (A01–A42) | {Continental, Highlands, Large Islands, Mediterranean, Plains, Small Islands, Tournament Islands} × {Tiny … Gigantic}, 2 players, registry epoch, seed 1000+37k |
| B: player counts | 8 (B01–B08) | Continental Medium × {3, 4, 6, 8}; Large Islands Large × {4, 8}; Tournament Islands Small × {3, 4}; seed 2001+k |
| C: seed edges | 5 (C01–C05) | Highlands Small, 2 players × seed {0, 1, 32767, 1000000, 2000000} |
| D: epochs | 3 (D01–D03) | Plains Medium, 2 players × {first epoch, the registry default, last epoch}, seed 4001 |
| E: editor path | 2 (E01–E02) | Scenario Editor → *Create Random Map*: Mediterranean Small with typed seed 5001; Small Islands Large with typed seed 5002 (`FUN_00596426`; no forced seed needed) |

Each case is one generation of 8–11 calls per attempt, so about 600+ replayed calls.
- **Retry coverage.** At least two cases must have attempts > 1 (`generations/000.json`). If none of A–E retries, search seeds on Small Islands Gigantic, 8 players.
- **FilterHeightMap coverage.** At least one call per script where `summary.filter = 1` (it is script-controlled).

**Pass criterion:**
- every pack of every case passes on Windows (completeness), and
- the same packs pass on the Mac against the translated DLLs (M1).

### 8.3 Compare masks [measured on legion, 2026-10-15]
`rmg_replay_core.c` (`k_masks`) does not compare a few bytes that `SetRandomMapValues` copies from **uninitialised stack slots** into two heap vectors. A difference in them is counted and printed ("N masked bytes differ"; with `-v` per region), never dropped silently. A mask names a region label, an element stride and the byte offsets in each element; nothing else is masked.

| Region | Element | Masked bytes | Evidence |
|---|---|---|---|
| `heap<-ctx+0x164`: the placement-rule vector at ctx+0x160 (§4.3), 28 elements in every case so far | 24 B | **16–19, 23** | On Windows +0x10 holds a stale stack address below the call's ESP (`0x263deff0`) and +0x17 holds `0x76` (the high byte of a system-DLL address); the replay leaves other stack values or 0. The values are identical in all Windows captures (S00b, A01–A06), so they come from the same deeper stack frames (msvcrt's `fgets`/`sscanf` while parsing the script), not from the generator. `SetRandomMapValues` reads only +0x04/+0x08/+0x0c/+0x15/+0x16 of these elements (§4.3). **[open]** confirm in Ghidra that the EXE's placement code never reads +0x10–0x13 or +0x17. |
| `heap<-ctx+0x204`: the vector at ctx+0x200 | 8 B | **6–7** | The high halves of the dwords at +4/+0xc/+0x14 of the region differ (`e0af0101` vs `10000101`), the low halves match: a 6-byte record padded to 8 (struct padding). |

Why restoring the stack cannot fix this: the garbage is produced *inside* the call by whatever runs deeper on the stack (Windows' msvcrt, the replay's shims, the Mac's native C runtime), so it differs by construction. Add a mask only after the same analysis: where the bytes come from, and why nothing reads them.

## 9. Runbook (legion)

Run by the main session or the legion operator. All commands are from the Mac in the ee-macos checkout; `L` is the helper from `windows/README.md`.

**Deploy (once):**
```sh
scp windows/frida/rmg_capture.js windows/frida/run.py legion:C:/EEPort/frida/
ssh legion "if not exist C:\EEPort\m1\replay mkdir C:\EEPort\m1\replay"
scp -r tests/m1/replay/* legion:C:/EEPort/m1/replay/
```

**Build `rmg_replay.exe` (once).** Either:
- on legion with VS 2022 Build Tools: `C:\EEPort\m1\replay\build_win32.bat` in an x86 Native Tools prompt; or
- on the Mac with MinGW (needs `brew install mingw-w64`, with the user's OK):
  ```sh
  i686-w64-mingw32-gcc -std=c99 -O2 tests/m1/replay/rmg_replay_win32.c tests/m1/replay/rmg_replay_core.c \
      -o rmg_replay.exe -static -Wl,--large-address-aware -Wl,--dynamicbase
  scp rmg_replay.exe legion:C:/EEPort/tools/
  ```

**Smoke case S00** (defaults: Continental Small, 2 players). Run this first and check `rmg_capture.json`: `warnings` empty, the `foreign` counts per call (expected small; note them), capture time per call (`capture_ms` in the manifests). The decisive check is that every pack replays.
```sh
L stop
L start                     # then ESC until the main menu (~30 s)
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts modules,rmg_capture --seconds 900 --out C:\EEPort\captures\rmg\S00 --param case=S00 --param seed=1000 --param stop_file=C:\EEPort\captures\rmg\S00\STOP --stop-file C:\EEPort\captures\rmg\S00\STOP' &
#   Single Player -> Play Random Map -> Start Game   (session.ps1 move/click/grab, legion.md §6)
wait                        # returns when the map is generated (STOP written) or after 900 s
L stop
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\m1\replay\rmg_pack.py list C:\EEPort\captures\rmg\S00'
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\m1\replay\rmg_pack.py pack C:\EEPort\captures\rmg\S00 -o C:\EEPort\captures\rmg\S00\packs --verify'
ssh legion 'C:\EEPort\tools\rmg_replay.exe --game "C:\EEPort\game\Empire Earth" --diff-dir C:\EEPort\captures\rmg\S00\diff C:\EEPort\captures\rmg\S00\packs\*.rrp'
scp -r legion:C:/EEPort/captures/rmg/S00 analysis/m1-captures/
```

**Per case** (example B03, Continental Medium, 6 players), with the game stopped:
```sh
ssh legion "powershell -NoProfile -Command \"Set-ItemProperty -Path 'HKCU:\Software\SSSI\Empire Earth\Game Options' -Name 'Map Type' -Value 'Continental'; Set-ItemProperty -Path 'HKCU:\Software\SSSI\Empire Earth\Game Options' -Name 'Map Size' -Value 2 -Type DWord\""
L start
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts modules,rmg_capture --seconds 900 --out C:\EEPort\captures\rmg\B03 --param case=B03 --param seed=2004 --param stop_file=C:\EEPort\captures\rmg\B03\STOP --stop-file C:\EEPort\captures\rmg\B03\STOP' &
#   Single Player -> Play Random Map -> add 4 computer players -> grab to check -> Start Game
wait; L stop
```
Then pack and replay as for S00.

For the editor cases (E), use `--param generations=1` without `seed`, and type the seed in the dialog.

## 10. Open questions
1. **Is `Default.dll` mapped before its DllMain runs at the Frida module observer?** Tracking starts at `FUN_0066d402` entry anyway, before the plugin load, so DllMain allocations are covered either way. If the texture worker keeps the DLL loaded across a generation, the tracking order changes; watch `loads.json` and the replay results.
2. **Capture cost on Huge/Gigantic** (full closure scan at entry and exit, about 2–4 MB per call): seconds per call are expected. If too slow, scan rows with a typed fast path (§4.3).
3. **Starting Epoch registry values** → `$StartEpoch` mapping, and whether the setup screen re-reads `Map Type`/`Map Size` from the registry on each visit or only at startup.
4. **Thread.** Which thread runs world creation (main or a loader thread); the capture records `tid`. The CW is expected to be `0x027f` (set after every plugin load).
5. **Compiler on legion.** MSVC is not installed. Choose VS Build Tools on legion, or MinGW/clang-cl on the Mac.
6. **Mac harness hooks** (A/B): exact-address mapping, module bases, import overrides and call-at-ESP, as in the replay README.
7. **EXE side.** Not in M1: `FUN_0066b921`/`FUN_0066f0d5` (object placement) and *PaintTiles* consume the context RNGs after the plugin. A later milestone can extend the same capture to `CreateRandomMap` as a whole.
