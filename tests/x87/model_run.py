"""Mac-side models for x87 instruction vectors; output has the same format as x87_runner.py.

  python -I tests/x87/model_run.py --model exact vectors.jsonl -o results-exact.jsonl
  python -I tests/x87/model_run.py --model kit   vectors.jsonl -o results-kit.jsonl

exact  Our reading of the Intel SDM: rational arithmetic rounded to PC/RC with the 15-bit
       exponent range, x87 NaN rules, x87 stores and integer conversion. This is the
       specification the exact-x87 layer has to implement. For transcendentals it gives the
       correctly rounded 64-bit value (a reference to measure hardware against, not an oracle);
       with --pi66 fsin/fcos/fptan/fsincos reduce with the x87's 66-bit pi.
kit    The recomp-kit runtime as forked (runtime/x86.h, translate.py): x87 registers are
       doubles, PC=24 is a float cast of the double result, constants are doubles,
       transcendentals come from the host libm, m80 loads round to double.
"""
import argparse
import json
import math
import os
import struct
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402

DENORM_Q = -16445          # quantum of extended denormals


def dq(pc):
    """log2 quantum of a tiny register result at precision pc: a p-bit format with the 15-bit
    exponent range, i.e. gradual underflow keeps the quantum of the smallest normal."""
    return -16382 - (pc - 1)


def _sw(flags, depth, ccode=0):
    top = (8 - depth) & 7
    return (flags & 0x3f) | (flags & X.FLAG_C1) | ccode | (top << 11)


def _is_denorm(tv):
    t = tv.split(":")[0]
    b = int.from_bytes(X.to_bytes(tv), "little")
    if t == "f32":
        return (b >> 23) & 0xff == 0 and b & 0x7fffff
    if t == "f64":
        return (b >> 52) & 0x7ff == 0 and b & ((1 << 52) - 1)
    if t == "x80":
        se, m = X.x80_parts(tv)
        return se & 0x7fff == 0 and m != 0
    return False


# ---------------------------------------------------------------------------------- exact model

