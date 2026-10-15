#!/usr/bin/env python3
"""Run the single-player checksum-stream scenario on the Mac (P4-C).

    .venv/bin/python tools/simparity/run.py [--runs N] [--minutes M] [--seed S]
        [--ptps P] [--reports] [--units PT,PT...] [--units-every N]
        [--oosdump PT] [--max-pt N] [--lockstep] [--no-civ-timer] [--out DIR]
        [--no-build] [--kit DIR]
        [--insert-dylib PATH]

Each run boots build/recomp/ee_boot headless with a fresh user directory,
drives tests/menus/simparity.script (Single Player -> Play Random Map -> Start
Game; nobody at the controls but for OK on the civilization screen the game
opens 5 game minutes in, then M more minutes) and writes the world-checksum
stream of the simparity mod (mods/simparity/) to OUT/mac-<k>.csv. The
registry is tools/boot/seed_user.py's (legion's options) with the PT rate
pinned: Min PTs per Second = Max PTs per Second = P, so the step size does
not follow the measured tick time. With two runs or more, the streams are
compared (compare.py) and the exit status says whether they agree.

Every run holds tools/game_ports.py's lock: a random map opens the game's
server on fixed ports. --insert-dylib (or $SIMPARITY_INSERT_DYLIB) is put in
DYLD_INSERT_LIBRARIES of the game, e.g. a crash-exit shim on a machine whose
crash reporter hangs. Streams stay out of git: the default OUT is
analysis/simparity/mac/ (ignored). docs/reference/simparity.md.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_user  # noqa: E402
from game_ports import game_ports_lock  # noqa: E402
import compare  # noqa: E402

SCRIPT = ROOT / "tests/menus/simparity.script"
HKCU = "HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"
# Booting, the menus, the map's creation and the civilization screen (5 game
# minutes in, about 200 s at 40 PT/s) before the timed part of the game.
START_SECONDS = 600


def pin_registry(user, ptps):
    reg_path = user / "registry.json"
    reg = json.loads(reg_path.read_text())
    for name in ("Min PTs per Second", "Max PTs per Second"):
        reg[HKCU][name] = {"type": seed_user.REG_DWORD, "data": ptps}
    reg_path.write_text(json.dumps(reg, indent=1))


def scenario(minutes, tmp, no_civ_timer=False):
    text = SCRIPT.read_text()
    text, n = re.subn(r"^wait \d+(\s+# simparity: duration.*)$",
                      lambda m: "wait %d%s" % (int(minutes * 60000), m.group(1)), text, flags=re.M)
    if n != 1:
        sys.exit("simparity.script has no '# simparity: duration' wait")
    if no_civ_timer:
        text, n = re.subn(r"^# simparity: civ begin.*?^# simparity: civ end[^\n]*\n", "", text,
                          flags=re.M | re.S)
        if n != 1:
            sys.exit("simparity.script has no '# simparity: civ begin/end' block")
    path = tmp / "simparity.script"
    path.write_text(text)
    return path


def run_one(k, args, out):
    tmp = Path(tempfile.mkdtemp(prefix="ee-simparity-%d-" % k))
    user = tmp / "user"
    frames = out / ("mac-%d-frames" % k)
    trace = out / ("mac-%d.csv" % k)
    log = out / ("mac-%d.log" % k)
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    try:
        seed_user.main(["--user", str(user), "--width", "800", "--height", "600"])
        pin_registry(user, args.ptps)
        script = scenario(args.minutes, tmp, args.no_civ_timer)
        seconds = int(START_SECONDS + args.minutes * 60)
        # The mod is copied per run, so a rebuild cannot touch a running game's plugin.
        mods = tmp / "ee-mods"
        shutil.copytree(ROOT / "build/recomp/ee-mods", mods)
        env = dict(os.environ, RECOMP_STRICT="1", RECOMP_EE_CHECKSUM_TRACE=str(trace),
                   RECOMP_EE_SIMPARITY_SEED=str(args.seed), RECOMP_MODS_DIR=str(mods))
        if not args.reports:
            env["RECOMP_EE_SIMPARITY_NO_REPORTS"] = "1"
        if args.lockstep:
            env["RECOMP_EE_SIMPARITY_LOCKSTEP"] = "1"
        if args.no_civ_timer:
            env["RECOMP_EE_SIMPARITY_NO_CIV_TIMER"] = "1"
        for key, val in (("RECOMP_EE_CHECKSUM_UNITS", args.units),
                         ("RECOMP_EE_CHECKSUM_UNITS_EVERY", args.units_every),
                         ("RECOMP_EE_CHECKSUM_OOSDUMP", args.oosdump),
                         ("RECOMP_EE_CHECKSUM_MAX_PT", args.max_pt),
                         ("RECOMP_EE_CHECKSUM_FLAGS", args.flags)):
            if val is not None:
                env[key] = str(val)
        command = [str(ROOT / "build/recomp/ee_boot"), "--user", str(user), "--seconds", str(seconds),
                   "--script", str(script), "--script-out", str(frames)]
        prefix = ["nice", "-n", "10", "env"]
        if args.insert_dylib:
            prefix.append("DYLD_INSERT_LIBRARIES=" + args.insert_dylib)
        prefix += ["timeout", str(seconds + 180)]
        with game_ports_lock("simparity run %d (%s)" % (k, ROOT)):
            start = time.monotonic()
            with open(log, "w") as f:
                result = subprocess.run(prefix + command, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                                        env=env)
            took = time.monotonic() - start
        text = log.read_text(errors="replace")
        problems = []
        if result.returncode != 0:
            problems.append("exit code %d" % result.returncode)
        if "[script] finished:" not in text:
            failed = re.search(r"\[script\] FAILED (.*)", text)
            problems.append("the script did not finish%s" % (": " + failed.group(1) if failed else ""))
        for pattern in (r"\[recomp\] strict:.*", r".*guest memory fault.*"):
            m = re.search(pattern, text)
            if m:
                problems.append(m.group(0))
        if args.oosdump is not None:
            for dump in user.rglob("OOS *.log"):
                shutil.copy(dump, out / ("mac-%d-%s" % (k, dump.name.replace(" ", "_"))))
        stream = compare.load(trace) if trace.exists() else None
        print("run %d: %.0f s, %s; %s" % (k, took, "ok" if not problems else "; ".join(problems),
              compare.describe(stream) if stream else "no stream"), flush=True)
        return trace, problems
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--minutes", type=float, default=5.0, help="game time with nobody at the controls")
    parser.add_argument("--seed", type=int, default=1000, help="the game seed (EEMStartLoading)")
    parser.add_argument("--ptps", type=int, default=40, help="PTs per second, pinned (registry)")
    parser.add_argument("--reports", action="store_true",
                        help="keep the wall-clock statistics reports (EXE 0x547365)")
    parser.add_argument("--lockstep", action="store_true",
                        help="the server waits for the simulation before each CT (simparity.md §4.3)")
    parser.add_argument("--no-civ-timer", action="store_true",
                        help="no civilization timer, no civ screen, no click (simparity.md §4.3)")
    parser.add_argument("--units", default=None, help="per-unit rows after these PTs (comma list)")
    parser.add_argument("--units-every", type=int, default=None)
    parser.add_argument("--oosdump", type=int, default=None, help="the game's OOS dump after this PT")
    parser.add_argument("--max-pt", type=int, default=None)
    parser.add_argument("--flags", default=None, help="flags of the second checksum column")
    parser.add_argument("--out", type=Path, default=ROOT / "analysis/simparity/mac")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--kit", default=None)
    parser.add_argument("--insert-dylib", default=os.environ.get("SIMPARITY_INSERT_DYLIB"))
    args = parser.parse_args()
    if not args.no_build:
        command = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_boot"]
        command += ["--kit", args.kit] if args.kit else []
        subprocess.run(command, check=True)
    args.out.mkdir(parents=True, exist_ok=True)
    traces, failed = [], 0
    for k in range(1, args.runs + 1):
        trace, problems = run_one(k, args, args.out)
        traces.append(trace)
        failed += bool(problems)
    if failed:
        return 2
    if len(traces) >= 2:
        same = True
        for other in traces[1:]:
            report = compare.compare(compare.load(traces[0]), compare.load(other))
            print("%s vs %s: %s" % (traces[0].name, other.name, report["summary"]))
            same &= report["agree"]
        return 0 if same else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
