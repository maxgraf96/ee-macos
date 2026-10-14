// Per-thread x87 control word (and MXCSR) by *sampling*: every PARAMS.interval_ms the JS thread
// suspends each game thread, reads its CONTEXT (FloatSave.ControlWord, ExtendedRegisters MXCSR,
// EIP/ESP) and resumes it. Suspend/GetThreadContext/Resume run in a CModule so nothing allocates
// while a thread is stopped. Also: thread start address (Win32StartAddress), description, and a
// stack scan for return addresses into game modules (to tell main/render/sound/net threads apart).
//
// Independent of the IAT-probe in iat_census.js (which reads the CW at the moment of each API call).
'use strict';

const P = Object.assign({ interval_ms: 50, stack_scans: 6, stack_bytes: 8192 }, globalThis.PARAMS || {});
const GAME = ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'language.dll', 'dx7hrdisplay.dll',
  'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll', 'miles sound system mixer.dll', 'mss32.dll',
  'binkw32.dll', 'ddraw.dll'];

const k32 = Process.getModuleByName('kernel32.dll');
const kbase = Process.getModuleByName('kernelbase.dll');
const ntdll = Process.getModuleByName('ntdll.dll');
const fn = (m, n, r, a) => new NativeFunction(m.getExportByName(n), r, a, 'stdcall');
const OpenThread = fn(k32, 'OpenThread', 'pointer', ['uint32', 'int', 'uint32']);
const NtQIT = fn(ntdll, 'NtQueryInformationThread', 'uint32', ['pointer', 'uint32', 'pointer', 'uint32', 'pointer']);
const GetThreadDescription = kbase.findExportByName('GetThreadDescription')
  ? fn(kbase, 'GetThreadDescription', 'uint32', ['pointer', 'pointer']) : null;
const LocalFree = fn(k32, 'LocalFree', 'pointer', ['pointer']);

// Note: Frida's TinyCC data sections are read-only here (writing a C global faults), and
// __attribute__((stdcall)) externs unbalance the stack. So buffers come in as arguments and the
// stdcall APIs are called through inline-asm helpers (callee pops its arguments).
const cm = new CModule(`
typedef unsigned int DWORD;
static DWORD call1(void *f, DWORD a) {
  DWORD r; __asm__ __volatile__("pushl %2\\n\\tcall *%1" : "=a"(r) : "r"(f), "r"(a) : "ecx", "edx", "memory"); return r; }
static DWORD call2(void *f, DWORD a, DWORD b) {
  DWORD r; __asm__ __volatile__("pushl %3\\n\\tpushl %2\\n\\tcall *%1" : "=a"(r) : "r"(f), "r"(a), "r"(b) : "ecx", "edx", "memory"); return r; }
typedef struct { DWORD ok, fcw, fsw, mxcsr, eip, esp, ebp, ftw; } Sample;
/* fns: SuspendThread, GetThreadContext, ResumeThread. c: 16-byte aligned CONTEXT buffer. */
int sample(DWORD *h, int n, Sample *out, void **fns, unsigned char *c) {
  int i, good = 0;
  for (i = 0; i < n; i++) {
    Sample *s = &out[i];
    s->ok = 0;
    if (call1(fns[0], h[i]) == (DWORD) -1) continue;
    *(DWORD *) c = 0x10029;   /* CONTEXT_i386 | CONTROL | FLOATING_POINT | EXTENDED_REGISTERS */
    if (call2(fns[1], h[i], (DWORD) c)) {
      s->ok = 1; good++;
      s->fcw = *(DWORD *) (c + 0x1c) & 0xffff;
      s->fsw = *(DWORD *) (c + 0x20) & 0xffff;
      s->ftw = *(DWORD *) (c + 0x24) & 0xffff;
      s->mxcsr = *(DWORD *) (c + 0xcc + 24);
      s->ebp = *(DWORD *) (c + 0xb4);
      s->eip = *(DWORD *) (c + 0xb8);
      s->esp = *(DWORD *) (c + 0xc4);
    }
    call1(fns[2], h[i]);
  }
  return good;
}
`);
const fnTable = Memory.alloc(12);
fnTable.writePointer(k32.getExportByName('SuspendThread'));
fnTable.add(4).writePointer(k32.getExportByName('GetThreadContext'));
fnTable.add(8).writePointer(k32.getExportByName('ResumeThread'));
const ctxBuf = Memory.alloc(0x400);   // Frida allocations are 16-byte aligned
const sampleNative = new NativeFunction(cm.sample, 'int', ['pointer', 'int', 'pointer', 'pointer', 'pointer']);

const threads = new Map();   // tid -> info
const symCache = new Map();
const MAXT = 256;
const hbuf = Memory.alloc(4 * MAXT);
const sbuf = Memory.alloc(32 * MAXT);
let samples = 0, started = Date.now(), timer = null;

function where(addr) {
  const k = addr.toString();
  if (symCache.has(k)) return symCache.get(k);
  let r = k;
  const m = Process.findModuleByAddress(addr);
  if (m) {
    r = m.name + '+0x' + addr.sub(m.base).toString(16);
    try {
      const s = DebugSymbol.fromAddress(addr);
      if (s && s.name && !s.name.startsWith('0x')) r += ' (' + s.name + ')';
    } catch (e) { }
  }
  symCache.set(k, r);
  return r;
}

