#!/usr/bin/env python3
"""The M1 replay on the Mac: build it, self-test it, replay packs with it.

    .venv/bin/python tests/m1/replay/run_replay.py [--kit DIR] [--regenerate] [--no-build]
    .venv/bin/python tests/m1/replay/run_replay.py [...] PACKS... [--diff-dir DIR]
                                                   [--results FILE] [-v] [--lenient]

Run through `tools/test.py --m1-replay [PACKS...]`. Both builds take part:
ee_m1_replay (the freestanding bundles, dlopen) and ee_m1_replay_static (the
generated archives linked in).

Without PACKS, the self-tests:
  1. the replay core's own self-test (selftest/run.sh): rmg_pack.py and the
     core over a mock platform, on a synthetic capture;
  2. the round trip, with no Windows involved: `ee_m1_replay --record` runs
     EERMPIMSetRandomMapValues through the translated modules on a synthetic
     context and synthetic scripts (written here, no game data) and records it
     in rmg_capture.js's format; rmg_pack.py --verify packs it; both builds
     replay it and must PASS. Twice: once with the kit's heap, once with the
     modules' heap above guest_size (--heap-at), where the replay has to open
     memory and the kit's string functions have to read it; the two must
     leave the same values in the context;
  3. two deliberate corruptions that must fail: one expected output byte
     (the compare reports a MISMATCH) and one recorded script line (the
     replayed call computes something else).
With PACKS (.rrp files, or directories of them): replays every pack with both
builds and prints the summary; any failure is the exit status.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RECOMP = ROOT / "build" / "recomp"
PACKER = HERE / "rmg_pack.py"

# The synthetic scripts: the script language and its parameter keywords, with
# values of their own; CRLF like the game's. Between() and PERCENT() draw from
# the context's generator, so the seed matters; the include and the #if chains
# exercise the preprocessor.
SCRIPT = r"""// A synthetic random-map script for the Mac replay round trip (run_replay.py).
// Not a game script: the script language and its parameter keywords, with values of its own.
UseMapGenerator		"Default"
StringTableID		1
#include "MacRoundTrip Values"
#if		PERCENT(50)
#define		(kElevationScale,	Between(1, 3))
#else
#define		(kElevationScale,	0.75)
#endif
#if IsSmallMap
#define		(kPercentLand,		0.62)
#elif IsTinyMap
#define		(kPercentLand,		0.55)
#else
#define		(kPercentLand,		0.7)
#endif
MinIntElevation		kMinIntElevation
MaxIntElevation		kMaxIntElevation
InitialElevation	0
FlatElevation		0
ElevationScale		kElevationScale
HeightMapChaos		kHeightMapChaos
PercentLand		kPercentLand
LandElevationThreshold	0
WaterBorder		kWaterBorder
RiverWidth		0
TeamGrowth		false
CoastalToDeeperWaterPercentage	1.0
ShallowsElevation	-1
ShallowsWidth		3
MaxShallowConnectionsPerPlayer	1
GenerateShallows	false
PlayerInnerRadius	kPlayerInnerRadius
PlayerOuterRadius	kPlayerOuterRadius
PlayerPercentFlat	0.3
PlayerTreePercentage	0.1
PlayerLandChaos		0.7
PlayerLandClumps	2
PlayerFlatChaos		0.5
PlayerFlatClumps	3
MaxAngleBetweenTeamMembers	60.0
MinimumStartPositionToMapEdge	4
OuterPushFromMapCenter	2
InnerPushFromMapCenter	0
NeutralInnerRadius	0.15
NeutralOuterRadius	0.45
NumInnerNeutrals	Between(2, 5)
InnerNeutralPercentLand	0.8
InnerNeutralPercentFlat	0.2
InnerNeutralTreePercentage	0.15
InnerNeutralLandChaos	0.6
InnerNeutralLandClumps	2
InnerNeutralFlatChaos	0.4
InnerNeutralFlatClumps	2
NumOuterNeutrals	3
OuterNeutralPercentLand	0.5
OuterNeutralPercentFlat	0.25
OuterNeutralTreePercentage	0.2
OuterNeutralLandChaos	0.55
OuterNeutralLandClumps	1
OuterNeutralFlatChaos	0.45
OuterNeutralFlatClumps	1
MaxResourceElevation	4
ResourceToEdgeDistance	3
ResourceToWaterDistance	2
HeightMapSmoothness	2
"""
VALUES = r"""// Values for MacRoundTrip.rmv (synthetic).
#if			NOTDEFINED(MAC_ROUNDTRIP_VALUES)
#define		(MAC_ROUNDTRIP_VALUES,	1)
#define		(kMinIntElevation,	-5)
#define		(kMaxIntElevation,	7)
#define		(kHeightMapChaos,	Between(10, 20))
#define		(kWaterBorder,		4)
#if Is2Players
#define		(kPlayerInnerRadius,	0.55)
#define		(kPlayerOuterRadius,	0.8)
#else
#define		(kPlayerInnerRadius,	0.6)
#define		(kPlayerOuterRadius,	0.85)
#endif
#endif
"""
FAR_HEAP = 0x2F000000  # above guest_size (0x11000000), where a big game's Windows heap lies
CTX_SCALARS = (0x220, 0x3D0)  # rmg.md 4.2: floats and ints only, no pointers


def builds(bundles_dir):
    return [("bundles", [str(RECOMP / "ee_m1_replay"), "--bundles", str(bundles_dir)]),
            ("linked", [str(RECOMP / "ee_m1_replay_static")])]


def run(cmd, expect=None, quiet=False, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, **kw)
    if not quiet:
        sys.stdout.write(r.stdout)
    if expect is not None and r.returncode != expect:
        sys.stdout.write(r.stdout[-4000:] if quiet else "")
        sys.stderr.write(r.stderr[-4000:])
        raise SystemExit("%s: exit %d, expected %d" % (" ".join(map(str, cmd[:3])), r.returncode, expect))
    return r


def write_scripts(d):
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("MacRoundTrip.rmv", SCRIPT), ("MacRoundTrip Values.rmv", VALUES)):
        (d / name).write_bytes(text.replace("\n", "\r\n").encode())


def blob_path(cap, h):
    return cap / "blobs" / h[:2] / (h + ".bin")


def region_bytes(cap, region):
    return b"".join(blob_path(cap, h).read_bytes() for h in region["chunks"])


def store_blob(cap, data):
    h = hashlib.sha256(data).hexdigest()
    p = blob_path(cap, h)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return h


def ctx_exit(cap):
    call = json.loads((cap / "calls/000000.json").read_text())
    return region_bytes(cap, next(r for r in call["exit"]["regions"] if r["kind"] == "ctx"))


def corrupt_output(src, dst):
    """One byte the call wrote into the context, changed in the expected exit bytes."""
    shutil.copytree(src, dst)
    path = dst / "calls/000000.json"
    call = json.loads(path.read_text())
    entry = region_bytes(dst, next(r for r in call["entry"]["regions"] if r["kind"] == "ctx"))
    region = next(r for r in call["exit"]["regions"] if r["kind"] == "ctx")
    data = bytearray(region_bytes(dst, region))
    off = next(i for i in range(CTX_SCALARS[0], len(data)) if data[i] != entry[i])
    data[off] ^= 0x01
    chunk = 4096
    region["chunks"] = [store_blob(dst, bytes(data[i:i + chunk])) for i in range(0, len(data), chunk)]
    path.write_text(json.dumps(call))
    return "ctx+%#x" % off


def corrupt_input(src, dst):
    """kWaterBorder's value in the recorded script line: 4 becomes 5."""
    shutil.copytree(src, dst)
    path = dst / "calls/000000.json"
    call = json.loads(path.read_text())
    for e in call["files"]:
        line = bytes.fromhex(e.get("data_hex", ""))
        if b"kWaterBorder," in line and b"4" in line:
            e["data_hex"] = line.replace(b"4", b"5").hex()
            path.write_text(json.dumps(call))
            return line.split(b"\0")[0].strip().decode()
    raise SystemExit("corrupt_input: the recorded scripts have no kWaterBorder line")


