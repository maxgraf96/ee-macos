// Sync-related engine exports: call counts per thread (and x87 CW) for every ESChecksum /
// ESChecksumEntry / ESVerify-style export of Low-Level Engine.dll, plus a timestamped stream of
// ESChecksum::GetTotalChecksum() results (prototype of the per-tick checksum-stream oracle).
// Counting runs in a CModule (cheap enough for per-unit AddChecksum calls); only
// GetTotalChecksum and Compare get JS onLeave hooks.
//
// PARAMS: pattern (regex over export names, default ES checksum/verify family),
//         max_stream (default 20000 GetTotalChecksum records).
'use strict';

const P = Object.assign({ pattern: '@ESChecksum(Entry)?@@|^\\?ES(Quiet)?Verify@@', max_stream: 20000 },
  globalThis.PARAMS || {});
const engine = Process.getModuleByName('low-level engine.dll');
const k32 = Process.getModuleByName('kernel32.dll');
const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer',
  ['pointer', 'uint', 'uint32', 'uint32'], 'stdcall');

const NB = 1 << 12;
const mem = VirtualAlloc(NULL, 0x1000 + NB * 16, 0x3000, 0x04);   // RW, never freed
const table = mem.add(0x1000);
const cm = new CModule(`
#include <gum/guminterceptor.h>
typedef struct { unsigned int tid, fn, cw, count; } Rec;
extern Rec table[];
extern volatile int lockv;
extern volatile unsigned int dropped;
#define NB ${NB}
static int xchg(volatile int *p, int v) {
  __asm__ __volatile__("xchgl %0, (%1)" : "=r"(v) : "r"(p), "0"(v) : "memory");
  return v;
}
void onEnter(GumInvocationContext *ic) {
  unsigned short cw;
  unsigned int tid, fn, h, probes = 0;
  Rec *r;
  __asm__ __volatile__("fnstcw %0" : "=m"(cw));
  tid = gum_invocation_context_get_thread_id(ic);
  fn = (unsigned int) gum_invocation_context_get_listener_function_data(ic);
  h = ((tid * 2654435761u) ^ (fn * 40503u) ^ (cw * 2246822519u)) & (NB - 1);
  while (xchg(&lockv, 1)) { }
  for (;;) {
    r = &table[h];
    if (r->count == 0) { r->tid = tid; r->fn = fn; r->cw = cw; r->count = 1; break; }
    if (r->tid == tid && r->fn == fn && r->cw == cw) { r->count++; break; }
    h = (h + 1) & (NB - 1);
    if (++probes > 256) { dropped++; break; }
  }
  lockv = 0;
}
`, { table, lockv: mem, dropped: mem.add(32) });

const re = new RegExp(P.pattern);
const fns = [];
const stream = [];
const compares = [];
const t0 = Date.now();
for (const e of engine.enumerateExports()) {
  if (e.type !== 'function' || !re.test(e.name)) continue;
  const i = fns.length;
  fns.push(e.name);
  try {
    Interceptor.attach(e.address, { onEnter: cm.onEnter }, ptr(i));
    if (e.name.startsWith('?GetTotalChecksum@ESChecksum@@')) {
      Interceptor.attach(e.address, {
        onLeave(ret) {
          if (stream.length < P.max_stream) stream.push([Date.now() - t0, this.threadId, ret.toUInt32() >>> 0]);
        },
      });
    } else if (e.name.startsWith('?Compare@ESChecksum@@')) {
      Interceptor.attach(e.address, {
        onLeave(ret) { if (compares.length < 2000) compares.push([Date.now() - t0, this.threadId, ret.toUInt32() & 0xff]); },
      });
    }
  } catch (err) {
    send({ type: 'hook-error', name: e.name, error: err.message });
  }
}
send({ type: 'hooked', count: fns.length, names: fns });

function snapshot() {
  const rows = [];
  const buf = new Uint32Array(table.readByteArray(NB * 16));
  for (let i = 0; i < NB; i++) {
    const count = buf[i * 4 + 3];
    if (count) rows.push({ tid: buf[i * 4], name: fns[buf[i * 4 + 1]], cw: '0x' + buf[i * 4 + 2].toString(16).padStart(4, '0'), count });
  }
  return { elapsedMs: Date.now() - t0, dropped: mem.add(32).readU32(), hooked: fns, rows,
           totalChecksumStream: stream.map(([ms, tid, v]) => [ms, tid, '0x' + v.toString(16).padStart(8, '0')]),
           compares };
}

rpc.exports = { snapshot };
