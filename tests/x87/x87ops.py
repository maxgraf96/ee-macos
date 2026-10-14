"""Shared definitions for the x87 conformance suite (tests/x87/).

Used by the generator (gen_vectors.py), the instruction runner (x87_runner.py: real x86 on
legion, or Unicorn on the Mac), the Mac-side models (model_run.py) and compare.py. Contains no
game bytes.

Typed values (strings, hex digits are the big-endian value of the bit pattern)
---------------------------------------------------------------------------
  x80:SSSSMMMMMMMMMMMMMMMM   80-bit extended: 16-bit sign+exponent, then the 64-bit significand
                             including the explicit integer bit. 1.0 = x80:3fff8000000000000000
  f32:XXXXXXXX  f64:XXXXXXXXXXXXXXXX   IEEE single / double bit patterns
  i16:XXXX  i32:XXXXXXXX  i64:XXXXXXXXXXXXXXXX   two's-complement integers
  str:<text>                 a C string (game-function vectors only)
  hex:<bytes>                raw bytes in memory order (object buffers, out-parameters)

Instruction vectors (kind "x87"), one JSON object per line
---------------------------------------------------------
  {"id": "arith-000001", "kind": "x87", "op": "fmul", "cw": "027f",
   "a": "x80:...", "b": "x80:...", "m": "f64:...", "tag": "arith/random53"}
  op   key of OPS below. a, b, c are the register inputs: a is ST(0), b ST(1), c ST(2).
  m    the memory input for *_m32/*_m64/fild*/fld_m* ops.
  cw   x87 control word (hex). Exceptions must stay masked: cw & 0x3f == 0x3f.

Instruction results, one per line (first line may be {"meta": {...}}):
  {"id": ..., "out": ["x80:...", ...], "mem": "f32:...", "sw": "3800", "sw_end": "..."}
  out  the register stack after the op, ST(0) first (the runner stores every live register).
  mem  the memory output of store ops (fst*/fist*), typed.
  sw   the status word right after the op (C0..C3, exception flags, TOP).

Game-function vectors (kind "call", for windows/frida/ummath_vectors.js and um_unicorn.py)
-------------------------------------------------------------------------------------------
  {"id": "um-000001", "kind": "call", "fn": "UMArcTan2", "cw": "027f",
   "args": ["f32:3f800000", "f32:bf000000"], "tag": "um/atan2-game"}
  fn   a known name (see KNOWN_CALLS) or "<module>+0x<rva>" with an explicit "sig".
  sig  "<conv>:<ret>:<args>"; conv c=cdecl s=stdcall t=thiscall i=x87-stack (msvcrt _CI*,
       _ftol); ret f=ST(0) v=void i=EAX l=EDX:EAX b=AL; args f=float d=double i=int32
       s=string p=out-buffer(16 bytes) P=in/out buffer (initial bytes from "args").
  this for thiscall: "hex:<object bytes>".
Results: {"id", "st0": "x80:..."|null, "eax": "i32:..", "edx": "i32:..", "bufs": ["hex:.."],
          "this": "hex:..", "cw_after": "027f", "sw_after": "...", "top_delta": 0}
"""
from fractions import Fraction
import math
import struct

# ---------------------------------------------------------------------------------------------
# Block layout shared by the stub and the runners: every operand is addressed as [rcx+disp8]
# (ECX in 32-bit mode), so the same op bytes run on 32-bit Unicorn and on 64-bit hosts.
OFF_CW, OFF_SAVED, OFF_SW, OFF_SW_END, OFF_MEM_IN = 0x00, 0x02, 0x04, 0x06, 0x08
OFF_IN = (0x10, 0x20, 0x30)
OFF_OUT = (0x40, 0x50, 0x60)
OFF_MEM_OUT = 0x70
BLOCK_SIZE = 0x80

