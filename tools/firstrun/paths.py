"""Where the first run's pieces are, in a checkout and in the app.

The first run runs as `python -I tools/firstrun/firstrun.py`, so nothing but
the standard library, capstone and pefile is importable unless this module
puts the repository's own tools on sys.path: tools/ (kitpath), tools/boot
(seed_user), tools/inputs (the exporter). The app keeps the same relative
layout under its resources (docs/reference/firstrun.md §6).
"""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                     # the repository (or the app's copy of it)
for sub in ("tools", "tools/boot", "tools/inputs", "tools/firstrun"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

HOME_ENV = "EE_HOME"


def default_home():
    """The player's data folder: also the kit's default profile directory
    (runtime/layout.cpp os_user_data_dir("EmpireEarth"))."""
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library/Application Support/EmpireEarth"


def kit_dir(explicit=None):
    """The recomp-kit checkout: --kit, $RECOMP_KIT_DIR, else <root>/kit."""
    kit = explicit or os.environ.get("RECOMP_KIT_DIR")
    kit = Path(kit).expanduser().resolve() if kit else ROOT / "kit"
    return kit


class Home:
    """The layout under the player's data folder (firstrun.md §4)."""

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        self.game_root = self.root / "Game"            # the published import
        self.game = self.game_root / "Empire Earth"    # the guest's C:\Empire Earth
        self.import_record = self.game_root / "import.json"
        self.game_partial = self.root / "Game.partial"  # an import in progress (resumable)
        self.game_old = self.root / "Game.old"          # only between the two renames of a publish
        self.build = self.root / "Build"               # the bundle cache
        self.current = self.build / "current.json"
        self.log = self.build / "firstrun.log"
        self.lock = self.root / ".firstrun.lock"
        self.registry = self.root / "registry.json"    # the kit's registry store
        self.game_path = self.root / "game-path.txt"   # the kit host's saved game path

    def work(self, key):
        return self.build / ("work-" + key)

    def bundles(self, key):
        return self.build / key
