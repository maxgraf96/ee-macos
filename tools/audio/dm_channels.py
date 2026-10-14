# For each EE segment: band instruments per PChannel (DLS ref vs GM/GS) and which PChannels
# the style's parts actually play notes on. Reads structure only.
import sys, os, struct, uuid, collections
def chunks(buf, off, end):
    while off + 8 <= end:
        ck = buf[off:off+4]; size = struct.unpack_from('<I', buf, off+4)[0]; body = off + 8
        form = buf[body:body+4] if ck in (b'RIFF', b'LIST') else None
        yield ck, form, body, size
        off = body + size + (size & 1)
def find_all(buf, off, end, pred, out):
    for ck, form, body, size in chunks(buf, off, end):
        if pred(ck, form): out.append((ck, form, body, size))
        if form: find_all(buf, body+4, body+size, pred, out)
def bands(buf):
    res = []
    lst = []; find_all(buf, 0, len(buf), lambda c, f: f == b'lbin', lst)
    for _, _, body, size in lst:
        ins = None; ref = None
        for ck, form, b2, s2 in chunks(buf, body+4, body+size):
            if ck == b'bins':
                patch, assign = struct.unpack_from('<II', buf, b2)
                pch, flags = struct.unpack_from('<II', buf, b2+24)
                ins = (pch, patch, flags)
            if form == b'DMRF':
                for c3, f3, b3, s3 in chunks(buf, b2+4, b2+s2):
                    if c3 == b'file': ref = buf[b3:b3+s3].decode('utf-16le').split('\0')[0]
        res.append((ins, ref))
    return res
def style_note_channels(buf):
    parts = {}  # guid -> note count
    lst = []; find_all(buf, 0, len(buf), lambda c, f: f == b'part', lst)
    for _, _, body, size in lst:
        g = None; n = 0
        for ck, form, b2, s2 in chunks(buf, body+4, body+size):
            if ck == b'prth': g = buf[b2+132:b2+148]
            if ck == b'note':
                isz = struct.unpack_from('<I', buf, b2)[0]; n += (s2 - 4) // isz
        parts[g] = parts.get(g, 0) + n
    used = collections.Counter()
    lst = []; find_all(buf, 0, len(buf), lambda c, f: c == b'prfc', lst)
    for _, _, body, size in lst:
        g = buf[body:body+16]; pch = struct.unpack_from('<H', buf, body+16)[0]
        used[pch] += parts.get(g, 0)
    return used
d = sys.argv[1]
segs = {'Balance':'Balance.sty','Enmity':'Enmity.sty','Fallen':'Fallen.sty','Honor':'Honor.sty','Pawns':'Pawn-Style.sty','Rivers':'Rivers.sty','Shadow2':'Shadows.sty','Time':'Time.sty'}
for s, sty in segs.items():
    sb = open(os.path.join(d, s + '.sgt'), 'rb').read(); yb = open(os.path.join(d, sty), 'rb').read()
    used = style_note_channels(yb)
    gm = []
    for (pch, patch, flags), ref in bands(sb):
        if ref is None:
            kind = 'GS' if flags & 0x200 else 'GM' if flags & 0x100 else 'none'
            prog = patch & 0x7f; bank = (patch >> 8) & 0x7f, (patch >> 16) & 0x7f; drum = bool(patch & 0x80000000)
            gm.append(f'pch{pch}:{kind} prog{prog}{" drum" if drum else ""} flags={flags:#x} notes={used.get(pch,0)}')
    print(f'{s}: note pchannels={dict(sorted(used.items()))}')
    print('   non-DLS band entries:', '; '.join(gm) or '-')
