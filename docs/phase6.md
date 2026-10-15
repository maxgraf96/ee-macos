# Phase 6: the shareable macOS app → M5 (1.0 for friends)

Context:
- [PLAN.md](PLAN.md) §5.8, Phase 6 and D10;
- **[packaging.md](reference/packaging.md):** what a player's first run needs, with measured sizes;
- [build.md](reference/build.md) §2.1 (Ghidra-free inputs);
- the protocol in [phase2.md](phase2.md).

## Done when
A friend with only their own GOG *Empire Earth Gold* installer (or install folder) and this app can:
- install it by drag and drop;
- start it, let it import their game files, verify them, translate and compile locally with the bundled toolchain (a few minutes once), and play;
- with nothing game-derived inside the app, and no Xcode, Command Line Tools, Ghidra or Homebrew needed.

## Workstreams
| ID | State | Scope |
|---|---|---|
| P6-A inputs | **merged** (ee-macos 8a27424) | Translation inputs from `db/functions` + the player's binaries with our own capstone-based renderer of Ghidra's syntax. Byte-identical to the Ghidra pipeline: 1,931,496 instructions; all 9 modules' generated C, 268 MB, `diff -r` clean. Pins in `db/inputs.json`. `setup.py`/`run.py` default to it; `--ghidra` is kept for development. |
| P6-B2 importer | **done on `phase6/importer`** (kit `feat/importer`), to merge | The first-run importer and pipeline driver ([firstrun.md](reference/firstrun.md)).<br>• `tools/firstrun/firstrun.py {probe,import,build,status}`: `python -I`, stdlib + capstone + pefile; JSON-lines progress; resumable; cache keyed by modules + translator + headers + compiler.<br>• Sources: GOG installer (innoextract, built from pinned sources, 822 KB, links only libSystem/libc++/libz), install folder, `Empire Earth` folder, Wine wrapper or bottle.<br>• Verification: nine module SHA-256s plus all 294 files against `db/gog-files.json`, with diagnostics for CD, patched, NeoEE, AoC, partial and modded copies; GOG's hashdb is read and compared.<br>• From the user's wrapper: import 0.7 s (APFS clones), build 250 s under load, peak 0.74 GB under `Build/`, 176 MB kept. Bundles byte-identical to `tools/build.py --target bundles`; M1 A06 9/9.<br>• Open: GOG's real installer (morning test, firstrun.md §3.4); Inno Setup ≥ 6.4.2 needs `--source pr210`; `symbols.json` lookup for the dlopen host. |
| P6-B1 toolchain | **done on `phase6/toolchain`** (kit `feat/toolchain`), to merge | The toolchain the app bundles ([build.md](reference/build.md) §5.1, [packaging.md](reference/packaging.md) §2, §3, §7; kit `tools/toolchain/`).<br>• `tools/stage_toolchain.py` → `build/toolchain/`: CPython 3.12.13 (python-build-standalone) + capstone 5.0.9 + pefile, trimmed, 44.1 MB; clang + ld64.lld 23.1.3 built from the source release as one AArch64-only binary with ThinLTO + PGO (trained on the kit's synthetic instruction vocabulary, no game code), 76.1 MB; `THIRD_PARTY_NOTICES.txt`; `MANIFEST.json`. 120.6 MB, 31.4 MB as `.tar.xz` (the official LLVM release alone was 303 MB). Every input pinned by SHA-256; the build is reproducible.<br>• All nine bundles byte-identical to Homebrew clang/lld 23.1.3's; compiles at 0.98–1.00× Homebrew's time (1.2× without PGO).<br>• Proof: steps 2–4 and `firstrun.py build` with only the staged toolchain, `env -i`, in a sandbox hiding Xcode, the CLT and Homebrew: inputs pinned, C `diff -r` identical, bundles identical, M1 A06 9/9, 136 s; nothing written into the toolchain.<br>• `bundle.py` now passes `--no-default-config` and keeps `SDKROOT`/`CPATH`/… out of the compiler's environment. Kit NOTICE gains glslang.<br>• Open: this repository has no licence file; FFmpeg's LGPL source offer (packaging.md §7). |
| P6-B app | next (after P3-E lands) | The distributable `EmpireEarth.app`: the runtime host **without** translated code, which `dlopen`s per-module bundles built on first run into `~/Library/Application Support/EmpireEarth/`; a bundled trimmed Python + capstone/pefile; bundled clang + `ld64.lld` (aim for an AArch64-only multicall build); a first-run importer (GOG installer via built-in Inno extraction, or a folder) → hash check → inputs → translate → bundles; ~~replace `nm` in `bundle.py`~~ (P6-B2); ad-hoc signing with `disable-library-validation`; ~~licence notices~~ and the bundled Python + LLVM (P6-B1). |
