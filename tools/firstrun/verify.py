"""Is this the GOG Empire Earth Gold base game, and is it whole?
(docs/reference/firstrun.md §2)

Three levels, cheapest first:
1. The nine modules game.toml pins (SHA-256): the translation is made for
   exactly these bytes, so any other version stops the import.
2. Every file of db/gog-files.json present at its size (a stat each): a
   partial copy is caught before anything is copied.
3. Every file's MD5 against the manifest, computed while the copy is made
   (imported.py), so the copy that is kept is the copy that was checked.

Files the native build never opens (GOG's ddraw.dll wrapper, the Bink and
Miles DLLs the kit replaces, the DirectX redistributable, read-mes, tools)
are "optional": missing or different, they are only a warning. Everything
else - the modules, Data/ and the configuration files - must match, or the
game would differ from every other player's (a LAN game would go out of
sync).
"""
import fnmatch
import hashlib
import json
from pathlib import Path

import paths  # noqa: F401  (sys.path)
from progress import Failure

MANIFEST = paths.ROOT / "db/gog-files.json"
# Relative paths (lower case) the native build never reads.
OPTIONAL = ("binkw32.dll", "ddraw.dll", "mss32.dll", "dxcfg.exe", "dxcfg.ini", "rmvparse.exe",
            "readme*.txt", "eula*.txt", "sierra.inf", "redist/*")
# Files a player made, worth carrying over into the profile (never overwriting).
PLAYER_FILES = ("data/saved games/*", "data/scenarios/*", "users/*")


def load_manifest(path=MANIFEST):
    doc = json.loads(Path(path).read_text())
    doc["files"] = {p: (size, md5) for p, size, md5 in doc["files"]}
    return doc


def load_modules(root=None):
    """[(file name, sha256)] for the executable and the eight DLLs, from game.toml."""
    import tomllib
    cfg = tomllib.loads(((root or paths.ROOT) / "game.toml").read_text())
    mods = [(cfg["game"]["executable"], cfg["game"]["sha256"])]
    for _, m in sorted(cfg["modules"]["aux"].items()):
        mods.append((m["name"], m["sha256"]))
    return mods


def is_optional(rel):
    low = rel.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in OPTIONAL)


def is_player_file(rel):
    low = rel.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in PLAYER_FILES)


def index(folder):
    """{lower-case relative posix path: actual Path} for every file under folder:
    the copy may come from a case-insensitive file system or a Windows PC."""
    folder = Path(folder)
    out = {}
    for p in folder.rglob("*"):
        try:
            if p.is_file():
                out[p.relative_to(folder).as_posix().lower()] = p
        except OSError:
            continue
    return out


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def classify_folder(folder):
    """What kind of folder this is when it is not the base game: "aoc" (The Art
    of Conquest's), "gold" (the GOG install folder holding both), or None."""
    folder = Path(folder)
    names = {p.name.lower() for p in folder.iterdir()} if folder.is_dir() else set()
    if "empire earth.exe" in names:
        return "game"
    if "ee-aoc.exe" in names:
        return "aoc"
    if "empire earth" in names and (folder / "Empire Earth").is_dir():
        return "gold"
    return None


