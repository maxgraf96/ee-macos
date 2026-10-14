#!/usr/bin/env python3
"""The M1 matrix on the Mac: fetch capture packs from legion, replay every case, report.

    .venv/bin/python tools/m1/run_matrix.py [--no-fetch] [--refetch] [--cases S00,A01,...]
            [--build bundles|linked|both] [--no-build] [--kit DIR] [--jobs N] [--lenient]
            [--captures DIR] [--report FILE]

1. Fetch (unless --no-fetch). Lists C:\\EEPort\\captures\\rmg on legion (`ssh legion "dir ..."`,
   nothing else runs there) and copies, with scp, every case whose packs\\ directory is
   finished - it holds index.tsv, which rmg_pack.py writes last - into
   <captures>/<case>/packs/, unless that is already here (--refetch replaces it). A
   replay-win.txt beside packs\\ (rmg_replay.exe's output on legion) comes along when present.
2. Replay. Builds ee_m1_replay and ee_m1_replay_static (unless --no-build) and replays each
   case's packs with the chosen builds (default both), strict mode on:
   <captures>/<case>/replay-<build>.tsv (one row per pack) and diff-<build>/ (expected and
   replayed bytes of every region that differs).
3. Report: <captures>/report.md (default analysis/m1-captures/report.md): per case, per
   call (case x export), and every call that did not pass with its first difference.

Exit status: 0 when every replayed pack passed on every build, 1 otherwise (or when there
was nothing to replay). Captures and packs hold game-derived bytes: they stay under
analysis/, which git ignores. docs/reference/m1-replay.md is the manual.
"""
import argparse
import concurrent.futures
import csv
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECOMP = ROOT / "build" / "recomp"
HOST = "legion"
REMOTE = r"C:\EEPort\captures\rmg"
# rmg.md section 2.3: the exports in pipeline order.
EXPORTS = ["SetRandomMapValues", "GeneratePlayerPositions", "GenerateNeutralPositions",
           "CreatePlayerLandWaterMap", "CreateNeutralLandWaterMap", "CreateFlatMap", "CreateHeightMap",
           "GenerateShallows", "FilterHeightMap", "GenerateResources", "CreateTrees"]
SHORT = {"SetRandomMapValues": "Values", "GeneratePlayerPositions": "PlayerPos",
         "GenerateNeutralPositions": "NeutralPos", "CreatePlayerLandWaterMap": "PlayerLW",
         "CreateNeutralLandWaterMap": "NeutralLW", "CreateFlatMap": "Flat", "CreateHeightMap": "Height",
         "GenerateShallows": "Shallows", "FilterHeightMap": "Filter", "GenerateResources": "Resources",
         "CreateTrees": "Trees"}
RANK = {"PASS": 0, "MISMATCH": 1, "ERROR": 2}


def ssh_dir(path):
    """`dir /b` of a legion directory: its entries, or None when it does not exist."""
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", HOST, 'dir /b "%s"' % path],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        return None
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def fetch(captures, wanted, refetch):
    cases = ssh_dir(REMOTE)
    if cases is None:
        print("legion: %s does not exist or cannot be listed; nothing fetched" % REMOTE)
        return
    for case in sorted(cases):
        if wanted and case not in wanted:
            continue
        remote = "%s\\%s" % (REMOTE, case)
        entries = ssh_dir(remote + "\\packs")
        if not entries or "index.tsv" not in entries:
            print("legion: %s has no finished packs\\ (no index.tsv); skipped" % case)
            continue
        local = captures / case
        if (local / "packs" / "index.tsv").is_file() and not refetch:
            print("%s: packs already here (--refetch replaces them)" % case)
            continue
        local.mkdir(parents=True, exist_ok=True)
        if (local / "packs").exists():
            for p in (local / "packs").iterdir():
                p.unlink()
        src = "%s:%s/%s/packs" % (HOST, REMOTE.replace("\\", "/"), case)
        print("%s: fetching %d files" % (case, len(entries)))
        subprocess.run(["scp", "-q", "-r", src, str(local)], check=True, timeout=3600)
        root = ssh_dir(remote) or []
        if "replay-win.txt" in root:
            subprocess.run(["scp", "-q", "%s:%s/%s/replay-win.txt" % (HOST, REMOTE.replace("\\", "/"), case),
                            str(local)], check=True, timeout=600)


