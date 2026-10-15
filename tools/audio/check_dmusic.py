#!/usr/bin/env python3
"""Empire Earth's eight music tracks through the kit's DirectMusic (P4-A).

Each track is loaded from the player's Data/Music by the kit's DirectMusic
shim, the way SEConductor loads it (search directory, GetObject, download,
SetRepeats(100000), master groove level), rendered offline by the kit's
`dmusic_render` and measured. Two checks against docs/reference/audio.md:

  raw     150 s at intensity 1 (master groove 1), no volume, no reverb, 44.1 kHz:
          every track loads, is not silent (fewer than 5 % silent 100 ms windows)
          and its level is audio.md §3.2's for patched dmusic, less the kit's
          -3 dB trim, within 1.5 dB.
  legion  Pawns with the forced intensity schedule of §11.4 at the in-game
          Music Volume (62 %, SetVolume(0.62) = -478 mB) and Pawns' own
          reverb (SetReverbStyle(0, 0.25, -9, 2100), §2.4), 48 kHz: each
          phase's level within 2.5 dB of the Windows loopback's (which carries
          about -31 dB of other sound, §11.1).

Nothing is written into the repository; --wavs DIR keeps the renders.

    .venv/bin/python tools/audio/check_dmusic.py [--kit DIR] [--build] [--render PATH] [--wavs DIR]

--build builds the kit's dmusic_render for this game first (tools/build.py
--cmake-target dmusic_render). The kit needs DirectMusic (kit feat/dmusic).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import kitpath  # noqa: E402

MUSIC = ROOT / "original/gog/Empire Earth/Data/Music"

# audio.md §3.2: patched dmusic with the GS bank, intensity 1, 150 s, raw (dBFS).
RAW_REFERENCE = {
    "Balance": -10.9, "Enmity": -14.0, "Fallen": -13.5, "Honor": -11.6,
    "Pawns": -14.1, "Rivers": -13.9, "Shadow2": -11.7, "Time": -11.7,
}
TRIM_DB = -3.0  # the kit's output trim (dx/dmusic.cpp kOutputTrimDb)
RAW_TOLERANCE_DB = 1.5
MAX_SILENT_PCT = 5.0

# audio.md §11.4: Pawns on legion, forced schedule, Music Volume 62 %.
SCHEDULE = [(0, 1), (30, 2), (60, 3), (90, 2), (120, 0), (150, 1)]
LEGION_PHASES_DB = [-23.0, -22.0, -19.1, -22.5, -24.0, -23.3]
LEGION_TOLERANCE_DB = 2.5
MUSIC_VOLUME_MB = -478  # round(1000 * ln 0.62)
PAWNS_REVERB = "0,-9,2100,0.25"  # in gain, mix, time, HF ratio (SetReverbStyle(0, 0.25, -9, 2100))


def render(binary, segment, seconds, *extra):
    out = subprocess.run([str(binary), "--dir", str(MUSIC), "--segment", segment, "--seconds", str(seconds),
                          *map(str, extra)], capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
    if out.returncode != 0 or not lines:
        raise RuntimeError("%s: dmusic_render failed (%d): %s" % (segment, out.returncode,
                                                                 (out.stdout + out.stderr)[-400:]))
    return json.loads(lines[-1])


def main(argv):
    kit, rest = kitpath.take_kit(list(argv))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="build dmusic_render for this game first")
    parser.add_argument("--render", type=Path, default=ROOT / "build/recomp/dmusic_render")
    parser.add_argument("--wavs", type=Path, help="keep the renders here (outside the repository)")
    args = parser.parse_args(rest)
    if not MUSIC.is_dir():
        sys.exit("No music at %s: run tools/setup.py --install first" % MUSIC)
    if args.build:
        subprocess.run([sys.executable, str(ROOT / "tools/build.py"), "--kit", str(kit),
                        "--cmake-target", "dmusic_render"], check=True)
    if not args.render.is_file():
        sys.exit("No dmusic_render at %s: pass --build or --render PATH" % args.render)
    if args.wavs:
        args.wavs.mkdir(parents=True, exist_ok=True)

    failures = []
    print("raw: 150 s at intensity 1, no volume or reverb, 44.1 kHz (audio.md §3.2 less %.0f dB)" % -TRIM_DB)
    print("%-8s %8s %8s %6s %7s %7s %6s" % ("track", "rms dB", "want", "diff", "silent", "peak", "x rt"))
    for track, reference in RAW_REFERENCE.items():
        extra = ["--groove", 1, "--rate", 44100]
        if args.wavs:
            extra += ["--wav", args.wavs / ("%s-raw.wav" % track)]
        r = render(args.render, track + ".sgt", 150, *extra)
        want = reference + TRIM_DB
        diff = r["rms_db"] - want
        print("%-8s %8.2f %8.2f %+6.2f %6.1f%% %7.3f %6.0f" % (track, r["rms_db"], want, diff, r["silent_pct"],
                                                               r["peak"], r["x_realtime"]))
        if abs(diff) > RAW_TOLERANCE_DB:
            failures.append("%s: %.2f dB, want %.2f +- %.1f" % (track, r["rms_db"], want, RAW_TOLERANCE_DB))
        if r["silent_pct"] >= MAX_SILENT_PCT:
            failures.append("%s: %.1f %% silent" % (track, r["silent_pct"]))

    print()
    print("legion: Pawns, forced schedule %s, Music Volume 62 %%, its reverb, 48 kHz (audio.md §11.4)"
          % ",".join("%d:%d" % p for p in SCHEDULE))
    extra = ["--rate", 48000, "--volume-mb", MUSIC_VOLUME_MB, "--reverb", PAWNS_REVERB, "--groove", SCHEDULE[0][1],
             "--schedule", ",".join("%d:%d" % p for p in SCHEDULE)]
    if args.wavs:
        extra += ["--wav", args.wavs / "Pawns-legion-schedule.wav"]
    r = render(args.render, "Pawns.sgt", 180, *extra)
    print("%-10s %9s %9s %9s" % ("phase", "rms dB", "Windows", "diff"))
    for phase, windows, (at, groove) in zip(r["phases"], LEGION_PHASES_DB, SCHEDULE):
        diff = phase["rms_db"] - windows
        print("%3d-%3d i%d %9.2f %9.2f %+9.2f" % (phase["from"], phase["to"], groove, phase["rms_db"], windows, diff))
        if abs(diff) > LEGION_TOLERANCE_DB:
            failures.append("Pawns %d-%d s: %.2f dB, Windows %.2f +- %.1f" % (phase["from"], phase["to"],
                                                                          phase["rms_db"], windows,
                                                                          LEGION_TOLERANCE_DB))
    print()
    for f in failures:
        print("FAIL", f)
    print("check_dmusic: %s" % ("all passed" if not failures else "%d failure(s)" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
