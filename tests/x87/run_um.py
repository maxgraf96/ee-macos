#!/usr/bin/env python3
"""ADR-002 criterion 2: the game's maths, called in the translated modules,
against the same calls recorded inside the real game on Windows.

    .venv/bin/python tests/x87/run_um.py [--kit DIR] [--no-build] [--with-exe]
        [--vectors build/x87/um.jsonl] [--recording build/x87/um-legion.jsonl]

Builds the static M1 harness (or ee_um_harness with --with-exe, which links
the executable's translation as well and so also reaches its truncating ftol
helper), runs every call vector with --um, and compares the results with the
recording (tests/x87/compare.py, by function and control word). The
recording is windows/frida/ummath_vectors.js's output; the vectors come from
gen_vectors.py --suite um (docs/decisions/002-exact-x87.md, "Running on
legion").

Criterion 2 requires the engine's UM* functions, UMFastMath::Sqrt, msvcrt
sqrt, _CIsqrt and _ftol, and the executable's ftol helper, to equal the
recording bit for bit (ST(0) as 80 bits, EAX/EDX, out-buffers): exit status
1 otherwise. The other msvcrt functions in the vectors (pow, sin, log,
atof, sscanf, ...) are the C runtime's libm-based implementations and are
reported, not required."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

REQUIRED = {"UMArcTan2", "UMArcCos", "UMArcTan", "UMCos", "UMTan", "UMQuadraticEquation",
            "UMFastMath::Sqrt", "sqrt", "_CIsqrt", "_ftol", "exe_ftol_trunc"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kit")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--with-exe", action="store_true", help="link the executable's translation too")
    ap.add_argument("--vectors", default=str(ROOT / "build/x87/um.jsonl"))
    ap.add_argument("--recording", default=str(ROOT / "build/x87/um-legion.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "build/x87/um-port.jsonl"))
    ap.add_argument("--md", default=str(ROOT / "build/x87/um-legion-vs-port.md"))
    a = ap.parse_args()
    target = "ee_um_harness" if a.with_exe else "ee_m1_harness_static"
    if not a.no_build:
        cmd = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", target]
        cmd += ["--kit", a.kit] if a.kit else []
        subprocess.run(cmd, check=True)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    subprocess.run([str(ROOT / "build/recomp" / target), "--check", "--um", a.vectors, "--um-out", a.out],
                   cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-I", str(HERE / "compare.py"), a.recording, a.out, "--vectors", a.vectors,
                    "--by", "fn,cw", "--md", a.md], cwd=ROOT, stdout=subprocess.DEVNULL)
    # The verdict, from the per-group table compare.py wrote.
    bad, required_n, other_n = [], 0, 0
    for line in open(a.md):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[2].isdigit():
            continue
        fn, cw, n, differ = cells[0], cells[1], int(cells[2]), int(cells[3])
        if fn in REQUIRED:
            required_n += n
            if differ:
                bad.append("%s %s: %d of %d differ" % (fn, cw, differ, n))
        else:
            other_n += n
    print("criterion 2: %d required call vectors, %s; %d others reported in %s"
          % (required_n, "%d groups differ" % len(bad) if bad else "all equal the recording", other_n, a.md))
    for b in bad:
        print("  " + b)
    return 1 if bad or not required_n else 0


if __name__ == "__main__":
    sys.exit(main())