# Op table. regs: register inputs (a=ST0, b=ST1, c=ST2); mem: memory input type; code: op bytes
# (memory operands at [rcx+8] in, [rcx+0x70] out); depth: registers live after the op;
# out_mem: type of the memory output; cls: class for reports.
def _op(code, regs=0, depth=1, mem=None, out_mem=None, cls="arith"):
    return dict(code=bytes.fromhex(code), regs=regs, depth=depth, mem=mem, out_mem=out_mem, cls=cls)


OPS = {
    # register arithmetic: ST0 = a op b (D8 C1.. = op st(0), st(1)); b stays in ST1
    "fadd": _op("d8c1", 2, 2), "fsub": _op("d8e1", 2, 2), "fsubr": _op("d8e9", 2, 2),
    "fmul": _op("d8c9", 2, 2), "fdiv": _op("d8f1", 2, 2), "fdivr": _op("d8f9", 2, 2),
    "fsqrt": _op("d9fa", 1, 1),
    # memory-operand arithmetic: ST0 = a op m
    "fadd_m32": _op("d84108", 1, 1, "f32"), "fsub_m32": _op("d86108", 1, 1, "f32"),
    "fsubr_m32": _op("d86908", 1, 1, "f32"), "fmul_m32": _op("d84908", 1, 1, "f32"),
    "fdiv_m32": _op("d87108", 1, 1, "f32"), "fdivr_m32": _op("d87908", 1, 1, "f32"),
    "fadd_m64": _op("dc4108", 1, 1, "f64"), "fsub_m64": _op("dc6108", 1, 1, "f64"),
    "fsubr_m64": _op("dc6908", 1, 1, "f64"), "fmul_m64": _op("dc4908", 1, 1, "f64"),
    "fdiv_m64": _op("dc7108", 1, 1, "f64"), "fdivr_m64": _op("dc7908", 1, 1, "f64"),
    "fiadd_m32": _op("da4108", 1, 1, "i32"), "fimul_m32": _op("da4908", 1, 1, "i32"),
    "fidiv_m32": _op("da7108", 1, 1, "i32"), "fisub_m32": _op("da6108", 1, 1, "i32"),
    # loads
    "fld_m32": _op("d94108", 0, 1, "f32", cls="load"), "fld_m64": _op("dd4108", 0, 1, "f64", cls="load"),
    "fild_m16": _op("df4108", 0, 1, "i16", cls="load"), "fild_m32": _op("db4108", 0, 1, "i32", cls="load"),
    "fild_m64": _op("df6908", 0, 1, "i64", cls="load"),
    # constants (rounded per RC)
    "fld1": _op("d9e8", cls="const"), "fldl2t": _op("d9e9", cls="const"), "fldl2e": _op("d9ea", cls="const"),
    "fldpi": _op("d9eb", cls="const"), "fldlg2": _op("d9ec", cls="const"), "fldln2": _op("d9ed", cls="const"),
    "fldz": _op("d9ee", cls="const"),
    # stores (pop a into memory, rounded per RC; integer stores give the indefinite on overflow)
    "fstp_m32": _op("d95970", 1, 0, out_mem="f32", cls="store"),
    "fstp_m64": _op("dd5970", 1, 0, out_mem="f64", cls="store"),
    "fistp_m16": _op("df5970", 1, 0, out_mem="i16", cls="store"),
    "fistp_m32": _op("db5970", 1, 0, out_mem="i32", cls="store"),
    "fistp_m64": _op("df7970", 1, 0, out_mem="i64", cls="store"),
    # compares (results in C0/C2/C3 of sw)
    "fcom": _op("d8d1", 2, 2, cls="compare"), "fucom": _op("dde1", 2, 2, cls="compare"),
    "ftst": _op("d9e4", 1, 1, cls="compare"), "fxam": _op("d9e5", 1, 1, cls="compare"),
    "fcom_m32": _op("d85108", 1, 1, "f32", cls="compare"), "fcom_m64": _op("dc5108", 1, 1, "f64", cls="compare"),
    # exact / non-PC unary
    "fabs": _op("d9e1", 1, 1, cls="exact"), "fchs": _op("d9e0", 1, 1, cls="exact"),
    "frndint": _op("d9fc", 1, 1, cls="exact"), "fxtract": _op("d9f4", 1, 2, cls="exact"),
    "fscale": _op("d9fd", 2, 2, cls="exact"),
    "fprem": _op("d9f8", 2, 2, cls="exact"), "fprem1": _op("d9f5", 2, 2, cls="exact"),
    # transcendentals (not affected by PC)
    "fsin": _op("d9fe", 1, 1, cls="transc"), "fcos": _op("d9ff", 1, 1, cls="transc"),
    "fsincos": _op("d9fb", 1, 2, cls="transc"), "fptan": _op("d9f2", 1, 2, cls="transc"),
    "fpatan": _op("d9f3", 2, 1, cls="transc"),       # ST0=x (a), ST1=y (b): atan2(y, x)
    "f2xm1": _op("d9f0", 1, 1, cls="transc"),
    "fyl2x": _op("d9f1", 2, 1, cls="transc"),        # ST0=x (a), ST1=y (b): y*log2(x)
    "fyl2xp1": _op("d9f9", 2, 1, cls="transc"),      # y*log2(x+1)
    # game idiom: FLDPI; FMUL m64; FSTP m64 (ADR-001's 26% case, kept as one vector)
    "idiom_fldpi_fmul_m64": _op("d9ebdc4908dd5970", 0, 0, "f64", out_mem="f64", cls="idiom"),
}


