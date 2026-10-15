#!/usr/bin/env python3
"""Read an Empire Earth save game (.ees): its header and its compressed sections (P4-E).

    .venv/bin/python tools/simparity/savefile.py SAVE.ees [--dump DIR]
    .venv/bin/python tools/simparity/savefile.py A.ees B.ees [--context N]

The format, as far as the save path (EXE 0x540aca) and the files show it
(docs/reference/simparity.md §10):

    u32  size of the rest of the file
    str  the save's file name ("AutoSave <date> - <time>.ees")
    str  the description ("No game description provided.")
    ...  the players' names, the time of the save as ctime() text, the game's
         settings
    then sections:  u32 0xe7, u32 id, u32 length, then `length` bytes that
         start "PK01", u32 the unpacked size, u32 0, and a PKWARE DCL
         ("implode") stream

str = u32 length (with the NUL) + the bytes. The sections are unpacked with a
port of zlib's contrib/blast.c (PKWARE DCL explode, Mark Adler).

With one file: its header strings and sections (id, packed and unpacked size).
--dump DIR writes each unpacked section to DIR/<id>.bin.
With two files: where they differ: the header (the strings and the bytes
around), then each section by id, unpacked, as the first differing offset and
the count of differing bytes. Exit status: 0 the same past the header's
strings, 1 they differ, 2 unreadable.
Save files are game data: they stay out of git (analysis/ is ignored).
"""
import argparse
import struct
import sys
from pathlib import Path

SECTION_TAG = 0xE7
MAXBITS = 13

# blast.c's code length tables, run-length coded: (repeat - 1) << 4 | length.
LITLEN = bytes([11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8, 9, 7, 6, 7, 8, 7, 6,
                55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5, 7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7,
                9, 8, 25, 11, 8, 11, 9, 12, 8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24,
                10, 27, 44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44, 173])
LENLEN = bytes([2, 35, 36, 53, 38, 23])
DISTLEN = bytes([2, 20, 53, 230, 247, 151, 248])
BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)


class Huffman:
    def __init__(self, rep):
        lengths = []
        for b in rep:
            lengths += [b & 15] * ((b >> 4) + 1)
        self.count = [0] * (MAXBITS + 1)
        for n in lengths:
            self.count[n] += 1
        offs = [0] * (MAXBITS + 2)
        for n in range(1, MAXBITS):
            offs[n + 1] = offs[n] + self.count[n]
        self.symbol = [0] * len(lengths)
        for sym, n in enumerate(lengths):
            if n:
                self.symbol[offs[n]] = sym
                offs[n] += 1


LIT, LEN, DIST = Huffman(LITLEN), Huffman(LENLEN), Huffman(DISTLEN)


class Bits:
    def __init__(self, data, pos=0):
        self.data, self.pos, self.buf, self.cnt = data, pos, 0, 0

    def get(self, need):
        while self.cnt < need:
            if self.pos >= len(self.data):
                raise ValueError("out of input")
            self.buf |= self.data[self.pos] << self.cnt
            self.pos += 1
            self.cnt += 8
        val = self.buf & ((1 << need) - 1)
        self.buf >>= need
        self.cnt -= need
        return val

    def decode(self, h):
        # The codes are stored bit-reversed and complemented (blast.c decode()).
        code = first = index = 0
        for n in range(1, MAXBITS + 1):
            code |= self.get(1) ^ 1
            count = h.count[n]
            if code < first + count:
                return h.symbol[index + code - first]
            index += count
            first += count
            first <<= 1
            code <<= 1
        raise ValueError("bad code")


def explode(data, pos=0):
    """PKWARE DCL explode (blast.c); returns the unpacked bytes."""
    s = Bits(data, pos)
    lit = s.get(8)
    if lit > 1:
        raise ValueError("bad literal flag %d" % lit)
    dict_bits = s.get(8)
    if not 4 <= dict_bits <= 6:
        raise ValueError("bad dictionary size %d" % dict_bits)
    out = bytearray()
    while True:
        if s.get(1):
            sym = s.decode(LEN)
            length = BASE[sym] + s.get(EXTRA[sym])
            if length == 519:
                return bytes(out)
            shift = 2 if length == 2 else dict_bits
            dist = (s.decode(DIST) << shift) + s.get(shift) + 1
            if dist > len(out):
                raise ValueError("distance too far back")
            for _ in range(length):
                out.append(out[-dist])
        else:
            out.append(s.decode(LIT) if lit else s.get(8))


