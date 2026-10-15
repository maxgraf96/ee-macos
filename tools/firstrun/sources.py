"""Where a player's game files can come from (docs/reference/firstrun.md §1).

    installer  GOG's offline installer: setup_empire_earth_gold_*.exe, with its
               *-1.bin parts beside it (extracted with innoextract, inno.py)
    gold       the installed "Empire Earth Gold" folder (Empire Earth/ inside,
               goggame-1207658777.* beside it)
    game       just its "Empire Earth" folder (copied from a Windows PC, a USB
               stick, a network share)
    wrapper    a Wine wrapper or bottle: a Wineskin/Sikarugir/Kegworks/Porting
               Kit .app, a CrossOver or Whisky bottle, a Wine prefix; the game
               is searched for under drive_c

Everything here only reads. A wrapper the player still uses is never changed.
"""
import os
from pathlib import Path

import paths  # noqa: F401
import gogdb

EXE = "empire earth.exe"
AOC_EXE = "ee-aoc.exe"
# Inside a wrapper .app, where the Wine prefix's drive_c lives.
WRAPPER_DRIVE_C = ("Contents/SharedSupport/prefix/drive_c", "Contents/drive_c",
                   "Contents/Resources/drive_c", "Contents/SharedSupport/drive_c", "drive_c")
# Folders under drive_c never worth walking into.
SKIP_DIRS = {"windows", "programdata", "$recycle.bin", "system volume information", "frameworks"}
MAX_DEPTH = 5


class Source:
    def __init__(self, kind, path, game_dir=None, gold_dir=None, wine_prefix=None, installer=None):
        self.kind = kind
        self.path = Path(path)
        self.game_dir = Path(game_dir) if game_dir else None
        self.gold_dir = Path(gold_dir) if gold_dir else None
        self.wine_prefix = Path(wine_prefix) if wine_prefix else None
        self.installer = [Path(p) for p in installer or []]
        self.notes = []

    def as_dict(self):
        return {"kind": self.kind, "path": str(self.path),
                "game_dir": str(self.game_dir) if self.game_dir else None,
                "gold_dir": str(self.gold_dir) if self.gold_dir else None,
                "wine_prefix": str(self.wine_prefix) if self.wine_prefix else None,
                "installer": [str(p) for p in self.installer], "notes": self.notes}

    def __repr__(self):
        return "Source(%s, %s)" % (self.kind, self.game_dir or self.installer)


def _child(folder, lower_name):
    """The entry of folder named lower_name, case-insensitively, or None."""
    try:
        for entry in os.scandir(folder):
            if entry.name.lower() == lower_name:
                return Path(entry.path)
    except OSError:
        pass
    return None


def is_installer(path):
    """A Windows executable that is not the game itself: GOG's installer is an
    Inno Setup loader (inno.py decides whether it can read it)."""
    path = Path(path)
    if path.suffix.lower() != ".exe" or path.name.lower() in (EXE, AOC_EXE) or not path.is_file():
        return False
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def installer_parts(exe):
    """The installer and its numbered .bin parts (name-1.bin, name-2.bin, ...)."""
    exe = Path(exe)
    parts = [exe]
    n = 1
    while True:
        part = _child(exe.parent, ("%s-%d.bin" % (exe.stem, n)).lower())
        if part is None:
            break
        parts.append(part)
        n += 1
    return parts


def _gold_of(game_dir):
    """The GOG install folder around an "Empire Earth" folder, if it is one."""
    parent = game_dir.parent
    for name in (gogdb.SCRIPT, gogdb.HASHDB, gogdb.GOGLOG, gogdb.INFO):
        if _child(parent, name.lower()) is not None:
            return parent
    return None


def _prefix_of(path):
    """The Wine prefix (the folder holding system.reg) above path, if any."""
    for p in [Path(path)] + list(Path(path).parents):
        if p.name.lower() == "drive_c":
            prefix = p.parent
            return prefix if (prefix / "system.reg").is_file() else prefix
    return None


def _game_source(game_dir, kind=None, origin=None):
    gold = _gold_of(game_dir)
    prefix = _prefix_of(game_dir)
    if kind is None:
        kind = "wrapper" if prefix is not None else ("gold" if gold is not None else "game")
    src = Source(kind, origin or game_dir, game_dir=game_dir, gold_dir=gold, wine_prefix=prefix)
    if gold is not None and _child(gold, "empire earth - the art of conquest") is not None:
        src.notes.append("The Art of Conquest is installed here too; only the base game is imported.")
    return src


