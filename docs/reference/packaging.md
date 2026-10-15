# Packaging: what the first run needs on a player's Mac

Status: Phase 6 workstream P6-A, 2026-10-15. This is an inventory for the app bundle (PLAN D10, §5.8, Phase 6). The bundle itself is the next step.

Labels: **[measured]** = run on this Mac (M4 Max, 16 cores, macOS 15.5); **[static]** = read from files, binaries or code; **[inferred]** = reasoning, not run.

---

## 1. The first-run pipeline

The app ships only our code. On first run it imports the player's own GOG files, then translates and compiles them locally. No Ghidra, Xcode or Command Line Tools are involved.

| Step | What runs | Needs | Output (all in `~/Library/Application Support/EmpireEarth/`, never in the app) | Time [measured] |
|---|---|---|---|---|
| 1. Import ([firstrun.md](firstrun.md) §1–4) | locate the game (GOG installer, install folder, `Empire Earth` folder, Wine wrapper or bottle); check the nine modules' SHA-256 (`game.toml`) and all 294 files against `db/gog-files.json` (size, MD5); copy into `Game/`; seed the registry from the player's GOG script | Python (stdlib); for the installer, innoextract (822 KB, §3 there) | `Game/Empire Earth/` (413 MB; APFS clones from a wrapper on the same volume take no space), `registry.json`, `game-path.txt` | 0.7 s from the user's Wineskin wrapper (294 files cloned and hashed); the installer path is tested on a synthetic GOG-shaped installer only (firstrun.md §3.4) |
| 2. Inputs | `tools/inputs/export.py` ([build.md](build.md) §2.1) | Python, capstone, pefile, `db/functions/*.json`, `db/inputs.json` | listings + `funcdb.json`, 214 MB, checked against the pinned digests | 15 s |
| 3. Translate | `kit/tools/recomp/translate.py`, once per module | Python, capstone, pefile | generated C, 269 MB | 43 s, one process after another (the EXE alone takes 27 s and 2.0 GB RSS) |
| 4. Compile + link | `kit/tools/recomp/bundle.py` → `clang -ffreestanding -nostdinc`, `ld64.lld -bundle` | clang, ld64.lld, 10 clang headers | objects (271 MB), then nine bundles (169 MB) | 102 s wall, 1,352 CPU-s at 16 jobs |
| 5. Clean up | delete the listings (after translation), C and objects; keep the bundles in `Build/<key>/`, keyed by a digest of the modules, `game.toml`, `globals.toml`, `db/inputs.json`, the translator, the runtime headers, `bundle.py` and the compiler (firstrun.md §5.2) | — | 176 MB steady state (nine bundles + `symbols.json`) | — |

- Steps 2–4 ran end to end with the two components described below: a trimmed python-build-standalone runtime, and clang and lld from the official LLVM 23.1.3 release [measured].
  - The runtime's inputs hash to the pins.
  - Its translation equals the development venv's (`diff -r`, the generated tree).
  - All nine bundles built with no foreign symbols.
  - Case A06 of the M1 replay passes 9/9 on those bundles.
  - The M1 bundles are byte-identical to Homebrew clang 23.1.3's.
- **Total on an M4 Max: about 2.7 minutes, peak disk about 0.93 GB** [measured, sum of the rows].
- **The first-run driver** (`tools/firstrun/firstrun.py`, P6-B2, [firstrun.md](firstrun.md)) runs steps 1–5 with JSON-lines progress and resumes after a stop. Its bundles are byte-identical to `tools/build.py --regenerate --target bundles`'s, and M1 case A06 passes 9/9 on them [measured].
  - It deletes the listings once the translation is done, so its peak under `Build/` is **0.74 GB** [measured].
  - With other agents' builds loading this Mac (load average ~67) it took 250 s; uncontended, the rows above apply.
- **On a base M1** (4 performance + 4 efficiency cores): about 7–9 minutes [inferred: 1,352 CPU-s at roughly 1.5× slower cores over about 6 effective cores, plus the single-threaded translation].
  - The three DLL-sized translations can run beside the EXE's (about 30 s in all [inferred]).
  - On 8 GB Macs, cap the compile at 4 jobs: some EXE chunks are large [inferred].

## 2. Python runtime (translator, exporter, bundle driver)

A Mac without the Command Line Tools has no usable Python: `/usr/bin/python3` is a stub that offers to install the CLT. So the app must bring its own.

