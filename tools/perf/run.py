#!/usr/bin/env python3
"""Time and profile a heavy scene headless (P7-B, docs/reference/performance.md).

    .venv/bin/python tools/perf/run.py --load SAVE [--minutes M] [--sample AT:SECONDS ...]
    .venv/bin/python tools/perf/run.py --random-map [--reg 'Game Options\\Map Size=4'] ...
        [--width W --height H] [--out DIR] [--env NAME=VALUE ...] [--insert-dylib PATH]
        [--app build/player/EmpireEarth.app --code-dir build/recomp/bundles]

Boots build/recomp/ee_boot with a fresh user directory (tools/boot/seed_user.py's
registry, the PT rate left to the game as a player's is) and either loads SAVE
(tests/menus/simparity-load.script: Single Player -> Play Saved Game -> Load) or
starts a random map (tests/menus/simparity.script: Single Player -> Play Random
Map -> Start Game). Nobody plays. --app runs the windowed host instead (Metal),
behind every other window (RECOMP_SCRIPT_BACKGROUND), as a child of this
process; with the player host, --code-dir names the bundles it loads, as the
shareable app's first run builds them (RECOMP_CODE_DIR). It writes, in OUT:

  log.txt      the game's output, every line prefixed with the seconds since start
               (the game's own load phases are OutputDebugString lines)
  perf.csv     the perfprobe mod's PT counter every 250 ms (mods/perfprobe/)
  sample-*.txt macOS `sample` profiles taken AT seconds after start for SECONDS
  frames.csv   with --app: the presenter's frame timings (RECOMP_FRAME_TIMINGS)
  summary.json the times of the load (the click on Load or Start Game to the first
               PT), the PT rate over the game and, with --app, the frame intervals

Every run holds tools/game_ports.py's lock and waits while the player's own app runs:
a game opens the game's fixed ports. Silent: no audio device.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
import seed_user  # noqa: E402
from game_ports import game_ports_lock  # noqa: E402

LOAD_SCRIPT = ROOT / "tests/menus/simparity-load.script"
MAP_SCRIPT = ROOT / "tests/menus/simparity.script"
PLAYER_APP = "/Applications/EmpireEarth.app/Contents/MacOS"  # the player's own install
HKCU = "HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"
# The click that starts the load in each scenario, as the script logs it.
LOAD_CLICK = {LOAD_SCRIPT: "click (511, 728)", MAP_SCRIPT: "click (644, 730)"}
START_EPOCH = [0.0]  # the wall clock when the game started (the log's t = 0)
UPTIME_OFFSET = [0.0]  # the wall clock minus CLOCK_UPTIME_RAW, then


def set_registry(user, ptps, extra):
    """The PT rate pinned when asked (Min = Max PTs per Second), and NAME=VALUE or
    SUBKEY\\NAME=VALUE under the game's key: a number is a DWORD, anything else a string."""
    reg_path = user / "registry.json"
    reg = json.loads(reg_path.read_text())
    if ptps is not None:
        for name in ("Min PTs per Second", "Max PTs per Second"):
            reg[HKCU][name] = {"type": seed_user.REG_DWORD, "data": ptps}
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


APP_PREFIX = """# tools/perf/run.py --app: the movies skipped, as tests/menus/app/random-map.script does
timeout 4000000
pointer f32 [0x9197a8]+4 [0x9197a8]+8
hold 250
wait frames 30
key ESCAPE
wait 2500
key ESCAPE
wait 2500
key ESCAPE
wait size 1024 768 within 180000
wait 3000
"""


# --pan: the camera swept across the map with the arrow keys, so terrain, units and
# buildings keep coming into view (the stutter players see when more comes on screen).
PAN = """wait 20000
key RIGHT down
wait %(hold)d
key RIGHT up
key DOWN down
wait %(hold)d
key DOWN up
key LEFT down
wait %(hold)d
key LEFT up
key UP down
wait %(hold)d
key UP up
"""


