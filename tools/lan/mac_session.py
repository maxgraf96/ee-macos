#!/usr/bin/env python3
"""A live LAN session on this Mac: the native game, headless, against another
machine's (docs/reference/lansession.md).

    .venv/bin/python -I tools/lan/mac_session.py run host|join [--out DIR] [--minutes M] [--name G]
    .venv/bin/python -I tools/lan/mac_session.py say DIR 'click 660 206' 'dump start'
    .venv/bin/python -I tools/lan/mac_session.py status DIR
    .venv/bin/python -I tools/lan/mac_session.py checksums DIR [--frida netcode_hooks.json]

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
    script = out / ("%s.script" % args.role)
    text = (SCRIPTS / ("%s.script" % args.role)).read_text().replace("@LIVE@", str(live))
    script.write_text(text.replace("mac-ref", args.name))
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
    log = d / "ee_boot.log"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("role", choices=("host", "join"))
    r.add_argument("--out", default=None)
    r.add_argument("--minutes", type=float, default=60)
    r.add_argument("--name", default="mac-ref", help="the hosted game's name (host)")
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
    args = parser.parse_args()
    return {"run": cmd_run, "say": cmd_say, "status": cmd_status,
            "checksums": cmd_checksums}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
