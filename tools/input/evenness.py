#!/usr/bin/env python3
"""How evenly the camera moves while an arrow key is held, from the input
probe (mods/inputprobe: RECOMP_EE_INPUT_PROBE=FILE, in ee_boot or the app).

Empire Earth moves the camera one step per pass of its main thread's loop
(docs/reference/performance.md, "Input and frame rate"); the render thread
draws it as it finds it. On Windows the loop makes about 600 passes a second
while the game renders, so every frame finds the camera a few steps further on.
When the main thread gets the cooperative baton in bursts, frames find it where
the last frame did and then far beyond: the camera moves in bursts. This
measures what the player sees: the camera's move from one rendered frame to
the next (the probe's R rows carry the look-at point each frame starts from).

For each hold (a run of the probe's S rows in one direction), from the first
step plus --skip ms to the frame after the last movement (a map edge stops the
camera; the frames after it are not the scheduler's), it reports

  - steps, main-loop passes and frames a second;
  - the camera's speed, its units a second;
  - per frame: the move (mean, p5, median, p95, max), the share of frames
    that did not move at all, and the move divided by speed x the frame's own
    interval (1.0 is motion exactly even in time; its p5 and p95).

--check fails (exit 1) a hold that misses any of: no more than --max-still of
its frames still, p5 of the per-frame move at least --min-p5 of its median and
p95 at most --max-p95 of it, and (with --windows-speed) the speed within
--speed-tolerance of Windows'.

    tools/input/evenness.py PROBE.csv [--skip MS] [--check] [--windows-speed U]
                                      [--json OUT]

The keyboard test (tests/menus/run_menus.py) and the app's frame-limit test
(tests/menus/run_app.py) use it; so can a scheduler change.
"""
import argparse
import json
import math
import sys
from pathlib import Path

# Windows: legion 0080 (the parity game, 800x600, nothing selected, the default
# zoom): about 603 scroll steps a second, one per main-loop pass, and the
# camera's look-at point 30.5 units a second (Down held 2 s: 61.5).
WINDOWS_SPEED = 30.5


