# First run: import, verify, translate, compile

Status: Phase 6, workstream P6-B2 (importer), 2026-10-15. This is the driver between "the player points the app at their GOG files" and "the bundles are cached and the game can start" (PLAN D10, §5.8; [packaging.md](packaging.md) §1). P6-B1 bundles the toolchain (Python with capstone and pefile, clang, ld64.lld). **P6-B3 built the app and its first-run window on the contract in §6** (§6.1 has the app's side as built; [packaging.md](packaging.md) §8 the app).

Labels: **[measured]** = run on this Mac (M4 Max, 16 cores, macOS 15.5), with other agents' builds loading the CPU (load average 40–70); **[static]** = read from files or code; **[inferred]** = reasoning, not run.

```sh
python -I tools/firstrun/firstrun.py probe                              # where is the game? (no PATH: the usual places)
python -I tools/firstrun/firstrun.py import "/Applications/Empire Earth.app"   # or the installer, or a folder
python -I tools/firstrun/firstrun.py build                              # inputs -> translate -> bundles
python -I tools/firstrun/firstrun.py status                             # ready / needs-build / needs-import
```

Every command takes `--home DIR` (default `$EE_HOME`, else `~/Library/Application Support/EmpireEarth`). Use a scratch home for tests: the developer profile lives in the default one.

---

## 1. Sources

`tools/firstrun/sources.py` turns a path into one or more sources. It only reads; a Wine wrapper the player still uses is never changed.

| Kind | What the player picks | How it is found |
|---|---|---|
| `installer` | GOG's offline installer `setup_empire_earth_gold_*.exe`, with its `*-1.bin` (and `-2.bin` …) parts in the same folder; or one of the `.bin` parts; or the folder holding them (Downloads) | an `MZ` file that is not `Empire Earth.exe`; parts are `<stem>-N.bin` beside it. Extracted with innoextract (§3) |
| `gold` | the installed `Empire Earth Gold` folder, or its `Empire Earth` subfolder | `Empire Earth.exe` in the folder or one level down; the parent counts as the GOG folder when it holds `goggame-1207658777.*` or `goglog.ini` |
| `game` | a bare `Empire Earth` folder (USB stick, network share, copied from a Windows PC) | as above, with no GOG files beside it; a bounded search (3 levels) also finds it below a chosen folder |
| `wrapper` | a Wineskin, Sikarugir, Kegworks or Porting Kit `.app`; a CrossOver or Whisky bottle; a Wine prefix | `drive_c` at `Contents/SharedSupport/prefix/drive_c`, `Contents/drive_c`, `Contents/Resources/drive_c`, `Contents/SharedSupport/drive_c` or `drive_c`; then a breadth-first search (5 levels, skipping `windows/`, `ProgramData/`, hidden folders) for `Empire Earth.exe`, case-insensitively. The prefix's `system.reg` is a CD key source (§4.3) |

`probe` with no path looks in the usual places [static]: `/Applications/*.app` and `~/Applications/*.app` with a `drive_c`, CrossOver and Whisky bottles, `~/.wine`, `~/Games/Heroic`, and installers in `~/Downloads` and `~/Desktop`. On this Mac it finds the user's `/Applications/Empire Earth.app` (usable) and two NeoEE installs, which it rejects as NeoEE [measured].

The user's wrapper is a real example of the `wrapper` kind: `/Applications/Empire Earth.app/Contents/SharedSupport/prefix/drive_c/GOG Games/Empire Earth Gold/`, with The Art of Conquest beside the base game (noted and ignored).

## 2. Verification

**Decision: check everything, cheaply, against a manifest the app ships.** Three levels, cheapest first (`tools/firstrun/verify.py`):

1. **The nine modules' SHA-256** (`game.toml`): the translation is made for exactly these bytes, so anything else stops the import. 10 MB to hash.
2. **Every file present at its size** (`db/gog-files.json`, a stat each): a partial copy is caught before anything is copied.
3. **Every file's MD5**, computed while the file is copied (or cloned, then read once): the copy that is kept is the copy that was checked.

`db/gog-files.json` lists the base game as GOG installs it: 294 files, 413,292,860 bytes, and 5 empty folders the installer creates (`Data/Saved Games`, `Data/Scenarios`, `Data/Sounds`, `Users/default/Civilizations`, `_wonHTTPCache`). It holds paths, sizes and MD5s only, like `game.toml`'s hashes: nothing game-derived. `tools/firstrun/manifest.py --from "<Empire Earth Gold>"` regenerates it from an install's own hash database.

**GOG's `goggame-1207658777.hashdb` is usable** [measured]:
- Format: a zip holding one member of the same name: a 12-byte header (`<III`: header size 12, version 1, record count), then 1,056-byte records of a NUL-padded UTF-8 Windows path (1,024 bytes, relative to the install folder) and the file's MD5 as 32 hex digits. `gogdb.read_hashdb` reads it and returns `None` for anything else.
- The user's install has 602 records, 294 of them under `Empire Earth\`. All 294 match the files on disk; the only file on disk not listed is the game's own `0_Error.log`. Checking all 294 takes 0.6 s with a warm cache.
- It is not used as the reference, because a bare `Empire Earth` folder (source `game`) has none, and a different GOG build would be self-consistent. The manifest was made from it. When an import has one, it is compared with the manifest, and a difference is reported as "a different GOG build" (a warning).

**Optional files.** The native build never opens `binkw32.dll`, `ddraw.dll` (GOG's DirectDraw wrapper; the kit's shim answers the name, boot.md §2), `Mss32.dll`, `dxcfg.*`, `rmvparse.exe`, the read-mes, EULAs, `sierra.inf` and `redist/`. Missing or different (a player who dropped dgVoodoo's `ddraw.dll` in), they are a warning only. Everything else (the modules, `Data/`, the `.cfg` files) must match: a different `data.ssa` is a different game, and a LAN game with it goes out of sync.

**Common mistakes, and what the player is told** (error code in brackets, §6):

| Mistake | Detected by | Message (abridged) |
|---|---|---|
| Another version: the 2001 CD release, a Sierra patch | EXE hash; its size differs from GOG's | "a different version from GOG's Empire Earth Gold … import a fresh GOG installation or the GOG installer" [`wrong-version`] |
| A CD install | `Empire Earth.icd` (SafeDisc) beside the EXE | "an installation from the original CD … install it from GOG" [`wrong-version`] |
| NeoEE (the community edition) | `neoee.dll` / `NeoEE.cfg` | "This is NeoEE … needs GOG's original" [`wrong-version`] (both NeoEE installs on this Mac [measured]) |
| A patched or damaged module | same size, other hash | "… differs from GOG's Empire Earth Gold: patched, modded or damaged" [`wrong-version`] |
| The Art of Conquest picked | `EE-AOC.exe` and no `Empire Earth.exe` | "This is The Art of Conquest … choose the "Empire Earth" folder next to it" [`aoc-only`] |
| AoC installed too | `Empire Earth - The Art of Conquest` beside the base game | warning: only the base game is imported |
| A partial copy | missing or shorter files | "The game folder is incomplete: N files missing (…)" [`partial-copy`] |
| Modded data | MD5 | "N game files differ from GOG's … a modified game goes out of sync with other players" [`modified-files`]; `--allow-modified` keeps them, with warnings |
| A damaged installer download | innoextract's checksum warnings; MD5 | "The installer is damaged … download the .exe and its .bin files again" [`installer-unreadable`] |
| Installer parts missing | innoextract (slice errors) | "download all of them into the same folder and choose the .exe" [`installer-parts-missing`] |
| Another GOG game's installer | `innoextract --gog-game-id` ≠ 1207658777 | "GOG's installer for another game" [`wrong-version`] |

## 3. The GOG installer: innoextract

### 3.1 The binary

`tools/firstrun/innoextract/build.sh` builds one self-contained arm64 binary, reproducibly, from pinned sources:

| Source | Version | SHA-256 of the download | Licence |
|---|---|---|---|
| innoextract | master `6e9e34ed0876014fdb46e684103ef8c3605e382e` (2025-02-06) | `4d885b27…4c08ae` | zlib |
| Boost (iostreams, filesystem, date_time, system, program_options; static) | 1.87.0, `boost-1.87.0-b2-nodocs.tar.xz` | `3abd7a51…ecc680` | BSL-1.0 |
| xz (liblzma only; static) | 5.8.4 | `4ce24038…f65de4b` | 0BSD |
| bzip2 (static) | 1.0.8 | `ab5a0317…c4a2269` | bzip2 (BSD-style) |
| zlib | the system's `/usr/lib/libz.1.dylib` | — | zlib |

- **Why master, not 1.9:** 1.9 (2020) reads Inno Setup only up to 6.0.5. Master reads up to 6.4.0 and carries two 2025 fixes for GOG Galaxy installers (repeated and shared file locations).
- **Configuration:** built-in charset conversion (`WITH_CONV=builtin`, no libiconv), Boost/liblzma/bzip2 static, `-mmacosx-version-min=13.0`, Homebrew and `/usr/local` excluded from every search. Boost's CMake package asks for a plain `-lbz2`; a `-L` in front makes that the static `libbz2.a`, not the SDK's dylib.
- **Checks in the script:** `otool -L` lists only `libSystem`, `libc++` and `libz`; arm64 only; `minos 13.0`; innoextract's own unit tests pass. Then it strips (`strip -x`), signs ad hoc, and writes `build.json` (pins, compiler, the binary's SHA-256) and the four licence texts.
- **Output:** `/Users/max/Code/ee-build-cache/innoextract/innoextract-6e9e34e-boost1.87.0-xz5.8.4-bz1.0.8-macos13.0/bin/innoextract`: **822,256 bytes**, SHA-256 `81276b4ce99bdc11e3556515e7b698727187d4231f7b2044cff1badc1c43bdd5` [measured].
  - A clean build takes 2 minutes.
  - **A second clean build into another directory gave the same SHA-256** [measured]: the build is reproducible on this Mac with the same Xcode (clang 17.0.0, SDK 15.5).
  - The `--source pr210` variant: `innoextract-376a13e-…/bin/innoextract`, 822,272 bytes, SHA-256 `5d599b8e…dd94d09`, the same linkage.
- **Why not a pure-Python extractor:** Inno Setup's format changes with nearly every release (header layouts, LZMA/LZMA2/BZip2/zlib, encryption, GOG's split files). innoextract is the maintained reference implementation, and the open pull requests below track new versions. A Python port would have to be kept up by us and could not be checked against GOG's installers tonight.

