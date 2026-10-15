# Packaging: what the first run needs on a player's Mac

Status: Phase 6, 2026-10-15: P6-A's inventory, with the importer (P6-B2, §1) and the bundled toolchain (P6-B1, §2, §3, §7) built and measured. The app bundle itself (P6-B3) is next.

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

- **Steps 2–4 ran end to end with only the bundled toolchain** (§2, §3; [build.md](build.md) §5.1) [measured, P6-B1]: `env -i`, `nice -n 15`, in a sandbox that hides Xcode, the Command Line Tools and Homebrew.
  - The inputs hash to the pins; the listings and the generated C are `diff -r`-identical to the development venv's.
  - All nine bundles, and `symbols.json`, are byte-identical to Homebrew clang and lld 23.1.3's from the same C.
  - Through `firstrun.py build`: 136 s (inputs 13 s, translation 29 s, compile + link 92 s at 12 jobs) under a load average of 15–40 from other agents.
  - M1 replay case A06 passes 9/9 on these bundles (`ee_m1_replay --bundles`).
- **Total on an M4 Max: about 2.7 minutes, peak disk about 0.93 GB** [measured, sum of the rows].
- **The first-run driver** (`tools/firstrun/firstrun.py`, P6-B2, [firstrun.md](firstrun.md)) runs steps 1–5 with JSON-lines progress and resumes after a stop. Its bundles are byte-identical to `tools/build.py --regenerate --target bundles`'s, and M1 case A06 passes 9/9 on them [measured].
  - It deletes the listings once the translation is done, so its peak under `Build/` is **0.74 GB** [measured].
  - With other agents' builds loading this Mac (load average ~67) it took 250 s; uncontended, the rows above apply.
- **On a base M1** (4 performance + 4 efficiency cores): about 7–9 minutes [inferred: 1,352 CPU-s at roughly 1.5× slower cores over about 6 effective cores, plus the single-threaded translation].
  - The three DLL-sized translations can run beside the EXE's (about 30 s in all [inferred]).
  - On 8 GB Macs, cap the compile at 4 jobs: some EXE chunks are large [inferred].

## 2. Python runtime (translator, exporter, bundle driver)

A Mac without the Command Line Tools has no usable Python: `/usr/bin/python3` is a stub that offers to install the CLT. So the app brings its own, staged by the kit's `tools/toolchain/python_runtime.py` (P6-B1; `tools/stage_toolchain.py` here, [build.md](build.md) "The bundled toolchain").

**Needed:** CPython ≥ 3.11 (`tomllib`), plus **capstone 5.0.9** and **pefile 2024.8.26** (the kit's pins, `kit/tools/requirements.txt`). Nothing else: `translate.py`, `game_config.py`, `bundle.py`, `tools/inputs/` and `tools/firstrun/` use only the standard library apart from those two [static]. capstone must be exactly the pinned version, because the input digests in `db/inputs.json` depend on its decoding. (Its `capstone.__version__` says 5.0.7, an upstream quirk; the distribution's metadata says 5.0.9, and that is what the staging self-test and `firstrun.py` check.)

**What ships** [measured]:

| | |
|---|---|
| Base | python-build-standalone release 20260303, CPython 3.12.13, `aarch64-apple-darwin-install_only`: the same build uv installed for the development venv and P6-A's pins. SHA-256 pinned (kit `tools/toolchain/pins.json`); its GitHub attestation checks (`release.yml`). |
| Wheels | capstone 5.0.9 (`py3-none-macosx_11_0_arm64`) and pefile 2024.8.26 from PyPI, pinned by SHA-256, unpacked into `site-packages` without pip |
| On disk | **44.1 MB, 1,139 files; 10.1 MB as `.tar.xz`**: `libpython3.12.dylib` 16.2 MB (18.5 MB before `strip -x`), the standard library's `.py` 10.0 MB and `.pyc` 10.5 MB, `libcapstone.dylib` 7.2 MB (every architecture) |
| Removed | Tcl/Tk and `tkinter`, `idlelib`, `ensurepip`, `pip`, `lib2to3`, `pydoc_data`, `turtle`/`turtledemo`, headers, man pages, `config-3.12-darwin` (the static libpython), `_ctypes_test`, capstone's C headers |
| Bytecode | every `.py` precompiled as an **unchecked-hash** `.pyc` with relative file names: valid whatever the files' times after a copy or unzip, and the same bytes wherever it is staged (two stagings into different directories give the same `MANIFEST.json`) |
| Signing | every Mach-O `strip -x`, then `codesign -s - -f`; `codesign --verify --strict` passes on all |

