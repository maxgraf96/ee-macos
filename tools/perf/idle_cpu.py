#!/usr/bin/env python3
"""What the windowed app costs while nobody touches it: the main menu, then a
saved game left running, each for a fixed time (docs/reference/performance.md,
"Scheduling", the run thread's wait).

For each phase it reports, from the kernel's own accounting of the process
(proc_pid_rusage, RUSAGE_INFO_V6):

  - CPU: user plus system time, in percent of one core;
  - power: the kernel's energy estimate for the process (ri_energy_nj), in mW,
    and the share of its cycles on performance cores;
  - the mean clock of the cores it ran on (cycles / CPU time) and the
    instructions it retired a second;
  - wake-ups a second (interrupt and package-idle);

and per thread (libproc PROC_PIDTHREADID64INFO): the run thread's CPU (the
process's first thread) and the busiest others'. With RECOMP_FRAME_TIMINGS it
counts the frames the guest sealed a second, those the presenter dropped
included (a window behind others loses frames there, not in the game).

    tools/perf/idle_cpu.py [--app APP] [--save SAVE] --out DIR [--width W --height H]
                           [--menu-seconds 40] [--game-seconds 60] [--ptps N]
                           [--sample] [--env K=V ...]

The app runs silently, behind other windows (RECOMP_SCRIPT_BACKGROUND), on a
scratch profile seeded by tools/boot/seed_user.py with SAVE as its only saved
game; the script loads it from the main menu (Single Player -> Play Saved Game
-> Load). --ptps pins the simulation's rate (Min = Max PTs per Second): the
game tunes it from its own measured tick time, so two builds compared at the
game's choice may be doing different amounts of work. --sample takes a
`sample` profile 10 s into each phase; it suspends the process every
millisecond and changes how the threads are scheduled, so that phase's numbers
are not the unprofiled ones. The game opens its ports at the main menu, so the
run holds tools/game_ports.py's lock.
"""
import argparse
import csv
import ctypes
import json
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

SAVE = ROOT / "analysis/perf/saves/AutoSave 2026.10.15 - 20h.57m.31s.ees"
SCRIPT = """# tools/perf/idle_cpu.py: the main menu, then a saved game, both left alone.
timeout {timeout}
pointer f32 [0x9197a8]+4 [0x9197a8]+8
hold 120
wait frames 30
key ESCAPE
wait 2500
key ESCAPE
wait 2500
key ESCAPE
wait size 1024 768 within 180000
wait 5000
moveby 1 1
wait 300
log menu-idle-begin
wait {menu}
log menu-idle-end
click 511 268                 # Single Player
wait change within 15000
wait 1000
click 748 452                 # Play Saved Game
wait change within 20000
wait 1500
click 295 114                 # the first (only) saved game in the list
wait 1500
click 511 728                 # Load
wait size {width} {height} within 240000
wait change 0.5 within 300000
wait {settle}
log game-idle-begin
wait {game}
log game-idle-end
quit
"""
MARKS = ("menu-idle-begin", "menu-idle-end", "game-idle-begin", "game-idle-end")

# --- the kernel's accounting -------------------------------------------------
libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
RUSAGE_FIELDS = (
    "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size resident_size phys_footprint "
    "proc_start_abstime proc_exit_abstime child_user_time child_system_time child_pkg_idle_wkups "
    "child_interrupt_wkups child_pageins child_elapsed_abstime diskio_bytesread diskio_byteswritten "
    "cpu_time_qos_default cpu_time_qos_maintenance cpu_time_qos_background cpu_time_qos_utility "
    "cpu_time_qos_legacy cpu_time_qos_user_initiated cpu_time_qos_user_interactive billed_system_time "
    "serviced_system_time logical_writes lifetime_max_phys_footprint instructions cycles billed_energy "
    "serviced_energy interval_max_phys_footprint runnable_time flags user_ptime system_ptime pinstructions "
    "pcycles energy_nj penergy_nj secure_time_in_system secure_ptime_in_system neural_footprint "
    "lifetime_max_neural_footprint interval_max_neural_footprint").split()


class RusageV6(ctypes.Structure):
    _fields_ = ([("uuid", ctypes.c_uint8 * 16)] + [(f, ctypes.c_uint64) for f in RUSAGE_FIELDS]
                + [("reserved", ctypes.c_uint64 * 9)])


class ThreadInfo(ctypes.Structure):  # struct proc_threadinfo
    _fields_ = [("user", ctypes.c_uint64), ("system", ctypes.c_uint64), ("cpu", ctypes.c_int32),
                ("policy", ctypes.c_int32), ("run_state", ctypes.c_int32), ("flags", ctypes.c_int32),
                ("sleep", ctypes.c_int32), ("curpri", ctypes.c_int32), ("pri", ctypes.c_int32),
                ("maxpri", ctypes.c_int32), ("name", ctypes.c_char * 64)]


class Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


_tb = Timebase()
ctypes.CDLL(None).mach_timebase_info(ctypes.byref(_tb))
TICK_NS = _tb.numer / _tb.denom  # rusage times are in mach ticks; thread times in ns


def rusage(pid):
    r = RusageV6()
    if libproc.proc_pid_rusage(pid, 6, ctypes.byref(r)) != 0:
        return None
    return {f: getattr(r, f) for f in RUSAGE_FIELDS}


def threads(pid):
    """{thread id: CPU seconds} (PROC_PIDLISTTHREADIDS, PROC_PIDTHREADID64INFO)."""
    ids = (ctypes.c_uint64 * 512)()
    n = libproc.proc_pidinfo(pid, 28, 0, ids, ctypes.sizeof(ids)) // 8
    out = {}
    for i in range(max(n, 0)):
        ti = ThreadInfo()
        if libproc.proc_pidinfo(pid, 15, ids[i], ctypes.byref(ti), ctypes.sizeof(ti)) == ctypes.sizeof(ti):
            out[ids[i]] = (ti.user + ti.system) / 1e9
    return out


def phase_numbers(a, b):
    dt = b["t"] - a["t"]
    ra, rb = a["rusage"], b["rusage"]
    cpu = (rb["user_time"] + rb["system_time"] - ra["user_time"] - ra["system_time"]) * TICK_NS / 1e9
    cycles = rb["cycles"] - ra["cycles"]
    run_thread = min(b["threads"]) if b["threads"] else None
    per = sorted(((b["threads"][t] - a["threads"].get(t, 0.0)) / dt * 100, t) for t in b["threads"])
    others = [round(v, 1) for v, t in reversed(per) if t != run_thread][:3]
    main = ((b["threads"][run_thread] - a["threads"].get(run_thread, 0.0)) / dt * 100) if run_thread else None
    return {
        "seconds": round(dt, 1),
        "cpu_percent": round(100 * cpu / dt, 1),
        "power_mw": round((rb["energy_nj"] - ra["energy_nj"]) / 1e6 / dt),
        "p_core_cycle_share": round((rb["pcycles"] - ra["pcycles"]) / cycles, 2) if cycles else None,
        "ghz": round(cycles / 1e9 / cpu, 2) if cpu else None,
        "ginstructions_per_s": round((rb["instructions"] - ra["instructions"]) / 1e9 / dt, 2),
        "interrupt_wakeups_per_s": round((rb["interrupt_wkups"] - ra["interrupt_wkups"]) / dt),
        "idle_wakeups_per_s": round((rb["pkg_idle_wkups"] - ra["pkg_idle_wkups"]) / dt),
        "run_thread_cpu_percent": round(main, 1) if main is not None else None,
        "busiest_other_threads_cpu_percent": others,
    }


