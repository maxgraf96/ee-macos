#!/usr/bin/env python3
"""A live LAN session on this Mac: the native game, headless or the player's
app on Metal, against another machine's (docs/reference/lansession.md).

    .venv/bin/python -I tools/lan/mac_session.py run host|join [--out DIR] [--minutes M] [--name G]
        [--app EmpireEarth.app --home DIR]
    .venv/bin/python -I tools/lan/mac_session.py say DIR 'click 660 206' 'dump start'
    .venv/bin/python -I tools/lan/mac_session.py status DIR
    .venv/bin/python -I tools/lan/mac_session.py checksums DIR [--frida netcode_hooks.json]
    .venv/bin/python -I tools/lan/mac_session.py report DIR [--lag]

`run` seeds a fresh user directory, holds tools/game_ports.py's lock (one game
server on this Mac at a time) and runs build/recomp/ee_boot with:

  - a script (tools/lan/scripts/<host|join>.script) that reaches the LAN page
    (`join`) or hosts `mac-ref` and opens its room (`host`), then follows
    DIR/live.script (the kit's `follow` step): every line appended to that file
    is the game's next step;
  - --lan-pcap DIR/lan.pcap (the game's UDP and TCP traffic, wall-clock times,
    readable while the game runs) and RECOMP_NET_TRACE=1 (every socket call);
  - its frames in DIR/frames, its output in DIR/ee_boot.log.

At the end it copies the game's own logs (OOS*.log, *Error*.log) out of the
user directory and deletes the directory (it holds the player's CD key).

With --app, `run` starts the player's app instead (tools/package_app.py; its
first run done in --home, the app's EE_HOME) through Launch Services
(`open -n -W`, so the window has the focus and DirectInput is acquired),
with:

  - tools/lan/scripts/app-<role>.script as RECOMP_INPUT_SCRIPT (Escape
    through the movies, then as above), its frames in DIR/frames;
  - RECOMP_NET_PCAP=DIR/lan.pcap (the kit's capture: the same traffic as
    --lan-pcap), RECOMP_NET_TRACE=1, RECOMP_FRAME_TIMINGS=DIR/frames.csv
    (every displayed frame) and DYLD_INSERT_LIBRARIES if it is set here;
  - the host process's CPU time and memory every 10 s in DIR/cpu.csv.

The game's output is <home>/Logs/game.log while it runs, copied to
DIR/game.log at the end with the game's own logs. The home stays (it holds
the build); delete it when done (it holds the CD key).

`say` appends steps to DIR/live.script. `status` prints the script's latest
lines and what the pcap holds so far: the lobby events (adverts, joins, the
roster, chat, ready, StartGame), the game's ticks, checksum responses and any
removal for out-of-sync.

`checksums` lines up, tick by tick, the world checksums of the session: the
0x32 responses on the wire (each client's own value, by sender) and, with
--frida, the Windows peer's windows/frida/netcode_hooks.js snapshot (`world`:
the value that machine computed; `logged`: a host's tally, by slot). It
prints how many ticks each pair shares, how many agree, and the first that
does not.

DIR defaults to analysis/lan-sessions/<UTC time>-<host|join> (ignored: the
evidence holds game state). Run the binary under whatever wrapper this machine
needs (`env DYLD_INSERT_LIBRARIES=...` reaches ee_boot through this script).
"""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/lan"))
import eelan  # noqa: E402

SCRIPTS = ROOT / "tools/lan/scripts"


