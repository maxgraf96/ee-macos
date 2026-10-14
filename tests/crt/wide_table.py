"""Turn measured wctype results into the HLE's wide-character table input.

  python3 -I tests/crt/wide_table.py build/crt/vectors-wide.tsv build/crt/results-wide-legion.tsv \
      -o build/crt/msvcrt-wide-measured.tsv
  (cd kit && python3 tools/gen_msvcrt_unicode.py --measured ../build/crt/msvcrt-wide-measured.tsv \
      > runtime/msvcrt_unicode.h)

Any vector/result pair containing `wctype` rows works (the full-BMP suite from
`gen_vectors.py --suite wide`, or the wctype rows of the main suite). Output lines:
`codepoint<TAB>iswctype(w, 0x1ff) under "C"<TAB>... under "English"<TAB>towupper
(English)<TAB>towlower (English)`, hex; a field is `-` when that case was not measured.
The classification and case mapping of Windows' msvcrt.dll for those code points is the
whole content: facts measured by calling the functions, nothing taken from Windows files.
"""
import argparse


def load(path):
    with open(path, encoding="latin-1") as f:
        return {l.split("\t", 1)[0]: l.rstrip("\n").split("\t") for l in f if "\t" in l}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vectors")
    ap.add_argument("results")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    vec, res = load(args.vectors), load(args.results)
    table = {}
    for vid, v in vec.items():
        if v[1] != "wctype" or vid not in res or len(res[vid]) < 5 or res[vid][2] in ("EXC", "CRASH"):
            continue
        w = int(v[3]) & 0xFFFF
        up, lo, bits = res[vid][2], res[vid][3], res[vid][4]
        row = table.setdefault(w, {"C": "-", "English": "-", "up": "-", "lo": "-"})
        row[v[2]] = bits
        if v[2] == "English":
            row["up"], row["lo"] = up, lo
    with open(args.output, "w") as out:
        for w in sorted(table):
            r = table[w]
            out.write(f"{w:04x}\t{r['C']}\t{r['English']}\t{r['up']}\t{r['lo']}\n")
    print(f"{len(table)} code points -> {args.output}")


if __name__ == "__main__":
    main()
