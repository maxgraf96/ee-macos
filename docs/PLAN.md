# Empire Earth → native macOS (Apple Silicon): port plan

Status: **v0.4** · 2026-10-15 · owner: Max
Background research: [`research/neoee.md`](research/neoee.md), [`research/strategies.md`](research/strategies.md).

---

## 0. TL;DR

- **Target:** base *Empire Earth* (2001, Stainless Steel Studios), the **GOG Empire Earth Gold** build, as a native arm64 macOS app.
  - No Wine, no Rosetta, no CrossOver, and no x86 code at runtime.
  - Single player plus **LAN multiplayer**, on a physical network or a virtual LAN (Hamachi / ZeroTier / Tailscale).
  - Mac ↔ Mac is required. Mac ↔ unmodified Windows GOG players is the goal.
- **Out of scope:** NeoEE, the AoC expansion, online lobbies.
- **Strategy: recompile first, decompile progressively.**
  1. Statically translate the game's 32-bit x86 modules to C, compiled for arm64.
  2. Run them on a small runtime that reimplements the **519 Win32 functions** and few COM interfaces the game actually uses, on SDL3 / Metal / CoreAudio / sockets.
  3. Keep an interpreter fallback for anything that can't be translated ahead of time.
  4. Replace translated functions with readable C++ where we want fixes or modernisation.
- **Base kit:** **recomp-kit** (MIT, Sept 2026), forked per [ADR-001](decisions/001-base-translation-kit.md). The Phase 0 spike translated EE's engine, Dinkumware and map-generator DLLs with no failures and ran their code natively.
- **Why this route:**
  - Apple's Rosetta goes games-only after macOS 27, so Wine-based setups are on borrowed time.
  - 4.2 MB of x86 code with no symbols is too much to decompile before anything runs.
  - Translation keeps the original instruction semantics, which is exactly what lockstep LAN play against Windows players needs.
- **Distribution:** for you and other Mac players who own the game.
  - The app contains only our code.
  - On first run it imports the player's own GOG installer or game folder and generates the translated code locally.

---

## 1. Decisions log

| # | Decision | Source |
|---|---|---|
| D1 | Base EE only; AoC out of scope. AoC could later be added cheaply: same engine, separate EXE. | Max, 2026-10-14 |
| D2 | No NeoEE / online lobby. Multiplayer = the game's built-in LAN/TCP-IP play, incl. over virtual LANs (Hamachi/ZeroTier/Tailscale). | Max |
| D3 | Users: Max + other Mac players; **ideally cross-play with unmodified Windows players**. | Max |
| D4 | Canonical binaries: **GOG Empire Earth Gold, base-game modules** (hash-pinned, §2.4). It's DRM-free and is also what all existing community RE (00xJS, Reborn-DLL, EELibrary) targets. | derived from D1–D3 |
| D5 | Analysis tools: **Ghidra** (free; scripted pipeline, function-DB export) + **REA** MCP for agent access. Optional second opinion: **Binary Ninja Free** (decompiler + built-in MCP; no API, so it stays outside the pipeline). | Max |
| D6 | CrossOver/commercial wrappers are a no-go for users. | Max |
| D7 | Not needed now: the retail CD + key. Keep it in reserve in case retail-version Windows friends want to join (version check, §5.6). | Max |
| D8 | All Windows friends use **GOG** copies, so the GOG build is the single compatibility target; no Steam/retail work. | Max, 2026-10-14 |
| D9 | Virtual LAN = **ZeroTier**, which the group already uses Windows↔Windows. It forwards broadcasts, so built-in discovery works. Tailscale/Join-by-IP is secondary. | Max |
| D10 | The app **bundles its own compiler** for the first-run translation build; no Xcode/CLT needed. See §5.8. | Max |

---

## 2. What we're porting

### 2.1 The game
- *Empire Earth* (Sierra, Nov 2001) is a 14-epoch RTS on Stainless Steel Studios' in-house engine.
- Final patch: 2.00 (build 3466). WON online play died in 2008, but LAN/TCP-IP play is self-contained and still works.
- Rebellion owns the IP and sells it on GOG (*Empire Earth Gold*) and, since 26 May 2026, on Steam (Windows only).
- The GOG EXE is a 2017 GOG rebuild of the de-protected 2.00 binary.

### 2.2 Module architecture (measured)
The engine is cleanly split into an EXE, a C++ engine DLL and **plugin DLLs ("PIMs")** with narrow interfaces:

```
Empire Earth.exe              game logic, UI, AI, (dead) WON lobby client — 4.2 MB x86
│   imports 1,293 C++ symbols (full mangled names) from ↓
├── Low-Level Engine.dll      "Titan" core: 319 exported classes, 4,432 exports
│     GE* graphics · UI* toolkit · FS* files/.ssa archives · NE* lockstep netcode
│     SE* sound (SEConductor/SEScore = DirectMusic) · IS* input (DirectInput)
│     TS* threads · ES* checksums (OOS detection) · UM* math wrappers
├── sxlrt233.dll              Dinkumware C++ library 2.33
├── Language.dll              strings/resources (+ UI text, UTF-16)
├── Plugins (PIMGetName / PIMGetType / PIMIsValid):
│   ├── DX7HRDisplay.dll      RPIMGetRasterizer → DX7Rasterizer; DirectDraw7/D3D7 without
│   │                         hardware T&L ("Rasterizer Name"="Direct3D", the default here)
│   ├── DX7HRTnLDisplay.dll   same with hardware T&L (optional)
│   ├── Miles Sound System Mixer.dll   SPIM* mixer over mss32 (34 AIL_* functions)
│   ├── TCP Protocol.dll      NPIM* transport: ws2_32, UDP broadcast discovery, TCP/UDP ports
│   │                         33334/33335, adapter enumeration (GetAdaptersInfo)
│   └── Default.dll           EERMPIM* random-map generator (pure compute)
└── third party: Mss32.dll (Miles), binkw32.dll (Bink 1), DirectMusic (COM via ole32)
```

### 2.3 Code characteristics
- **Size** (function DB, [function-db.md](reference/function-db.md)):
  - **EXE:** `.text` 4,232 KB, **25,602 functions** (37,459 entries including EH funclets and thunks). **1,879 RTTI classes**; 23,800/23,800 vtable slots resolved.
  - **Code split by origin:** game 65%, **WON lobby library 24%** (needed for the LAN lobby), Dinkumware 6%, DRM residue 4%, plus libpng / zlib 1.1.3 / libjpeg 6b / a little MFC.
  - **Other modules:** engine 586 KB (5,218 entries); Dinkumware 684 KB; plugins ~160 KB.