def cmd_run(args):
    import seed_user
    from game_ports import game_ports_lock
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).resolve() if args.out else ROOT / "analysis/lan-sessions" / (
        "%s-%s" % (stamp, args.role))
    out.mkdir(parents=True, exist_ok=True)
    live = out / "live.script"
    live.touch()
    kind = ("app-" if args.app else "") + args.role
    script = out / ("%s.script" % kind)
    text = (SCRIPTS / ("%s.script" % kind)).read_text().replace("@LIVE@", str(live))
    script.write_text(text.replace("mac-ref", args.name))
    if args.app:
        return run_app(args, out, script)
    user = Path(tempfile.mkdtemp(prefix="ee-lan-session-"))
    seconds = int(args.minutes * 60)
    try:
        seed_user.main(["--user", str(user)])
        env = dict(os.environ, RECOMP_NET_TRACE="1")
        env.setdefault("RECOMP_STRICT", "1")
        command = [str(ROOT / "build/recomp/ee_boot"), "--user", str(user), "--seconds", str(seconds),
                   "--script", str(script), "--script-out", str(out / "frames"),
                   "--lan-pcap", str(out / "lan.pcap")]
        print("session %s: %s" % (args.role, out), flush=True)
        with game_ports_lock("lan session %s (%s)" % (args.role, out)), \
                open(out / "ee_boot.log", "w") as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=seconds + 300)
        print("ee_boot exit %d" % result.returncode, flush=True)
        kept = []
        for path in user.rglob("*"):
            if path.is_file() and (re.match(r"(?i)oos", path.name) or "error" in path.name.lower()):
                shutil.copy2(path, out / path.name)
                kept.append(path.name)
        print("game logs kept: %s" % (", ".join(kept) or "none"), flush=True)
        return result.returncode
    finally:
        shutil.rmtree(user, ignore_errors=True)


def host_pid(app):
    """The app's game process: the launcher exec()s the host, so it is the
    process running Contents/MacOS/EmpireEarth{,Host} of this bundle."""
    found = subprocess.run(["/usr/bin/pgrep", "-f", str(app / "Contents/MacOS/EmpireEarth")],
                           capture_output=True, text=True).stdout.split()
    return int(found[0]) if found else None


