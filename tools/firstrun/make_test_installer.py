#!/usr/bin/env python3
"""A synthetic GOG-style Inno Setup installer for testing the importer's
installer path without GOG's (docs/reference/firstrun.md §3.3).

    .venv/bin/python tools/firstrun/make_test_installer.py --iscc DIR/ISCC.exe --out DIR [--wine WINE]

Development only. Writes a game-shaped tree of synthetic files (the nine
module names, Data/, a goggame-1207658777.script with a fake CD key, an Art
of Conquest folder and a manual that must not be extracted), compiles it with
Inno Setup's command-line compiler under Wine, split like GOG's installers
(setup_*.exe plus setup_*-1.bin, -2.bin, ...), and writes manifest.json and
modules.json describing the synthetic game, which tests/test_firstrun.py
uses in place of db/gog-files.json and game.toml when EE_TEST_INNO_DIR
names the output directory. Nothing in it comes from the game.

ISCC.exe comes from Inno Setup's own installer, which innoextract unpacks:
    innoextract -d isetup -I app innosetup-5.6.1-unicode.exe   (isetup/app/ISCC.exe)
Wine runs headless here: the null graphics driver, no Mono or Gecko.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FAKE_KEY = bytes(range(0x41, 0x51))
MODULES = ["Empire Earth.exe", "sxlrt233.dll", "Low-Level Engine.dll", "Default.dll", "Language.dll",
           "DX7HRDisplay.dll", "DX7HRTnLDisplay.dll", "TCP Protocol.dll", "Miles Sound System Mixer.dll"]
OTHER = ["Data/Campaigns/EETheGreeks.ssa", "WONLobby.cfg", "ddraw.dll", "readme.txt"]
BIG = "Data/data.ssa"                      # incompressible, so the installer spans several slices


def content(rel):
    if rel == BIG:
        out, h = bytearray(), hashlib.sha256(b"synthetic data.ssa")
        while len(out) < 3 << 20:
            h = hashlib.sha256(h.digest())
            out += h.digest()
        return bytes(out[:3 << 20])
    return ("synthetic %s\n" % rel).encode() * 7


SCRIPT = r"""; A synthetic GOG-shaped installer (tools/firstrun/make_test_installer.py)
[Setup]
AppName=Empire Earth Synthetic
AppVersion=1.0
DefaultDirName={pf}\GOG Games\Empire Earth Gold
OutputDir=out
OutputBaseFilename=setup_empire_earth_gold_synthetic
Compression=lzma2
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1048576
SlicesPerDisk=1
Uninstallable=no
PrivilegesRequired=lowest

[Files]
Source: "src\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iscc", required=True, type=Path, help="Inno Setup's ISCC.exe")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--wine", default=shutil.which("wine") or "wine")
    ap.add_argument("--wineprefix", type=Path, default=None, help="default: <out>/wineprefix")
    args = ap.parse_args(argv)
    out = args.out.resolve()
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    src = work / "src"
    game = src / "Empire Earth"
    files = MODULES + OTHER + [BIG]
    for rel in files:
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content(rel))
    actions = [{"install": {"action": "setRegistry", "arguments": {
        "root": "HKLM", "subkey": "Software\\Sierra\\CDKeys", "valueName": "EmpireEarth",
        "valueType": "binary", "valueData": base64.b64encode(FAKE_KEY).decode()}}}]
    (src / "goggame-1207658777.script").write_text(json.dumps({"actions": actions, "productId": "1207658777"}))
    (src / "goggame-1207658777.info").write_text(json.dumps({"gameId": "1207658777", "name": "synthetic"}))
    (src / "Empire Earth - The Art of Conquest").mkdir()
    (src / "Empire Earth - The Art of Conquest/EE-AOC.exe").write_bytes(b"MZ synthetic expansion")
    (src / "Manual.pdf").write_bytes(b"%PDF synthetic")
    (work / "setup.iss").write_text(SCRIPT.replace("\n", "\r\n"))
    prefix = (args.wineprefix or out / "wineprefix").resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    if not (prefix / "user.reg").exists():
        # the null graphics driver from the first start: no window ever opens
        (prefix / "user.reg").write_text('WINE REGISTRY Version 2\n\n#arch=win64\n\n'
                                         '[Software\\\\Wine\\\\Drivers]\n"Graphics"="null"\n\n')
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all",
               WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d;winemac.drv=d")
    r = subprocess.run([args.wine, str(args.iscc.resolve()), "/Q", "setup.iss"], cwd=work, env=env,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        sys.exit("ISCC failed (%d): %s" % (r.returncode, (r.stdout + r.stderr)[-2000:]))
    built = sorted((work / "out").iterdir())
    for p in out.glob("setup_empire_earth_gold_synthetic*"):
        p.unlink()
    for p in built:
        shutil.move(str(p), out / p.name)
    manifest = {"base": "Empire Earth", "product": {"gameId": "1207658777"}, "dirs": [],
                "files": [[rel, len(content(rel)), hashlib.md5(content(rel)).hexdigest()] for rel in files]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out / "modules.json").write_text(json.dumps([[n, hashlib.sha256(content(n)).hexdigest()] for n in MODULES]) + "\n")
    shutil.rmtree(work)
    print("%s: %s" % (out, ", ".join(p.name for p in built)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
