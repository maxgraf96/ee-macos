// M1 oracle: capture every EERMPIM* call Default.dll receives during random-map generation,
// with everything needed to replay the call address-exactly in a fresh process
// (tests/m1/replay/). Spec: docs/reference/rmg.md §6. For windows/frida/run.py (needs the run.py
// that understands {"type": "file"} messages; deploy both together).
//
//   run.py --scripts modules,rmg_capture --seconds 900 --out C:\EEPort\captures\rmg\<case> ^
//          --param case=<case> --param seed=<n> --param stop_file=C:\EEPort\captures\rmg\<case>\STOP ^
//          --stop-file C:\EEPort\captures\rmg\<case>\STOP
//   (attach at the main menu, then start the random-map game)
//
// What it writes (through run.py, under --out):
//   calls/NNNNNN.json      one manifest per EERMPIM call: args, ESP, x87 CW/SW/TW, input regions,
//                          allocation and file-I/O sequences, output regions, EAX
//   generations/NNN.json   one per EERandomMap generation (EXE 0x66d402): seed, attempts, results
//   loads.json             Default.dll loads/unloads seen
//   blobs/xx/<sha256>.bin  region contents, content-addressed, in chunks of PARAMS.chunk bytes
//   rmg_capture.json       snapshot(): summary, counters, warnings
//
// How a call is captured [design; rmg.md §6.2]:
//   * From the moment Default.dll is mapped (module observer) until it is unloaded, every block
//     returned by msvcrt's allocators (any thread) is tracked in a live map. Everything the
//     context can reach was allocated in that window (the EXE builds the context after loading
//     the plugin), so the live map is a precise index of candidate heap blocks.
//   * At entry: roots = context [ctx, ctx+0x3d0), Default.dll .data, the UString argument of
//     SetRandomMapValues. Every aligned dword of a root or captured block that points into a
//     tracked live block pulls in that whole block (conservative closure). Values that point into
//     other private RW memory are counted as "foreign": mostly stale addresses in uninitialised
//     memory; the replay shows whether any of them is really used.
//     Also captured, not scanned: the stack window [ESP-stack_below, ESP+stack_above) and,
//     for diagnosis only, Low-Level Engine.dll .data.
//   * During the call, on the calling thread, allocator/free calls whose caller is a game module
//     (Default, Low-Level Engine, sxlrt233, msvcp60) are logged in order with their results and
//     the initial contents of new blocks; fopen/fgets/fclose likewise (with the returned text).
//   * At exit: the same closure again gives the output regions.
//
// PARAMS (all optional):
//   seed=<int>          force the map seed (FUN_0066d402 argument 5 and [[0x91977c]+0x54])
//   case=<str>          case id stored in every manifest
//   stop_file=<path>    written after PARAMS.generations generations have completed
//   generations=<n>     default 1
//   stack_below=<n>     default 0x8000 bytes below ESP;  stack_above=<n> default 0x200
//   chunk=<n>           blob chunk size, default 4096
//   alloc_content=bool  capture initial contents of blocks allocated during a call (default true)
//   engine_data=bool    capture Low-Level Engine.dll .data at entry (diagnostic, default true)
//   heapwalk=bool       HeapWalk fallback for foreign pointers, one level (default false)
//   max_foreign=<n>     foreign pointers listed per phase (default 64; all are counted)
'use strict';

const P = Object.assign({
  seed: null, case: null, stop_file: null, generations: 1,
  stack_below: 0x8000, stack_above: 0x200, chunk: 4096,
  alloc_content: true, engine_data: true, heapwalk: false, max_foreign: 64,
}, globalThis.PARAMS || {});

const SCHEMA = 'ee-rmg-capture/1';
const CTX_SIZE = 0x3d0;
const EXE_SIZE = 0x682000;                 // GOG Empire Earth.exe SizeOfImage (fixed base 0x400000)
const EXE_RVA = {
  generate: 0x26d402,                      // FUN_0066d402: load generator, retry loop (<= 11 attempts)
  createRandomMap: 0x26c48f,               // FUN_0066c48f: one attempt (thiscall, ECX = EERandomMap*)
  playable: 0x26b631,                      // FUN_0066b631(ctx, world) -> bool
  scheduler: 0x51977c,                     // VA 0x91977c: object whose +0x54 is the random-map seed
};
const EXPORTS = {                          // name -> number of stack arguments
  EERMPIMSetRandomMapValues: 2, EERMPIMGeneratePlayerPositions: 1, EERMPIMGenerateNeutralPositions: 1,
  EERMPIMCreatePlayerLandWaterMap: 1, EERMPIMCreateNeutralLandWaterMap: 1, EERMPIMCreateFlatMap: 1,
  EERMPIMCreateHeightMap: 1, EERMPIMGenerateShallows: 1, EERMPIMFilterHeightMap: 1,
  EERMPIMGenerateResources: 1, EERMPIMCreateTrees: 1,
};
// Context vectors (offset -> element size) for the per-call summary only; rmg.md §4.
const CTX_VECTORS = [[0xa0, 16], [0xb0, 16], [0xc0, 16], [0xd0, 16], [0xe0, 0x3c], [0xf0, 16], [0x100, 0x18],
  [0x110, 0x20], [0x120, 16], [0x130, 8], [0x140, 0x14], [0x150, 0x14], [0x160, 0x18], [0x170, 0x14],
  [0x180, 0x80], [0x190, 0xc], [0x1a0, 16], [0x1b0, 4], [0x1c0, 0x18], [0x1d0, 4], [0x1e0, 0x14],
  [0x1f0, 4], [0x200, 8], [0x210, 0xc]];