def selftest_core():
    print("== 1. the replay core's self-test (selftest/run.sh, mock platform)")
    r = run(["sh", str(HERE / "selftest/run.sh")], quiet=True)
    sys.stdout.write("".join("   " + l + "\n" for l in r.stdout.splitlines()))
    if r.returncode or "selftest: ok" not in r.stdout:
        sys.stderr.write(r.stderr[-4000:])
        raise SystemExit("the replay core's self-test failed")


def round_trip(bundles_dir, tmp):
    scripts = tmp / "scripts"
    write_scripts(scripts)
    record = [str(RECOMP / "ee_m1_replay"), "--bundles", str(bundles_dir), "--record"]
    common = ["--scripts", str(scripts), "--rmv", "MacRoundTrip", "--seed", "1037", "--case", "mac-roundtrip"]
    caps = {}
    for label, extra in (("kit heap", []), ("heap above guest_size", ["--heap-at", hex(FAR_HEAP)])):
        print("== 2. round trip, %s" % label)
        cap = tmp / ("capture-" + ("far" if extra else "kit"))
        r = run(record + [str(cap)] + common + extra, quiet=True)
        sys.stdout.write("".join("   " + l + "\n" for l in r.stdout.splitlines()))
        if r.returncode:
            sys.stderr.write(r.stderr[-4000:])
            raise SystemExit("recording failed (exit %d)" % r.returncode)
        packs = tmp / (cap.name + "-packs")
        run([sys.executable, "-I", str(PACKER), "pack", str(cap), "-o", str(packs), "--verify"], expect=0, quiet=True)
        for name, cmd in builds(bundles_dir):
            r = run(cmd + sorted(str(p) for p in packs.glob("*.rrp")), quiet=True)
            line = next((l for l in r.stdout.splitlines() if l.startswith(("PASS", "FAIL", "ERROR", "MISMATCH"))), "")
            print("   %-7s %s" % (name, line))
            if r.returncode or not line.startswith("PASS"):
                sys.stdout.write(r.stdout[-3000:])
                raise SystemExit("the %s build did not replay the %s round trip" % (name, label))
        caps[label] = cap
    a, b = (ctx_exit(c)[CTX_SCALARS[0]:CTX_SCALARS[1]] for c in caps.values())
    print("   context values ctx+%#x..%#x after the call: %s in both recordings"
          % (CTX_SCALARS[0], CTX_SCALARS[1], "the same" if a == b else "DIFFERENT"))
    if a != b:
        raise SystemExit("the heap above guest_size changed what the call computed")
    return caps["kit heap"]


