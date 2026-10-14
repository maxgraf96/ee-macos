#!/usr/bin/env python3
"""Build Empire Earth with recomp-kit: every option is kit/tools/build.py's.

    .venv/bin/python tools/build.py --regenerate --target gen [--kit DIR]
    .venv/bin/python tools/build.py --regenerate --cmake-target ee_m1_harness

--kit DIR (or RECOMP_KIT_DIR) builds against another kit checkout than the
kit/ submodule. --prerebased (TEMPORARY, until the kit's loader relocates
DLLs) builds the derived game directory tools/prerebase.py stages instead of
this one. See docs/reference/build.md.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitpath  # noqa: E402


def main(argv):
    game_dir = kitpath.ROOT
    if "--prerebased" in argv:
        argv = [a for a in argv if a != "--prerebased"]
        kit, _ = kitpath.take_kit(list(argv))
        subprocess.run([sys.executable, "-I", str(kitpath.ROOT / "tools/prerebase.py"), "--kit", str(kit)],
                       check=True)
        game_dir = kitpath.ROOT / "build/prerebased"
    return kitpath.run_kit_tool("build.py", argv, game_dir=game_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
