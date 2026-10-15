# Phase 2: translation core → M1 (native random-map generator)

Started 2026-10-14. Context: [PLAN.md](PLAN.md) §5.1–§5.2 and §7 Phase 2, [ADR-001](decisions/001-base-translation-kit.md) gaps 1–7, [ADR-002](decisions/002-exact-x87.md), [function-db.md](reference/function-db.md).

## M1 definition of done
- `Default.dll` + `Low-Level Engine.dll` + `sxlrt233.dll` translated from the canonical GOG modules, using the function DB as the authoritative input.
- They are loaded by the fork's multi-module loader at their **runtime bases**, run their `DllMain`s, and execute the random-map pipeline.
- Results are **byte-identical** to the real DLLs on Windows across a matrix of ≥ 50 cases (scripts × sizes × seeds × player counts), measured with the capture/replay oracle below.
- **Build:** freestanding generated C, linked into bundles by `ld64.lld` (PLAN §5.8). The build passes on a machine without the Xcode Command Line Tools.
- **x87:** ADR-002 is implemented; the hardware corpus (criterion 1, 1.09 M vectors) passes against translated x87 code.
- **CI:** the kit's own tests and our suites are green.

## The M1 oracle: capture and replay
We don't hand-build the map-generator context. Instead:
1. **Windows (legion operator, mailbox):** Frida hooks each `EERMPIM*` call that `Default.dll` receives during real random-map starts. It records the arguments, every input memory region reachable from them (the context struct and pointed-to buffers) and all file reads (`.rmv` scripts). It records outputs after each call. Seeds and settings vary across the matrix.
2. **Self-check:** a native 32-bit Windows driver (MSVC x86 on legion) replays the captures against the real DLLs and must reproduce the recorded outputs. This proves a capture is complete.
3. **Mac:** the same replay driver logic runs against the translated DLLs. Outputs must be byte-identical.

The capture format is specified by the `m1-re` workstream in `docs/reference/rmg.md`. Captures are derived from game data and stay out of git (`analysis/m1-captures/`).

## Workstreams
| ID | Name | Scope | Where |
|---|---|---|---|
| A | game-repo | Make `ee-macos` the kit's game repo: `game.toml` (hashes, modules, DB-driven translate inputs via `tools/ghidra/kit_export.py`), `globals.toml`, build/test wrappers, game-file setup. Port the spike harness to `tests/m1/`. **Gap 2:** DB-authoritative translation (function-db.md §7.4). **Gap 7:** freestanding bundle mode plus the `ld64.lld` step. | `kit` branch `feat/game-repo` + `ee-macos` |
| B | runtime-core | **Gap 1:** multi-module loader (relocation, cross-module import binding, dependency-ordered `DllMain`, TLS). **Gap 3:** strict traps, stdcall argc for every import, 4 GB guarded arena, larger heap, CW `0x027f`. | `kit` branch `feat/runtime-core` |
| C | x87 | ADR-002: mode word, guard, SoftFloat-80 side path (vendored, freestanding), PC24 rounding, `_controlfp` HLE, `-ffp-contract=off` + FMA check, corpus conformance. | `kit` branch `feat/x87` |
| D | crt | **Gap 4:** msvcrt/msvcp60 HLE in priority order (M1 modules' imports first, then the EXE's), using `db/win32-imports.json` as the checklist. | `kit` branch `feat/crt` |
| E | eh | **Gap 5:** MSVC SEH + C++ EH (`_except_handler3`, `__CxxFrameHandler`, `_CxxThrowException`, `_setjmp3`/`longjmp`). Wave 2. | `kit` branch `feat/eh` |
| F | m1-oracle | Capture hooks, the native replay driver on legion, the capture matrix. | legion operator via mailbox, branch `legion/work` |
| G | m1-re | Reverse-engineer the EXE's random-map pipeline: call sequence, context layout, inputs and outputs, seeds. Specify the capture format and the replay driver (`docs/reference/rmg.md`). | `ee-macos` docs + `analysis/` |

## Branches, worktrees and merging
- **Kit fork:** `codeberg.org/maxgraf/recomp-kit`. `main` is an upstream mirror at `eb32cc4`; `ee` is our integration branch. `ee-macos/kit` is a submodule tracking `ee`.
- **Worktrees:** each kit workstream works in its own worktree under `/Users/max/Code/ee-kit-wt/<name>`, on branch `feat/<name>` from `ee`. Never edit `ee-macos/kit` directly.
- **Before reporting:** rebase onto the latest `ee` and push the feature branch to the fork (`git push fork feat/<name>`).
- **Merging:** the main session merges into `ee` one branch at a time, runs the full test suite, bumps the submodule in `ee-macos`, and announces new `ee` heads to running workstreams.
- **Every change** must keep `kit/tools/test.py` green, add tests for new behaviour, and follow the kit's rules (`check_repo.py`; no game literals in kit code; `check_game_literals.py`). EE-specific code goes in `ee-macos`, not the kit.
- **Shared files** (`translate.py`, `x86.h`, `loader.cpp`): keep diffs localised, don't reformat, and mention touched areas in the report so merges stay cheap.

## Legal hygiene (unchanged)
No game bytes, generated C, captures or Ghidra databases in either repo. The kit stays game-neutral.

