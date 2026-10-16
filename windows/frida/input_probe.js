// input_probe.js (P7-C, legion request 0080; docs/reference/performance.md): keyboard, hotkey, sound and frame-rate probe.
//
// Attach in game (windows/frida/run.py --scripts input_probe --seconds N --out DIR); not --spawn.
// Hooks (GOG Empire Earth.exe at 0x400000, Low-Level Engine.dll by export name):
//   - every SESoundManager::Play / PlayAndReturnReferencedNode: [ms, tid, "play", soundId, caller]
//   - the game window's WndProc (EXE 0x6622dd): WM_KEYDOWN/KEYUP/CHAR/SYSKEYDOWN/SYSKEYUP/
//     IME_CHAR/IME_COMPOSITION as [ms, tid, "msg", msg, wParam, lParam]
//   - UIManager::UpdateTranslatedKey (the WndProc's WM_CHAR target): [ms, tid, "char", byte]
//   - call counts per 100 ms bucket of:
//       scroll  EXE 0x5c9070  EEUIEKeyboardScroll fire (one camera step per call)
//       zoomkey EXE 0x5c913c  EEUIEHotKeyZoom fire
//       air     EXE 0x5d9090  EEUIESelectClosestAircraft fire (A, B, D, F)
//       ui      EXE 0x661889  the main thread's input + UI step (once per main-loop pass)
//       render  EXE 0x51015d  RTSGraphics::Render (one per rendered frame)
//       kbd     ISKeyboard::Refresh (engine export)
//   - the camera (EXE 0x918cd0, 0x110 bytes as floats) sampled every 50 ms: [ms, "cam", [floats]]
// rpc: snapshot() -> everything; camera() -> the camera floats now; mark(label) -> an event.
'use strict';

const P = Object.assign({ max_events: 50000, cam_ms: 50 }, globalThis.PARAMS || {});
const t0 = Date.now();
const exe = Process.getModuleByName('Empire Earth.exe');
const engine = Process.getModuleByName('low-level engine.dll');
const X = (va) => exe.base.add(va - 0x400000);
const events = [];
const buckets = {}; // name -> {bucket index: count}
const totals = {};
function ms() { return Date.now() - t0; }
function log(e) { if (events.length < P.max_events) events.push(e); }
function tick(name) {
  totals[name] = (totals[name] || 0) + 1;
  const b = buckets[name] || (buckets[name] = {});
  const i = Math.floor(ms() / 100);
  b[i] = (b[i] || 0) + 1;
}
function caller(ctx) {
  try {
    const r = ctx.returnAddress;
    const m = Process.findModuleByAddress(r);
    return m ? m.name + '+0x' + r.sub(m.base).toString(16) : r.toString();
  } catch (e) { return '?'; }
}

for (const name of ['?Play@SESoundManager@@QAEKKMMM_NPAD@Z',
                    '?PlayAndReturnReferencedNode@SESoundManager@@QAEPAVSESMInstanceNode@@KMMM_NPAD@Z']) {
  const a = engine.findExportByName(name);
  if (!a) { send({ type: 'missing', name }); continue; }
  const short = name.indexOf('PlayAndReturn') >= 0 ? 'playref' : 'play';
  Interceptor.attach(a, {
    onEnter(args) { log([ms(), Process.getCurrentThreadId(), short, args[0].toUInt32(), caller(this)]); },
  });
}

const WANT = { 0x100: 1, 0x101: 1, 0x102: 1, 0x104: 1, 0x105: 1, 0x286: 1, 0x10f: 1 };
Interceptor.attach(X(0x6622dd), {
  onEnter(args) {
    const msg = args[1].toUInt32();
    if (WANT[msg]) log([ms(), Process.getCurrentThreadId(), 'msg', msg, args[2].toUInt32(), args[3].toUInt32()]);
  },
});
{
  const a = engine.findExportByName('?UpdateTranslatedKey@UIManager@@QAEJPBDF@Z');
  if (a) Interceptor.attach(a, {
    onEnter(args) { let c = -1; try { c = args[0].readU8(); } catch (e) { } log([ms(), Process.getCurrentThreadId(), 'char', c]); },
  });
}
for (const [name, va] of [['scroll', 0x5c9070], ['zoomkey', 0x5c913c], ['air', 0x5d9090],
                          ['ui', 0x661889], ['render', 0x51015d]]) {
  Interceptor.attach(X(va), { onEnter() { tick(name); } });
}
{
  const a = engine.findExportByName('?Refresh@ISKeyboard@@UAEXXZ');
  if (a) Interceptor.attach(a, { onEnter() { tick('kbd'); } });
}

function camera() {
  const p = X(0x918cd0);
  const out = [];
  for (let i = 0; i < 0x110; i += 4) out.push(Math.round(p.add(i).readFloat() * 1e5) / 1e5);
  return out;
}
const cams = [];
setInterval(() => { if (cams.length < 20000) cams.push([ms(), camera()]); }, P.cam_ms);

send({ type: 'hooked', t0Epoch: t0, exe: exe.base.toString(), engine: engine.base.toString() });

function snapshot() {
  return { t0Epoch: t0, elapsedMs: ms(), totals, buckets, events, cams };
}
function mark(label) { log([ms(), Process.getCurrentThreadId(), 'mark', String(label)]); return ms(); }
rpc.exports = { snapshot, camera, mark };
