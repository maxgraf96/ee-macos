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
| `docs/` | plan, ADRs, research, reference notes |
| `tools/` | analysis scripts (`peinfo.py`, `inventory.py`; deps in `tools/requirements.txt`) |
| `db/` | function/API databases derived from analysis (names and addresses only) |
| `windows/` | scripts for the Windows reference machine |
| `original/` | (ignored) the player's game files |

## Tools quickstart
```sh
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python -I tools/inventory.py "original/gog/Empire Earth"   # → db/win32-imports.json
```