def scenario(path, minutes, out, app=None, pan=0, hold=6000):
    text = path.read_text()
    if app:
        # The windowed host: the movies play first; the in-game test pixel is the
        # headless 800x600 frame's, so wait for the game's mode instead.
        body = text.split("wait frames 30", 1)[1]
        body = re.sub(r"^wait size \d+ \d+ within (\d+)", "wait size %d %d within \\1" % app, body, flags=re.M)
        body = re.sub(r"^wait pixel .*$", "wait 5000", body, flags=re.M)
        text = APP_PREFIX + body
    text, n = re.subn(r"^wait \d+(\s+# simparity: duration.*)$",
                      lambda m: (PAN % {"hold": hold}) * pan + "wait %d%s" % (int(minutes * 60000),
                                                                             m.group(1)), text,
                      flags=re.M)
    if n != 1:
        sys.exit("%s has no '# simparity: duration' wait" % path.name)
    # The civilization screen opens 5 game minutes in; these runs are shorter, and its
    # click is in 800x600 coordinates.
    text = re.sub(r"^# simparity: civ begin.*?^# simparity: civ end[^\n]*\n", "", text,
                  flags=re.M | re.S)
    script = out / "scenario.script"
    script.write_text(text)
    return script


def player_running():
    """The player's own app (a checkout's build/player app is an agent's run)."""
    return subprocess.run(["pgrep", "-f", PLAYER_APP], capture_output=True).returncode == 0


def wait_for_player():
    while player_running():
        print("the player's app is running; waiting", flush=True)
        time.sleep(60)


def stamp(stream, log, start, lines):
    for raw in iter(stream.readline, b""):
        line = raw.decode(errors="replace").rstrip("\n")
        t = time.monotonic() - start
        log.write("%9.3f %s\n" % (t, line))
        log.flush()
        lines.append((t, line))


def summarize(lines, perf, out, click):
    """The load: from the scenario's mark (the click) to the first PT the game ran;
    the PT rate: PTs per second of wall time over the game, per 10 s window."""
    mark = next((t for t, l in lines if click in l), None)
    rows = []
    if perf.exists():
        for line in perf.read_text().splitlines():
            if line.startswith("S,"):
                f = line.split(",")
                rows.append((float(f[1]) / 1000.0, int(f[3]), float(f[5]) if len(f) > 5 else 0.0))
    summary = {"phases": [(round(t, 3), l.split("OutputDebugString: ", 1)[1])
                          for t, l in lines if "OutputDebugString:" in l and
                          ("completed" in l or "time:" in l or "Playable" in l)]}
    # perf.csv's clock starts when the plugin loads; line it up with the log's by the
    # plugin's own log line.
    loaded = next((t for t, l in lines if "perfprobe: sampling" in l), None)
    if rows and loaded is not None:
        rows = [(t + loaded, p, g) for t, p, g in rows]
        base = next(((t, p, g) for t, p, g in rows if p >= 0), None)
        first = next(((t, p, g) for t, p, g in rows if base and p > base[1]), None)
        if mark is not None and first:
            summary["load_s"] = round(first[0] - mark, 2)
            summary["first_pt"] = first[1]
        if first:
            game = [(t, p) for t, p, g in rows if t >= first[0] and p >= 0]
            times = [(t, g) for t, p, g in rows if t >= first[0] and p >= 0]
            if len(game) > 2:
                span = game[-1][0] - game[0][0]
                summary["pts"] = game[-1][1] - game[0][1]
                summary["pt_per_s"] = round(summary["pts"] / span, 2) if span > 0 else None
                # Game seconds per wall second: 1.0 at the standard speed means it keeps up.
                summary["game_s_per_s"] = round((times[-1][1] - times[0][1]) / 1000.0 / span, 3) \
                    if span > 0 else None
                windows, i = [], 0
                while i < len(game):
                    j = i
                    while j < len(game) and game[j][0] - game[i][0] < 10:
                        j += 1
                    if j < len(game):
                        windows.append(round((game[j][1] - game[i][1]) / (game[j][0] - game[i][0]), 1))
                    i = j
                summary["pt_per_s_10s"] = windows
    # The log's t = 0 on the wall clock, and CLOCK_UPTIME_RAW's offset from it (the
    # scheduler's timeline is on that clock): tools/perf/late_frames.py lines them up.
    summary["start_epoch"] = START_EPOCH[0]
    summary["uptime_offset"] = UPTIME_OFFSET[0]
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def present_stats(path, lines, click):
    """The game's own frame times: every present the renderer made (RECOMP_D3D7_FRAME_LOG),
    from 10 s after the load's click on. The display may show fewer of them (a window
    behind others is throttled by macOS), so this is what the game itself achieves."""
    times = []
    for line in Path(path).read_text().splitlines():
        try:
            times.append(float(line) / 1000.0)
        except ValueError:
            continue
    mark = next((t for t, l in lines if click in l), None)
    if len(times) < 10 or mark is None:
        return {}
    # Both clocks are the wall clock: the log's in epoch seconds, the lines' since START_EPOCH.
    after = [t for t in times if t - START_EPOCH[0] > mark + 10]
    dts = sorted(1000.0 * (b - a) for a, b in zip(after, after[1:]))
    if not dts:
        return {}

    def pct(q):
        return round(dts[min(len(dts) - 1, int(q * len(dts)))], 2)
    return {"frames": len(after), "fps": round(1000.0 * len(dts) / sum(dts), 1),
            "frame_ms": {"p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95), "p99": pct(0.99),
                         "max": round(dts[-1], 1)},
            "over_33ms": sum(1 for d in dts if d > 33.4), "over_100ms": sum(1 for d in dts if d > 100)}


