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
| Ghidra 12.1.4 + the func-db environment | setup only: exporting the translation inputs | [function-db.md §9](function-db.md) |
| Your GOG installation | everything game-backed | linked by `tools/setup.py`, never copied into git |

Every wrapper takes `--kit DIR` (or `RECOMP_KIT_DIR=DIR`) to use another kit checkout than the `kit/` submodule, e.g. a worktree of the fork during kit work. Nothing else changes: outputs still go to this repository's ignored `build/`.

## 2. Setup: translation inputs from the function DB

```sh
git submodule update --init kit
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth"
```

1. The kit's `setup.py --link-only` checks `Empire Earth.exe` against `game.toml` and links the installation at `original/gog/Empire Earth` (ignored).
2. Every module in `game.toml` is checked against its SHA-256 (PLAN §2.4).
3. For each module, `tools/ghidra/ghidra_run.py kit` exports, from the func-db Ghidra project, exactly the DB's non-DRM functions into `analysis/kit/<module>/` (ignored): `functions.tsv` (with the DB's names), `functions/<va>.asm`, and `funcdb.json`.

`funcdb.json` (schema `recomp-funcdb/1`) is what `game.toml` names as `function_db`: the kit's translator then classifies code by the DB's code ranges instead of section flags, seeds every entry point and alternate entry (`alt_vas` in the DB), keeps data-in-code out of every scan, never translates the DRM, and runs **no** data-pointer scan ([function-db.md §7.4](function-db.md)). The export needs the func-db Ghidra projects: `tools/ghidra/regen.py` builds them (about 20 minutes), or point `FUNCDB_WORK` at an existing `analysis/func-db`. `--link-only` skips step 3.

## 3. The base map

Every EE DLL prefers 0x10000000, so each is translated for, and loaded at, its own base. A base other than the preferred one is reached through the DLL's base relocations: the translator applies them to the image it reads and moves the Ghidra listings (exported at the preferred base) by the same delta; the kit's multi-module loader (gap 1, workstream B) must apply the same relocations when it maps the file. All bases are 64 KB aligned, above the kit's shim trampolines (0x0ff00000–0x10000000) [static: SizeOfImage from the DB].

| Module | Preferred | **Load base** | SizeOfImage | End | game.toml key |
|---|---|---|---|---|---|
| Empire Earth.exe | 0x00400000 | **0x00400000** (no relocations) | 0x682000 | 0x00a82000 | main image |
| Low-Level Engine.dll | 0x10000000 | **0x10000000** | 0x130000 | 0x10130000 | `b_engine` |
| sxlrt233.dll | 0x10000000 | **0x10200000** | 0xe4000 | 0x102e4000 | `a_sxlrt233` |
| Default.dll | 0x10000000 | **0x10300000** | 0x1d000 | 0x1031d000 | `c_default` |
| Language.dll | 0x10000000 | 0x10400000 (reserved) | 0x31000 | | |
| DX7HRDisplay.dll | 0x10000000 | 0x10500000 (reserved) | 0x18000 | | |
| DX7HRTnLDisplay.dll | 0x10000000 | 0x10600000 (reserved) | 0x1a000 | | |
| TCP Protocol.dll | 0x10000000 | 0x10700000 (reserved) | 0x9000 | | |
| Miles Sound System Mixer.dll | 0x10000000 | 0x10800000 (reserved) | 0x7000 | | |