def corruptions(bundles_dir, tmp, good):
    for label, fn, want in (("an expected output byte", corrupt_output, "MISMATCH"),
                            ("a recorded script line", corrupt_input, "MISMATCH")):
        bad = tmp / ("corrupt-" + fn.__name__)
        what = fn(good, bad)
        print("== 3. corrupted: %s (%s)" % (label, what))
        packs = tmp / (bad.name + "-packs")
        run([sys.executable, "-I", str(PACKER), "pack", str(bad), "-o", str(packs), "--verify"], expect=0, quiet=True)
        for name, cmd in builds(bundles_dir):
            r = run(cmd + sorted(str(p) for p in packs.glob("*.rrp")), quiet=True)
            lines = [l for l in r.stdout.splitlines() if l.startswith(want)]
            print("   %-7s exit %d: %s" % (name, r.returncode, lines[0] if lines else "(no %s line)" % want))
            if r.returncode != 1 or not lines:
                sys.stdout.write(r.stdout[-3000:])
                raise SystemExit("the %s build did not detect %s" % (name, label))


def replay_packs(bundles_dir, paths, args):
    packs = []
    for p in map(Path, paths):
        packs += sorted(p.glob("*.rrp")) if p.is_dir() else [p]
    if not packs:
        raise SystemExit("no .rrp packs in %s" % " ".join(paths))
    worst = 0
    for name, cmd in builds(bundles_dir):
        extra = ["-v"] * args.verbose + (["--lenient"] if args.lenient else [])
        if args.diff_dir:
            extra += ["--diff-dir", str(Path(args.diff_dir) / name)]
        if args.results:
            extra += ["--results", "%s.%s.tsv" % (args.results, name)]
        print("== %s build: %d packs" % (name, len(packs)))
        r = subprocess.run(cmd + extra + [str(p) for p in packs], timeout=None)
        worst = max(worst, r.returncode)
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packs", nargs="*", help=".rrp files or directories of them")
    ap.add_argument("--kit", default=None)
    ap.add_argument("--regenerate", action="store_true", help="translate before building")
    ap.add_argument("--no-build", action="store_true", help="run what is already built")
    ap.add_argument("--jobs", default=None)
    ap.add_argument("--diff-dir", default=None, help="expected/actual bytes of mismatching regions")
    ap.add_argument("--results", default=None, help="TSV prefix: <prefix>.<build>.tsv per build")
    ap.add_argument("--lenient", action="store_true", help="without strict mode (RECOMP_STRICT=0)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args()
    if not args.no_build:
        cmd = [sys.executable, str(ROOT / "tools/build.py"), "--cmake-target", "ee_m1_replay",
               "--cmake-target", "ee_m1_replay_static"]
        cmd += ["--kit", args.kit] if args.kit else []
        cmd += ["--regenerate"] if args.regenerate else []
        cmd += ["--jobs", args.jobs] if args.jobs else []
        subprocess.run(cmd, check=True)
    bundles_dir = RECOMP / "bundles"
    if args.packs:
        return replay_packs(bundles_dir, args.packs, args)
    selftest_core()
    with tempfile.TemporaryDirectory(prefix="ee-m1-replay-") as t:
        tmp = Path(t)
        good = round_trip(bundles_dir, tmp)
        corruptions(bundles_dir, tmp, good)
    print("M1 replay self-tests passed: core self-test, round trip on both builds (kit heap and "
          "heap above guest_size), 2 corruptions detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
