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
`# menus: seed ARGS` line passes ARGS to tools/boot/seed_user.py. Two lines
check its frames: `# menus: mode WxHxB` (the game set that display mode) and
`# menus: same A B` (dumps A and B show the same picture apart from the
pointer, which may land a pixel or two from where it was: no cursor trail).

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
import struct
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


def same_picture(a, b, pointer=48, tolerance=24):
    """None when frames a and b agree except inside one pointer-sized box (the
    cursor itself, a pixel or two apart); otherwise what differs."""
    import numpy as np
    from PIL import Image
    x = np.asarray(Image.open(a).convert("RGB"), dtype=np.int16)
    y = np.asarray(Image.open(b).convert("RGB"), dtype=np.int16)
    if x.shape != y.shape:
        return "sizes %s and %s" % (x.shape[1::-1], y.shape[1::-1])
    ys, xs = np.nonzero(np.abs(x - y).max(axis=2) > tolerance)
    if not len(xs):
        return None
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    if box[2] - box[0] <= pointer and box[3] - box[1] <= pointer:
        return None
    return "%d pixels in %s" % (len(xs), box)


# A held arrow key at the keyboard scenario's settings (legion's parity game,
# nothing selected, the default zoom): how fast Windows moves the camera's
# look-at point, in its units a second (legion 0080: about 603 steps a second,
# one per main-loop pass; Down held 2 s moved it 61.5; docs/reference/
# performance.md "Input and frame rate"), and how close the Mac has to come over
# one second. The Mac's first step of a press is one step and its last pass
# falls partly outside the second, so headless (18 passes a second) comes out
# 10-15 % short.
WINDOWS_SCROLL_PER_S = 30.5
SCROLL_TOLERANCE = 0.25
# EEUIESelectClosestAircraft (A, B, D, F) looks its "none" sound up twice when
# the player has no aircraft (EXE 0x5d925d and 0x5d9268, return addresses).
AIRCRAFT_NONE_SOUND = {"3c2"}
AIRCRAFT_CALLERS = {"005d925d", "005d9268"}


