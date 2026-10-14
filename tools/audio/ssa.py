# Own reader for Stainless Steel 'rass' archives (index + attributes); decompresses
# selected entries in memory via zlib's contrib/blast. Writes nothing to disk.
import sys, struct, ctypes, os, collections
LIB = ctypes.CDLL(os.path.join(os.path.dirname(__file__), '..', 'build', 'libdcl.dylib'))
def dcl(data, cap):
    out = ctypes.create_string_buffer(cap); n = ctypes.c_uint(0)
    rc = LIB.dcl_decompress(data, len(data), out, cap, ctypes.byref(n))
    return rc, out.raw[:n.value]
class SSA:
    def __init__(self, path):
        self.f = open(path, 'rb'); f = self.f
        magic, vmaj, vmin, idxlen = struct.unpack('<4sIII', f.read(16)); assert magic == b'rass'
        self.version = (vmaj, vmin); self.entries = []
        end = 16 + idxlen
        while f.tell() < end:
            n = struct.unpack('<I', f.read(4))[0]; name = f.read(n).rstrip(b'\0').decode('latin-1')
            start, stop, size = struct.unpack('<III', f.read(12))
            self.entries.append((name, start, stop, size))
        cnt = struct.unpack('<I', f.read(4))[0]; self.attrs = []
        for _ in range(cnt):
            k = f.read(struct.unpack('<I', f.read(4))[0]).rstrip(b'\0'); v = f.read(struct.unpack('<I', f.read(4))[0]).rstrip(b'\0')
            self.attrs.append((k.decode('latin-1'), v.decode('latin-1')))
        self.data_start = f.tell()
    def raw(self, e):
        self.f.seek(e[1]); return self.f.read(e[3])
    def read(self, e, cap=None):
        r = self.raw(e)
        if r[:4] != b'PK01': return 'stored', len(r), r[:cap] if cap else r
        usize, unk = struct.unpack_from('<II', r, 4)
        rc, out = dcl(r[12:], cap or usize)
        return f'PK01(unk={unk})', usize, out
if __name__ == '__main__':
    a = SSA(sys.argv[1])
    print('version', a.version, 'entries', len(a.entries), 'attrs', a.attrs, 'data_start', a.data_start)
    ext = collections.Counter(os.path.splitext(e[0])[1].lower() for e in a.entries)
    top = collections.Counter(e[0].split('\\')[0].lower() for e in a.entries)
    print('extensions', ext.most_common(25)); print('top dirs', top.most_common(25))
    print('first entry start', min(e[1] for e in a.entries), 'contiguous inclusive end?', all(e[2] == e[1] + e[3] - 1 for e in a.entries))
