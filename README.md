# Empire Earth for Apple Silicon

This project ports *Empire Earth* (2001; the GOG *Empire Earth Gold* base game) to native arm64 macOS. It uses static recompilation of the original x86 code plus a small runtime that reimplements the Win32/DirectX APIs the game uses. Multiplayer is the game's own LAN play: Mac↔Mac and Mac↔Windows (GOG), including over ZeroTier.

- Plan, decisions and roadmap: [`docs/PLAN.md`](docs/PLAN.md)
- Technical decisions (ADRs): [`docs/decisions/`](docs/decisions/)
- Research notes: [`docs/research/`](docs/research/)

**Status:** Phase 0 (foundations and base-kit spike).

## Ground rules
- **No game files in this repository, ever.** Game binaries, assets, Ghidra databases and any C generated from game code stay in the git-ignored `original/`, `analysis/`, `build/` and `spikes/` directories. Each player supplies their own GOG copy, and translated code is generated locally.
- Every change to translated or reimplemented behaviour needs a mechanical check: fuzz/differential tests, checksum streams, or byte-identical outputs versus Windows.

## Layout
| Path | Contents |
|---|---|
| `kit/` | [recomp-kit](https://codeberg.org/maxgraf/recomp-kit) fork (submodule, branch `ee`): translator, runtime, hosts |
| `game.toml`, `globals.toml` | the kit's game configuration: module hashes, the base map, translation inputs, curated symbols |
| `docs/` | plan, ADRs, research, reference notes |
| `tools/` | `build.py`/`setup.py`/`test.py` (thin wrappers around the kit's) and analysis scripts (`peinfo.py`, `inventory.py`, `ghidra/`; deps in `tools/requirements.txt`) |
| `mods/`, `tests/` | EE targets in the kit's build (the M1 harness, `tests/m1/`) and this repository's tests |
| `db/` | function/API databases derived from analysis (names and addresses only) |
| `windows/` | scripts for the Windows reference machine |
| `original/` | (ignored) the player's game files |

## Try it
```sh
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r kit/requirements-dev.txt   # once
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth"   # once: link your GOG copy, export the listings
.venv/bin/python tools/run.py      # seed the registry, translate (first time), build and open build/EmpireEarth.app
```
Boots to the main menu (Metal, mouse and keyboard; no audio yet). Details, the headless boot and what is still a stand-in: [`docs/reference/boot.md`](docs/reference/boot.md).

## Build
Setup, build, tests, the freestanding/`ld64.lld` bundle build and the DLL base map: [`docs/reference/build.md`](docs/reference/build.md).

## Tools quickstart
```sh
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python -I tools/inventory.py "original/gog/Empire Earth"   # → db/win32-imports.json
```
