"""Reference model of msvcrt.dll's decimal-to-binary conversion, with its open variants.

The kit implements the measured model (kit/runtime/msvcrt_format.cpp, "Decimal -> binary"),
which reproduces every strtod, atof and sscanf("%f"/"%lf") result measured on legion
(about 14,000 inputs, among them 1,500 chosen to tell models apart):

  1. Only the first 24 significant digits count. Those digits, without trailing zeros, form
     an integer that is truncated to its 64 most significant bits.
  2. That integer times 10^E is truncated to 80 significant bits (one rounding of the exact
     product stands for the extended-precision arithmetic msvcrt does).
  3. A normal result is rounded half-down from those 80 bits: up only past half, so an
     exact tie goes down.
  4. A denormal is rounded from the same 80 bits half-down at half its unit, and that bit is
     then truncated.

`gen_vectors.py --suite decimal2` writes inputs on which the variants below disagree;
`fit_decimal.py` scores them against a run (legion, 2026-10-15: only the 24-digit limit
fits all 300).

  stepwise      truncate after every digit instead of once
  max_digits    ignore digits after this many (24; None: all)
  strip_zeros   drop trailing zeros before accumulating
  product_bits  significant bits the scaled product keeps (None: exact)
  product_mode  "trunc" or "nearest" for that step
  stage1        round a denormal to the full significand first
"""
from fractions import Fraction
import re

NUM = re.compile(r'^[ \t\n\r\v\f]*([+-]?)(\d*)(?:\.(\d*))?(?:[eEdD]([+-]?\d+))?')
DEFAULT = dict(stepwise=False, max_digits=24, strip_zeros=True, product_bits=80,
               product_mode="trunc", stage1=False)


def parse(s):
    """(negative, digits without leading zeros, exp10) with value 0.digits x 10^exp10."""
    m = NUM.match(s)
    sign, ip, fp, ex = m.group(1), m.group(2) or "", m.group(3) or "", m.group(4)
    if not ip and not fp:
        return None
    digits = ip + fp
    e = len(ip) + (int(ex) if ex else 0)
    stripped = digits.lstrip("0")
    e -= len(digits) - len(stripped)
    return sign == "-", stripped, e


def trunc_bits(n, bits):
    length = n.bit_length()
    if length <= bits:
        return n
    s = length - bits
    return (n >> s) << s


def accumulate(digits, stepwise, max_digits, strip_zeros):
    if max_digits:
        digits = digits[:max_digits]
    if strip_zeros:
        digits = digits.rstrip("0")
    if not digits:
        return 0, 0
    if stepwise:
        acc = 0
        for ch in digits:
            acc = trunc_bits(acc * 10 + (ord(ch) - 48), 64)
        return acc, len(digits)
    return trunc_bits(int(digits), 64), len(digits)


def exp2(q):
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if Fraction(2) ** e > q:
        e -= 1
    if Fraction(2) ** (e + 1) <= q:
        e += 1
    return e


def round_to_bits(q, bits, mode):
    e = exp2(q)
    x = q * Fraction(2) ** (bits - 1 - e)
    n = x.numerator // x.denominator
    r = x - n
    if mode == "nearest" and (r > Fraction(1, 2) or (r == Fraction(1, 2) and n & 1)):
        n += 1
    return Fraction(n) * Fraction(2) ** (e - (bits - 1))


def round_ms(q, P, emin, emax, stage1=False):
    """Fraction q > 0 -> Fraction result (None for overflow)."""
    e = exp2(q)
    if e >= emin or stage1:
        x = q * Fraction(2) ** (P - 1 - e)
        m = x.numerator // x.denominator
        if x - m > Fraction(1, 2):
            m += 1
            if m == 1 << P:
                m >>= 1
                e += 1
        if e > emax:
            return None
        if e >= emin:
            return Fraction(m) * Fraction(2) ** (e - (P - 1))
        q = Fraction(m) * Fraction(2) ** (e - (P - 1))
    unit = Fraction(2) ** (emin - (P - 1) - 1)
    x = q / unit
    n = x.numerator // x.denominator
    if x - n > Fraction(1, 2):
        n += 1
    return Fraction(n >> 1) * unit * 2


def convert(s, P=53, emin=-1022, emax=1023, **variant):
    """The value msvcrt gives for text s, as a Fraction (or None for infinity), and the
    sign; P/emin/emax 53/-1022/1023 for double, 24/-126/127 for float."""
    v = dict(DEFAULT, **variant)
    p = parse(s)
    if p is None or not p[1]:
        return Fraction(0), bool(p and p[0])
    neg, digits, e = p
    if e > 400:
        return None, neg
    if e < -400:
        return Fraction(0), neg
    acc, n = accumulate(digits, v["stepwise"], v["max_digits"], v["strip_zeros"])
    if acc == 0:
        return Fraction(0), neg
    q = Fraction(acc) * Fraction(10) ** (e - n)
    if v["product_bits"]:
        q = round_to_bits(q, v["product_bits"], v["product_mode"])
    return round_ms(q, P, emin, emax, v["stage1"]), neg


def to_bits(value, neg, P=53):
    import struct
    if P == 53:
        x = float("inf") if value is None else float(value)
        b = struct.unpack("<Q", struct.pack("<d", x))[0]
        return "%016x" % (b | (1 << 63) if neg else b)
    if value is None:
        b = 0x7F800000
    else:
        b = struct.unpack("<I", struct.pack("<f", float(value)))[0]
    return "%08x" % (b | (1 << 31) if neg else b)