def sealed_frames(path, marks, offset):
    """Frames the guest sealed a second in each phase: presented (repeat 0) plus dropped."""
    rows = list(csv.DictReader(open(path)))
    fps = {}
    for ph in ("menu", "game"):
        b, e = marks.get(ph + "-idle-begin"), marks.get(ph + "-idle-end")
        w = [r for r in rows if b and e and b + 2 <= float(r["sealed_s"]) + offset <= e - 1]
        if w:
            n = sum(1 for r in w if r["repeat"] == "0") + int(w[-1]["drops"]) - int(w[0]["drops"])
            fps[ph] = round(n / (e - 1 - b - 2), 1)
    return fps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=ROOT / "build/EmpireEarth.app")
    ap.add_argument("--save", type=Path, default=SAVE)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1200)
    ap.add_argument("--menu-seconds", type=int, default=40)
    ap.add_argument("--game-seconds", type=int, default=60)
    ap.add_argument("--settle-seconds", type=int, default=20, help="after the load, before measuring")
    ap.add_argument("--ptps", type=int, default=None, help="pin Min = Max PTs per Second")
    ap.add_argument("--sample", action="store_true", help="a `sample` profile 10 s into each phase")
    ap.add_argument("--env", action="append", default=[], help="K=V for the app")
    args = ap.parse_args(argv)

    out = args.out.resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    user = Path(tempfile.mkdtemp(prefix="ee-idle-"))
    seed_user.main(["--user", str(user), "--width", str(args.width), "--height", str(args.height)])
    if args.ptps is not None:
        reg = json.loads((user / "registry.json").read_text())
        for name in ("Min PTs per Second", "Max PTs per Second"):
            reg[seed_user.HKCU_SSSI][name] = {"type": seed_user.REG_DWORD, "data": args.ptps}
        (user / "registry.json").write_text(json.dumps(reg, indent=1))
    saved = user / "Data" / "Saved Games"
    saved.mkdir(parents=True)
    shutil.copy(args.save, saved / args.save.name)
    total = args.menu_seconds + args.settle_seconds + args.game_seconds + 600
    (out / "idle.script").write_text(SCRIPT.format(
        timeout=total * 1000, menu=args.menu_seconds * 1000, settle=args.settle_seconds * 1000,
        game=args.game_seconds * 1000, width=args.width, height=args.height))

    env = {"RECOMP_PROFILE_DIR": str(user), "RECOMP_REGISTRY": str(user / "registry.json"),
           "RECOMP_INPUT_SCRIPT": str(out / "idle.script"), "RECOMP_INPUT_SCRIPT_OUT": str(out / "frames"),
           "RECOMP_SCRIPT_BACKGROUND": "1", "RECOMP_HOST_NO_AUDIO": "1", "SDL_AUDIO_DRIVER": "dummy",
           "SDL_AUDIODRIVER": "dummy", "RECOMP_FRAME_TIMINGS": str(out / "frames.csv")}
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v
    app = args.app.resolve()
    log = out / "app.log"
    cmd = ["/usr/bin/open", "-g", "-n", "-W", "--stdout", str(log), "--stderr", str(log)]
    for kv in sorted(env.items()):
        cmd += ["--env", "%s=%s" % kv]
    cmd.append(str(app))
    uptime_offset = time.time() - time.clock_gettime(time.CLOCK_UPTIME_RAW)

    done = threading.Event()
    marks, snaps = {}, []

    def watch():
        sampled = set()
        pid = None
        while not done.wait(0.5):
            t = time.time()
            text = log.read_text(errors="replace") if log.exists() else ""
            for m in re.finditer(r"\[script\] line \d+: (%s)" % "|".join(MARKS), text):
                marks.setdefault(m.group(1), t)
            if pid is None:
                found = subprocess.run(["/usr/bin/pgrep", "-f", str(app / "Contents/MacOS/")],
                                       capture_output=True, text=True).stdout.split()
                pid = int(found[0]) if found else None
                if pid is None:
                    continue
            r = rusage(pid)
            if r is None:
                continue
            snaps.append({"t": t, "rusage": r, "threads": threads(pid)})
            for ph in ("menu", "game"):
                begin = marks.get(ph + "-idle-begin")
                if args.sample and begin and ph not in sampled and t - begin > 10:
                    sampled.add(ph)
                    subprocess.Popen(["/usr/bin/sample", str(pid), "5", "1", "-mayDie", "-file",
                                      str(out / ("sample-%s.txt" % ph))],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with game_ports_lock("tools/perf/idle_cpu.py (%s)" % out):
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        subprocess.run(cmd, timeout=total + 600)
        done.set()
        watcher.join(5)
    text = log.read_text(errors="replace") if log.exists() else ""
    for name in MARKS:  # a mark logged just before the quit, seen only now
        if name not in marks and re.search(r"\[script\] line \d+: %s\b" % name, text) and snaps:
            marks[name] = snaps[-1]["t"]

    result = {"app": str(app), "ptps": args.ptps, "marks": marks, "phases": {}}
    for ph in ("menu", "game"):
        b, e = marks.get(ph + "-idle-begin"), marks.get(ph + "-idle-end")
        win = [s for s in snaps if b and e and b + 2 <= s["t"] <= e - 1]
        if len(win) >= 2:
            result["phases"][ph] = phase_numbers(win[0], win[-1])
    if (out / "frames.csv").exists():
        for ph, fps in sealed_frames(out / "frames.csv", marks, uptime_offset).items():
            result["phases"].setdefault(ph, {})["frames_sealed_per_s"] = fps
    (out / "summary.json").write_text(json.dumps(result, indent=1))
    for ph, r in result["phases"].items():
        print("%s: CPU %s%% of a core (run thread %s%%, next %s), %s mW (%s on P cores) at %s GHz, "
              "%s G instructions/s, %s interrupt wake-ups/s, %s frames/s" % (
                  ph, r.get("cpu_percent"), r.get("run_thread_cpu_percent"),
                  r.get("busiest_other_threads_cpu_percent"), r.get("power_mw"), r.get("p_core_cycle_share"),
                  r.get("ghz"), r.get("ginstructions_per_s"), r.get("interrupt_wakeups_per_s"),
                  r.get("frames_sealed_per_s")))
    shutil.rmtree(user, ignore_errors=True)
    return 0 if len(result["phases"]) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
