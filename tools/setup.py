#!/usr/bin/env python3
"""Prepare Empire Earth's private translation inputs.

    .venv/bin/python tools/setup.py --install "/path/to/Empire Earth" [--kit DIR] [--link-only]
    .venv/bin/python tools/setup.py --install "/path/to/Empire Earth" --ghidra   # development

1. kit/tools/setup.py --link-only: checks Empire Earth.exe against game.toml
   and links the installation at original/gog/Empire Earth (ignored).
2. Checks every module game.toml names against its SHA-256 (PLAN.md §2.4).
3. Unless --link-only: writes the translation inputs of every module into
   analysis/kit/<module>/ (ignored): functions.tsv, functions/<va>.asm and
   funcdb.json, from the function DB (db/functions/) and the player's own
   binaries (tools/inputs/export.py, about 15 s), checked against the digests
   db/inputs.json pins. No Ghidra is involved.

   --ghidra exports the same files from the func-db Ghidra projects instead
   (tools/ghidra/ghidra_run.py kit, docs/reference/function-db.md §9). They
   must exist under $FUNCDB_WORK (default analysis/func-db; tools/ghidra/regen.py
   builds them in about 20 minutes), and --funcdb-python names the PyGhidra
   environment. `tools/inputs/export.py --compare analysis/kit` checks the
   two against each other.

Nothing is downloaded and no game file is changed.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "inputs"))
import kitpath  # noqa: E402

ROOT = kitpath.ROOT
# game.toml module -> the func-db slug (tools/ghidra/common.py MODULES)
SLUGS = {"Empire Earth.exe": "exe", "Low-Level Engine.dll": "engine", "sxlrt233.dll": "sxlrt233",
         "Default.dll": "default", "Language.dll": "language", "DX7HRDisplay.dll": "dx7hr",
         "DX7HRTnLDisplay.dll": "dx7hrtnl", "TCP Protocol.dll": "tcp",
         "Miles Sound System Mixer.dll": "milesmix"}


def modules(cfg):
    """(file name, path, sha256, listings dir) for the executable and every module."""
    yield (cfg["game"]["executable"], cfg["developer_exe_path"], cfg["game"]["sha256"], cfg["listings_path"])
    for m in cfg["aux_modules"]:
        yield (m["name"], m["path"], m["sha256"], m["listings_path"])


def export_with_ghidra(cfg, funcdb_python):
    """The development path: listings from the func-db Ghidra projects."""
    if not funcdb_python.is_file():
        sys.exit("No func-db environment at %s; see docs/reference/function-db.md §9" % funcdb_python)
    work = Path(os.environ.get("FUNCDB_WORK", ROOT / "analysis/func-db")).resolve()  # Ghidra needs absolute paths
    for name, _, _, listings in modules(cfg):
        slug = SLUGS[name]
        if not (work / "ghidra" / slug / ("%s.gpr" % slug)).is_file():
            sys.exit("No func-db Ghidra project for %s under %s: run tools/ghidra/regen.py %s "
                     "(or set FUNCDB_WORK)" % (name, work, slug))
        db = ROOT / "db/functions" / (name.lower().replace(" ", "-") + ".json")
        subprocess.run([str(funcdb_python), str(ROOT / "tools/ghidra/ghidra_run.py"), "kit", slug,
                        str(db), str(listings)], cwd=ROOT, check=True,
                       env=dict(os.environ, FUNCDB_WORK=str(work)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install", type=Path, required=True, help="your Empire Earth installation (GOG)")
    parser.add_argument("--link-only", action="store_true", help="link and check, export nothing")
    parser.add_argument("--ghidra", action="store_true",
                        help="export the listings from the func-db Ghidra projects (development)")
    parser.add_argument("--funcdb-python", type=Path, default=ROOT / "analysis/func-db/.venv/bin/python",
                        help="with --ghidra: the func-db environment (PyGhidra; function-db.md §9)")
    args, rest = parser.parse_known_args()
    kit, rest = kitpath.take_kit(rest)
    if rest:
        parser.error("unrecognized arguments: %s" % " ".join(rest))
    link = [sys.executable, str(kit / "tools/setup.py"), "--game-dir", str(ROOT),
            "--install", str(args.install), "--link-only"]
    if subprocess.call(link, cwd=ROOT):
        return 1
    sys.path.insert(0, str(kit / "tools"))
    import game_config
    cfg = game_config.load(ROOT)
    for name, path, sha256, _ in modules(cfg):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if digest != sha256:
            sys.exit("%s: SHA-256 %s, expected %s (PLAN.md §2.4)" % (path, digest, sha256))
    print("All %d modules match game.toml." % (1 + len(cfg["aux_modules"])))
    if args.link_only:
        return 0
    if args.ghidra:
        export_with_ghidra(cfg, args.funcdb_python)
    else:
        import export
        export.export_all(cfg)
    print("Translation inputs ready in analysis/kit/. Next: tools/run.py (or tools/build.py --regenerate ...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
