# Building Empire Earth on recomp-kit

Status: Phase 2, workstream A (game-repo), 2026-10-14. This repository is a recomp-kit **game repository**: the kit (our fork, submodule `kit/`, branch `ee`) holds the translator, runtime and hosts; this repository holds what is Empire Earth's: `game.toml`, `globals.toml`, the function DB, the M1 harness and thin wrappers around the kit's tools.

Labels: **[measured]** = output of the commands here on an M4 Max (16 cores, macOS 15.5); **[static]** = read from the binaries or the DB; **[inferred]** = reasoning.

---

## 1. Prerequisites

| What | Why | How |
|---|---|---|
| Apple Silicon Mac | the only target | |
| Python 3.12 venv | the wrappers and the kit's tools | `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r kit/requirements-dev.txt` (Python 3.14 cannot build the pinned numpy) |
| Xcode Command Line Tools | the kit's hosted build (runtime, hosts, harness) | `xcode-select --install` |
| LLVM clang + lld | freestanding bundles (§5); no CLT needed for these | `brew install llvm lld` (`/opt/homebrew/opt/llvm/bin/clang`, `/opt/homebrew/opt/lld/bin/ld64.lld`) |
| Ghidra 12.1.4 + the func-db environment | development only: regenerating the DB, `setup.py --ghidra`, `export.py --compare` | [function-db.md §9](function-db.md) |
| Your GOG installation | everything game-backed | linked by `tools/setup.py`, never copied into git |

Every wrapper takes `--kit DIR` (or `RECOMP_KIT_DIR=DIR`) to use another kit checkout than the `kit/` submodule, e.g. a worktree of the fork during kit work. Nothing else changes: outputs still go to this repository's ignored `build/`.

## 2. Setup: translation inputs from the function DB

```sh
git submodule update --init kit
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth"
```

1. The kit's `setup.py --link-only` checks `Empire Earth.exe` against `game.toml` and links the installation at `original/gog/Empire Earth` (ignored).
2. Every module in `game.toml` is checked against its SHA-256 (PLAN §2.4).
3. `tools/inputs/export.py` writes each module's translation inputs into `analysis/kit/<module>/` (ignored), from two things only: the tracked DB (`db/functions/<module>.json`) and the player's own module. **No Ghidra and no `analysis/func-db/`.** All nine modules take 15 s [measured]:
   - `functions.tsv`: the DB's non-DRM entries, with the DB's names and body sizes;
   - `functions/<va>.asm`: each entry's body, decoded with capstone and printed in the listing grammar;
   - `funcdb.json`: the DB sidecar.

`funcdb.json` (schema `recomp-funcdb/1`) is what `game.toml` names as `function_db`. With it the kit's translator:
- classifies code by the DB's code ranges instead of section flags;
- seeds every entry point and alternate entry (`alt_vas` in the DB);
- keeps data-in-code out of every scan;
- never translates the DRM;
- runs **no** data-pointer scan ([function-db.md §7.4](function-db.md)).

`tools/run.py` re-exports by itself when the inputs are missing or older than the DB, `db/inputs.json` or `tools/inputs/`. `--link-only` skips step 3.

### 2.1 Inputs without Ghidra (Phase 6, P6-A)

The first-run pipeline on a player's Mac (PLAN D10, §5.8) has no Ghidra, and the app cannot ship listings, which are derived from game code. So the inputs are made on the player's machine from the DB plus the player's binaries.

**Design: write the same listings, with our own decoder.** Two designs were weighed:
- (a) generate the existing input files from the DB and the binary, with a decoder that prints Ghidra's syntax;
- (b) give the translator a native mode that decodes the image itself, with the DB for bounds.

We chose (a), for these reasons [static, `kit/tools/recomp/translate.py`]:
- **The translator consumes listings as text, not only as instructions.**
  - It parses the operand text (`parse_operand`).
  - It copies every line into the generated C as a comment (`/* 00401000 PUSH EBP */`).
  - It reads control flow from the listing's shape: a listing that ends on a `CALL` marks a callee that never returns (`noreturn_callees_from`), and the `functions.tsv` size bounds a function (`Function.end`).
  - Byte-identical C therefore needs Ghidra's exact text on either route. (b) still needs the same renderer, only inside the kit.