class Exact:
    def __init__(self, pi66=False):
        self.pi66 = pi66
        self.consts = {}

    def nan_result(self, *vals):
        """x87 QNaN rules for operands that include a NaN: SNaN -> IE; two NaNs -> larger
        significand; the result is quiet."""
        nans = [v for v in vals if v[0] == "nan"]
        flags = X.FLAG_IE if any(not v[3] for v in nans) else 0
        quiet = [v for v in nans if v[3]]
        if len(nans) == 2 and len(quiet) == 1:      # SDM table 4-7: SNaN and QNaN -> the QNaN
            best = quiet[0]
        else:                                        # both of a kind -> larger significand;
            best = nans[0]                           # equal significands -> the positive one
            for v in nans[1:]:                       # (QEMU's choice; legion decides)
                if v[1] > best[1] or (v[1] == best[1] and v[2] < best[2]):
                    best = v
        return ("nan", best[1] | (1 << 62), best[2], True), flags

    def arith(self, opname, a, b, pc, rc):
        if a[0] == "nan" or b[0] == "nan":
            return self.nan_result(a, b)
        kind = opname
        sa, sb = a[2], b[2]
        if kind in ("add", "sub"):
            if kind == "sub":
                b = (b[0], -b[1] if b[0] == "num" else None, 1 - sb)
                sb = b[2]
            if a[0] == "inf" and b[0] == "inf":
                if sa != sb:
                    return X.INDEFINITE, X.FLAG_IE
                return a, 0
            if a[0] == "inf":
                return a, 0
            if b[0] == "inf":
                return b, 0
            s = a[1] + b[1]
            if s == 0:
                if a[1] == 0 and b[1] == 0 and sa == sb:
                    return ("num", Fraction(0), sa), 0
                return ("num", Fraction(0), 1 if rc == X.RC_DOWN else 0), 0
            k, r, fl = X.round_value(s, 0, pc, -16382, 16383, rc, dq(pc))
            return (k, r, 1 if s < 0 else 0), fl
        sign = sa ^ sb
        if kind == "mul":
            if a[0] == "inf" or b[0] == "inf":
                if (a[0] == "num" and a[1] == 0) or (b[0] == "num" and b[1] == 0):
                    return X.INDEFINITE, X.FLAG_IE
                return ("inf", None, sign), 0
            p = a[1] * b[1]
            if p == 0:
                return ("num", Fraction(0), sign), 0
            k, r, fl = X.round_value(p, 0, pc, -16382, 16383, rc, dq(pc))
            return (k, r, sign), fl
        if kind == "div":
            if a[0] == "inf" and b[0] == "inf":
                return X.INDEFINITE, X.FLAG_IE
            if a[0] == "inf":
                return ("inf", None, sign), 0
            if b[0] == "inf":
                return ("num", Fraction(0), sign), 0
            if b[1] == 0:
                if a[1] == 0:
                    return X.INDEFINITE, X.FLAG_IE
                return ("inf", None, sign), X.FLAG_ZE
            q = a[1] / b[1]
            if q == 0:
                return ("num", Fraction(0), sign), 0
            k, r, fl = X.round_value(q, 0, pc, -16382, 16383, rc, dq(pc))
            return (k, r, sign), fl
        raise ValueError(kind)

    def sqrt(self, a, pc, rc):
        if a[0] == "nan":
            return self.nan_result(a)
        if a[0] == "inf":
            return (a, 0) if a[2] == 0 else (X.INDEFINITE, X.FLAG_IE)
        if a[1] == 0:
            return a, 0
        if a[1] < 0:
            return X.INDEFINITE, X.FLAG_IE
        k, r, fl = X.isqrt_round(a[1], pc, -16382, rc, dq(pc))
        return (k, r, 0), fl

    def load(self, tv):
        v = X.decode_typed(tv)
        fl = X.FLAG_DE if _is_denorm(tv) and not tv.startswith("x80") else 0
        if v[0] == "nan" and not v[3]:
            return ("nan", v[1] | 1 << 62, v[2], True), fl | X.FLAG_IE
        return v, fl

    def store(self, fmt, v, rc):
        """Register value -> typed memory value (fst m32/m64, fist m16/32/64)."""
        if fmt in ("f32", "f64"):
            if v[0] == "nan":
                return X.encode(fmt, v), (0 if v[3] else X.FLAG_IE)
            if v[0] == "inf" or v[1] == 0:
                return X.encode(fmt, v), 0
            p, emin, emax, _ = X.FMT[fmt]
            k, r, fl = X.round_value(v[1], 0, p, emin, emax, rc, emin - (p - 1))
            return X.encode(fmt, (k, r, v[2])), fl
        bits = {"i16": 16, "i32": 32, "i64": 64}[fmt]
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        indef = "%s:%0*x" % (fmt, bits // 4, 1 << (bits - 1))
        if v[0] != "num":
            return indef, X.FLAG_IE
        n, fl = self.to_int(v[1], rc)
        if n < lo or n > hi:
            return indef, X.FLAG_IE
        return "%s:%0*x" % (fmt, bits // 4, n & ((1 << bits) - 1)), fl

    @staticmethod
    def to_int(x, rc):
        fl_ = x.numerator // x.denominator
        frac = x - fl_
        if frac == 0:
            return fl_, 0
        if rc == X.RC_DOWN:
            n = fl_
        elif rc == X.RC_UP:
            n = fl_ + 1
        elif rc == X.RC_CHOP:
            n = fl_ if x > 0 else fl_ + 1
        else:
            n = fl_ + (1 if frac > Fraction(1, 2) or (frac == Fraction(1, 2) and fl_ % 2) else 0)
        return n, X.FLAG_PE

    def const(self, name, rc):
        if name == "fld1":
            return ("num", Fraction(1), 0), 0
        if name == "fldz":
            return ("num", Fraction(0), 0), 0
        if name not in self.consts:
            self.consts[name] = X._const(name)
        k, r, fl = X.round_value(self.consts[name], 0, 64, -16382, 16383, rc)
        return (k, r, 0), 0              # FLDxx does not report PE

    def compare(self, a, b, unordered_ok):
        if a[0] == "nan" or b[0] == "nan":
            sn = any(v[0] == "nan" and not v[3] for v in (a, b))
            return 0x4500, (X.FLAG_IE if (not unordered_ok or sn) else 0)
        va = a[1] if a[0] == "num" else (Fraction(-1) if a[2] else Fraction(1)) * Fraction(10) ** 6000
        vb = b[1] if b[0] == "num" else (Fraction(-1) if b[2] else Fraction(1)) * Fraction(10) ** 6000
        if va > vb:
            return 0, 0
        if va < vb:
            return 0x100, 0
        return 0x4000, 0

    def fxam(self, tv):
        se, m = X.x80_parts(tv)
        c1 = 0x200 if se >> 15 else 0
        e = se & 0x7fff
        if e == 0x7fff:
            return c1 | (0x500 if (m & 0x7fffffffffffffff) == 0 else 0x100)
        if e == 0:
            return c1 | (0x4000 if m == 0 else 0x4400)
        return c1 | (0x400 if m >> 63 else 0)

    def transc(self, op, a, b):
        """Correctly rounded (nearest) 64-bit reference via mpmath."""
        import mpmath
        mpmath.mp.prec = 256
        if any(v[0] != "num" for v in ([a] + ([b] if b else []))):
            return None
        fa = mpmath.mpf(a[1].numerator) / a[1].denominator
        fb = mpmath.mpf(b[1].numerator) / b[1].denominator if b else None
        if self.pi66 and op in ("fsin", "fcos", "fptan", "fsincos") and abs(a[1]) < 2 ** 63:
            # pi to 66 significant bits (2 integer + 64 fraction bits; the next bit is 0)
            P66 = Fraction(int(mpmath.floor(mpmath.pi * mpmath.mpf(2) ** 64)), 1 << 64)
            fa = fa * mpmath.pi / (mpmath.mpf(P66.numerator) / P66.denominator)
        if op in ("fsin", "fcos", "fsincos", "fptan") and abs(a[1]) >= 2 ** 63:
            return "out-of-range"             # C2 = 1, operand unchanged (SDM)
        if op == "fsin":
            res = [mpmath.sin(fa)]
        elif op == "fcos":
            res = [mpmath.cos(fa)]
        elif op == "fsincos":
            res = [mpmath.cos(fa), mpmath.sin(fa)]
        elif op == "fptan":
            res = [mpmath.mpf(1), mpmath.tan(fa)]
        elif op == "fpatan":
            res = [mpmath.atan2(fb, fa)]
        elif op == "f2xm1":
            res = [mpmath.power(2, fa) - 1]
        elif op == "fyl2x":
            if fa <= 0:
                return None
            res = [fb * mpmath.log(fa, 2)]
        elif op == "fyl2xp1":
            res = [fb * mpmath.log(1 + fa, 2)]
        else:
            return None
        out = []
        for r in res:
            if r == 0:
                out.append(X.encode("x80", ("num", Fraction(0), 0)))
                continue
            m, e = mpmath.frexp(r)
            fr = Fraction(int(mpmath.floor(abs(m) * mpmath.mpf(2) ** 200)), 1) * Fraction(2) ** (int(e) - 200)
            fr = fr + Fraction(1, 1 << 210) * Fraction(2) ** int(e)       # sticky
            k, v, _ = X.round_value(fr, 0, 64, -16382, 16383, X.RC_NEAR, DENORM_Q)
            out.append(X.encode("x80", (k, v, 1 if r < 0 else 0)))
        return out

    def run(self, v):
        op = v["op"]
        o = X.OPS[op]
        cw = int(v["cw"], 16)
        pc, rc = X.pc_rc(cw)
        ins = [X.decode_x80(v[k]) for k in "abc"[:o["regs"]]]
        flags = 0
        for k in "abc"[:o["regs"]]:
            se, _ = X.x80_parts(v[k])
            if _is_denorm(v[k]):
                flags |= X.FLAG_DE
        out, mem, cc = None, None, 0
        base = op.split("_")[0]
        AR = {"fadd": ("add", False), "fsub": ("sub", False), "fsubr": ("sub", True), "fmul": ("mul", False),
              "fdiv": ("div", False), "fdivr": ("div", True), "fiadd": ("add", False), "fisub": ("sub", False),
              "fimul": ("mul", False), "fidiv": ("div", False)}
        if op.startswith("idiom_fldpi_fmul_m64"):
            pi, _ = self.const("fldpi", rc)
            m, fl = self.load(v["m"])
            r, fl2 = self.arith("mul", pi, m, pc, rc)
            mem, fl3 = self.store("f64", r, rc)
            return {"out": [], "mem": mem, "sw": "%04x" % _sw(fl | fl2 | fl3, 0)}
        if base in AR:
            kind, rev = AR[base]
            a = ins[0]
            if o["mem"]:
                b, fl = self.load(v["m"])
                flags |= fl
            else:
                b = ins[1]
            x, y = (b, a) if rev else (a, b)
            r, fl = self.arith(kind, x, y, pc, rc)
            flags |= fl
            out = [r] + ([ins[1]] if o["depth"] == 2 else [])
        elif op == "fsqrt":
            r, fl = self.sqrt(ins[0], pc, rc)
            flags |= fl
            out = [r]
        elif o["cls"] == "load":
            r, fl = self.load(v["m"])
            flags |= fl
            out = [r]
        elif o["cls"] == "const":
            r, fl = self.const(op, rc)
            out = [r]
        elif o["cls"] == "store":
            mem, fl = self.store(o["out_mem"], ins[0], rc)
            flags |= fl
            out = []
        elif op in ("fcom", "fucom"):
            cc, fl = self.compare(ins[0], ins[1], op == "fucom")
            flags |= fl
            out = ins
        elif op in ("fcom_m32", "fcom_m64"):
            m, fl = self.load(v["m"])
            cc, fl2 = self.compare(ins[0], m, False)
            flags |= fl | fl2
            out = ins
        elif op == "ftst":
            cc, fl = self.compare(ins[0], ("num", Fraction(0), 0), False)
            flags |= fl
            out = ins
        elif op == "fxam":
            cc = self.fxam(v["a"])
            out = ins
        elif op == "fabs":
            a = ins[0]
            out = [(a[0], abs(a[1]) if a[0] == "num" else a[1], 0) + tuple(a[3:])]
        elif op == "fchs":
            a = ins[0]
            out = [(a[0], -a[1] if a[0] == "num" else a[1], 1 - a[2]) + tuple(a[3:])]
        elif op == "frndint":
            a = ins[0]
            if a[0] == "num":
                n, fl = self.to_int(a[1], rc)
                flags |= fl
                out = [("num", Fraction(n), a[2] if n == 0 else (1 if n < 0 else 0))]
            elif a[0] == "nan":
                r, fl = self.nan_result(a)
                flags |= fl
                out = [r]
            else:
                out = [a]
        elif o["cls"] == "transc":
            res = self.transc(op, ins[0], ins[1] if len(ins) > 1 else None)
            if res == "out-of-range":
                return {"out": [v["a"]], "sw": "%04x" % (0x400 | (((8 - 1) & 7) << 11)), "note": "C2"}
            if res is None:
                return {"out": None, "sw": None, "note": "no reference"}
            return {"out": res, "sw": None, "note": "cr64 reference"}
        else:
            return {"out": None, "sw": None, "note": "not modelled"}
        enc = []
        for r in out:
            enc.append(X.encode("x80", r))
        res = {"out": enc, "sw": "%04x" % _sw(flags, o["depth"], cc)}
        if mem is not None:
            res["mem"] = mem
        return res


# ------------------------------------------------------------------------------------ kit model

def _d(tv):
    """Value of a typed register input as the kit holds it: a double."""
    t = tv.split(":")[0]
    if t == "x80":                      # rdf80
        se, m = X.x80_parts(tv)
        e = se & 0x7fff
        if e == 0x7fff:
            v = math.nan if (m << 1) & 0xffffffffffffffff else math.inf
        elif e == 0 and m == 0:
            v = 0.0
        else:
            try:
                v = math.ldexp(float(m), e - 16383 - 63)
            except OverflowError:
                v = math.inf
        return -v if se & 0x8000 else v
    if t == "f32":
        return X.f32_of(tv)
    if t == "f64":
        return X.f64_of(tv)
    return float(int.from_bytes(X.to_bytes(tv), "little", signed=True))


INDEF_D = struct.unpack("<d", struct.pack("<Q", 0xfff8000000000000))[0]


def _wrf80(v):
    """The kit's wrf80: NaN -> positive 0xc000.. pattern, else exact expansion of the double."""
    if math.isnan(v):
        return X.x80(0x7fff, 0xc000000000000000)
    if math.isinf(v):
        return X.x80(0xffff if v < 0 else 0x7fff, 1 << 63)
    s = 1 if math.copysign(1.0, v) < 0 else 0
    if v == 0:
        return X.x80(s << 15, 0)
    return X.encode("x80", ("num", Fraction(abs(v)) * (-1 if s else 1), s))


def _f32(v):
    try:
        return struct.unpack("<f", struct.pack("<f", v))[0]
    except OverflowError:
        return math.copysign(math.inf, v)


class Kit:
    CONST = {"fld1": 1.0, "fldz": 0.0, "fldpi": 3.14159265358979323846, "fldln2": 0.69314718055994530942,
             "fldl2e": 1.44269504088896340736, "fldlg2": 0.30102999566398119521, "fldl2t": 3.32192809488736234787}

    @staticmethod
    def fx87(r, pc):
        if math.isnan(r):
            return INDEF_D
        return _f32(r) if pc == 24 else r

    @staticmethod
    def fround(v, rc):
        if rc == 1:
            return math.floor(v)
        if rc == 2:
            return math.ceil(v)
        if rc == 3:
            return math.trunc(v)
        lo, hi = math.floor(v), math.ceil(v)
        if v - lo < hi - v:
            return lo
        if v - lo > hi - v:
            return hi
        return lo if lo % 2 == 0 else hi

    def run(self, v):
        op = v["op"]
        o = X.OPS[op]
        cw = int(v["cw"], 16)
        pc, rc = X.pc_rc(cw)
        st = [_d(v[k]) for k in "abc"[:o["regs"]]]
        AR = {"fadd": lambda x, y: x + y, "fsub": lambda x, y: x - y, "fsubr": lambda x, y: y - x,
              "fmul": lambda x, y: x * y, "fdiv": lambda x, y: _div(x, y), "fdivr": lambda x, y: _div(y, x),
              "fiadd": lambda x, y: x + y, "fisub": lambda x, y: x - y, "fimul": lambda x, y: x * y,
              "fidiv": lambda x, y: _div(x, y)}
        base = op.split("_")[0]
        mem = None
        cc = 0
        if op == "idiom_fldpi_fmul_m64":
            r = self.fx87(self.CONST["fldpi"] * X.f64_of(v["m"]), pc)
            return {"out": [], "mem": X.f64_bits(r), "sw": None}
        if base in AR:
            y = _d(v["m"]) if o["mem"] else st[1]
            r = self.fx87(AR[base](st[0], y), pc)
            out = [r] + st[1:2]
        elif op == "fsqrt":
            r = math.sqrt(st[0]) if st[0] >= 0 or math.isnan(st[0]) else math.nan
            out = [self.fx87(r, pc)]
        elif o["cls"] == "load":
            out = [_d(v["m"])]
        elif o["cls"] == "const":
            out = [self.CONST[op]]
        elif o["cls"] == "store":
            x = st[0]
            t = o["out_mem"]
            if t == "f32":
                mem = X.f32_bits(fto_float_kit(x, rc))
            elif t == "f64":
                mem = X.f64_bits(x)
            else:
                bits = int(t[1:])
                r = self.fround(x, rc) if not (math.isnan(x) or math.isinf(x)) else None
                if r is None or not (-(2 ** (bits - 1)) <= r <= 2 ** (bits - 1) - 1):
                    n = 1 << (bits - 1)
                else:
                    n = int(r) & ((1 << bits) - 1)
                mem = "%s:%0*x" % (t, bits // 4, n)
            out = []
        elif op in ("fcom", "fucom", "fcom_m32", "fcom_m64", "ftst"):
            y = 0.0 if op == "ftst" else (_d(v["m"]) if o["mem"] else st[1])
            x = st[0]
            cc = 0x4500 if (math.isnan(x) or math.isnan(y)) else (0x100 if x < y else (0x4000 if x == y else 0))
            out = st
        elif op == "fabs":
            out = [abs(st[0])]
        elif op == "fchs":
            out = [-st[0]]
        elif op == "frndint":
            out = [self.fround(st[0], rc) if math.isfinite(st[0]) else st[0]]
        elif o["cls"] == "transc":
            x = st[0]
            y = st[1] if len(st) > 1 else None
            try:
                if op == "fsin":
                    out = [math.sin(x)]
                elif op == "fcos":
                    out = [math.cos(x)]
                elif op == "fsincos":
                    out = [math.cos(x), math.sin(x)]
                elif op == "fptan":
                    out = [1.0, math.tan(x)]
                elif op == "fpatan":
                    out = [math.atan2(y, x)]
                elif op == "f2xm1":
                    out = [math.expm1(x * math.log(2))]
                elif op == "fyl2x":
                    out = [y * math.log2(x)]
                elif op == "fyl2xp1":
                    out = [y * math.log1p(x) / math.log(2)]
                else:
                    return {"out": None, "sw": None, "note": "not modelled"}
            except (ValueError, OverflowError):
                out = [INDEF_D]
        else:
            return {"out": None, "sw": None, "note": "not modelled"}
        # fild: the kit also keeps the exact integer (st_bits) for a later FIST, but the register
        # value written out here is the rounded double, as an FSTP m80 would store it.
        res ={"out": [_wrf80(x) for x in out], "sw": "%04x" % (cc | (((8 - o["depth"]) & 7) << 11))}
        if mem is not None:
            res["mem"] = mem
        return res


def _div(x, y):
    try:
        return x / y
    except ZeroDivisionError:
        if x == 0 or math.isnan(x):
            return math.nan
        return math.copysign(math.inf, x) * math.copysign(1.0, y)


def fto_float_kit(v, rc):
    """runtime/x86.h fto_float: host nearest, then step one ulp for the directed modes."""
    f = _f32(v)
    if rc == 0 or math.isnan(v) or math.isinf(v) or f == v:
        return f
    back = f
    if rc == 1 and back > v:
        return _nextf(f, -1)
    if rc == 2 and back < v:
        return _nextf(f, 1)
    if rc == 3 and ((v > 0 and back > v) or (v < 0 and back < v)):
        return _nextf(f, 0)
    return f


def _nextf(f, toward):
    u = struct.unpack("<I", struct.pack("<f", f))[0]
    if toward == 0:
        u -= 1                            # toward zero: magnitude down
    elif (toward > 0) == (f >= 0):
        u += 1
    else:
        u -= 1
    return struct.unpack("<f", struct.pack("<I", u & 0xffffffff))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vectors")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--model", choices=["exact", "kit"], default="exact")
    ap.add_argument("--pi66", action="store_true")
    a = ap.parse_args()
    m = Exact(a.pi66) if a.model == "exact" else Kit()
    n = 0
    with open(a.vectors) as f, open(a.out, "w") as out:
        out.write(json.dumps({"meta": {"backend": "model-" + a.model, "pi66": a.pi66}}) + "\n")
        for line in f:
            if not line.strip():
                continue
            v = json.loads(line)
            if v.get("kind", "x87") != "x87":
                continue
            r = m.run(v)
            r["id"] = v["id"]
            out.write(json.dumps(r) + "\n")
            n += 1
    print("model %s: %d vectors -> %s" % (a.model, n, a.out))


if __name__ == "__main__":
    main()
