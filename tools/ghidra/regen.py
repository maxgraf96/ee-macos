#!/usr/bin/env python3
"""Regenerate the function DB from scratch (or just rebuild it from existing facts).

    analysis/func-db/.venv/bin/python tools/ghidra/regen.py              # everything
    analysis/func-db/.venv/bin/python tools/ghidra/regen.py exe engine   # some modules
    analysis/func-db/.venv/bin/python tools/ghidra/regen.py --db-only    # facts -> DB only

Steps per module:
  1. ghidra_run.py analyze      import at the preferred base, setup, auto-analysis
  2. loop (max --rounds):       export facts -> build_db.py --seeds-only -> ghidra_run.py seed
                                until no evidence-backed start is missing
  3. ghidra_run.py export, build_db.py   final facts and db/functions/<module>.json
  4. build_db.py --summary      cross-module summary for docs/reference/function-db.md

Every step is deterministic for a given Ghidra version and game build; timings on an
M4 Max: EXE ~10 min in total, all DLLs ~3 min.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402

PY = sys.executable


def run(*args, mem=None):
    env = dict(os.environ)
    if mem:
        env["GHIDRA_MAXMEM"] = mem
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([PY, *map(str, args)], check=True, env=env)


def seeds_pending(slug):
    p = common.WORK / "work" / ("seeds-%s.json" % slug)
    return len(json.loads(p.read_text())) if p.exists() else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*")
    ap.add_argument("--db-only", action="store_true", help="skip Ghidra; rebuild the DB from facts")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--maxmem", default=os.environ.get("GHIDRA_MAXMEM", "8G"))
    args = ap.parse_args()
    mods = common.select(args.modules)
    gr = HERE / "ghidra_run.py"
    bd = HERE / "build_db.py"
    if not args.db_only:
        run(gr, "analyze", *mods, mem=args.maxmem)
        for slug in mods:
            for rnd in range(args.rounds):
                run(gr, "export", slug, mem=args.maxmem)
                run("-I", bd, "--seeds-only", slug)
                n = seeds_pending(slug)
                print("[regen] %s round %d: %d seeds" % (slug, rnd + 1, n), flush=True)
                if n == 0:
                    break
                run(gr, "seed", slug, common.WORK / "work" / ("seeds-%s.json" % slug),
                    "--accept", "strong,orphan_code,validated", mem=args.maxmem)
                rep = json.loads((common.WORK / "work" / ("seed-%s-report.json" % slug)).read_text())
                if rep["created"] == 0:
                    print("[regen] %s: seeds left that Ghidra cannot place: %s"
                          % (slug, rep["failed_list"]), flush=True)
                    run(gr, "export", slug, mem=args.maxmem)
                    break
            else:
                run(gr, "export", slug, mem=args.maxmem)
    run("-I", bd, *mods)
    run("-I", bd, "--summary")


if __name__ == "__main__":
    main()
