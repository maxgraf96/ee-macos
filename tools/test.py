#!/usr/bin/env python3
"""Test Empire Earth on recomp-kit.

    .venv/bin/python tools/test.py                 # this repository's checks, then the kit's portable suites
    .venv/bin/python tools/test.py --m1 [--prerebased] [--regenerate]   # the M1 harness (tests/m1/)
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
