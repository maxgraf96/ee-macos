#!/usr/bin/env python3
"""The EH harness test: build ee_eh_harness (the bundles of sxlrt233, the
engine and the executable) and require

  - WON's ReadBuffer to throw ReadBufferException from two frames down and a
    message's Unpack to catch it, return false and run its destructor,
    2000 times over with nothing left behind;
  - with nothing to catch it, the exception to end the process in terminate()
    (exit code 3), naming its type.

    .venv/bin/python tests/eh/run_eh.py [--kit DIR] [--regenerate] [--no-build]
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(binary, args):
    result = subprocess.run([str(binary)] + args, cwd=ROOT, capture_output=True, text=True,
                            timeout=900)
    print(result.stdout, end="")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit", default=None)
    parser.add_argument("--regenerate", action="store_true", help="translate before building")
    parser.add_argument("--no-build", action="store_true", help="run what is already built")
    parser.add_argument("--jobs", default=None)
    args = parser.parse_args()
    if not args.no_build:
        command = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_eh_harness"]
        command += ["--kit", args.kit] if args.kit else []
        command += ["--regenerate"] if args.regenerate else []
        command += ["--jobs", args.jobs] if args.jobs else []
        subprocess.run(command, check=True)
    recomp = ROOT / "build/recomp"
    bundles = ["--bundles", str(recomp / "bundles")]
    caught = run(recomp / "ee_eh_harness", bundles + ["--check"])
    if caught.returncode or "EH harness: all checks passed" not in caught.stdout:
        sys.stderr.write(caught.stderr[-4000:])
        raise SystemExit("ee_eh_harness failed (exit %d)" % caught.returncode)
    unhandled = run(recomp / "ee_eh_harness", bundles + ["--unhandled"])
    if unhandled.returncode != 3 or "ReadBufferException" not in unhandled.stderr:
        sys.stderr.write(unhandled.stderr[-4000:])
        raise SystemExit("an uncaught ReadBufferException did not end in terminate() (exit %d)"
                         % unhandled.returncode)
    print("EH harness test passed: caught, unwound and destroyed; uncaught ends in terminate()")
    return 0


if __name__ == "__main__":
    sys.exit(main())
