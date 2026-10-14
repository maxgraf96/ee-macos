# Minimal RIFF tree dumper for DirectMusic files (.sgt/.sty/.dls). Prints structure,
# track headers and reference descriptors; never dumps payload bytes.
import sys, struct, uuid
CONTAINERS = {b'RIFF', b'LIST'}
def guid(b): return '{' + str(uuid.UUID(bytes_le=b)).upper() + '}'
def utf16z(b): return b.decode('utf-16le', 'replace').split('\0')[0]
def walk(buf, off, end, depth, out, maxdepth):
    while off + 8 <= end:
        ck = buf[off:off+4]; size = struct.unpack_from('<I', buf, off+4)[0]
        body = off + 8; nxt = body + size + (size & 1)
        ind = '  ' * depth
        if ck in CONTAINERS:
            form = buf[body:body+4]
            out.append(f'{ind}{ck.decode()} {form.decode(errors="replace")} ({size})')
            if depth < maxdepth: walk(buf, body+4, min(body+size, end), depth+1, out, maxdepth)
        else:
            extra = ''
            d = buf[body:body+size]
            if ck == b'trkh' and size >= 32:
                cls, pos, grp, ckid, fcc = struct.unpack_from('<16sIIII', d)
                extra = f' class={guid(cls)} group={grp:#x} ckid={struct.pack("<I",ckid)!r} fcc={struct.pack("<I",fcc)!r}'
            elif ck == b'trkX' and size >= 8:
                extra = ' flags=%#x/%#x' % struct.unpack_from('<II', d)
            elif ck == b'refh' and size >= 20:
                cls, valid = struct.unpack_from('<16sI', d); extra = f' class={guid(cls)} valid={valid:#x}'
            elif ck in (b'guid',) and size == 16: extra = ' ' + guid(d)
            elif ck in (b'name', b'file', b'UNAM', b'UCAT', b'UCOP', b'UFIL'): extra = ' "' + utf16z(d) + '"'
            elif ck == b'segh' and size >= 24:
                rep, mtlen, mtplay, mtls, mtle, res = struct.unpack_from('<IiiiiI', d)
                extra = f' repeats={rep} mtLength={mtlen} mtPlayStart={mtplay} loop=[{mtls},{mtle}] resolution={res:#x}'
                if size >= 40:
                    rtlen, flags = struct.unpack_from('<qI', d, 24); extra += f' rtLength={rtlen} flags={flags:#x}'
            elif ck == b'cmnd' and size >= 4:
                isz = struct.unpack_from('<I', d)[0]; items = []
                for p in range(4, size, isz):
                    mt, meas, beat, cmd, groove, grange = struct.unpack_from('<iHBBBB', d, p)[:6] if isz >= 10 else (0,0,0,0,0,0)
                    items.append(f'(m{meas} b{beat} cmd{cmd} groove{groove}±{grange})')
                extra = ' ' + ' '.join(items[:24]) + (' ...' if len(items) > 24 else '')
            elif ck == b'tetr' and size >= 4:
                isz = struct.unpack_from('<I', d)[0]
                items = [struct.unpack_from('<i4xd', d, p) for p in range(4, size, isz)]
                extra = ' tempos=' + ','.join(f'{t:.1f}@{m}' for m, t in items[:12])
            elif ck == b'ptnh' and size >= 10:
                # DMUS_IO_PATTERN: timesig(4: beatsPerMeasure u8, beat u8, gridsPerBeat u16), bGrooveBottom, bGrooveTop, wEmbellishment, wNbrMeasures, ...
                bpm, beat, gpb, gb, gt, emb, nm = struct.unpack_from('<BBHBBHH', d)
                extra = f' groove={gb}-{gt} embellishment={emb:#x} measures={nm}'
            elif ck == b'styh' and size >= 12:
                bpm, beat, gpb, tempo = struct.unpack_from('<BBHd', d); extra = f' timesig={bpm}/{beat} grids={gpb} tempo={tempo}'
            elif ck == b'vers' and size >= 8:
                hi, lo = struct.unpack_from('<II', d); extra = f' {hi>>16}.{hi&0xffff}.{lo>>16}.{lo&0xffff}'
            out.append(f'{ind}{ck.decode(errors="replace")} ({size}){extra}')
        off = nxt
def main():
    maxdepth = int(sys.argv[2]) if len(sys.argv) > 2 else 99
    buf = open(sys.argv[1], 'rb').read(); out = []
    walk(buf, 0, len(buf), 0, out, maxdepth); print('\n'.join(out))
main()
