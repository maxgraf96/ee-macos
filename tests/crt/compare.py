"""Compare msvcrt.dll results (crtvec.exe on Windows) with the HLE's (crt_tests --vectors).

  python3 -I tests/crt/compare.py build/crt/vectors.tsv build/crt/results-win.tsv \
      build/crt/results-mac.tsv [--report build/crt/report.md] [--examples 8]

Classes (README.md explains why):
  strict         must be identical: everything a program can observe from the C runtime's
                 deterministic functions (parsing, formatting, rand, strings, ctype, files...).
  truthy         ctype predicates: the value only matters as zero / non-zero; raw bit
                 differences are counted separately.
  informational  transcendental maths (log, sin, pow, _CI* other than _CIsqrt), every
                 80-bit result field and every result at control word 0x037F (PC=64; the
                 HLE holds doubles), setlocale("") (the Windows user's own locale), and
                 strftime %Z.
Windows-side EXC / IPH / CRASH lines are listed on their own.
Exit status: 0 when no strict difference, 1 otherwise.
"""
import argparse
import collections
import sys

TRANSCENDENTAL = {"log", "sin", "pow", "_CIsin", "_CIcos", "_CItan", "_CIexp", "_CIlog",
                  "_CIpow", "_CIatan2"}


def load(path):
    rows = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 2:
                rows[fields[0]] = fields
    return rows


def decode(h):
    try:
        return repr(bytes.fromhex(h).decode("latin-1"))
    except ValueError:
        return h


# Which input fields (after id and op) are hex-encoded text, for the report.
HEX_FIELDS = {"strtod": {1}, "atof": {1}, "sscanf_f": {1}, "sscanf_lf": {1}, "sscanf_d": {0},
              "atoi": {0}, "atoi64": {0}, "strtol": {0}, "strtoul": {0}, "sprintf_d": {1},
              "sprintf_i": {0}, "sprintf_i64": {0}, "sprintf_s": {0, 1}, "snprintf": {1, 2},
              "cmp": {2, 3}, "setlocale": {1}, "strftime": {1}, "mbcase": {1},
              "splitpath": {0}, "typename": {0}, "textfile": {0, 1}}


# Which output fields are hex-encoded text.
OUT_HEX = {"sprintf_d": {0}, "sprintf_i": {0}, "sprintf_i64": {0}, "sprintf_s": {0},
           "itoa": {0}, "typename": {0}, "splitpath": {0, 1, 2, 3}, "asctime": {0},
           "strftime": {1}, "setlocale": set(range(12)), "mbcase": {0, 1}}


def show(op, fields):
    hexes = OUT_HEX.get(op, set())
    return "\t".join(decode(f) if i in hexes and f not in ("NULL", "") else f
                     for i, f in enumerate(fields))


def describe(vec):
    hexes = HEX_FIELDS.get(vec[1], set())
    return " ".join([vec[1]] + [decode(f) if i in hexes else f for i, f in enumerate(vec[2:])])


