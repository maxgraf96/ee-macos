#!/usr/bin/env python3
"""db/gog-files.json: the GOG Empire Earth Gold base game's files, which the
first-run importer checks a player's copy against (docs/reference/firstrun.md §2).

    .venv/bin/python tools/firstrun/manifest.py --from "/path/to/Empire Earth Gold"

Development only. Reads the install's own goggame-1207658777.hashdb (GOG's
MD5 of every installed file), keeps the entries under "Empire Earth\\" (the
base game; The Art of Conquest and the PDFs are not needed), checks every one
against the file on disk and records its size. The manifest holds paths,
sizes and hashes only: no game bytes. The nine modules' SHA-256 stay in
game.toml; the manifest must agree with them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  (sys.path for the repository's tools)
import gogdb  # noqa: E402

ROOT = HERE.parents[1]
MANIFEST = ROOT / "db/gog-files.json"
BASE = "Empire Earth"


def build(gold):
    gold = Path(gold)
    db = gogdb.read_hashdb(gold / gogdb.HASHDB)
    if db is None:
        sys.exit("%s: no readable %s" % (gold, gogdb.HASHDB))
    info = gogdb.read_info(gold / gogdb.INFO) or {}
    prefix = BASE + "\\"
    files = []
    for name, md5 in sorted(db.items(), key=lambda kv: kv[0].lower()):
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):].replace("\\", "/")
        path = gold / BASE / rel
        data = path.read_bytes()
        if hashlib.md5(data).hexdigest() != md5:
            sys.exit("%s differs from the install's hashdb" % path)
        files.append([rel, len(data), md5])
    base = gold / BASE
    dirs = sorted(str(d.relative_to(base)) for d in base.rglob("*")
                  if d.is_dir() and not any(d.iterdir()))
    return {
        "about": "GOG Empire Earth Gold, base game: every file the installer puts in "
                 "\"Empire Earth\\\" (path relative to it, size, MD5 as GOG's own "
                 "goggame-%s.hashdb has it) and the empty folders it creates. "
                 "tools/firstrun/manifest.py writes it; the first-run importer checks a "
                 "player's copy against it (docs/reference/firstrun.md)." % gogdb.GAME_ID,
        "product": {k: info.get(k) for k in ("gameId", "name", "buildId")},
        "base": BASE,
        "bytes": sum(f[1] for f in files),
        "dirs": dirs,
        "files": files,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="gold", required=True, help="an installed Empire Earth Gold folder")
    ap.add_argument("--out", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)
    doc = build(args.gold)
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    # one file per line keeps diffs readable
    text = text.replace("[\n   ", "[").replace(",\n   ", ", ").replace("\n  ]", "]")
    args.out.write_text(text + "\n")
    print("%s: %d files, %d bytes, %d empty folders" % (args.out, len(doc["files"]), doc["bytes"], len(doc["dirs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
