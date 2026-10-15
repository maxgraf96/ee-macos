#!/usr/bin/env python3
"""The lobby test: Empire Earth's Multiplayer lobby (WONAPI's GDI window over
the game's DirectDraw frame) driven by scripted input, headless, in strict
mode, with the kit's software Direct3D 7 renderer and a fresh user directory.
Every scenario in tests/lobby/*.script starts at the main menu and must

  - run its script to the end: every click reaches its target (the game's own
    pointer at the menu, the system pointer in the lobby) and every `wait
    change` sees the screen it waits for;
  - end through Exit Game (the game's own ExitProcess, not the deadline), with
    exit code 0, no strict-mode stop, no guest fault and no import called
    without an implementation;
  - write the frames its script dumps: build/lobby/<scenario>/<name>.png.

Per scenario:

  lobby-open      the lobby's first frame differs from the menu's, and the
                  menu afterwards is the menu again (the game presents again)
  lobby-lan-host  the chat line appears in the room's chat history, the Map
                  Type list opens over the options, and the host's
                  LanGameAdvert for "mac-ref" goes out on UDP 8585 (ee_boot
                  --lan-pcap, decoded with tools/lan/eelan.py)

Every scenario also times the lobby's paints (RECOMP_GDI_TIMING, BeginPaint
to the end of EndPaint) and fails when the median exceeds 25 ms: the kit's
GDI fast paths make one 1-3 ms, its per-pixel path 80 ms or more
(docs/reference/lobby.md §13.5).

The lobby binds the LAN ports (UDP 8585, UDP and TCP 33336), so scenarios run
one at a time, each holding tools/game_ports.py's lock: no other game server
of this user runs beside it, in any checkout.

    .venv/bin/python tests/lobby/run_lobby.py [--kit DIR] [--no-build] [SCENARIO ...]

Run through tools/test.py --lobby. docs/reference/lobby.md has the scenarios
and what they showed.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/boot"))
sys.path.insert(0, str(ROOT / "tools"))
import seed_user  # noqa: E402
from game_ports import game_ports_lock  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "build/lobby"
KNOWN_UNIMPLEMENTED = set()
SECONDS = {"lobby-open": 150, "lobby-lan-host": 240}


def load_png(path):
    """(w, h, rows of RGB bytes) of an 8-bit RGB PNG, as input_script writes them."""
    data = path.read_bytes()
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
    raw = zlib.decompress(idat)
    stride, rows, prev = w * 3 + 1, [], bytearray(w * 3)
    for y in range(h):
        kind, line = raw[y * stride], bytearray(raw[y * stride + 1:(y + 1) * stride])
        for i in range(len(line)):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def changed(a, b, box=None, step=1):
    """Pixels that differ by more than 24 in a channel, inside box (x0, y0, x1, y1)."""
    w, h, ra = a
    _, _, rb = b
    x0, y0, x1, y1 = box or (0, 0, w, h)
    n = 0
    for y in range(y0, y1, step):
        la, lb = ra[y], rb[y]
        if la[3 * x0:3 * x1] == lb[3 * x0:3 * x1]:
            continue
        for x in range(x0, x1, step):
            i = 3 * x
            if max(abs(la[i] - lb[i]), abs(la[i + 1] - lb[i + 1]), abs(la[i + 2] - lb[i + 2])) > 24:
                n += 1
    return n


def lan_messages(pcap):
    """eelan.py's decode of the run's UDP datagrams, one dict per message."""
    out = Path(tempfile.mkdtemp(prefix="ee-lobby-eelan-"))
    try:
        subprocess.run([sys.executable, "-I", str(ROOT / "tools/lan/eelan.py"), "export", str(pcap),
                        "--out-dir", str(out)], check=True, capture_output=True, text=True)
        path = out / "messages.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    finally:
        shutil.rmtree(out, ignore_errors=True)


PAINT_MEDIAN_MS = 25


def paint_times(log_text):
    """The milliseconds of each `gdi: paint` line (RECOMP_GDI_TIMING)."""
    return [float(m) for m in re.findall(r"gdi: paint \w+: ([\d.]+) ms", log_text)]


def check_scenario(name, out_dir, problems, log_text):
    frames = {p.stem: load_png(p) for p in out_dir.glob("*.png")}
    if name == "lobby-open":
        menu, lobby, again = (frames.get(k) for k in ("main-menu", "lobby-first", "main-menu-again"))
        if menu and lobby:
            total = menu[0] * menu[1]
            moved = changed(menu, lobby, step=2) * 4
            if moved < total // 2:
                problems.append("the lobby's frame differs from the menu's in only %.1f%%" %
                                (100.0 * moved / total))
        if menu and again:
            total = menu[0] * menu[1]
            moved = changed(menu, again, step=2) * 4
            # The game's cursor and the button under it differ; the menu does not.
            if moved > total * 0.05:
                problems.append("the menu after the lobby differs from the one before in %.1f%%" %
                                (100.0 * moved / total))
    if name == "lobby-lan-host":
        room, chat, dropdown = (frames.get(k) for k in ("room", "room-chat", "map-type-open"))
        if room and chat:
            # The chat history's second line, where "<player>: hello mac" lands.
            text = changed(room, chat, (18, 156, 578, 180))
            if text < 150:
                problems.append("no chat line in the room's history (%d pixels changed)" % text)
        if chat and dropdown:
            listed = changed(chat, dropdown, (38, 512, 190, 640))
            if listed < 2000:
                problems.append("the Map Type list did not open (%d pixels changed)" % listed)
        pcap = out_dir.parent / ("%s.pcap" % name)
        messages = lan_messages(pcap) if pcap.exists() else []
        adverts = [m for m in messages if m.get("msg", {}).get("name") == "LanGameAdvert" and
                   m.get("dst", "").endswith(":8585") and m.get("msg", {}).get("game") == "mac-ref"]
        if not adverts:
            problems.append("no LanGameAdvert for mac-ref on UDP 8585 in %s (%d messages)" %
                            (pcap.relative_to(ROOT), len(messages)))
        else:
            a = adverts[0]["msg"]
            print("     %s: LanGameAdvert %r to %s, lobby port %s, %s/%s players; %d LAN messages" %
                  (name, a.get("game"), adverts[0]["dst"], a.get("lobby_port"), a.get("players"),
                   a.get("max_players"), len(messages)))


def run(name, script, seconds, keep_user):
    """Runs one scenario; returns (name, problems, log path, dumps)."""
    user = Path(tempfile.mkdtemp(prefix="ee-lobby-%s-" % name))
    out_dir = OUT / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    log = OUT / ("%s.log" % name)
    pcap = OUT / ("%s.pcap" % name)
    try:
        seed_user.main(["--user", str(user)])
        env = dict(os.environ, RECOMP_STRICT="1", RECOMP_GDI_TIMING="1")
        with game_ports_lock("lobby %s (%s)" % (name, ROOT)):
            result = subprocess.run([str(ROOT / "build/recomp/ee_boot"), "--user", str(user),
                                     "--seconds", str(seconds), "--script", str(script),
                                     "--script-out", str(out_dir), "--lan-pcap", str(pcap)],
                                    cwd=ROOT, capture_output=True, text=True,
                                    timeout=seconds + 180, env=env)
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
        if "deadline: posting WM_CLOSE" in out or "stop: guest called ExitProcess" not in out:
            problems.append("Exit Game did not end the game (%s)" %
                            (re.search(r"^stop: (.*)", out, re.M) or [None, "?"])[1])
        dumps = re.findall(r"^\[script\] dump (\S+): (\d+)x(\d+) at frame \d+, hash (\w+), "
                           r"differs from the previous dump in ([\d.]+)%", out, re.M)
        wanted = re.findall(r"^\s*dump\s+(\S+)", script.read_text(), re.M)
        written = [d[0] for d in dumps]
        if written != wanted:
            problems.append("frames written %s, the script dumps %s" % (written, wanted))
        paints = sorted(paint_times(out))
        if paints:
            median = paints[len(paints) // 2]
            print("     %s: %d paints, median %.1f ms, 90th percentile %.1f ms, longest %.1f ms" %
                  (name, len(paints), median, paints[int(len(paints) * 0.9)], paints[-1]))
            if median > PAINT_MEDIAN_MS:
                problems.append("the lobby's median paint took %.1f ms (more than %d)" %
                                (median, PAINT_MEDIAN_MS))
        else:
            problems.append("no paint was timed (RECOMP_GDI_TIMING)")
        if not problems:
            check_scenario(name, out_dir, problems, out)
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
    for script in scripts:
        name, problems, log, dumps = run(script.stem, script, SECONDS.get(script.stem, 240),
                                         args.keep_user)
        print("%s %s (%d frames; log %s)" % ("ok  " if not problems else "FAIL", name, len(dumps),
                                             log.relative_to(ROOT)))
        for d in dumps:
            print("       %-22s %sx%s  %5s%% changed" % (d[0], d[1], d[2], d[4]))
        for p in problems:
            print("     - %s" % p)
        failures += bool(problems)
    print("lobby: %d of %d scenarios passed; frames in %s" %
          (len(scripts) - failures, len(scripts), OUT.relative_to(ROOT)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
