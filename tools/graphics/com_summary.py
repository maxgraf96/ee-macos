"""Summarise windows/frida/com_census.js captures (com_census.json, plus iat_census.json if present)
into Markdown on stdout, and compare them with the static inventory in docs/reference/graphics.md.

  python3 -I tools/graphics/com_summary.py <capture dir> [<capture dir> ...] [--top 60]

Per capture:
- frames: EndScene count, and fps;
- per-method calls per second and per frame, split by caller module and thread;
- render-state, texture-stage and draw-call tables;
- the decoded aggregates (CreateSurface, Lock, Blt, Flip, viewports, transforms, materials, lights,
  DirectInput);
- GDI from iat_census;
- a checklist diff: methods called by game code that are not in the static list, and static-list
  methods never seen.

Pure standard library.
"""
import argparse
import collections
import json
import os
import sys

# Static inventory (docs/reference/graphics.md §4.7 and §8.1): methods the game itself calls.
STATIC = {
    'IDirectDraw7': {'QueryInterface', 'Release', 'CreateSurface', 'EnumDisplayModes', 'GetCaps', 'GetDisplayMode',
                     'SetCooperativeLevel', 'SetDisplayMode', 'GetAvailableVidMem', 'RestoreAllSurfaces',
                     'TestCooperativeLevel', 'GetDeviceIdentifier'},
    'IDirectDrawSurface7': {'Release', 'AddAttachedSurface', 'Blt', 'DeleteAttachedSurface', 'Flip',
                            'GetAttachedSurface', 'GetDC', 'Lock', 'ReleaseDC', 'Restore', 'SetColorKey', 'Unlock'},
    'IDirect3D7': {'Release', 'EnumDevices', 'CreateDevice', 'CreateVertexBuffer'},
    'IDirect3DDevice7': {'Release', 'GetCaps', 'EnumTextureFormats', 'BeginScene', 'EndScene', 'SetRenderTarget',
                         'Clear', 'SetTransform', 'SetViewport', 'SetMaterial', 'SetLight', 'GetLight',
                         'SetRenderState', 'DrawPrimitiveVB', 'DrawIndexedPrimitiveVB', 'SetTexture',
                         'SetTextureStageState', 'ValidateDevice', 'LightEnable'},
    'IDirect3DVertexBuffer7': {'Release', 'Lock', 'Unlock'},
    'IDirectInputA': {'CreateDevice', 'Release'},
    'IDirectInputDeviceA': {'Release', 'GetProperty', 'SetProperty', 'Acquire', 'Unacquire', 'GetDeviceState',
                            'GetDeviceData', 'SetDataFormat', 'SetCooperativeLevel', 'GetObjectInfo'},
}
STATIC_RS = {2, 7, 8, 9, 14, 15, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 34, 36, 37, 41, 136, 137, 138, 139, 140,
             141, 142, 143, 145, 146, 147, 148}
STATIC_TSS = {(0, t) for t in (1, 2, 3, 4, 5, 6, 11, 12, 16, 17, 18, 19)} | {(1, 1), (1, 4)}
GAME_CALLERS = {'empire earth.exe', 'low-level engine.dll', 'dx7hrdisplay.dll', 'dx7hrtnldisplay.dll',
                'binkw32.dll', 'sxlrt233.dll'}
D3DBLEND = {1: 'ZERO', 2: 'ONE', 3: 'SRCCOLOR', 4: 'INVSRCCOLOR', 5: 'SRCALPHA', 6: 'INVSRCALPHA', 7: 'DESTALPHA',
            8: 'INVDESTALPHA', 9: 'DESTCOLOR', 10: 'INVDESTCOLOR', 11: 'SRCALPHASAT'}
D3DCMP = {1: 'NEVER', 2: 'LESS', 3: 'EQUAL', 4: 'LESSEQUAL', 5: 'GREATER', 6: 'NOTEQUAL', 7: 'GREATEREQUAL', 8: 'ALWAYS'}
D3DTOP = {1: 'DISABLE', 2: 'SELECTARG1', 3: 'SELECTARG2', 4: 'MODULATE'}
D3DTA = {0: 'DIFFUSE', 1: 'CURRENT', 2: 'TEXTURE', 3: 'TFACTOR', 4: 'SPECULAR'}
VALUE_NAMES = {
    'SRCBLEND': D3DBLEND, 'DESTBLEND': D3DBLEND, 'ZFUNC': D3DCMP, 'ALPHAFUNC': D3DCMP,
    'CULLMODE': {1: 'NONE', 2: 'CW', 3: 'CCW'}, 'FILLMODE': {1: 'POINT', 2: 'WIREFRAME', 3: 'SOLID'},
    'SHADEMODE': {1: 'FLAT', 2: 'GOURAUD'}, 'FOGVERTEXMODE': {0: 'NONE', 1: 'EXP', 2: 'EXP2', 3: 'LINEAR'},
    'COLOROP': D3DTOP, 'ALPHAOP': D3DTOP, 'COLORARG1': D3DTA, 'COLORARG2': D3DTA, 'ALPHAARG1': D3DTA,
    'ALPHAARG2': D3DTA, 'ADDRESS': {1: 'WRAP', 2: 'MIRROR', 3: 'CLAMP', 4: 'BORDER'},
    'MAGFILTER': {1: 'POINT', 2: 'LINEAR', 3: 'FLATCUBIC', 4: 'GAUSSIANCUBIC', 5: 'ANISOTROPIC'},
    'MINFILTER': {1: 'POINT', 2: 'LINEAR', 3: 'ANISOTROPIC'}, 'MIPFILTER': {1: 'NONE', 2: 'POINT', 3: 'LINEAR'},
}