const t0 = Date.now();
const hex = v => '0x' + (v >>> 0).toString(16);
const u32 = p => p.toUInt32() >>> 0;
const P32 = v => ptr(v >>> 0);
const warnings = [];
function warn(msg) { if (warnings.length < 500) warnings.push([Date.now() - t0, msg]); send({ type: 'warning', msg }); }

// ---------------------------------------------------------------------------------------------
// Output through run.py: {"type": "file", "path": rel} + bytes, or {"type": "file", "path", "json"}.
const stats = { blobs: 0, blobBytes: 0, chunks: 0, files: 0, calls: 0 };
const sentBlobs = new Set();
function writeJson(path, obj) { stats.files++; send({ type: 'file', path, json: obj }); }

let sha256;
if (typeof Checksum !== 'undefined' && Checksum.compute) {
  sha256 = buf => Checksum.compute('sha256', buf);
} else {
  // Fallback (slow): plain SHA-256 over an ArrayBuffer.
  const K = new Uint32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
    0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152,
    0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138,
    0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70,
    0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa,
    0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  sha256 = function (buf) {
    const b = new Uint8Array(buf), n = b.length, L = ((n + 9 + 63) >> 6) << 6, m = new Uint8Array(L);
    m.set(b); m[n] = 0x80;
    const bits = n * 8; m[L - 4] = bits >>> 24; m[L - 3] = bits >>> 16; m[L - 2] = bits >>> 8; m[L - 1] = bits;
    m[L - 5] = Math.floor(n / 0x20000000);
    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const W = new Uint32Array(64), r = (x, s) => (x >>> s) | (x << (32 - s));
    for (let o = 0; o < L; o += 64) {
      for (let i = 0; i < 16; i++) W[i] = (m[o + 4 * i] << 24) | (m[o + 4 * i + 1] << 16) | (m[o + 4 * i + 2] << 8) | m[o + 4 * i + 3];
      for (let i = 16; i < 64; i++) {
        const s0 = r(W[i - 15], 7) ^ r(W[i - 15], 18) ^ (W[i - 15] >>> 3), s1 = r(W[i - 2], 17) ^ r(W[i - 2], 19) ^ (W[i - 2] >>> 10);
        W[i] = W[i - 16] + s0 + W[i - 7] + s1;
      }
      let [a, bb, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i++) {
        const t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) >>> 0;
        const t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = bb; bb = a; a = (t1 + t2) >>> 0;
      }
      H[0] += a; H[1] += bb; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
    }
    return Array.from(H, x => x.toString(16).padStart(8, '0')).join('');
  };
  warn('Checksum API missing: using the JS SHA-256 fallback (slow)');
}

function storeBuffer(buf) {
  const h = sha256(buf);
  stats.chunks++;
  if (!sentBlobs.has(h)) {
    sentBlobs.add(h);
    stats.blobs++; stats.blobBytes += buf.byteLength;
    send({ type: 'file', path: 'blobs/' + h.slice(0, 2) + '/' + h + '.bin' }, buf);
  }
  return h;
}
// Region contents as a list of chunk hashes (dedup across calls: unchanged chunks cost nothing).
function storeBytes(addr, size) {
  const hs = [];
  for (let off = 0; off < size; off += P.chunk) {
    const n = Math.min(P.chunk, size - off);
    hs.push(storeBuffer(P32(addr + off).readByteArray(n)));
  }
  return hs;
}
function storeArrayBuffer(ab) {
  const hs = [];
  for (let off = 0; off < ab.byteLength; off += P.chunk) hs.push(storeBuffer(ab.slice(off, Math.min(ab.byteLength, off + P.chunk))));
  return hs;
}
function hexBytes(ab) { return Array.from(new Uint8Array(ab), x => x.toString(16).padStart(2, '0')).join(''); }