- **Compiler:** MSVC 6 (Rich header `Utc12` 8168/8799, linker 6.00.8447).
  - EXE base fixed at `0x400000` with no relocations; DLLs relocatable.
  - SafeDisc leftovers (`SIntf32.dll`, `CmdLineExt.dll` strings) to exclude. GOG's `.cms_t`/`.cms_d` sections are DRM residue (including a Petite-packed PE); Phase 1 must confirm they are unreachable.
  - **Every EXE section is flagged non-executable** (`0xc0000040`), and `SizeOfHeaders` overlaps `.text`, so code classification must not trust section flags (ADR-001, gap 2).
  - **C++ exceptions are used:** 2,274 EH frames and 98 throw sites in the EXE, 376 frames in sxlrt233; none in the engine or Default.dll.
- **Floating point:** pure **x87**; no SSE/MMX/3DNow!/CPUID.
  - Measured on real Windows (`legion`, [reference notes](reference/legion.md)) and consistent with [00xJS](https://github.com/00xJS/Empire-Earth):
    - **All game threads run at `0x027f`** (53-bit precision, round-nearest), including the main thread.
    - The **render thread runs at `0x007f`** (24-bit). The game sets this *itself* via the engine's `UMSetFloatingPointPrecision`; GOG's D3D9 wrapper preserves the FPU.
    - The rounding mode is never changed.
  - The game polls precision about 4,200 times a second at the menu.
  - Consequences:
    - our D3D layer must **not** touch the control word;
    - `_controlfp` must be exact per thread, and cheap.
- **Plugin loading:** the engine's plugin scan `LoadLibrary`s **every DLL in the game folder**. Plugins and the engine are therefore relocated, to different addresses per run (only the EXE, `sxlrt233.dll` and `binkw32.dll` get their preferred bases).
- **Hot imports:** 26 distinct imports are called at the main menu and 64 in-game.
  - **Hottest in-game:** `new`/`delete` 18.4k/s each, critical sections 9.5k/s, `memcpy` 9.6k/s, `sqrt` 8.8k/s, `_controlfp` 3.3k/s, `__RTDynamicCast` 2.2k/s, GDI text ~100–185/s.
  - **Barely used:** Miles is ~3/s.
  - These need HLE fast paths.
- **Threads in-game** (`legion`, random map vs 1 AI):
  - **Simulation:** EXE `TSThread` at `EXE+0x15332b`, **~41 ticks/s**, CW `0x027f` throughout. Each tick: wait on an event → world write lock → `NESynchronousServer::GetCurrentCommunicationsTick` → signal.
  - **Render:** ~83 fps, CW `0x007f`.
  - **Main:** message pump; takes the world read lock 454/s.
  - **Other:** texture/asset worker, input, music intensity, three network-server threads (single player hosts locally).
- **Simulation maths per second:** msvcrt `sqrt` ~2.9k, engine wrappers `UMArcTan2` ~1.5k, `UMArcCos` ~630, `UMCos` ~130, `fabs`.
  - msvcrt `sin/cos/_CI*` are never called, and neither is `_ftol`. Float→int is inline; a rare `0x0e7f` sample (truncate rounding) on the main thread hints at a temporary RC switch, to check in Ghidra.
  - So the transcendental surface for bit-exactness is essentially **the engine's `UM*` wrappers plus `sqrt`**.
- **No CW changes in-game:** none on any thread across 17k samples per thread; precision is set only at thread start and on plugin reload.
- **Out-of-sync detection** ([netcode.md](reference/netcode.md)):
  - From tick 40, every multiplayer tick computes an additive 32-bit **world checksum at `EXE+0x145ebf`**. It is not `ESChecksum`, which explains the zero calls seen in single player.
  - Clients send their value to the host (message `0x32`); the host's own value never goes on the wire.
  - The host decides: with 2 players both are flagged; with 3+ the majority wins.
  - Every machine writes an OOS log; the losers are removed.
  - "Debug OOS Handling" has no runtime effect.
  - **Oracle:** `windows/frida/netcode_hooks.js` records the per-tick world checksum on each machine.
- **Two kinds of screen:** menus and gameplay are DirectDraw 7 in exclusive fullscreen (`0x513`; `DDSCL_NORMAL` only with the windowed option). The multiplayer lobby is a **GDI-drawn UI** from the WON toolkit statically linked into the EXE: one owned `WS_POPUP` over the game window, shown after `FlipToGDISurface` while the render thread stops presenting; the cooperative level never changes ([lobby.md](reference/lobby.md), corrected 2026-10-15).
  - The current Wine build hangs on entering the lobby: the main thread spins in the lobby's message loop and nothing is displayed.
  - Our user32/gdi32 + DirectDraw HLE must present GDI windows and switch between the two modes cleanly (Phase 3 test case).
- **Free metadata:** mangled C++ signatures for the whole engine API and the display-plugin interface, plus RTTI in the EXE. No PDBs, though the plugins leak build paths (`C:\SSSI Development\…`).
- **Netcode tuning in the registry** (`HKCU\Software\SSSI\Empire Earth`): `Min/Max PTs per Second` (physics ticks 15–125/s), `CT Send Interval…`, `Debug OOS Handling`, `Network Adapter` (index), `Rasterizer Name`.

### 2.4 Canonical modules (GOG, SHA-256)

| File | SHA-256 |
|---|---|
| `Empire Earth.exe` | `957e35d75ba80309d094cbfb7367f7c6d61c5fd19fc5f7ca3f2e1cf636a065ab` |
| `Low-Level Engine.dll` | `e3f6a2f6d1ec5c3144f7cdd918cf54166de86547d4bbb6b3c9177d3eeb003214` |
| `sxlrt233.dll` | `ccf6f97d21bdf07abfb709a19fd7e75c585a972bada6341d61952b4700b4440b` |
| `Language.dll` | `2539f8b76ee3a6c6b6e03e5ce76d43e891c8b41533d058e249feea5ca8ca3456` |
| `DX7HRDisplay.dll` | `46a69ac011e9a934b1edd484a3f91f3482a5c85a3d47a44eddd1e4545746ff53` |
| `DX7HRTnLDisplay.dll` | `f0c5b16a7db0409e7915af7ff6ded3ac61eb29835f557aad5c5a68b37085b154` |
| `Default.dll` | `ab0e791039e558ac93f250594003995a4037883864cbdbbb6ded6f8faa4f9d3e` |
| `TCP Protocol.dll` | `b695886b01f63b7f699d204e89f38fcd03fb374a543866ed898381a194c170f0` |
| `Miles Sound System Mixer.dll` | `6e1a5145d31dfe325d2c7c5b62daaa49af233bb24fede22c56bc66ac11265e09` |
| `Mss32.dll` (reference only, HLE'd) | `d0db426ebac97dc410b5130c9c74d19bc04151236df85b012823d6cb3d7fb87c` |
| `binkw32.dll` (reference only, HLE'd) | `7ef2f1d2b6d833bcc3bea2359975e747b84e166bebfdf92e6a2a7389b8f2c0e6` |

These must also match the Windows friends' installs (§5.6). The Steam build's hashes are still unknown.

### 2.5 External API surface
**519 functions** across EXE, engine and all plugins:

| DLL | Functions | DLL | Functions |
|---|---|---|---|
| msvcrt | 167 | wsock32 | 28 |
| kernel32 | 88 | ws2_32 | 24 |
| user32 | 74 | msvcp60 | 17 |
| gdi32 | 36 | winmm | 16 |
| mss32 | 34 | binkw32 | 9 |
| advapi32 | 7 | imm32 | 7 |
| version (DRM-only, so stubbed) / ole32 / ddraw | 3 each | dinput / shell32 / iphlpapi | 1 each |

Plus COM: DirectDraw7 / Direct3D7, DirectInput, DirectMusic. For scale, Wine implements tens of thousands of functions.

### 2.6 Assets
- `data.ssa` (163 MB; magic `rass`, PKWARE DCL-compressed).
- Campaign `.ssa` files, Bink 1 movies, DirectMusic music (`.sgt`/`.sty`/`.dls`), WAVs, `.rmv` map scripts, scenarios.
- The game's own code parses all of these, so **no asset conversion is needed**. Community tools exist (EE Studio II, SSA-tool) if we ever need them.

### 2.7 Prior art on the Mac
- [00xJS/Empire-Earth](https://github.com/00xJS/Empire-Earth) (Sept 2026) runs the GOG build under Wine 11 + Rosetta with D7VK → DXVK → MoltenVK. It is single-player only, but useful to us for:
  - measured FPU modes;
  - hot x87 functions with *verified identical* SSE2 rewrites;
  - renderer and window fixes;
  - GOG addresses.
- **No decompilation or engine reimplementation of EE exists.**
- **Community RE, harvested and verified** ([community-re.md](research/community-re.md), [`db/community-symbols.json`](../db/community-symbols.json)): 251 claims for the GOG base game, **240 statically verified, 0 mismatched**. Highlights:
  - **Simulation hot functions:** all 64 of 00xJS's x87 hot functions match byte for byte and map to the `EEPhysicsModel`/`EEPM*` classes.
  - **Engine maths:** `UMCos`/`UMArcCos` are **table-based** (engine-data tables), so they are trivially exact if we translate them faithfully.
  - **Determinism checkpoints:** per-object update `0x547b5c` and per-tick world update `0x546d6b`. Combined with the `-loadgame` switch, Mac-vs-Windows runs can start from the same save.
  - **Struct layouts:** player table `0x918374`; struct maps for `GEModel`, `GETerrainMesh` and others.
  - **Renderer requirements:** two-page flipping, display modes 640×480/800×600/1024×768, FVF `0x152` with a 2442-vertex ring buffer, an 82-slot rasterizer vtable.
  - **Watch item:** a thread race in `BuildPointList` (late-game crash) will carry over into a faithful translation.
  - **New sources:** McGregory99/empire-earth-web reimplements `TCP Protocol.dll` call for call (no licence, so facts only).

---

## 3. Goals and success criteria

1. A native arm64 app (macOS 14+) that needs no Wine, Rosetta or x86 code in steady state.
2. Full single player: skirmish/random maps, all campaigns, scenarios, scenario editor, save/load, movies, music.
3. **LAN multiplayer, Mac ↔ Mac**: physical LAN, Hamachi/ZeroTier (broadcast discovery) and Tailscale (Join by IP).
4. **LAN multiplayer, Mac ↔ Windows GOG players** (unmodified): needs a bit-identical simulation.
5. Mac quality: Retina, any resolution/aspect ratio, native fullscreen + windowed, Cmd-Tab, trackpad, working music.
6. Easy for friends: drag-to-Applications app plus first-run import of their own GOG copy.

**Done when:**
- Campaign and skirmish test corpus completes.
- 2–4-player LAN games run Mac↔Mac and Mac↔Windows (GOG) to completion with **no out-of-sync**, over physical LAN and over a VPN.
- Saves load in both directions between Mac and Windows.
- A friend can install from scratch with only their GOG installer.

---

## 4. Strategy

| Option | Native? | First playable | Long term | Mac↔Windows LAN | Verdict |
|---|---|---|---|---|---|
| A. Wine `.app` (Wine 11 WoW64 + D7VK/wined3d) | No; Rosetta | weeks | Rosetta goes games-only after macOS 27; Wine coverage unclear; arm64 Wine + FEX needs a restricted Apple entitlement | yes | Stopgap only; 00xJS already covers single player |
| B. Emulator in app (BoxedWine; own Unicorn + Win32) | emulated | weeks | GPL, slow | yes | No |
| C. Full decompilation first | yes | only at ~100% | best | must hand-match x87 | Too slow as step one, with no PDB |
| **D. Static recompilation + HLE runtime** | **yes** | ~2–4 mo | becomes C over time | **yes, by construction** | **Chosen** |

Precedents for D:
- recomp-kit + populous-recomp (MIT);
- xi-on-anything (Final Fantasy XI, MIT);
- the console recompilers (N64Recomp, XenonRecomp).

Precedents for the later C phase:
- re3, OpenRCT2, OpenJKDF2;
- isledecomp → isle-portable;
- AI-agent decompilation at scale (Oct 2026). That project had a PDB, and its agents cheated until verification was mechanical, which is why we gate everything on mechanical checks.

**Legal hygiene:** translated code is generated locally from each player's own files. The repo holds only our code, annotations and tests (cf. the re3 DMCA).

---

## 5. Architecture

```
┌──────────────────────────── EmpireEarth.app (arm64) ─────────────────────────────┐
│ Launcher / importer: GOG offline installer (Inno; innoextract-style) or existing  │
│ install folder → verifies §2.4 hashes → generates + compiles translation (cached) │
│ → settings UI (resolution, window mode, network adapter, player name)             │
├───────────────────────────────────────────────────────────────────────────────────┤
│ Translated guest modules (generated C → clang -O2 → arm64, never committed)       │
│   EE.exe · Low-Level Engine · sxlrt233 · Language · Default · DX7HRDisplay ·      │
│   Miles mixer · TCP Protocol   (+ overrides: hand-written C++ per function)       │
├───────────────────────────────────────────────────────────────────────────────────┤
│ Runtime: 4 GB guest arena (u32 pointers, base+offset) · PE loader/binder ·        │
│ per-thread CPU context · exact x87 (double @PC=53, float @PC=24, SoftFloat edge   │
│ cases) · SEH + C++ EH · indirect-call tables + trap/report · interpreter fallback │
├───────────────────────────────────────────────────────────────────────────────────┤
│ HLE: kernel32 · msvcrt/msvcp60 · user32 (window + message queue) · gdi32 (DIBs,   │
│ text) · advapi32 registry → file · winmm · wsock32/ws2_32 → BSD sockets ·         │
│ iphlpapi (real interfaces incl. VPN utun) · DirectDraw7/D3D7 → Metal ·            │
│ DirectInput → SDL3 · DirectMusic → GothicKit/dmusic · mss32 → mixer ·             │
│ binkw32 → FFmpeg (LGPL dylibs)                                                    │
├───────────────────────────────────────────────────────────────────────────────────┤
│ Host: SDL3 · Metal · CoreAudio (via SDL3) · BSD sockets                           │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Base kit: adopt recomp-kit or build our own (Phase 0 gate)

**recomp-kit** (MIT, ~170k LOC, one maintainer, one shipped game) already has:
- Python translator from Ghidra listings;
- C runtime with x86 semantics and an explicit x87 precision model;
- interpreter, PE loader, scheduler;
- trap/report modes for unresolved indirect targets;
- kernel32/user32/gdi32 (TrueType text);
- DirectDraw incl. partial DD7, D3D2/3, a D3D9 shader generator on Metal;
- DirectInput, DirectSound, Bink via FFmpeg;
- "no game bytes tracked" checks.

**EE gaps:**
- a D3D7 device;
- Miles (its shim is a stub);
- DirectMusic;
- Winsock for LAN;
- proof of x87 exactness and C++ EH depth.

**Spike result, 2026-10-14 → [ADR-001](decisions/001-base-translation-kit.md), accepted: fork recomp-kit at `eb32cc4` as `kit/`.**
- **Translation:** Ghidra 12.1.4 headless (arm64), then the kit translator. Default.dll (740 functions), engine (5,142) and sxlrt233 (3,350) translated with 0 failures.
- **Execution:** the DllMains ran; `PIMGetName/Type/IsValid` gave correct values; `EERMPIMSetRandomMapValues` parsed real `.rmv` scripts. The map-building stages need the EXE-built context, so they move to M1.
- **EXE at scale:** 20.8k Ghidra functions; 36.8k translated; 5.4 M lines of C; 85 s freestanding compile on an M4 Max.
- **Bundled-compiler path (§5.8) proven:** `-ffreestanding -nostdinc` builds and `ld64.lld -bundle -undefined dynamic_lookup` links, with no SDK. A `dlopen`ed bundle gave byte-identical results.
- **Correction to the capability list above:** the kit has **no DirectDraw 7**. Its Miles shim is real (46 AIL exports; 17 of EE's 34).
- **Imports:** the kit covers 226 of EE's 519. Missing: msvcrt 165, msvcp60 17, ws2_32 24, wsock32 23, plus D3D7.
- **x87:** the kit uses `double` registers. That is deterministic Mac↔Mac, but **not bit-exact vs Windows**:
  - extended-precision operands differ (FLDPI×x mismatches in 26% of cases at PC=53);
  - 80-bit loads, the exponent range and host-libm transcendentals also differ;
  - threads start at CW 0x037f instead of 0x027f.
  
  Plain `+ − × ÷ √` on doubles at PC=53 is already exact.
- **Why a fork rather than tracking upstream:** upstream has one author, has been dormant since 2026-09-21, and chose double-based x87 on purpose. Our changes reach its core (x87, loader, MSVC EH).

**Principles we require either way:**
1. One semantics source for translator and interpreter.
2. Exact, deterministic x87.
3. Every indirect target resolvable, with a trap on a miss.
4. Function-level overrides.
5. A differential test harness.

### 5.2 x87 and determinism
- **Two different requirements:**
  - **Mac↔Mac** only needs our build to be deterministic with itself: same arm64 code, no uninitialised reads or thread-timing dependence. Static translation gives this almost for free.
  - **Mac↔Windows** needs bit-identical results with real x87. This is achievable because:
    - At **PC=53**, x87 `fadd/fsub/fmul/fdiv/fsqrt` equal IEEE `double`, except where the extended exponent range matters (detect and route to SoftFloat).
    - Loads and stores convert exactly as x87 does (`fstp m32` rounds to float).
- **Phase 1 results** ([ADR-002](decisions/002-exact-x87.md), proposed):
  - **No extended-precision sources in game code:**
    - no FLD constants, no 80-bit loads or spills, no transcendental instructions (except 2 in a GDI drawing method);
    - no `fldcw` (the only 8 are in the dead DRM residue);
    - `FILD m64` is almost all zero-extended u32.
  - **The engine's trig is tables plus `sqrt`:**
    - `UMCos` uses a 16,385-float table, `UMArcCos` a 32,760-float table plus a `sqrt`/atan series near 1;
    - `UMArcTan2` is built on `UMArcCos`;
    - there is no `UMSin`/`UMSqrt`. The tables are engine data, so they are exact by construction.
  - **Float→int:**
    - 1,330 inline `FISTP` sites using round-to-nearest-even;
    - a truncating helper (567 call sites) built on `FISTP m64`, which never touches the control word.
  - **Measured:** the kit's double model is **already bit-exact** on game-shaped work: 0 differences on 13,577 PC53 and 7,700 PC24 vectors, and on 22,076 `UM*` vectors vs Unicorn running the real engine. Rosetta's x87 matches our reference model on 184k vectors.
- **Design (ADR-002):** a hybrid. The double fast path is selected by a per-thread mode word (PC53, round-nearest, no extended value live); a free exponent guard routes rare edge cases to SoftFloat-80; PC24 uses midpoint-safe rounding; CW starts at `0x027f`; `_controlfp` is a cheap HLE; build with `-ffp-contract=off` and a CI check for FMA. **3.5–4.5 weeks.**
- **Validated on legion hardware (2026-10-14), so ADR-002 is accepted:**
  - Instructions: 187k vectors; the exact model equals Intel hardware bit for bit on every non-transcendental case at all tested control words.
  - Game maths: the real `UM*` functions inside the game equal the double model with 0 differences at the simulation's `0x027f`.
  - Remaining: implementation acceptance in Phase 2 (ADR-002 criteria). An AMD run is a nice-to-have; Intel/AMD transcendentals differ, but none feed EE's simulation.
- **Oracles:**
  - **The game's own sync check:** a per-tick world checksum at `EXE+0x145ebf`, sent to the host as message `0x32`; out-of-sync players are removed.
  - `windows/frida/netcode_hooks.js` for per-machine checksum streams.
  - LAN games against the unmodified GOG client on `legion`.
  - 00xJS's verified rewrites as a cross-check.

### 5.3 Graphics
- **Inventory result** ([graphics.md](reference/graphics.md), static): `DX7HRDisplay.dll` sends **only untransformed vertices** and relies on **D3D7 fixed-function transform and lighting**.
  - **Vertices:** one system-memory vertex buffer, FVF `0x152` (XYZ|NORMAL|DIFFUSE|TEX1), drawn with `DrawPrimitiveVB`/`DrawIndexedPrimitiveVB` only, for 3D and 2D alike.
  - **State:** `SetTransform` (25 sites), up to 8 point/directional lights, materials, colour-vertex sources, linear vertex fog.
  - **Texturing:** stage 0 only (SELECTARG1/SELECTARG2/MODULATE).
- **Shim checklist:**
  - 50 COM methods (DD7 12, Surface7 12, D3D7 4, Device7 19, VB7 3) + 3 exports;
  - 32 render states;
  - 12 TSS types;
  - 7 surface types (flip/mip chains, Z16/24/32, managed and render-target textures);
  - 12 pixel formats.
  
  Not needed: multitexturing, palettes, clippers, gamma, state blocks.
- **Design:** extend the kit's D3D2/3 command recorder + Metal renderer (`ddraw.cpp`, `d3d.cpp`, `host/d3d_render.cpp`) with version-7 vtables, FVF `0x152`, vertex T&L with 8 lights and fog in MSL, render-to-texture, 32-bpp and mip chains. Don't use the kit's D3D9 path.
  - Present only on Flip or a Blt to the primary; drop the kit's Populous-specific frame heuristics.
  - **Effort:** ~4.5–6 weeks, plus 1–1.5 weeks for GDI ANSI text and DirectInput fixes.
- **GDI:** used mostly to build glyph atlases once (`TextLock`) and to measure text on a screen DC (the ~92/s calls). Per-frame GDI on the back buffer is possible; `com_census.js` will measure it.
- **The T&L plugin** (GOG's default) is the same code with 3 overridden methods: about a day extra, so support both.
- **Rasterizer interface:** `GERasterizer` has 78 slots, `DX7Rasterizer` 82. The EXE reads a 0x340-byte per-screen state record inline about 49 times, so that layout is part of the interface.
- **DirectInput:** version 0x700, keyboard and mouse only. Two traps: never return exactly 256 keyboard records, and re-acquire only through `WM_ACTIVATE`.
- Render at Retina resolution; scale the 2D UI cleanly.
- **Decompilation track:** the display plugin is small (~500 functions) with a fully named interface. Rewrite it natively for true widescreen, UI scaling and better throughput.

### 5.4 Audio and video
- **Miles:** the 34 `AIL_*` functions over the kit's mixer (WAV/ADPCM; minimp3 is already vendored if `.asi` MP3 is used).
- **DirectMusic** (measured, [audio.md](reference/audio.md)): EE uses 9 DX6/7 interfaces and ~40 methods. **GothicKit/dmusic** (MIT) loads and plays **all 8 tracks** once patched:
  - a bank-select fix (upstream mis-assigns 89 of 103 instruments);
  - master groove level and segment repeats. EE's whole intensity mechanism is the groove level 1–4, switching at the next bar.
  
  The patch is in `third_party/patches/dmusic-ee-fixes.patch`. We pin a dmusic fork and upstream the fixes.
  - 4 of 8 tracks need Windows' system General MIDI DLS bank (`gm.dls`), which isn't shipped. Use macOS's own `gs_instruments.dls` at runtime (read from the system, never redistributed), falling back to the kit's soundfont.
  - **Gaps:** reverb (EE sets one per track), gain staging, and possibly variation choice and synth rate vs Windows.
  - Rendering runs 550–1300× realtime.
  - Music uses its own RNG, so it can't affect lockstep.
- **Miles:** 2D only, 8-bit mono 22 kHz samples, plus 3 MP3s for menu/UI music. 17 small AIL calls are missing in the kit.
- **Bink:** all three movies are Bink 1 640×480; FFmpeg decodes them cleanly; the kit covers all 9 calls.
- **Shared stub DirectSound** object for Miles, DirectMusic and Bink.
- **Bink 1:** FFmpeg dylibs (LGPL-2.1, dynamically linked; the kit already does this). Covers 3 movies: Sierra, SSSI, intro.

### 5.5 Input and windowing
- SDL3 window (fullscreen Space or windowed, Retina-aware); Win32 message emulation for the game's window proc; DirectInput keyboard/mouse from SDL3.
- Mouse capture/confinement, edge scrolling in windowed mode, Cmd-key mappings (00xJS's mappings are a reference), trackpad scrolling.

### 5.6 LAN multiplayer
- **Two layers**, validated on real captures from the 2026-10-14 LAN session ([netcode.md](reference/netcode.md) §10 has a claim-by-claim table):
  - **Lobby:** the EXE's statically linked WON `LanLogic`, over **wsock32**; ports come from GOG's `WONLobby.cfg`.
    - **UDP 8585, game list:** each lobby broadcasts query, advert, keep-alive (every 15 s) and closed messages to `255.255.255.255:8585`.
    - **TCP 33336, lobby room on the host:** join, roster, chat, slots, ready, a version check (version string `v2.00.2974` + data checksum), and *start game*.
    - UDP 33336 is bound but unused on the wire.
    - Still opaque: the 82-byte game-setup payload.
  - **Game:** on *start game* the client sends its connection request to the host on **UDP 33334** within ms, via `TCP Protocol.dll`; *Join By IP Address* takes the same path.
    - One engine message per datagram, RC4-obfuscated: 2,687 datagrams decoded with zero errors.
    - **TCP 33335** is opened for file transfer (no payload in a random-map game).
    - The host also sends one plugin `"EEService"` search per hosted game (a duplicate-name check).
  - **Ticks:** every 350 ms. World checksum requested every tick from 40; the client answers 0–42 ms after the tick.
- **Winsock HLE checklist** is netcode.md §7:
  - Both wsock32 (lobby) and ws2_32 (game) must really work.
  - `WSAEventSelect` must re-signal while data is still queued.
  - Calls on an invalid socket must fail cleanly; the dead Sierra DNS lookup must fail instantly.
  - **macOS sends the limited broadcast out of only one interface**, so we fan broadcasts out per interface (Wi-Fi *and* ZeroTier) for 8585 and 33334. A socket bound to one IP broadcasts only on that interface.
- **Data checksum on join:** the connect request carries a byte-sum of `Low-Level Engine.dll`, `sxlrt233.dll`, `TCP Protocol.dll` and the `Data\Db`, `Unit AI Scripts` and `Models` files. It is identical on both peers (`0x0e998b9e`). Keep those files byte-identical in the game folder even though they're translated.
- **Network adapters matter** (the lobby's broadcasts leave via the chosen adapter). **An original-game bug:**
  - **What happens:** `TCP Protocol.dll+0x1bee` walks the `GetAdaptersInfo` list to the chosen index, then `free()`s the advanced pointer.
  - **Effect:** index ≥ 2 corrupts the heap, which explains legion's 3/3 crashes with VMware adapters listed first. With the default adapter, legion's lobby broadcasts went out via a VMware adapter, which is why the VM never saw them.
  - **Our iphlpapi HLE:**
    - report ≤ 10 adapters, ordered primary `en*` first, then ZeroTier (`feth*`/`zt*`) and `utun*` with IPv4, then others;
    - skip `lo0`, down interfaces and those without IPv4;
    - return `gethostbyname(own name)` in the same order;
    - and either our `free` tolerates interior pointers, or we override `+0x1bee`.
  - **Windows friends:** leave the adapter on default, or pick the first entry.
- **First real LAN session (2026-10-14, legion ↔ VMware VM on legion, by session neoee-macos-b4):** the host's per-tick world-checksum stream (Frida) and the client's wire responses **agree on 817/817 ticks**, and `tools/lan/eelan.py` decodes both captures identically. Captures are in `analysis/lan-captures/session2/` (git-ignored).
- **Virtual LANs:**
  - **Hamachi / ZeroTier** forward broadcasts, so games appear automatically.
  - **Tailscale** does not forward broadcasts, so use the game's built-in **Join By IP Address**.
  - Optional port-side extra: a "known peers" list that unicasts discovery to friends' tailnet IPs. Mac-only convenience; it doesn't change the wire protocol.
- **Version match:** everyone must run the same game build. Canonical is GOG (§2.4).
  - Windows friends on GOG: should just work. Steam: hashes unknown, to check.
  - Retail 2.00 + CD key: to check, and that's where your key could matter.
  - **CD key:** GOG's installer writes `HKLM\SOFTWARE\WOW6432Node\Sierra\CDKeys\EmpireEarth` (REG_BINARY) from its install script. Our importer reads the value from the player's own GOG files into the registry emulation, and never ships it. Since the group already plays GOG↔GOG over ZeroTier, a shared GOG key evidently doesn't block LAN games.
- **Display plugin is per-client:** GOG's installer defaults to `Direct3D Hardware TnL`, 16-bit, 1024×768, so Windows friends likely run the T&L plugin. Rendering runs on its own thread and shouldn't affect sync; verify in a LAN test.
- **Firewall:** Windows' "Allow" prompt creates **Private-only** rules, and a ZeroTier network usually starts as **Public**. Friends' ZeroTier networks must be set to Private, or need a Public rule scoped to the ZeroTier subnet. Every peer needs **inbound UDP 8585, TCP 33336, UDP 33334 and TCP 33335**. Our friend-facing guide will say this; [reference/legion.md](reference/legion.md) §7 has commands.
- **Firewall:** the app requests incoming connections (macOS firewall prompt). Document the VPN setup for friends.

### 5.8 Bundled first-run compiler (D10)
- **Ship:** an open-source LLVM toolchain (Apache-2.0 with LLVM exception), arm64 only, stripped: `clang` (C only) + `ld64.lld`, ideally one multi-call binary, ~100–150 MB.
- **Constraint:** Apple's macOS SDK (headers, `libSystem` `.tbd` stubs) **may not be redistributed**. So:
  - Generated code is **freestanding C** (`-ffreestanding -nostdlib`). It includes only our runtime headers and compiler builtins, and calls only runtime entry points.
  - It links into a `MH_BUNDLE`/dylib with `-undefined dynamic_lookup` (or `-bundle_loader` against the app executable), so no SDK stubs are needed. Any libm-style helpers come from our runtime or compiler-rt (shipped).
- **Code signing:** all arm64 code must be signed. lld ad-hoc signs arm64 output by default.
  - The app needs `com.apple.security.cs.disable-library-validation` to load a locally built, ad-hoc-signed bundle under the hardened runtime. That entitlement is compatible with notarization if we ever get a Developer ID.
- **Build cost:** a one-time compile of ~1–2 M lines of generated C (parallel; minutes on M-series), cached in `~/Library/Application Support/EmpireEarth/`. It is keyed by module hashes and translator version.
- **Verify early (Phase 2):** generated C compiles with `-ffreestanding` and links with `ld64.lld` alone on a machine without the CLT.
- **No Ghidra at first run (Phase 6, P6-A, done):** the translation inputs are made from the function DB plus the player's binaries by our own decoder (`tools/inputs/`). Inputs and generated C are byte-identical to the Ghidra pipeline's for all nine modules ([build.md](reference/build.md) §2.1).
- **Measured inventory** ([packaging.md](reference/packaging.md)):
  - official LLVM 23.1.3 `clang` + `ld64.lld`, stripped: 303 MB (65 MB compressed), about twice the estimate above; an AArch64-only multicall build should get back to that range;
  - a trimmed python-build-standalone runtime with capstone and pefile: 39 MB;
  - the first run takes about 2.7 min and 0.93 GB peak disk on an M4 Max, leaving 169 MB of bundles.

### 5.7 Decompilation track
- Any guest function can be **overridden** by C++ registered by address, using typed guest-memory views (`gptr<T>`, structs from Ghidra).
- Every override is checked against the translated original in lockstep validation mode: same snapshot, diff of registers, memory writes and returns, plus fuzzed inputs.
- **Order of work:**
  1. bug fixes;
  2. display plugin → modern renderer;
  3. UI/HUD (dreXmod-style camera zoom/FOV is client-side, so it is safe for sync);
  4. simulation, last and strictest.
- Optional endgame: fully native 64-bit C++, which also opens AoC/Linux/Windows-arm builds.

---

## 6. Verification

1. **Instructions:** differential fuzzing of translator and interpreter against Unicorn on the Mac (dev-only, GPL, not shipped), and against **real x86** on `legion` (tiny `runner.exe`) for x87 corner cases and transcendentals.
2. **Functions:** translated vs interpreted vs overridden execution on recorded snapshots.
3. **Module, M1:** `Default.dll` random maps. The same script and seed on Windows (`legion`, a native driver loads the DLL) and on the Mac must produce **byte-identical** maps.
4. **Game:**
   - Reference capture on `legion` with Frida: API logs, file I/O, FPU control words per thread, per-tick `ESChecksum`.
   - Scripted AI-only skirmishes with fixed seeds, campaign missions, save/load. The Mac must reproduce the checksum streams.
   - Byte-compared saves.
   - Perceptual screenshot diffs.
5. **LAN** (oracle: per-tick world checksum via `windows/frida/netcode_hooks.js` and `tools/lan/eelan.py` decoding of captures):
   - Mac↔Mac (two builds on two Macs). Two instances on **one** machine can't share UDP 33334, so use Join by IP with separate network namespaces, or two machines.
   - Mac↔`legion` (GOG on Windows).
   - Mac↔the existing Wine build (a second "Windows" client on the same Mac).
   - Soak tests: 8 AIs at max speed, with the game's OOS removal as the oracle.
   - Then repeat over Tailscale and ZeroTier/Hamachi.
6. **Repo hygiene:** CI checks that no game bytes or generated code are tracked.
7. **CI:** macOS runner (build, unit, fuzz, headless scenarios); `legion` reference capture scripted over SSH.

---

## 7. Roadmap

Rough focused-engineering weeks for one developer with AI agents; Phases 0–1 recalibrate them.

### Phase 0: Foundations and kit spike (≈2 wk)
- `git init`, `.gitignore` (installers, `original/`, `analysis/`, `build/`, generated C), private remote; `docs/decisions/` (ADRs).
- **`legion` reference:**
  - Copy the GOG *Empire Earth* folder from this Mac (it's DRM-free), import the `SSSI` registry keys and verify hashes.
  - Run with DDrawCompat or GOG's wrapper.
  - Install Frida, Sysinternals, x32dbg and Wireshark.
  - Capture: modules, FPU control word per thread, and a LAN session `legion` ↔ the Mac's Wine build (the discovery and game packets).
- **Spike:** recomp-kit on `Default.dll` + engine + Dinkumware → ADR-001 (adopt/fork vs own).
- `NeoEE_Setup.exe` is no longer needed. Delete it, or move it to `original/` (git-ignored).

### Phase 1: Reverse-engineering foundation: static work **done 2026-10-14**; legion confirmation runs pending
| Output | Result |
|---|---|
| Function DB ([function-db.md](reference/function-db.md), `db/functions/`, `tools/ghidra/`) | ≥ 99.9% of `.text` in every module, all vtable slots resolved, library/DRM tags, DRM unreachable, reproducible; the kit translates with it cleanly |
| Graphics/input inventory ([graphics.md](reference/graphics.md)) | D3D7 shim checklist (50 methods, T&L + 8 lights + fog), rasterizer interface, DirectInput traps |
| Sync maths ([ADR-002](decisions/002-exact-x87.md), `tests/x87/`) | no extended precision in game code; hybrid x87 design; Mac-side vectors pass |
| Netcode ([netcode.md](reference/netcode.md), `tools/lan/`) | lobby vs game layers, ports, RC4 framing, message catalogue, OOS checksum, Winsock checklist, capture decoder |
| Audio ([audio.md](reference/audio.md), `tools/audio/`, dmusic patch) | all 8 tracks play via patched dmusic; Miles/Bink scoped |
| Community RE ([community-re.md](research/community-re.md), `db/community-symbols.json`) | 240 verified symbols, struct maps, determinism checkpoints |

**Pending runs on `legion`** (scripts ready):
- ~~`code_census.js`~~ **done 2026-10-15:** menu run plus a 14-minute in-game random map. 0 executed blocks outside the DB and 0 in DRM, across EXE 8,744 / engine 6,342 / DX7HRDisplay 531 / Miles 109 / TCP 25 blocks. **Phase 1 complete.**
- ~~`com_census.js`: D3D7/DInput calls in-game~~ **captured 2026-10-14** (spawn/menu/in-game; analysis pending);
- ~~`x87_runner.py` + `ummath_vectors.js`: hardware vectors that gate ADR-002~~ **done 2026-10-14, passed**;
- ~~`music_hooks.js` + loopback recording~~ **captured 2026-10-14** (trace + intensity schedule + WAVs; analysis pending);
- ~~`netcode_hooks.js` + `pktmon`: LAN session with a second Windows client~~ **done 2026-10-14** (legion ↔ VMware VM; 817/817 ticks agree).

Thread roles come from these censuses, not the static DB.

### Phase 2: Translation core → **M1: native random-map generator** (≈7–12 wk, per ADR-001 gaps 1–7)
**Status 2026-10-15, 04:45: M1 met.** All kit workstreams are merged (loader, DB translation, freestanding bundles, CRT, exact x87, EH). **51 random-map cases (459 calls) are byte-identical to Windows** on both Mac builds: 7 scripts × 6 sizes, 5 seeds, 3 epochs. Groups B (player counts) and E (editor) remain as extra coverage. Details in [phase2.md](phase2.md).

- **Gate passed:** [ADR-002](decisions/002-exact-x87.md) is accepted, with the design validated on legion hardware. Implement it within ~3.5–4.5 weeks; if the fork's x87 layer can't meet §5.2 within ~6 weeks, replace that layer only.
- **Gap 2 (EXE hygiene)** is reduced to ~2–3 days of kit changes: make the DB authoritative in `game_config.py`, take code ranges from the DB, disable the data-pointer scan, feed `data_in_code` into the jump-table ranges, exclude DRM (function-db.md §7.4).
- Close the fork's gaps:
  - multi-module loader (relocations, cross-module imports, DllMain order);
  - EXE translation hygiene, driven by the Phase 1 function DB;
  - runtime safety (strict traps, import argc, 4 GB guarded arena, CW 0x027f);
  - msvcrt/msvcp60 (~182 functions);
  - MSVC SEH and C++ EH;
  - exact x87;
  - freestanding bundle build mode.
- **M1:** maps byte-identical to Windows across a matrix of scripts, sizes and seeds.

### Phase 3: Platform → **M2: main menu, natively** (≈6–10 wk)
**Status 2026-10-15:** **M2 reached in substance.** EE boots natively to the main menu, the menus and options work (four scripted scenarios, `tools/test.py --menus`), and a random-map game starts and runs (Metal at 1920×1080, about 110 fps). Open: perceptual comparison against Windows references (0010 Part 3, 0026) and three small in-game oddities ([phase3.md](phase3.md), [menus.md](reference/menus.md)).

- kernel32/msvcrt/advapi32/version/shell32; case-insensitive file mapping (user data in `~/Library/Application Support/EmpireEarth/`); threads; timers.
- user32/gdi32 on SDL3 (macOS ships Arial, Tahoma, Times New Roman and Verdana, so text metrics can match).
- **DirectDraw7/D3D7 → Metal** for `DX7HRDisplay.dll`; DirectInput.
- **M2:** EE boots, menus and options work.

### Phase 4: Gameplay, audio, video → **M3: single-player parity** (≈5–8 wk)
**Status 2026-10-15:** core audio and video merged: Miles (all 34 AIL imports), DirectMusic (GothicKit/dmusic with 10 patches), Bink audio ([phase4.md](phase4.md)). Gameplay verification (M3) is next.

- Miles, DirectMusic (dmusic + upstream fixes), Bink.
- Skirmish, campaigns, scenario editor, save/load. Performance on big late-game battles: x87 fast paths, profiling.
- **M3:** full playthroughs; checksum streams match Windows on the scenario corpus.

### Phase 5: LAN → **M4: Mac↔Windows LAN game, no OOS** (≈2–4 wk)
**Status 2026-10-15:** Winsock/iphlpapi merged (P5-A). The Multiplayer lobby is specified from static analysis and Windows traces ([lobby.md](reference/lobby.md)): one custom-drawn WONAPI popup over a frozen DirectDraw frame. Its presentation (P5-C) and text/fonts/cursor (P5-D) are in progress ([phase5.md](phase5.md)).

- Winsock/iphlpapi HLE; `TCP Protocol.dll`; adapter selection incl. VPN tunnels.
- Mac↔Mac, then Mac↔`legion` and Mac↔Wine build; soak.
- VPN matrix: Tailscale (Join by IP), ZeroTier/Hamachi (broadcast). Optional "known peers" convenience.
- Check Steam/retail builds for version compatibility if friends use them.

### Phase 6: Shareable app → **M5: 1.0 for friends** (≈2–4 wk)
**Status 2026-10-15:** **the shareable `EmpireEarth.app` exists** (P6-B1–B3 merged, [phase6.md](phase6.md), [INSTALL.md](INSTALL.md)). It is 191 MB (DMG 78 MB) and holds nothing game-derived. It bundles a 76 MB clang+lld and a trimmed Python. Its first run imports the player's GOG files, then translates and compiles them in about 2–3 min on an M4 Max, with no developer tools. Open: GOG's real installer, a never-Xcode Mac, this repository's licence.

- App bundle + first-run importer: GOG offline installer (Inno) or game folder → hash check → translate + compile (one-time, cached).
- **Bundled LLVM toolchain** (D10, §5.8): freestanding build, lld link, ad-hoc signing, cached output.
- **No Ghidra on players' Macs:** first-run translation inputs come from the repo's function DB plus the player's own binaries, decoded by our own tools. Ghidra listings are derived from game code and can't ship. Workstream P6-A, **done 2026-10-15**: `tools/setup.py`/`tools/run.py` need only the repository and the game files, with inputs and generated C byte-identical to the Ghidra pipeline's ([build.md](reference/build.md) §2.1); inventory in [packaging.md](reference/packaging.md).
- Settings UI; logs and crash reports; a friend-facing README (VPN how-to).
- **Signing:**
  - Ad-hoc signed by default; friends use *Open Anyway* in Privacy & Security once.
  - Developer ID + notarization ($99/yr) only if friction becomes a problem.

### Phase 7: Decompilation and modernisation (ongoing, from M3)
Bug fixes, then the display plugin and modern renderer, widescreen/HiDPI UI, camera zoom, then UI, then simulation. Optional: AoC, fully native 64-bit.

**Rough total to M5:** ~6–9 months of focused work. This was recalibrated after the spike: ADR-001's gap list totals 23–35 weeks, on top of Phases 1 and 6.
- **Critical path:** translator correctness → D3D7-on-Metal → determinism.
- **Parallelises well across agents:** HLE functions, test triage, decompilation.

---

## 8. Repository layout (proposed)

Follows recomp-kit's game-repo pattern if adopted:

```
neoee-macos/
├── kit/             recomp-kit (submodule/fork), or our own recomp/ + runtime/ + hle/
├── game.toml        identity, module hashes (§2.4), translator inputs
├── db/              function DB, globals, per-module config
├── src/             EE-specific HLE (D3D7, DirectMusic, Miles, LAN) and overrides
├── windows/         legion harnesses: Frida scripts, runner.exe, capture scripts
├── tests/           fuzzers, function diffs, scenarios, golden checksum streams
├── tools/           analysis scripts (peinfo.py, Ghidra exporters)
├── docs/            PLAN.md, decisions/, research/, formats/
├── original/        (ignored) game files / installers
├── analysis/        (ignored) Ghidra projects and listings
└── build/           (ignored) generated C, objects, app
```

---

## 9. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Translator bugs (flags, x87, EH) | One semantics source; Unicorn + real-x86 fuzzing; lockstep validation; interpreter cross-check |
| R2 | Mac↔Windows desync | Exact x87 at measured PC/RC; msvcrt transcendentals bit-exact; checksum streams; OOS oracle; LAN soak. Mac↔Mac is unaffected. |
| R3 | Missed functions / indirect targets | **Mitigated in Phase 1:** the function DB classifies ≥ 99.9% of `.text` in every module and resolves every vtable slot. With the DB the kit translates the EXE with 0 gaps, 0 unmodelled instructions and no data-pointer scan. Trap + report stays as the safety net. |
| R4 | D3D7 fidelity on Metal | Scope to `DX7HRDisplay` usage; screenshot diffs vs Windows; 00xJS's D7VK fixes as hints |
| R5 | DirectMusic fidelity: bank bug (patched), missing system GM bank, no reverb, variation differences | Pinned dmusic fork + upstream fixes; macOS `gs_instruments.dls`; add reverb; compare against Windows loopback recordings; offline-render fallback |
| R6 | Windows friends on another build (Steam/retail) | Hash check in Phase 5; document "use GOG" |
| R7 | Tailscale has no broadcast | Built-in Join by IP; optional known-peers unicast; recommend ZeroTier/Hamachi for auto-discovery |
| R8 | Friends need a compiler at first run | Xcode CLT (free) or bundled LLVM (Phase 6 decision) |
| R9 | Young base kit (one month old, one maintainer) | Fork + pin; MIT; we can maintain it |
| R10 | Legal | Never commit or publish game bytes, generated C or decompiled game code; local generation from each player's own copy |
| R11 | Agent reliability at scale | Mechanical verification gates on every change; small units; golden tests in CI |

---

## 10. Next steps

1. Phase 0 housekeeping: git, `.gitignore`, ADR folder.
2. `legion`: copy the GOG EE folder + registry, verify it runs, install capture tooling, record the FPU modes and a LAN session against the Mac's Wine build.
3. recomp-kit spike on `Default.dll` → ADR-001.
4. Ghidra + REA on all canonical modules → first function-DB export.

## 11. Open questions

None outstanding; see the decisions log (§1). Phase 0 started 2026-10-14.