def builds(selected):
    out = []
    if selected in ("bundles", "both"):
        out.append(("bundles", [str(RECOMP / "ee_m1_replay"), "--bundles", str(RECOMP / "bundles")]))
    if selected in ("linked", "both"):
        out.append(("linked", [str(RECOMP / "ee_m1_replay_static")]))
    return out


def replay_case(case_dir, build, cmd, lenient):
    packs = sorted((case_dir / "packs").glob("*.rrp"))
    tsv = case_dir / ("replay-%s.tsv" % build)
    if tsv.exists():
        tsv.unlink()
    args = cmd + ["--results", str(tsv), "--diff-dir", str(case_dir / ("diff-" + build))]
    args += ["--lenient"] if lenient else []
    r = subprocess.run(args + [str(p) for p in packs], capture_output=True, text=True)
    (case_dir / ("replay-%s.log" % build)).write_text(r.stdout + r.stderr)
    return r.returncode


def read_results(tsv):
    if not tsv.is_file():
        return []
    with open(tsv, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_windows(case_dir):
    """rmg_replay.exe's PASS/FAIL/ERROR lines, by seq, when the case has them."""
    out = {}
    p = case_dir / "replay-win.txt"
    if p.is_file():
        for line in p.read_text(errors="replace").splitlines():
            m = re.match(r"(PASS|FAIL|ERROR) seq=(\d+)", line)
            if m:
                out[int(m.group(2))] = "MISMATCH" if m.group(1) == "FAIL" else m.group(1)
    return out


def git_head(path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or "?"


def short_export(name):
    return name.replace("EERMPIM", "")


def write_report(path, captures, cases, build_names, kit):
    rows, calls, worst = [], [], 0
    for case in cases:
        d = captures / case
        npacks = len(list((d / "packs").glob("*.rrp")))
        win = read_windows(d)
        per_build = {b: read_results(d / ("replay-%s.tsv" % b)) for b in build_names}
        merged = {}
        for b, results in per_build.items():
            for r in results:
                key = Path(r["pack"]).name
                m = merged.setdefault(key, {"seq": int(r["seq"]), "gen": int(r["gen"]), "attempt": int(r["attempt"]),
                                            "export": short_export(r["export"]), "status": {}, "detail": {}})
                m["status"][b] = r["status"]
                m["detail"][b] = r["detail"]
        counts = {"PASS": 0, "MISMATCH": 0, "ERROR": 0}
        for key in sorted(merged, key=lambda k: merged[k]["seq"]):
            m = merged[key]
            status = max(m["status"].values(), key=lambda s: RANK.get(s, 2)) if m["status"] else "ERROR"
            missing = [b for b in build_names if b not in m["status"]]
            if missing:
                status = "ERROR"
            counts[status if status in counts else "ERROR"] += 1
            m["overall"] = status
            m["windows"] = win.get(m["seq"], "")
            calls.append((case, key, m))
            worst = max(worst, RANK.get(status, 2))
        win_pass = sum(1 for s in win.values() if s == "PASS")
        rows.append((case, npacks, counts, ("%d/%d" % (win_pass, len(win))) if win else "-", merged))
    total = {k: sum(r[2][k] for r in rows) for k in ("PASS", "MISMATCH", "ERROR")}
    npacks = sum(r[1] for r in rows)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = ["# M1 replay matrix (Mac)", "",
           "Generated %s by `tools/m1/run_matrix.py`. ee-macos %s, kit %s; builds: %s; strict mode."
           % (now, git_head(ROOT), kit, ", ".join(build_names)), "",
           "M1 passes when every pack of at least 50 cases passes on Windows (`rmg_replay.exe`, the "
           "completeness check) and on the Mac on every build (docs/phase2.md, docs/reference/rmg.md "
           "section 8). Captures: `%s`." % (os.path.relpath(captures, ROOT) if captures.is_relative_to(ROOT)
                                              else captures), "",
           "**%d cases, %d packs: %d pass, %d mismatch, %d error.**" % (len(rows), npacks, total["PASS"],
                                                                       total["MISMATCH"], total["ERROR"]), ""]
    if not rows:
        out += ["No captures yet: nothing was replayed.", ""]
    out += ["## Cases", "", "| Case | Packs | Pass | Mismatch | Error | Windows pass |", "|---|---:|---:|---:|---:|---:|"]
    for case, n, c, win, _ in rows:
        out.append("| %s | %d | %d | %d | %d | %s |" % (case, n, c["PASS"], c["MISMATCH"], c["ERROR"], win))
    out += ["", "## Calls (case x export)", "",
            "Each cell: calls that passed on every build / calls replayed (a case runs each export once per "
            "attempt); `M` marks a mismatch, `E` an error.", "",
            "| Case | " + " | ".join(SHORT[e] for e in EXPORTS) + " |", "|---|" + "---:|" * len(EXPORTS)]
    for case, _, _, _, merged in rows:
        cells = []
        for e in EXPORTS:
            ms = [m for m in merged.values() if m["export"] == e]
            if not ms:
                cells.append("")
                continue
            ok = sum(1 for m in ms if m["overall"] == "PASS")
            mark = "".join(sorted({"M" if m["overall"] == "MISMATCH" else "E" for m in ms if m["overall"] != "PASS"}))
            cells.append("%d/%d%s" % (ok, len(ms), " " + mark if mark else ""))
        out.append("| %s | %s |" % (case, " | ".join(cells)))
    # A call Windows did not replay is not complete evidence either, whatever the Mac says.
    bad = [(case, key, m) for case, key, m in calls
           if m["overall"] != "PASS" or (m["windows"] and m["windows"] != "PASS")]
    out += ["", "## Calls that did not pass", ""]
    if not bad:
        out.append("None." if rows else "None (nothing replayed).")
    else:
        out += ["The detail is the replay's own line: the region, its label, the first differing offset and the "
                "dword expected vs replayed; or the first diverging allocator or file event; or the error. "
                "`diff-<build>/` in the case directory holds the expected and replayed bytes of each region "
                "that differs (`<seq>-<region>-expected.bin`, `-replay.bin`).", "",
                "| Case | Pack | seq | gen | att | Export | Windows | " + " | ".join(build_names) + " | Detail |",
                "|---|---|---:|---:|---:|---|---|" + "---|" * len(build_names) + "---|"]
        for case, key, m in bad:
            detail = next((m["detail"][b] for b in build_names if m["status"].get(b) != "PASS" and m["detail"].get(b)),
                          "the Mac passed; the Windows replay did not (see replay-win.txt)")
            out.append("| %s | %s | %d | %d | %d | %s | %s | %s | %s |" % (
                case, key, m["seq"], m["gen"], m["attempt"], m["export"], m["windows"] or "-",
                " | ".join(m["status"].get(b, "-") for b in build_names), detail.replace("|", "/")))
    path.write_text("\n".join(out) + "\n")
    return worst, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captures", type=Path, default=ROOT / "analysis/m1-captures")
    ap.add_argument("--report", type=Path, default=None, help="default <captures>/report.md")
    ap.add_argument("--cases", default=None, help="comma-separated case names (default: all)")
    ap.add_argument("--no-fetch", action="store_true", help="replay what is already here")
    ap.add_argument("--refetch", action="store_true", help="fetch cases already here again")
    ap.add_argument("--build", choices=("bundles", "linked", "both"), default="both")
    ap.add_argument("--no-build", action="store_true", help="use what is already built")
    ap.add_argument("--kit", default=None)
    ap.add_argument("--jobs", type=int, default=4, help="cases replayed at once")
    ap.add_argument("--lenient", action="store_true", help="without strict mode")
    args = ap.parse_args()
    captures = args.captures.resolve()
    captures.mkdir(parents=True, exist_ok=True)
    wanted = set(args.cases.split(",")) if args.cases else None
    if not args.no_fetch:
        fetch(captures, wanted, args.refetch)
    if not args.no_build:
        cmd = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_m1_replay",
               "--cmake-target", "ee_m1_replay_static"] + (["--kit", args.kit] if args.kit else [])
        subprocess.run(cmd, check=True)
    cases = sorted(d.name for d in captures.iterdir()
                   if d.is_dir() and any((d / "packs").glob("*.rrp")) and (not wanted or d.name in wanted))
    chosen = builds(args.build)
    jobs = [(case, b, cmd) for case in cases for b, cmd in chosen]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(replay_case, captures / case, b, cmd, args.lenient): (case, b) for case, b, cmd in jobs}
        for f in concurrent.futures.as_completed(futures):
            case, b = futures[f]
            rc = f.result()
            print("%-8s %-8s %s" % (case, b, {0: "all passed", 1: "mismatch"}.get(rc, "error (exit %d)" % rc)))
    kit = args.kit or str(ROOT / "kit")
    report = args.report or captures / "report.md"
    worst, n = write_report(report, captures, cases, [b for b, _ in chosen], git_head(kit))
    print("report: %s (%d cases)" % (report, n))
    return 0 if n and worst == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