- **Run it as `python/bin/python3 -I -B`.**
  - `-I` ignores `PYTHON*` variables, the user's site-packages and the script's directory; the kit's and this repository's tools add their own directories to `sys.path`.
  - `-B` writes no `.pyc` next to anything imported. `PYTHONDONTWRITEBYTECODE` cannot do it, because `-I` ignores it.
  - A `site-packages/sitecustomize.py` sets `sys.dont_write_bytecode` as well, so a tool that starts it with `-I` alone also writes nothing: `firstrun.py` starts its tools that way [measured: an imported module leaves no `__pycache__`].
- **Relocatable and read-only safe** [measured]: a read-only copy under a path with spaces runs the tools and builds a bundle; a full first run with the staged runtime left the staged tree unchanged (every file's SHA-256 and time as staged).
- **Why 3.12.13 and not 3.12.15** (release 20261013): 3.12.15 was a day old when this was pinned, and 3.12.13 is the build the development venv and the P6-A pins were made with. A bump is a pin change and a rerun of §1's proof.
- Not done: a capstone built for x86 only (about 2 MB instead of 7.2) or the standard library zipped. Frozen programs (PyInstaller/Nuitka) were rejected in P6-A: three entry points that call each other through `sys.executable`, for perhaps 10 MB.

## 3. LLVM toolchain (clang + ld64.lld)

**Built from source** by the kit's `tools/toolchain/llvm.py` and `pgo.py` (P6-B1): `llvm-project-23.1.3.src.tar.xz`, the release Homebrew's llvm and lld 23.1.3 are built from. SHA-256 pinned; its sigstore attestation was verified (`gh attestation verify … --bundle ….jsonl --repo llvm/llvm-project`: signed by `release-sources.yml` at the tag).

| | official release, stripped (P6-A) | built: ThinLTO | **built: ThinLTO + PGO (shipped)** |
|---|---|---|---|
| clang + ld64.lld | 171.8 + 130.9 = 302.7 MB, two binaries | 93.7 MB, one | **76.1 MB, one** (96.3 MB before `strip -x`) |
| as `.tar.xz` | 65 MB | 23.7 MB | **21.1 MB** |
| download | 871 MB | 179 MB of source | the same |
| nine modules compiled + linked, 12 jobs, against Homebrew's 108.6 / 114.5 s (1,253 / 1,304 CPU-s) | — | 129.6 / 132.3 s, **1.19–1.20×** | **108.4 / 110.9 s (1,255 / 1,287 CPU-s), 0.98–1.00×** |
| the nine bundles against Homebrew's | the M1 bundles identical | all nine byte-identical | **all nine byte-identical** |

[measured; two runs of each, alternated, on this Mac while other agents loaded it (load average 15–45)]

- **Configuration:** `LLVM_ENABLE_PROJECTS=clang;lld`, `LLVM_TARGETS_TO_BUILD=AArch64`, Release, no assertions, no plugins, no static analyzer. `LLVM_TOOL_LLVM_DRIVER_BUILD=ON` with `LLVM_DISTRIBUTION_COMPONENTS=clang;lld;…`, so the multicall `llvm` binary holds clang and lld and nothing else. No zstd, libxml2, libedit, libpfm, ffi or curl; `-dead_strip_dylibs`. It links only `libSystem`, `libc++` and `libz`, `minos 13.0`.
- **PGO** is what makes it as fast as Homebrew's (which is PGO + ThinLTO too, but split into dylibs). The profile comes from an instrumented build compiling, with `bundle.py`'s own flags, a corpus made from the kit's synthetic instruction vocabulary (the Unicorn suite's cases), shaped like a translation: 32 files of about 1 MB, functions sized like real ones. **No game code is involved**, and anyone can reproduce it.
- **Layout:** `llvm/bin/llvm`, `llvm/bin/clang -> llvm`, `llvm/bin/ld64.lld -> llvm` (relative symlinks: the driver dispatches on the name, and clang's resource directory is `bin/../lib/clang/23` from the binary's real path), `llvm/lib/clang/23/include/` with the 10 headers a freestanding compile reads (`stddef.h`, its seven `__stddef_*.h`, `stdint.h`, `limits.h`; 41 KB, checked at staging by a probe compile of the runtime headers). No `llvm-nm`: `bundle.py` reads the bundle's symbol table itself (kit `de82255`, P6-B2).
- **Why the bundles are identical while the objects are not:** every object differs from Homebrew's in two bytes, the SDK version in `LC_BUILD_VERSION`: Homebrew's clang finds the Command Line Tools' SDK by itself (`-isysroot`, `-target-sdk-version=15.x`, a read of its `SDKSettings.json`), the staged clang records none. lld writes the bundle's build version from `-platform_version macos 13.0 13.0`, so the bundles do not change [measured]. Nothing else differs.
- **No SDK at all** (B2 found Homebrew's clang reading the CLT's `SDKSettings.json`): the staged clang has no default sysroot and no configuration file, and `bundle.py` now passes `--no-default-config` and keeps `SDKROOT`, `CPATH` and the other variables clang reads out of its environment (`TOOL_ENV_IGNORED`). A full first run inside a `sandbox-exec` profile that hides Xcode, the Command Line Tools and Homebrew, with `SDKROOT` pointing at the hidden CLT SDK, gave the same nine bundles and depfiles that name no SDK file [measured, §1].
- **Building it** (the packager's Mac, with Xcode or the CLT, CMake and Ninja): about 19 min at 12 jobs and `nice 15` (instrumented build 7 min, training 1 min, final build 10 min), 2.6 GB of disk at the peak, 0.25 GB of downloads kept. A second final build from the same profile into another directory gave the same binary; the training itself varies slightly between runs (build.md §5.1) [measured].
- **Fallback, not needed:** the official `LLVM-23.1.3-macOS-ARM64.tar.zst`, stripped and re-signed, is 303 MB on disk; it was the plan if the build had failed.

## 4. Build time and disk, summarised

| | M4 Max [measured] | base M1 [inferred] |
|---|---|---|
| first run, steps 2–4 | about 2.7 min | about 7–9 min |
| peak disk | about 0.93 GB (listings 214 MB + C 269 MB + objects 271 MB + bundles 169 MB) | same |
| steady-state cache | 169 MB of bundles | same |
| peak memory | 2.0 GB, the EXE's translation (build.md §4) | cap the compile jobs on 8 GB Macs |
| app payload for the pipeline | the staged toolchain 120.6 MB (Python 44.1 MB + LLVM 76.2 MB + notices) + DB 14 MB + innoextract 0.8 MB + tools about 1 MB, so **about 137 MB**; the toolchain is 31.4 MB as `.tar.xz` [measured] | |

A re-run after an app update re-translates only when the translator or the DB changed. `bundle.py` already rebuilds only objects whose source, headers or flags changed [static].

## 5. Code signing

- **Bundles the player builds:** lld signs arm64 output ad hoc, `flags=0x20002(adhoc,linker-signed)` [measured on `main.bundle`]. Files the app creates are not quarantined, so Gatekeeper does not check them [inferred: quarantine marks downloads].
- **The app, ad hoc (the default, D10):** sign every Mach-O inside it ad hoc:
  - the host;
  - `libpython3.12.dylib`, `libcapstone.dylib` and the stdlib's extension modules;
  - `clang`, `ld64.lld`;
  - FFmpeg's dylibs.

  The staged toolchain arrives signed: `stage.py` signs its six Mach-O files ad hoc (`python3.12`, `libpython3.12.dylib`, two `lib-dynload` modules, `libcapstone.dylib`, and `llvm`, which takes the identifier `org.llvm.clang` from its embedded `Info.plist`) and checks them with `codesign --verify --strict`. `clang` and `ld64.lld` are symlinks to `llvm` inside the toolchain directory, which an app's seal records as symlinks. Copy the directory into the app with symlinks kept (`ditto`, `cp -R`), then sign the app as usual, inside out [static: not yet tried inside an app; P6-B3].

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
| the toolchain's PGO profile (compiled into `llvm`) | branch counts from compiling the kit's synthetic instruction vocabulary | no: no game code was compiled to make it |
| `THIRD_PARTY_NOTICES.txt` | the licences (§7) | no |

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

## 7. Licence notices

`stage.py` writes `THIRD_PARTY_NOTICES.txt` (kit `tools/toolchain/notices.py`, 0.35 MB): an index, then each component's licence, source, place in the app and the **complete** texts as its authors distribute them, read from the pinned sources (never paraphrased). Components [static]:

- **Toolchain:** LLVM 23.1.3 (clang, lld, LLVM, the libc utilities LLVM compiles in: Apache-2.0 WITH LLVM-exception; `LICENSE.TXT` of llvm, clang, lld, libc at the tag); the third-party code inside LLVM's Support library (regex BSD-3, strlcpy ISC, ConvertUTF and the Unicode name tables Unicode-DFS, MD5 public domain, xxhash BSD-2, BLAKE3 CC0/Apache-2.0: their own file headers); CPython 3.12.13 (PSF-2.0: its `LICENSE.txt` and `Doc/license.rst`); python-build-standalone (MPL-2.0) and what it links into libpython: OpenSSL 3, libffi, bzip2, liblzma, SQLite, mpdecimal, expat, libuuid (the build's `PYTHON.json` lists them; ncurses, libedit and zlib are macOS's own, Tcl/Tk is removed); capstone 5.0.9 (BSD-3, plus `LICENSE_LLVM.TXT` from its source archive for its LLVM-derived tables); pefile (MIT).
- **The macOS host** (what `recomp_app` links, from the kit's CMake): the kit itself (MIT, with NOTICE), SDL 3.4.16 (Zlib), FFmpeg 7.1.1 (LGPL-2.1+, dylibs), glslang 16.6.0, volk, Vulkan-Headers, Lua 5.4.6, miniz 3.0.2, SoftFloat 3e, stb_truetype, TinySoundFont, GothicKit dmusic, minimp3, Open Sans, GeneralUser GS 2.0.3. The kit's NOTICE now names glslang, which it had left out; `test_toolchain.py` keeps the two in step and checks the SDL and glslang tags against `Dependencies.cmake`.
- **This repository's:** innoextract and the Boost, XZ and bzip2 code in it, from the licence files `tools/firstrun/innoextract/build.sh` writes (`tools/stage_toolchain.py --innoextract DIR`).

**To decide before the app is shared** [static]:
- **This repository has no licence file.** The app ships its code (`tools/firstrun/`, `tools/inputs/`, `db/`, `game.toml`): it needs one, or an explicit statement, before the app is given to anyone.
- **FFmpeg (LGPL-2.1):** the notice names the exact upstream source and SHA-256. The LGPL asks that the source accompany the binary or be offered in writing for three years; the safe course is to put `ffmpeg-7.1.1.tar.xz` next to the app wherever it is published. The dylibs stay replaceable; a replaced one needs the app re-signed ad hoc (the notice says how).
- **GeneralUser GS** has its own licence (it permits use in software projects; its text ships in full).
- python-build-standalone's MPL-2.0 covers its build scripts and the patches it applies to CPython; the notice carries the MPL text and the release URL, which is what the MPL asks of a binary.
- glslang's licence file also carries GPL-3.0 with Bison's exception, for a Bison grammar glslang no longer uses: nothing GPL is built in.
