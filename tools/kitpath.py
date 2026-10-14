"""Which recomp-kit checkout the wrappers drive.

The game repository pins the kit as the `kit/` submodule. During kit work the
same commands can run against another checkout, a worktree of the fork, by
naming it with --kit DIR or RECOMP_KIT_DIR; nothing else changes (outputs
still go to this repository's ignored build/).
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def take_kit(argv):
    """Remove `--kit DIR` / `--kit=DIR` from argv and return (kit, rest)."""
    kit, rest, i = os.environ.get("RECOMP_KIT_DIR"), [], 0
    while i < len(argv):
        if argv[i] == "--kit" and i + 1 < len(argv):
            kit, i = argv[i + 1], i + 2
            continue
        if argv[i].startswith("--kit="):
            kit, i = argv[i].split("=", 1)[1], i + 1
            continue
        rest.append(argv[i])
        i += 1
    kit = Path(kit).expanduser().resolve() if kit else ROOT / "kit"
    if not (kit / "tools/build.py").is_file():
        sys.exit("No recomp-kit at %s: run `git submodule update --init kit`, or pass --kit DIR "
                 "(or RECOMP_KIT_DIR) naming a kit checkout" % kit)
    return kit, rest


def run_kit_tool(name, argv, game_dir=ROOT):
    """Run kit/tools/<name> for this game; every other option is the kit's."""
    import subprocess
    kit, rest = take_kit(argv)
    command = [sys.executable, str(kit / "tools" / name), "--game-dir", str(game_dir)] + rest
    return subprocess.call(command, cwd=ROOT)
