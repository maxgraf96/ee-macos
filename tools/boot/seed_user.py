#!/usr/bin/env python3
"""Seed the player's data directory for the native build: the emulated registry.

    .venv/bin/python tools/boot/seed_user.py [--user DIR] [--gog-script FILE]
                                             [--width W --height H] [--reset]

Writes <user>/registry.json (the kit's registry store, runtime/misc.cpp), the
values a GOG install of the base game leaves on Windows:

- HKCU\\Software\\SSSI\\Empire Earth (+ \\Game Options): the values on legion,
  the Windows reference machine (windows/setup.ps1, docs/reference/legion.md
  §3), so the native build and the references start from the same options.
  The install location is the guest's: Installed From Volume "C:" and
  Installed From Directory "\\Empire Earth\\" (game.toml guest_root).
- HKLM\\Software\\Stainless Steel Studios\\Empire Earth and
  HKLM\\Software\\Sierra\\CDKeys "EmpireEarth": read from the player's own GOG
  install script (goggame-1207658777.script), base game only. The CD key is
  the player's; it is written only into their registry file and never
  printed. A 32-bit program sees HKLM\\Software, so there is no WOW6432Node.

An existing registry is merged, never overwritten: the game saves its options
there on exit. --reset starts again from the defaults.

Default user directory: ~/Library/Application Support/EmpireEarth (the one
build/recomp/ee_boot uses). Default script: next to the game folder, then the
Wineskin build's.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "goggame-1207658777.script"
SCRIPT_CANDIDATES = [
    ROOT / "original/gog" / SCRIPT,
    ROOT / "original" / SCRIPT,
    Path("/Applications/Empire Earth.app/Contents/drive_c/GOG Games/Empire Earth Gold") / SCRIPT,
]

HKCU_SSSI = "HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"
REG_SZ, REG_BINARY, REG_DWORD = 1, 3, 4


def sssi_values(width, height):
    """windows/setup.ps1's $Sssi, with the guest's install location."""
    d = {
        "Ambient Sounds": 1, "Animation Smoothing": 1, "AutoSave In Milliseconds": 0x927c0,
        "Button MIP Levels to Drop": 0, "Computer Player Difficulty Level": 1,
        "CT Send Interval Step Down": 0x0a, "CT Send Interval Step Up": 0x7d, "Debug OOS Handling": 1,
        "Display Clouds": 1, "Display Shadows": 1, "Effect Detail": 2, "Enable Animal Herds": 1,
        "Game Bit Depth": 32, "Game Window Height": height, "Game Window Width": width,
        "Grab Scroll Speed": 0x11, "Host Game Name": "Unknown",
        "Installed From Directory": "\\Empire Earth\\", "Installed From Volume": "C:",
        "Last Auto Save": "", "Last Game File": "", "Last Open Post Game Stats Page": 1,
        "Last Quick Save": "", "Last Scenario File": "", "Max CT Send Interval": 0x8ca,
        "Max Number Of Sounds (percent)": 100, "Max PTs per Second": 0x7d, "Min PTs per Second": 0x0f,
        "MIP Levels to Drop": 0, "Model Detail": 2, "Mouse Sensitivity": 0x1f, "Music Enabled": 1,
        "Music Volume": 0x3e, "Network Adapter": 0, "Number Of AutoSaves": 10,
        "Player Name": "Max the Conqueror", "PT Rate Tuning Threshold (percent)": 0x1e,
        "Rasterizer Name": "Direct3D", "Reverb Enabled": 1, "Right Hand Mouse Orientation": 1,
        "Screen ID": 0, "Show Auto Waypoints": 0, "Show Blood": 1, "Side Scroll Speed": 0xc8,
        "Sierra.com Game Name": "Unknown", "Sierra.com User ID": "Unknown", "Sound Volume": 100,
        "Take End Game Screenshots": 0, "Take JPG Screenshots": 1, "Terrain MIP Levels to Drop": 0,
        "Texture Bit Depth": 32, "Trilinear Filter": 0, "Unit Lighting": 0, "Unit Scale Factor": 100,
        "UseAreaEffectOverlays": 1, "UseCandidateWindow": 0, "Wait for VSync": 1,
        "WonNet Password": "", "Zoom Lock": 100, "ZoomStyle": 0,
    }
    return d


GAME_OPTIONS = {
    "Allow Custom Civs": 0, "Cheat Codes": 1, "Difficulty Level": 1, "Ending Epoch": 0x0d,
    "Game Speed": 1, "Game Unit Limit": 0x1f4, "Game Variant": 0, "Lock Speed": 0, "Lock Teams": 0,
    "Map Size": 1, "Map Type": "Continental", "Record Game": 0, "Reveal Map": 0,
    "Starting Epoch": 0x0e, "Starting Resources": 4, "Wonders For Victory": 3,
}


def value(v):
    if isinstance(v, int):
        return {"type": REG_DWORD, "data": v}
    return {"type": REG_SZ, "data": v}


def hklm_from_script(path):
    """{key: {name: value}} for the script's base-game HKLM actions, as setup.ps1 applies them."""
    out = {}
    actions = json.loads(Path(path).read_text(encoding="utf-8-sig"))["actions"]
    for a in actions:
        inst = a.get("install", {})
        arg = inst.get("arguments", {})
        if inst.get("action") != "setRegistry" or arg.get("root") != "HKLM":
            continue
        sub = arg.get("subkey", "")
        if "Mad Doc" in sub or arg.get("valueName") == "EEAOC":
            continue
        key = out.setdefault("HKEY_LOCAL_MACHINE\\" + sub, {})
        name = arg.get("valueName")
        if not name:
            continue
        if arg.get("valueType") == "binary":
            key[name] = {"type": REG_BINARY, "data": base64.b64decode(arg["valueData"]).hex()}
        elif arg.get("valueType") == "dword":
            key[name] = {"type": REG_DWORD, "data": int(arg["valueData"])}
        else:
            key[name] = {"type": REG_SZ, "data": str(arg.get("valueData", ""))}
    return out