// ---------------------------------------------------------------------------------------------
// Native helpers. TinyCC on this Frida: no writable globals, no stdcall externs (see threads.js).
// x87 environment of the calling thread: CW, SW, TW (FNSTENV masks exceptions, so FLDENV it back).
let cmFpu;
try {
  cmFpu = new CModule(`
void fpu_env(unsigned short *out) {
  unsigned char env[28];
  __asm__ __volatile__("fnstenv %0\\n\\tfldenv %0" : "+m"(env) : : "memory");
  out[0] = *(unsigned short *)(env + 0); out[1] = *(unsigned short *)(env + 4); out[2] = *(unsigned short *)(env + 8);
}`);
} catch (e) {
  cmFpu = new CModule(`
void fpu_env(unsigned short *out) {
  unsigned short cw, sw;
  __asm__ __volatile__("fnstcw %0" : "=m"(cw)); __asm__ __volatile__("fnstsw %0" : "=m"(sw));
  out[0] = cw; out[1] = sw; out[2] = 0xdead;   /* tag word unavailable */
}`);
}
const cm = new CModule(`
typedef unsigned int u32;
/* Scan nwords dwords at mem from word 'start'. Hits are (word index, block index | 0xffffffff, value):
   a block hit when bs[i] <= v < be[i] (sorted, disjoint), a foreign hit when v lies in one of the
   sorted ranges [rs, re). Stops when out is full; *next = first word not scanned. Returns #u32 written. */
u32 scan(const u32 *mem, u32 nwords, u32 start, const u32 *bs, const u32 *be, u32 n, u32 lo, u32 hi,
         const u32 *rs, const u32 *re, u32 m, u32 *out, u32 maxout, u32 *next) {
  u32 k = 0, i;
  for (i = start; i < nwords; i++) {
    u32 v = mem[i], a, b;
    if (v < 0x10000) continue;
    if (k + 3 > maxout) break;
    if (n && v >= lo && v < hi) {
      a = 0; b = n;
      while (a < b) { u32 mid = (a + b) >> 1; if (bs[mid] <= v) a = mid + 1; else b = mid; }
      if (a > 0 && v < be[a - 1]) { out[k++] = i; out[k++] = a - 1; out[k++] = v; continue; }
    }
    if (m) {
      a = 0; b = m;
      while (a < b) { u32 mid = (a + b) >> 1; if (rs[mid] <= v) a = mid + 1; else b = mid; }
      if (a > 0 && v < re[a - 1]) { out[k++] = i; out[k++] = 0xffffffffu; out[k++] = v; }
    }
  }
  *next = i;
  return k;
}
`);
const fpuEnvNative = new NativeFunction(cmFpu.fpu_env, 'void', ['pointer']);
const scanNative = new NativeFunction(cm.scan, 'uint32', ['pointer', 'uint32', 'uint32', 'pointer', 'pointer', 'uint32',
  'uint32', 'uint32', 'pointer', 'pointer', 'uint32', 'pointer', 'uint32', 'pointer']);
const fpuBuf = Memory.alloc(8);
function fpuEnv() { fpuEnvNative(fpuBuf); return { cw: hex(fpuBuf.readU16()), sw: hex(fpuBuf.add(2).readU16()), tw: hex(fpuBuf.add(4).readU16()) }; }
const SCAN_MAX = 3 * 65536;
const scanOut = Memory.alloc(4 * SCAN_MAX), scanNext = Memory.alloc(4);

// ---------------------------------------------------------------------------------------------
// Modules.
const exe = Process.getModuleByName('Empire Earth.exe');
if (exe.size !== EXE_SIZE || !exe.base.equals(ptr(0x400000))) warn('unexpected EXE: base ' + exe.base + ' size ' + hex(exe.size));
const crt = Process.getModuleByName('msvcrt.dll');
const k32 = Process.getModuleByName('kernel32.dll');