**Needed:** CPython ≥ 3.11 (`tomllib`), plus **capstone 5.0.9** and **pefile 2024.8.26** (the kit's pins, `kit/tools/requirements.txt`). Nothing else: `translate.py`, `game_config.py`, `bundle.py` and `tools/inputs/` use only the standard library apart from those two [static]. capstone must be exactly the pinned version, because the input digests in `db/inputs.json` depend on its decoding.

| Option | Size | Notes |
|---|---|---|
| **python-build-standalone 3.12.13, aarch64, trimmed (recommended)** | **39 MB on disk, 763 files; 8.9 MB as `.tar.xz`** [measured] | uv's install of it is 58 MB. Removed: Tcl/Tk and `tkinter`, `idlelib`, `ensurepip`, `pip`, `lib2to3`, `pydoc_data`, `turtledemo`, headers and man pages. Contents: `libpython3.12.dylib` 17 MB, stdlib 13 MB plus `.pyc`, capstone 8.5 MB (`libcapstone.dylib`, 7.3 MB, holds every architecture), pefile 0.3 MB. It runs every tool unchanged, with `-I`. |
| PyInstaller/Nuitka, frozen | not measured; likely 20–30 MB [inferred: the same libpython and capstone, minus unused stdlib] | Three entry points (exporter, translator, bundle driver) become three frozen programs or one dispatcher; the kit's tools call each other with `sys.executable`. More moving parts for about 10 MB. |
| System Python, or the user installs the CLT | 0 MB | It contradicts D10 (no CLT), and the CLT's Python version is not ours to pin. |

**Licences:** CPython is PSF-2.0 (`lib/python3.12/LICENSE.txt` ships with it). python-build-standalone's build scripts are MPL-2.0, and the distribution statically includes third-party libraries (OpenSSL, libffi, zlib, xz, bzip2, SQLite, mpdecimal, …) under their own licences; the about box must carry those notices [static: its documentation; to collect when the bundle is built]. capstone is BSD-3-Clause, pefile MIT.

Optional savings [inferred]:
- a capstone built for x86 only (about 2 MB instead of 7.3);
- the stdlib zipped into `python312.zip`.

## 3. LLVM toolchain (clang + ld64.lld)

**Source:** the official release `LLVM-23.1.3-macOS-ARM64.tar.zst` (llvm-project tag `llvmorg-23.1.3`, 2026-10-06). It is an 871 MB download that unpacks to 8.2 GB in 10,782 files [measured]. Each asset has a `.sig` and a sigstore `.jsonl` attestation for verifying it.

| Piece | As released | Stripped (`strip -x`) | `xz -6` | Needed? |
|---|---|---|---|---|
| `bin/clang-23` (shipped as `clang`) | 196.0 MB | **171.8 MB** | 37.7 MB | yes |
| `bin/lld` (shipped as `ld64.lld`) | 146.2 MB | **130.9 MB** | 27.2 MB | yes |
| `lib/clang/23/include` | 16 MB | **10 headers, about 84 KB on disk**: `stddef.h`, its seven `__stddef_*.h`, `stdint.h`, `limits.h` (the depfile of a freestanding compile) | — | those 10 |
| `lib/clang/23/lib/darwin/libclang_rt.osx.a` | 0.23 MB | — | — | no: `bundle.py` links with `-undefined dynamic_lookup` and no compiler-rt |
| `bin/llvm-nm` | 47.6 MB | 45.7 MB | — | no, once kit `feat/importer` is merged: `bundle.py` then reads the Mach-O symbol table itself (`macho_undefined`, equal to `llvm-nm -u` on all nine bundles [measured]). Until then it is needed for `bundle.py`'s undefined-symbol check: on a player's Mac `/usr/bin/nm` is another CLT stub |
| **Total shipped** | | **about 303 MB** | **about 65 MB** | |

All figures [measured].
- **Self-contained.** Both binaries link only `libSystem`, `libz` and `libc++` (lld also `libxml2`), which every macOS has.
- **Signature.** As released they are ad-hoc and linker-signed. `strip` invalidates that, so re-sign: `codesign -s - -f` [measured: both run afterwards].
- **Resource directory.** Keep `bin/` and `lib/clang/23/include/` in their relative layout: `clang -print-resource-dir` resolves `bin/../lib/clang/23`, and `bundle.py` passes that directory as `-isystem`.
- **Against PLAN §5.8's estimate.** The PLAN estimated 100–150 MB. The official binaries are about twice that, because each statically links all of LLVM for every target. To get back to that range [inferred]:
  - build LLVM ourselves with `LLVM_TARGETS_TO_BUILD=AArch64` and `LLVM_TOOL_LLVM_DRIVER_BUILD=ON`, giving one multicall `llvm` binary that serves as both clang and ld64.lld (about 100–130 MB); or
  - ship the official pair compressed (65 MB) and unpack it on first run.
- **Licence:** Apache License 2.0 with LLVM Exceptions (clang, lld, LLVM).
  - Ship the licence text in the app. The release archive does not contain it: take `llvm/LICENSE.TXT`, `clang/LICENSE.TXT` and `lld/LICENSE.TXT` at the tag.
  - The exception means the code clang emits into the player's bundles carries no attribution requirement.
- **Apple SDK.** None is involved or redistributed (§5.8): `-nostdinc`, no `libSystem` stubs, `-undefined dynamic_lookup`.

## 4. Build time and disk, summarised

| | M4 Max [measured] | base M1 [inferred] |
|---|---|---|
| first run, steps 2–4 | about 2.7 min | about 7–9 min |
| peak disk | about 0.93 GB (listings 214 MB + C 269 MB + objects 271 MB + bundles 169 MB) | same |
| steady-state cache | 169 MB of bundles | same |
| peak memory | 2.0 GB, the EXE's translation (build.md §4) | cap the compile jobs on 8 GB Macs |
| app payload for the pipeline | Python 39 MB + LLVM 303 MB + DB 14 MB + tools about 1 MB, so **about 357 MB** (about 75 MB compressed) | |

A re-run after an app update re-translates only when the translator or the DB changed. `bundle.py` already rebuilds only objects whose source, headers or flags changed [static].

## 5. Code signing

- **Bundles the player builds:** lld signs arm64 output ad hoc, `flags=0x20002(adhoc,linker-signed)` [measured on `main.bundle`]. Files the app creates are not quarantined, so Gatekeeper does not check them [inferred: quarantine marks downloads].
- **The app, ad hoc (the default, D10):** sign every Mach-O inside it ad hoc:
  - the host;
  - `libpython3.12.dylib`, `libcapstone.dylib` and the stdlib's extension modules;
  - `clang`, `ld64.lld`;
  - FFmpeg's dylibs.

  **Without the hardened runtime, library validation is off**, so the host can `dlopen` the locally built bundles; the M1, EH and replay harnesses do this today. Players open the app once with *Open Anyway* in Privacy & Security.
- **App Translocation:** an app started from Downloads runs from a read-only random path. Nothing may be written inside the app: the cache, user data and registry all live in `~/Library/Application Support/EmpireEarth/` (already the case for the user data).
- **If we get a Developer ID** (notarization requires the hardened runtime):
  - The host needs `com.apple.security.cs.disable-library-validation`, so it can load bundles that our team did not sign.
  - `clang`, `ld64.lld` and the Python executable must be signed with the Developer ID and the hardened runtime too, as nested code.
  - Python's ctypes (capstone's binding) may need `com.apple.security.cs.allow-unsigned-executable-memory` under the hardened runtime. This is untested and needed only on this path [inferred].

