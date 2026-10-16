#!/usr/bin/env python3
"""Late frames in a tools/perf/run.py run, and who held the baton meanwhile.

    .venv/bin/python tools/perf/late_frames.py RUN_DIR [RUN_DIR ...] [--threshold MS ...]

A run made with --env RECOMP_D3D7_STATS=1 (or RECOMP_D3D7_FRAME_LOG) leaves presents.txt,
the wall-clock time of every frame the game presented; with
--env RECOMP_SCHED_TRACE_TIMELINE=RUN_DIR/timeline.csv it also leaves the scheduler's
holds (who had the baton, from when to when, and why they gave it up). For each held
arrow key of the scenario (run.py --pan) this prints the frame intervals, how many
were longer than each threshold, and, over the long ones, the share of the time each
guest thread held the baton (the rest: nobody did, i.e. every thread waited).

The performance overlay's red bar is a new frame shown more than 1.2 frame budgets
after the previous one (kit host/performance_overlay.cpp; 20 ms at its default 60 fps
limit), so --threshold 20 counts red bars where the game makes its frames; a window
behind others is throttled by macOS, so what the display showed (frames.csv) is not
used. 12.5 ms is one refresh missed on a 120 Hz display.

The last --skip-tail ms of each hold are left out: the scripted run reads frames back
from the GPU in a wait's last second (kit host/input_script.cpp), which is the
harness's cost, not the game's.
"""
import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path


def log_lines(run):
    out = []
    for raw in (run / "log.txt").read_text(errors="replace").splitlines():
        m = re.match(r"\s*([0-9.]+) (.*)", raw)
        if m:
            out.append((float(m.group(1)), m.group(2)))
    return out


def start_epoch(run, lines, summary):
    """The wall clock at the log's t = 0."""
    if "start_epoch" in summary:
        return summary["start_epoch"]
    # Older runs: line perf.csv's epoch column up with the plugin's own log line.
    rows = [l.split(",") for l in (run / "perf.csv").read_text().splitlines() if l.startswith("S,")]
    loaded = next(t for t, l in lines if "perfprobe: sampling" in l)
    return float(rows[0][2]) / 1000 - float(rows[0][1]) / 1000 - loaded


def holds_of(run, lines):
    """The key holds of the scenario, in log seconds: (key, start, end)."""
    script = (run / "scenario.script").read_text().splitlines()
    try:
        i = next(k for k, l in enumerate(script) if l.split("#")[0].strip() == "dump in-game-start")
    except StopIteration:
        return []
    t = next(t for t, l in lines if "dump in-game-start" in l)
    out, down = [], {}
    for raw in script[i + 1:]:
        step = raw.split("#")[0].split()
        if not step:
            continue
        if step[0] == "wait" and len(step) == 2 and step[1].isdigit():
            t += int(step[1]) / 1000.0
        elif step[0] == "key" and len(step) == 3 and step[2] == "down":
            down[step[1]] = t
        elif step[0] == "key" and len(step) == 3 and step[2] == "up" and step[1] in down:
            out.append((step[1], down.pop(step[1]), t))
        elif step[0] not in ("key", "wait"):
            break
    return out


def report(run, thresholds, skip_tail):
    summary = json.loads((run / "summary.json").read_text()) if (run / "summary.json").exists() else {}
    lines = log_lines(run)
    e0 = start_epoch(run, lines, summary)
    presents = [float(x) / 1000.0 - e0 for x in (run / "presents.txt").read_text().split()]
    holds = holds_of(run, lines)
    if not holds:
        print("%s: no key holds after 'dump in-game-start'" % run)
        return
    # The timeline is on CLOCK_UPTIME_RAW; run.py records its offset from the wall clock.
    offset = summary.get("uptime_offset", time.time() - time.clock_gettime(time.CLOCK_UPTIME_RAW))
    baton = []
    timeline = run / "timeline.csv"
    if timeline.exists():
        for l in timeline.read_text().splitlines():
            if l.startswith("H,"):
                _, s, e, name, why = l.split(",", 4)
                baton.append((float(s) + offset - e0, float(e) + offset - e0, name, why))
        baton.sort()
    print("== %s" % run)
    every = []
    for key, h0, h1 in holds:
        h1 -= skip_tail / 1000.0
        ints = [(a, b) for a, b in zip(presents, presents[1:]) if h0 <= a and b <= h1]
        dts = sorted(1000.0 * (b - a) for a, b in ints)
        every += ints
        if not dts:
            continue
        pick = lambda q: dts[min(len(dts) - 1, int(q * len(dts)))]
        print("  %-5s %4d frames, %5.1f fps, p50 %.1f p95 %.1f p99 %.1f max %.1f ms; " %
              (key, len(dts), 1000.0 * len(dts) / sum(dts), pick(.5), pick(.95), pick(.99), dts[-1]) +
              ", ".join("> %g ms: %d" % (th, sum(1 for d in dts if d > th)) for th in thresholds))
    dts = [1000.0 * (b - a) for a, b in every]
    if not dts:
        return
    for th in thresholds:
        late = [(a, b) for a, b in every if 1000.0 * (b - a) > th]
        line = "  all   > %g ms: %d of %d frames (%.2f%%), %.0f ms in all" % (
            th, len(late), len(dts), 100.0 * len(late) / len(dts), sum(1000.0 * (b - a) for a, b in late))
        if baton and late:
            who, why, total = Counter(), Counter(), 0.0
            for a, b in late:
                total += b - a
                for s, e, name, reason in baton:
                    if e > a and s < b:
                        who[name] += min(b, e) - max(a, s)
                        why[(name, reason)] += min(b, e) - max(a, s)
            line += "; the baton: " + ", ".join("%s %.0f%%" % (n, 100 * v / total) for n, v in who.most_common(4))
            line += ", nobody %.0f%%" % (100 * (1 - sum(who.values()) / total))
            line += "; given up by: " + ", ".join("%s %s %.0f%%" % (n, r, 100 * v / total)
                                                for (n, r), v in why.most_common(4))
        print(line)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--threshold", type=float, action="append", default=None, metavar="MS",
                   help="frame intervals longer than this are late (default: 20 and 12.5)")
    p.add_argument("--skip-tail", type=float, default=1300, metavar="MS")
    a = p.parse_args()
    for run in a.runs:
        report(run, a.threshold or [20.0, 12.5], a.skip_tail)


if __name__ == "__main__":
    main()
