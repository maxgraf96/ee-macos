#!/usr/bin/env python3
"""The M1 harness test: build ee_m1_harness (freestanding bundles, dlopen) and
ee_m1_harness_static (linked archives), run both, and require

  - every module's translation to register and its DllMain to return TRUE,
  - PIMGetName -> "Default", PIMGetType -> 0x726d6170, PIMIsValid -> 1,
  - EERMPIMSetRandomMapValues to fill the context identically in both builds.

    .venv/bin/python tests/m1/run_harness.py [--kit DIR] [--prerebased] [--regenerate] [--no-build]

--prerebased (TEMPORARY) uses the derived game directory tools/prerebase.py
stages, for a kit whose loader cannot relocate DLLs yet. Run through
tools/test.py --m1.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import kitpath  # noqa: E402

SCRIPT = "Continental"


def run(binary, args):
    result = subprocess.run([str(binary)] + args + ["--check", "--rmv", SCRIPT], cwd=ROOT,
                            capture_output=True, text=True, timeout=600)
    print(result.stdout, end="")
    if result.returncode:
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit("%s failed (exit %d)" % (binary.name, result.returncode))
    digest = re.search(r"EERMPIMSetRandomMapValues\(ctx, \"%s\"\) -> \d+; (\d+) non-zero dwords.*digest ([0-9a-f]{8})"
                       % SCRIPT, result.stdout)
    if "M1 harness: all checks passed" not in result.stdout or not digest:
        raise SystemExit("%s did not pass every check" % binary.name)
    return digest.group(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit", default=None)
    parser.add_argument("--prerebased", action="store_true", help="TEMPORARY: pre-relocated DLL copies")
    parser.add_argument("--regenerate", action="store_true", help="translate before building")
    parser.add_argument("--no-build", action="store_true", help="run what is already built")
    parser.add_argument("--jobs", default=None)
    args = parser.parse_args()
    game_dir = ROOT / "build/prerebased" if args.prerebased else ROOT
    build_root = game_dir / "build"
    if not args.no_build:
        command = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_m1_harness",
                   "--cmake-target", "ee_m1_harness_static"]
        command += ["--kit", args.kit] if args.kit else []
        command += ["--prerebased"] if args.prerebased else []
        command += ["--regenerate"] if args.regenerate else []
        command += ["--jobs", args.jobs] if args.jobs else []
        subprocess.run(command, check=True)
    recomp = build_root / "recomp"
    bundled = run(recomp / "ee_m1_harness", ["--bundles", str(recomp / "bundles")])
    linked = run(recomp / "ee_m1_harness_static", [])
    if bundled != linked:
        raise SystemExit("the bundle build filled the context differently (%s) from the linked build (%s)"
                         % (bundled, linked))
    print("M1 harness test passed: both builds, context digest %s" % bundled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
