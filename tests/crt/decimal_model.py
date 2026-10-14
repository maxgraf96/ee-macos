"""Reference model of msvcrt.dll's decimal-to-binary conversion, with its open variants.

The kit implements the measured model (kit/runtime/msvcrt_format.cpp, "Decimal -> binary"):
digits accumulate into an integer truncated to its 64 most significant bits, the integer
times 10^E is exact, it is rounded half-down (up only past half) to the significand, and a
denormal is rounded again half-down at half its unit and that bit truncated.

legion's 4,267 strtod and 4,267 sscanf("%f") results cannot tell apart the variants below;
`gen_vectors.py --suite decimal` writes inputs on which they disagree, and `fit_decimal.py`
says which variant Windows follows.

  stepwise   truncate after every digit (True) or once, over all digits (False)
  max_digits ignore digits after this many (None: all)
  stage1     a denormal is first rounded to the full significand (True) or straight from
             the exact value (False)
  scale_bits None: exact scaling; n: the product is truncated to n bits before rounding
"""
from fractions import Fraction
import re

NUM = re.compile(r'^[ \t\n\r\v\f]*([+-]?)(\d*)(?:\.(\d*))?(?:[eEdD]([+-]?\d+))?')
DEFAULT = dict(stepwise=True, max_digits=None, stage1=True, scale_bits=None)


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


def accumulate(digits, stepwise, max_digits):
    if max_digits:
        digits = digits[:max_digits]
    if stepwise:
        acc = 0
        for ch in digits:
            acc = trunc_bits(acc * 10 + (ord(ch) - 48), 64)
        return acc, len(digits)
    return trunc_bits(int(digits), 64) if digits else 0, len(digits)


def round_ms(q, P, emin, emax, stage1=True):
    """Fraction q > 0 -> Fraction result (None for overflow)."""
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if Fraction(2) ** e > q:
        e -= 1
    if Fraction(2) ** (e + 1) <= q:
        e += 1
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
    acc, n = accumulate(digits, v["stepwise"], v["max_digits"])
    if acc == 0:
        return Fraction(0), neg
    q = Fraction(acc) * Fraction(10) ** (e - n)
    if v["scale_bits"]:
        exp = q.numerator.bit_length() - q.denominator.bit_length() + 1
        scaled = q * Fraction(2) ** (v["scale_bits"] - exp)
        q = Fraction(scaled.numerator // scaled.denominator) * Fraction(2) ** (exp - v["scale_bits"])
    return round_ms(q, P, emin, emax, v["stage1"]), neg


def to_bits(value, neg, P=53):
    import struct
    if value is None:
        x = float("inf")
    else:
        x = float(value) if P == 53 else value
    if P == 53:
        b = struct.unpack("<Q", struct.pack("<d", x if value is None else float(value)))[0]
        return "%016x" % (b | (1 << 63) if neg else b)
    if value is None:
        b = 0x7F800000
    else:
        b = struct.unpack("<I", struct.pack("<f", float(value)))[0]
    return "%08x" % (b | (1 << 31) if neg else b)