def check_modules(files, modules, manifest):
    """Level 1. files: the folder's index. Raises Failure("wrong-version")."""
    exe_name = modules[0][0]
    exe = files.get(exe_name.lower())
    if exe is None:
        raise Failure("not-found", "There is no %s in this folder." % exe_name)
    report = []
    for name, want in modules:
        p = files.get(name.lower())
        got = sha256_file(p) if p is not None else None
        report.append({"name": name, "sha256": got, "expected": want, "ok": got == want})
    bad = [r for r in report if not r["ok"]]
    if not bad:
        return report
    exe_bad = not report[0]["ok"]
    gog_size = manifest["files"].get(exe_name, (None,))[0]
    names = ", ".join(r["name"] for r in bad)
    if any(n in files for n in ("neoee.dll", "neoee.cfg")):
        why = ("This is NeoEE, the community edition of Empire Earth, which replaces the game's "
               "own files. This app needs GOG's original Empire Earth Gold: import a GOG "
               "installation or the GOG installer.")
    elif any(n.endswith(".icd") for n in files):
        why = ("This looks like an installation from the original CD (it has SafeDisc's .icd "
               "file). Only GOG's Empire Earth Gold is supported: install it from GOG and "
               "import that instead.")
    elif exe_bad and gog_size is not None and exe.stat().st_size != gog_size:
        why = ("%s is a different version from GOG's Empire Earth Gold (the original CD "
               "release, a Sierra patch, or a mod that replaces the executable). Only GOG's "
               "version is supported: import a fresh GOG installation or the GOG installer."
               % exe_name)
    elif any(r["sha256"] is None for r in bad):
        missing = ", ".join(r["name"] for r in bad if r["sha256"] is None)
        raise Failure("partial-copy", "Game files are missing: %s. The copy is incomplete; "
                      "copy the whole \"Empire Earth\" folder again." % missing,
                      {"modules": report})
    else:
        why = ("%s differ%s from GOG's Empire Earth Gold: patched, modded or damaged. Import a "
               "fresh GOG installation or the GOG installer." % (names, "s" if len(bad) == 1 else ""))
    raise Failure("wrong-version", why, {"modules": report})


def check_presence(files, manifest):
    """Level 2: (missing required, short required, missing optional), by stat only."""
    missing, short, optional = [], [], []
    for rel, (size, _) in manifest["files"].items():
        p = files.get(rel.lower())
        if p is None:
            (optional if is_optional(rel) else missing).append(rel)
            continue
        try:
            # shorter: an incomplete copy; longer: modified, which the MD5 check reports
            if p.stat().st_size < size and not is_optional(rel):
                short.append(rel)
        except OSError:
            missing.append(rel)
    return missing, short, optional


def player_files(files, manifest):
    """Relative paths of the player's own files (saves, scenarios, profiles)."""
    known = {rel.lower() for rel in manifest["files"]}
    return sorted(rel for rel in files if rel not in known and is_player_file(rel))


def quick(folder, manifest=None, modules=None):
    """Levels 1 and 2 for probe: a dict, never raises for a game folder."""
    manifest = manifest or load_manifest()
    modules = modules or load_modules()
    files = index(folder)
    out = {"folder": str(folder), "ok": False}
    try:
        out["modules"] = check_modules(files, modules, manifest)
    except Failure as f:
        out.update(code=f.code, message=f.message, modules=f.data.get("modules"))
        return out
    missing, short, optional = check_presence(files, manifest)
    out.update(missing=missing, short=short, missing_optional=optional,
               player_files=len(player_files(files, manifest)))
    if missing or short:
        out.update(code="partial-copy", message=partial_message(missing, short))
        return out
    out.update(ok=True, message="GOG Empire Earth Gold, all %d files present" % len(manifest["files"]))
    return out


def partial_message(missing, short):
    parts = []
    if missing:
        parts.append("%d file%s missing (%s)" % (len(missing), "" if len(missing) == 1 else "s",
                                                  ", ".join(missing[:3]) + (", ..." if len(missing) > 3 else "")))
    if short:
        parts.append("%d file%s too short (%s)" % (len(short), "" if len(short) == 1 else "s",
                                                         ", ".join(short[:3]) + (", ..." if len(short) > 3 else "")))
    return ("The game folder is incomplete: %s. Copy the whole \"Empire Earth\" folder again, "
            "or import the GOG installer." % "; ".join(parts))


def compare_hashdb(hashdb, manifest):
    """The player's own goggame-*.hashdb against the manifest: the base game's
    entries that differ (another GOG build), or None without a hashdb."""
    if hashdb is None:
        return None
    base = manifest.get("base", "Empire Earth") + "\\"
    theirs = {k[len(base):].replace("\\", "/"): v for k, v in hashdb.items() if k.startswith(base)}
    ours = {rel: md5 for rel, (_, md5) in manifest["files"].items()}
    return sorted(rel for rel in set(theirs) | set(ours) if theirs.get(rel) != ours.get(rel))