def build_stub(op, sysv=False):
    """Machine code for one vector: set CW, load inputs, run the op, store everything, restore.
    The block pointer is in RCX (Win64) / ECX (32-bit); `sysv` prefixes mov rcx, rdi."""
    o = OPS[op]
    b = bytearray()
    if sysv:
        b += bytes.fromhex("4889f9")                       # mov rcx, rdi
    b += bytes([0xD9, 0x79, OFF_SAVED])                    # fnstcw [rcx+2]
    b += bytes([0xD9, 0x69, OFF_CW])                       # fldcw  [rcx+0]
    b += bytes.fromhex("dbe2")                             # fnclex
    for i in reversed(range(o["regs"])):
        b += bytes([0xDB, 0x69, OFF_IN[i]])                # fld tbyte [rcx+in_i]  (c, b, a)
    b += o["code"]
    b += bytes([0xDD, 0x79, OFF_SW])                       # fnstsw [rcx+4]
    for k in range(o["depth"]):
        b += bytes([0xDB, 0x79, OFF_OUT[k]])               # fstp tbyte [rcx+out_k]
    b += bytes([0xDD, 0x79, OFF_SW_END])                   # fnstsw [rcx+6]
    b += bytes.fromhex("dbe2")                             # fnclex
    b += bytes([0xD9, 0x69, OFF_SAVED])                    # fldcw [rcx+2]
    b += b"\xc3"
    return bytes(b)


# ---------------------------------------------------------------------------------------------
# Typed values

TYPE_BYTES = {"f32": 4, "f64": 8, "i16": 2, "i32": 4, "i64": 8, "x80": 10}


def to_bytes(tv):
    """Typed value string -> little-endian memory bytes."""
    t, _, h = tv.partition(":")
    if t == "x80":
        se, m = int(h[:4], 16), int(h[4:], 16)
        return struct.pack("<QH", m, se)
    if t == "hex":
        return bytes.fromhex(h)
    n = TYPE_BYTES[t]
    return int(h, 16).to_bytes(n, "little")


def from_bytes(t, b):
    if t == "x80":
        m, se = struct.unpack("<QH", bytes(b[:10]))
        return "x80:%04x%016x" % (se, m)
    if t == "hex":
        return "hex:" + bytes(b).hex()
    n = TYPE_BYTES[t]
    return "%s:%0*x" % (t, 2 * n, int.from_bytes(bytes(b[:n]), "little"))


