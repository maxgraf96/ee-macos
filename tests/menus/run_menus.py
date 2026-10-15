#!/usr/bin/env python3
"""The menu test: Empire Earth driven through its menus by scripted input
(the kit's host/input_script.h), headless, in strict mode, with the kit's
software Direct3D 7 renderer. Every scenario in tests/menus/*.script starts at
the main menu with a fresh user directory and must

  - run its script to the end: every click reaches its target through the
    game's own pointer, and every `wait change` sees the screen it waits for;
  - end cleanly (exit code 0) with no strict-mode stop, no guest fault, and no
    import called without an implementation;
  - write the frames its script dumps: build/menus/<scenario>/<name>.png;
  - settings.script leaves through Exit Game: the game must end itself;
  - random-map.script must reach the game: 800x600 frames that change while
    it runs.

A script's `# menus: alone` line runs it on its own (it opens the game's
server), `# menus: env NAME=VALUE` lines set its environment, and a
`# menus: seed ARGS` line passes ARGS to tools/boot/seed_user.py.

    .venv/bin/python tests/menus/run_menus.py [--kit DIR] [--no-build] [--jobs N]
                                              [SCENARIO ...]

Run through tools/test.py --menus. docs/reference/menus.md has the scenarios
and what they showed.
"""
import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
import seed_user  # noqa: E402
from game_ports import game_ports_lock  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "build/menus"
# Imports a scenario may reach with nothing behind them: none. (Phase 5's
# sockets were, until the kit's Winsock.)
KNOWN_UNIMPLEMENTED = set()
SECONDS = {"random-map": 420}