def classify(vec, win, mac):
    """Returns (strict_equal, informational_note) for one vector."""
    op = vec[1]
    w, m = win[2:], mac[2:]
    # Documented divergences (README "Known divergences"): Windows behaviour that
    # is undefined or a property of its heap, compared loosely.
    if op == "ctype" and not -128 <= int(vec[3]) <= 255:
        strict = w[5:] == m[5:]  # tolower/toupper are defined: the low byte, mapped
        return strict, ("undefined: classes read past msvcrt's table" if w[:5] != m[:5] else None)
    if op == "msize":
        return w[:1] == m[:1], ("heap address alignment differs" if w[1:] != m[1:] else None)
    if op == "textfile" and vec[3] == "" and len(w) > 10:
        strict = w[:9] == m[:9] and w[10:] == m[10:]
        return strict, ("ftell after ungetc past an empty file's end" if w[9] != m[9] else None)
    if op == "ctype":
        if len(w) != len(m):
            return False, None
        truth_w = [int(x, 16) != 0 for x in w[:5]]
        truth_m = [int(x, 16) != 0 for x in m[:5]]
        strict = truth_w == truth_m and w[5:] == m[5:]
        raw = w[:5] != m[:5]
        return strict, ("ctype raw bits differ" if raw else None)
    if op in ("math", "ci") and vec[3].lower() == "037f":
        # PC=64: the result has a 64-bit significand, which the HLE's double
        # registers cannot hold; the x87 layer's extended path decides it.
        return True, ("PC=64 result differs" if w != m else None)
    if op == "math":
        fn = vec[2]
        if fn in TRANSCENDENTAL:
            return True, ("value differs" if w[:1] != m[:1] else None) or \
                ("80-bit differs" if w[1:2] != m[1:2] else None)
        strict = w[:1] == m[:1] and w[2:] == m[2:]
        return strict, ("80-bit differs" if w[1:2] != m[1:2] else None)
    if op == "ci":
        fn = vec[2]
        if fn in TRANSCENDENTAL:
            return True, ("value differs" if w[:1] != m[:1] else None) or \
                ("80-bit differs" if w[1:2] != m[1:2] else None)
        return w[:1] == m[:1], ("80-bit differs" if w[1:2] != m[1:2] else None)
    if op == "setlocale" and bytes.fromhex(vec[3]).decode("latin-1") in ("", ".1252", ".ACP", ".OCP"):
        # The user's default locale: a property of the machine, not of msvcrt.
        return True, ("user-default locale differs" if w != m else None)
    if op == "strftime" and "%Z" in bytes.fromhex(vec[3]).decode("latin-1"):
        return True, ("time zone name differs" if w != m else None)
    return w == m, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vectors")
    ap.add_argument("windows")
    ap.add_argument("mac")
    ap.add_argument("--report")
    ap.add_argument("--examples", type=int, default=8)
    args = ap.parse_args()
    vecs = load(args.vectors)
    win = load(args.windows)
    mac = load(args.mac)

    stats = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)
    info_examples = collections.defaultdict(list)
    windows_events = []
    for vid, vec in sorted(vecs.items(), key=lambda kv: int(kv[0])):
        op = vec[1] if vec[1] not in ("math", "ci") else f"{vec[1]}:{vec[2]}"
        if vid not in win:
            stats[op]["missing-win"] += 1
            continue
        if vid not in mac:
            stats[op]["missing-mac"] += 1
            continue
        w, m = win[vid], mac[vid]
        if len(w) > 2 and w[2] in ("EXC", "CRASH") or w[-1] == "IPH":
            windows_events.append((vid, describe(vec), "\t".join(w[2:])))
            stats[op]["windows-event"] += 1
            continue
        equal, note = classify(vec, w, m)
        stats[op]["total"] += 1
        if equal:
            stats[op]["equal"] += 1
        else:
            stats[op]["DIFF"] += 1
            if len(examples[op]) < args.examples:
                examples[op].append((vid, describe(vec), show(vec[1], w[2:]), show(vec[1], m[2:])))
        if note:
            stats[op]["info"] += 1
            if len(info_examples[op]) < args.examples:
                info_examples[op].append((vid, note, describe(vec), show(vec[1], w[2:]),
                                          show(vec[1], m[2:])))

    lines = ["# msvcrt.dll vs HLE", "",
             "| op | total | equal | strict diffs | informational | Windows EXC/IPH/CRASH |",
             "|---|---|---|---|---|---|"]
    strict_total = 0
    for op in sorted(stats):
        s = stats[op]
        strict_total += s["DIFF"]
        lines.append(f"| {op} | {s['total']} | {s['equal']} | {s['DIFF']} | {s['info']} | "
                     f"{s['windows-event']} |")
    lines.append("")
    if examples:
        lines.append("## Strict differences (first examples per op)")
        for op in sorted(examples):
            lines.append(f"### {op}")
            for vid, desc, w, m in examples[op]:
                lines += [f"- #{vid} `{desc}`", f"  - windows: `{w}`", f"  - hle:     `{m}`"]
        lines.append("")
    if info_examples:
        lines.append("## Informational differences")
        for op in sorted(info_examples):
            lines.append(f"### {op}")
            for vid, note, desc, w, m in info_examples[op]:
                lines += [f"- #{vid} {note}: `{desc}`", f"  - windows: `{w}`", f"  - hle:     `{m}`"]
        lines.append("")
    if windows_events:
        lines.append("## Windows-side exceptions, invalid-parameter calls and crashes")
        for vid, desc, w in windows_events[:200]:
            lines.append(f"- #{vid} `{desc}`: `{w}`")
    text = "\n".join(lines) + "\n"
    if args.report:
        with open(args.report, "w") as f:
            f.write(text)
    print(text if not args.report else "\n".join(lines[:len(stats) + 4]))
    print(f"strict differences: {strict_total}")
    return 1 if strict_total else 0


if __name__ == "__main__":
    sys.exit(main())