def x80(se, m):
    return "x80:%04x%016x" % (se & 0xffff, m & 0xffffffffffffffff)


def x80_parts(tv):
    h = tv[4:]
    return int(h[:4], 16), int(h[4:], 16)


def f32_bits(v):
    return "f32:%08x" % struct.unpack("<I", struct.pack("<f", v))[0]


def f64_bits(v):
    return "f64:%016x" % struct.unpack("<Q", struct.pack("<d", v))[0]


def f64_of(tv):
    return struct.unpack("<d", to_bytes(tv))[0]


def f32_of(tv):
    return struct.unpack("<f", to_bytes(tv))[0]


# ---------------------------------------------------------------------------------------------
# Exact values. A decoded value is one of:
#   ("num", Fraction, sign)   finite (sign kept for zeros)
#   ("inf", None, sign)
#   ("nan", payload, sign, quiet)  payload = 63-bit fraction field of the extended significand

def decode_x80(tv):
    se, m = x80_parts(tv)
    sign, e = se >> 15, se & 0x7fff
    if e == 0x7fff:
        if (m & 0x7fffffffffffffff) == 0:
            return ("inf", None, sign)
        return ("nan", m & 0x7fffffffffffffff, sign, bool(m >> 62 & 1))
    if e == 0:
        v = Fraction(m, 1) * Fraction(2) ** (-16382 - 63)
    else:
        if not (m >> 63):          # unnormal: x87 treats it as invalid
            return ("nan", 0x4000000000000000, 1, True)
        v = Fraction(m, 1) * Fraction(2) ** (e - 16383 - 63)
    return ("num", -v if sign else v, sign)


def decode_typed(tv):
    t = tv.split(":", 1)[0]
    b = to_bytes(tv)
    if t == "x80":
        return decode_x80(tv)
    if t in ("i16", "i32", "i64"):
        v = int.from_bytes(b, "little", signed=True)
        return ("num", Fraction(v), 1 if v < 0 else 0)
    if t == "f32":
        u = struct.unpack("<I", b)[0]
        sign, e, f = u >> 31, (u >> 23) & 0xff, u & 0x7fffff
        if e == 0xff:
            return ("inf", None, sign) if f == 0 else ("nan", f << 40, sign, bool(f >> 22 & 1))
        v = Fraction(f, 1 << 23) * Fraction(2) ** (-126) if e == 0 else \
            (1 + Fraction(f, 1 << 23)) * Fraction(2) ** (e - 127)
        return ("num", -v if sign else v, sign)
    if t == "f64":
        u = struct.unpack("<Q", b)[0]
        sign, e, f = u >> 63, (u >> 52) & 0x7ff, u & ((1 << 52) - 1)
        if e == 0x7ff:
            return ("inf", None, sign) if f == 0 else ("nan", f << 11, sign, bool(f >> 51 & 1))
        v = Fraction(f, 1 << 52) * Fraction(2) ** (-1022) if e == 0 else \
            (1 + Fraction(f, 1 << 52)) * Fraction(2) ** (e - 1023)
        return ("num", -v if sign else v, sign)
    raise ValueError(tv)


FMT = {  # precision (significand bits), emin (unbiased, normal), emax, bias, kind
    "x80": (64, -16382, 16383, 16383),
    "f64": (53, -1022, 1023, 1023),
    "f32": (24, -126, 127, 127),
}
PC_BITS = {0: 24, 1: 64, 2: 53, 3: 64}    # PC=01 is reserved; hardware treats it as 64? (unused)
RC_NEAR, RC_DOWN, RC_UP, RC_CHOP = 0, 1, 2, 3

FLAG_IE, FLAG_DE, FLAG_ZE, FLAG_OE, FLAG_UE, FLAG_PE, FLAG_C1 = 1, 2, 4, 8, 0x10, 0x20, 0x200