def load(d, name):
    p = os.path.join(d, name + '.json')
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def vname(state, v):
    if v == 'float':
        return 'float'
    names = VALUE_NAMES.get(state)
    if names and v in names:
        return '%s (%d)' % (names[v], v)
    if isinstance(v, int) and v > 0xffff:
        return '0x%08x' % v
    return str(v)


def fmt_rate(n, secs, frames):
    per_frame = ('%.2f' % (n / frames)) if frames else '-'
    return '%d | %.1f | %s' % (n, n / secs, per_frame)


def summarize(d, top):
    j = load(d, 'com_census')
    out = []
    w = out.append
    if not j or 'rows' not in j:
        return '## %s\n\nno com_census.json\n' % d
    secs = j['elapsedMs'] / 1000.0
    rows = j['rows']
    game = [r for r in rows if r['caller'] in GAME_CALLERS]
    frames = sum(r['count'] for r in game if r['method'] == 'EndScene')
    w('## %s\n' % os.path.basename(os.path.normpath(d)))
    w('- elapsed %.1f s, native table dropped %d, %d vtables hooked: %s' % (
        secs, j['dropped'], len(j['vtables']), ', '.join('%s (%s)' % (v['iface'], v['how']) for v in j['vtables'])))
    w('- frames (EndScene): %d -> %.1f fps\n' % (frames, frames / secs if secs else 0))

    # per method
    per = collections.Counter()
    threads = collections.defaultdict(collections.Counter)
    for r in rows:
        k = (r['iface'], r['method'], r['caller'])
        per[k] += r['count']
        threads[k][r['tid']] += r['count']
    w('### Calls per method\n')
    w('| interface::method | caller | calls | /s | /frame | threads (tid: calls) |')
    w('|---|---|---|---|---|---|')
    for (i, m, c), n in sorted(per.items(), key=lambda x: -x[1])[:top]:
        th = ', '.join('%s: %d' % (t, k) for t, k in threads[(i, m, c)].most_common(4))
        w('| %s::%s | %s | %s | %s |' % (i, m, c, fmt_rate(n, secs, frames), th))
    w('')

    # render states
    rs = collections.Counter()
    for r in game:
        if r['method'] == 'SetRenderState':
            rs[(r.get('a'), r.get('a_name'), r.get('b'))] += r['count']
    if rs:
        w('### SetRenderState (game callers)\n')
        w('| state | value | calls | /s | /frame |')
        w('|---|---|---|---|---|')
        for (a, an, b), n in sorted(rs.items(), key=lambda x: (x[0][0], -x[1])):
            w('| %s (%s) | %s | %s |' % (an, a, vname(an, b), fmt_rate(n, secs, frames)))
        w('')
    tss = collections.Counter()
    for r in game:
        if r['method'] == 'SetTextureStageState':
            tss[(r.get('a'), r.get('b'), r.get('b_name'), r.get('c'))] += r['count']
    if tss:
        w('### SetTextureStageState (game callers)\n')
        w('| stage | type | value | calls | /s | /frame |')
        w('|---|---|---|---|---|---|')
        for (a, b, bn, c), n in sorted(tss.items(), key=lambda x: (x[0][0], x[0][1], -x[1])):
            w('| %s | %s (%s) | %s | %s |' % (a, bn, b, vname(bn, c), fmt_rate(n, secs, frames)))
        w('')
    draws = [r for r in game if 'Primitive' in r['method']]
    if draws:
        w('### Draw calls (game callers)\n')
        w('| method | primitive | flags | calls | /s | /frame | vertices or indices (sum) | per call | per frame |')
        w('|---|---|---|---|---|---|---|---|---|')
        agg = collections.defaultdict(lambda: [0, 0])
        for r in draws:
            k = (r['method'], r.get('a_name'), r.get('b'))
            agg[k][0] += r['count']
            agg[k][1] += r.get('sum', 0)
        for (m, p, f), (n, s) in sorted(agg.items(), key=lambda x: -x[1][0]):
            w('| %s | %s | %s | %s | %d | %.1f | %s |' % (m, p, f, fmt_rate(n, secs, frames), s, s / n,
                                                      ('%.0f' % (s / frames)) if frames else '-'))
        w('')
    other = [r for r in rows if r['method'] in ('SetTransform', 'SetTexture', 'Clear', 'Lock', 'Flip', 'Blt',
                                                 'LightEnable', 'SetLight', 'GetLight', 'SetCooperativeLevel',
                                                 'GetDeviceState', 'GetDeviceData', 'GetProperty', 'SetProperty',
                                                 'GetObjectInfo')]
    if other:
        w('### Argument values of other counted methods\n')
        w('| interface::method | caller | a | b | c | calls | /s | /frame |')
        w('|---|---|---|---|---|---|---|---|')
        for r in sorted(other, key=lambda r: (r['iface'], r['method'], -r['count'])):
            a = r.get('a_name', r.get('a', ''))
            w('| %s::%s | %s | %s | %s | %s | %s |' % (r['iface'], r['method'], r['caller'], a, r.get('b', ''),
                                                    r.get('c', ''), fmt_rate(r['count'], secs, frames)))
        w('')

    # aggregates
    if j.get('aggregates'):
        w('### Decoded aggregates\n')
        for name, items in j['aggregates'].items():
            w('**%s**\n' % name)
            for it in items[:25]:
                w('- %d × `%s`%s' % (it['count'], it['sig'], (' (%s)' % it['caller']) if it.get('caller') else ''))
            if len(items) > 25:
                w('- … %d more' % (len(items) - 25))
            w('')

    # selected events
    kinds = collections.Counter()
    notable = []
    for e in j.get('events', []):
        kinds[e['kind']] += 1
        if e['kind'] in ('DirectDrawCreateEx', 'SetCooperativeLevel', 'SetDisplayMode', 'IDirect3D7::CreateDevice',
                         'DirectInputCreateA', 'IDirectInputA::CreateDevice', 'DI SetCooperativeLevel',
                         'DirectDrawEnumerateExA', 'DirectDrawEnumerateA', 'hook-error', 'decoder-error',
                         'discover-error', 'discovered'):
            notable.append(e)
    w('### Events\n')
    w('- kinds: ' + ', '.join('%s %d' % kv for kv in kinds.most_common()))
    for e in notable[:40]:
        w('- `%s`' % json.dumps({k: v for k, v in e.items() if k != 'bt'}))
    w('')

    # GDI from iat_census
    iat = load(d, 'iat_census')
    if iat and 'rows' in iat:
        isecs = iat['elapsedMs'] / 1000.0
        c = collections.Counter()
        for r in iat['rows']:
            if r['dll'].startswith('gdi32') or (r['dll'].startswith('user32') and r['name'] in ('GetDC', 'ReleaseDC')) \
                    or r['dll'].startswith('binkw32') or r['dll'].startswith('ddraw'):
                c[(r['module'], r['dll'], r['name'])] += r['count']
        w('### GDI, Bink and DDRAW imports (iat_census, %.1f s)\n' % isecs)
        if not c:
            w('none called')
        else:
            w('| module | dll | function | calls | /s | /frame (com_census fps) |')
            w('|---|---|---|---|---|---|')
            fps = frames / secs if secs else 0
            for (m, dll, n), k in c.most_common():
                w('| %s | %s | %s | %d | %.1f | %s |' % (m, dll, n, k, k / isecs,
                                                      ('%.2f' % (k / isecs / fps)) if fps else '-'))
        w('')

    # checklist diff
    seen = collections.defaultdict(set)
    for r in game:
        seen[r['iface']].add(r['method'])
    w('### Against the static inventory (game callers only)\n')
    extra = ['%s::%s' % (i, m) for i in sorted(seen) for m in sorted(seen[i]) if m not in STATIC.get(i, set())]
    w('- called but not in the static list: ' + (', '.join(extra) or 'none'))
    hooked = {v['iface'] for v in j['vtables']}
    missing = ['%s::%s' % (i, m) for i in sorted(STATIC) if i in hooked for m in sorted(STATIC[i]) if m not in seen[i]]
    w('- in the static list, interface hooked, but not called in this capture: ' + (', '.join(missing) or 'none'))
    rs_seen = {r.get('a') for r in game if r['method'] == 'SetRenderState'}
    w('- render states set but not in the static list: %s' % (sorted(rs_seen - STATIC_RS) or 'none'))
    w('- static render states not set in this capture: %s' % sorted(STATIC_RS - rs_seen))
    tss_seen = {(r.get('a'), r.get('b')) for r in game if r['method'] == 'SetTextureStageState'}
    w('- texture stage states set but not in the static list: %s' % (sorted(tss_seen - STATIC_TSS) or 'none'))
    w('')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--top', type=int, default=60)
    a = ap.parse_args()
    sys.stdout.write('# COM census summary\n\n')
    for d in a.dirs:
        sys.stdout.write(summarize(d, a.top) + '\n')


if __name__ == '__main__':
    main()
