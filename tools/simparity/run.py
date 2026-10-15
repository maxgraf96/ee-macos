#!/usr/bin/env python3
"""Run the single-player checksum-stream scenario on the Mac (P4-C).

    .venv/bin/python tools/simparity/run.py [--runs N] [--minutes M] [--seed S]
        [--ptps P] [--autosave-ms MS] [--reg [SUBKEY\\]NAME=N] [--reports] [--units PT,PT...] [--units-every N]
        [--oosdump PT] [--max-pt N] [--draws A-B] [--trace-fns F,..] [--mem PT,A,L;..]
        [--players N] [--calls F,..] [--memfn F,L;..] [--ai-sched] [--autosave-watch]
        [--trace-match TEXT]
        [--lockstep] [--no-civ-timer] [--against STREAM] [--out DIR]
        [--load SAVE] [--keep-saves] [--no-build] [--kit DIR]
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

--load SAVE starts from a saved game instead (tests/menus/simparity-load.script:
Single Player -> Play Saved Game -> Load, with SAVE alone in the user
directory's Data\\Saved Games); --minutes then counts from the load.
--keep-saves copies the saves the game wrote to OUT/mac-<k>-saves/.

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
LOAD_SCRIPT = ROOT / "tests/menus/simparity-load.script"
HKCU = "HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"
SAVE_TO_FILE = "00540aca"  # writes a save game; 0 = done (simparity.md §8)
# Booting, the menus, the map's creation and the civilization screen (5 game
# minutes in, about 200 s at 40 PT/s) before the timed part of the game.
START_SECONDS = 600


def pin_registry(user, ptps, autosave_ms=None, extra=()):
    """The PT rate pinned, and the other values asked for: NAME=VALUE under the game's key,
    or SUBKEY\\NAME=VALUE (e.g. 'Game Options\\Difficulty Level=0'); a number is a DWORD,
    anything else a string ('Game Options\\Map Type=Small Islands')."""
    reg_path = user / "registry.json"
    reg = json.loads(reg_path.read_text())
    for name in ("Min PTs per Second", "Max PTs per Second"):
        reg[HKCU][name] = {"type": seed_user.REG_DWORD, "data": ptps}
    if autosave_ms is not None:
        reg[HKCU]["AutoSave In Milliseconds"] = {"type": seed_user.REG_DWORD, "data": autosave_ms}
    for item in extra:
        path, _, value = item.partition("=")
        sub, _, name = path.rpartition("\\")
        key = HKCU + ("\\" + sub if sub else "")
        try:
            entry = {"type": seed_user.REG_DWORD, "data": int(value, 0)}
        except ValueError:
            entry = {"type": seed_user.REG_SZ, "data": value}
        reg.setdefault(key, {})[name] = entry
    reg_path.write_text(json.dumps(reg, indent=1))


def scenario(minutes, tmp, no_civ_timer=False, load=False):
    text = (LOAD_SCRIPT if load else SCRIPT).read_text()
    text, n = re.subn(r"^wait \d+(\s+# simparity: duration.*)$",
                      lambda m: "wait %d%s" % (int(minutes * 60000), m.group(1)), text, flags=re.M)
    if n != 1:
        sys.exit("simparity.script has no '# simparity: duration' wait")
    if no_civ_timer and not load:
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
        pin_registry(user, args.ptps, args.autosave_ms, args.reg)
        if args.load:
            # The game lists Data\\Saved Games (the overlay's writable tier is the user directory).
            saved = user / "Data" / "Saved Games"
            saved.mkdir(parents=True, exist_ok=True)
            shutil.copy(args.load, saved / args.load.name)
        script = scenario(args.minutes, tmp, args.no_civ_timer, bool(args.load))
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
        if args.ai_sched:
            env["RECOMP_EE_SIMPARITY_AI_SCHED"] = "1"
        if args.autosave_watch:
            env["RECOMP_EE_SIMPARITY_AUTOSAVE"] = "1"
        for key, val in (("RECOMP_EE_CHECKSUM_UNITS", args.units),
                         ("RECOMP_EE_CHECKSUM_UNITS_EVERY", args.units_every),
                         ("RECOMP_EE_CHECKSUM_OOSDUMP", args.oosdump),
                         ("RECOMP_EE_CHECKSUM_MAX_PT", args.max_pt),
                         ("RECOMP_EE_CHECKSUM_FLAGS", args.flags),
                         ("RECOMP_EE_CHECKSUM_DRAWS", args.draws),
                         ("RECOMP_EE_CHECKSUM_TRACE_FNS", args.trace_fns),
                         ("RECOMP_EE_CHECKSUM_MEM", args.mem),
                         ("RECOMP_EE_SIMPARITY_PLAYERS", args.players),
                         ("RECOMP_EE_SIMPARITY_CALLS", args.calls),
                         ("RECOMP_EE_SIMPARITY_MEMFN", args.memfn)):
            if val is not None:
                env[key] = str(val)
        command = [str(ROOT / "build/recomp/ee_boot"), "--user", str(user), "--seconds", str(seconds),
                   "--script", str(script), "--script-out", str(frames)]
        if args.trace_match:
            command += ["--trace", str(out / ("mac-%d-imports.txt" % k)), "--trace-match", args.trace_match]
        prefix = ["nice", "-n", "10", "env"]
        if args.insert_dylib:
            prefix.append("DYLD_INSERT_LIBRARIES=" + args.insert_dylib)
        prefix += ["timeout", str(seconds + 180)]
        with game_ports_lock("simparity run %d (%s)" % (k, ROOT), timeout=4 * 3600):
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
        saves = [c for c in (stream.calls if stream else []) if c["fn"] == SAVE_TO_FILE]
        if stream:
            problems += save_problems(stream, args.ptps,
                                      args.autosave_ms if "540aca" in (args.calls or "") else None)
        files = [f for f in user.rglob("*") if f.is_file() and f.parent.name.lower() == "saved games"
                 and not (args.load and f.name == args.load.name)]
        if args.keep_saves and files:
            kept = out / ("mac-%d-saves" % k)
            shutil.rmtree(kept, ignore_errors=True)
            kept.mkdir()
            for f in files:
                shutil.copy2(f, kept / f.name)
        if saves or files:
            print("run %d: %d saves, %d files in Saved Games" % (k, len(saves), len(files)), flush=True)
        print("run %d: %.0f s, %s; %s" % (k, took, "ok" if not problems else "; ".join(problems),
              compare.describe(stream) if stream else "no stream"), flush=True)
        return trace, problems
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# The tracer's autosave watch (--autosave-watch): the host's counter restarting when the save
# is posted (0x547676), and the counter past the interval with nothing posted (a blocking flag).
POSTED = re.compile(r"# autosave: counter restarted after PT (\d+)")
UNPOSTED = re.compile(r"# autosave: due and not posted after PT (\d+)")
# A posted autosave is handled by the simulation thread straight away and runs at the end of
# the CT that carries it: a few CTs later (14 PTs each at 40 PT/s), or after a longer server
# pause. A post closer than this to the stream's end may not have run yet.
SAVE_LAG_PTS = 400


def save_problems(stream, ptps, autosave_ms=None):
    """A save the game could not write skips its side effects on the simulation (the computer
    players' serializer reschedules their strategy review): every save must succeed, as on
    Windows. K rows of the save-to-file function (0x540aca) say so. With the autosave watch,
    every autosave posted must also have run, and none may be due unposted; without it, and
    with the interval known, fewer saves than were due is a problem."""
    saves = [c for c in stream.calls if c["fn"] == SAVE_TO_FILE]
    out = ["the save at PT %d failed (0x540aca returned %s)" % (c["pt"], c["eax"])
           for c in saves if c["eax"] != "00000000"]
    posts = [int(m.group(1)) for m in map(POSTED.match, stream.header) if m]
    out += ["an autosave was due after PT %s and not posted" % m.group(1)
            for m in map(UNPOSTED.match, stream.header) if m]
    last = max(stream.ticks) if stream.ticks else 0
    if posts and saves:
        done = sorted(c["pt"] for c in saves)
        for pt in posts:
            if not any(pt < s <= pt + SAVE_LAG_PTS for s in done) and pt + SAVE_LAG_PTS <= last:
                out.append("the autosave posted after PT %d never ran" % pt)
    if stream.ticks and autosave_ms:
        # The host counts ms per PT (1000 / PT rate) towards the interval, from the
        # game's start or from the load.
        due = (last - min(stream.ticks) + 1) * 1000 // ptps // autosave_ms
        if posts:
            if len(posts) < due:
                out.append("%d autosaves posted, %d due" % (len(posts), due))
        elif len(saves) < due - 1:
            out.append("%d saves, %d autosaves due" % (len(saves), due))
    return out


def save_timing(a, b):
    """In single player the save message lands in whichever CT the server builds after the
    simulation thread queued it, which is thread timing (simparity.md §9.3): the PTs of the
    first save that landed at different PTs in the two streams, or None."""
    sa = [c["pt"] for c in a.calls if c["fn"] == SAVE_TO_FILE]
    sb = [c["pt"] for c in b.calls if c["fn"] == SAVE_TO_FILE]
    for x, y in zip(sa, sb):
        if x != y:
            return x, y
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--minutes", type=float, default=5.0, help="game time with nobody at the controls")
    parser.add_argument("--seed", type=int, default=1000, help="the game seed (EEMStartLoading)")
    parser.add_argument("--ptps", type=int, default=40, help="PTs per second, pinned (registry)")
    parser.add_argument("--autosave-ms", type=int, default=None, metavar="MS",
                        help="the registry's AutoSave In Milliseconds (seed_user's: 600000; 0 = off)")
    parser.add_argument("--reg", action="append", default=[], metavar="[SUBKEY\\]NAME=VALUE",
                        help="a value under the game's registry key (a number: DWORD, else a string), "
                             "e.g. 'Game Options\\Difficulty Level=0' (repeatable)")
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
    parser.add_argument("--draws", default=None, metavar="A-B",
                        help="R rows: every call of a random-drawing function in PTs A..B")
    parser.add_argument("--trace-fns", default=None, metavar="ADDR,...",
                        help="with --draws: '# call' lines for these EXE functions (hex)")
    parser.add_argument("--mem", default=None, metavar="PT,ADDR,LEN;...",
                        help="'# mem' lines: LEN bytes of the object whose pointer is at ADDR, after PT")
    parser.add_argument("--players", type=int, default=None, metavar="N",
                        help="P rows (each player's resources, +0xaf0) after every Nth PT")
    parser.add_argument("--calls", default=None, metavar="ADDR,...",
                        help="K rows: every call of these EXE functions (hex; F/N shows N stack arguments), any PT")
    parser.add_argument("--memfn", default=None, metavar="ADDR,LEN;...",
                        help="'# memfn' lines: LEN bytes of ECX's object on entry of ADDR")
    parser.add_argument("--ai-sched", action="store_true",
                        help="'# sched' lines: computer players scheduling their strategy reviews")
    parser.add_argument("--trace-match", default=None, metavar="TEXT",
                        help="ee_boot's import trace, only the calls whose DLL!name contains TEXT "
                             "('|' between several), to OUT/mac-<k>-imports.txt")
    parser.add_argument("--autosave-watch", action="store_true",
                        help="'# autosave' lines: the autosave counter passing the interval unposted, "
                             "and restarting when the save is posted")
    parser.add_argument("--against", type=Path, action="append", default=[], metavar="STREAM",
                        help="also compare every run with this stream (e.g. a Windows one); skipped "
                             "when the file is missing")
    parser.add_argument("--out", type=Path, default=ROOT / "analysis/simparity/mac")
    parser.add_argument("--load", type=Path, default=None, metavar="SAVE",
                        help="start from this saved game (.ees) instead of a new random map; "
                             "--minutes counts from the load")
    parser.add_argument("--keep-saves", action="store_true",
                        help="copy the saves the game wrote to OUT/mac-<k>-saves/")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--kit", default=None)
    parser.add_argument("--insert-dylib", default=os.environ.get("SIMPARITY_INSERT_DYLIB"))
    args = parser.parse_args()
    if args.load and not args.load.is_file():
        parser.error("--load: no such file: %s" % args.load)
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
    same = True
    pairs = [(traces[0], other) for other in traces[1:]] + \
        [(trace, ref) for ref in args.against if ref.is_file() for trace in traces]
    for ref in args.against:
        if not ref.is_file():
            print("%s: not here; skipped" % ref)
    for x, y in pairs:
        a, b = compare.load(x), compare.load(y)
        report = compare.compare(a, b)
        timing = save_timing(a, b)
        if timing and not report["agree"] and min(timing) >= report.get("first_pt", 0):
            timing = None  # the streams differ before the saves did
        hidden = []
        for key in ("players", "calls"):
            first = (report.get(key) or {}).get("first_difference")
            if first and not (timing and first["pt"] >= min(timing)):
                hidden.append("%s differ at PT %d" % (key, first["pt"]))
        note = "; " + ", ".join(hidden) if hidden else ""
        if timing:
            note += ("; a save landed at PT %d / %d (thread timing, simparity.md §9.3), %s"
                     % (timing + ("nothing differs after it" if report["agree"] else "the rest follows",)))
        print("%s vs %s: %s%s" % (x.name, y, report["summary"], note), flush=True)
        same &= (report["agree"] or bool(timing)) and not hidden
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
