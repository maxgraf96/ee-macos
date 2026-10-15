#!/usr/bin/env python3
"""The first run: import the player's GOG Empire Earth, then translate and
compile it into the bundles the app loads (docs/reference/firstrun.md).

    python -I tools/firstrun/firstrun.py [--home DIR] probe [PATH ...]
    python -I tools/firstrun/firstrun.py [--home DIR] import PATH [--allow-modified]
                                         [--width W --height H] [--player-name NAME]
                                         [--innoextract BIN]
    python -I tools/firstrun/firstrun.py [--home DIR] build [--jobs N] [--cc CLANG] [--lld LLD]
                                         [--python PY] [--kit DIR] [--keep-work]
    python -I tools/firstrun/firstrun.py [--home DIR] status

PATH is GOG's offline installer (setup_empire_earth_gold_*.exe, with its .bin
parts beside it), the installed "Empire Earth Gold" folder or its "Empire
Earth" folder, or a Wine wrapper or bottle that has the game in its drive_c.
probe with no PATH looks in the usual places.

--home is the player's data folder, default $EE_HOME or ~/Library/Application
Support/EmpireEarth. stdout carries JSON lines (one object per line:
phase, done, total, message, event; the last line is the result); a human log
goes to <home>/Build/firstrun.log, and to stderr unless --quiet. Exit status:
0 done, 1 failed (the error line says why, with a stable code), 2 bad usage,
130 stopped (run the same command again to resume). --nice N lowers the
priority of the whole run.

Needs only the standard library, capstone and pefile, and runs with -I.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from progress import Failure, Reporter  # noqa: E402


class Stop(BaseException):
    """SIGINT or SIGTERM: stop cleanly, leaving resumable state."""


def _on_signal(signum, frame):
    raise Stop()


def lock(home):
    home.root.mkdir(parents=True, exist_ok=True)
    fh = open(home.lock, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise Failure("busy", "Another first run is working in %s." % home.root)
    return fh


def cmd_probe(args, home, r):
    import sources
    import verify
    manifest, modules = verify.load_manifest(), verify.load_modules()
    roots = [Path(p) for p in args.paths] or sources.default_locations()
    found = []
    total = len(roots)
    for i, root in enumerate(roots):
        r.progress("probe", i, total, "Looking in %s" % root, unit="steps")
        cands = sources.locate(root)
        if not cands and args.paths:
            code, message = sources.explain(root)
            found.append({"path": str(root), "ok": False, "code": code, "message": message})
        for c in cands:
            item = c.as_dict()
            if c.kind == "installer":
                item.update(ok=None, message="GOG installer: checked when imported")
            else:
                q = verify.quick(c.game_dir, manifest, modules)
                item.update({k: q.get(k) for k in ("ok", "code", "message", "missing", "short",
                                                   "missing_optional", "player_files")})
            found.append(item)
    ok = [f for f in found if f.get("ok") is not False]
    r.result("probe", bool(ok), "%d candidate%s, %d usable" % (len(found), "" if len(found) == 1 else "s",
                                                              len(ok)), {"candidates": found})
    return 0 if ok else 1


def cmd_import(args, home, r):
    import importer
    rec = importer.run_import(home, args.path, r, allow_modified=args.allow_modified,
                              innoextract=args.innoextract, width=args.width, height=args.height,
                              player_name=args.player_name)
    r.result("import", True, "Imported %d files from %s" % (rec["files"], rec["source"]["kind"]),
             {"game": str(home.game), "record": rec})
    return 0


def toolchain(args):
    import pipeline
    return pipeline.Toolchain(python=args.python, cc=args.cc, lld=args.lld, kit=args.kit)


def cmd_build(args, home, r):
    import pipeline
    b = pipeline.Build(home, r, toolchain(args), jobs=args.jobs, keep_work=args.keep_work)
    rec, cached = b.run()
    d = home.bundles(rec["key"])
    r.result("build", True, "Bundles %s in %s" % ("already built" if cached else "built", d),
             {"key": rec["key"], "dir": str(d), "load_order": [str(d / f) for f in rec["load_order"]],
              "symbols": str(d / rec["symbols"]), "cached": cached, "seconds": rec.get("seconds")})
    return 0


def cmd_status(args, home, r):
    import importer
    import pipeline
    imp = importer.status(home)
    st = pipeline.status(home, toolchain(args))
    if not imp["imported"]:
        state = "needs-import"
    elif st["ready"]:
        state = "ready"
    elif st.get("resumable"):
        state = "build-resumable"
    else:
        state = "needs-build"
    r.result("status", True, state, {"state": state, "home": str(home.root), "import": imp, "build": st,
                                     "game_path": str(home.game / "Empire Earth.exe"),
                                     "registry": str(home.registry)})
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, default=None, help="the player's data folder")
    ap.add_argument("--quiet", action="store_true", help="no human-readable lines on stderr")
    ap.add_argument("--nice", type=int, default=0, help="lower the priority by N")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="find the game; check it quickly")
    p.add_argument("paths", nargs="*")
    p = sub.add_parser("import", help="verify and copy the game into <home>/Game")
    p.add_argument("path")
    p.add_argument("--allow-modified", action="store_true",
                   help="keep game files that differ from GOG's (a modded game; LAN games go out of sync)")
    p.add_argument("--innoextract", default=None, help="the innoextract binary (default: the app's)")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--player-name", default=None)
    for name in ("build", "status"):
        p = sub.add_parser(name, help={"build": "translate and compile into bundles",
                                       "status": "what is imported and built"}[name])
        p.add_argument("--python", default=None, help="the Python that runs the translator (default: this one)")
        p.add_argument("--cc", default=None, help="clang (default $RECOMP_FREESTANDING_CC, Homebrew's, PATH)")
        p.add_argument("--lld", default=None, help="ld64.lld (default $RECOMP_LD64_LLD, Homebrew's, PATH)")
        p.add_argument("--kit", default=None, help="the recomp-kit (default $RECOMP_KIT_DIR, else kit/)")
        if name == "build":
            p.add_argument("--jobs", type=int, default=None, help="compile jobs (default: by cores and memory)")
            p.add_argument("--keep-work", action="store_true", help="keep the generated C and objects")
    return ap


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    home = paths.Home(args.home or paths.default_home())
    r = Reporter(human=None if args.quiet else sys.stderr)
    if args.nice:
        os.nice(args.nice)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    phase = args.command
    held = None
    try:
        if args.command in ("import", "build"):
            held = lock(home)
            r.open_log(home.log)
            r.log("firstrun.py %s (home %s)" % (" ".join(argv if argv is not None else sys.argv[1:]),
                                                home.root))
        return {"probe": cmd_probe, "import": cmd_import, "build": cmd_build,
                "status": cmd_status}[args.command](args, home, r)
    except Failure as f:
        r.error(phase, f.code, f.message, f.data)
        r.result(phase, False, f.message, {"code": f.code})
        return 1
    except Stop:
        r.error(phase, "interrupted", "Stopped. Run the same command again to continue where it stopped.")
        r.result(phase, False, "stopped", {"code": "interrupted"})
        return 130
    except Exception as e:  # noqa: BLE001 - the app needs a last line whatever happens
        r.log(traceback.format_exc())
        r.error(phase, "internal", "%s: %s" % (type(e).__name__, e))
        r.result(phase, False, "internal error", {"code": "internal"})
        return 1
    finally:
        r.close()
        if held is not None:
            held.close()


if __name__ == "__main__":
    sys.exit(main())
