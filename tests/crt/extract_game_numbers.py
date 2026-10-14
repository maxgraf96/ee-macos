"""Collect the numeric strings Empire Earth parses at run time, for the CRT vectors.

The game reads decimal text in two places that matter here:
  - the random-map scripts (Data/Random Map Scripts/**/*.rmv, plain text on disk), which
    Default.dll parses with sscanf("%d") and sscanf("%f");
  - the unit AI scripts (unit ai scripts/*.tai inside Data/data.ssa), parsed by the EXE.
`db/*.dat` are binary records (floats stored as IEEE bits), so they carry no decimal text;
the converter log in data.ssa lists the stats the designers typed, which are the same kind
of short decimals, and is included as a third, informational source.

Our own reader: the .ssa index layout is documented in docs/reference/audio.md section 5.
Compressed entries are PKWARE DCL streams; reading them needs a DCL decoder library with
`int dcl_decompress(const void*, unsigned, void*, unsigned, unsigned*)` (the one
docs/reference/audio.md builds from zlib's contrib/blast). Without --dcl-lib only the .rmv
files are scanned.

The output is derived from game files, so it is written under analysis/ (git-ignored):

  python3 -I tests/crt/extract_game_numbers.py --game-dir "original/gog/Empire Earth" \
      --dcl-lib analysis/audio/build/libdcl.dylib -o analysis/crt/game-numbers.tsv

Output: TSV lines `token<TAB>count<TAB>sources`, sorted by count.
"""
import argparse
import collections
import ctypes
import os
import re
import struct
import sys

# A decimal token as the game's parsers would see it: an optional sign, digits with an
# optional point, an optional exponent. Version strings and dates are excluded by
# requiring the token not to touch other digits, points or letters.
TOKEN = re.compile(rb"(?<![\w.])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?(?![\w.])")


def scan_text(data, source, counts, sources):
    for raw_line in data.splitlines():
        line = raw_line.split(b"//", 1)[0]  # comments carry dates and versions
        for m in TOKEN.finditer(line):
            tok = m.group(0).decode("ascii")
            counts[tok] += 1
            sources[tok].add(source)


def ssa_entries(path):
    with open(path, "rb") as f:
        magic, _vmaj, _vmin, idxlen = struct.unpack("<4sIII", f.read(16))
        if magic != b"rass":
            raise SystemExit(f"{path}: not an SSA archive")
        end = 16 + idxlen
        while f.tell() < end:
            n = struct.unpack("<I", f.read(4))[0]
            name = f.read(n).rstrip(b"\0").decode("latin-1")
            start, _stop, size = struct.unpack("<III", f.read(12))
            yield name, start, size


def ssa_read(path, start, size, dcl):
    with open(path, "rb") as f:
        f.seek(start)
        raw = f.read(size)
    if raw[:4] != b"PK01":
        return raw
    unpacked = struct.unpack_from("<I", raw, 4)[0]
    out = ctypes.create_string_buffer(unpacked)
    got = ctypes.c_uint(0)
    rc = dcl.dcl_decompress(raw[12:], len(raw) - 12, out, unpacked, ctypes.byref(got))
    if rc != 0:
        raise RuntimeError(f"DCL stream failed ({rc})")
    return out.raw[:got.value]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", required=True)
    ap.add_argument("--dcl-lib", help="DCL decoder shared library for data.ssa entries")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    counts = collections.Counter()
    sources = collections.defaultdict(set)
    rmv_root = os.path.join(args.game_dir, "Data", "Random Map Scripts")
    nfiles = 0
    for dirpath, _dirs, files in os.walk(rmv_root):
        for name in sorted(files):
            if name.lower().endswith(".rmv"):
                with open(os.path.join(dirpath, name), "rb") as f:
                    scan_text(f.read(), "rmv", counts, sources)
                nfiles += 1
    print(f"{nfiles} .rmv files", file=sys.stderr)

    if args.dcl_lib:
        dcl = ctypes.CDLL(os.path.abspath(args.dcl_lib))
        ssa = os.path.join(args.game_dir, "Data", "data.ssa")
        n_tai = n_log = 0
        for name, start, size in ssa_entries(ssa):
            low = name.lower()
            if low.endswith(".tai"):
                scan_text(ssa_read(ssa, start, size, dcl), "tai", counts, sources)
                n_tai += 1
            elif low.startswith("db\\") and low.endswith(".log"):
                scan_text(ssa_read(ssa, start, size, dcl), "dblog", counts, sources)
                n_log += 1
        print(f"{n_tai} .tai scripts, {n_log} db logs from data.ssa", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as out:
        for tok, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            out.write(f"{tok}\t{n}\t{','.join(sorted(sources[tok]))}\n")
    print(f"{len(counts)} distinct tokens -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