- **(b) would move EE-validated rendering into a shared, game-neutral file** that other workstreams edit. The kit's own in-image decoder (`Image.to_insn`, used for recovered blocks) prints a different spelling, and changing it would change every game's recovered code.
- **(a) is checkable per instruction.** The Ghidra path and the Ghidra-free path write the same files, so they can be compared line by line (`export.py --compare`): a much finer check than a C diff. The kit is untouched.
- **The cost of (a)** is 214 MB of listings written on the player's machine, in 15 s. They can be deleted after translation.

**How [static, `tools/inputs/`]:**
- `sidecar.py`: `functions.tsv` and `funcdb.json` as pure functions of the DB. `tools/ghidra/kit_export.py` uses the same code, so only the listings can differ between the two paths.
- `listing.py`: decodes a body front to back: the DB's `chunks`, or `[va, va + size)`. The DB's bodies are Ghidra's, and Ghidra's function bodies hold only instructions. It renders each instruction from the encoding (opcode, ModRM, displacement and immediate widths), not just from capstone's operand list, because Ghidra's display depends on the encoding:
  - an 8-bit immediate prints signed after an arithmetic opcode (`ADD EAX,-0x8`: 83 /0 /2 /3 /5 /7, 6A, 6B) and unsigned after a logical one (`OR EDX,0xffffffff`: 83 /1 /4 /6); IMUL's full-width immediate (69) prints signed;
  - an 8-bit displacement prints signed and is left out when zero (`[EBP + -0x8]`, `[EBP]`); a 32-bit displacement prints unsigned, zero included (`[EBP + 0xffffff10]`, `[EDI*0x8 + 0x0]`);
  - absolute addresses print as 8 digits except through a segment (`[0x00654321]`, `FS:[0x0]`); the A0–A3 forms and LEA carry no size;
  - branch targets print as 8-digit addresses; condition codes use Ghidra's names (`JC`, `JNZ`); REP is a mnemonic suffix (`MOVSD.REP ES:EDI,ESI`);
  - x87 register forms follow Ghidra's constructors (`FADD ST0,ST1` but `FMUL ST1`; `FCOM`, `FXCH` and `FADDP` bare for ST1); floating-point memory operands are `float`/`double ptr`.
  - Anything outside this vocabulary is an error, not a guess. The modules are hash-pinned, so the vocabulary is fixed.
- `export.py`: writes the three files and checks two things:
  - every body decodes to exactly the DB's own instruction count (`insns`);
  - each module's inputs hash to the SHA-256 that `db/inputs.json` pins.
  
  The pins are the inputs this repository was compared against Ghidra with, so a player's first run either reproduces them exactly or stops. A mismatch means a different capstone (the pin is 5.0.9, `kit/tools/requirements.txt`) or a DB change nobody re-pinned.

**Equivalence with the Ghidra pipeline [measured, 2026-10-15]:**

| | Ghidra export (`setup.py --ghidra`) vs Ghidra-free |
|---|---|
| `functions.tsv`, `funcdb.json` | identical in all 9 modules |
| listings | 47,980 files, 1,931,496 instructions: **0 differences** |
| generated C (`translate.py` for the EXE and all 8 DLLs, kit `ee` 1220f89) | 271 files, 268 MB: **identical** (`diff -r`); the reports differ only in input paths and timings |

- **Reference.** Eight modules were compared against the Ghidra exports in the main checkout's `analysis/kit/`.
  - The comparison was repeated end to end after the refactor: `tools/setup.py --ghidra` (38 s) exported all nine modules again, with the shared `sidecar.py` and Default.dll's regenerated project.
  - `export.py --compare analysis/kit` then reported all nine modules identical.
