#!/usr/bin/env python3
"""The boot test: build ee_boot (the executable and all eight DLLs, translated)
and boot it headless, in strict mode, with a fresh user directory, and require

  - every boot milestone through the main menu's message loop
    (docs/reference/boot.md §2),
  - the threads the game starts, with the x87 control word legion measured:
    0x027f everywhere but the render thread (EXE+0x10fe16), which sets 0x007f
    itself,
  - frames presented through the kit's DirectDraw 7 (software renderer); the
    last one is kept as build/boot/main-menu.ppm, the main menu at 1024x768,
  - a clean exit on WM_CLOSE (exit code 0),
  - the engine's assertion (ESVerify) hooked, so a failure names itself, and
    none failing (app/ee_assertions.cpp),
  - no strict-mode stop, and no import called without an implementation except
    the ones boot.md §5 lists as known (sockets, Phase 5),
  - the game folder untouched: everything the run writes is in the user
    directory.

    .venv/bin/python tests/boot/run_boot.py [--kit DIR] [--regenerate] [--no-build]
                                             [--seconds S] [--keep-user]

Run through tools/test.py --boot.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/boot"))
import seed_user  # noqa: E402

GAME = ROOT / "original/gog/Empire Earth"
MILESTONES = 11
# Called with no implementation behind them, and known: Phase 5's sockets.
KNOWN_UNIMPLEMENTED = {"WS2_32.dll!socket", "WS2_32.dll!closesocket"}
# EXE thread bodies (EXE+offset) whose control word legion measured
# (legion.md §5.4): every one 0x027f but the render thread's.
RENDER_BODY = "EXE+10fe16"


def game_snapshot():
    """(path, mtime, size) of every file in the game folder."""
    out = []
    for dirpath, _, files in os.walk(GAME):
        for f in files:
            p = Path(dirpath) / f
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
    return sorted(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit", default=None)
    parser.add_argument("--regenerate", action="store_true", help="translate before building")
    parser.add_argument("--no-build", action="store_true", help="run what is already built")
    parser.add_argument("--jobs", default=None)
    parser.add_argument("--seconds", type=float, default=25.0, help="post WM_CLOSE after S seconds")
    parser.add_argument("--keep-user", action="store_true", help="keep the temporary user directory")
    args = parser.parse_args()
    if not args.no_build:
        command = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_boot"]
        command += ["--kit", args.kit] if args.kit else []
        command += ["--regenerate"] if args.regenerate else []
        command += ["--jobs", args.jobs] if args.jobs else []
        subprocess.run(command, check=True)

    user = Path(tempfile.mkdtemp(prefix="ee-boot-"))
    try:
        seed_user.main(["--user", str(user)])
        before = game_snapshot()
        env = dict(os.environ, RECOMP_STRICT="1")
        frames = user / "frames"
        result = subprocess.run([str(ROOT / "build/recomp/ee_boot"), "--user", str(user),
                                 "--seconds", str(args.seconds), "--frames", str(frames),
                                 "--frame-every", "60"],
                                cwd=ROOT, capture_output=True, text=True, timeout=args.seconds + 120,
                                env=env)
        out = result.stdout + result.stderr
        problems = []
        if result.returncode != 0:
            problems.append("exit code %d (%s)" % (result.returncode,
                            (re.search(r"stop: (.*)", out) or [None, "?"])[1]))
        reached = {int(m.group(1)) for m in re.finditer(r"^\[ee\] milestone (\d+) ", out, re.M)}
        missing = sorted(set(range(MILESTONES)) - reached)
        if missing:
            problems.append("milestones not reached: %s" % missing)
        # The engine's assertion is reported by name (app/ee_assertions.cpp), in
        # every host: the hook must be in place before the entry point.
        if not re.search(r"^\[ee\] assertions: a failed ESVerify \(Low-Level Engine\.dll\+1c43e\) "
                         r"is reported$", out, re.M):
            problems.append("ESVerify is not hooked (no \"[ee] assertions:\" line)")
        if "[ee] ESVerify failed:" in out:
            problems.append(re.search(r"\[ee\] ESVerify failed:.*", out).group(0))
        if "[recomp] strict:" in out:
            problems.append("a strict-mode stop: %s" % re.search(r"\[recomp\] strict:.*", out).group(0))
        called = set(re.findall(r"^\s+(\S+!\S+)\s+CALLED$", out, re.M))
        unknown = sorted(called - KNOWN_UNIMPLEMENTED)
        if unknown:
            problems.append("imports called with no implementation: %s" % ", ".join(unknown))
        threads = re.findall(r"^\s+thread (\d+)\s+(.+?)\s+cw ([0-9a-f]{4}) -> ([0-9a-f]{4})", out, re.M)
        render = [last for _, body, _, last in threads if body == RENDER_BODY]
        if render != ["007f"]:
            problems.append("the render thread %s ended at CW %s, not 007f" % (RENDER_BODY, render))
        others = ["%s %s" % (body, last) for _, body, first, last in threads
                  if body != RENDER_BODY and (first != "027f" or last != "027f")]
        starts = [first for _, _, first, _ in threads if first != "027f"]
        if others or starts:
            problems.append("threads not at CW 027f: %s" % others)
        presented = re.search(r"^frames presented: (\d+); application active: (\w+)", out, re.M)
        if presented and presented.group(2) != "yes":
            problems.append("the application was not active at the end")
        if not presented or int(presented.group(1)) == 0:
            problems.append("no frame was presented")
        dumped = sorted(frames.glob("frame-*.ppm")) if frames.is_dir() else []
        if dumped:
            keep = ROOT / "build/boot/main-menu.ppm"
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dumped[-1], keep)
            size = dumped[-1].read_bytes()[:20].split(b"\n")[1].decode()
            print("last frame (%s): %s" % (size, keep))
            if size != "1024 768":
                problems.append("the last frame is %s, not the menu's 1024x768" % size)
        if before != game_snapshot():
            problems.append("the game folder changed")
        if not (user / "registry.json").is_file():
            problems.append("no registry in the user directory")
        print(re.search(r"== Empire Earth boot report ==.*?(?=import coverage)", out, re.S).group(0)
              if "== Empire Earth boot report ==" in out else out[-3000:])
        if problems:
            sys.stderr.write(out[-6000:])
            for p in problems:
                print("FAIL:", p)
            return 1
        print("Boot test passed: %d milestones to the main menu loop, %d threads, render at 007f, "
              "clean exit" % (MILESTONES, len(threads)))
        return 0
    finally:
        if args.keep_user:
            print("user directory kept: %s" % user)
        else:
            shutil.rmtree(user, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