- The engine keeps its preferred base, so the DB's, Ghidra's and the community symbols' engine addresses are run-time addresses. For sxlrt233 and Default.dll, run-time = DB VA + 0x200000 / + 0x300000.
- On Windows the bases differ per process (legion's code census: sxlrt233 at 0x10000000, the engine relocated to 0x012d0000) [measured, `analysis/legion-captures/census`]. Byte-identical results do not depend on bases; captured pointers into a DLL must be translated by module + RVA [inferred].
- Keys sort in dependency order (sxlrt233 ← engine ← Default.dll, from the DB's imports): today's kit loader maps auxiliary modules in key order and binds each one's imports when it maps it. `tests/test_game_config.py` checks the order, the alignment, the sizes and that nothing overlaps.
- `[game] guest_size = 0x11000000` covers the reserved slots until workstream B's 4 GB arena.

## 4. Build

```sh
.venv/bin/python tools/build.py --regenerate --target gen          # translate everything, compile the EXE archive
.venv/bin/python tools/build.py --regenerate --target bundles      # translate, then every module as a freestanding bundle
.venv/bin/python tools/build.py --cmake-target ee_m1_harness --cmake-target ee_m1_harness_static
```

`--regenerate` translates the EXE and every auxiliary module into `build/recomp/gen/` (`aux-<key>/` per DLL). `--cmake-target` builds one of this repository's own targets (`mods/CMakeLists.txt` → `tests/m1/harness/CMakeLists.txt`): `ee_m1_harness` (dlopens the three M1 bundles; builds `ee_m1_bundles` first), `ee_m1_harness_static` (links the three DLLs' generated archives). Neither links the EXE's translation.

### Translation [measured]

Function-DB inputs, no allowance flags (`--allow-table-gaps`/`--allow-unmodelled`), kit `feat/game-repo`:

| Module | Translated | Entry points (alternate) | Failures | Table gaps / undecoded | Unmodelled | Scan entries (data pointers / immediates) | Not in the DB | Time |
|---|---|---|---|---|---|---|---|---|
| Default.dll @0x10300000 | 835 / 835 | 857 (+22) | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 0.8 s |
| sxlrt233.dll @0x10200000 | 3,351 / 3,351 | 3,372 (+21) | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 4.9 s |
| Low-Level Engine.dll @0x10000000 | 5,221 / 5,221 | 5,292 (+71) | 0 | 0 / 0 | 0 | 0 / 0 | 3 | 4.5 s |
| Empire Earth.exe | 36,888 / 36,888 | 40,598 (+3,710) | 0 | 0 / 0 | 0 | 0 / 0 | 1 | 26.8 s, 2.0 GB RSS |

Generated C: EXE 262 MB in `gen/` with the three DLLs at 28 + 29 + 4.4 MB.

"Not in the DB" are bodies exact branch following reached that the DB does not list. Each is a DB gap, reported rather than hidden:
- **Default.dll+0x6caa** (20 bytes): code that the DB classifies as data (`data_in_code` 0x6caa/0x6cb0/0x6cb4). Function +0x6c9a is a guarded static initializer: `TEST [flag],1; JNZ; OR [flag],1`, then `MOV [..],imm32; MOV ECX,..; JMP` at +0x6caa, whose relocated immediate Ghidra had made a pointer. Its tail is reachable and needed. Fix in `repair.py` (the "relocation pointer inside an undecoded `MOV [abs],imm32`" case) [static].
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
.venv/bin/python tools/test.py --m1 [--regenerate] [--prerebased]   # the M1 harness
.venv/bin/python tools/test.py --native           # any other option: the kit's test.py for this game
```

- `tests/test_game_config.py`: hashes against PLAN §2.4 and the DB, the base map, the dependency order, the DB-driven inputs, and the curated `globals.toml` addresses against the DB's exports at the load bases.
- **The M1 harness** (`tests/m1/run_harness.py`) builds both harnesses, then runs each with `--check --rmv Continental`. It requires:
  - each module's translation to register and its DllMain to return TRUE, in Windows' order: sxlrt233, then the engine, then Default.dll;
  - `PIMGetName` → a `UString` holding "Default", `PIMGetType` → 0x726d6170 ('pamr'), `PIMIsValid` → 1;
  - `EERMPIMSetRandomMapValues` on Continental.rmv to fill the (synthetic) context **identically** in the bundle build and the linked build.

  [measured] Both pass: 3 bundles registered (3,372 / 5,292 / 857 entries); 5 script files read; 74 non-zero context dwords; context digest `bd488b9c` in both builds.

### Temporary pieces (delete when the named kit work is on `ee`)
- `--prerebased` (`tools/prerebase.py`, `tests/test_prerebase.py`): today's kit loader refuses a DLL whose PE ImageBase is not its configured base. This stages relocated copies (and a copy of the EXE, since the loader looks for DLLs beside its real path) in `build/prerebased/install/`, plus a derived game directory `build/prerebased/` whose `game.toml` names them. It is removed when **workstream B's** relocating loader lands; the harness then runs from `game.toml` directly.
- `tests/m1/harness/crt_stopgap.cpp`: the spike's ~50 msvcrt shims. They are registered before the kit's tables, so each one the kit implements replaces ours. It is removed when **workstream D's** msvcrt lands. `EE_M1_NO_CRT_STOPGAP=1` runs without it.

## 7. Outputs and disk
Everything is ignored: `analysis/kit/` (listings, 208 MB, EXE 163 MB), `build/` (translation ~330 MB per build root; CMake tree with SDL3 + FFmpeg; bundles ~37 MB for the DLLs) [measured]. `build/` can be deleted at any time; `analysis/kit/` is re-exported by `tools/setup.py`.
