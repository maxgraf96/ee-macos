// Game-function maths vectors: call the engine's UM* functions, msvcrt maths/parsing entry points
// and selected EXE helpers inside the running game with inputs from a file, and write the exact
// results (ST(0) as 80 bits, EAX/EDX, out-buffers, CW/SW after the call) as JSON Lines.
// Vector and result formats: tests/x87/x87ops.py ("call" vectors from tests/x87/gen_vectors.py
// --suite um). The Mac side runs the same file through tests/x87/um_unicorn.py / um_model.py,
// and tests/x87/compare.py diffs the two.
//
// The calls run on Frida's own JS thread through a small x86 stub (the same bytes as
// x87ops.build_call_stub): it sets the vector's x87 control word, pushes the arguments, calls,
// stores the results and restores the thread's control word (FNINIT first, so every call starts
// with an empty x87 stack). Game threads and game state are not touched; the UM* functions only
// read constant tables. Attach at the main menu is enough.
//
// PARAMS: in   = vectors file on legion (JSON Lines), required
//         out  = results file (default: <in>.results.jsonl)
//         chunk = vectors per timer tick (default 500), limit = stop after N vectors
// run.py: --scripts ummath_vectors --seconds 120 --param in=C:\\EEPort\\x87\\um.jsonl
//         --param out=C:\\EEPort\\x87\\um-legion.jsonl   (snapshot() reports progress; re-run
//         with a larger --seconds if "finished" is false)
'use strict';

const P = Object.assign({ chunk: 500, limit: 0 }, globalThis.PARAMS || {});
if (!P.in) throw new Error('ummath_vectors: --param in=<vectors.jsonl> is required');
if (!P.out) P.out = P.in.replace(/\.jsonl$/, '') + '.results.jsonl';

const k32 = Process.getModuleByName('kernel32.dll');
const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer',
  ['pointer', 'uint', 'uint32', 'uint32'], 'stdcall');

// ---- block layout (x87ops.py CB_*)
const CB = { FN: 0x00, CW_IN: 0x04, CW_SAVED: 0x06, CW_AFTER: 0x08, SW_AFTER: 0x0A, EAX: 0x0C, EDX: 0x10,
  ST0: 0x18, SW_BEFORE: 0x24, ARGS: 0x30, BUFS: 0x70, THIS: 0xF0, STRS: 0x110, STR_LEN: 0x40, SIZE: 0x220 };

const code = VirtualAlloc(NULL, 0x10000, 0x3000, 0x40);   // RWX, never freed
let codeOff = 0;
const block = VirtualAlloc(NULL, 0x1000, 0x3000, 0x04);

function d32(v) { return [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]; }

// JS twin of x87ops.build_call_stub
function buildStub(sig) {
  const [conv, ret, args] = sig.split(':');
  let b = [0x55, 0x8b, 0xec, 0x53, 0x56, 0x57, 0x8b, 0x5d, 0x08];
  b = b.concat([0xd9, 0xbb], d32(CB.CW_SAVED), [0xd9, 0xab], d32(CB.CW_IN), [0xdb, 0xe2],
    [0xdf, 0xe0, 0x66, 0x89, 0x83], d32(CB.SW_BEFORE));
  if (conv === 'i') {
    for (let k = 0; k < args.length; k++) {
      if (args[k] !== 'd') throw new Error('x87-stack args must be doubles: ' + sig);
      b = b.concat([0xdd, 0x83], d32(CB.ARGS + 8 * k));
    }
  } else {
    let nbuf = 0, nstr = 0;
    const plan = [];
    for (let k = 0; k < args.length; k++) {
      const t = args[k];
      if (t === 'p' || t === 'P') plan.push([t, CB.BUFS + 16 * nbuf++]);
      else if (t === 's') plan.push([t, CB.STRS + CB.STR_LEN * nstr++]);
      else plan.push([t, CB.ARGS + 8 * k]);
    }
    for (let k = plan.length - 1; k >= 0; k--) {
      const [t, off] = plan[k];
      if (t === 'f' || t === 'i') b = b.concat([0xff, 0xb3], d32(off));
      else if (t === 'd') b = b.concat([0xff, 0xb3], d32(off + 4), [0xff, 0xb3], d32(off));
      else b = b.concat([0x8d, 0x83], d32(off), [0x50]);
    }
    if (conv === 't') b = b.concat([0x8d, 0x8b], d32(CB.THIS));
  }
  b = b.concat([0xff, 0x93], d32(CB.FN), [0x89, 0x83], d32(CB.EAX), [0x89, 0x93], d32(CB.EDX));
  if (ret === 'f') b = b.concat([0xdb, 0xbb], d32(CB.ST0));
  b = b.concat([0xd9, 0xbb], d32(CB.CW_AFTER), [0xdf, 0xe0, 0x66, 0x89, 0x83], d32(CB.SW_AFTER),
    [0xdb, 0xe3], [0xd9, 0xab], d32(CB.CW_SAVED), [0x8d, 0x65, 0xf4, 0x5f, 0x5e, 0x5b, 0x5d, 0xc3]);
  const at = code.add(codeOff);
  at.writeByteArray(b);
  codeOff += (b.length + 15) & ~15;
  return new NativeFunction(at, 'void', ['pointer']);
}