function peSections(base) {
  const nt = base.add(base.add(0x3c).readU32());
  const nsec = nt.add(6).readU16(), opt = nt.add(24), optSize = nt.add(20).readU16();
  const out = [];
  let s = opt.add(optSize);
  for (let i = 0; i < nsec; i++, s = s.add(40)) {
    const nb = new Uint8Array(s.readByteArray(8));
    let name = ''; for (const c of nb) { if (!c) break; name += String.fromCharCode(c); }
    out.push({ name, rva: s.add(12).readU32(), vsize: s.add(8).readU32() });
  }
  return { timestamp: nt.add(8).readU32(), imageBase: opt.add(28).readU32(), sizeOfImage: opt.add(56).readU32(), sections: out };
}
const fileHashes = new Map();
function fileSha(path) {
  if (fileHashes.has(path)) return fileHashes.get(path);
  let h = null;
  try { h = sha256(File.readAllBytes(path)); } catch (e) { warn('cannot hash ' + path + ': ' + e.message); }
  fileHashes.set(path, h);
  return h;
}
function moduleRecord(m) {
  const pe = peSections(m.base);
  const data = pe.sections.find(s => s.name === '.data');
  return { name: m.name, base: hex(u32(m.base)), size: m.size, path: m.path, timestamp: hex(pe.timestamp),
           preferred: hex(pe.imageBase), sizeOfImage: hex(pe.sizeOfImage), sha256: fileSha(m.path),
           data: data ? { rva: hex(data.rva), size: data.vsize } : null };
}
// Ranges of the modules whose allocator/file calls belong to the replayed code.
const GAME_CALLERS = ['low-level engine.dll', 'sxlrt233.dll', 'msvcp60.dll'];
let callerRanges = [];                       // [lo, hi, name]
function refreshCallerRanges() {
  callerRanges = [];
  for (const n of GAME_CALLERS.concat(['default.dll'])) {
    const m = Process.findModuleByName(n);
    if (m) callerRanges.push([u32(m.base), u32(m.base) + m.size, m.name]);
  }
}
function callerOf(ra) {
  const a = u32(ra);
  for (const [lo, hi, name] of callerRanges) if (a >= lo && a < hi) return name + '+' + hex(a - lo);
  return null;
}

// ---------------------------------------------------------------------------------------------
// Heap tracking: live blocks (any thread) while Default.dll is loaded or a generation runs.
const live = new Map();                    // addr -> size
let tracking = false, trackingSince = null;
function startTracking(why) {
  if (tracking) return;
  tracking = true; trackingSince = why; live.clear();
  send({ type: 'tracking', on: true, why });
}
function stopTracking(why) {
  if (!tracking) return;
  tracking = false; live.clear();
  send({ type: 'tracking', on: false, why });
}

let cur = null;                            // the call being captured
let seq = 0, gen = -1, attempt = -1, genRec = null, generationsDone = 0;
const generations = [], calls = [], loads = [];

const OP = { new: 1, 'new[]': 2, malloc: 3, calloc: 4, realloc: 5, delete: 6, 'delete[]': 7, free: 8 };
function hookAllocator(name, op) {
  const a = crt.findExportByName(name);
  if (!a) return;
  Interceptor.attach(a, {
    onEnter(args) {
      if (!tracking) { this.skip = true; return; }
      this.a = u32(args[0]);
      this.b = (op === 'calloc' || op === 'realloc') ? u32(args[1]) : 0;
      this.rec = cur !== null && this.threadId === cur.tid ? callerOf(this.returnAddress) : null;
    },
    onLeave(ret) {
      if (this.skip) return;
      const r = u32(ret);
      let size = this.a;
      if (op === 'calloc') size = this.a * this.b;
      else if (op === 'realloc') { size = this.b; if (r && this.a) live.delete(this.a); else if (!r && this.b === 0 && this.a) live.delete(this.a); }
      if (r) live.set(r, size);
      if (this.rec) {
        const ev = { op, a: hex(this.a), b: hex(this.b), ret: hex(r), size, caller: this.rec };
        if (r && P.alloc_content && size) {
          try { ev.chunks = storeBytes(r, size); } catch (e) { ev.error = e.message; }
        }
        cur.allocs.push(ev);
      }
    },
  });
}
function hookFree(name, op) {
  const a = crt.findExportByName(name);
  if (!a) return;
  Interceptor.attach(a, {
    onEnter(args) {
      if (!tracking) return;
      const p = u32(args[0]);
      live.delete(p);
      if (cur !== null && this.threadId === cur.tid) {
        const c = callerOf(this.returnAddress);
        if (c) cur.allocs.push({ op, a: hex(p), caller: c });
      }
    },
  });
}
hookAllocator('??2@YAPAXI@Z', 'new'); hookAllocator('??_U@YAPAXI@Z', 'new[]');
hookAllocator('malloc', 'malloc'); hookAllocator('calloc', 'calloc'); hookAllocator('realloc', 'realloc');
hookFree('??3@YAXPAX@Z', 'delete'); hookFree('??_V@YAXPAX@Z', 'delete[]'); hookFree('free', 'free');