def percentile(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    i = int(math.floor(k))
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def load(path):
    rows = [line.strip().split(",") for line in Path(path).read_text().splitlines()
            if line and not line.startswith("#")]
    steps = [(float(r[1]), r[2], float(r[3]), float(r[4])) for r in rows if r[0] == "S"]
    passes = [float(r[1]) for r in rows if r[0] == "U"]
    frames = [(float(r[1]), float(r[2]), float(r[3])) for r in rows if r[0] == "R" and len(r) >= 4]
    return steps, passes, frames


def holds_of(steps, gap_ms=300):
    """Runs of steps in one direction with no gap longer than gap_ms."""
    holds = []
    for t, d, _, _ in steps:
        if holds and holds[-1]["dir"] == d and t - holds[-1]["end"] < gap_ms:
            holds[-1]["end"] = t
            holds[-1]["steps"] += 1
        else:
            holds.append({"dir": d, "start": t, "end": t, "steps": 1})
    return holds


NAMES = {"0": "Up", "1": "Down", "2": "Left", "3": "Right"}


def measure(hold, passes, frames, skip_ms):
    start, end = hold["start"] + skip_ms, hold["end"]
    inside = [f for f in frames if start <= f[0] <= end + 50]
    moves = []
    for a, b in zip(inside, inside[1:]):
        moves.append((b[0] - a[0], math.hypot(b[1] - a[1], b[2] - a[2])))
    # A map edge: the camera stops for good. Cut the frames after its last move.
    while moves and moves[-1][1] == 0:
        moves.pop()
    out = {"key": NAMES.get(hold["dir"], hold["dir"]), "steps": hold["steps"],
           "seconds": (hold["end"] - hold["start"]) / 1000.0}
    out["steps_per_s"] = hold["steps"] / out["seconds"] if out["seconds"] > 0 else float("nan")
    span = sum(dt for dt, _ in moves) / 1000.0
    out["frames"] = len(moves)
    out["window_s"] = span
    if not moves or span <= 0:
        return out
    t0 = inside[0][0]
    t1 = t0 + span * 1000
    out["passes_per_s"] = sum(1 for t in passes if t0 <= t < t1) / span
    out["frames_per_s"] = len(moves) / span
    distance = sum(m for _, m in moves)
    out["speed"] = distance / span
    per_frame = [m for _, m in moves]
    out["move_mean"] = distance / len(moves)
    out["move_p5"] = percentile(per_frame, 5)
    out["move_median"] = percentile(per_frame, 50)
    out["move_p95"] = percentile(per_frame, 95)
    out["move_max"] = max(per_frame)
    out["still_share"] = sum(1 for m in per_frame if m == 0) / len(per_frame)
    even = [m / (out["speed"] * dt / 1000.0) for dt, m in moves if dt > 0]
    out["even_p5"] = percentile(even, 5)
    out["even_p95"] = percentile(even, 95)
    return out


def check(m, args):
    problems = []
    if "speed" not in m:
        return ["no frames inside the hold"]
    if m["still_share"] > args.max_still:
        problems.append("%.1f %% of frames did not move (at most %.1f %%)" %
                        (100 * m["still_share"], 100 * args.max_still))
    if m["move_median"] > 0:
        if m["move_p5"] < args.min_p5 * m["move_median"]:
            problems.append("p5 of the per-frame move is %.2f of its median (at least %.2f)" %
                            (m["move_p5"] / m["move_median"], args.min_p5))
        if m["move_p95"] > args.max_p95 * m["move_median"]:
            problems.append("p95 of the per-frame move is %.2f of its median (at most %.2f)" %
                            (m["move_p95"] / m["move_median"], args.max_p95))
    if args.windows_speed and abs(m["speed"] / args.windows_speed - 1) > args.speed_tolerance:
        problems.append("speed %.1f, Windows %.1f (within %d %%)" %
                        (m["speed"], args.windows_speed, round(100 * args.speed_tolerance)))
    return problems


def report(path, args):
    steps, passes, frames = load(path)
    results = []
    for hold in holds_of(steps):
        if hold["steps"] < 3:
            continue
        m = measure(hold, passes, frames, args.skip)
        m["problems"] = check(m, args) if args.check else []
        results.append(m)
    return results


def line(m):
    if "speed" not in m:
        return "%-5s %4d steps in %.2f s: no frames inside the hold" % (m["key"], m["steps"],
                                                                       m["seconds"])
    return ("%-5s %4d steps/s %4.0f passes/s %5.1f frames/s | speed %5.1f/s | per frame: mean "
            "%.3f p5 %.3f median %.3f p95 %.3f max %.3f, still %4.1f %% | even p5 %.2f p95 %.2f" %
            (m["key"], m["steps_per_s"], m["passes_per_s"], m["frames_per_s"], m["speed"],
             m["move_mean"], m["move_p5"], m["move_median"], m["move_p95"], m["move_max"],
             100 * m["still_share"], m["even_p5"], m["even_p95"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--skip", type=float, default=200,
                        help="ms after a hold's first step left out (the key's first pass)")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--max-still", type=float, default=0.02)
    parser.add_argument("--min-p5", type=float, default=0.5)
    parser.add_argument("--max-p95", type=float, default=1.5)
    parser.add_argument("--windows-speed", type=float, default=None,
                        help="Windows' speed, look-at units a second (%.1f at the parity game's "
                             "start)" % WINDOWS_SPEED)
    parser.add_argument("--speed-tolerance", type=float, default=0.15)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    results = report(args.probe, args)
    if not results:
        print("no held arrow key in %s" % args.probe)
        return 1
    for m in results:
        print(line(m))
        for p in m["problems"]:
            print("      - %s" % p)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    return 1 if any(m["problems"] for m in results) else 0


if __name__ == "__main__":
    sys.exit(main())