class Save:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        d = self.data
        if len(d) < 8 or struct.unpack_from("<I", d, 0)[0] != len(d) - 4:
            raise ValueError("%s: not a save (size field)" % path)
        self.strings = []  # (offset, text) of the header's length-prefixed strings
        self.sections = []  # (offset, id, packed bytes, unpacked bytes)
        first = self._find_sections()
        self.header = d[:first]
        self._find_strings(self.header)

    def _find_sections(self):
        d, out, pos = self.data, [], None
        i = d.find(b"PK01")
        while i >= 12:
            tag, sid, length = struct.unpack_from("<III", d, i - 12)
            if tag != SECTION_TAG:
                i = d.find(b"PK01", i + 4)
                continue
            if pos is None:
                pos = i - 12
            size = struct.unpack_from("<I", d, i + 4)[0]
            body = explode(d, i + 12)
            if len(body) != size:
                raise ValueError("%s: section %d unpacked to %d bytes, not %d" % (self.path, sid, len(body), size))
            self.sections.append((i - 12, sid, d[i:i + length], body))
            i = d.find(b"PK01", i + length)
        return pos if pos is not None else len(d)

    def _find_strings(self, h):
        """Length-prefixed printable strings in the header: u32 n, then n bytes ending in NUL."""
        i = 4
        while i + 4 <= len(h):
            n = struct.unpack_from("<I", h, i)[0]
            if 2 <= n <= 256 and i + 4 + n <= len(h) and h[i + 3 + n] == 0 and \
                    all(32 <= c < 127 or c in (9, 10) for c in h[i + 4:i + 3 + n]):
                self.strings.append((i, h[i + 4:i + 3 + n].decode("latin-1")))
                i += 4 + n
            else:
                i += 1


def first_difference(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return None if len(a) == len(b) else n


def describe(s):
    print("%s: %d bytes, header %d bytes" % (s.path, len(s.data), len(s.header)))
    for off, text in s.strings:
        print("  str @%#x: %r" % (off, text))
    for off, sid, packed, body in s.sections:
        print("  section %d @%#x: %d bytes packed, %d unpacked" % (sid, off, len(packed), len(body)))


def compare(a, b, context=16):
    same = True
    sa, sb = [t for _, t in a.strings], [t for _, t in b.strings]
    for i, (x, y) in enumerate(zip(sa, sb)):
        if x != y:
            print("header string %d: %r / %r" % (i, x, y))
    if len(sa) != len(sb):
        print("header strings: %d / %d" % (len(sa), len(sb)))
        same = False
    # The header's bytes with the strings taken out (their lengths and text are above).
    def rest(s):
        out, last = bytearray(), 4
        for off, text in s.strings:
            out += s.header[last:off]
            last = off + 4 + len(text) + 1
        return bytes(out + s.header[last:])
    ra, rb = rest(a), rest(b)
    d = first_difference(ra, rb)
    if d is None:
        print("header, without its strings: the same (%d bytes)" % len(ra))
    else:
        same = False
        diff = sum(1 for x, y in zip(ra, rb) if x != y) + abs(len(ra) - len(rb))
        print("header, without its strings: %d / %d bytes, %d differ, first at %d:" % (len(ra), len(rb), diff, d))
        print("   A %s" % ra[max(0, d - context):d + context].hex())
        print("   B %s" % rb[max(0, d - context):d + context].hex())
    ida = [(sid, body) for _, sid, _, body in a.sections]
    idb = [(sid, body) for _, sid, _, body in b.sections]
    if [x for x, _ in ida] != [x for x, _ in idb]:
        print("sections: %s / %s" % ([x for x, _ in ida], [x for x, _ in idb]))
        same = False
    for (sid, x), (_, y) in zip(ida, idb):
        d = first_difference(x, y)
        if d is None:
            print("section %d: the same (%d bytes unpacked)" % (sid, len(x)))
            continue
        same = False
        diff = sum(1 for p, q in zip(x, y) if p != q) + abs(len(x) - len(y))
        print("section %d: %d / %d bytes, %d differ, first at %#x" % (sid, len(x), len(y), diff, d))
        print("   A %s" % x[max(0, d - context):d + context].hex())
        print("   B %s" % y[max(0, d - context):d + context].hex())
    return same


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--dump", type=Path, default=None, metavar="DIR")
    parser.add_argument("--context", type=int, default=16)
    args = parser.parse_args()
    try:
        saves = [Save(p) for p in args.files]
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    if len(saves) == 1:
        describe(saves[0])
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            for _, sid, _, body in saves[0].sections:
                (args.dump / ("%d.bin" % sid)).write_bytes(body)
        return 0
    for s in saves[:2]:
        describe(s)
    return 0 if compare(saves[0], saves[1], args.context) else 1


if __name__ == "__main__":
    sys.exit(main())
