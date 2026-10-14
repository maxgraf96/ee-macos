"""Compare two x87 result files (instruction or game-function results) vector by vector.

  python -I tests/x87/compare.py REF.jsonl OTHER.jsonl --vectors vectors.jsonl [--by op,cw,tag]
         [--md report.md] [--examples 3] [--sw-mask 0x4500] [--ignore-nan-sign]

REF is normally legion's hardware results; OTHER a model (exact, kit), Unicorn or the Mac port.
Results whose value is null in either file (no model for that op) are counted as "skipped".
For instruction vectors the status word is compared only for compare-class ops, through
--sw-mask (default C0|C2|C3); pass e.g. 0x7f3f to include exception flags, C1 and TOP.
Exit status 1 if any compared vector differs (use in CI).
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402


def load(path):
    meta, rows = {}, {}
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if "meta" in r:
                meta = r["meta"]
                continue
            rows[r["id"]] = r
    return meta, rows


def ordkey(tv):
    """Totally ordered integer for x80/f32/f64 values (None for NaN)."""
    t = tv.split(":")[0]
    if t == "x80":
        se, m = X.x80_parts(tv)
        e = se & 0x7fff
        if e == 0x7fff and (m & 0x7fffffffffffffff):
            return None
        k = e * (1 << 63) + (m & 0x7fffffffffffffff) if e else m
        return -k if se >> 15 else k
    if t in ("f32", "f64"):
        n = 32 if t == "f32" else 64
        u = int(tv.split(":")[1], 16)
        mag = u & ((1 << (n - 1)) - 1)
        expmask = ((1 << (8 if n == 32 else 11)) - 1) << (23 if n == 32 else 52)
        if (mag & expmask) == expmask and mag & ~expmask:
            return None
        return -mag if u >> (n - 1) else mag
    if t.startswith("i"):
        return int.from_bytes(X.to_bytes(tv), "little", signed=True)
    return None


def is_nan(tv):
    return tv is not None and ":" in tv and tv.split(":")[0] in ("x80", "f32", "f64") and ordkey(tv) is None


def same(a, b, ignore_nan_sign):
    if a == b:
        return True
    if ignore_nan_sign and is_nan(a) and is_nan(b):
        return True
    return False


def dist(a, b):
    ka, kb = ordkey(a), ordkey(b)
    if ka is None or kb is None:
        return "nan"
    return abs(ka - kb)


def fields(r):
    """Comparable value fields of a result row (instruction or call)."""
    out = {}
    if "out" in r:
        if r["out"] is None:
            return None
        for i, v in enumerate(r["out"]):
            out["out%d" % i] = v
        if "mem" in r:
            out["mem"] = r["mem"]
    else:
        for k in ("st0", "eax", "edx", "this"):
            if r.get(k) is not None:
                out[k] = r[k]
        for i, b in enumerate(r.get("bufs") or []):
            out["buf%d" % i] = b
        if "error" in r:
            out["error"] = r["error"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("other")
    ap.add_argument("--vectors")
    ap.add_argument("--by", default="op,cw")
    ap.add_argument("--md")
    ap.add_argument("--examples", type=int, default=2)
    ap.add_argument("--sw-mask", type=lambda s: int(s, 0), default=0x4500)
    ap.add_argument("--ignore-nan-sign", action="store_true")
    a = ap.parse_args()

    mref, ref = load(a.ref)
    moth, oth = load(a.other)
    vec = {}
    if a.vectors:
        with open(a.vectors) as f:
            for line in f:
                if line.strip():
                    v = json.loads(line)
                    vec[v["id"]] = v
    keys = a.by.split(",")
    groups = collections.OrderedDict()
    for vid, r in ref.items():
        v = vec.get(vid, {})
        gk = tuple(str(v.get(k, v.get("fn", "?") if k == "op" else "?")) if k != "tag" else
                   str(v.get("tag", "?")) for k in keys)
        g = groups.setdefault(gk, {"n": 0, "diff": 0, "skip": 0, "missing": 0, "dist": collections.Counter(),
                                   "fields": collections.Counter(), "ex": []})
        o = oth.get(vid)
        if o is None:
            g["missing"] += 1
            continue
        fr, fo = fields(r), fields(o)
        if fr is None or fo is None or "not modelled" in str(r.get("error", "")) or \
                "not modelled" in str(o.get("error", "")):
            g["skip"] += 1
            continue
        g["n"] += 1
        bad = []
        for k in sorted(set(fr) | set(fo)):
            if not same(fr.get(k), fo.get(k), a.ignore_nan_sign):
                bad.append(k)
                if fr.get(k) and fo.get(k):
                    d = dist(fr[k], fo[k])
                    g["dist"][d if d == "nan" or d <= 4 else (">4" if d < 1 << 11 else ">=2^11")] += 1
        op = v.get("op")
        if op and X.OPS.get(op, {}).get("cls") == "compare" and r.get("sw") and o.get("sw"):
            if (int(r["sw"], 16) ^ int(o["sw"], 16)) & a.sw_mask:
                bad.append("sw")
        if bad:
            g["diff"] += 1
            for k in bad:
                g["fields"][k] += 1
            if len(g["ex"]) < a.examples:
                g["ex"].append({"id": vid, "fields": bad, "vec": {k: v.get(k) for k in ("op", "fn", "cw", "a", "b", "m", "args")
                                                                    if k in v},
                                "ref": {k: r.get(k) for k in bad if k in r} or fr, "other": {k: o.get(k) for k in bad if k in o} or fo})
    total = sum(g["n"] for g in groups.values())
    diffs = sum(g["diff"] for g in groups.values())
    L = ["# x87 comparison", "", "- ref: `%s` %s" % (a.ref, json.dumps(mref)),
         "- other: `%s` %s" % (a.other, json.dumps(moth)),
         "- compared %d vectors, %d differ" % (total, diffs), "",
         "| %s | n | differ | %% | fields | distance (ulps of the result format) | skipped |" % " | ".join(keys),
         "|%s---|---|---|---|---|---|---|" % ("---|" * len(keys))]
    for gk, g in groups.items():
        pct = 100.0 * g["diff"] / g["n"] if g["n"] else 0.0
        L.append("| %s | %d | %d | %.2f | %s | %s | %d |" % (
            " | ".join(gk), g["n"], g["diff"], pct, ", ".join("%s:%d" % kv for kv in g["fields"].most_common()),
            ", ".join("%s:%d" % (k, c) for k, c in sorted(g["dist"].items(), key=lambda kv: str(kv[0]))),
            g["skip"] + g["missing"]))
    exs = [(gk, e) for gk, g in groups.items() for e in g["ex"]]
    if exs:
        L += ["", "## Examples", ""]
        for gk, e in exs[:200]:
            L.append("- %s `%s`: %s" % ("/".join(gk), e["id"], json.dumps(e)))
    text = "\n".join(L) + "\n"
    if a.md:
        with open(a.md, "w") as f:
            f.write(text)
    print(text if len(text) < 20000 else text[:20000] + "\n... (truncated; see --md)")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