## 6. Nothing game-derived ships

| In the app | Contents | Game-derived? |
|---|---|---|
| host and runtime (kit `runtime/`, the SDL host), SDL3, FFmpeg | our code and third-party code | **no, as long as no translation is linked in** (see below) |
| `kit/tools/recomp/*.py`, `kit/tools/game_config.py`, `tools/inputs/*.py` | translator, bundle driver, input exporter | no |
| `db/functions/*.json` (14 MB) | addresses, sizes, chunk ranges, kinds, tags, flags and counts, plus names from export and import tables, RTTI, Function ID and the community lists. No bytes, no code, no strings ([function-db.md](function-db.md) §3) | no |
| `db/inputs.json` | SHA-256 of the inputs the first run must reproduce | no |
| `game.toml`, `globals.toml` | hashes, base map, addresses, settings | no |
| Python runtime, LLVM, SoundFont | third-party code and data, with their licences | no |

Made only on the player's Mac, never shipped:
- the listings (`analysis/kit/`);
- the generated C, objects and bundles;
- `symbols.json`;
- the registry with the player's own CD key (`tools/boot/seed_user.py` reads it from the player's GOG install script).

**Two things to change when the app bundle is built:**
1. **Today's development app must never be distributed.** `build/EmpireEarth.app` (`tools/run.py`) links the translated EXE and DLLs statically: `Contents/MacOS/EmpireEarth` is 193 MB, and `Resources/symbols.json` comes from the translation.
   - The players' app must be a host with no generated code that `dlopen`s the cached bundles. The runtime's weak `recomp_call`/`recomp_jump` already dispatch through the module registry the bundles fill (build.md §5).
   - `symbols.json` has to be written at first run.
2. **The first run needs only part of the DB:**
   - the module header;
   - per function: `va`, `size`, `chunks`, `kind`, `tag`, `name`, the `xref` keys, `alt_vas`, `switch` and `insns`;
   - `data_in_code`.

   A slim export could leave out the RTTI classes, the community provenance and the x87 statistics. This is not required: none of them is game content, but the smaller file is the more obviously clean one.

A packaging check should assert that the app contains:
- no PE image;
- none of the generated `fn_*` symbols;
- no file from `analysis/` or `build/recomp/`.

The kit's `check_repo.py` already does the same for the repositories.