def parents(key):
    """Every ancestor of a key below its hive, so opening any of them succeeds."""
    parts = key.split("\\")
    return ["\\".join(parts[:i]) for i in range(2, len(parts))]


def defaults(width, height, script):
    reg = {HKCU_SSSI: {k: value(v) for k, v in sssi_values(width, height).items()},
           HKCU_SSSI + "\\Game Options": {k: value(v) for k, v in GAME_OPTIONS.items()}}
    if script:
        reg.update(hklm_from_script(script))
    for key in list(reg):
        for p in parents(key):
            reg.setdefault(p, {})
    return reg


def merge(existing, seed):
    """Add what is missing; the game's own values win."""
    added = 0
    for key, values in seed.items():
        have = existing.setdefault(key, {})
        for name, v in values.items():
            if name not in have:
                have[name] = v
                added += 1
    return added


def default_user_dir():
    return Path.home() / "Library/Application Support/EmpireEarth"


def find_script(explicit):
    if explicit:
        return Path(explicit)
    for p in SCRIPT_CANDIDATES:
        if p.is_file():
            return p
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", type=Path, default=default_user_dir())
    parser.add_argument("--gog-script", default=None, help="the player's %s" % SCRIPT)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--reset", action="store_true", help="discard the existing registry")
    args = parser.parse_args(argv)

    script = find_script(args.gog_script)
    if script is not None and not script.is_file():
        sys.exit("no GOG install script at %s" % script)
    seed = defaults(args.width, args.height, script)
    args.user.mkdir(parents=True, exist_ok=True)
    path = args.user / "registry.json"
    existing = {}
    if path.is_file() and not args.reset:
        existing = json.loads(path.read_text())
    added = merge(existing, seed)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    cd_key = any("CDKeys" in k and "EmpireEarth" in v for k, v in existing.items())
    print("%s: %d values added (%d keys); CD key %s; script %s"
          % (path, added, len(existing), "present" if cd_key else "MISSING", script or "not found"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