// File I/O and diagnostics, only for the call being captured.
function inCall(ctx) { return cur !== null && ctx.threadId === cur.tid ? callerOf(ctx.returnAddress) : null; }
Interceptor.attach(crt.getExportByName('fopen'), {
  onEnter(args) { this.c = inCall(this); if (this.c) { this.path = args[0]; this.mode = args[1]; } },
  onLeave(ret) {
    if (!this.c) return;
    const r = u32(ret), raw = [];
    for (let i = 0; i < 1024; i++) { const b = this.path.add(i).readU8(); if (!b) break; raw.push(b); }
    const path = String.fromCharCode.apply(null, raw);
    const ev = { op: 'fopen', path, path_hex: hexBytes(new Uint8Array(raw).buffer), mode: this.mode.readCString(),
                 ret: hex(r), caller: this.c };
    if (r) { try { const ab = File.readAllBytes(path); ev.size = ab.byteLength; ev.content = storeArrayBuffer(ab); } catch (e) { ev.error = e.message; } }
    cur.files.push(ev);
  },
});
Interceptor.attach(crt.getExportByName('fgets'), {
  onEnter(args) { this.c = inCall(this); if (this.c) { this.buf = args[0]; this.n = args[1].toInt32(); this.fp = u32(args[2]); } },
  onLeave(ret) {
    if (!this.c) return;
    const r = u32(ret);
    const ev = { op: 'fgets', fp: hex(this.fp), n: this.n, ret: hex(r) };
    if (r && this.n > 0) {
      const b = new Uint8Array(this.buf.readByteArray(this.n));
      let len = b.indexOf(0); if (len < 0) len = this.n - 1;
      ev.data_hex = hexBytes(b.buffer.slice(0, len + 1));   // includes the terminating NUL
    }
    cur.files.push(ev);
  },
});
Interceptor.attach(crt.getExportByName('fclose'), {
  onEnter(args) { this.c = inCall(this); if (this.c) this.fp = u32(args[0]); },
  onLeave(ret) { if (this.c) cur.files.push({ op: 'fclose', fp: hex(this.fp), ret: hex(u32(ret)) }); },
});
for (const n of ['fread', 'fscanf', 'fgetc', 'getc', 'feof', 'fseek', 'ftell', '_fsopen', 'freopen', 'fwrite', 'fputs',
                 'fprintf', '_open', '_read', '_filbuf', 'ungetc', 'rewind']) {
  const a = crt.findExportByName(n);
  if (a) Interceptor.attach(a, { onEnter() { const c = inCall(this); if (c) cur.warnings.push('unsupported file op ' + n + ' from ' + c); } });
}
Interceptor.attach(crt.getExportByName('printf'), {
  onEnter(args) { const c = inCall(this); if (c) cur.printf.push(args[0].readCString()); },
});
// kernelbase first: kernel32's export forwards there, so import tables point at kernelbase.
for (const m of [Process.findModuleByName('kernelbase.dll'), k32]) {
  const a = m && m.findExportByName('OutputDebugStringA');
  if (a) { Interceptor.attach(a, { onEnter(args) { const c = inCall(this); if (c && cur.debug.length < 2000) cur.debug.push(args[0].readCString()); } }); break; }
}

// ---------------------------------------------------------------------------------------------
// Capture of a state (entry or exit).
function blockIndex() {
  const keys = Array.from(live.keys()).sort((a, b) => a - b);
  const n = keys.length, bs = new Uint32Array(n), be = new Uint32Array(n);
  let k = 0;
  for (const a of keys) {
    const e = a + Math.max(1, live.get(a));
    if (k > 0 && a < be[k - 1]) continue;        // stale overlapping entry: keep the earlier block
    bs[k] = a; be[k] = e; k++;
  }
  const B = Memory.alloc(4 * Math.max(1, k)), E = Memory.alloc(4 * Math.max(1, k));
  if (k) { B.writeByteArray(bs.buffer.slice(0, 4 * k)); E.writeByteArray(be.buffer.slice(0, 4 * k)); }
  return { n: k, bs: bs.subarray(0, k), be: be.subarray(0, k), B, E, lo: k ? bs[0] : 0, hi: k ? be[k - 1] : 0 };
}
function foreignRanges() {
  // Private RW memory outside module images: heaps, stacks, VirtualAlloc'd areas.
  const rs = [];
  for (const r of Process.enumerateRanges({ protection: 'rw-', coalesce: true })) {
    if (r.file || Process.findModuleByAddress(r.base)) continue;
    rs.push([u32(r.base), u32(r.base) + r.size]);
  }
  rs.sort((a, b) => a[0] - b[0]);
  const m = rs.length, S = Memory.alloc(4 * Math.max(1, m)), E = Memory.alloc(4 * Math.max(1, m));
  rs.forEach(([lo, hi], i) => { S.add(4 * i).writeU32(lo); E.add(4 * i).writeU32(hi); });
  return { m, S, E };
}
function scanRegion(addr, size, idx, fr, onHit) {
  const nwords = size >>> 2;
  let start = 0;
  while (start < nwords) {
    const k = scanNative(P32(addr), nwords, start, idx.B, idx.E, idx.n, idx.lo, idx.hi, fr.S, fr.E, fr.m, scanOut, SCAN_MAX, scanNext);
    if (k) {
      const hits = new Uint32Array(scanOut.readByteArray(4 * k));
      for (let i = 0; i < k; i += 3) onHit(addr + 4 * hits[i], hits[i + 1], hits[i + 2]);
    }
    start = scanNext.readU32();
  }
}
let heapHandle = null, HeapWalk = null, HeapLock = null, HeapUnlock = null;
function heapWalkIndex() {
  if (!HeapWalk) {
    const sc = (n, r, a) => new NativeFunction(k32.getExportByName(n), r, a, 'stdcall');
    HeapWalk = sc('HeapWalk', 'int', ['pointer', 'pointer']); HeapLock = sc('HeapLock', 'int', ['pointer']);
    HeapUnlock = sc('HeapUnlock', 'int', ['pointer']);
    const g = crt.findExportByName('_get_heap_handle');
    heapHandle = g ? new NativeFunction(g, 'pointer', [])() : sc('GetProcessHeap', 'pointer', [])();
  }
  const e = Memory.alloc(32), out = [];
  e.writePointer(NULL);
  HeapLock(heapHandle);
  try { while (HeapWalk(heapHandle, e)) if (e.add(10).readU16() & 4) out.push([u32(e.readPointer()), e.add(4).readU32()]); }
  finally { HeapUnlock(heapHandle); }
  out.sort((a, b) => a[0] - b[0]);
  return out;
}