// ---- typed values (x87ops.py)
const TYPE_BYTES = { f32: 4, f64: 8, i16: 2, i32: 4, i64: 8, x80: 10 };
function hexToBytesLE(h, n) {            // big-endian hex digits of the value -> little-endian bytes
  const out = new Array(n).fill(0);
  h = h.padStart(2 * n, '0');
  for (let i = 0; i < n; i++) out[i] = parseInt(h.substr(2 * (n - 1 - i), 2), 16);
  return out;
}
function writeTyped(p, tv) {
  const i = tv.indexOf(':');
  const t = tv.slice(0, i), h = tv.slice(i + 1);
  if (t === 'hex') {
    const b = [];
    for (let k = 0; k < h.length; k += 2) b.push(parseInt(h.substr(k, 2), 16));
    p.writeByteArray(b);
    return;
  }
  if (t === 'x80') {
    p.writeByteArray(hexToBytesLE(h.slice(4), 8));
    p.add(8).writeByteArray(hexToBytesLE(h.slice(0, 4), 2));
    return;
  }
  p.writeByteArray(hexToBytesLE(h, TYPE_BYTES[t]));
}
function hexOf(p, n) {
  const b = new Uint8Array(p.readByteArray(n));
  let s = '';
  for (let i = 0; i < n; i++) s += b[i].toString(16).padStart(2, '0');
  return s;
}
function beHex(p, n) {                   // little-endian bytes -> big-endian hex digits
  const b = new Uint8Array(p.readByteArray(n));
  let s = '';
  for (let i = n - 1; i >= 0; i--) s += b[i].toString(16).padStart(2, '0');
  return s;
}
function x80At(p) { return 'x80:' + beHex(p.add(8), 2) + beHex(p, 8); }

// ---- symbol resolution
const fnCache = new Map();
function resolve(v) {
  const key = v.mod + '!' + v.sym;
  if (fnCache.has(key)) return fnCache.get(key);
  const m = Process.findModuleByName(v.mod);
  let a = null;
  if (m) a = v.sym.startsWith('+') ? m.base.add(parseInt(v.sym.slice(1), 16)) : m.findExportByName(v.sym);
  fnCache.set(key, a);
  return a;
}
const stubs = new Map();
function stubFor(sig) {
  if (!stubs.has(sig)) stubs.set(sig, buildStub(sig));
  return stubs.get(sig);
}

// Inputs that would hang the game's own code (UMCos steps by 2*pi until in range)
function wouldHang(v) {
  if (v.fn !== 'UMCos' && v.fn !== 'UMTan') return false;
  const u = parseInt(v.args[0].slice(4), 16) >>> 0;
  const e = (u >>> 23) & 0xff;
  return e === 0xff || e >= 127 + 20;      // non-finite or |x| >= 2^20
}

