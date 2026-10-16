// Performance probe (P7-B) for Empire Earth (GOG 2.00) on Windows, for windows/frida/run.py:
// the Windows baselines the Mac's hot-path work is measured against (docs/reference/performance.md).
//
//   run.py --scripts perf_probe --seconds 600 --out C:\EEPort\captures\perf\NAME ^
//          --param out=C:\EEPort\captures\perf\NAME\probe.csv
//   (attach at the main menu; then start or load the game by hand or by session.ps1)
//
// Rows (CSV, written straight to PARAMS.out; times are QueryPerformanceCounter milliseconds since
// the script loaded):
//   # ...                     comments: what was found, the counter frequency
//   O,t_ms,text               every OutputDebugStringA (the game prints its own load phases:
//                             "EERandomMap::PaintTiles Processing completed in  N s", ...)
//   F,t_ms,n,dt_ms            every IDirectDrawSurface7::Flip the game makes (the GOG wrapper's
//                             method, hooked once by address, so every surface's Flip counts):
//                             the running count and the time since the previous one
//   S,t_ms,epoch_ms,pts,flips every PARAMS.interval_ms: the wall clock (Date.now(), to line a
//                             click's time up with these rows), the PTs executed so far
//                             (EEPhysics+0xb4, EEPhysics at [0x919774]; -1 with no game) and the
//                             Flip count
//
// PARAMS: out (required), interval_ms (default 250), flips (bool, default true: F rows; with
//         false only the count in S rows).
// Nothing in the game is changed: the hooks only read.
'use strict';

const P = Object.assign({ out: null, interval_ms: 250, flips: true }, globalThis.PARAMS || {});
if (!P.out) throw new Error('perf_probe: PARAMS.out is required');

const k32 = Process.getModuleByName('kernel32.dll');
const qpc = new NativeFunction(k32.getExportByName('QueryPerformanceCounter'), 'int', ['pointer']);
const qpf = new NativeFunction(k32.getExportByName('QueryPerformanceFrequency'), 'int', ['pointer']);
const cell = Memory.alloc(8);
qpf(cell);
const freq = cell.readU64().toNumber();
qpc(cell);
const t0 = cell.readU64().toNumber();
function now() {
  qpc(cell);
  return (cell.readU64().toNumber() - t0) * 1000 / freq;
}

const file = new File(P.out, 'w');
let rows = 0;
function emit(line) { file.write(line + '\n'); rows++; }
function flush() { try { file.flush(); } catch (e) { /* closed */ } }
emit('# perf_probe: QPC frequency ' + freq + ' Hz');

// OutputDebugStringA: the game's own phase timings.
Interceptor.attach(k32.getExportByName('OutputDebugStringA'), {
  onEnter(args) {
    let s = '';
    try { s = args[0].readCString() || ''; } catch (e) { s = '?'; }
    emit('O,' + now().toFixed(3) + ',' + JSON.stringify(s.replace(/\s+$/, '')));
  },
});

// Flip: the GOG wrapper's IDirectDrawSurface7::Flip (vtable slot 11), found through the engine's
// active rasterizer as com_census.js does (state +0x2ac front GESurface, +0x48 the surface).
let flips = 0, lastFlip = -1, flipHooked = null;
function findFlip() {
  const eng = Process.findModuleByName('Low-Level Engine.dll');
  if (!eng) return null;
  const sar = eng.findExportByName('?sActiveRasterizer@GERasterizer@@1PAV1@A');
  const r = sar ? sar.readPointer() : NULL;
  if (r.isNull()) return null;
  const sb = r.add(0x20).readPointer(), se = r.add(0x24).readPointer();
  for (let p = sb, i = 0; !sb.isNull() && p.compare(se) < 0 && i < 8; p = p.add(4), i++) {
    const st = p.readPointer();
    if (st.isNull()) continue;
    const gs = st.add(0x2ac).readPointer();
    if (gs.isNull()) continue;
    const s = gs.add(0x48).readPointer();
    if (s.isNull()) continue;
    return s.readPointer().add(11 * 4).readPointer();
  }
  return null;
}
function hookFlip() {
  if (flipHooked) return;
  let fn = null;
  try { fn = findFlip(); } catch (e) { fn = null; }
  if (!fn) return;
  flipHooked = fn;
  emit('# perf_probe: Flip at ' + fn + ' (' + (Process.findModuleByAddress(fn) || { name: '?' }).name + ')');
  Interceptor.attach(fn, {
    onEnter() {
      const t = now();
      flips++;
      if (P.flips) emit('F,' + t.toFixed(3) + ',' + flips + ',' + (lastFlip < 0 ? 0 : t - lastFlip).toFixed(3));
      lastFlip = t;
    },
  });
}

// The PT counter.
const exe = Process.getModuleByName('empire earth.exe');
const physicsCell = exe.base.add(0x919774 - 0x400000);
function pts() {
  try {
    const ph = physicsCell.readPointer();
    return ph.isNull() ? -1 : ph.add(0xb4).readU32();
  } catch (e) { return -1; }
}

hookFlip();
const timer = setInterval(() => {
  hookFlip();
  emit('S,' + now().toFixed(3) + ',' + Date.now() + ',' + pts() + ',' + flips);
  flush();
}, P.interval_ms);

rpc.exports = {
  snapshot() { return { rows, flips, flip: flipHooked ? flipHooked.toString() : null, pts: pts() }; },
  stop() { clearInterval(timer); Interceptor.detachAll(); flush(); try { file.close(); } catch (e) { /* closed */ } },
};
