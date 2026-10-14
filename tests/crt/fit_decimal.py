"""Say which variant of decimal_model.py Windows follows, from a run of the decimal suite.

  python3 -I tests/crt/fit_decimal.py build/crt/vectors-decimal.tsv build/crt/results-decimal-legion.tsv

Prints, for each variant, how many strtod / sscanf("%f") / sscanf("%lf") results it
reproduces. The kit implements "measured"; a variant that fits better says what to change.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decimal_model as dm  # noqa: E402
from gen_vectors import VARIANTS  # noqa: E402


def load(path):
    with open(path, encoding="latin-1") as f:
        return {l.split("\t", 1)[0]: l.rstrip("\n").split("\t") for l in f if "\t" in l}


def main():
    vec, res = load(sys.argv[1]), load(sys.argv[2])
    score = {name: [0, 0] for name in VARIANTS}
    for vid, v in vec.items():
        if vid not in res or v[1] not in ("strtod", "sscanf_f", "sscanf_lf"):
            continue
        text = bytes.fromhex(v[3]).decode("latin-1")
        r = res[vid]
        if v[1] == "strtod":
            got, P = r[2], 53
        elif r[2] != "1":
            continue
        else:
            got, P = r[3], 24 if v[1] == "sscanf_f" else 53
        lim = (P, -1022, 1023) if P == 53 else (P, -126, 127)
        for name, variant in VARIANTS.items():
            ok = dm.to_bits(*dm.convert(text, *lim, **variant), P=P) == got
            score[name][0] += ok
            score[name][1] += 1
    for name, (ok, n) in sorted(score.items(), key=lambda kv: -kv[1][0]):
        print(f"{name:10s} {ok}/{n}")


if __name__ == "__main__":
    main()
