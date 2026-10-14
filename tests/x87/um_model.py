"""The engine's UM* maths functions under the double model: Python doubles for x87 registers
at PC=53, float rounding at every m32 store, FISTP = round-half-even, x87 compare flags (an
unordered compare sets C3, C2 and C0). Constants and lookup tables are read from the user's own
Low-Level Engine.dll at the addresses the functions use (RVAs below), never copied.

This is what translated code computes if x87 registers are plain doubles, so

  python -I tests/x87/um_model.py --game "original/gog/Empire Earth" um.jsonl -o um-double.jsonl
  python -I tests/x87/compare.py um-unicorn.jsonl um-double.jsonl --vectors um.jsonl --by fn,cw

checks the ADR-002 claim "the double model is exact for the UM* entry points at PC=53" against
Unicorn's x87, and the same file can be diffed against legion's ummath_vectors.js results.
Only PC=53 vectors are modelled (the render thread's PC=24 calls are reported as not modelled).
"""
import argparse
import json
import math
import os
import struct
import sys
from fractions import Fraction

import pefile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402

# Engine RVAs (Low-Level Engine.dll, GOG build, sha256 e3f6a2f6...3214)
RVA = dict(zero=0x94504, one=0x94508, half_pi=0x94818, m_one=0x96504, pi=0x9653c, m_pi=0x96534,
           two_pi=0x96538, cos_scale=0x96744, c3=0x96748, c5=0x9674c, c7=0x96750, acos_lim=0x96754,
           acos_scale=0x96758, quarter_pi=0x9675c, m_half_pi=0x96760, m_fltmax=0x96764, fltmax=0x96768,
           four=0x964f0, cos_table=0xd9130, acos_table=0xe9134)


def f32(v):
    try:
        return struct.unpack("<f", struct.pack("<f", v))[0]
    except OverflowError:
        return math.copysign(math.inf, v)
    except (ValueError, struct.error):
        return v                              # NaN passes through


def fistp32(v):
    if math.isnan(v) or math.isinf(v) or not (-2147483648.5 < v < 2147483647.5):
        return -2147483648
    return int(round(v))                      # Python rounds half to even, as FISTP at RC=near


def fcom(a, b):
    """(CF, ZF) after FCOM a,b; FNSTSW AX; SAHF  (C0 -> CF, C3 -> ZF; unordered sets both)."""
    if math.isnan(a) or math.isnan(b):
        return True, True
    return a < b, a == b


INDEF = struct.unpack("<d", struct.pack("<Q", 0xfff8000000000000))[0]


def crt_sqrt(x):
    if math.isnan(x):
        return x
    if x < 0:
        return INDEF
    return math.sqrt(x)