def run(name, script, seconds, keep_user):
    """Runs one scenario; returns (name, problems, log path, dumps)."""
    user = Path(tempfile.mkdtemp(prefix="ee-menus-%s-" % name))
    out_dir = OUT / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    log = OUT / ("%s.log" % name)
    try:
        # `# menus: seed ARGS` lines: seed_user.py's options for this scenario
        # (the in-game resolution of the parity run).
        seed = [a for line in re.findall(r"^# menus: seed (.*)$", script.read_text(), re.M)
                for a in line.split()]
        seed_user.main(["--user", str(user)] + seed)
        env = dict(os.environ, RECOMP_STRICT="1")
        # `# menus: env NAME=VALUE` lines: what the scenario runs with
        # (RECOMP_EE_FORCE_SEED, the parity run's map).
        env.update(re.findall(r"^# menus: env (\w+)=(\S*)", script.read_text(), re.M))
        result = subprocess.run([str(ROOT / "build/recomp/ee_boot"), "--user", str(user),
                                 "--seconds", str(seconds), "--script", str(script),
                                 "--script-out", str(out_dir)],
                                cwd=ROOT, capture_output=True, text=True, timeout=seconds + 180,
                                env=env)
        out = result.stdout + result.stderr
        log.write_text(out)
        problems = []
        if result.returncode != 0:
            problems.append("exit code %d (%s)" % (result.returncode,
                            (re.search(r"^stop: (.*)", out, re.M) or [None, "?"])[1]))
        if "[script] finished:" not in out:
            failed = re.search(r"\[script\] FAILED (.*)", out)
            problems.append("the script did not finish%s" % (": " + failed.group(1) if failed else ""))
        if "[recomp] strict:" in out:
            problems.append("a strict-mode stop: %s" % re.search(r"\[recomp\] strict:.*", out).group(0))
        if "guest memory fault" in out:
            problems.append(re.search(r".*guest memory fault.*", out).group(0))
        called = set(re.findall(r"^\s+(\S+!\S+)\s+CALLED$", out, re.M))
        unknown = sorted(called - KNOWN_UNIMPLEMENTED)
        if unknown:
            problems.append("imports called with no implementation: %s" % ", ".join(unknown))
        dumps = re.findall(r"^\[script\] dump (\S+): (\d+)x(\d+) at frame \d+, hash (\w+), "
                           r"differs from the previous dump in ([\d.]+)%", out, re.M)
        wanted = re.findall(r"^\s*dump\s+(\S+)", script.read_text(), re.M)
        written = [d[0] for d in dumps]
        if written != wanted:
            problems.append("frames written %s, the script dumps %s" % (written, wanted))
        for d in written:
            if not (out_dir / ("%s.png" % d)).is_file():
                problems.append("no %s.png" % d)
        if name == "settings":
            # Exit Game is the last step: the game ends itself, long before
            # the deadline would have closed it.
            if "deadline: posting WM_CLOSE" in out or "stop: guest called ExitProcess" not in out:
                problems.append("Exit Game did not end the game (%s)" %
                                (re.search(r"^stop: (.*)", out, re.M) or [None, "?"])[1])
        forced = re.search(r"^# menus: env RECOMP_EE_FORCE_SEED=(\d+)", script.read_text(), re.M)
        if forced and not re.search(r"^\[ee\] random map: seed \d+ forced to %s$" % forced.group(1),
                                    out, re.M):
            problems.append("the map was not generated from seed %s" % forced.group(1))
        if name == "random-map":
            game = [d for d in dumps if d[0].startswith("in-game")]
            if not game or any((w, h) != ("800", "600") for _, w, h, _, _ in game):
                problems.append("no 800x600 in-game frames: %s" % [(d[0], d[1], d[2]) for d in game])
            elif len({d[3] for d in game}) < 2:
                problems.append("the game's frames never changed while it ran")
        return name, problems, log, dumps
    finally:
        if not keep_user:
            shutil.rmtree(user, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help="scenario names (default: all)")
    parser.add_argument("--kit", default=None)
    parser.add_argument("--no-build", action="store_true", help="run what is already built")
    parser.add_argument("--regenerate", action="store_true", help="translate before building")
    parser.add_argument("--jobs", type=int, default=4, help="scenarios run at once")
    parser.add_argument("--keep-user", action="store_true", help="keep the user directories")
    args = parser.parse_args()
    scripts = sorted(HERE.glob("*.script"))
    if args.scenarios:
        scripts = [s for s in scripts if s.stem in args.scenarios]
        missing = set(args.scenarios) - {s.stem for s in scripts}
        if missing:
            sys.exit("no such scenario: %s" % ", ".join(sorted(missing)))
    if not args.no_build:
        command = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_boot"]
        command += ["--kit", args.kit] if args.kit else []
        command += ["--regenerate"] if args.regenerate else []
        subprocess.run(command, check=True)
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0
    # A scenario that opens the game's server (Play Random Map) binds UDP
    # 33334 and TCP 33335, fixed in TCP Protocol.dll: two at once collide
    # (WSAEADDRINUSE) and the loser faults in the engine. Those run one after
    # another, each holding the machine-wide game-ports lock (tools/game_ports.py)
    # so that other runs and checkouts wait for it too; the rest alongside them.
    alone = [s for s in scripts if re.search(r"^# menus: alone", s.read_text(), re.M)]
    together = [s for s in scripts if s not in alone]
    seconds = lambda s: SECONDS.get(s.stem, 240)

    def run_alone(s):
        with game_ports_lock("menus %s (%s)" % (s.stem, ROOT)):
            return run(s.stem, s, seconds(s), args.keep_user)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        serial = pool.submit(lambda: [run_alone(s) for s in alone])
        jobs = [pool.submit(run, s.stem, s, seconds(s), args.keep_user) for s in together]
        results = serial.result() + [job.result() for job in jobs]
        for name, problems, log, dumps in sorted(results, key=lambda r: r[0]):
            print("%s %s (%d frames; log %s)" % ("ok  " if not problems else "FAIL", name,
                                                 len(dumps), log.relative_to(ROOT)))
            for d in dumps:
                print("       %-22s %sx%s  %5s%% changed" % (d[0], d[1], d[2], d[4]))
            for p in problems:
                print("     - %s" % p)
            failures += bool(problems)
    print("menus: %d of %d scenarios passed; frames in %s" %
          (len(scripts) - failures, len(scripts), OUT.relative_to(ROOT)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
