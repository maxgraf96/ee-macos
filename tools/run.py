#!/usr/bin/env python3
"""Build and launch Empire Earth, windowed: build/EmpireEarth.app.

    .venv/bin/python tools/run.py [--kit DIR] [--regenerate] [--user DIR] [--no-open] [--jobs N]

What it does, each step only when needed:

1. Writes the translation inputs of all nine modules (analysis/kit/,
   tools/inputs/export.py) when they are missing or older than the function
   DB or the exporter. That needs the game linked once with
   `tools/setup.py --install "/path/to/Empire Earth"`; no Ghidra.
2. Seeds the player's registry in the user directory if it has none
   (tools/boot/seed_user.py: legion's options, the CD key from the player's own
   GOG install script). Default user directory: ~/Library/Application
   Support/EmpireEarth, where the game's saves and logs go too.
3. Builds the app (`tools/build.py --target app`), translating first when a
   module's generated code is missing or older than its inputs or the kit's
   translator, or --regenerate is given (about 5 minutes).
4. Opens it. Click in the window to capture the mouse; Escape skips the intro
   movies; hold Escape to release the mouse; Command-Q quits. No audio yet.

docs/reference/boot.md has the details, and the headless ee_boot.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/boot"))
import kitpath  # noqa: E402
import seed_user  # noqa: E402

APP = ROOT / "build/EmpireEarth.app"
GEN = ROOT / "build/recomp/gen"


def game():
    return tomllib.loads((ROOT / "game.toml").read_text())


def modules():
    """(module file name, its funcdb.json, a file the translator always writes)
    per module in game.toml."""
    cfg = game()
    yield cfg["game"]["executable"], ROOT / cfg["translate"]["function_db"], GEN / "table.c"
    for key, module in sorted(cfg["modules"]["aux"].items()):
        yield module["name"], ROOT / module["function_db"], GEN / ("aux-" + key) / "table.c"


def stale_inputs():
    """Why analysis/kit must be written again (tools/inputs/export.py), or None."""
    exporter = max(p.stat().st_mtime for p in (ROOT / "tools/inputs").glob("*.py"))
    for name, funcdb, _ in modules():
        if not funcdb.is_file():
            return "%s is missing" % funcdb.relative_to(ROOT)
        made = funcdb.stat().st_mtime
        db = ROOT / "db/functions" / (name.lower().replace(" ", "-") + ".json")
        for source in (db, ROOT / "db/inputs.json"):
            if source.stat().st_mtime > made:
                return "%s is newer than %s" % (source.relative_to(ROOT), funcdb.relative_to(ROOT))
        if exporter > made:
            return "tools/inputs/ is newer than %s" % funcdb.relative_to(ROOT)
    return None


def stale_translation(kit):
    """Why build/recomp/gen must be translated again, or None."""
    translator = max(p.stat().st_mtime for p in (kit / "tools/recomp").glob("*.py"))
    for _, db, generated in modules():
        if not generated.is_file():
            return "%s is missing" % generated.relative_to(ROOT)
        made = generated.stat().st_mtime
        if db.stat().st_mtime > made:
            return "%s is newer than its translation" % db.relative_to(ROOT)
        if translator > made:
            return "the kit's translator is newer than %s" % generated.relative_to(ROOT)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit", default=None, help="a kit checkout other than kit/")
    parser.add_argument("--regenerate", action="store_true", help="translate again before building")
    parser.add_argument("--user", type=Path, default=seed_user.default_user_dir(),
                        help="the player's data directory (default %(default)s)")
    parser.add_argument("--no-open", action="store_true", help="build, do not launch")
    parser.add_argument("--jobs", default=None)
    args = parser.parse_args(argv)

    kit_args = ["--kit", args.kit] if args.kit else []
    why = stale_inputs()
    if why:
        if not (ROOT / game()["game"]["developer_exe"]).is_file():
            sys.exit("The game is not linked: run once\n"
                     "  .venv/bin/python tools/setup.py --install \"/path/to/Empire Earth\"\n"
                     "(docs/reference/build.md §2)")
        print("Writing the translation inputs (%s)" % why, flush=True)
        subprocess.run([sys.executable, str(ROOT / "tools/inputs/export.py")] + kit_args, check=True)
    if not (args.user / "registry.json").is_file():
        print("Seeding the registry in %s" % args.user, flush=True)
        seed_user.main(["--user", str(args.user)])

    command = [sys.executable, str(ROOT / "tools/build.py"), "--target", "app"]
    command += kit_args
    command += ["--jobs", args.jobs] if args.jobs else []
    why = "--regenerate" if args.regenerate else stale_translation(kitpath.take_kit(list(kit_args))[0])
    if why:
        print("Translating (%s)" % why, flush=True)
        command.append("--regenerate")
    subprocess.run(command, check=True)
    if not APP.is_dir():
        sys.exit("the build did not produce %s" % APP)
    if args.no_open:
        print("Built %s" % APP)
        return 0
    # An app started by `open` gets launchd's environment, not this shell's:
    # the user directory goes along explicitly.
    subprocess.run(["open", "-n", "--env", "RECOMP_PROFILE_DIR=%s" % args.user, str(APP)], check=True)
    print("Opened %s (user data: %s)" % (APP, args.user))
    return 0


if __name__ == "__main__":
    sys.exit(main())