def cpu_seconds(text):
    """ps's cputime ([[dd-]hh:]mm:ss.cc) in seconds."""
    days, _, rest = text.strip().rpartition("-")
    parts = [float(p) for p in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return (int(days) if days else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def run_app(args, out, script):
    """The player's app through Launch Services, holding the game ports, with
    the CPU sampled until it exits (see the top)."""
    import threading
    import time
    from game_ports import game_ports_lock
    app, home = Path(args.app).resolve(), Path(args.home).resolve()
    env = {"EE_HOME": str(home), "RECOMP_INPUT_SCRIPT": str(script),
           "RECOMP_INPUT_SCRIPT_OUT": str(out / "frames"), "RECOMP_NET_PCAP": str(out / "lan.pcap"),
           "RECOMP_NET_TRACE": "1", "RECOMP_FRAME_TIMINGS": str(out / "frames.csv")}
    if os.environ.get("DYLD_INSERT_LIBRARIES"):
        env["DYLD_INSERT_LIBRARIES"] = os.environ["DYLD_INSERT_LIBRARIES"]
    # frames.csv times are the presenter's (mach absolute time): the offset to the wall clock.
    offset = time.time() - time.clock_gettime(time.CLOCK_UPTIME_RAW)
    (out / "session.json").write_text(json.dumps({"app": str(app), "home": str(home), "env": env,
                                                  "uptime_offset": offset}, indent=1) + "\n")
    command = ["/usr/bin/open", "-n", "-W"]
    for kv in sorted(env.items()):
        command += ["--env", "%s=%s" % kv]
    command.append(str(app))
    if args.direct:
        # The app's executable as this process's child: macOS then counts
        # this terminal as the app's responsible process, and the terminal's
        # Local Network access covers the game (an app started by Launch
        # Services needs its own, which only the user can grant: macOS 15
        # answers its LAN connects with EHOSTUNREACH meanwhile). `open -a`
        # then brings the running instance forward, so its window has the
        # focus as a Finder start's would.
        command = [str(app / "Contents/MacOS/EmpireEarth")]
    seconds = int(args.minutes * 60)
    done = threading.Event()

    def sample():
        with open(out / "cpu.csv", "w") as f:
            f.write("wall,pid,cpu_s,rss_kb\n")
            while not done.wait(10):
                pid = host_pid(app)
                if not pid:
                    continue
                ps = subprocess.run(["/bin/ps", "-o", "cputime=,rss=", "-p", str(pid)],
                                    capture_output=True, text=True).stdout.split()
                if len(ps) == 2:
                    f.write("%.0f,%d,%.2f,%s\n" % (time.time(), pid, cpu_seconds(ps[0]), ps[1]))
                    f.flush()

    print("session %s (app): %s" % (args.role, out), flush=True)
    wait = args.wait_ports * 60 if args.wait_ports else None  # None: block in the kernel
    with game_ports_lock("lan session %s, app (%s)" % (args.role, out), timeout=wait):
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            if args.direct:
                proc = subprocess.Popen(command, env=dict(os.environ, **env),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(6)
                before = sorted(int(p) for p in subprocess.run(
                    ["/usr/bin/pgrep", "-f", str(app / "Contents/MacOS/")], capture_output=True,
                    text=True).stdout.split())
                subprocess.run(["/usr/bin/open", "-a", str(app)])
                time.sleep(3)
                after = sorted(int(p) for p in subprocess.run(
                    ["/usr/bin/pgrep", "-f", str(app / "Contents/MacOS/")], capture_output=True,
                    text=True).stdout.split())
                for extra in set(after) - set(before):
                    print("open started a second instance (pid %d): stopped" % extra, flush=True)
                    os.kill(extra, 9)
                code = proc.wait(timeout=seconds + 600)
            else:
                result = subprocess.run(command, timeout=seconds + 600)
                code = result.returncode
        except subprocess.TimeoutExpired:
            pid = host_pid(app)
            print("the app outlived its %d minutes; SIGTERM to %s" % (args.minutes, pid), flush=True)
            if pid:
                os.kill(pid, 15)
            code = 124
        finally:
            done.set()
            sampler.join()
    print("open exit %d" % code, flush=True)
    for name in ("game.log", "launcher.log"):
        if (home / "Logs" / name).exists():
            shutil.copy2(home / "Logs" / name, out / name)
    kept = []
    for path in home.rglob("*"):
        if path.is_file() and "Game" not in path.relative_to(home).parts[:1] and (
                re.match(r"(?i)oos", path.name) or "error" in path.name.lower()):
            shutil.copy2(path, out / path.name)
            kept.append(path.name)
    print("game logs kept: %s" % (", ".join(kept) or "none"), flush=True)
    return code


def game_log(d):
    """The session's game output: ee_boot's log, or the app's (live in its home)."""
    for name in ("ee_boot.log", "game.log"):
        if (d / name).exists():
            return d / name
    meta = d / "session.json"
    if meta.exists():
        return Path(json.loads(meta.read_text())["home"]) / "Logs/game.log"
    return d / "ee_boot.log"


def cmd_say(args):
    live = Path(args.dir) / "live.script"
    if not live.exists():
        sys.exit("no %s" % live)
    with open(live, "a") as f:
        for line in args.lines:
            f.write(line.rstrip("\n") + "\n")
    print("%s: +%d lines" % (live, len(args.lines)))


def summarize(pcap, limit):
    """The lobby and game events of a session's pcap, as short lines."""
    events, counts, checks = [], collections.Counter(), {}
    lobby = ("LanGameAdvert", "LanGameClosed", "LobbyJoin", "LobbyRoster", "LobbyChat",
             "LobbyChatRelay", "LobbyKeepAlive", "NEMClientConnectionRequest",
             "NEMClientConnectionRequestResponse", "tcp_fin")
    last_ct = None
    for ev in eelan.iter_events([str(pcap)]):
        if ev.get("proto") == "stats" or not ev.get("msg"):
            continue
        when = datetime.datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S")
        for m, carrier in eelan.walk(ev["msg"]):
            name = m.get("name", "")
            counts[name] += 1
            if name == "NEMCommunicationsTick":
                last_ct = (when, m.get("ct"), m.get("pts_to_perform"), m.get("generate_checksum"),
                           m.get("dump_the_world"))
                if m.get("dump_the_world"):
                    events.append("%s %s -> %s CT %s DUMP THE WORLD" %
                                  (when, ev["src"], ev["dst"], m.get("ct")))
            if name == "NEMWorldDatabaseChecksumResponse":
                checks[m.get("tick")] = m.get("checksum")
            if name in lobby and name == "LanGameAdvert" and counts[name] > 3:
                continue
            if name in lobby or name.startswith("LobbyGameData") or name in (
                    "NEMRemovePlayer", "EEMResign", "EEMInGameChat", "NEMPauseGame",
                    "NEMStartGame", "EEMStartLoading"):
                brief = {k: v for k, v in m.items()
                         if k not in ("name", "len", "key", "lan_type", "hex", "inputs")}
                events.append("%s %s -> %s %s %s" % (when, ev["src"], ev["dst"], name,
                                                     json.dumps(brief)[:160]))
    for e in events[-limit:]:
        print("  " + e)
    print("  counts: " + ", ".join("%s %d" % kv for kv in counts.most_common(14)))
    if last_ct:
        print("  last CT: %s ct=%s pts=%s gen_checksum=%s dump=%s" % last_ct)
    if checks:
        ticks = sorted(t for t in checks if t is not None)
        print("  0x32 checksum responses: %d, ticks %s..%s" % (len(ticks), ticks[0], ticks[-1]))


def cmd_status(args):
    d = Path(args.dir)
    log = game_log(d)
    if log.exists():
        lines = [l for l in log.read_text(errors="replace").splitlines() if l.startswith("[script]")]
        for l in lines[-args.script_lines:]:
            print(l)
        tail = log.read_text(errors="replace").splitlines()[-3:]
        if any("stop:" in l or "boot report" in l for l in tail):
            print("  (the run has ended)")
    pcap = d / "lan.pcap"
    if pcap.exists() and pcap.stat().st_size > 24:
        summarize(pcap, args.events)
    frames = sorted((d / "frames").glob("*.png"), key=lambda p: p.stat().st_mtime)
    if frames:
        print("  latest frames: " + ", ".join(p.name for p in frames[-4:]))


def wire_checksums(pcap):
    """{sender ip: {tick: checksum}} from the 0x32 responses in a pcap."""
    out = collections.defaultdict(dict)
    for ev in eelan.iter_events([str(pcap)]):
        if ev.get("proto") != "udp" or not ev.get("msg"):
            continue
        for m, _ in eelan.walk(ev["msg"]):
            if m.get("name") == "NEMWorldDatabaseChecksumResponse" and m.get("orig_slot") != 0xfe:
                out[ev["src"].split(":")[0]][m["tick"]] = m["checksum"] & 0xffffffff
    return out


def cmd_checksums(args):
    streams = {}
    for sender, ticks in wire_checksums(Path(args.dir) / "lan.pcap").items():
        if sender != "0.0.0.0":
            streams["wire 0x32 from %s" % sender] = ticks
        else:
            streams["wire 0x32 from this Mac"] = ticks
    if args.frida:
        snap = json.loads(Path(args.frida).read_text())
        world = {}
        for _, _, tick, _, flags, value in snap.get("world", []):
            if flags == 1:
                world[tick] = int(value, 16)
        streams["frida world (that machine's own)"] = world
        for slot in sorted({row[3] for row in snap.get("logged", [])}):
            streams["frida host tally, slot %d" % slot] = {
                tick: int(value, 16) for _, tick, value, s in snap["logged"] if s == slot}
    names = sorted(streams)
    for name in names:
        t = sorted(streams[name])
        print("%-36s %5d ticks%s" % (name, len(t), " (%d..%d)" % (t[0], t[-1]) if t else ""))
    worst = 0
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = sorted(set(streams[a]) & set(streams[b]))
            if not shared:
                continue
            bad = [t for t in shared if streams[a][t] != streams[b][t]]
            print("%s vs %s: %d shared ticks (%d..%d), %d agree%s" % (
                a, b, len(shared), shared[0], shared[-1], len(shared) - len(bad),
                "; FIRST MISMATCH tick %d: %08x vs %08x" % (bad[0], streams[a][bad[0]],
                                                           streams[b][bad[0]]) if bad else ""))
            worst = max(worst, len(bad))
    return 1 if worst else 0


def percentiles(values, *qs):
    v = sorted(values)
    return [v[min(len(v) - 1, int(q * len(v)))] if v else float("nan") for q in qs]


def cmd_report(args):
    """One session's numbers: the game's span and speed from the capture, the
    checksum exchange and its verdicts, the end of the game, and (an app run)
    the frame rate and the CPU."""
    d = Path(args.dir)
    cts, checks, events = {}, collections.Counter(), []
    start = None
    for ev in eelan.iter_events([str(d / "lan.pcap")]):
        if ev.get("proto") == "stats" or not ev.get("msg"):
            continue
        for m, _ in eelan.walk(ev["msg"]):
            name = m.get("name", "")
            if name == "NEMCommunicationsTick" and m.get("ct") is not None:
                cts.setdefault(m["ct"], (ev["ts"], m.get("pts_to_perform") or 0,
                                         m.get("generate_checksum"), m.get("dump_the_world")))
            elif name == "NEMWorldDatabaseChecksumResponse":
                checks[ev["src"].split(":")[0]] += 1
            elif name == "NEMStartGame" and start is None:
                start = ev["ts"]
            if name in ("NEMStartGame", "EEMResign", "NEMRemovePlayer", "EEMCivTimerExpired",
                        "NEMPauseGame", "LanGameClosed") or (name == "EEMLagStatus" and args.lag):
                brief = {k: v for k, v in m.items() if k not in ("name", "len", "key", "hex", "inputs")}
                events.append((ev["ts"], "%s %s -> %s %s %s" % (
                    datetime.datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S"), ev["src"],
                    ev["dst"], name, json.dumps(brief)[:120])))
    lags = sum(1 for ev in eelan.iter_events([str(d / "lan.pcap")]) if ev.get("msg")
               for m, _ in eelan.walk(ev["msg"]) if m.get("name") == "EEMLagStatus")
    # PTs 0xff mark the loading CTs (netcode.md §6.1), not play.
    play = sorted((ct, v) for ct, v in cts.items() if v[1] and v[1] != 0xff)
    print("capture: %d CTs (%s..%s), %d with PTs" % (
        len(cts), min(cts) if cts else "-", max(cts) if cts else "-", len(play)))
    if play:
        t0, t1 = play[0][1][0], play[-1][1][0]
        pts = sum(v[1] for _, v in play)
        rates = collections.Counter(v[1] for _, v in play)
        print("  play: %s..%s (%.1f min wall), %d PTs = %.1f min at full speed (15 PTs per "
              "350-ms CT, 42.9 PT/s); %.1f PT/s on average" % (
                  datetime.datetime.fromtimestamp(t0).strftime("%H:%M:%S"),
                  datetime.datetime.fromtimestamp(t1).strftime("%H:%M:%S"), (t1 - t0) / 60, pts,
                  pts / (15 / 0.35) / 60, pts / max(1.0, t1 - t0)))
        print("  PTs per CT: " + ", ".join("%d x%d" % kv for kv in sorted(rates.items())))
        per_min = collections.defaultdict(int)
        for _, v in play:
            per_min[int((v[0] - t0) // 60)] += v[1]
        print("  PT/s by minute: " + " ".join("%.0f" % (per_min[m] / 60.0)
                                              for m in range(max(per_min) + 1)))
        gen = sum(1 for _, v in play if v[2])
        dumps = [ct for ct, v in cts.items() if v[3]]
        print("  checksum requested on %d CTs; 0x32 responses by sender: %s; "
              "dump-the-world flags: %s" % (gen, dict(checks) or "none",
                                            dumps[:5] if dumps else "none"))
        print("  EEMLagStatus messages: %d" % lags)
    for _, line in sorted(events):
        print("  " + line)
    frames = d / "frames.csv"
    if frames.exists():
        import csv
        import time
        meta = d / "session.json"
        offset = json.loads(meta.read_text()).get("uptime_offset") if meta.exists() else None
        if offset is None or args.uptime_offset is not None:
            # Valid while this Mac has not slept since the run.
            offset = args.uptime_offset if args.uptime_offset is not None else (
                time.time() - time.clock_gettime(time.CLOCK_UPTIME_RAW))
        rows = [r for r in csv.DictReader(open(frames)) if r["presented_s"]]
        new = [float(r["presented_s"]) + offset for r in rows if r["repeat"] == "0"]
        sizes = collections.Counter((r["drawable_w"], r["drawable_h"]) for r in rows)
        print("frames: %d displayed, %d new; drawable %s" % (
            len(rows), len(new), "x".join(sizes.most_common(1)[0][0]) if sizes else "?"))

        def rate(t0, t1, label):
            bins = collections.Counter(int((t - t0) // 10) for t in new if t0 <= t < t1)
            fps = [bins[b] / 10.0 for b in range(int((t1 - t0) // 10))]
            if fps:
                print("  %s: new frames/s per 10 s: p10 %.0f, median %.0f, p90 %.0f, min %.0f "
                      "(%d bins)" % ((label,) + tuple(percentiles(fps, 0.1, 0.5, 0.9)) +
                                     (min(fps), len(fps))))
        if new:
            rate(min(new), max(new), "the whole run")
        if play:
            rate(play[0][1][0], play[-1][1][0], "in game (the capture's play CTs)")
    cpu = d / "cpu.csv"
    if cpu.exists():
        import csv
        rows = [r for r in csv.DictReader(open(cpu))]
        usage, ingame = [], []
        for a, b in zip(rows, rows[1:]):
            dt = float(b["wall"]) - float(a["wall"])
            if dt <= 0 or a["pid"] != b["pid"]:
                continue
            pct = 100.0 * (float(b["cpu_s"]) - float(a["cpu_s"])) / dt
            usage.append(pct)
            if play and play[0][1][0] <= float(b["wall"]) <= play[-1][1][0]:
                ingame.append(pct)
        if usage:
            print("cpu: %d samples, %% of one core: median %.0f, p90 %.0f, max %.0f; "
                  "max rss %.0f MB" % ((len(usage),) + tuple(percentiles(usage, 0.5, 0.9)) +
                                       (max(usage), max(int(r["rss_kb"]) for r in rows) / 1024.0)))
        if ingame:
            print("  in game: %d samples, median %.0f%%, p90 %.0f%%, max %.0f%%" % (
                (len(ingame),) + tuple(percentiles(ingame, 0.5, 0.9)) + (max(ingame),)))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("role", choices=("host", "join"))
    r.add_argument("--out", default=None)
    r.add_argument("--minutes", type=float, default=60)
    r.add_argument("--name", default="mac-ref", help="the hosted game's name (host)")
    r.add_argument("--app", default=None, help="the player's EmpireEarth.app instead of ee_boot")
    r.add_argument("--home", default=None, help="with --app: its EE_HOME, first run done")
    r.add_argument("--direct", action="store_true",
                   help="with --app: start its executable as a child of this process (see run_app)")
    r.add_argument("--wait-ports", type=float, default=None,
                   help="minutes to wait for another game server on this Mac to end "
                        "(default: until it does)")
    s = sub.add_parser("say")
    s.add_argument("dir")
    s.add_argument("lines", nargs="+")
    t = sub.add_parser("status")
    t.add_argument("dir")
    t.add_argument("--events", type=int, default=25)
    t.add_argument("--script-lines", type=int, default=8)
    c = sub.add_parser("checksums")
    c.add_argument("dir")
    c.add_argument("--frida", default=None, help="a netcode_hooks.json snapshot")
    rp = sub.add_parser("report")
    rp.add_argument("dir")
    rp.add_argument("--lag", action="store_true", help="list every EEMLagStatus too")
    rp.add_argument("--uptime-offset", type=float, default=None,
                    help="wall clock minus CLOCK_UPTIME_RAW during the run (else session.json's)")
    args = parser.parse_args()
    if args.cmd == "run" and bool(args.app) != bool(args.home):
        parser.error("--app and --home go together")
    return {"run": cmd_run, "say": cmd_say, "status": cmd_status,
            "checksums": cmd_checksums, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