def round_value(v, sign, p, emin, emax, rc, denorm_q=None):
    """Round exact Fraction v (v != 0) to p significant bits with exponent range [emin, emax].
    denorm_q: log2 of the smallest quantum (gradual underflow). Returns (kind, Fraction|None,
    flags) where kind is "num" or "inf"."""
    flags = 0
    a = abs(v)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if Fraction(2) ** e > a:
        e -= 1
    q = e - (p - 1)                               # log2 of the quantum at this exponent
    tiny = e < emin
    if denorm_q is not None and q < denorm_q:
        q = denorm_q
    scaled = a / Fraction(2) ** q
    n = scaled.numerator // scaled.denominator
    rem = scaled - n
    if rem:
        flags |= FLAG_PE
        neg = v < 0
        up = False
        if rc == RC_NEAR:
            up = rem > Fraction(1, 2) or (rem == Fraction(1, 2) and n % 2 == 1)
        elif rc == RC_DOWN:
            up = neg
        elif rc == RC_UP:
            up = not neg
        if up:
            n += 1
            flags |= FLAG_C1
        if tiny:
            flags |= FLAG_UE
    r = Fraction(n) * Fraction(2) ** q
    if r >= Fraction(2) ** (emax + 1):
        flags |= FLAG_OE | FLAG_PE
        maxf = (Fraction(2) ** p - 1) * Fraction(2) ** (emax - p + 1)
        neg = v < 0
        to_inf = rc == RC_NEAR or (rc == RC_UP and not neg) or (rc == RC_DOWN and neg)
        if to_inf:
            return ("inf", None, flags)
        r = maxf
    return ("num", -r if v < 0 else r, flags)