- **Default.dll was compared against a fresh export.** The main checkout's Default.dll listings came from a Ghidra project older than the regenerated Default DB (§4 "Fixed: Default.dll+0x6caa"). Its +0x6c9a listing stops at 17 bytes, where the DB has 37.
  - Translated, that stale listing gives a listing gap and a recovered block `fn_10306caa` instead of one 37-byte body: different C, the same behaviour.
  - Regenerating Default.dll from scratch (`regen.py default`) reproduced the tracked DB byte for byte, and its Ghidra export equals the Ghidra-free inputs exactly.
  - **A checkout whose `analysis/kit/default` predates the DB fix should re-run `tools/setup.py`.**
- The same comparison passes with a trimmed python-build-standalone runtime ([packaging.md](packaging.md)).
- **The Ghidra-free build behaves the same [measured].** It was set up with `FUNCDB_WORK` and `GHIDRA_HOME` pointing nowhere and no `analysis/func-db/`.
  - `tools/setup.py`: 14 s. Then `tools/run.py --no-open` and `tools/test.py --boot`: the boot test passes (11 milestones, 14 threads, render thread at 007f, clean exit).
  - `--m1-replay`: the self-tests pass. On the post-fix cases A06–A21 (144 packs per build), 143 pass on both builds. The one mismatch, A15 `GenerateResources`, is the same failure line for line as in the matrix run on Ghidra-made inputs (`analysis/m1-captures/report.md`).
  - `tools/run.py` with `analysis/kit/` deleted rewrites the inputs, translates and builds.

**Development:**

```sh
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth" --ghidra   # the Ghidra export (FUNCDB_WORK)
.venv/bin/python tools/inputs/export.py --compare analysis/kit               # Ghidra-free vs whatever is there
.venv/bin/python tools/inputs/export.py --pin                                # after a DB change, once compared
```

After a DB regeneration: export with `--ghidra` into `analysis/kit`, run `export.py --compare analysis/kit`, then `export.py --pin`, and commit `db/inputs.json` with the DB. `tests/test_inputs.py` checks the renderer on hand-written encodings, the sidecar against every DB and, with the game linked, the DLLs' inputs against their pins.

## 3. The base map

Every EE DLL prefers 0x10000000, so each is translated for, and loaded at, its own base. A base other than the preferred one is reached through the DLL's base relocations: the translator applies them to the image it reads and moves the Ghidra listings (exported at the preferred base) by the same delta, and the kit's loader applies the same relocations to the original file when it maps it (9,655 for sxlrt233 and 1,644 for Default.dll [measured]). No relocated copies are involved. All bases are 64 KB aligned, above the kit's shim trampolines (0x0ff00000–0x10000000) [static: SizeOfImage from the DB].

| Module | Preferred | **Load base** | SizeOfImage | End | game.toml key |
|---|---|---|---|---|---|
| Empire Earth.exe | 0x00400000 | **0x00400000** (no relocations) | 0x682000 | 0x00a82000 | main image |
| Low-Level Engine.dll | 0x10000000 | **0x10000000** | 0x130000 | 0x10130000 | `b_engine` |
| sxlrt233.dll | 0x10000000 | **0x10200000** | 0xe4000 | 0x102e4000 | `a_sxlrt233` |
| Default.dll | 0x10000000 | **0x10300000** | 0x1d000 | 0x1031d000 | `c_default` |
| Language.dll | 0x10000000 | **0x10400000** (resources only, no code) | 0x31000 | 0x10431000 | `d_language` |
| DX7HRDisplay.dll | 0x10000000 | **0x10500000** | 0x18000 | 0x10518000 | `e_dx7hr` |
| DX7HRTnLDisplay.dll | 0x10000000 | **0x10600000** | 0x1a000 | 0x1061a000 | `f_dx7hrtnl` |
| TCP Protocol.dll | 0x10000000 | **0x10700000** | 0x9000 | 0x10709000 | `g_tcp` |
| Miles Sound System Mixer.dll | 0x10000000 | **0x10800000** | 0x7000 | 0x10807000 | `h_milesmix` |