def check_keyboard(out, probe):
    """The keyboard scenario's checks: a held arrow key moves the camera as far
    as on Windows, and each W/A/S/D press is one key message, one character and
    no more than Windows' sounds."""
    import math
    problems = []
    peeks = [tuple(int(w, 16) for w in m) for m in
             re.findall(r"^\[script\] line \d+: peek 00918d48: (\w+) (\w+)$", out, re.M)]
    if len(peeks) != 4:
        return ["the camera was peeked %d times, not 4" % len(peeks)]
    f32 = lambda bits: struct.unpack("<f", struct.pack("<I", bits))[0]
    camera = [(f32(x), f32(y)) for x, y in peeks]
    for label, a, b in (("Up", camera[0], camera[1]), ("Down", camera[2], camera[3])):
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        print("     keyboard: %s held 1.0 s moved the camera %.3f" % (label, distance))
        if WINDOWS_SCROLL_PER_S and abs(distance / WINDOWS_SCROLL_PER_S - 1) > SCROLL_TOLERANCE:
            problems.append("%s held for 1 s moved the camera %.3f, Windows %.3f" %
                            (label, distance, WINDOWS_SCROLL_PER_S))
        if distance <= 0:
            problems.append("%s held for 1 s did not move the camera" % label)
    zooms = [f32(int(z, 16)) for z in
             re.findall(r"^\[script\] line \d+: peek 00918db0: (\w+)$", out, re.M)]
    if len(zooms) != 3:
        problems.append("the zoom was peeked %d times, not 3" % len(zooms))
    else:
        print("     keyboard: ] then [ held 0.2 s each: zoom %.3f -> %.3f -> %.3f" % tuple(zooms))
        if not (zooms[1] < zooms[0] and zooms[2] > zooms[1]) or not all(0 <= z <= 1 for z in zooms):
            problems.append("] and [ did not zoom in and back out within 0..1: %s" %
                            ", ".join("%.3f" % z for z in zooms))
    if not probe.is_file():
        return problems + ["no input probe (%s)" % probe.name]
    rows = [line.strip().split(",") for line in probe.read_text().splitlines()
            if line and not line.startswith("#")]
    # Each press, by its WM_KEYDOWN, and what happened from a little before it
    # (the game's DirectInput poll can fire the hotkey before the window
    # message is dispatched) to a second after it. The presses are 1.5 s apart.
    downs = [r for r in rows if r[0] == "M" and r[2] == "100" and r[3] in ("57", "41", "53", "44")]
    keys = [chr(int(r[3], 16)) for r in downs]
    if keys != list("WASDWASD"):
        problems.append("the key presses the game saw: %s, not WASDWASD" % "".join(keys))
        return problems
    for n, (down, key) in enumerate(zip(downs, keys)):
        t = float(down[1])
        span = [r for r in rows if t - 400 <= float(r[1]) < t + 1000]
        how = "tapped" if n < 4 else "typed"
        chars = [r for r in span if r[0] == "M" and r[2] == "102"]
        keydowns = [r for r in span if r[0] == "M" and r[2] == "100"]
        if len(keydowns) != 1 or len(chars) != 1:
            problems.append("%s %s: %d WM_KEYDOWN and %d WM_CHAR, not one of each" %
                            (key, how, len(keydowns), len(chars)))
        fires = sum(1 for r in span if r[0] == "A")
        sounds = [r for r in span if r[0] == "N" and r[3] in AIRCRAFT_CALLERS]
        others = [r for r in span if r[0] == "N" and r[2] != "ffffffff" and r[3] not in
                  AIRCRAFT_CALLERS]
        want = 1 if key in "AD" else 0
        if fires != want or len(sounds) != 2 * want or any(r[2] not in AIRCRAFT_NONE_SOUND
                                                           for r in sounds):
            problems.append("%s %s: %d aircraft hotkey fires and sounds %s, Windows 1 and 3c2 for "
                            "A and D, nothing for W and S" %
                            (key, how, fires, [r[2] for r in sounds]))
        # Whatever else the game looked up meanwhile (the world's own sounds)
        # is not the key's; the same sound again and again would be.
        repeated = sorted({r[2] for r in others if sum(o[2] == r[2] for o in others) > 4})
        if repeated:
            problems.append("%s %s: sounds looked up again and again: %s" % (key, how, repeated))
    return problems


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
        # (RECOMP_EE_FORCE_SEED, the parity run's map); {out} is the
        # scenario's output directory.
        env.update((k, v.replace("{out}", str(out_dir)))
                   for k, v in re.findall(r"^# menus: env (\w+)=(\S*)", script.read_text(), re.M))
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
        if "[ee] ESVerify failed:" in out:  # the engine's assertion (app/ee_assertions.cpp)
            problems.append(re.search(r"\[ee\] ESVerify failed:.*", out).group(0))
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
        text = script.read_text()
        for mode in re.findall(r"^# menus: mode (\d+x\d+x\d+)", text, re.M):
            if "Video mode changed to %s " % mode not in out:
                problems.append("the game never set %s" % mode)
        for a, b in re.findall(r"^# menus: same (\S+) (\S+)", text, re.M):
            if a in written and b in written:
                differs = same_picture(out_dir / ("%s.png" % a), out_dir / ("%s.png" % b))
                if differs:
                    problems.append("%s and %s differ outside the pointer: %s" % (a, b, differs))
        if name == "random-map":
            game = [d for d in dumps if d[0].startswith("in-game")]
            if not game or any((w, h) != ("800", "600") for _, w, h, _, _ in game):
                problems.append("no 800x600 in-game frames: %s" % [(d[0], d[1], d[2]) for d in game])
            elif len({d[3] for d in game}) < 2:
                problems.append("the game's frames never changed while it ran")
        if name == "keyboard":
            problems += check_keyboard(out, out_dir / "probe.csv")
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
    else:
        # `# menus: manual` scenarios have a runner of their own
        # (simparity.script: tools/simparity/run.py).
        scripts = [s for s in scripts if not re.search(r"^# menus: manual", s.read_text(), re.M)]
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
