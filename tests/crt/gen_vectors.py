"""Generate the msvcrt.dll behaviour vectors (format in README.md).

  python3 -I tests/crt/gen_vectors.py --seed 1 -o build/crt/vectors.tsv \
      [--game-numbers analysis/crt/game-numbers.tsv]

Every line is one call: `id<TAB>op<TAB>field...`. Strings are hex (so tabs, newlines and
high bytes survive), doubles are their 16 hex-digit IEEE bits, integers are decimal. The
same file is run by crtvec.exe against Windows' msvcrt.dll and by the kit's
`crt_tests --vectors` against the HLE; compare.py diffs the two outputs.

Without --game-numbers the file contains no game data and may be shared freely; with it,
it carries the numeric tokens of the user's own copy and stays under build/ (git-ignored).
"""
import argparse
import os
import math
import random
import struct
from decimal import Decimal, getcontext

getcontext().prec = 1200


def hx(s):
    if isinstance(s, str):
        s = s.encode("latin-1")
    return s.hex()


def dbits(x):
    return struct.pack("<d", x)[::-1].hex()


def from_bits(b):
    return struct.unpack("<d", struct.pack("<Q", b))[0]


def fbits_to_double(b):
    return struct.unpack("<f", struct.pack("<I", b))[0]


def next_up(x):
    return math.nextafter(x, math.inf)


def exact_decimal(x):
    """The exact decimal expansion of a finite double."""
    return format(Decimal(x), "f")


def midpoint_decimal(lo, hi):
    """The exact decimal of the midpoint between two adjacent doubles (or floats)."""
    return format((Decimal(lo) + Decimal(hi)) / 2, "f")


SPECIALS = [0.0, -0.0, 1.0, -1.0, math.inf, -math.inf, from_bits(0x7FF8000000000000),
            from_bits(0xFFF8000000000000), from_bits(0x7FF0000000000001),
            from_bits(0x7FF4000000000000), from_bits(1), from_bits(0x000FFFFFFFFFFFFF),
            from_bits(0x0010000000000000), 1.7976931348623157e308, 2.2250738585072014e-308]