### 3.2 Inno Setup versions [measured]

| Installer (Inno Setup's own, from jrsoftware/issrc releases, SHA-256 checked) | Setup data | upstream master | `--source pr210` |
|---|---|---|---|
| `innosetup-5.5.9-unicode.exe` | 5.5.7 (u) | reads, lists, extracts | reads, extracts |
| `innosetup-5.6.1-unicode.exe` | 5.6.0 (u) | reads, lists, extracts | reads, extracts |
| synthetic GOG-shaped installer, Inno Setup 5.6.1, LZMA2 solid, exe + 4 `.bin` slices (§3.3) | 5.6.0 (u) | imports end to end; missing and damaged slices caught | the same (the installer tests pass with it) |
| `innosetup-6.4.3.exe` | 6.4.3 | **fails** ("Unexpected setup data version") | reads, extracts |
| `innosetup-6.7.3.exe` | 6.7.0 | **fails** ("Could not determine setup data version") | extracts; one warning (it cannot read back a multi-part file to checksum it) |
| `innosetup-7.1.0-x64.exe` | 7.0.0.3 | **fails** (same) | extracts; the same warning |

**What GOG uses:** the user's GOG install (March 2026) has an uninstaller built by **Inno Setup 5.6.2 (u)**: `unins000.exe` holds "Inno Setup Setup Data (5.6.2) (u)" [static]. GOG's installer for this build is therefore 5.6.2, which master reads.

If GOG rebuilds it with Inno Setup 6.4.2 or later, upstream innoextract cannot read it yet. `build.sh --source pr210` builds dscho/innoextract `376a13e` instead: upstream pull request #210, open, which adds 6.4.2 to 7.0.2 on top of master. It read every installer above here.

**Decision:** the app ships the upstream build. It switches to `pr210` only if GOG's real installer (§3.4) turns out to be 6.4.2 or later, or when upstream merges #210.

### 3.3 How the importer uses it (`tools/firstrun/inno.py`)

1. **Find the binary:** `--innoextract`, `$EE_INNOEXTRACT`, the app's `bin/innoextract`, the developer build in `ee-build-cache`, then `PATH`.
2. **Check the installer:** `--gog-game-id` must be 1207658777 (when the installer has a GOG id). `--list` must contain `Empire Earth.exe`.
3. **Extract only the base game and GOG's metadata,** about 413 MB, into `<home>/Game.partial/extract`: `--include "Empire Earth"` (a whole path component, so `Empire Earth - The Art of Conquest` does not match) plus the GOG files (script, hashdb, info, `goglog.ini`). The flags are `--extract --timestamps none --progress=0 --color=0`. The manuals and the expansion are never written [measured on the synthetic installer].
4. **Progress:** one JSON line per extracted file, counted against the `--list` total.
5. **Errors:**
   - innoextract only warns ("Checksum mismatch", exit 0) when a slice is damaged; the importer treats that as `installer-unreadable`.
   - Slice errors become `installer-parts-missing`.
   - The MD5 check of §2 runs afterwards anyway.
6. **Then the extracted folder is imported like any other,** except that its files are moved into place, not copied.

**Testing it without GOG's installer:**
- `tools/firstrun/make_test_installer.py` compiles a synthetic GOG-shaped installer with Inno Setup 5.6.1's `ISCC.exe` under Wine. The installer has the nine module names with synthetic bytes, a 3 MB incompressible `Data/data.ssa`, a script with a fake CD key, an expansion folder and a manual. It is split into an exe and `-1.bin` … `-4.bin` like GOG's.
- Wine runs headless: a fresh prefix with the null graphics driver and no Mono or Gecko. The build took 11 s.
- With `EE_TEST_INNO_DIR` set to its output, `tests/test_firstrun.py` imports it end to end. It checks the CD key and every file's MD5, and that the expansion and manual are not extracted. Then it removes one `.bin` (`installer-parts-missing`), damages one (`installer-unreadable`), and offers a fake `.exe` (`installer-unreadable`).

### 3.4 Still to test: GOG's real installer (needs the user)

There is no GOG installer on this Mac or on legion. In the morning:

1. On gog.com → Account → Games → **Empire Earth Gold** → *Download offline backup game installers* → Windows, English: download `setup_empire_earth_gold_<version>.exe` **and** every `setup_empire_earth_gold_<version>-N.bin` beside it, into `~/Downloads`.
2. Run, in `/Users/max/Code/ee-macos-wt/importer` (or the main checkout once merged):

   ```sh
   IE=/Users/max/Code/ee-build-cache/innoextract/innoextract-6e9e34e-boost1.87.0-xz5.8.4-bz1.0.8-macos13.0/bin/innoextract
   $IE -i ~/Downloads/setup_empire_earth_gold_*.exe | head -3          # the Inno Setup version
   T=/tmp/ee-firstrun-test
   EE_INNOEXTRACT=$IE .venv/bin/python -I tools/firstrun/firstrun.py --home $T import ~/Downloads/setup_empire_earth_gold_*.exe
   .venv/bin/python -I tools/firstrun/firstrun.py --home $T build
   .venv/bin/python -I tools/firstrun/firstrun.py --home $T status
   ```
3. **Expected:**
   - the import ends with `{"event": "result", "ok": true, ...}`, with `"source": {"kind": "installer"}` and `"registry": {"source": "script", "cd_key": true}`, in about 15–30 s;
   - `build` prints the same cache key as an import from the wrapper (`4fe2bf83292861c0` with today's kit and Homebrew LLVM), so the bundles are identical;
   - `diff -r "$T/Game/Empire Earth" "/Applications/Empire Earth.app/Contents/SharedSupport/prefix/drive_c/GOG Games/Empire Earth Gold/Empire Earth"` shows only `0_Error.log`.
4. **If innoextract fails** with "Unexpected setup data version" or "Could not determine setup data version":
   - the installer is Inno Setup 6.4.2 or later: build the other variant and repeat with it:

     ```sh
     tools/firstrun/innoextract/build.sh --source pr210
     ```
   - The binary lands in `ee-build-cache/innoextract/innoextract-376a13e-…`.
5. Delete `/tmp/ee-firstrun-test` afterwards: it holds a copy of the game and the CD key.

## 4. Import (`tools/firstrun/importer.py`)

### 4.1 Layout of the player's data folder

```
~/Library/Application Support/EmpireEarth/        <home>; also the kit's default profile directory
├── Game/
│   ├── Empire Earth/          the verified copy: the guest's C:\Empire Earth (read only in practice)
│   └── import.json            what was imported from where, the verification summary (never the key)
├── Build/                     the bundle cache (§5)
├── game-path.txt              <home>/Game/Empire Earth/Empire Earth.exe, for the kit host
├── registry.json              the kit's registry store, seeded here (§4.3)
├── Data/ Users/ 0_Error.log … the game's own writes, through the kit's overlay
└── .firstrun.lock
```

How the runtime maps the folders [static, kit `runtime/layout.cpp`, `host/game_path.cpp`, `mods/overlay.cpp`, `runtime/loader.cpp`]:
- **Profile.** Outside a checkout the profile is `os_user_data_dir("EmpireEarth")`, which is `<home>`, unless `$RECOMP_PROFILE_DIR` says otherwise.
- **Where the game is.** The host finds the EXE through `--exe`, `$RECOMP_EXE`, a developer checkout, then `<profile>/game-path.txt`, checked against the EXE's SHA-256. The importer writes that file.
- **The DLLs** load from beside the EXE.
- **Writes.** The overlay maps a guest path `C:\Empire Earth\X` to `<profile>/X` for reads first and for every write. The GOG copy is never written.
- **No collisions.** `Game/`, `Build/` and the dot files are names the game never opens under its root.

### 4.2 Copy, verify, publish

- **Fail fast.** Locate the source, check the nine modules and the presence of every file, then copy.
- **How each file is placed,** in order:
  - moved, when it comes from our own extraction;
  - cloned (`clonefile(2)`), on the same APFS volume, as for a Wine wrapper: no extra disk;
  - copied, elsewhere.
  
  Either way the destination is hashed: the MD5 of every file, plus the SHA-256 of the modules.
- **Staging.** Each file goes to `name.part`, then is renamed. Everything lands in `<home>/Game.partial/`.
- **Publishing** is two renames: `Game` → `Game.old`, `Game.partial` → `Game`; then `Game.old` is deleted. A run that stops between the two renames is finished by the next command (`recover()`). `<home>/Game` is always the previous complete import or the new one. A rejected import leaves the previous one untouched [tested].
- **Resume.** A stopped import keeps `Game.partial/`. The next run re-hashes the files staged there and keeps those that match (tested: "5 kept").
- **Normalised.** Modes are 0644, the manifest's spelling of every path is used, and the empty folders are created.
- **The player's own files** come along: saved games, scenarios and `Users/` profiles found in the source but not in the manifest. They go into the profile (`<home>/Data/Saved Games/…`), never overwriting.
- **Measured** from the user's wrapper: 294 files, 413,292,860 bytes, **0.6–0.7 s**, all 294 cloned [measured].

### 4.3 Registry and CD key

`registry.json` is seeded with `tools/boot/seed_user.py`'s defaults and merged, so the game's own saved options win.
- **Player name.** "Player Name" is the macOS account's first name, not legion's "Max the Conqueror"; `--player-name` overrides it.
- **Resolution.** `--width`/`--height` default to 1920×1080, as `seed_user.py` does.
- **Bit depth.** `Game Bit Depth` and `Texture Bit Depth` are seeded 32, legion's (`seed_user.py --bit-depth` sets either). Where a 16 comes from [measured, P3-G, headless]:
  - **EE's own defaults.** Started with no registry, EE writes 16/16, 800×600, `Direct3D` and the player name "Unknown" on exit. GOG's script writes only HKLM, and the importer takes nothing else from it, from `goglog.ini` or from a Wine prefix.
  - **A mode the display list lacks.** When the registry's Width × Height × depth is not enumerated, EE runs the movies, the menus and the game at 800×600×16 and saves Game Bit Depth 16 and 800×600 (Texture Bit Depth stays): seeded 1920×1080×32 with `RECOMP_D3D7_MODES` limited to 1152×864, the old headless list. The app offers every size up to 2560×1600 in both depths (the desktop host records no screen size), so the seeded 1920×1080×32 is always there.
  - **The merge keeps it.** The game's values win, so a profile that an earlier unseeded or headless run wrote keeps 16 bpp through an import. That is how the developer's profile ran at 16 bpp (2026-10-15: player name "Unknown144", 800×600×16; until P3-F, headless runs wrote the default profile's registry). A friend's first import starts with no registry and gets 32.
  - **16 bpp works** since kit `feat/cursor16`: the menu's cursor trail was the DD7 `Flip`, not the depth (graphics.md §11.6), and it showed at 32 bpp too.
- **The HKLM values,** with the CD key, come from the first of:
  1. `goggame-1207658777.script` (seed_user's own parser);
  2. `goglog.ini`, the installer's own record of the registry it wrote (section `[1207658777]`, `RootKey_i`/`SubKey_i`/`ValueName_i`/`ValueType_i`/`ValueData_i`);
  3. a Wine prefix's `system.reg` (`[Software\\Wow6432Node\\Sierra\\CDKeys]` `"EmpireEarth"=hex:…`).
- **Base game only:** the expansion's `EEAOC` and Mad Doc keys are left out.
- **The key is never printed or logged.** Only "CD key present/missing" and the source kind reach the JSON lines, the log and `import.json`. The tests check stdout, the log and `import.json` for the fake key in three encodings.
- **No key found** (a bare `Empire Earth` folder) is a warning: the game starts, but multiplayer may refuse it.

## 5. Build (`tools/firstrun/pipeline.py`)

### 5.1 Steps

| Phase | What | Progress unit | Resumes from |
|---|---|---|---|
| `preflight` | the imported modules' SHA-256; clang, ld64.lld, capstone/pefile in the translator's Python; what bundle.py's symbol check needs (§8); memory and cores; free disk; old work directories removed | steps | — |
| `inputs` | `tools/inputs/export.py`'s `run_one` per module in a process pool, each checked against `db/inputs.json`'s pins (`inputs-mismatch` otherwise) | modules | a module's `funcdb.json` (written last) |
| `translate` | `kit/tools/recomp/translate.py` per module: the EXE first, the DLLs beside it; each into `parts/<key>.tmp`, renamed when complete; a failed module does not stop the others | modules | each `parts/<key>/` |
| (assemble) | `parts/main` → `gen/`, `parts/<key>` → `gen/aux-<key>/`, `x86.h` beside them (as the kit's `publish_generated`); the listings are deleted | — | `work/translated` |
| `compile` | `kit/tools/recomp/bundle.py --gen gen --out bundles --cc … --lld … --jobs N -D GUEST_IMAGE_BASE=0x00400000u -D GUEST_HEAP_BASE=0x01000000u -D GUEST_SIZE=0x28000000u` (the defines CMake's `recomp_add_bundles` passes); objects counted as they finish; one line per linked bundle | objects (253) | bundle.py's per-object stamps |
| `publish` | bundles moved into `Build/<key>.tmp/`, with `symbols.json` and `build.json`; renamed to `Build/<key>/`; `Build/current.json` replaced atomically | steps | — |
| `cleanup` | the work directory and every other build deleted | steps | — |

The work directory mirrors a game repository, so the kit's tools run unchanged on it:
- `work-<key>/game/` has copies of `game.toml` and `globals.toml`;
- `game/original/gog/Empire Earth` is a symlink to `<home>/Game/Empire Earth`;
- `game/analysis/kit/` holds the inputs.

Every child runs as `<python> -I` in its own session, so a stop takes its compilers down too.

### 5.2 Cache key

`Build/<key>`: the first 16 hex digits of a SHA-256 over:
- the nine modules' SHA-256;
- `game.toml`, `globals.toml` and `db/inputs.json` (the pins cover the function DB and the exporter's output);
- the translator (`translate.py`, `game_config.py`);
- the kit headers the generated C includes: the include closure of `x86.h`, `intrinsics.h`, `seh.h`, `thunks.h` and `freestanding.h`. They are also the bundles' ABI with the host, so a runtime-header change rebuilds;
- `bundle.py`, the defines, the minimum macOS;
- `clang --version` and `ld64.lld --version` (first lines).

An app update that changes none of these reuses the bundles. One that changes any rebuilds, and the old build is deleted once the new one is current. The key does not depend on paths or a git checkout, so it is the same in the app and in a checkout.

### 5.3 Resources

- **Jobs.** Compile jobs default to the core count. On Macs with 8 GB or less: 4 compile jobs, one DLL translation beside the EXE's, and 2 exporter processes.
- **Memory.** The EXE's translation peaks at 2.15 GB RSS [measured]; packaging.md §1 has the rest.
- `--jobs N` and `--nice N` override.
- **Disk.** Unless the bundles are already built, the preflight wants 800 MB (the measured peak was 744 MB) minus what the work directory already holds, plus a 200 MB margin: 1.0 GB for a fresh build.

### 5.4 Measured [measured]

| | First build (`--keep-work`) | Second build, normal, stopped by SIGTERM at 66/253 objects and resumed |
|---|---|---|
| inputs | 13.1 s | 27.2 s |
| translate (the EXE's; the DLLs ran beside it) | 31.8 s | 58.7 s |
| compile + link (253 objects, 16 jobs) | 204.7 s | 114.5 s, stopped (exit 130 within 1 s, no compiler left running); then 170.6 s, starting at 66 objects |
| total | **250 s** at load average ~67 (`tools/build.py --regenerate --target bundles`: 283 s in the same conditions) | 205 s + 171 s at load average 40–70 |
| peak disk under `Build/` | 0.92 GB, the listings kept | **0.74 GB** (726,552 KiB, sampled every second): C, objects and bundles; 0.49 GB while the listings and the C coexist |
| result | 9 bundles, 176 MB (main 134.8 MB, engine 20.8, sxlrt233 13.9, Default 2.9, DX7HR 2.1, DX7HRTnL 2.1, TCP 0.5, Miles mixer 0.2, Language 0.03) + `symbols.json` 7.4 MB | the same bytes as the first build (`cmp`, all nine) |

Uncontended, packaging.md §1's figures apply: about 2.7 min on an M4 Max and 7–9 min on a base M1 [inferred]. Peak memory is the EXE's translation, 2.15 GB RSS [measured]. Steady state on the player's disk: `Game/` 413 MB (nothing extra when cloned from a wrapper on the same volume) plus `Build/` 176 MB.

**Equivalence with the developer pipeline [measured, 2026-10-15]:**

| Check | Result |
|---|---|
| inputs vs `tools/inputs/export.py` run in a checkout | `diff -r` identical |
| generated C, all nine modules + `symbols.json`, vs `tools/build.py --regenerate --target bundles` from the same inputs (kit `ee` e93e875) | `diff -r` identical |
| the nine bundles vs that build's | **byte-identical** (`cmp`) |
| M1 replay case A06, `ee_m1_replay --bundles <home>/Build/<key>` | **9/9 packs pass** |

## 6. The contract for P6-B3 (the app)

**Invocation:**

```
<bundled python> -I <resources>/tools/firstrun/firstrun.py --home <home> [--quiet] [--nice N] COMMAND …
```

- **Commands:** `probe [PATH…]`, `import PATH [--allow-modified] [--innoextract BIN] [--width W --height H] [--player-name NAME]`, `build [--jobs N] [--cc CLANG] [--lld LLD] [--python PY] [--kit DIR] [--keep-work]`, `status [--cc …]`.
- **The toolchain is injectable:** `--python` (default: the interpreter running firstrun.py), `--cc`/`--lld` (default `$RECOMP_FREESTANDING_CC`/`$RECOMP_LD64_LLD`, then Homebrew's, then `PATH`), `--innoextract`/`$EE_INNOEXTRACT`, `--kit`/`$RECOMP_KIT_DIR`.

**stdout is JSON lines only;** a human-readable copy goes to stderr unless `--quiet`, and to `<home>/Build/firstrun.log`. Every line is one object:

```json
{"event": "progress", "phase": "compile", "done": 141, "total": 253, "unit": "objects", "message": "Compiling"}
{"event": "warning",  "phase": "verify",  "done": 0, "total": 0, "message": "ddraw.dll differs from GOG's; ..."}
{"event": "error",    "phase": "import",  "done": 0, "total": 0, "code": "partial-copy", "message": "...", "data": {...}}
{"event": "result",   "phase": "build",   "done": 1, "total": 1, "ok": true, "message": "...", "data": {...}}
```

- `phase`, `done`, `total` and `message` are always there.
- `unit` is one of `files`, `bytes`, `modules`, `objects`, `steps`.
- **Phases in order.** import: `locate`, `extract` (installer only), `verify`, `copy`, `registry`, `publish`. build: `preflight`, `inputs`, `translate`, `compile`, `publish`, `cleanup`.
- **Weights for one progress bar** (from §5.4): inputs 5 %, translate 15 %, compile 78 %, the rest 2 %.
- **The last line is always the `result`.**
- **Exit status:** 0 done, 1 failed, 2 bad usage, 130 stopped (SIGINT/SIGTERM; run the same command again to resume).

**Error codes** (`progress.ERROR_CODES`): `usage`, `busy` (another run holds `<home>/.firstrun.lock`), `not-found`, `aoc-only`, `wrong-version`, `partial-copy`, `modified-files`, `installer-unreadable`, `installer-parts-missing`, `installer-tool-missing`, `not-imported`, `disk-space` (`data.need_bytes`, `data.free_bytes`), `toolchain-missing`, `inputs-mismatch`, `translate-failed`, `compile-failed`, `interrupted`, `internal`.

**`status`'s result `data`** (≈0.1 s; takes no lock):
- `state`: `needs-import` | `needs-build` | `build-resumable` | `ready`;
- `import`: whether imported, the source, the file count, whether a CD key is present;
- `build`: `expected_key`, `current` (`key`, `dir`, `load_order`, `symbols`, `complete`), `ready`, `resumable`;
- `game_path`, `registry`.

The app shows the first-run UI unless `state` is `ready`.

**The bundle layout the host loads (proposal):**

```
<home>/Build/current.json      {"key": "<key>", "dir": "<key>"}
<home>/Build/<key>/
    a_sxlrt233.bundle  b_engine.bundle  c_default.bundle  d_language.bundle  e_dx7hr.bundle
    f_dx7hrtnl.bundle  g_tcp.bundle  h_milesmix.bundle  main.bundle
    symbols.json       the EXE's translation index (mods' symbol table)
    build.json         key, load_order, bundles [{key, module, file, bytes, sha256}], inputs, seconds, disk_bytes
```

- **Load order.** `build.json`'s `load_order` is the eight DLLs in `game.toml` key order (dependency order), then `main.bundle`, as the EH harness loads them.
- **Registration.** Each bundle registers its module from a constructor (build.md §5), and the runtime's weak `recomp_call`/`recomp_jump` dispatch through the registry. So the host only `dlopen`s them:
  - in its own code, by reading `build.json`;
  - or with the kit's existing `RECOMP_EXTRA_CODE=<path>:<path>:…`, which `dlopen`s a colon-separated list.
- **Before starting,** the launcher runs `status`. Only `ready` (the current build's key is `expected_key`, every bundle present) starts the game: bundles from another kit have another ABI.
- **`symbols.json`** (resolved in P6-B3): the kit's `host_resource("symbols.json")` is `$RECOMP_SYMBOLS`, else `$RECOMP_CODE_DIR/symbols.json` (kit `runtime/layout.cpp`). The app's `Resources/` never holds one.
- **The load order** (as built): the kit's player host reads `RECOMP_CODE_DIR/build.json`'s `load_order` (kit `host/code_dir.h`). It `dlopen`s each bundle and stops with a message if one is missing, does not load, or registers nothing, or if no bundle covers the EXE's entry point.

**What the app must carry for the first run** (as built, packaging.md §8: `Contents/Resources/ee/` in this shape, `tools/package_app.py` `ee_sources`) [measured: the packaged app's first run used only these]:
- this repository's `tools/firstrun/`, `tools/inputs/`, `tools/boot/seed_user.py`, `tools/kitpath.py`;
- `game.toml`, `globals.toml`, `db/functions/*.json`, `db/inputs.json`, `db/gog-files.json`;
- the kit's `tools/recomp/translate.py`, `tools/recomp/bundle.py`, `tools/game_config.py`;
- the runtime headers of §5.2: the include closure of the five, five files today (`x86.h`, `intrinsics.h`, `seh.h`, `thunks.h`, `freestanding.h`);
- `bin/innoextract`; its four licence texts are in the app's `THIRD_PARTY_NOTICES.txt`;
- P6-B1's Python, clang and ld64.lld, in `Contents/Resources/toolchain/`.

Nothing else from the kit: no `tools/build.py`, no CMake.

### 6.1 The app's side, as built (P6-B3)

`Contents/MacOS/EmpireEarth` (`app/launcher/launcher.m`, AppKit, Objective-C) drives `firstrun.py` with this contract:
- **Invocation:** `toolchain/python/bin/python3 -I -B ee/tools/firstrun/firstrun.py --home <home> --quiet …`.
  - `status` and `build` get `--cc`, `--lld` and `--kit`, all inside the app; `import` gets `--innoextract`.
  - The environment is the launcher's, with `PATH=/usr/bin:/bin:/usr/sbin:/sbin` and no `PYTHON*`, `RECOMP_*`, `SDKROOT`, `CPATH` or similar.
  - `<home>` is `$EE_HOME`, else `~/Library/Application Support/EmpireEarth`.
- **The window**, unless `status` says `ready`:
  - a welcome line;
  - "Choose your GOG Empire Earth Gold installer or game folder": an `NSOpenPanel` that takes files, folders and `.app`s;
  - drag and drop onto the window;
  - up to four one-click suggestions from `probe` (usable candidates only; `ok` is not false);
  - one progress bar.
    - The import fills the first 3 %, or 15 % when an `extract` phase shows it is the installer.
    - The build fills the rest by §6's weights (preflight 1, inputs 5, translate 15, compile 78, publish and cleanup 0.5 each).
    - Above the bar: "Step 1 of 2"/"Step 2 of 2", the phase in words and the elapsed time. Below it: firstrun's message with "N of M".
  - Errors: a headline per error code, firstrun's message, and what to do (INSTALL.md §4 has the table). A problem with the chosen files offers the choice again; anything else offers *Try Again* and *Show Log* (`Build/firstrun.log` in Finder).
- **Stopping:**
  - *Cancel*, ⌘Q, closing the window and SIGTERM all send SIGTERM to `firstrun.py`, which stops its compilers and exits 130. The app quits once it has: `applicationShouldTerminate` answers "later". If firstrun does not stop within 15 s, it gets SIGKILL.
  - The window then offers *Continue*, and the next start sees `build-resumable` and carries on. Measured: stopped in the compile step, the next start resumed and finished (`runs: 2`).
- **Imported but not built** (`needs-build`, `build-resumable`): the build starts by itself.
- **`ready`:** the launcher `exec`s `Contents/MacOS/EmpireEarthHost`, with no window, in the same process (packaging.md §8 has the environment). The host's output goes to `<home>/Logs/game.log`, the launcher's own lines to `<home>/Logs/launcher.log`.
- **For tests and automation**, all with no window:
  - `EmpireEarth --no-ui [--import PATH] [--no-play]` (with no `--import`, the first usable candidate `probe` finds). It prints firstrun's JSON lines on stdout and the host's environment on stderr, and passes SIGINT and SIGTERM on to firstrun;
  - `EmpireEarth --status` prints the `status` result;
  - `EmpireEarth --import PATH` opens the window and starts importing at once;
  - `$EE_PROBE_PATHS` (colon-separated) replaces probe's usual places, `$EE_FIRSTRUN_NICE` lowers the build's priority and `$EE_FIRSTRUN_JOBS` sets its jobs.

**The home as built** (§4.1, plus `Logs/`):

```
<home>/Game/Empire Earth/ …   Game/import.json   Build/<key>/{9 bundles, symbols.json, build.json}
<home>/Build/current.json     Build/firstrun.log  registry.json  game-path.txt
<home>/Logs/launcher.log      Logs/game.log (+ game.previous.log)   Data/ Users/ …   .firstrun.lock
```

## 7. Files

| File | Role |
|---|---|
| `tools/firstrun/firstrun.py` | the CLI (§6), locking, signals |
| `tools/firstrun/sources.py` | locating a game in a path (§1) |
| `tools/firstrun/verify.py` | the three levels and the diagnostics (§2) |
| `tools/firstrun/gogdb.py` | hashdb, product info, the CD key's three sources |
| `tools/firstrun/inno.py` | innoextract (§3.3) |
| `tools/firstrun/importer.py` | copy, publish, registry, `game-path.txt` (§4) |
| `tools/firstrun/pipeline.py` | preflight, inputs, translate, compile, publish, cache key (§5) |
| `tools/firstrun/progress.py` | JSON lines, the log, error codes |
| `tools/firstrun/paths.py` | `sys.path` for `-I`, the home layout |
| `tools/firstrun/manifest.py` | writes `db/gog-files.json` (development) |
| `tools/firstrun/make_test_installer.py` | the synthetic installer (development, §3.3) |
| `tools/firstrun/innoextract/build.sh` | the innoextract build (§3.1) |
| `tests/test_firstrun.py` | §9 |

## 8. Open issues

- **`nm` on a player's Mac.** bundle.py's undefined-symbol check runs `llvm-nm` beside clang or on `PATH`, else `nm`. Without the Command Line Tools, `/usr/bin/nm` is a stub that pops up the CLT installer.
  - Kit `feat/importer` makes bundle.py read the Mach-O's symbol table itself (`macho_undefined`). It agrees with llvm-nm on all nine bundles, with tests.
  - Until that is merged, the importer's preflight refuses a stub `nm` (`toolchain-missing`) rather than letting it pop up; P6-B1 could ship `llvm-nm` instead.
- ~~**`symbols.json` lookup.**~~ Resolved in P6-B3 (§6).
- **Homebrew clang reads `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/SDKSettings.json`** even with `-nostdinc` (in every object's depfile) [measured]. On a Mac without the CLT the file is absent, which P6-B1's bundled clang must tolerate; packaging.md measured the official LLVM release working.
- **Inno Setup ≥ 6.4.2:** §3.2; settled by the morning test.
- **Defaults to decide for players:** the resolution (1920×1080, legion's) and the player name (the account's first name).
- **A different GOG build** (data files changed by GOG) fails the manifest. The hashdb comparison then names it. Re-run `manifest.py` on it, and check the modules still match `game.toml`.

## 9. Tests

`tests/test_firstrun.py` (pytest, part of `tools/test.py`; no game files) covers:
- **GOG files.** The hashdb round trip and garbage; the CD key from the script, `goglog.ini` and a Wine `system.reg` (continuation lines), in that order of preference.
- **Sources.** A gold folder with the expansion, its subfolder and its EXE; a bare USB copy; a fake Wineskin wrapper (the key only in `system.reg`); installers with two `.bin` parts, picked as the exe, a part, or the folder; an AoC-only folder; nothing.
- **Verify.** A patched module, NeoEE, a CD install, a missing or short data file, a missing module, a missing optional file.
- **Import.**
  - From the fake wrapper end to end: files, empty folders, carried saves, `game-path.txt`, the registry with the key and player name.
  - The key is absent from stdout, the log and `import.json`, and the wrapper is untouched.
  - A re-import keeps the game's own registry values.
- **Rejections keep the previous import:** modified data, then `--allow-modified`; a wrong module; a partial copy; a modified optional file (warning only); a missing path.
- **Interrupts.** A stop after 5 files, then a resume that keeps them; a publish stopped between its renames; the lock (`busy`); usage and `status` with nothing imported.
- **Pipeline.** Job plans by memory; the defines equal CMake's; the cache key follows the modules, the translator, the include closure and the compiler.
- **The whole build** with a stand-in translator and bundle driver:
  - one module fails, and the others finish;
  - the resume translates only the failed one;
  - publish, `current.json`, nine bundles in load order, the work directory gone;
  - a second run is cached and `status` says `ready`;
  - a translator change says `needs-build`, rebuilds under a new key and deletes the old one;
  - the preflight's disk check, and a build before import.
- **The JSON schema** of every line, in every case.
- **With `EE_TEST_INNO_DIR`:** the synthetic spanned installer of §3.3, imported end to end, then with a missing and a damaged `.bin`, and a fake exe.

[measured] 17 tests, plus the 2 installer tests with `EE_TEST_INNO_DIR`, pass in 1.6 s.