def encode(fmt, val):
    """Encode a decoded value into a typed string of format fmt (x80/f64/f32). Finite values
    must already be representable."""
    kind = val[0]
    sign = val[2]
    if fmt == "x80":
        if kind == "inf":
            return x80((sign << 15) | 0x7fff, 1 << 63)
        if kind == "nan":
            return x80((sign << 15) | 0x7fff, (1 << 63) | val[1])
        v = abs(val[1])
        if v == 0:
            return x80(sign << 15, 0)
        e = v.numerator.bit_length() - v.denominator.bit_length()
        if Fraction(2) ** e > v:
            e -= 1
        if e < -16382:                                    # extended denormal
            m = v * Fraction(2) ** (16382 + 63)
            assert m.denominator == 1
            return x80(sign << 15, int(m))
        m = v * Fraction(2) ** (63 - e)
        assert m.denominator == 1, "not representable"
        return x80((sign << 15) | (e + 16383), int(m))
    p, emin, emax, bias = FMT[fmt]
    nbits = 32 if fmt == "f32" else 64
    fbits = p - 1
    ebits = nbits - 1 - fbits
    if kind == "inf":
        u = (sign << (nbits - 1)) | (((1 << ebits) - 1) << fbits)
    elif kind == "nan":
        u = (sign << (nbits - 1)) | (((1 << ebits) - 1) << fbits) | (val[1] >> (63 - fbits))
        u |= 1 << (fbits - 1)                             # quiet
    else:
        v = abs(val[1])
        if v == 0:
            u = sign << (nbits - 1)
        else:
            e = v.numerator.bit_length() - v.denominator.bit_length()
            if Fraction(2) ** e > v:
                e -= 1
            if e < emin:
                m = v * Fraction(2) ** (-(emin - fbits))
                assert m.denominator == 1
                u = (sign << (nbits - 1)) | int(m)
            else:
                m = v * Fraction(2) ** (fbits - e)
                assert m.denominator == 1
                u = (sign << (nbits - 1)) | ((e + bias) << fbits) | (int(m) - (1 << fbits))
    return "%s:%0*x" % (fmt, nbits // 4, u)


INDEFINITE = ("nan", 0x4000000000000000, 1, True)


def pc_rc(cw):
    return PC_BITS[(cw >> 8) & 3], (cw >> 10) & 3


# High-precision constants for FLDxx (exact enough for any rounding: 200 bits).
def _const(name):
    import mpmath
    mpmath.mp.prec = 300
    v = {"fldpi": mpmath.pi, "fldl2e": mpmath.log(mpmath.e, 2), "fldl2t": mpmath.log(10, 2),
         "fldlg2": mpmath.log10(2), "fldln2": mpmath.log(2)}[name]
    m, e = mpmath.frexp(v)
    return Fraction(int(mpmath.floor(m * mpmath.mpf(2) ** 280)), 1) * Fraction(2) ** (int(e) - 280)


def isqrt_round(v, p, emin, rc, denorm_q):
    """Correctly rounded sqrt of a positive Fraction to p bits (via integer sqrt + sticky)."""
    # choose a scale 2^(2k) so that the integer root has p + 4 bits at least
    e = v.numerator.bit_length() - v.denominator.bit_length()
    k = (p + 8) - e // 2
    num = v * Fraction(2) ** (2 * k)
    n = num.numerator // num.denominator
    r = math.isqrt(n)
    exact = (r * r == n) and num.denominator == 1 and n == num
    # value = r * 2^-k (+ sticky)
    approx = Fraction(r) * Fraction(2) ** (-k)
    if not exact:
        approx += Fraction(1, 4) * Fraction(2) ** (-k)    # sticky: strictly between r and r+1
    return round_value(approx, 0, p, emin, 16383, rc, denorm_q)


# ---------------------------------------------------------------------------------------------
# Game-function entry points for call vectors (windows/frida/ummath_vectors.js, um_unicorn.py).
# name -> (module, export symbol or "+0xRVA", signature). Signatures as in the module docstring.
ENGINE = "Low-Level Engine.dll"
EXE = "Empire Earth.exe"
CRT = "msvcrt.dll"
KNOWN_CALLS = {
    "UMArcTan2": (ENGINE, "?UMArcTan2@@YAMMM@Z", "c:f:ff"),
    "UMArcCos": (ENGINE, "?UMArcCos@@YAMM@Z", "c:f:f"),
    "UMArcTan": (ENGINE, "?UMArcTan@@YAMM@Z", "c:f:f"),
    "UMCos": (ENGINE, "?UMCos@@YAMM@Z", "c:f:f"),
    "UMTan": (ENGINE, "?UMTan@@YAMM@Z", "c:f:f"),
    "UMQuadraticEquation": (ENGINE, "?UMQuadraticEquation@@YA_NMMMAAM0@Z", "c:b:fffpp"),
    "UMFastMath::Sqrt": (ENGINE, "?Sqrt@UMFastMath@@SAMM@Z", "c:f:f"),
    "UMGetFloatingPointPrecision": (ENGINE, "?UMGetFloatingPointPrecision@@YAKXZ", "c:i:"),
    "UMGetFloatingPointRoundingMode": (ENGINE, "?UMGetFloatingPointRoundingMode@@YAKXZ", "c:i:"),
    # EXE's truncating float->int64 helper (FISTP m64 + correction, no FLDCW): arg in ST(0)
    "exe_ftol_trunc": (EXE, "+0x13ba92", "i:l:d"),
    "sqrt": (CRT, "sqrt", "c:f:d"), "fabs": (CRT, "fabs", "c:f:d"), "pow": (CRT, "pow", "c:f:dd"),
    "atan2": (CRT, "atan2", "c:f:dd"), "atan": (CRT, "atan", "c:f:d"), "acos": (CRT, "acos", "c:f:d"),
    "asin": (CRT, "asin", "c:f:d"), "sin": (CRT, "sin", "c:f:d"), "cos": (CRT, "cos", "c:f:d"),
    "tan": (CRT, "tan", "c:f:d"), "exp": (CRT, "exp", "c:f:d"), "log": (CRT, "log", "c:f:d"),
    "log10": (CRT, "log10", "c:f:d"), "floor": (CRT, "floor", "c:f:d"), "ceil": (CRT, "ceil", "c:f:d"),
    "fmod": (CRT, "fmod", "c:f:dd"), "ldexp": (CRT, "ldexp", "c:f:di"),
    "_CIpow": (CRT, "_CIpow", "i:f:dd"), "_CIsqrt": (CRT, "_CIsqrt", "i:f:d"),
    "_CIsin": (CRT, "_CIsin", "i:f:d"), "_CIcos": (CRT, "_CIcos", "i:f:d"),
    "_CIatan2": (CRT, "_CIatan2", "i:f:dd"), "_CIlog": (CRT, "_CIlog", "i:f:d"),
    "_ftol": (CRT, "_ftol", "i:l:d"),
    "atof": (CRT, "atof", "c:f:s"), "strtod": (CRT, "strtod", "c:f:sp"),
    "sscanf_f": (CRT, "sscanf", "c:i:ssp"),       # args: input string, "%f", out float
    "rand": (CRT, "rand", "c:i:"), "srand": (CRT, "srand", "c:v:i"),
}


def call_vector(vid, name, args, cw="027f", tag=None, **extra):
    mod, sym, sig = KNOWN_CALLS[name]
    v = {"id": vid, "kind": "call", "fn": name, "mod": mod, "sym": sym, "sig": sig, "cw": cw, "args": args}
    if tag:
        v["tag"] = tag
    v.update(extra)
    return v


# ---------------------------------------------------------------------------------------------
# Call stub for game-function vectors (32-bit). windows/frida/ummath_vectors.js builds the same
# bytes in JavaScript; keep the two in step. The stub takes the block pointer as its only cdecl
# argument, sets the CW, pushes the arguments, calls block->fn, then records EAX, EDX, ST(0)
# (if the signature returns in ST(0)), the CW and SW after the call, and restores the CW.
CB_FN, CB_CW_IN, CB_CW_SAVED, CB_CW_AFTER, CB_SW_AFTER = 0x00, 0x04, 0x06, 0x08, 0x0A
CB_EAX, CB_EDX, CB_ST0, CB_SW_BEFORE = 0x0C, 0x10, 0x18, 0x24
CB_ARGS, CB_BUFS, CB_THIS, CB_STRS = 0x30, 0x70, 0xF0, 0x110
CB_STR_LEN, CB_SIZE = 0x40, 0x220


def parse_sig(sig):
    conv, ret, args = sig.split(":")
    return conv, ret, args


def build_call_stub(sig):
    conv, ret, args = parse_sig(sig)
    d32 = lambda v: struct.pack("<I", v)                      # noqa: E731
    b = bytearray(bytes.fromhex("558bec535657") + bytes.fromhex("8b5d08"))   # push ebp.. ; mov ebx,[ebp+8]
    b += b"\xd9\xbb" + d32(CB_CW_SAVED)                       # fnstcw [ebx+saved]
    b += b"\xd9\xab" + d32(CB_CW_IN)                          # fldcw  [ebx+cw_in]
    b += b"\xdb\xe2"                                          # fnclex
    b += b"\xdf\xe0" + b"\x66\x89\x83" + d32(CB_SW_BEFORE)    # fnstsw ax; mov [ebx+sw_before], ax
    if conv == "i":                                           # arguments on the x87 stack, in order
        for k, t in enumerate(args):
            assert t == "d"
            b += b"\xdd\x83" + d32(CB_ARGS + 8 * k)           # fld qword [ebx+arg_k]
    else:
        nbuf = nstr = 0
        plan = []
        for k, t in enumerate(args):
            if t in "pP":
                plan.append((t, CB_BUFS + 16 * nbuf))
                nbuf += 1
            elif t == "s":
                plan.append((t, CB_STRS + CB_STR_LEN * nstr))
                nstr += 1
            else:
                plan.append((t, CB_ARGS + 8 * k))
        for t, off in reversed(plan):
            if t in ("f", "i"):
                b += b"\xff\xb3" + d32(off)                   # push dword [ebx+off]
            elif t == "d":
                b += b"\xff\xb3" + d32(off + 4) + b"\xff\xb3" + d32(off)
            else:                                             # s, p, P: pointer into the block
                b += b"\x8d\x83" + d32(off) + b"\x50"         # lea eax,[ebx+off]; push eax
        if conv == "t":
            b += b"\x8d\x8b" + d32(CB_THIS)                   # lea ecx,[ebx+this]
    b += b"\xff\x93" + d32(CB_FN)                             # call [ebx+fn]
    b += b"\x89\x83" + d32(CB_EAX) + b"\x89\x93" + d32(CB_EDX)
    if ret == "f":
        b += b"\xdb\xbb" + d32(CB_ST0)                        # fstp tbyte [ebx+st0]
    b += b"\xd9\xbb" + d32(CB_CW_AFTER)                       # fnstcw [ebx+cw_after]
    b += b"\xdf\xe0" + b"\x66\x89\x83" + d32(CB_SW_AFTER)     # fnstsw ax; mov [ebx+sw_after], ax
    b += b"\xdb\xe3"                                          # fninit: leave an empty x87 stack
    b += b"\xd9\xab" + d32(CB_CW_SAVED)                       # fldcw [ebx+saved]
    b += bytes.fromhex("8d65f45f5e5b5dc3")                    # lea esp,[ebp-12]; pop edi esi ebx ebp; ret
    return bytes(b)


def fill_call_block(v, fn_addr):
    blk = bytearray(CB_SIZE)
    struct.pack_into("<IH", blk, CB_FN, fn_addr, int(v.get("cw", "027f"), 16))
    conv, ret, args = parse_sig(v["sig"])
    nbuf = nstr = 0
    for k, (t, a) in enumerate(zip(args, v["args"])):
        if t in "pP":
            init = to_bytes(a) if a.startswith("hex:") else b""
            blk[CB_BUFS + 16 * nbuf:CB_BUFS + 16 * nbuf + len(init)] = init
            nbuf += 1
        elif t == "s":
            s = a[4:].encode("latin-1")[:CB_STR_LEN - 1]
            blk[CB_STRS + CB_STR_LEN * nstr:CB_STRS + CB_STR_LEN * nstr + len(s)] = s
            nstr += 1
        elif t == "i" and not a.startswith("i32:"):
            struct.pack_into("<i", blk, CB_ARGS + 8 * k, int(a))
        else:
            raw = to_bytes(a)
            blk[CB_ARGS + 8 * k:CB_ARGS + 8 * k + len(raw)] = raw
    if v.get("this"):
        raw = to_bytes(v["this"])
        blk[CB_THIS:CB_THIS + len(raw)] = raw
    return blk


def read_call_block(v, blk):
    conv, ret, args = parse_sig(v["sig"])
    eax, edx = struct.unpack_from("<II", blk, CB_EAX)
    sw_b = struct.unpack_from("<H", blk, CB_SW_BEFORE)[0]
    sw_a = struct.unpack_from("<H", blk, CB_SW_AFTER)[0]
    r = {"st0": from_bytes("x80", blk[CB_ST0:CB_ST0 + 10]) if ret == "f" else None,
         "eax": "i32:%08x" % (eax & (0xff if ret == "b" else 0xffffffff)) if ret in ("i", "l", "b") else None,
         "edx": "i32:%08x" % edx if ret == "l" else None,
         "bufs": ["hex:" + bytes(blk[CB_BUFS + 16 * k:CB_BUFS + 16 * k + 16]).hex()
                  for k in range(sum(1 for t in args if t in "pP"))],
         "cw_after": "%04x" % struct.unpack_from("<H", blk, CB_CW_AFTER)[0],
         "sw_after": "%04x" % sw_a,
         "top_delta": (((sw_a >> 11) & 7) - ((sw_b >> 11) & 7)) % 8}
    if conv == "t":
        r["this"] = "hex:" + bytes(blk[CB_THIS:CB_THIS + 32]).hex()
    return r
