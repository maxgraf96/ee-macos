#!/usr/bin/env python3
"""Check a code_census.js capture from legion against the function DB.

    python3 -I tools/ghidra/legion/check_census.py <capture dir or code_census.json> [--json out.json]

For every executed basic block (module + RVA + length):
  - in_function   the block lies inside a DB function body (chunks)          -> expected
  - at_entry      the block starts at a DB function start                     -> counts executed functions
  - outside       the block starts in no DB function body                     -> a DB gap: report it
  - drm           the block lies in .cms_t/.cms_d                             -> must be zero
The report also lists which tags executed (game / lib:* / drm) and how many functions of
each kind ran. Needs only the DB (no game files).
"""
import argparse
import json
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "db/functions"


def db_for(module):
    return DB / (module.lower().replace(" ", "-") + ".json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture")
    ap.add_argument("--json")
    args = ap.parse_args()
    p = Path(args.capture)
    if p.is_dir():
        p = p / "code_census.json"
    cap = json.loads(p.read_text())
    report = {}
    for mod, info in cap["modules"].items():
        path = db_for(mod)
        if not path.exists() or "blocks" not in info:
            continue
        db = json.loads(path.read_text())
        ranges = []
        for f in db["functions"]:
            chunks = f.get("chunks") or [[f["rva"], hex(int(f["rva"], 16) + f["size"])]]
            for a, b in chunks:
                ranges.append((int(a, 16), int(b, 16), f))
        ranges.sort(key=lambda r: r[0])
        starts = [r[0] for r in ranges]
        entries = {int(f["rva"], 16): f for f in db["functions"]}
        drm = [(int(s["rva"], 16), int(s["rva"], 16) + int(s["vsize"], 16))
               for s in db["module"]["sections"] if s["name"] in (".cms_t", ".cms_d")]
        c = Counter()
        tags = Counter()
        kinds = Counter()
        executed = set()
        outside = []
        for rva, ln in info["blocks"]:
            if any(lo <= rva < hi for lo, hi in drm):
                c["drm"] += 1
            i = bisect_right(starts, rva) - 1
            if i >= 0 and rva < ranges[i][1]:
                c["in_function"] += 1
                f = ranges[i][2]
                if f["rva"] not in executed:
                    executed.add(f["rva"])
                    tags[f["tag"]] += 1
                    kinds[f["kind"]] += 1
            else:
                c["outside"] += 1
                outside.append(hex(rva))
            if rva in entries:
                c["at_entry"] += 1
        report[mod] = {"blocks": len(info["blocks"]), **c, "functions_executed": len(executed),
                       "functions_in_db": len(db["functions"]), "tags_executed": dict(tags),
                       "kinds_executed": dict(kinds), "outside_first": outside[:50]}
        print("%-32s blocks %6d  in-function %6d  outside %4d  drm %d  functions run %5d/%d  %s"
              % (mod, len(info["blocks"]), c["in_function"], c["outside"], c["drm"], len(executed),
                 len(db["functions"]), dict(tags)))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n")
    bad = sum(r.get("outside", 0) + r.get("drm", 0) for r in report.values())
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
