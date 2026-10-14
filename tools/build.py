#!/usr/bin/env python3
"""Build Empire Earth with recomp-kit: every option is kit/tools/build.py's.

    .venv/bin/python tools/build.py --regenerate --target gen [--kit DIR]
    .venv/bin/python tools/build.py --regenerate --cmake-target ee_m1_harness

--kit DIR (or RECOMP_KIT_DIR) builds against another kit checkout than the
kit/ submodule. See docs/reference/build.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitpath  # noqa: E402

if __name__ == "__main__":
    sys.exit(kitpath.run_kit_tool("build.py", sys.argv[1:]))
