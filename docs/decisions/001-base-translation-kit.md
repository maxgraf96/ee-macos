# 001: Base translation kit: adopt recomp-kit or build our own
Status: accepted
Date: 2026-10-14

## Context

PLAN §5.1 makes the base kit a Phase 0 gate. The candidate is
[recomp-kit](https://github.com/veritr1x/recomp-kit) (MIT): a Python translator that
turns Ghidra listings of 32-bit x86 PE code into C, plus a C/C++ runtime (guest arena,
PE loader, cooperative scheduler, SEH), Win32/DirectX shims and SDL3/Metal hosts. It
ships six game ports, among them Populous, Majesty, Pharaoh, Siege of Avalon (Delphi)
and NFS Most Wanted (D3D9).

The spike pointed the kit, unmodified, at commit `eb32cc4`, on the GOG modules
`sxlrt233.dll`, `Low-Level Engine.dll` and `Default.dll`. It then tried translation
plus compile only of `Empire Earth.exe`. Full notes are in `spikes/ee-recomp/NOTES.md`,
which is git-ignored and local to this machine.

Hard requirements from the PLAN:
- one semantics source;
- exact, deterministic x87 (Mac↔Windows lockstep, D3);
- every indirect target resolvable, with a trap on a miss;
- function-level overrides;
- a differential harness;
- freestanding generated C linked by a bundled `ld64.lld` (D10, §5.8).

## Options considered

1. **Adopt as an upstream-tracking submodule.** Use the kit as is and send changes upstream.
2. **Adopt as a fork.** Fork at `eb32cc4`, pin our fork as `kit/`, and keep EE code in
   this repo using the kit's game-repo pattern. Offer generic fixes upstream when cheap,
   but never depend on upstream.
3. **Build our own, borrowing parts** (MIT). Write a new translator and runtime on the
   PLAN's principles, and lift shims and hosts from the kit file by file.

## Decision

**Option 2: adopt recomp-kit as a fork, pinned as the `kit/` submodule.**

**Why the kit carries its weight.** The core pipeline worked on EE within the spike day:
- Ghidra 12.1.4 runs headless on arm64.
- The translator covered 100% of listed functions in all three DLLs.
- The code compiled to arm64.
- The DllMains and the random-map PIM ran.
- `EERMPIMSetRandomMapValues` parsed real `.rmv` scripts.

Generated C is fast enough to translate and build (EXE: 72 s translate, 85 s compile on an
M4 Max). It is almost freestanding already.

Building our own would mean re-creating a large body of working code:
- translator;
- loader, scheduler and SEH landing machinery;
- kernel32, user32 and gdi32;
- DirectDraw, Metal/SDL3 presentation, the mixer, Bink and the launcher.

That is an estimated **19–26 extra weeks** before reaching the kit's current level.

**Why not track upstream.**
- Upstream has had one author and no commits since 2026-09-21: 484 commits landed in
  nine days, then it went quiet.
- The changes EE needs reach the kit's core: the x87 register model, the loader, MSVC
  EH and arena layout. Upstream has made the opposite x87 choice: "x87 registers are
  `double` (plan ruling; not 80-bit)".

**What we keep from option 3.** The fork is ours to restructure. If the exact-x87 work
(gap 6) cannot be done inside the fork's `x86.h` and translator within its budget, we
replace that layer, not the kit.

## Consequences

- We own ~216k lines of first-party kit code (181k C/C++/ObjC, 19k Python), partly
  agent-written at high speed. Docs are extensive but partly inconsistent. Every new
  kit area needs reading before trusting.
- EE code lives in this repo following the kit's pattern:
  - `game.toml` and `globals.toml`;
  - `mods/CMakeLists.txt` for EE-only targets;
  - `[translate] native` for overrides.
  Generic kit changes stay in the fork, and we keep the kit's "no game literal in kit
  code" rule.
- The plan's spike pass criterion was "run the random-map generator". It is met
  **partially**:
  - DllMain, `PIM*` and the script parse all work.
  - The `Create*Map` stages need the context the EXE builds, so they move to Phase 2 (M1).
- PLAN corrections from the spike evidence:
  - §5.1: there is **no DD7** in the kit: `DirectDrawCreateEx` returns `DDERR_UNSUPPORTED`.
    The Miles shim is **not a stub**: it has 46 AIL exports, of which 17 of EE's 34
    are present.
  - §5.2: the kit's x87 is double-based. See gap 6.
  - §2.3: `.cms_t`/`.cms_d` are DRM residue, including a Petite-packed PE.
  - §2.3: every EXE section is non-executable (`0xc0000040`), and `SizeOfHeaders` (0x1000)
    overlaps `.text`.
  - §5.3: `DX7HRDisplay` may submit untransformed vertices (it exports
    `ConvertToD3DMatrix`). Check this in the Phase 1 COM inventory.

### Gap list (rough focused weeks, one developer with agents)

| # | Gap | Evidence | Effort |
|---|---|---|---|
| 1 | Multi-module loader. Needs base relocation, binding the EXE's imports from DLLs (the main IAT is bound before aux modules exist), dependency-ordered DllMain for static imports, and TLS for DLLs | `runtime/loader.cpp:296-370, 744-748`; `kernel32.cpp:1137-1170`. The spike pre-rebased copies | 1–1.5 |
| 2 | EXE translation hygiene. Classify code without section flags. Ignore the header/`.text` overlap. Handle the stack-indexed switch shape. Take the Phase 1 function DB as authoritative input. Curb the data-pointer scan on an EXE without relocations | 13,343 entries came from data pointers; 145 data-as-code sites; 3 undecoded tables | 1–2 |
| 3 | Runtime safety. Strict mode for unknown calls (today they return EAX=0 and continue, `cpu.cpp:200-266`). An argc for every stdcall import (missing argc means stack drift). A 4 GB arena with guard (today out-of-arena access becomes a host SIGSEGV). Heap above 208 MB. Thread CW 0x027f, not 0x037f | `loader.cpp:778`, `kernel32.cpp:3110`, `x86.h:43-74` | 1 |
| 4 | msvcrt and msvcp60 HLE: about 182 functions (heap, stdio, string/MBCS, locale, time, CRT start-up) | The kit covers 2 of 167 msvcrt and 0 of 17 msvcp60. The spike needed about 70 shims | 2–3 |
| 5 | MSVC SEH and C++ EH runtime (`_except_handler3`, `__CxxFrameHandler`, `_CxxThrowException`, `_setjmp3`/`longjmp` unwind), built on the kit's Delphi SEH landings | The EXE has 2,274 C++ EH frames and 98 throw sites; sxlrt233 has 376 frames; the engine and Default.dll have 0. Kit spec: "MSVC `__CxxThrowException` … unsupported" | 2–3 |
| 6 | **Exact x87 mode.** Needs 64-bit-significand registers wherever values carry extended precision, exact FLD constants, extended exponent range and bit-exact msvcrt math (`_CI*`, `pow`, `log`, `sscanf %f`). The D3D7 shim must leave the control word alone: `legion` shows the game sets 24-bit on the render thread itself via `UMSetFloatingPointPrecision`, and every other thread stays at 0x027f. `_controlfp` must be exact and cheap per thread (about 4,200 calls/s at the menu). Verify on `legion`, because Unicorn's FSIN is double precision | `x86.h:13, 211-226, 1259-1271`; `translate.py:3687-3696, 3831-3874`. FLDPI×x at PC=53 differs in 26% of cases (exact model and Unicorn agree). Plain `+ − × ÷ √` at PC=53 on doubles is already exact | 4–6 |
| 7 | Freestanding bundle mode. Add a `RECOMP_FREESTANDING` include guard. Make the main table self-register like aux modules. Route the fatal path through the runtime. Provide libm-style helpers from the runtime. Add an ld64.lld step to the build | Five stand-in headers were enough. All four modules linked as bundles with no SDK; the Default.dll bundle was `dlopen`ed and gave byte-identical results | 0.5–1 |
| 8 | Direct3D 7 for `DX7HRDisplay`: DD7, Surface7, D3D7 and Device7 (plus vertex buffers if used), a texture-stage-state ubershader in MSL, possibly software T&L, and GDI drawing onto surfaces | There is no DD7; the Device3 TSS is folded into a single legacy stage (`d3d.cpp:2295-2330`); D3D9 fixed-function draws are skipped (`d3d9.cpp:1588`) | 5–7 |
| 9 | GDI text: `CreateFontA`, `GetTextExtentPoint32A`, `ExtTextOutA`, `TextOutW`, `GetCharWidthW`, plus 4 more gdi32 calls | Coverage table | 0.5–1 |
| 10 | Miles: the remaining 17 AIL calls (`open_digital_driver`, `serve`, sample position/rate, …) | `dx/mss32.cpp` | 0.5–1 |
| 11 | DirectMusic through GothicKit/dmusic | Not in the kit | 3–4 |
| 12 | Winsock and iphlpapi for LAN: real non-blocking sockets, `select`, broadcast, and yields to the scheduler baton | ws2_32 is 24 of 24 logging-only (`misc.cpp:2167-2197`); wsock32 is 5 of 28 | 2–3 |
| 13 | Fork hygiene: Populous names and hook sentinels, drop `d3d11.cpp`/`d3dcompiler.cpp` (they describe an LGPL contract and EE does not need them), CI | — | 0.5–1 |

The total is about 23–35 weeks. Of that, about 13–20 weeks (gaps 6, 8, 11, 12 and most of
4–5) would be needed under any option, and are already budgeted in PLAN Phases 2–5.

### Gate for Phase 2

Write a short exact-x87 design note before closing gaps 4–6. Prove it with a differential
suite: the kit's Unicorn harness for PC/RC arithmetic, and `legion` hardware for
transcendentals and msvcrt math. If that note shows the fork's x87 layer cannot meet
§5.2 within 6 weeks, revisit this ADR for that layer only.

## Evidence

All measurements are from 2026-10-14 on an M4 Max (16 cores), macOS 15.5. Full log:
`spikes/ee-recomp/NOTES.md`.

**Setup.**
- Kit commit `eb32cc42623e0e9e868b9d27423a57d5a5cba56b` (`m1.2-424-geb32cc4`).
- Ghidra 12.1.4 on OpenJDK 21.0.12.1 aarch64 (Homebrew).
- lld 23.1.3 and Homebrew clang 21.1.8.
- Python 3.12 venv. Python 3.14 cannot build the pinned numpy 2.0.2.

**Ghidra function counts.**

| Module | Functions | Time |
|---|---|---|
| Default.dll | 622 | 22 s |
| engine | 4,899 | 52 s |
| sxlrt233 | 2,970 | 50 s |
| EXE | 20,838 | 322 s |

**Translation.** All three DLLs translated with 0 failures, 0 table gaps and 0 unmodelled
instructions:

| Module | Functions | Entries | Time |
|---|---|---|---|
| sxlrt233 | 3,350/3,350 | 3,415 | 4.6 s |
| engine | 5,142/5,142 | 5,326 | 6.5 s |
| Default.dll | 740/740 | 856 | 0.8 s |
| EXE | 36,849/36,863 | 45,994 | 71.5 s (2.2 GB RSS) |

The EXE needed the three workarounds of gap 2.

**Unresolved indirect targets.** Two were hit at run time with the kit's DLL default
`function_alignment = 4`. They returned 0 silently and the parser stopped early. With
alignment 1 (function starts in the engine and Default.dll are unaligned), none were hit.

**Size and build time.**
- EXE generated C: 5.43 M lines, 209 MB, 186 chunks.
- Compile: 157.6 s wall with the kit build (-O2 -g, -j16), or 85 s freestanding (-O2, 1,069 CPU-s).
- Link with ld64.lld: under 1 s.
- arm64 `__TEXT` is 135.6 MB, against 4.2 MB of x86.

**Execution** (`ee_harness`):
- DllMain → 1 for sxlrt233, the engine and Default.dll. Static initialisation (all three DLLs) made 5,043 `sqrt` calls.
- `PIMGetName` → UString "Default"; `PIMGetType` → `0x726d6170` ('pamr'); `PIMIsValid` → 1.
- `EERMPIMSetRandomMapValues(ctx, UString "Continental")` → 0, after reading 5 `.rmv`
  files (781 lines) and writing 74 context fields.
- Remaining issue: a `#DE` at `0x1000212b`. The cause is the synthetic context. The kit
  logs a `#DE` and continues instead of raising it.

**Import coverage.** Kit shims cover 226 of EE's 519 external functions (44%). Gaps are
listed in the table above. EE's DirectInput use (`IDirectInputA`/`DeviceA`) and Bink
(9/9) fit as is.

**Overrides.** Three mechanisms exist:
- compile-time `FN_<addr>` native replacements;
- run-time hook table plus the C/Lua mod API;
- capture/replay validation of native candidates (`mods/native/replay.h`).

**Tests and licence.**
- `tools/test.py`: 414 passed, 4 skipped.
- Licence: MIT, with a scoping preface (so GitHub shows `NOASSERTION`). NOTICE lists only
  permissive or dynamically linked LGPL (FFmpeg) components. The GeneralUser GS soundfont
  carries a sample-provenance caveat.

### Reproduce

From `spikes/ee-recomp/` (ignored):

```sh
git clone https://github.com/veritr1x/recomp-kit.git kit && git -C kit checkout eb32cc42623e0e9e868b9d27423a57d5a5cba56b
brew install ghidra lld
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r kit/requirements-dev.txt
GD="original/gog/Empire Earth"           # in this repository's checkout
mkdir -p original/stage && ln -sfn "$GD" original/gog && ln -sfn "$GD/Data" original/stage/Data
ln -sfn "$GD/Low-Level Engine.dll" "original/stage/Low-Level Engine.dll"
.venv/bin/python -I tools/rebase.py "$GD/sxlrt233.dll" original/stage/sxlrt233.dll 0x00400000
.venv/bin/python -I tools/rebase.py "$GD/Default.dll" original/stage/Default.dll 0x10200000
.venv/bin/python tools/analyze.py
.venv/bin/python tools/build.py --regenerate --target gen --jobs 16
.venv/bin/cmake --build build/cmake/macos --parallel 16 --target ee_harness ee_harness_dyn
RECOMP_LOG=1 ./build/recomp/ee_harness --rmv Continental
.venv/bin/python -I tools/list_imports.py "$GD" build/ee_imports.tsv
EE_NO_SPIKE_SHIMS=1 ./build/recomp/ee_harness --imports build/ee_imports.tsv
tools/fs_build.sh build/recomp/gen/aux-b_default build/fs aux-b_default build/recomp/gen
EE_BUNDLE=$PWD/build/fs/aux-b_default.bundle ./build/recomp/ee_harness_dyn --rmv Continental
for t in x87model unicorn_pc unicorn_fldpi unicorn_fsin; do .venv/bin/python -I tools/x87/$t.py; done
```

The EXE scale run (`exe-scale/`: `tools/mark_code.py`, header-function filter,
`--allow-table-gaps`/`--allow-unmodelled`) is spelled out in NOTES.md §7.