def frame_stats(path, load_s, lines, click):
    """Frame intervals from the presenter's sealed times, in game (from the first PT on)."""
    import csv
    sealed = []
    for row in csv.DictReader(open(path)):
        try:
            if row.get("repeat") in ("0", "", None):
                sealed.append(float(row["sealed_s"]))
        except (KeyError, ValueError):
            continue
    if len(sealed) < 10:
        return {}
    sealed.sort()
    dts = [1000.0 * (b - a) for a, b in zip(sealed, sealed[1:])]
    # The last 60 % of the frames are in game in every scenario here; the load's are not.
    game = dts[int(len(dts) * 0.4):]
    game.sort()

    def pct(q):
        return round(game[min(len(game) - 1, int(q * len(game)))], 2)
    return {"frames": len(sealed), "frame_ms": {"p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95),
                                                 "p99": pct(0.99), "max": round(game[-1], 1)},
            "fps": round(1000.0 * len(game) / sum(game), 1) if sum(game) else None}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--load", type=Path, metavar="SAVE")
    what.add_argument("--random-map", action="store_true")
    p.add_argument("--minutes", type=float, default=3.0, help="game time after the load")
    p.add_argument("--reg", action="append", default=[], metavar="[SUBKEY\\]NAME=VALUE")
    p.add_argument("--ptps", type=int, default=None, help="pin Min = Max PTs per Second (default: the game tunes)")
    p.add_argument("--pan", type=int, default=0, metavar="N",
                   help="after 20 s in game, sweep the camera N times (right, down, left, up; 24 s each), "
                        "then --minutes more")
    p.add_argument("--pan-hold", type=int, default=6000, metavar="MS",
                   help="how long each arrow key is held in a sweep (a scripted run reads frames back "
                        "only in a wait's last second, so long holds keep the sweep clean)")
    p.add_argument("--width", type=int, default=800)
    p.add_argument("--height", type=int, default=600)
    p.add_argument("--sample", action="append", default=[], metavar="AT:SECONDS",
                   help="a macOS sample profile AT seconds after start, for SECONDS")
    p.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--binary", type=Path, default=ROOT / "build/recomp/ee_boot")
    p.add_argument("--app", type=Path, default=None, help="the windowed app bundle to run instead")
    p.add_argument("--code-dir", type=Path, default=None,
                   help="with a player-host --app: the bundles (RECOMP_CODE_DIR)")
    p.add_argument("--insert-dylib", default=os.environ.get("SIMPARITY_INSERT_DYLIB"))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ee-perf-"))
    user = tmp / "user"
    try:
        seed_user.main(["--user", str(user), "--width", str(a.width), "--height", str(a.height)])
        set_registry(user, a.ptps, a.reg)
        if a.load:
            saved = user / "Data" / "Saved Games"
            saved.mkdir(parents=True, exist_ok=True)
            shutil.copy(a.load, saved / a.load.name)
        source = LOAD_SCRIPT if a.load else MAP_SCRIPT
        script = scenario(source, a.minutes, a.out, (a.width, a.height) if a.app else None, a.pan,
                          a.pan_hold)
        mods = tmp / "ee-mods"
        shutil.copytree(ROOT / "build/recomp/ee-mods", mods)
        perf = a.out / "perf.csv"
        env = dict(os.environ, RECOMP_MODS_DIR=str(mods), RECOMP_EE_PERF_TRACE=str(perf),
                   RECOMP_HOST_NO_AUDIO="1", SDL_AUDIO_DRIVER="dummy", SDL_AUDIODRIVER="dummy")
        for item in a.env:
            k, _, v = item.partition("=")
            env[k] = v
        seconds = int(600 + a.minutes * 60 + a.pan * (20 + 4 * a.pan_hold / 1000.0))
        if a.app:
            exe = next(p for p in sorted((a.app / "Contents/MacOS").iterdir()) if os.access(p, os.X_OK))
            command = [str(exe)]
            env.update(RECOMP_INPUT_SCRIPT=str(script), RECOMP_INPUT_SCRIPT_OUT=str(a.out / "frames"),
                       RECOMP_SCRIPT_BACKGROUND="1", RECOMP_PROFILE_DIR=str(user),
                       RECOMP_REGISTRY=str(user / "registry.json"),
                       RECOMP_FRAME_TIMINGS=str(a.out / "frames.csv"),
                       RECOMP_D3D7_FRAME_LOG=str(a.out / "presents.txt"))
            if a.code_dir:
                env["RECOMP_CODE_DIR"] = str(a.code_dir.resolve())
                # A player host has no developer copy of the game to fall back on.
                env.setdefault("RECOMP_EXE", str(ROOT / "original/gog/Empire Earth/Empire Earth.exe"))
        else:
            command = [str(a.binary), "--user", str(user), "--seconds", str(seconds), "--script",
                       str(script), "--script-out", str(a.out / "frames")]
        prefix = ["nice", "-n", "10", "env"]
        if a.insert_dylib:
            prefix.append("DYLD_INSERT_LIBRARIES=" + a.insert_dylib)
        prefix += ["timeout", str(seconds + 180)]
        wait_for_player()
        with game_ports_lock("perf run (%s)" % ROOT, timeout=4 * 3600):
            start = time.monotonic()
            START_EPOCH[0] = time.time()
            UPTIME_OFFSET[0] = time.time() - time.clock_gettime(time.CLOCK_UPTIME_RAW)
            proc = subprocess.Popen(prefix + command, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=env)
            lines = []
            with open(a.out / "log.txt", "w") as log:
                reader = threading.Thread(target=stamp, args=(proc.stdout, log, start, lines), daemon=True)
                reader.start()
                samplers = []

                def game_pid():
                    pid = str(proc.pid)
                    # nice -> env -> timeout -> the game: find the game itself.
                    for _ in range(4):
                        kids = subprocess.run(["pgrep", "-P", pid], capture_output=True,
                                              text=True).stdout.split()
                        if not kids:
                            break
                        pid = kids[0]
                    return pid
                for item in a.sample:
                    at, _, dur = item.partition(":")

                    def take(at=float(at), dur=int(dur or 20)):
                        delay = at - (time.monotonic() - start)
                        if delay > 0:
                            time.sleep(delay)
                        if proc.poll() is None:
                            subprocess.run(["sample", game_pid(), str(dur), "-mayDie", "-file",
                                            str(a.out / ("sample-%d.txt" % int(at)))],
                                           capture_output=True)
                    t = threading.Thread(target=take, daemon=True)
                    t.start()
                    samplers.append(t)
                code = proc.wait()
                reader.join(5)
                for t in samplers:
                    t.join(1)
        summary = summarize(lines, perf, a.out, LOAD_CLICK[source])
        if (a.out / "frames.csv").exists():
            summary["display"] = frame_stats(a.out / "frames.csv", summary.get("load_s"), lines,
                                             LOAD_CLICK[source])
        if (a.out / "presents.txt").exists():
            summary["game_frames"] = present_stats(a.out / "presents.txt", lines,
                                                   LOAD_CLICK[source])
        summary["exit"] = code
        (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps({k: v for k, v in summary.items() if k != "phases"}, indent=1))
        for t, phase in summary["phases"]:
            print("  %8.2f s  %s" % (t, phase))
        return 0 if code == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