// roots: [{kind, addr, size, scan, restore, module?}]
function captureState(roots) {
  const idx = blockIndex(), fr = foreignRanges();
  const regions = [], work = [], seenBlock = new Set(), seenFallback = new Set();
  const foreign = { count: 0, sample: [] }, foreignVals = new Map();
  function add(r) { regions.push(r); if (r.scan) work.push(r); }
  for (const r of roots) add(Object.assign({}, r));
  function drain() {
    while (work.length) {
      const r = work.pop();
      scanRegion(r.addr, r.size, idx, fr, (at, bi, v) => {
        if (bi !== 0xffffffff) {
          if (seenBlock.has(bi)) return;
          seenBlock.add(bi);
          add({ kind: 'heap', addr: idx.bs[bi], size: idx.be[bi] - idx.bs[bi], scan: true, restore: true, via: at, from: r.kind });
        } else {
          if (seenFallback.has(v)) return;
          foreign.count++;
          if (foreign.sample.length < P.max_foreign) foreign.sample.push([hex(at), hex(v), r.kind]);
          if (!foreignVals.has(v)) foreignVals.set(v, at);
        }
      });
    }
  }
  drain();
  if (P.heapwalk && foreignVals.size) {           // one level: add the busy heap blocks they hit
    const hw = heapWalkIndex();
    for (const [v, at] of foreignVals) {
      let a = 0, b = hw.length;
      while (a < b) { const mid = (a + b) >> 1; if (hw[mid][0] <= v) a = mid + 1; else b = mid; }
      if (a > 0 && v < hw[a - 1][0] + hw[a - 1][1] && !seenFallback.has(hw[a - 1][0])) {
        seenFallback.add(hw[a - 1][0]); seenFallback.add(v);
        add({ kind: 'heap_hw', addr: hw[a - 1][0], size: hw[a - 1][1], scan: true, restore: true, via: at });
      }
    }
    drain();
  }
  let bytes = 0;
  const out = regions.map(r => {
    bytes += r.size;
    const rec = { kind: r.kind, addr: hex(r.addr), size: r.size, restore: !!r.restore, scan: !!r.scan };
    if (r.module) rec.module = r.module;
    if (r.via !== undefined) rec.via = hex(r.via);
    if (r.from) rec.from = r.from;
    try { rec.chunks = storeBytes(r.addr, r.size); } catch (e) { rec.error = e.message; }
    return rec;
  });
  return { regions: out, bytes, foreign, tracked_blocks: idx.n };
}

