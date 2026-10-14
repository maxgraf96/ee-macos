"""Generate x87 conformance vectors (JSON Lines; format in x87ops.py).

  python -I tests/x87/gen_vectors.py --suite arith,conv,consts,compare,exact,transc,idiom \
      --n 300 --seed 1 -o build/x87/insn.jsonl
  python -I tests/x87/gen_vectors.py --suite um --n 2000 --seed 1 -o build/x87/um.jsonl

Instruction suites (kind "x87", for x87_runner.py and model_run.py):
  arith    + - * / sqrt, register and m32/m64/m32int forms, under six control words
           (PC 53/24/64 at round-to-nearest; PC 53 at chop/down/up). Operand classes:
           rand53 (doubles), rand24 (floats), rand64 (64-bit significands), game (float values
           shaped like EE positions/angles/directions), tie53/tie24 (exact and near rounding
           midpoints), range (results outside double range or in its subnormal range), special.
  conv     fstp m32/m64 and fistp m16/m32/m64 from double/extended values (ties, limits, NaN),
           fld m32/m64 of arbitrary bit patterns (SNaN, denormals), fild m64 beyond 2^53.
  consts   FLDPI/L2E/L2T/LG2/LN2/1/Z under all four rounding modes.
  compare  fcom/fucom/ftst/fxam/fcom m32/m64 incl. NaN, +-0, equal values.
  exact    fabs fchs frndint fxtract fscale fprem fprem1.
  transc   fsin fcos fsincos fptan fpatan f2xm1 fyl2x fyl2xp1 over game ranges, near
           multiples of pi/2, and large arguments.
  idiom    FLDPI; FMUL m64; FSTP m64 (degrees -> radians shape).
Game-function suite (kind "call", for windows/frida/ummath_vectors.js and um_unicorn.py):
  um       UMArcTan2/UMArcCos/UMArcTan/UMCos/UMTan/UMQuadraticEquation/UMFastMath::Sqrt,
           msvcrt sqrt (+ characterisation of pow/atan2/atan/acos/sin/cos/log/exp/floor/ceil/
           fmod/_CIpow/_CIsqrt/_ftol/atof/strtod/sscanf "%f"), the EXE's truncating ftol
           helper; distributions follow the game's call sites (see ADR-002).
"""
import argparse
import json
import math
import os
import random
import struct
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402

CWS_ARITH = ["027f", "007f", "037f", "0e7f", "067f", "0a7f"]
CWS_CONV = ["027f", "007f", "037f", "0e7f", "067f", "0a7f"]


def f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def ext_of_double(d):
    return X.encode("x80", ("num", Fraction(d), 1 if math.copysign(1, d) < 0 else 0)) if math.isfinite(d) \
        else (X.x80(0xffff if d < 0 else 0x7fff, 1 << 63) if math.isinf(d) else X.x80(0xffff, 0xc000000000000000))


def ext(sign, e, m):
    """x80 from sign, unbiased exponent, 64-bit significand with the integer bit."""
    return X.x80((sign << 15) | (e + 16383), m)


class Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.n = 0

    def vid(self, prefix):
        self.n += 1
        return "%s-%06d" % (prefix, self.n)

    # ---- value classes (all return x80 strings)
    def rand53(self, emin=-60, emax=60):
        r = self.r
        return ext(r.getrandbits(1), r.randint(emin, emax), (1 << 63) | (r.getrandbits(52) << 11))

    def rand24(self, emin=-30, emax=30):
        r = self.r
        return ext(r.getrandbits(1), r.randint(emin, emax), (1 << 63) | (r.getrandbits(23) << 40))

    def rand64(self, emin=-60, emax=60):
        r = self.r
        return ext(r.getrandbits(1), r.randint(emin, emax), (1 << 63) | r.getrandbits(63))

    def game(self):
        """Float values shaped like the game's: coordinates, deltas, angles, unit components."""
        r = self.r
        k = r.randrange(5)
        if k == 0:
            v = r.uniform(0, 2048)
        elif k == 1:
            v = r.uniform(-300, 300)
        elif k == 2:
            v = r.uniform(-2 * math.pi, 2 * math.pi)
        elif k == 3:
            v = r.uniform(-1, 1)
        else:
            v = r.uniform(0, 64) * r.choice([1, 1e-3, 1e-6])
        return ext_of_double(f32(v))

    def specials(self):
        return [
            X.x80(0, 0), X.x80(0x8000, 0), ext(0, 0, 1 << 63), ext(1, 0, 1 << 63), ext(0, -1, 1 << 63),
            ext(0, 0, 3 << 62), ext(0, 1, 5 << 61),                         # 0.5 1.5 2.5
            X.x80(0x7fff, 1 << 63), X.x80(0xffff, 1 << 63),                  # +-inf
            X.x80(0x7fff, 0xc000000000000000), X.x80(0xffff, 0xc000000000000000),   # QNaN, indefinite
            X.x80(0x7fff, 0xa000000000000000), X.x80(0x7fff, 0xc000000000001234),   # SNaN, QNaN payload
            X.x80(0x0000, 0x0000000000000001), X.x80(0x0001, 1 << 63),               # ext denormal, min normal
            ext(0, 1023, 0xfffffffffffff800), ext(0, 1024, 3 << 62),                 # DBL_MAX, 1.5*2^1024
            ext(0, -1022, 1 << 63), ext(0, -1030, 1 << 63),                          # DBL_MIN, subnormal range
            ext(0, 127, 0xffffff0000000000), ext(0, -149, 1 << 63),                  # FLT_MAX, FLT_TRUE_MIN
            ext(0, 53, (1 << 63) | (1 << 10)), ext(0, 63, 0xffffffffffffffff),        # 2^53+... , 2^64-1
        ]

    def tie_pair(self, p):
        """(a, b) with a+b exactly at, or 2^-20 ulp beside, a p-bit rounding midpoint."""
        r = self.r
        e = r.randint(-20, 20)
        keep = 63 - (p - 1)
        a_m = (1 << 63) | (r.getrandbits(p - 1) << keep)
        a = ext(0, e, a_m)
        half = Fraction(1) * Fraction(2) ** (e - p)          # half an ulp of a at p bits
        off = r.choice([0, 0, 1, -1]) * half / (1 << 20)
        bv = half + off
        b = X.encode("x80", X.round_value(bv, 0, 64, -16382, 16383, 0)[:2] + (0,))
        return a, b

    def range_pair(self, op):
        r = self.r
        if op in ("fmul", "fdiv"):
            big = r.choice([True, False])
            if big:
                return ext(0, r.randint(520, 700), (1 << 63) | (r.getrandbits(52) << 11)), \
                    ext(r.getrandbits(1), r.randint(330, 600) * (1 if op == "fmul" else -1), (1 << 63) | (r.getrandbits(52) << 11))
            return ext(0, r.randint(-700, -520), (1 << 63) | (r.getrandbits(52) << 11)), \
                ext(0, r.randint(-600, -330) * (1 if op == "fmul" else -1), (1 << 63) | (r.getrandbits(52) << 11))
        e = r.choice([1023, -1022, -1030])
        return ext(0, e, (1 << 63) | (r.getrandbits(52) << 11)), ext(1, e - r.randint(0, 3), (1 << 63) | (r.getrandbits(52) << 11))

    # ---- suites
    def arith(self, n):
        out = []
        reg_ops = ["fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr"]
        for cw in CWS_ARITH:
            for op in reg_ops:
                for cls in ("rand53", "rand24", "rand64", "game", "tie53", "tie24", "range"):
                    for _ in range(n if cls not in ("range",) else max(1, n // 4)):
                        if cls == "tie53" or cls == "tie24":
                            if op not in ("fadd", "fsub"):
                                continue
                            a, b = self.tie_pair(53 if cls == "tie53" else 24)
                            if op == "fsub":
                                se, m = X.x80_parts(b)
                                b = X.x80(se ^ 0x8000, m)
                        elif cls == "range":
                            a, b = self.range_pair(op if op in ("fmul", "fdiv") else "fadd")
                        else:
                            g = getattr(self, cls)
                            a, b = g(), g()
                        out.append(dict(id=self.vid("ar"), kind="x87", op=op, cw=cw, a=a, b=b, tag="arith/" + cls))
                sp = self.specials()
                for a in sp:
                    for b in self.r.sample(sp, 6):
                        out.append(dict(id=self.vid("ar"), kind="x87", op=op, cw=cw, a=a, b=b, tag="arith/special"))
            for cls in ("rand53", "rand24", "rand64", "game"):
                for _ in range(n):
                    a = getattr(self, cls)()
                    se, m = X.x80_parts(a)
                    out.append(dict(id=self.vid("ar"), kind="x87", op="fsqrt", cw=cw, a=X.x80(se & 0x7fff, m),
                                    tag="arith/" + cls))
            for a in self.specials():
                out.append(dict(id=self.vid("ar"), kind="x87", op="fsqrt", cw=cw, a=a, tag="arith/special"))
            for op in ("fadd_m32", "fmul_m32", "fsub_m32", "fsubr_m32", "fdiv_m32", "fdivr_m32",
                       "fadd_m64", "fmul_m64", "fsub_m64", "fdivr_m64", "fdiv_m64"):
                t = X.OPS[op]["mem"]
                for cls in ("rand53", "game", "rand64"):
                    for _ in range(max(1, n // 2)):
                        a = getattr(self, cls)()
                        mv = self.r.uniform(-1000, 1000) * self.r.choice([1, 1e-5, 1e5])
                        m = X.f32_bits(mv) if t == "f32" else X.f64_bits(mv)
                        out.append(dict(id=self.vid("ar"), kind="x87", op=op, cw=cw, a=a, m=m, tag="arith/mem-" + cls))
            for op in ("fiadd_m32", "fimul_m32", "fidiv_m32", "fisub_m32"):
                for _ in range(max(1, n // 2)):
                    a = self.game()
                    m = "i32:%08x" % (self.r.randint(-100000, 100000) & 0xffffffff)
                    out.append(dict(id=self.vid("ar"), kind="x87", op=op, cw=cw, a=a, m=m, tag="arith/mem-int"))
        return out

    def conv(self, n):
        out = []
        r = self.r
        for cw in CWS_CONV:
            vals = []
            for _ in range(n):
                vals.append(("rand53", self.rand53(-40, 40)))
                vals.append(("rand64", self.rand64(-40, 40)))
                vals.append(("game", self.game()))
                # exact float midpoint held in a double / extended register
                fm = (1 << 63) | (r.getrandbits(23) << 40) | (1 << 39)
                vals.append(("tie24", ext(r.getrandbits(1), r.randint(-20, 20), fm)))
                # exact double midpoint in an extended register
                dm = (1 << 63) | (r.getrandbits(52) << 11) | (1 << 10)
                vals.append(("tie53", ext(r.getrandbits(1), r.randint(-20, 20), dm)))
                # integer + .5, near int32 limits
                k = r.randint(-100000, 100000)
                vals.append(("int-tie", ext_of_double(k + 0.5)))
                vals.append(("int-limit", ext_of_double(r.choice([2147483647.5, -2147483648.5, 2147483647.4,
                                                                   -2147483648.4, 32767.5, -32768.5, 2.0 ** 63]))))
            for s in self.specials():
                vals.append(("special", s))
            for cls, a in vals:
                for op in ("fstp_m32", "fstp_m64", "fistp_m16", "fistp_m32", "fistp_m64"):
                    if op.startswith("fistp") and cls in ("rand53", "rand64") and r.random() < 0.7:
                        continue
                    out.append(dict(id=self.vid("cv"), kind="x87", op=op, cw=cw, a=a, tag="conv/" + cls))
        for _ in range(n * 4):
            out.append(dict(id=self.vid("cv"), kind="x87", op="fld_m32", cw="027f",
                            m="f32:%08x" % r.getrandbits(32), tag="conv/bits"))
            out.append(dict(id=self.vid("cv"), kind="x87", op="fld_m64", cw="027f",
                            m="f64:%016x" % r.getrandbits(64), tag="conv/bits"))
            v = r.getrandbits(r.randint(1, 63)) * r.choice([1, -1])
            out.append(dict(id=self.vid("cv"), kind="x87", op="fild_m64", cw=r.choice(["027f", "037f"]),
                            m="i64:%016x" % (v & 0xffffffffffffffff), tag="conv/i64"))
        for bits in ("7fa00000", "ffa00001", "7f800001", "00000001", "807fffff"):
            out.append(dict(id=self.vid("cv"), kind="x87", op="fld_m32", cw="027f", m="f32:" + bits, tag="conv/special"))
        return out

    def consts(self, n):
        out = []
        for op in ("fldpi", "fldl2e", "fldl2t", "fldlg2", "fldln2", "fld1", "fldz"):
            for cw in ("027f", "067f", "0a7f", "0e7f", "037f", "007f"):
                out.append(dict(id=self.vid("ct"), kind="x87", op=op, cw=cw, tag="consts"))
        return out

    def compare(self, n):
        out = []
        sp = self.specials()
        for op in ("fcom", "fucom"):
            for a in sp:
                for b in sp:
                    out.append(dict(id=self.vid("cm"), kind="x87", op=op, cw="027f", a=a, b=b, tag="compare/special"))
            for _ in range(n):
                a = self.game()
                out.append(dict(id=self.vid("cm"), kind="x87", op=op, cw="027f", a=a, b=self.r.choice([a, self.game()]),
                                tag="compare/game"))
        for a in sp + [self.game() for _ in range(n)]:
            out.append(dict(id=self.vid("cm"), kind="x87", op="ftst", cw="027f", a=a, tag="compare"))
            out.append(dict(id=self.vid("cm"), kind="x87", op="fxam", cw="027f", a=a, tag="compare"))
            out.append(dict(id=self.vid("cm"), kind="x87", op="fcom_m32", cw="027f", a=a,
                            m=X.f32_bits(self.r.uniform(-10, 10)), tag="compare"))
        return out

    def exact(self, n):
        out = []
        sp = self.specials()
        for cw in ("027f", "037f", "0e7f", "067f", "0a7f"):
            for a in sp + [self.rand64(-5, 70) for _ in range(n)] + [ext_of_double(k + 0.5) for k in range(-4, 5)]:
                for op in ("fabs", "fchs", "frndint", "fxtract"):
                    out.append(dict(id=self.vid("ex"), kind="x87", op=op, cw=cw, a=a, tag="exact"))
        for _ in range(n):
            out.append(dict(id=self.vid("ex"), kind="x87", op="fscale", cw="027f", a=self.rand53(),
                            b=ext_of_double(float(self.r.randint(-80, 80))), tag="exact"))
            for op in ("fprem", "fprem1"):
                a = self.rand64(-10, 90 if self.r.random() < 0.2 else 30)
                b = self.rand53(-10, 10)
                se, m = X.x80_parts(b)
                out.append(dict(id=self.vid("ex"), kind="x87", op=op, cw="027f", a=a, b=X.x80(se & 0x7fff, m), tag="exact"))
        return out

    def transc(self, n):
        out = []
        r = self.r
        xs = []
        for _ in range(n):
            xs.append(("game", self.game()))
            xs.append(("angle53", ext_of_double(r.uniform(-2 * math.pi, 2 * math.pi))))
            k = r.randint(-64, 64)
            xs.append(("near-k-pi/2", ext_of_double(k * math.pi / 2 + r.choice([0, 1e-12, -1e-9, 1e-15]))))
            xs.append(("large", ext_of_double(r.uniform(1, 2) * 2.0 ** r.randint(10, 62) * r.choice([1, -1]))))
            xs.append(("tiny", ext_of_double(r.uniform(-1, 1) * 2.0 ** r.randint(-60, -20))))
            xs.append(("rand64", self.rand64(-8, 3)))
        xs.append(("out-of-range", ext(0, 63, 1 << 63)))
        xs.append(("out-of-range", ext(1, 70, 0xc000000000000000)))
        for cw in ("027f", "037f"):
            for cls, x in xs:
                for op in ("fsin", "fcos", "fsincos", "fptan"):
                    out.append(dict(id=self.vid("tr"), kind="x87", op=op, cw=cw, a=x, tag="transc/" + cls))
            for _ in range(n):
                # fpatan(y = b, x = a): direction vectors of game units, float operands
                ax, ay = self.game(), self.game()
                out.append(dict(id=self.vid("tr"), kind="x87", op="fpatan", cw=cw, a=ax, b=ay, tag="transc/game"))
                out.append(dict(id=self.vid("tr"), kind="x87", op="fpatan", cw=cw, a=self.rand64(-4, 4),
                                b=self.rand64(-4, 4), tag="transc/rand64"))
                out.append(dict(id=self.vid("tr"), kind="x87", op="f2xm1", cw=cw,
                                a=ext_of_double(r.uniform(-1, 1)), tag="transc/unit"))
                xpos = ext_of_double(r.uniform(0, 4) * 2.0 ** r.randint(-10, 10))
                out.append(dict(id=self.vid("tr"), kind="x87", op="fyl2x", cw=cw, a=xpos,
                                b=r.choice([ext(0, 0, 1 << 63), self.rand53(-3, 3)]), tag="transc/pos"))
                out.append(dict(id=self.vid("tr"), kind="x87", op="fyl2xp1", cw=cw,
                                a=ext_of_double(r.uniform(-0.29, 0.29)), b=ext(0, 0, 1 << 63), tag="transc/small"))
        return out

    def idiom(self, n):
        out = []
        for _ in range(n * 4):
            x = self.r.uniform(-720, 720)
            if self.r.random() < 0.5:
                x = f32(x)
            out.append(dict(id=self.vid("id"), kind="x87", op="idiom_fldpi_fmul_m64", cw="027f",
                            m=X.f64_bits(x), tag="idiom/deg2rad"))
        return out

    # ---- game-function vectors
    def um(self, n):
        r = self.r
        out = []
        F = lambda v: X.f32_bits(f32(v))          # noqa: E731
        D = X.f64_bits

        def add(name, args, tag, cw="027f"):
            out.append(X.call_vector(self.vid("um"), name, args, cw=cw, tag=tag))

        for cw in ("027f", "007f"):
            for _ in range(n):
                dx, dy = r.uniform(-300, 300), r.uniform(-300, 300)
                if r.random() < 0.2:
                    dx, dy = dx * 1e-4, dy * 1e-4
                add("UMArcTan2", [F(dy), F(dx)], "um/atan2-game", cw)
                add("UMArcCos", [F(r.uniform(-1, 1))], "um/acos-uniform", cw)
                add("UMArcCos", [F(r.uniform(0.999, 1.0) * r.choice([1, -1]))], "um/acos-near1", cw)
                add("UMArcTan", [F(r.uniform(-1, 1) * r.choice([1, 1, 50, 1e4]))], "um/atan", cw)
                add("UMCos", [F(r.uniform(-4 * math.pi, 4 * math.pi))], "um/cos-angle", cw)
                add("UMTan", [F(r.uniform(-math.pi, math.pi))], "um/tan-angle", cw)
                add("UMFastMath::Sqrt", [F(r.uniform(0, 1e6) * r.choice([1, 1e-6]))], "um/fastsqrt", cw)
                a, b, c = r.uniform(-10, 10), r.uniform(-100, 100), r.uniform(-100, 100)
                add("UMQuadraticEquation", [F(a), F(b), F(c), "hex:" + "00" * 4, "hex:" + "00" * 4], "um/quad", cw)
                add("sqrt", [D(r.uniform(0, 1e6) * r.choice([1, 1e-9, 1e9]))], "crt/sqrt", cw)
            # table-index rounding ties: UMArcCos index = fistp(float(x * 32768)), UMCos index =
            # fistp(float(|x| * 32768/(2pi as float)))
            for _ in range(max(1, n // 4)):
                k = r.randrange(0, 32750)
                x = f32((k + 0.5) / 32768)
                for d in (-1, 0, 1):
                    u = struct.unpack("<I", struct.pack("<f", x))[0] + d
                    add("UMArcCos", ["f32:%08x" % u], "um/acos-tie", cw)
                k = r.randrange(0, 16384)
                s = f32(32768 / (2 * f32(math.pi)))
                x = f32((k + 0.5) / s)
                for d in (-2, -1, 0, 1, 2):
                    u = struct.unpack("<I", struct.pack("<f", x))[0] + d
                    add("UMCos", ["f32:%08x" % u], "um/cos-tie", cw)
            for x in (0.0, -0.0, 1.0, -1.0, 1.0000001, -1.0000001, 0.9995, 0.99949997, 0.5, -0.5, 1e-30,
                      float("inf"), float("-inf"), float("nan")):
                add("UMArcCos", [F(x)], "um/acos-special", cw)
                add("UMArcTan", [F(x)], "um/atan-special", cw)
                add("UMFastMath::Sqrt", [F(x)], "um/fastsqrt-special", cw)
            for x in (0.0, -0.0, math.pi, -math.pi, 2 * math.pi, 1000.0, -1000.0, 50000.0):
                add("UMCos", [F(x)], "um/cos-special", cw)
                add("UMTan", [F(x)], "um/tan-special", cw)
            for y, x in ((0, 0), (0, 1), (0, -1), (1, 0), (-1, 0), (-0.0, -1), (1e-45, 1e-45), (3e38, 3e38),
                         (1, float("nan")), (float("inf"), 1)):
                add("UMArcTan2", [F(y), F(x)], "um/atan2-special", cw)
            for x in (-1.0, -0.0, 0.0, float("inf"), float("nan"), 5e-324, 2.0, 1e308):
                add("sqrt", [D(x)], "crt/sqrt-special", cw)
            # characterisation of the other CRT entry points the modules import
            for _ in range(max(1, n // 10)):
                add("pow", [D(r.uniform(0, 1)), D(r.choice([0.5, 1.5, 2.2, 1 / 2.2, r.uniform(-3, 3)]))], "crt/pow", cw)
                add("_CIpow", [D(r.uniform(0, 1)), D(r.choice([0.45, 2.2, r.uniform(0.1, 3)]))], "crt/cipow", cw)
                add("atan2", [D(r.uniform(-300, 300)), D(r.uniform(-300, 300))], "crt/atan2", cw)
                add("atan", [D(r.uniform(-10, 10))], "crt/atan", cw)
                add("acos", [D(r.uniform(-1, 1))], "crt/acos", cw)
                add("sin", [D(r.uniform(-10, 10))], "crt/sin", cw)
                add("cos", [D(r.uniform(-10, 10))], "crt/cos", cw)
                add("_CIsin", [D(r.uniform(-10, 10))], "crt/cisin", cw)
                add("log", [D(r.uniform(0, 100))], "crt/log", cw)
                add("exp", [D(r.uniform(-20, 20))], "crt/exp", cw)
                add("floor", [D(r.uniform(-1e6, 1e6))], "crt/floor", cw)
                add("ceil", [D(r.uniform(-1e6, 1e6))], "crt/ceil", cw)
                add("fmod", [D(r.uniform(-1e3, 1e3)), D(r.uniform(0.1, 10))], "crt/fmod", cw)
                add("_CIsqrt", [D(r.uniform(0, 1e6))], "crt/cisqrt", cw)
                v = r.uniform(-1e9, 1e9) if r.random() < 0.7 else r.randint(-1000, 1000) + 0.5
                add("_ftol", [D(v)], "crt/ftol", cw)
                add("exe_ftol_trunc", [D(v)], "exe/ftol-trunc", cw)
        for v in (0.5, -0.5, 1.5, -1.5, 2.5, 2147483647.0, 2147483648.0, -2147483649.0, 1e19, -1e19,
                  float("nan"), float("inf"), 4503599627370497.0, 0.9999999999999999):
            add("_ftol", [D(v)], "crt/ftol-special")
            add("exe_ftol_trunc", [D(v)], "exe/ftol-special")
        # decimal parsing (sscanf "%f" in the EXE, atof)
        strs = ["0.1", "1.5", "3.14159", "0.333333", "-2.75", "1e-45", "123456789.123456789", "  12", "-0",
                "1e400", "1e-400", "0.30000000000000004", "8.589973e9", "1.17549435e-38"]
        for _ in range(n // 4):
            digits = "".join(r.choice("0123456789") for _ in range(r.randint(1, 18)))
            point = r.randint(0, len(digits))
            strs.append(("-" if r.random() < 0.2 else "") + digits[:point] + "." + digits[point:]
                        + (("e%d" % r.randint(-40, 40)) if r.random() < 0.3 else ""))
            # decimal expansion of a float midpoint (hard rounding case for float parsing)
            u = r.getrandbits(31) & 0x7effffff
            lo = X.decode_typed("f32:%08x" % u)[1]
            hi = X.decode_typed("f32:%08x" % (u + 1))[1]
            mid = (lo + hi) / 2
            strs.append(_exact_decimal(mid, r.choice([0, 1, 2])))
        for s in strs:
            add("atof", ["str:" + s], "crt/atof")
            add("sscanf_f", ["str:" + s, "str:%f", "hex:00000000"], "crt/sscanf-f")
        return out


def _exact_decimal(fr, tweak):
    """Decimal string of a Fraction with a power-of-two denominator (exact), optionally nudged."""
    n, d = fr.numerator, fr.denominator
    k = d.bit_length() - 1
    digits = n * 5 ** k
    s = str(digits).rjust(k + 1, "0")
    s = s[:-k] + "." + s[-k:] if k else s
    if tweak == 1:                      # just above the midpoint
        s += "1"
    elif tweak == 2 and s[-1] == "5":   # just below it (a binary midpoint always ends in 5)
        s = s[:-1] + "49999"
    return s


SUITES = ["arith", "conv", "consts", "compare", "exact", "transc", "idiom", "um"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="arith,conv,consts,compare,exact,transc,idiom")
    ap.add_argument("--n", type=int, default=200, help="samples per bucket")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    g = Gen(a.seed)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    total = 0
    with open(a.out, "w") as f:
        for s in a.suite.split(","):
            if s not in SUITES:
                raise SystemExit("unknown suite %s (have %s)" % (s, ", ".join(SUITES)))
            vs = getattr(g, s)(a.n)
            for v in vs:
                f.write(json.dumps(v) + "\n")
            total += len(vs)
            print("%-8s %7d vectors" % (s, len(vs)))
    print("total %d -> %s" % (total, a.out))


if __name__ == "__main__":
    main()
