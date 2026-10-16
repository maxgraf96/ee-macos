#!/usr/bin/env python3
"""The frame limit and the camera's evenness in the windowed app
(build/EmpireEarth.app, Metal): the app's own frame stats, and how evenly a
held arrow key moves the camera from frame to frame, with the Frame limit at
its default and at "original".

tests/menus/app/frame-limit.script takes the app into a game with legion's
parity settings (800x600x32, custom civilizations on so that the game itself
comes up), waits 10 seconds, then holds Up and Down for 2 s each; in the
background (RECOMP_SCRIPT_BACKGROUND: the window is never raised and keeps its
DirectInput devices) and silent, with a fresh profile, under the game-ports
lock. It runs twice:

  - a fresh profile: game.toml's frame_limit (120). The guest's presents a
    second at 800x600 (the app's end-of-run report: "800x600 32bpp: N guest
    presents; S s at this mode") must be at most the limit + 1, and at least 30; and the
    camera must move evenly (tools/input/evenness.py --check: no more than
    2 % of frames still, the per-frame move's p5 at least half its median and
    p95 at most 1.5 times it, the speed within 15 % of Windows' 30.5 a
    second). --no-evenness reports it without failing;
  - "original" (host.display/frame_limit 0 in the profile): the display's own
    rate and the evenness, reported.

Both report, from the input probe (mods/inputprobe, RECOMP_EE_INPUT_PROBE),
the main loop's passes a second and the frames rendered a second over the
10 seconds still, and the app's CPU time. docs/reference/performance.md,
"Input and frame rate".

    .venv/bin/python tests/menus/run_app.py [--app PATH] [--keep] [--no-evenness]

Run through tools/test.py --app-frames. Needs the app built
(tools/build.py --target app) and a display; holds the machine for about three
minutes.
"""
import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/input"))
import evenness  # noqa: E402
import seed_user  # noqa: E402
from game_ports import game_ports_lock  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "build/menus/app"
SCRIPT = HERE / "app/frame-limit.script"
MODE = re.compile(r"^\s*800x600\s+32bpp:\s+(\d+) guest presents; ([\d.]+)s at this mode", re.M)


def probe_rates(path, seconds=8):
    """Main-loop passes and rendered frames a second over the seconds before
    the first held key (the game still)."""
    rows = [line.split(",") for line in path.read_text().splitlines()
            if line and not line.startswith("#")]
    first = [float(r[1]) for r in rows if r[0] == "S"]
    times = [float(r[1]) for r in rows if r[0] in ("U", "R")]
    if not times:
        return None, None
    end = (first[0] if first else max(times)) - 1000
    start = end - seconds * 1000
    count = lambda kind: sum(1 for r in rows if r[0] == kind and start <= float(r[1]) < end)
    return count("U") / seconds, count("R") / seconds