function moduleData(name) {
  const m = Process.findModuleByName(name);
  if (!m) return null;
  const d = peSections(m.base).sections.find(s => s.name === '.data');
  return d ? { addr: u32(m.base) + d.rva, size: d.vsize } : null;
}
function stackWindow(esp) {
  const r = Process.findRangeByAddress(P32(esp));
  let lo = esp - P.stack_below, hi = esp + P.stack_above;
  if (r) { lo = Math.max(lo, u32(r.base)); hi = Math.min(hi, u32(r.base) + r.size); }
  return { addr: lo, size: hi - lo };
}
function rootsFor(c, phase) {
  const roots = [{ kind: 'ctx', addr: c.args[0], size: CTX_SIZE, scan: true, restore: true }];
  const dd = moduleData('Default.dll');
  if (dd) roots.push({ kind: 'module_data', module: 'Default.dll', addr: dd.addr, size: dd.size, scan: true, restore: true });
  if (c.export === 'EERMPIMSetRandomMapValues') roots.push({ kind: 'arg', addr: c.args[1], size: 16, scan: true, restore: true });
  if (phase === 'entry') {
    const s = stackWindow(c.esp);
    roots.push({ kind: 'stack', addr: s.addr, size: s.size, scan: false, restore: true });
    if (P.engine_data) {
      const ed = moduleData('Low-Level Engine.dll');
      if (ed) roots.push({ kind: 'module_data_diag', module: 'Low-Level Engine.dll', addr: ed.addr, size: ed.size, scan: false, restore: false });
    }
  }
  return roots;
}
function readUString(p) {
  try { if (p.isNull()) return null; const d = p.add(4).readPointer(); return d.isNull() ? '' : d.readCString(); } catch (e) { return null; }
}
function ctxSummary(ctx) {
  const c = P32(ctx), rd = o => c.add(o).readU32(), f = o => c.add(o).readFloat();
  const vec = {};
  for (const [o, es] of CTX_VECTORS) {
    const first = rd(o + 4), last = rd(o + 8), end = rd(o + 12);
    vec[hex(o)] = first ? [(last - first) / es, (end - first) / es] : [0, 0];
  }
  return { rng0: [hex(rd(0)), hex(rd(0x10))], seed: rd(0x3b0), map_size_class: rd(0x250), start_epoch: rd(0x254),
           player_count_minus_1: rd(0x258), dims: [rd(0x37c), rd(0x380), rd(0x384), rd(0x388)],
           filter: c.add(0x3cd).readU8(), shallows: c.add(0x3cf).readU8(), elev_int: [rd(0x300), rd(0x2f4)],
           roughness: [f(0x27c), f(0x2e4)], script_dir: readUString(c.add(0x34)), vectors: vec };
}

// ---------------------------------------------------------------------------------------------
// Export hooks (per Default.dll load).
let defaultMod = null, defaultHooks = [];
function hookDefault(m) {
  for (const name of Object.keys(EXPORTS)) {
    const a = m.findExportByName(name);
    if (!a) { warn('Default.dll lacks ' + name); continue; }
    defaultHooks.push(Interceptor.attach(a, {
      onEnter() { enterCall(name, this); },
      onLeave(ret) { leaveCall(name, ret); },
    }));
  }
}
function enterCall(name, ictx) {
  if (cur !== null) { warn('nested EERMPIM call ' + name + ' inside ' + cur.export); return; }
  try { enterCall1(name, ictx); } catch (e) { warn('entry capture of ' + name + ' failed: ' + e.stack); cur = null; }
}
function enterCall1(name, ictx) {
  if (!tracking) { warn(name + ' called while not tracking'); startTracking('late:' + name); }
  if (name === 'EERMPIMSetRandomMapValues') attempt++;
  refreshCallerRanges();
  const esp = u32(ictx.context.esp);
  const args = [];
  for (let i = 0; i < EXPORTS[name]; i++) args.push(P32(esp + 4 + 4 * i).readU32());
  cur = { schema: SCHEMA, case: P.case, seq: seq++, gen, attempt, export: name, tid: ictx.threadId, esp, args,
          allocs: [], files: [], printf: [], debug: [], warnings: [], t: [Date.now() - t0, null] };
  cur.modules = ['sxlrt233.dll', 'Low-Level Engine.dll', 'Default.dll', 'msvcrt.dll', 'msvcp60.dll']
    .map(n => Process.findModuleByName(n)).filter(x => x).map(moduleRecord);
  cur.entry = { esp: hex(esp), args: args.map(hex), fpu: fpuEnv(), summary: ctxSummary(args[0]) };
  if (name === 'EERMPIMSetRandomMapValues') cur.entry.script = readUString(P32(args[1]));
  const st = captureState(rootsFor(cur, 'entry'));
  Object.assign(cur.entry, st);
  cur.entry.capture_ms = Date.now() - t0 - cur.t[0];
}
function leaveCall(name, ret) {
  if (cur === null || cur.export !== name) return;
  try { leaveCall1(name, ret); } catch (e) { warn('exit capture of ' + name + ' failed: ' + e.stack); cur = null; }
}
function leaveCall1(name, ret) {
  const tExit = Date.now() - t0;
  cur.exit = { eax: hex(u32(ret)), fpu: fpuEnv(), summary: ctxSummary(cur.args[0]) };
  const st = captureState(rootsFor(cur, 'exit'));
  Object.assign(cur.exit, st);
  cur.t[1] = tExit;
  cur.exit.capture_ms = Date.now() - t0 - tExit;
  const rec = cur;
  cur = null;
  rec.args = rec.args.map(hex);
  rec.esp = hex(rec.esp);
  writeJson('calls/' + String(rec.seq).padStart(6, '0') + '.json', rec);
  stats.calls++;
  calls.push({ seq: rec.seq, gen: rec.gen, attempt: rec.attempt, export: rec.export, eax: rec.exit.eax,
               in: [rec.entry.regions.length, rec.entry.bytes], out: [rec.exit.regions.length, rec.exit.bytes],
               allocs: rec.allocs.length, files: rec.files.length,
               foreign: [rec.entry.foreign.count, rec.exit.foreign.count], warnings: rec.warnings.length });
  // Foreign values are information, not errors: uninitialised capacity tails and recycled blocks
  // hold stale heap addresses. A value the code really dereferences shows up in the replay.
  if (rec.entry.foreign.count || rec.exit.foreign.count)
    send({ type: 'info', msg: 'call ' + rec.seq + ' ' + rec.export + ': foreign values ' + rec.entry.foreign.count + '/' + rec.exit.foreign.count });
  for (const w of rec.warnings) warn('call ' + rec.seq + ': ' + w);
}

