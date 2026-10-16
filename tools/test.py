#!/usr/bin/env python3
"""Test Empire Earth on recomp-kit.

    .venv/bin/python tools/test.py                 # this repository's checks, then the kit's portable suites
    .venv/bin/python tools/test.py --m1 [--regenerate]   # the M1 harness (tests/m1/)
    .venv/bin/python tools/test.py --eh [--regenerate]   # the EH harness (tests/eh/)
    .venv/bin/python tools/test.py --boot [--regenerate] # the whole game booted headless to the
                                                   # main menu (tests/boot/, docs/reference/boot.md)
    .venv/bin/python tools/test.py --menus [SCENARIO...] # scripted input through the menus and into
                                                   # a game, headless (tests/menus/, docs/reference/menus.md)
    .venv/bin/python tools/test.py --lobby [SCENARIO...] # the Multiplayer lobby (WONAPI's GDI window over
                                                   # the game's frame), headless (tests/lobby/, docs/reference/lobby.md)
    .venv/bin/python tools/test.py --app-frames    # the windowed app's frame limit on Metal, in the
                                                   # background: the default 60 and "original"
                                                   # (tests/menus/run_app.py, docs/reference/performance.md)
    .venv/bin/python tools/test.py --simparity [OPTIONS]  # the single-player checksum stream, run
                                                   # twice on the Mac; both must agree, every
                                                   # autosave must run and succeed, and the first
                                                   # save, loaded, must match Windows' load of it
                                                   # (default: 3 minutes, no civ timer, an autosave
                                                   # a minute, against legion's streams if present;
                                                   # tools/simparity/, docs/reference/simparity.md)
    .venv/bin/python tools/test.py --m1-replay [PACKS...]  # the Mac M1 replay (tests/m1/replay/):
                                                   # self-tests, or replay packs with both builds
    .venv/bin/python tools/test.py --native ...    # any other option: kit/tools/test.py's, for this game

--kit DIR (or RECOMP_KIT_DIR) tests against another kit checkout than kit/.
See docs/reference/build.md.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitpath  # noqa: E402


def main(argv):
    if "--m1" in argv:
        argv = [a for a in argv if a != "--m1"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/m1/run_harness.py")] + argv)
    if "--m1-replay" in argv:
        argv = [a for a in argv if a != "--m1-replay"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/m1/replay/run_replay.py")] + argv)
    if "--boot" in argv:
        argv = [a for a in argv if a != "--boot"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/boot/run_boot.py")] + argv)
    if "--menus" in argv:
        argv = [a for a in argv if a != "--menus"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/menus/run_menus.py")] + argv)
    if "--app-frames" in argv:
        argv = [a for a in argv if a != "--app-frames"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/menus/run_app.py")] + argv)
    if "--lobby" in argv:
        argv = [a for a in argv if a != "--lobby"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/lobby/run_lobby.py")] + argv)
    if "--simparity" in argv:
        argv = [a for a in argv if a != "--simparity"]
        # Without options: two 3-minute games with no civilization timer, so nothing
        # but the simulation feeds them, and an autosave every minute (each save
        # reschedules the computer player; a failed one did not, and the Mac left
        # Windows at PT 24301: simparity.md §9). Every save must succeed, and the
        # games must agree with each other and with legion's run of the same
        # scenario when its stream is here (analysis/ is not in git), up to a save
        # that landed in different CTs (thread timing, §9.3).
        # Every autosave posted must run (the counter watch, simparity.md §10.5), and the
        # first run's first save, loaded, must give the stream Windows got from loading the
        # Mac's save of the same state (legion 0066), when that stream is here (§10.2).
        argv = argv or ["--no-civ-timer", "--minutes", "3", "--autosave-ms", "60000",
                        "--players", "10", "--calls", "43dcf4/1,42f912/1,43ded9/0,540aca/0,42f783/2",
                        "--autosave-watch",
                        "--against", str(kitpath.ROOT / "analysis/simparity/win/win-as-1/stream.csv"),
                        "--then-load", "2", "--load-against",
                        str(kitpath.ROOT / "analysis/simparity/win/win-load-mac-2423/stream.csv")]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tools/simparity/run.py")] + argv)
    if "--eh" in argv:
        argv = [a for a in argv if a != "--eh"]
        return subprocess.call([sys.executable, str(kitpath.ROOT / "tests/eh/run_eh.py")] + argv)
    kit, rest = kitpath.take_kit(list(argv))
    if not rest:
        # This repository's own checks (game.toml, globals.toml, tools), against the chosen kit.
        suites = sorted(str(p) for p in (kitpath.ROOT / "tests").glob("test_*.py"))
        mine = subprocess.call([sys.executable, "-m", "pytest", "-q"] + suites,
                               cwd=kitpath.ROOT, env=dict(os.environ, RECOMP_KIT_DIR=str(kit)))
        if mine:
            return mine
    return kitpath.run_kit_tool("test.py", argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