function runOne(v) {
  const r = { id: v.id };
  const cw = parseInt(v.cw || '027f', 16);
  if ((cw & 0x3f) !== 0x3f) { r.error = 'unmasked exceptions not allowed'; return r; }
  if (wouldHang(v)) { r.error = 'skipped: input would loop forever in the game code'; return r; }
  const fn = resolve(v);
  if (fn === null) { r.error = 'symbol not found: ' + v.mod + '!' + v.sym; return r; }
  const [conv, ret, args] = v.sig.split(':');
  block.writeByteArray(new Array(CB.SIZE).fill(0));
  block.add(CB.FN).writePointer(fn);
  block.add(CB.CW_IN).writeU16(cw);
  let nbuf = 0, nstr = 0;
  for (let k = 0; k < args.length; k++) {
    const t = args[k], a = v.args[k];
    if (t === 'p' || t === 'P') {
      if (a && a.startsWith('hex:')) writeTyped(block.add(CB.BUFS + 16 * nbuf), a);
      nbuf++;
    } else if (t === 's') {
      block.add(CB.STRS + CB.STR_LEN * nstr++).writeAnsiString(a.slice(4).slice(0, CB.STR_LEN - 1));
    } else if (t === 'i' && !a.startsWith('i32:')) {
      block.add(CB.ARGS + 8 * k).writeS32(parseInt(a, 10));
    } else {
      writeTyped(block.add(CB.ARGS + 8 * k), a);
    }
  }
  if (v.this) writeTyped(block.add(CB.THIS), v.this);
  try {
    stubFor(v.sig)(block);
  } catch (e) {
    r.error = 'exception: ' + e.message;
    return r;
  }
  const swb = block.add(CB.SW_BEFORE).readU16(), swa = block.add(CB.SW_AFTER).readU16();
  r.st0 = ret === 'f' ? x80At(block.add(CB.ST0)) : null;
  const eax = block.add(CB.EAX).readU32();
  r.eax = (ret === 'i' || ret === 'l' || ret === 'b') ?
    'i32:' + ((ret === 'b' ? eax & 0xff : eax) >>> 0).toString(16).padStart(8, '0') : null;
  r.edx = ret === 'l' ? 'i32:' + block.add(CB.EDX).readU32().toString(16).padStart(8, '0') : null;
  r.bufs = [];
  for (let k = 0; k < nbuf; k++) r.bufs.push('hex:' + hexOf(block.add(CB.BUFS + 16 * k), 16));
  r.cw_after = block.add(CB.CW_AFTER).readU16().toString(16).padStart(4, '0');
  r.sw_after = swa.toString(16).padStart(4, '0');
  r.top_delta = ((((swa >> 11) & 7) - ((swb >> 11) & 7)) + 8) % 8;
  if (conv === 't') r.this = 'hex:' + hexOf(block.add(CB.THIS), 32);
  return r;
}

// ---- file I/O (File.readAllText exists in Frida >= 16.1; fall back to msvcrt fopen/fread)
function readText(path) {
  if (typeof File.readAllText === 'function') return File.readAllText(path);
  const crt = Process.getModuleByName('msvcrt.dll');
  const fopen = new NativeFunction(crt.getExportByName('fopen'), 'pointer', ['pointer', 'pointer']);
  const fread = new NativeFunction(crt.getExportByName('fread'), 'uint', ['pointer', 'uint', 'uint', 'pointer']);
  const fclose = new NativeFunction(crt.getExportByName('fclose'), 'int', ['pointer']);
  const f = fopen(Memory.allocAnsiString(path), Memory.allocAnsiString('rb'));
  if (f.isNull()) throw new Error('cannot open ' + path);
  const buf = Memory.alloc(1 << 20);
  let s = '';
  for (;;) {
    const n = fread(buf, 1, 1 << 20, f);
    if (n === 0) break;
    s += buf.readUtf8String(n);
  }
  fclose(f);
  return s;
}

const state = { in: P.in, out: P.out, total: 0, done: 0, errors: 0, finished: false, started: Date.now(), ms: 0 };
let lines = [], pos = 0, outFile = null, timer = null;

function meta() {
  const mods = {};
  for (const n of ['Empire Earth.exe', 'Low-Level Engine.dll', 'msvcrt.dll']) {
    const m = Process.findModuleByName(n);
    if (m) mods[n] = { base: m.base.toString(), size: m.size, path: m.path };
  }
  return { backend: 'frida-legion', frida: Frida.version, arch: Process.arch, platform: Process.platform,
    pid: Process.id, modules: mods, params: P };
}

function tick() {
  timer = null;
  const end = Math.min(lines.length, pos + P.chunk);
  const out = [];
  for (; pos < end; pos++) {
    const line = lines[pos].trim();
    if (!line) continue;
    let v;
    try { v = JSON.parse(line); } catch (e) { continue; }
    if (v.kind !== 'call') continue;
    const r = runOne(v);
    if (r.error) state.errors++;
    state.done++;
    out.push(JSON.stringify(r));
    if (P.limit && state.done >= P.limit) { pos = lines.length; break; }
  }
  if (out.length) { outFile.write(out.join('\n') + '\n'); outFile.flush(); }
  state.ms = Date.now() - state.started;
  if (pos >= lines.length) {
    outFile.close();
    state.finished = true;
    send({ type: 'event', event: { kind: 'ummath-done', done: state.done, errors: state.errors, ms: state.ms } });
    return;
  }
  timer = setTimeout(tick, 0);
}

function start() {
  lines = readText(P.in).split('\n');
  state.total = lines.length;
  outFile = new File(P.out, 'w');
  outFile.write(JSON.stringify({ meta: meta() }) + '\n');
  send({ type: 'event', event: { kind: 'ummath-start', lines: lines.length, out: P.out } });
  timer = setTimeout(tick, 0);
}

function snapshot() { return state; }
function stop() { if (timer) { clearTimeout(timer); timer = null; } return state; }

setTimeout(start, 0);
rpc.exports = { snapshot, stop };