function threadMeta(h) {
  const buf = Memory.alloc(28);
  const meta = {};
  if (NtQIT(h, 9, buf, 4, NULL) === 0) meta.start = where(buf.readPointer());          // Win32StartAddress
  if (NtQIT(h, 0, buf, 28, NULL) === 0) meta.teb = buf.add(4).readPointer();            // THREAD_BASIC_INFORMATION
  if (GetThreadDescription) {
    const pp = Memory.alloc(Process.pointerSize);
    if (GetThreadDescription(h, pp) === 0 && !pp.readPointer().isNull()) {
      meta.description = pp.readPointer().readUtf16String() || undefined;
      LocalFree(pp.readPointer());
    }
  }
  return meta;
}

function refresh() {
  const me = Process.getCurrentThreadId();
  const live = new Set();
  for (const t of Process.enumerateThreads()) {
    if (t.id === me) continue;
    live.add(t.id);
    if (threads.has(t.id)) continue;
    const h = OpenThread(0x2 | 0x8 | 0x40, 0, t.id);   // SUSPEND_RESUME | GET_CONTEXT | QUERY_INFORMATION
    if (h.isNull()) continue;
    const meta = threadMeta(h);
    threads.set(t.id, { tid: t.id, h, meta, firstSeenMs: Date.now() - started, fcw: {}, mxcsr: {}, eip: {},
                        n: 0, scans: [], alive: true });
  }
  for (const [tid, t] of threads) if (!live.has(tid)) t.alive = false;
}

function isRetAddr(v) {
  // Accept v if it follows a CALL: E8 rel32 (v-5), FF /2 with modrm forms (v-2, v-3, v-6, v-7).
  try {
    const b = v.sub(7).readByteArray(7);
    const u = new Uint8Array(b);
    if (u[2] === 0xe8) return true;
    if (u[5] === 0xff && (u[6] & 0x38) === 0x10) return true;
    if (u[4] === 0xff && (u[5] & 0x38) === 0x10) return true;
    if (u[1] === 0xff && (u[2] & 0x38) === 0x10) return true;
    if (u[0] === 0xff && (u[1] & 0x38) === 0x10) return true;
  } catch (e) { }
  return false;
}

function stackScan(t, esp) {
  try {
    if (!t.meta.teb) return null;
    const stackBase = t.meta.teb.add(4).readPointer();
    const sp = ptr(esp);
    const len = Math.min(P.stack_bytes, stackBase.sub(sp).toInt32());
    if (len <= 0) return null;
    const words = new Uint32Array(sp.readByteArray(len & ~3));
    const frames = [];
    for (const w of words) {
      const v = ptr(w >>> 0);
      const m = Process.findModuleByAddress(v);
      if (!m) continue;
      const r = Process.findRangeByAddress(v);
      if (!r || r.protection.indexOf('x') < 0 || !isRetAddr(v)) continue;
      frames.push(where(v));
      if (frames.length >= 24) break;
    }
    return frames;
  } catch (e) { return ['scan error: ' + e.message]; }
}

function tick() {
  const list = Array.from(threads.values()).filter(t => t.alive).slice(0, MAXT);
  list.forEach((t, i) => hbuf.add(4 * i).writePointer(t.h));
  sampleNative(hbuf, list.length, sbuf, fnTable, ctxBuf);
  samples++;
  list.forEach((t, i) => {
    const s = sbuf.add(32 * i);
    if (!s.readU32()) return;
    const fcw = '0x' + s.add(4).readU32().toString(16).padStart(4, '0');
    const mx = '0x' + s.add(12).readU32().toString(16).padStart(4, '0');
    const eip = ptr(s.add(16).readU32());
    t.n++;
    t.fcw[fcw] = (t.fcw[fcw] || 0) + 1;
    t.mxcsr[mx] = (t.mxcsr[mx] || 0) + 1;
    const m = Process.findModuleByAddress(eip);
    const mk = m ? m.name : 'anon';
    t.eip[mk] = (t.eip[mk] || 0) + 1;
    if (t.scans.length < P.stack_scans && (t.n === 1 || t.n % 40 === 0)) {
      t.scans.push({ fcw, eip: where(eip), frames: stackScan(t, s.add(20).readU32()) });
    }
  });
  if (samples % 20 === 0) refresh();
}

function start() {
  if (timer) return;
  refresh();
  timer = setInterval(tick, P.interval_ms);
}

function snapshot() {
  const out = [];
  for (const t of threads.values()) {
    out.push({ tid: t.tid, alive: t.alive, start: t.meta.start, description: t.meta.description,
               firstSeenMs: t.firstSeenMs, samples: t.n, fcw: t.fcw, mxcsr: t.mxcsr, eip_modules: t.eip,
               scans: t.scans });
  }
  return { samples, elapsedMs: Date.now() - started, interval_ms: P.interval_ms, threads: out };
}

function stop() { if (timer) { clearInterval(timer); timer = null; } return true; }

rpc.exports = { snapshot, stop, start };
if (!P.manual_start) start();