## Status (2026-10-14, evening)
| ID | State | Result |
|---|---|---|
| A game-repo | **merged** (`ee` 67fe69f, ee-macos 884a1c2) | DB-driven translation: Default 834, sxlrt233 3,351, engine 5,221, EXE 36,888 functions, 0 failures/gaps/unmodelled. Freestanding bundles linked by `ld64.lld`. `game.toml` base map. The M1 harness runs the original DLLs through the kit's loader. |
| B runtime-core | **merged** (`ee` 35624d7) | Multi-module loader (relocation, binding, Windows DllMain order, LoadLibrary refcounts, TLS), strict mode, 4 GB guarded arena, argc for 518/519 imports, CW `0x027f`. |
| C x87 | **merged** (`ee` 0f7a705) | ADR-002 implemented: 0 differences on 1.15 M non-transcendental hardware vectors (both corpora); transcendentals within 1 ulp; criterion 2 at 45,380 `UM*` calls, 0 differences, through the translated engine; `_controlfp` family; FMA check; SoftFloat-80 vendored freestanding. Extra hardware vectors and a 131k `UM*` set are pending (legion 0009). |
| D crt | **merged** (`ee` 381fa1a) | msvcrt 160/167, msvcp60 17/17; 42k unit checks. The Windows reference run is pending (legion 0008). |
| E eh | **merged** (`ee` 690fec0) | MSVC SEH (`_except_handler3`) + C++ EH (`__CxxFrameHandler`, `_CxxThrowException`, `_setjmp3`/`longjmp`), resuming at continuations via frame checkpoints; all 47 EXE catch blocks and continuations are translated entries; 817 `eh_tests` checks; WON `ReadBufferException` caught on real EXE code; arena faults delivered as guest access violations. |
| F m1-oracle | legion 0006 + 0007 queued | replay driver build; capture environment with DLLs rebased to the `game.toml` map; S00 smoke |
| H m1-replay | **merged** (`ee` 9388162, ee-macos 25d6de1) | Mac replay backend (bundles + linked), Mac-side recorder, round-trip test (7,396 allocations, 88 file calls, 457 output regions; corruptions caught), `tools/m1/run_matrix.py`, [m1-replay.md](reference/m1-replay.md) |
| G m1-re | **merged** (ee-macos 1401ee9) | [rmg.md](reference/rmg.md): pipeline, context, capture/replay spec, 60-case matrix |

**Mac side complete.** M1 now waits only on legion: 0007 (rebased capture copy) → 0006 (S00 capture + Windows replay) → the 60-case matrix → `tools/m1/run_matrix.py` on the Mac.

## First M1 results (2026-10-15, ~00:35 BST)
- **Windows** (legion, rebased capture copy + `base_guard.js`): 7/9 calls of each case replay byte-identically against the real DLLs (S00b, A01).
- **Mac** (translated DLLs, bundles and linked): **14/18 packs pass** for S00b and A01, the same 7 calls per case byte-identical: positions, flat, **height map**, shallows, **resources**, **trees**.
- **The 2 failing calls per case are capture-tooling issues, not translation errors:**
  - `SetRandomMapValues` copies uninitialised stack bytes into heap structs: 75 B differ on Windows, 121 B on the Mac. Fix: capture and restore the stack window below ESP.
  - `CreatePlayerLandWaterMap`'s entry capture misses part of the ctx heap block (76 B). Fix: capture whole blocks at entry.

  Both are in progress (`phase2/m1-fix`).
- The legion operator is running the 49-case matrix unattended. Groups B and E need setup-screen input and come after.
- **00:45:** with the ctx-block capture fix (`ca06a0f`), the `__dllonexit` replay shim (`54ee9cf`, Mac `2bd107b`) and narrow documented compare masks (`fee5bff`), **case A06 is 9/9 on the Mac (bundles + linked), identical to Windows.** Pre-fix cases S00b and A01–A05 match Windows failure for failure and are being re-captured. Watch item: an exit-only EXE object in A05 `GenerateResources`.
- **00:55:** Mac replay of the post-fix cases **A06–A11 (Continental Gigantic; Highlands Tiny…Huge): 6 cases, 54/54 packs pass, 0 mismatch**, on both builds, identical to Windows (9/9 each). M1 needs ≥ 50 cases; the legion matrix continues from A12, then groups B/E and the re-captures of S00b and A01–A05.
- **01:20:** Mac replay of **A12–A21: 89/90 packs pass**. The one mismatch is A15 `GenerateResources`, identical to Windows (a block that existed before the call but was unreachable at entry; capture fixed from A17 by `9503aa6`, so A15 gets re-captured). **15 post-fix cases fully byte-identical so far:** A06–A14 and A16–A21 (Continental, Highlands, Large Islands, Mediterranean; Tiny…Gigantic).
- **02:30:** Mac replay of **A22–A37: 13 cases, 117/117 packs pass on both builds**, all 9/9 on Windows too (Mediterranean Large…Gigantic, Plains, Small Islands, Tournament Islands Tiny). **28 post-fix cases fully byte-identical:** A06–A14, A16–A24, A26, A27, A29–A34, A36, A37. A25, A28 and A35 produced no capture on legion (the capture timed out at 271–631 s) and are to be retried.