def find_games(root, depth=MAX_DEPTH):
    """Folders under root holding Empire Earth.exe, breadth first, bounded."""
    found, frontier = [], [Path(root)]
    for _ in range(depth + 1):
        nxt = []
        for folder in frontier:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            if any(e.name.lower() == EXE and e.is_file() for e in entries):
                found.append(folder)
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False) and e.name.lower() not in SKIP_DIRS \
                            and not e.name.startswith("."):
                        nxt.append(Path(e.path))
                except OSError:
                    continue
        frontier = nxt
        if not frontier:
            break
    return found


def find_aoc(root, depth=2):
    """Folders under root holding only The Art of Conquest's executable."""
    out, frontier = [], [Path(root)]
    for _ in range(depth + 1):
        nxt = []
        for folder in frontier:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            names = {e.name.lower() for e in entries}
            if AOC_EXE in names and EXE not in names:
                out.append(folder)
            nxt += [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]
        frontier = nxt
    return out


def locate(path):
    """Every source at path (a file or a folder), best first. [] when there is
    no Empire Earth there; the caller explains why (explain())."""
    path = Path(path).expanduser()
    if path.is_file():
        if path.suffix.lower() == ".bin":
            # a part: the installer is the .exe of the same stem without "-N"
            stem = path.stem.rsplit("-", 1)[0]
            exe = _child(path.parent, (stem + ".exe").lower())
            path = exe if exe is not None else path
        if path.name.lower() == EXE:
            return [_game_source(path.parent)]
        if is_installer(path):
            return [Source("installer", path, installer=installer_parts(path))]
        return []
    if not path.is_dir():
        return []
    # a wrapper .app or a bottle: the game is under its drive_c
    for rel in WRAPPER_DRIVE_C:
        drive_c = path / rel
        if drive_c.is_dir():
            return [_game_source(g, "wrapper", origin=path) for g in find_games(drive_c)]
    if _child(path, EXE) is not None:
        return [_game_source(path)]
    games = find_games(path, depth=3)
    if games:
        return [_game_source(g) for g in games]
    # a folder holding the installer (Downloads)
    installers = sorted(p for p in path.iterdir()
                        if p.name.lower().startswith("setup_empire_earth") and is_installer(p))
    return [Source("installer", p, installer=installer_parts(p)) for p in installers]


def explain(path):
    """Why locate(path) found nothing: (code, message)."""
    path = Path(path).expanduser()
    if not path.exists():
        return "not-found", "%s does not exist." % path
    if path.is_dir():
        aoc = find_aoc(path)
        if aoc:
            return ("aoc-only", "This is The Art of Conquest (EE-AOC.exe), the expansion. Choose the "
                    "\"Empire Earth\" folder next to it, or the \"Empire Earth Gold\" folder that holds both.")
    if path.is_file():
        return ("not-found", "%s is neither Empire Earth.exe nor GOG's installer "
                "(setup_empire_earth_gold_*.exe)." % path.name)
    return ("not-found", "No Empire Earth game files in %s. Choose GOG's installer "
            "(setup_empire_earth_gold_*.exe), the \"Empire Earth Gold\" folder, or the Wine app "
            "you play it with." % path)


def default_locations(home=None):
    """Where a Mac player's copy usually is: Wine wrappers and bottles, Heroic,
    Downloads and Desktop (installers). Only cheap checks."""
    home = Path(home) if home else Path.home()
    roots = []
    for apps in (Path("/Applications"), home / "Applications"):
        try:
            for app in sorted(apps.glob("*.app")):
                if any((app / rel).is_dir() for rel in WRAPPER_DRIVE_C[:4]):
                    roots.append(app)
        except OSError:
            continue
    for bottles in (home / "Library/Application Support/CrossOver/Bottles",
                    home / "Library/Containers/com.isaacmarovitz.Whisky/Bottles",
                    home / "Library/Application Support/com.isaacmarovitz.Whisky/Bottles"):
        if bottles.is_dir():
            roots += sorted(p for p in bottles.iterdir() if (p / "drive_c").is_dir())
    for p in (home / ".wine", home / "Games/Heroic"):
        if p.is_dir():
            roots.append(p)
    for p in (home / "Downloads", home / "Desktop"):
        if p.is_dir():
            roots += sorted(q for q in p.iterdir()
                            if q.name.lower().startswith("setup_empire_earth") and is_installer(q))
    return roots
