#!/usr/bin/env python3
"""Build and launch Empire Earth, windowed: build/EmpireEarth.app.

    .venv/bin/python tools/run.py [--kit DIR] [--regenerate] [--user DIR] [--no-open] [--jobs N]

What it does, each step only when needed:

1. Checks the translation inputs exist (analysis/kit/, from `tools/setup.py
   --install "/path/to/Empire Earth"`, once).
2. Seeds the player's registry in the user directory if it has none
   (tools/boot/seed_user.py: legion's options, the CD key from the player's own
   GOG install script). Default user directory: ~/Library/Application
   Support/EmpireEarth, where the game's saves and logs go too.
3. Builds the app (`tools/build.py --target app`), translating first when
   build/recomp/gen is missing or --regenerate is given (about 5 minutes the
   first time).
4. Opens it. Click in the window to capture the mouse; Escape skips the intro
   movies; hold Escape to release the mouse; Command-Q quits. No audio yet.

docs/reference/boot.md has the details, and the headless ee_boot.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/boot"))
import kitpath  # noqa: E402
import seed_user  # noqa: E402

APP = ROOT / "build/EmpireEarth.app"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit", default=None, help="a kit checkout other than kit/")
    parser.add_argument("--regenerate", action="store_true", help="translate again before building")
    parser.add_argument("--user", type=Path, default=seed_user.default_user_dir(),
                        help="the player's data directory (default %(default)s)")
    parser.add_argument("--no-open", action="store_true", help="build, do not launch")
    parser.add_argument("--jobs", default=None)
    args = parser.parse_args(argv)

    if not (ROOT / "analysis/kit/exe/funcdb.json").is_file():
        sys.exit("No translation inputs in analysis/kit/: run once\n"
                 "  .venv/bin/python tools/setup.py --install \"/path/to/Empire Earth\"\n"
                 "(docs/reference/build.md §2)")
    if not (args.user / "registry.json").is_file():
        print("Seeding the registry in %s" % args.user)
        seed_user.main(["--user", str(args.user)])

    command = [sys.executable, str(ROOT / "tools/build.py"), "--target", "app"]
    command += ["--kit", args.kit] if args.kit else []
    command += ["--jobs", args.jobs] if args.jobs else []
    if args.regenerate or not (ROOT / "build/recomp/gen").is_dir():
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