class Gen:
    def __init__(self, seed, game_numbers):
        self.rng = random.Random(seed)
        self.lines = []
        self.game = game_numbers

    def add(self, op, *fields):
        self.lines.append((op, [str(f) for f in fields]))

    # -----------------------------------------------------------------------------
    def rand(self):
        for seed in [0, 1, 2, 3, 42, 12345, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF] + \
                [self.rng.getrandbits(32) for _ in range(20)]:
            self.add("rand", seed, 12)
        self.add("rand_thread", 8)

    # -----------------------------------------------------------------------------
    def decimal_strings(self):
        """Inputs for strtod/atof/sscanf: real game tokens, edge grammar, hard cases."""
        out = []
        out += [t for t in self.game]
        out += ["0", "-0", "+0", "1", "-1", "0.1", "0.5", "0.25", "1.5", "2.5", "1e10", "1E-10",
                "1.5d2", "1.5D-2", "1.5e+2", "1e", "1e+", "1e-", "1d", ".5", "5.", ".", "-.5",
                "+.5", "-", "+", "", " ", "inf", "INF", "-inf", "infinity", "nan", "NAN",
                "-nan(ind)", "1.#INF", "1.#QNAN", "0x10", "0X1p3", "1e400", "-1e400", "1e-400",
                "4.9406564584124654e-324", "2.4703282292062327e-324",
                "2.4703282292062328e-324", "2.2250738585072011e-308",
                "2.2250738585072014e-308", "1.7976931348623157e308", "1.7976931348623158e308",
                "1.797693134862315807e308", "1.7976931348623159e308", "1e308", "1e309",
                "   \t\n1.25", "1.25   ", "1.25abc", "1,25", "--1", "+-1", "1..2", "1.2.3",
                "00000000000000000000000000001.5", "1" + "0" * 40, "0." + "0" * 40 + "1",
                "123456789012345678901234567890", "9007199254740993",
                "9007199254740993.000000000000001", "9007199254740992.5",
                "3.4028234663852886e38", "3.4028235677973366e38", "1.17549435e-38",
                "1.4e-45", "7e-46", "0.000001", "1e-7", "100000000", "16777217",
                "33554433", "0.1f", "1e1000000000", "1e-1000000000", "1e2147483648"]
        # Doubles: exact midpoints between neighbours, and nearly-midpoints.
        for _ in range(150):
            x = abs(from_bits(self.rng.getrandbits(64) & 0x7FEFFFFFFFFFFFFF))
            if not math.isfinite(x) or x == 0:
                continue
            e = self.rng.choice([0, 0, 1, 2])
            if e == 0:
                x = self.rng.uniform(0.001, 10000.0)
            elif e == 1:
                x = float(self.rng.randint(1, 10 ** 6)) / 10 ** self.rng.randint(0, 8)
            mid = midpoint_decimal(x, next_up(x))
            out.append(mid if len(mid) < 900 else mid[:900])
            digits = self.rng.randint(17, 26)
            sig = Decimal(mid)
            out.append(format(sig, f".{digits}g"))
            out.append(repr(x))
            out.append("%.17g" % x)
            out.append("%.9g" % x)
        # Floats: midpoints between adjacent floats, as decimals (exact, short), and the
        # double-rounding trap: just above a float midpoint, below the next double.
        for _ in range(150):
            fb = self.rng.randint(0x00800000, 0x7F7FFFFF)
            if self.rng.random() < 0.5:
                fb = self.rng.randint(0x3C000000, 0x47000000)  # 0.0078 .. 32768, game-like
            lo, hi = fbits_to_double(fb), fbits_to_double(fb + 1)
            mid = Decimal(lo) + (Decimal(hi) - Decimal(lo)) / 2
            out.append(format(mid, "f"))
            ulp_d = Decimal(next_up((lo + hi) / 2)) - Decimal((lo + hi) / 2)
            out.append(format(mid + ulp_d / 4, "f")[:60])
            out.append(format(mid - ulp_d / 4, "f")[:60])
            out.append("%.9g" % lo)
            out.append("%.8g" % lo)
        # Random short decimals, the game's kind.
        for _ in range(300):
            n = self.rng.randint(1, 9)
            digits = "".join(self.rng.choice("0123456789") for _ in range(n))
            point = self.rng.randint(0, n)
            s = digits[:point] + "." + digits[point:]
            if self.rng.random() < 0.2:
                s = "-" + s
            if self.rng.random() < 0.15:
                s += "e" + str(self.rng.randint(-40, 40))
            out.append(s)
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq

    def parsing(self):
        strings = self.decimal_strings()
        for s in strings:
            self.add("strtod", "C", hx(s))
            self.add("atof", "C", hx(s))
            self.add("sscanf_f", "C", hx(s))
            self.add("sscanf_lf", "C", hx(s))
        for s in ["1,25", "1.25", "-0,5e3", "3,", ",5", "1.234,5"]:
            for loc in ["German", "English"]:
                self.add("strtod", loc, hx(s))
                self.add("sscanf_f", loc, hx(s))
        ints = ["0", "-0", "1", "-1", "42", "  42", "+42", "-2147483648", "2147483647",
                "2147483648", "4294967295", "4294967296", "4294967297", "99999999999",
                "-99999999999", "12abc", "abc", "", "0x1A", "077", "1e3", "\t\n-7"]
        ints += [t for t in self.game if "." not in t and "e" not in t.lower()][:200]
        for s in ints:
            self.add("sscanf_d", hx(s))
            self.add("atoi", hx(s))
            self.add("atoi64", hx(s))
            for base in [0, 2, 8, 10, 16, 36]:
                self.add("strtol", hx(s), base)
                self.add("strtoul", hx(s), base)
        for s in ["0x7fffffff", "0X80000000", "0xffffffff", "0x100000000", "-0x10", "0x",
                  "0xg", "08", "09", "0b101", "zz", "ZZZZZZZ", "-zz", "1_000"]:
            for base in [0, 2, 8, 10, 16, 36]:
                self.add("strtol", hx(s), base)
                self.add("strtoul", hx(s), base)
        for s in ["9223372036854775807", "9223372036854775808", "-9223372036854775808",
                  "-9223372036854775809", "18446744073709551616", "123456789012345678901"]:
            self.add("atoi64", hx(s))
        for v in [0, 1, -1, 42, -42, 255, 0x7FFFFFFF, -0x80000000, 1000000, 0x1234ABCD]:
            for radix in [2, 8, 10, 16, 36]:
                self.add("itoa", v, radix)

    # -----------------------------------------------------------------------------
    def float_values(self):
        vals = list(SPECIALS)
        vals += [0.5, 1.5, 2.5, 3.5, -0.5, -2.5, 0.125, 0.375, 0.625, 2.675, 1.005, 1.115,
                 0.05, 0.15, 0.25, 0.35, 0.45, 9.5, 99.5, 999.5, 9999.5, 0.95, 0.995, 0.9995,
                 0.09999999999999999, 0.1, 0.2, 0.3, 1 / 3, 2 / 3, math.pi, math.e, 1e15 + 0.5,
                 1e16, 1e17, 1e21, 1e22, 1e23, 123456789012345678.0, 4.35, 0.000123456789,
                 1e-5, 1e-4, 9.9999e-5, 0.00099999, 999999.5, 9999995.0, 100.0, 1e100,
                 1e300, 1e-300, 5e-324, 1.7976931348623157e308, 12.5, 360.0, 0.017453292519943295,
                 57.29577951308232]
        for _ in range(60):
            vals.append(self.rng.uniform(-1000, 1000))
        for _ in range(40):
            vals.append(fbits_to_double(self.rng.randint(0x3C000000, 0x47000000)))  # floats
        for _ in range(30):
            vals.append(from_bits(self.rng.getrandbits(64) & 0x7FEFFFFFFFFFFFFF))
        for t in self.game[:80]:
            try:
                vals.append(float(t))
            except ValueError:
                pass
        # Values whose 17-digit expansion ends in ...5 at a rounding position.
        for k in range(1, 8):
            vals.append(float("0." + "4" * k + "5"))
            vals.append(float("1." + "9" * k + "5"))
        return vals

    def formatting(self):
        fmts = ["%f", "%e", "%g", "%E", "%G", "%.0f", "%.1f", "%.2f", "%.3f", "%.6f", "%.10f",
                "%.15f", "%.16f", "%.17f", "%.18f", "%.20f", "%.30f", "%.0e", "%.1e", "%.10e",
                "%.15e", "%.16e", "%.17e", "%.20e", "%.0g", "%.1g", "%.6g", "%.10g", "%.15g",
                "%.16g", "%.17g", "%.20g", "%#g", "%#.0f", "%#.0e", "%#.3g", "%+f", "% f",
                "%015.4f", "%-12.3e|", "%6.6f", "%.40f", "%12g", "%-+8.2f|", "%lf", "%Lf",
                "%.2e", "%.4g"]
        vals = self.float_values()
        for v in vals:
            for f in fmts:
                self.add("sprintf_d", "C", hx(f), dbits(v))
        for v in [1.5, -2.25, 1e10, 0.001]:
            for f in ["%f", "%e", "%g", "%.2f"]:
                self.add("sprintf_d", "German", hx(f), dbits(v))
        ifmts = ["%d", "%i", "%u", "%x", "%X", "%o", "%#x", "%#X", "%#o", "%5d", "%-5d|",
                 "%05d", "%+d", "% d", "%.3d", "%.0d", "%c", "%hd", "%hu", "%hx", "%p",
                 "%ld", "%lu", "%10.4x", "%-#10x|", "%+.0d", "%%%d", "%y", "%5%"]
        for v in [0, 1, 0xFFFFFFFF, 42, 255, 0x7FFFFFFF, 0x80000000, 1000000, 0x1234ABCD,
                  65535, 65536, 0x41, 0xFFFFFF80]:
            for f in ifmts:
                self.add("sprintf_i", hx(f), v)
        for v in [0, 1, 0xFFFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000,
                  1234567890123456789, 0xFFFFFFFF]:
            for f in ["%I64d", "%I64u", "%I64x", "%I64X", "%020I64d", "%-22I64d|"]:
                self.add("sprintf_i64", hx(f), "%016x" % v)
        for s in ["", "abc", "hello world", "caf\xe9", "\x80\x9f\xff", "a" * 40]:
            for f in ["%s", "%10s", "%-10s|", "%.3s", "%10.3s", "%.0s", "[%s]"]:
                self.add("sprintf_s", hx(f), hx(s))
        for count in range(0, 7):
            for s in ["", "abc", "abcd", "abcdef"]:
                self.add("snprintf", count, hx("%s"), hx(s))

    # -----------------------------------------------------------------------------
    def maths(self):
        xs = list(SPECIALS) + [2.0, 0.5, 3.0, 1e-310, 4.0, 0.25, 1e300, 1e-300, 7.0, 100.0]
        xs += [self.rng.uniform(0, 1000) for _ in range(60)]
        xs += [fbits_to_double(self.rng.randint(0x3C000000, 0x47000000)) for _ in range(60)]
        # Squares of 24/25-bit numbers: the PC=24 double-rounding cases.
        for _ in range(80):
            a = self.rng.randint(1 << 23, (1 << 24) - 1)
            xs.append(float(a * a + self.rng.choice([-1, 0, 1, a])))
            m = (2 * a + 1)
            xs.append(float(m * m) / 4.0)
        for cw in ["027f", "007f", "037f"]:
            for x in xs:
                self.add("math", "sqrt", cw, dbits(x), "0")
        for x in xs[:60] + [-1.0, -0.5, 1e-320]:
            self.add("math", "log", "027f", dbits(x), "0")
            self.add("math", "fabs", "027f", dbits(x), "0")
            self.add("math", "_finite", "027f", dbits(x), "0")
        for x in [0.0, -0.0, 0.5, 1.0, 3.14159, 1e10, 1e20, 2.0 ** 63, 2.0 ** 64, -1e22, math.inf,
                  math.nan, 1e-300] + [self.rng.uniform(-10, 10) for _ in range(40)]:
            self.add("math", "sin", "027f", dbits(x), "0")
        pairs = [(2.0, 10.0), (2.0, 0.5), (-8.0, 1 / 3), (-8.0, 3.0), (0.0, -1.0), (-0.0, -1.0),
                 (0.0, 0.0), (1.0, math.nan), (math.nan, 0.0), (math.inf, 0.5), (-math.inf, 3.0),
                 (10.0, 308.0), (10.0, 309.0), (10.0, -323.0), (0.5, 1074.0), (1.0000001, 1e9),
                 (-1.0, math.inf), (1.5, -math.inf)]
        pairs += [(self.rng.uniform(0, 2), self.rng.uniform(0, 8)) for _ in range(40)]
        for x, y in pairs:
            self.add("math", "pow", "027f", dbits(x), dbits(y))
        for x, e in [(1.5, 4), (1.0, 1024), (1.0, 1023), (1.0, -1074), (1.0, -1075), (3.0, -1074),
                     (-2.0, 3), (0.0, 100), (math.inf, -5), (1e-310, 60)]:
            self.add("math", "ldexp", "027f", dbits(x), str(e))
        for fn in ["_CIsqrt", "_CIsin", "_CIcos", "_CItan", "_CIexp", "_CIlog"]:
            for x in [0.0, 0.5, 1.0, 2.0, -1.0, 10.0, 100.0, 1e-5, math.inf, math.nan] + \
                    [self.rng.uniform(-5, 5) for _ in range(10)]:
                self.add("ci", fn, "027f", dbits(x), dbits(0.0))
        for x, y in [(2.0, 3.0), (2.0, 0.5), (-2.0, 3.0), (10.0, 2.0), (0.5, -2.0)] + \
                [(self.rng.uniform(0, 4), self.rng.uniform(-3, 3)) for _ in range(10)]:
            self.add("ci", "_CIpow", "027f", dbits(x), dbits(y))
        for y, x in [(1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (0.0, -1.0), (-0.0, -1.0), (1.0, 0.0)] + \
                [(self.rng.uniform(-4, 4), self.rng.uniform(-4, 4)) for _ in range(10)]:
            self.add("ci", "_CIatan2", "027f", dbits(y), dbits(x))
        for x in [0.0, -0.0, 0.5, -0.5, 1.5, -2.75, 2.5, 1e9, 2147483648.0, -2147483649.0, 1e30,
                  -1e30, 2.0 ** 63, -(2.0 ** 63), math.inf, math.nan, 9007199254740993.0]:
            for cw in ["027f", "0e7f"]:
                self.add("ftol", cw, dbits(x))

    # -----------------------------------------------------------------------------
    def characters(self):
        for loc in ["C", "English"]:
            for ch in list(range(-129, 257)) + [0x1234, -1000]:
                self.add("ctype", loc, ch)
            for w in list(range(0, 0x300)) + [0x391, 0x3B1, 0x3C2, 0x416, 0x436, 0x401, 0x451,
                                             0x1E9E, 0x2160, 0x24B6, 0x3000, 0xFF10, 0xFF21,
                                             0xFF41, 0x10400 & 0xFFFF, 0xFFFF, 0xFEFF, 0x20AC]:
                self.add("wctype", loc, w)
            for b in range(1, 256):
                self.add("mbconv", loc, b)
            for start in range(0x20, 0x100, 16):
                self.add("mbcase", loc, hx(bytes(range(start, start + 16))))
        pairs = [("abc", "abd"), ("abd", "abc"), ("abc", "abc"), ("abc", "ab"), ("ab", "abc"),
                 ("", "a"), ("A", "a"), ("Hello", "hello"), ("HELLO", "help"),
                 ("caf\xe9", "CAF\xc9"), ("\xe9", "\xc9"), ("a\x80", "a\x7f"), ("[", "a"),
                 ("_", "A"), ("z", "\xe0"), ("\xff", "\x9f")]
        for fn in ["strcmp", "strncmp", "memcmp", "_stricmp", "_strnicmp", "_mbscmp",
                   "_mbsicmp", "_mbsnbcmp", "_mbsnbicmp", "_mbsncmp", "wcscmp"]:
            for loc in ["C", "English"]:
                for a, b in pairs:
                    for n in [1, 2, 3, 8]:
                        if fn in ("strcmp", "_stricmp", "_mbscmp", "_mbsicmp", "wcscmp") and n != 8:
                            continue
                        # memcmp reads n bytes whatever they are: stay within
                        # both strings and their terminators.
                        if fn == "memcmp" and n > min(len(a), len(b)) + 1:
                            continue
                        self.add("cmp", fn, loc, hx(a), hx(b), n)

    def locales(self):
        names = ["C", "", "English", "english", "English_United States",
                 "English_United States.1252", "English_USA.1252", "american", "american english",
                 "english-us", "ENU", "en-US", "English_United Kingdom", "british", "German",
                 "german", "deu", "French", "French_France.1252", "Spanish", "Italian", "Dutch",
                 "Portuguese", ".1252", ".ACP", ".OCP", "English.437", "Klingon", "C.UTF-8",
                 "LC_COLLATE=C;LC_CTYPE=English;LC_MONETARY=C;LC_NUMERIC=German;LC_TIME=C"]
        for name in names:
            for cat in [0, 1, 2, 3, 4, 5]:
                self.add("setlocale", cat, hx(name))

    def times(self):
        tms = [(55, 3, 2, 2, 0, 80, 3, 1, 0), (0, 0, 0, 1, 0, 70, 4, 0, 0),
               (59, 59, 23, 31, 11, 99, 5, 364, 0), (7, 8, 13, 29, 1, 100, 2, 59, 0),
               (5, 4, 3, 9, 6, 126, 2, 189, 1), (0, 0, 12, 15, 5, 101, 5, 165, 0)]
        for tm in tms:
            self.add("asctime", *tm)
            for loc in ["C", "English"]:
                for f in ["%a %A %b %B %d %H %I %j %m %M %p %S %U %w %W %y %Y %%",
                          "%c", "%#c", "%x", "%#x", "%X", "%#d %#H %#I %#j %#m %#M %#S %#y",
                          "%Y-%m-%d", "", "%Q", "abc%"]:
                    self.add("strftime", loc, hx(f), 128, *tm)
            self.add("strftime", "C", hx("%Y-%m-%d"), 10, *tm)
            self.add("strftime", "C", hx("%Y-%m-%d"), 11, *tm)
        for t in [0, 1, 86399, 86400, 951782400, 1000000000, 0x7FFFFFFF, -1, -86400]:
            self.add("gmtime", t)

    def misc(self):
        for n in [0, 1, 2, 7, 8, 15, 16, 17, 31, 100, 1000, 4096, 100000]:
            self.add("msize", n)
        for p in ["C:\\games\\data\\file.name.txt", "noext", "dir\\", "\\\\server\\share\\a.b",
                  "C:file.txt", "a/b/c.d", ".hidden", "dir.ext\\file", "C:", "", "x" * 300 + ".txt",
                  "a\\" + "y" * 300 + "\\b.c"]:
            self.add("splitpath", hx(p))
        for d in [".?AVFoo@@", ".?AUBar@ns@@", ".?AVInner@Outer@ns@@", ".H", ".PAX", ".PBD",
                  ".?AW4Kind@@", ".?AV?$vector@HV?$allocator@H@std@@@std@@",
                  ".?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@",
                  ".?AV?$Array@$03@@", ".?AV?$Array@$0?2@@", ".?AV?$Array@$0BA@@@", ".N", ".M",
                  ".?AVexception@@", ".?AVbad_cast@std@@", ".PAVFoo@@", ".AAH", ".PAPAD",
                  ".?AU?$pair@$$CBHH@std@@", ".?AV<lambda_1>@@", ".X", "._N", "._J"]:
            self.add("typename", hx(d))
        contents = ["line one\r\nline two\r\n", "a\nb\rc\r\r\nd", "no newline", "",
                    "x\x1ay\r\nz", "\r\n\r\n", "1234567890abcdefghij\r\nshort\r\n",
                    "end with cr\r", "bin\x00ary\r\n\xff\xfe"]
        for c in contents:
            for mode in ["r", "rb", "rt"]:
                self.add("textfile", hx(mode), hx(c))


def wide_suite(g):
    """Every BMP code point through towupper/towlower/iswctype in both locales:
    the measurement the HLE's wide tables are generated from (wide_table.py)."""
    for loc in ["C", "English"]:
        for w in range(0x10000):
            g.add("wctype", loc, w)


VARIANTS = {
    "measured": {},
    "stepwise": dict(stepwise=True),
    "keep_zeros": dict(strip_zeros=False),
    "cap24": dict(max_digits=24),
    "cap28": dict(max_digits=28),
    "cap32": dict(max_digits=32),
    "exact_product": dict(product_bits=None),
    "product79": dict(product_bits=79),
    "product81": dict(product_bits=81),
    "product78_nearest": dict(product_bits=78, product_mode="nearest"),
    "product79_nearest": dict(product_bits=79, product_mode="nearest"),
    "product80_nearest": dict(product_bits=80, product_mode="nearest"),
    "stage1": dict(stage1=True),
}


def decimal_suite(g, count=400, long_inputs=False):
    """Inputs on which the variants of decimal_model.py disagree (fit_decimal.py)."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import decimal_model as dm
    rng = g.rng
    kept = []

    def outcomes(text, P, emin, emax):
        return {name: dm.to_bits(*dm.convert(text, P, emin, emax, **v), P=P)
                for name, v in VARIANTS.items()}

    def consider(text):
        for P, emin, emax in ((53, -1022, 1023), (24, -126, 127)):
            o = outcomes(text, P, emin, emax)
            if len(set(o.values())) > 1:
                kept.append(text)
                return True
        return False

    tries = 0
    while len(kept) < count and tries < 40 * count:
        tries += 1
        kind = rng.random()
        if kind < 0.45:  # near a double midpoint: long expansions cut at 18..30 digits
            x = from_bits(rng.getrandbits(64) & 0x7FEFFFFFFFFFFFFF)
            if not math.isfinite(x) or x == 0:
                continue
            mid = Decimal(x) + (Decimal(next_up(x)) - Decimal(x)) / 2
            digits = rng.randint(18, 30)
            text = format(mid, f".{digits}e")
            if rng.random() < 0.5:
                text = format(mid + (Decimal(next_up(x)) - Decimal(x)) * Decimal(rng.choice([1, -1])) / 10 ** rng.randint(5, 14), f".{digits}e")
        elif kind < 0.7:  # near a float midpoint, any exponent including denormals
            fb = rng.randint(1, 0x7F7FFFFF)
            lo, hi = fbits_to_double(fb), fbits_to_double(fb + 1)
            mid = (Decimal(lo) + Decimal(hi)) / 2
            text = format(mid, f".{rng.randint(17, 26)}e")
        elif kind < 0.85:  # double denormals near unit boundaries
            n = rng.randint(1, 1 << 52)
            q = (Decimal(n) + Decimal(rng.choice([0.5, 0.25, 0.75, 0.4999999, 0.5000001]))) * Decimal(2) ** -1074
            text = format(q, f".{rng.randint(17, 24)}e")
        elif not long_inputs or kind < 0.92:  # 19-digit integers times large powers of ten
            text = "%d" % rng.randint(10 ** 18, 10 ** 19) + "e" + str(rng.randint(-330, 290))
        else:  # 24..40 digits with trailing zeros mixed in: the digit limit and the zeros
            digits = "%d" % rng.randint(10 ** 23, 10 ** rng.randint(24, 40))
            if rng.random() < 0.5:
                digits = digits[:rng.randint(19, len(digits))] + "0" * rng.randint(1, 6)
            text = digits[0] + "." + digits[1:] + "e" + str(rng.randint(-320, 300))
        consider(text)
    for text in kept:
        g.add("strtod", "C", hx(text))
        g.add("sscanf_f", "C", hx(text))
        g.add("sscanf_lf", "C", hx(text))
    print(f"decimal suite: {len(kept)} discriminating inputs from {tries} tries")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--suite", choices=["all", "wide", "decimal", "decimal2"], default="all",
                    help="wide: only the full-BMP wctype measurement (131,072 vectors); "
                    "decimal: inputs that decide between decimal_model.py's variants")
    ap.add_argument("--game-numbers", help="TSV from extract_game_numbers.py")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    game = []
    if args.game_numbers:
        with open(args.game_numbers) as f:
            game = [line.split("\t", 1)[0] for line in f if line.strip()]
    g = Gen(args.seed, game)
    if args.suite == "wide":
        wide_suite(g)
        return write(g, args.output)
    if args.suite in ("decimal", "decimal2"):
        decimal_suite(g, long_inputs=args.suite == "decimal2")
        return write(g, args.output)
    g.rand()
    g.parsing()
    g.formatting()
    g.maths()
    g.characters()
    g.locales()
    g.times()
    g.misc()
    write(g, args.output)


def write(g, path):
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="\n") as out:
        for i, (op, fields) in enumerate(g.lines):
            out.write("\t".join([str(i), op] + fields) + "\n")
    print(f"{len(g.lines)} vectors -> {path}")


if __name__ == "__main__":
    main()