def run(app, name, settings, keep):
    out = OUT / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    user = Path(tempfile.mkdtemp(prefix="ee-app-%s-" % name))
    try:
        seed_user.main(["--user", str(user), "--width", "800", "--height", "600",
                        "--player-name", "Unknown1634"])
        if settings:
            (user / "mod-settings.json").write_text(json.dumps(settings))
        # No "Choose Civilization" before the game (EXE 0x66062b opens it only
        # with custom civilizations off).
        reg = json.loads((user / "registry.json").read_text())
        reg["HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth\\Game Options"]["Allow Custom Civs"]["data"] = 1
        (user / "registry.json").write_text(json.dumps(reg, indent=1))
        env = dict(os.environ, RECOMP_HOST_NO_AUDIO="1", SDL_AUDIO_DRIVER="dummy",
                   SDL_AUDIODRIVER="dummy", RECOMP_SCRIPT_BACKGROUND="1",
                   RECOMP_INPUT_SCRIPT=str(SCRIPT), RECOMP_INPUT_SCRIPT_OUT=str(out),
                   RECOMP_PROFILE_DIR=str(user), RECOMP_REGISTRY=str(user / "registry.json"),
                   RECOMP_EE_INPUT_PROBE=str(out / "probe.csv"),
                   RECOMP_MODS_DIR=str(ROOT / "build/recomp/ee-mods"))
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        with game_ports_lock("app %s (%s)" % (name, ROOT)):
            log = out / "app.log"
            with open(log, "w") as f:
                result = subprocess.run([str(app)], stdout=f, stderr=subprocess.STDOUT, env=env,
                                        timeout=600)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        text = log.read_text(errors="replace")
        problems = []
        if result.returncode != 0:
            problems.append("exit code %d" % result.returncode)
        if "[script] finished:" not in text:
            failed = re.search(r"\[script\] FAILED (.*)", text)
            problems.append("the script did not finish%s" % (": " + failed.group(1) if failed else ""))
        mode = MODE.search(text)
        rate = int(mode.group(1)) / float(mode.group(2)) if mode else None
        if rate is None:
            problems.append("no 800x600 frames in the app's report")
        passes, frames = probe_rates(out / "probe.csv") if (out / "probe.csv").is_file() else (None, None)
        cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
        holds = []
        if (out / "probe.csv").is_file():
            args = argparse.Namespace(skip=200, check=True, max_still=0.02, min_p5=0.5, max_p95=1.5,
                                      windows_speed=evenness.WINDOWS_SPEED, speed_tolerance=0.15)
            holds = evenness.report(out / "probe.csv", args)
        if len(holds) != 2:
            problems.append("%d held keys in the probe, not 2 (Up, Down)" % len(holds))
        return {"rate": rate, "passes": passes, "frames": frames, "cpu": cpu, "problems": problems,
                "log": log, "holds": holds}
    finally:
        if not keep:
            shutil.rmtree(user, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app", type=Path,
                        default=ROOT / "build/EmpireEarth.app/Contents/MacOS/EmpireEarth")
    parser.add_argument("--keep", action="store_true", help="keep the profiles")
    parser.add_argument("--no-evenness", action="store_true",
                        help="report the camera's evenness without failing on it")
    args = parser.parse_args()
    if not args.app.is_file():
        sys.exit("no app at %s: tools/build.py --target app" % args.app)
    while subprocess.run(["pgrep", "-f", "/Applications/EmpireEarth.app/Contents/MacOS"],
                         capture_output=True).returncode == 0:
        print("the player's own app is running; waiting", flush=True)
        time.sleep(30)
    failures = 0
    frame_limit = tomllib.loads((ROOT / "game.toml").read_text())["settings"]["frame_limit"]
    for name, settings, limit in (("limit-default", None, frame_limit + 1.0),
                                  ("limit-original", {"host.display/frame_limit": 0}, None)):
        r = run(args.app, name, settings, args.keep)
        if limit is not None and r["rate"] is not None:
            if r["rate"] > limit:
                r["problems"].append("%.1f presents a second at 800x600, the limit is %d"
                                    % (r["rate"], frame_limit))
            if r["rate"] < 30:
                r["problems"].append("%.1f presents a second at 800x600" % r["rate"])
        if limit is not None and not args.no_evenness:
            for m in r["holds"]:
                r["problems"] += ["%s: %s" % (m["key"], p) for p in m["problems"]]
        fmt = lambda v: "%.1f" % v if v is not None else "?"
        print("%s %s: %s presents/s at 800x600, main loop %s passes/s, %s frames/s rendered, "
              "CPU %.0f s (log %s)" % ("ok  " if not r["problems"] else "FAIL", name, fmt(r["rate"]),
                                       fmt(r["passes"]), fmt(r["frames"]), r["cpu"],
                                       r["log"].relative_to(ROOT)))
        for m in r["holds"]:
            print("     %s" % evenness.line(m))
        for p in r["problems"]:
            print("     - %s" % p)
        failures += bool(r["problems"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