class UM:
    def __init__(self, dll):
        pe = pefile.PE(dll)
        img = pe.get_memory_mapped_image()
        self.k = {n: struct.unpack_from("<f", img, r)[0] for n, r in RVA.items() if not n.endswith("table")}
        self.cos_t = struct.unpack_from("<16385f", img, RVA["cos_table"])
        self.acos_t = struct.unpack_from("<32760f", img, RVA["acos_table"])

    def cos(self, x):
        k = self.k
        v = x
        while True:
            cf, zf = fcom(v, k["m_pi"])
            if not cf:
                break
            v = v + k["two_pi"]
        while True:
            cf, zf = fcom(v, k["pi"])
            if cf or zf:
                break
            v = v - k["two_pi"]
        cf, zf = fcom(v, k["zero"])
        if cf:
            v = -v
        t = f32(k["cos_scale"] * v)
        return self.cos_t[fistp32(t)]

    def acos(self, x):
        k = self.k
        v = x
        cf, zf = fcom(v, k["zero"])
        if not (cf or zf):                                   # v > 0
            cf, _ = fcom(v, k["acos_lim"])
            if cf:
                return self.acos_t[fistp32(f32(v * k["acos_scale"]))]
            cf, _ = fcom(v, k["one"])
            if not cf:
                return 0.0
            s = crt_sqrt(k["one"] - v * v)
            t = s / x
            t2f = f32(t * t)
            xm = f32(t)
            p3 = t * t2f
            a1 = f32(xm - k["c3"] * p3)
            p5 = p3 * t2f
            a2 = a1 + k["c5"] * p5
            p7 = t2f * p5
            return a2 - p7 * k["c7"]
        cf, zf = fcom(v, k["zero"])
        if cf:                                               # v < 0 or unordered
            cf, _ = fcom(v, k["m_one"])
            if cf:
                v = k["m_one"]
            return k["pi"] - self.acos(f32(-v))
        return k["half_pi"]

    def atan2(self, y, x):
        k = self.k
        cf, zf = fcom(x, k["zero"])
        if zf:
            cf, _ = fcom(y, k["zero"])
            return k["m_half_pi"] if cf else k["half_pi"]
        s = crt_sqrt(y * y + x * x)
        q = f32(x / s)
        cf, _ = fcom(y, k["zero"])
        r = self.acos(q)
        return -r if cf else r

    def atan(self, x):
        k = self.k
        v = x
        cf, zf = fcom(v, k["zero"])
        if not (cf or zf):
            cf, _ = fcom(v, k["one"])
            if cf:
                x2 = v * v
                x2f = f32(x2)
                p3f = f32(x2 * v)
                a1 = v - k["c3"] * p3f
                p5 = x2f * p3f
                a2 = k["c5"] * p5 + a1
                p7 = x2f * p5
                return a2 - p7 * k["c7"]
        cf, zf = fcom(v, k["one"])
        if not (cf or zf):                                   # v > 1
            xm = f32(1.0 / v)
            if math.isfinite(xm):
                return k["half_pi"] - self.atan(xm)
            return k["half_pi"]
        cf, _ = fcom(v, k["m_one"])
        if cf:                                               # v < -1 or unordered
            xm = f32(k["m_one"] / v)
            if math.isfinite(xm):
                return self.atan(xm) - k["half_pi"]
            return k["m_half_pi"]
        cf, _ = fcom(v, k["zero"])
        if cf:
            return -self.atan(f32(-v))
        _, zf = fcom(v, k["zero"])
        return 0.0 if zf else k["quarter_pi"]

    def tan(self, x):
        k = self.k
        c = self.cos(x)
        cf = f32(c)
        _, zf = fcom(c, k["zero"])
        if not zf:
            return self.cos(f32(k["half_pi"] - x)) / cf
        cf_, _ = fcom(x, k["zero"])
        if not cf_:
            cf2, _ = fcom(x, k["half_pi"])
            if cf2:
                return k["fltmax"]
        cf3, _ = fcom(x, k["m_pi"])
        if cf3:
            return k["m_fltmax"]
        cf4, zf4 = fcom(x, k["m_half_pi"])
        if not cf4 and not zf4:
            return k["m_fltmax"]
        return k["fltmax"]

    def quad(self, a, b, c):
        k = self.k
        _, zf = fcom(a, k["zero"])
        if zf:
            _, zf2 = fcom(b, k["zero"])
            if zf2:
                return 0, None, None
            r = -c / b
            return 1, f32(r), f32(r)
        D = b * b - a * c * k["four"]
        am = f32(a + a)
        r1 = f32((crt_sqrt(D) - b) / am)
        bm = -b
        r2 = f32((bm - crt_sqrt(D)) / am)
        return 1, r1, r2


def x80_of(v):
    if math.isnan(v):
        u = struct.unpack("<Q", struct.pack("<d", v))[0]
        return X.x80(((u >> 63) << 15) | 0x7fff, (1 << 63) | ((u & ((1 << 52) - 1)) << 11))
    if math.isinf(v):
        return X.x80(0xffff if v < 0 else 0x7fff, 1 << 63)
    s = 1 if math.copysign(1.0, v) < 0 else 0
    if v == 0:
        return X.x80(s << 15, 0)
    return X.encode("x80", ("num", Fraction(v), s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vectors")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--game", default="original/gog/Empire Earth")
    a = ap.parse_args()
    um = UM(os.path.join(a.game, X.ENGINE))
    fns = {"UMCos": um.cos, "UMArcCos": um.acos, "UMArcTan": um.atan, "UMTan": um.tan,
           "UMArcTan2": um.atan2, "UMFastMath::Sqrt": lambda x: crt_sqrt(x), "sqrt": crt_sqrt}
    n = 0
    with open(a.vectors) as f, open(a.out, "w") as out:
        out.write(json.dumps({"meta": {"backend": "um-double-model"}}) + "\n")
        for line in f:
            v = json.loads(line)
            if v.get("kind") != "call":
                continue
            r = {"id": v["id"]}
            if v.get("cw", "027f") != "027f" or (v["fn"] not in fns and v["fn"] != "UMQuadraticEquation"):
                r["error"] = "not modelled"
            else:
                args = [X.f32_of(x) if x.startswith("f32") else (X.f64_of(x) if x.startswith("f64") else None)
                        for x in v["args"]]
                if v["fn"] == "UMQuadraticEquation":
                    ok, r1, r2 = um.quad(*args[:3])
                    r.update(st0=None, eax="i32:%08x" % ok, edx=None, bufs=[
                        "hex:" + ((struct.pack("<f", r1) if r1 is not None else b"\0" * 4) + b"\0" * 12).hex(),
                        "hex:" + ((struct.pack("<f", r2) if r2 is not None else b"\0" * 4) + b"\0" * 12).hex()])
                else:
                    r.update(st0=x80_of(fns[v["fn"]](*args)), eax=None, edx=None, bufs=[])
            out.write(json.dumps(r) + "\n")
            n += 1
    print("um_model: %d call vectors -> %s" % (n, a.out))


if __name__ == "__main__":
    main()