function onDefaultLoaded(m) {
  if (defaultMod && defaultMod.base.equals(m.base)) return;
  defaultMod = m;
  loads.push({ t: Date.now() - t0, event: 'load', base: hex(u32(m.base)), tid: Process.getCurrentThreadId() });
  startTracking('Default.dll load');
  hookDefault(m);
}
function onDefaultUnloaded(m) {
  loads.push({ t: Date.now() - t0, event: 'unload', base: hex(u32(m.base)), tid: Process.getCurrentThreadId() });
  for (const h of defaultHooks) { try { h.detach(); } catch (e) { } }
  defaultHooks = []; defaultMod = null;
  Interceptor.flush();
  if (!genRec) stopTracking('Default.dll unload');
}
Process.attachModuleObserver({
  onAdded(m) { if (m.name.toLowerCase() === 'default.dll') onDefaultLoaded(m); },
  onRemoved(m) { if (m.name.toLowerCase() === 'default.dll') onDefaultUnloaded(m); },
});
{ const m = Process.findModuleByName('Default.dll'); if (m) { warn('Default.dll already loaded at attach: DllMain-time blocks are not tracked'); onDefaultLoaded(m); } }

// ---------------------------------------------------------------------------------------------
// EXE hooks: generation boundaries, forced seed, attempts, playability results.
Interceptor.attach(exe.base.add(EXE_RVA.generate), {
  onEnter(args) {
    const sp = this.context.esp;
    gen++; attempt = -1;
    genRec = { gen, tid: this.threadId, t: [Date.now() - t0, null], seed_arg: sp.add(0x14).readU32(),
               script: readUString(sp.add(0x10).readPointer()), attempts: [], playable: [] };
    const sched = exe.base.add(EXE_RVA.scheduler).readPointer();
    if (!sched.isNull()) genRec.seed_global = sched.add(0x54).readU32();
    if (P.seed !== null && P.seed !== undefined) {
      const s = Number(P.seed) >>> 0;
      sp.add(0x14).writeU32(s);
      if (!sched.isNull()) sched.add(0x54).writeU32(s);
      genRec.seed_forced = s;
    }
    startTracking('generation ' + gen);
  },
  onLeave(ret) {
    if (!genRec) return;
    genRec.ret = ret.toInt32();
    genRec.t[1] = Date.now() - t0;
    generations.push(genRec);
    writeJson('generations/' + String(genRec.gen).padStart(3, '0') + '.json', genRec);
    writeJson('loads.json', loads);
    genRec = null;
    generationsDone++;
    if (!defaultMod) stopTracking('generation done');
    if (P.stop_file && generationsDone >= P.generations) {
      writeJson('rmg_capture.json', snapshot());
      try { const f = new File(P.stop_file, 'w'); f.write('done\n'); f.close(); } catch (e) { warn('stop_file: ' + e.message); }
    }
  },
});
Interceptor.attach(exe.base.add(EXE_RVA.createRandomMap), {
  onEnter(args) {
    if (!genRec) return;
    const sp = this.context.esp;
    genRec.attempts.push({ t: Date.now() - t0, ctx: hex(u32(this.context.ecx) + 4), first_seq: seq,
                           player_arg: sp.add(4).readS32(), script_index: sp.add(8).readU32(), seed: sp.add(12).readU32() });
  },
});
Interceptor.attach(exe.base.add(EXE_RVA.playable), {
  onEnter(args) { this.ctx = u32(args[0]); },
  onLeave(ret) { if (genRec) genRec.playable.push({ attempt, ctx: hex(this.ctx), ok: (u32(ret) & 0xff) !== 0, after_seq: seq - 1 }); },
});

function snapshot() {
  return { schema: SCHEMA, case: P.case, params: P, elapsedMs: Date.now() - t0, tracking, trackingSince,
           liveBlocks: live.size, stats, generations, calls, loads, warnings };
}
send({ type: 'ready', exe: hex(u32(exe.base)), defaultLoaded: !!defaultMod, seed: P.seed });
rpc.exports = { snapshot, stop() { } };