- The engine keeps its preferred base, so the DB's, Ghidra's and the community symbols' engine addresses are run-time addresses. For sxlrt233 and Default.dll, run-time = DB VA + 0x200000 / + 0x300000.
- On Windows the loader picks bases per process (legion's code census: sxlrt233 at 0x10000000, the engine relocated to 0x012d0000) [measured, `analysis/legion-captures/census`]. **For the M1 oracle** (`docs/reference/rmg.md`), Windows captures are taken with DLL copies rebased to **this same map** on legion, so captured addresses match the translated modules.
- Keys sort in dependency order (sxlrt233 ← engine ← Default.dll, from the DB's imports), which keeps the generated `aux-<key>/` directories in that order. The loader maps every module before it binds any imports, and runs DllMains in dependency order by itself. `tests/test_game_config.py` checks the order, the alignment, the sizes and that nothing overlaps.
- `[game] guest_size = 0x28000000` (640 MB): above the last DLL the heap grows to there. The executable maps its 156 MB `data.ssa` whole, twice (boot.md §3), and is large-address aware; 0x11000000 left 8 MB of growth. It stays below 0x2f000000, where the M1 replay self-test opens a heap above `guest_size`. The kit reserves the whole 4 GB guest space and reports any access past `guest_size` with its guest address.
- All nine modules are configured (Phase 3, P3-A). `Language.dll` has an empty relocation table and no code: the kit's translator and loader move it with nothing to fix.

## 4. Build

```sh
.venv/bin/python tools/build.py --regenerate --target gen          # translate everything, compile the EXE archive
.venv/bin/python tools/build.py --regenerate --target bundles      # translate, then every module as a freestanding bundle
.venv/bin/python tools/build.py --cmake-target ee_m1_harness --cmake-target ee_m1_harness_static
```

`--regenerate` translates the EXE and every auxiliary module into `build/recomp/gen/` (`aux-<key>/` per DLL). `--cmake-target` builds one of this repository's own targets (`mods/CMakeLists.txt` → `tests/m1/harness/CMakeLists.txt`): `ee_m1_harness` (dlopens the three M1 bundles; builds `ee_m1_bundles` first), `ee_m1_harness_static` (links the three DLLs' generated archives). Neither links the EXE's translation.

### Translation [measured]

Function-DB inputs, no allowance flags (`--allow-table-gaps`/`--allow-unmodelled`), kit `feat/game-repo` (the last five rows: kit `feat/boot`, Phase 3):

| Module | Translated | Entry points (alternate) | Failures | Table gaps / undecoded | Unmodelled | Scan entries (data pointers / immediates) | Not in the DB | Time |
|---|---|---|---|---|---|---|---|---|
| Default.dll @0x10300000 | 834 / 834 | 856 (+22) | 0 | 0 / 0 | 0 | 0 / 0 | 0 | 0.7 s |
| sxlrt233.dll @0x10200000 | 3,351 / 3,351 | 3,372 (+21) | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 4.9 s |
| Low-Level Engine.dll @0x10000000 | 5,221 / 5,221 | 5,292 (+71) | 0 | 0 / 0 | 0 | 0 / 0 | 3 | 4.5 s |
| Empire Earth.exe | 36,888 / 36,888 | 40,598 (+3,710) | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 26.8 s, 2.0 GB RSS |
| Language.dll @0x10400000 | 0 / 0 (resources only) | 0 | 0 | 0 / 0 | 0 | 0 / 0 | 0 | 0.0 s |
| DX7HRDisplay.dll @0x10500000 | 677 / 677 | 722 (+45) | 0 | 0 / 0 | 0 | 0 / 0 | 4 | 0.6 s |
| DX7HRTnLDisplay.dll @0x10600000 | 720 / 720 | 765 (+45) | 0 | 0 / 0 | 0 | 0 / 0 | 5 | 0.6 s |
| TCP Protocol.dll @0x10700000 | 180 / 180 | 180 | 0 | 0 / 0 | 0 | 0 / 0 | 0 | 0.1 s |
| Miles Sound System Mixer.dll @0x10800000 | 124 / 124 | 124 | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 0.1 s |

Generated C: EXE 262 MB in `gen/` with the three DLLs at 28 + 29 + 4.4 MB.

"Not in the DB" are bodies exact branch following reached that the DB does not list. Each is a DB gap, reported rather than hidden:
- **Fixed: Default.dll+0x6caa.** The DB used to classify 20 bytes of code as data. Function +0x6c9a is a guarded static initializer (`TEST [flag],1; JNZ; OR [flag],1`, then `MOV [abs],imm32; MOV ECX,..; JMP` at +0x6caa). Ghidra's address-table analyzer had turned both relocated dwords of that `MOV` into pointers. `repair.py` cleared only one, so the instruction stayed blocked, with no new bookmark. `repair.py` now clears every pointer an instruction overlaps and follows the flow past them. The regenerated DB has +0x6c9a as one 37-byte body, and the translation finds nothing outside the DB [measured]. The other DLLs' DBs were not regenerated; the DX7 plugins and the mixer had the same repair pattern.
- **sxlrt233+0xa1055, engine+0x1c47e/+0x5f601/+0x842db, EXE 0x7eab2a**: the DB's "unknown" byte runs (`stats.coverage.*.largest_unknown`), i.e. the bytes after a call Ghidra treats as non-returning. The translator falls through to them because it treats the call as returning. Harmless either way [static].

The translation of a pre-relocated copy of a DLL is byte-identical to the translation of the original at the same base, except for the binary hash in `symbols.json`. The rewritten listings agree with capstone's decoding of the relocated image on every address literal: 0 mismatches in 31,193 (Default.dll) + 207,776 (sxlrt233) instructions [measured].

## 5. Freestanding bundles and `ld64.lld` (PLAN §5.8, ADR-001 gap 7)

`tools/build.py --target bundles` (kit `tools/recomp/bundle.py`; CMake `recomp_add_bundles`) builds each module like this:

```
clang -target arm64-apple-macos13.0 -std=c11 -O2 -ffreestanding -nostdinc \
      -isystem $(clang -print-resource-dir)/include -fno-math-errno -ffp-contract=off \
      -DRECOMP_FREESTANDING=1 -DGUEST_IMAGE_BASE=… -DGUEST_HEAP_BASE=… -DGUEST_SIZE=… \
      -I<module gen dir> -I<gen> -I<kit> -I<kit>/runtime -c chunk_NNN.c
ld64.lld -arch arm64 -platform_version macos 13.0 13.0 -bundle -undefined dynamic_lookup \
      -o build/recomp/bundles/<key>.bundle *.o
```

- **No SDK, no C library.** `RECOMP_FREESTANDING` makes `x86.h`, `intrinsics.h` and `seh.h` include the kit's `runtime/freestanding.h` instead of `<string.h>`, `<math.h>` and `<setjmp.h>`. That header uses compiler builtins where arm64 has an instruction (floor/ceil/trunc/rint/sqrt/fabs: correctly rounded, so identical to libm), and `recomp_libm_*` runtime entry points for library math (fmod, ldexp, scalbn, frexp, ilogb, nextafterf, sin, cos, tan, atan2, log2, log1p, expm1). Those entry points are the one place an exact-x87 math library (ADR-002) replaces the host's.
- **Self-registration.** Every module's `table.c`, the EXE's included, registers a `RecompModule` from a constructor when the bundle is loaded. The host links no generated code: the kit's `runtime/dispatch.cpp` supplies weak `recomp_call`/`recomp_jump`/`recomp_unknown_jump` (the fatal path) that dispatch through the module registry. A linked image table still overrides them.
- **Symbol policy.** After linking, `bundle.py` lists the bundle's undefined symbols (`<key>.undefined`). Anything other than runtime entry points (`_recomp_*`, `_g_mem`, …), `memcpy`/`memmove`/`memset`/`memcmp` and the host's `setjmp` fails the build.
- **Signing.** lld signs arm64 output ad hoc: `codesign -dv` → `flags=0x20002(adhoc,linker-signed)` [measured].
- **Incremental.** An object is rebuilt only when its source, a header it read (depfile) or the flags change.
- `RECOMP_FREESTANDING_CC` / `RECOMP_LD64_LLD` select the tools (default: Homebrew's, then `PATH`).

[measured]

| Bundle | Objects | Host symbols it needs | Size |
|---|---|---|---|
| `c_default.bundle` | 6 | 21, all runtime entry points | 2.9 MB |
| `a_sxlrt233.bundle` | 18 | 19 | 13.7 MB |
| `b_engine.bundle` | 28 | 21 | 20.3 MB |
| `main.bundle` (EXE, self-registering image table) | 186 | 25 (incl. `recomp_libm_sin`/`cos`) | 133 MB |

Time: the EXE's 186 objects compile and link in 95 s wall (1,031 CPU-s at 16 jobs; the spike's stand-in-header build took 85 s); the three DLLs take 21 s together (270 CPU-s), a rebuild with nothing changed 0.1 s, and a rebuilt bundle is byte-identical to the previous one. Every bundle's host symbols are runtime entry points only: no `memcpy` or `setjmp` call survives in these four [measured].

The kit's `tools/recomp/tests/test_freestanding.py` keeps this true without game files: the translator's whole synthetic instruction vocabulary (the Unicorn suite's cases, x87 and SEH forms included) compiles with `-ffreestanding -nostdinc` and needs nothing foreign; a translated image links with lld into a bundle; and a stand-in host `dlopen`s it, sees the table register and calls a translated function.

What remains for "builds without the CLT" (M1 DoD): the runtime and hosts are still compiled with the CLT's clang and SDK. Only the generated code is CLT-free today.

## 6. Tests

```sh
.venv/bin/python tools/test.py                    # tests/test_*.py, then the kit's portable suites
.venv/bin/python tools/test.py --m1 [--regenerate]   # the M1 harness
.venv/bin/python tools/test.py --m1-replay [PACKS...]   # the Mac M1 replay: self-tests, or replay packs
.venv/bin/python tools/test.py --boot [--regenerate]  # the whole game, booted headless to the main menu
.venv/bin/python tools/test.py --native           # any other option: the kit's test.py for this game
```

- `tests/test_game_config.py`: hashes against PLAN §2.4 and the DB, the base map, the dependency order, the DB-driven inputs, and the curated `globals.toml` addresses against the DB's exports at the load bases.
- **The M1 harness** (`tests/m1/run_harness.py`) builds both harnesses, then runs each with `--check --rmv Continental`. They use the kit's own loader and C runtime, on the original DLL files. It requires:
  - each module's translation to register (the bundles: one registration per `dlopen`);
  - each module at its `game.toml` base, relocated where it prefers another;
  - the loader to attach the modules as Windows does. `loader_attach_static` attaches the EXE's static imports (sxlrt233, then the engine), then `loader_module_load` (`LoadLibrary`) attaches Default.dll. Every DllMain must return TRUE, in that order;
  - `PIMGetName` → a `UString` holding "Default", `PIMGetType` → 0x726d6170 ('pamr'), `PIMIsValid` → 1;
  - **`--rmv`**: `EERMPIMSetRandomMapValues`, up to and including its `.rmv` read, filling a synthetic context **identically** in the bundle build and the linked build.
    - It replays the five set-up calls Default.dll+0xc268 makes, then the reader at +0xde77 [static].
    - The context gets a `URandom` at +0x10 and the scripts' `FSFileSpec` at +0x20, built with the engine's constructors [inferred from call sites].
    - The digest covers the values in ctx[0x40,0x2000), leaving out heap pointers, which depend on the allocator.
    - The rest of the function derives the map from fields the EXE fills, and it faults on a synthetic context. The real context comes from the M1 captures.

  **[measured]** Both builds pass:
  - bundles registered with 3,372 / 5,292 / 856 entries; 9,655 / 0 / 1,644 relocations applied;
  - DllMains #1–#3 in order, x87 CW 0x027f;
  - 80 values plus 29 heap pointers; digest `d4f04678` in both builds; ctx+0x260 = 32.0, as the spike read it.

  **Why the spike's digest `bd488b9c` no longer reproduces:** the scripts are CRLF. The spike's stopgap `fgets` was the host's and kept the `\r`, so keywords mis-parsed. The kit's msvcrt strips `\r` in text mode, as Windows does. With correctly terminated lines, the script's random draws reach a zeroed `URandom` (a `#DE` in `URandom::Generate`), and post-processing reads past the arena. The old digest also covered heap addresses.
- `EE_M1_TRACE_IMPORTS=1` prints every import call and result; `EE_M1_DUMP=1` prints the context values.
- **The M1 replay** (`tests/m1/replay/run_replay.py`, `tools/test.py --m1-replay`): `ee_m1_replay` (bundles) and `ee_m1_replay_static` (linked) replay captured `EERMPIM*` calls against the translated modules, byte for byte. Without packs it runs the self-tests: the replay core's, a Mac-side round trip (record, pack, replay on both builds) and two deliberate corruptions. [m1-replay.md](m1-replay.md) has the details; `tools/m1/run_matrix.py` runs the capture matrix.
- **The EH harness** (`tests/eh/run_eh.py`, `tools/test.py --eh`): Empire Earth's own C++ exceptions through the kit's MSVC exception handling (`runtime/msvc_eh.cpp`), on the executable's translation. `ee_eh_harness` dlopens the bundles of sxlrt233, the engine and the EXE (`ee_eh_bundles`), attaches the static imports, and runs WON's message unpacking [static]: `ReadBuffer::ReadBytes` (EXE+0x29ef50) throws `WONAPI::ReadBufferException` when the buffer runs short, from its own destructor-only C++ frame two calls below EXE+0x3944b0, whose `catch (ReadBufferException &)` returns false.
  **[measured]** A 3-byte buffer unpacks with no exception; a 1-byte buffer throws, the catch returns 0, EBP/EBX/ESI/EDI and FS:[0] come back, and the 2 CRT allocations made on the way (the thrower's temporary and the exception's message) are both freed - by the thrower's unwind action and the exception's destructor; 2000 throws in a row leave the CRT heap as it was. With nothing to catch it (`--unhandled`) the process ends in `terminate()` with exit code 3, the log naming the type and its message ("Attempt to read past end of buffer.").
  Translation [measured]: the EXE has 2,395 C++ frames (45 with try blocks) and 6 `_except_handler3` frames (4 with `__except`), so 49 checkpoints; its 47 catch blocks, their 47 continuations, 8,884 unwind-map actions, 4 filters, 4 `__except` and 2 `__finally` blocks are all translated entries (the report's `msvc_eh.missing` is empty); 130 `_CxxThrowException` call sites in 82 functions (the DB's 131/83 counts the import thunk) and 5 `_setjmp3` call sites, taken in place. sxlrt233: 374 C++ frames (none with a try block), 2 SEH frames (1 with `__except`), 1,166 unwind actions, nothing missing. The engine and Default.dll have no EH frames.

- **The boot test** (`tests/boot/run_boot.py`, `tools/test.py --boot`): the whole game, the executable and all eight DLLs translated and linked (`ee_boot`, `app/`), booted headless in strict mode with a fresh user directory to the main menu's message loop and closed with `WM_CLOSE`. [boot.md](boot.md) has the sequence, the threads and their control words, and the temporary stand-ins.

### Temporary pieces
- `tests/m1/harness/crt_pending.cpp`: stand-ins only for the msvcrt functions the kit does not have yet: the x87 control word (`_controlfp`, `_control87`, `__control87_2`; workstream C). They are registered before the kit's tables, so each one the kit gains replaces ours. MSVC exception handling is the kit's (`runtime/msvc_eh.cpp`, workstream E), and its stand-ins are gone.

## 7. Outputs and disk
Everything is ignored: `analysis/kit/` (listings, 214 MB, EXE 163 MB; `tools/setup.py` or `tools/run.py` rewrites it in 15 s), `build/` (translation ~330 MB per build root; CMake tree with SDL3 + FFmpeg; bundles ~37 MB for the DLLs) [measured]. `build/` and `analysis/kit/` can be deleted at any time.
