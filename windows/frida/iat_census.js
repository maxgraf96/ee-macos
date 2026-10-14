// API census + per-thread x87 control word at API-call time, by IAT patching.
//
// Every function import of the game modules (from non-game DLLs, i.e. the HLE surface) gets its
// IAT slot redirected to a 47-byte stub:
//     cmp byte [enabled],0 / je direct / pushfd / pushad / sub esp,4 / fnstcw [esp] /
//     mov eax,fs:[0x24] (thread id) / push eax / push fn / call record / add esp,12 / popad / popfd
//     direct: jmp [orig]
// record(fn, tid, cw) bumps a (tid, fn, cw) counter in a hash table. So the census counts only
// calls *made by game code* (not by the wrapper, drivers or system DLLs), and each row also says
// which thread called it with which control word, e.g. _ftol/sin/cos calls from the simulation.
// Data imports (msvcrt _iob, msvcp60 statics, ...) are skipped: their targets are not executable.
// Stubs, table and originals live in VirtualAlloc'd memory that is never freed; stop() disables
// the stubs and restores the IAT before the script is unloaded.
//
// PARAMS: internal (bool) also count imports between game modules (engine C++ API, Dinkumware).
'use strict';

const P = Object.assign({ internal: false, dry: false, only: null }, globalThis.PARAMS || {});
// Language.dll is not patched: it has no imports and is mapped as a resource module, and Frida's
// enumerateImports() on it crashes the game (seen with Frida 17.23.3).
const GAME = ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'dx7hrdisplay.dll',
  'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll', 'miles sound system mixer.dll'];
const INTERNAL = new Set(GAME.concat(['language.dll']));

const k32 = Process.getModuleByName('kernel32.dll');
const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer',
  ['pointer', 'uint', 'uint32', 'uint32'], 'stdcall');

const NB = 1 << 16;                // hash table entries (16 bytes each)
const MAXFN = 4096;
const STUB = 64;
const mem = VirtualAlloc(NULL, 0x1000 + NB * 16 + MAXFN * (4 + STUB), 0x3000, 0x40);  // RWX
if (mem.isNull()) throw new Error('VirtualAlloc failed');
const enabled = mem;               // byte flag
const lockv = mem.add(16);
const table = mem.add(0x1000);
const origs = table.add(NB * 16);
const stubs = origs.add(MAXFN * 4);

const cm = new CModule(`
typedef struct { unsigned int tid, fn, cw, count; } Rec;
extern Rec table[];
extern volatile int lockv;
extern volatile unsigned int dropped;
#define NB ${NB}
/* (an asm "m" operand naming an extern symbol directly faults under Frida's TinyCC; go via a pointer) */
static int xchg(volatile int *p, int v) {
  __asm__ __volatile__("xchgl %0, (%1)" : "=r"(v) : "r"(p), "0"(v) : "memory");
  return v;
}
static void spin_lock(void) { while (xchg(&lockv, 1)) { } }
void record(unsigned int fn, unsigned int tid, unsigned int cw) {
  unsigned int h, probes = 0;
  Rec *r;
  cw &= 0xffff;
  h = ((tid * 2654435761u) ^ (fn * 40503u) ^ (cw * 2246822519u)) & (NB - 1);
  spin_lock();
  for (;;) {
    r = &table[h];
    if (r->count == 0) { r->tid = tid; r->fn = fn; r->cw = cw; r->count = 1; break; }
    if (r->tid == tid && r->fn == fn && r->cw == cw) { r->count++; break; }
    h = (h + 1) & (NB - 1);
    if (++probes > 512) { dropped++; break; }
  }
  lockv = 0;
}
`, { table, lockv, dropped: mem.add(32) });

const fns = [];                    // index -> {module, dll, name, slot, orig}
const patchedSlots = new Map();    // slot address string -> fn index
let startedAt = null;

function writeStub(i, target) {
  const s = stubs.add(i * STUB);
  const origSlot = origs.add(i * 4);
  origSlot.writePointer(target);
  const b = [];
  const u32 = v => { v = ptr(v).toUInt32(); b.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff); };
  b.push(0x80, 0x3d); u32(enabled); b.push(0x00);           // cmp byte [enabled], 0
  b.push(0x74, 0x20);                                         // je +32 -> direct
  b.push(0x9c, 0x60);                                         // pushfd; pushad
  b.push(0x83, 0xec, 0x04);                                   // sub esp, 4
  b.push(0xd9, 0x3c, 0x24);                                   // fnstcw [esp]
  b.push(0x64, 0xa1, 0x24, 0x00, 0x00, 0x00);                 // mov eax, fs:[0x24]
  b.push(0x50);                                               // push eax
  b.push(0x68); u32(i);                                       // push fn
  b.push(0xb8); u32(cm.record);                               // mov eax, record
  b.push(0xff, 0xd0);                                         // call eax
  b.push(0x83, 0xc4, 0x0c);                                   // add esp, 12
  b.push(0x61, 0x9d);                                         // popad; popfd
  b.push(0xff, 0x25); u32(origSlot);                          // direct: jmp [orig]
  if (b.length !== 47) throw new Error('stub size ' + b.length);
  s.writeByteArray(b);
  return s;
}

function isExecutable(p) {
  const r = Process.findRangeByAddress(p);
  return r !== null && r.protection.indexOf('x') >= 0;
}

// Own PE import walker: Frida's enumerateImports() omits imports by ordinal (TCP Protocol.dll
// imports 23 of its 24 ws2_32 functions by ordinal, the EXE its wsock32 ones).
function enumImportsPE(m) {
  const base = m.base;
  const nt = base.add(base.add(0x3c).readU32());
  const impRva = nt.add(0x80).readU32();           // PE32 DataDirectory[IMPORT]
  const res = [];
  if (!impRva) return res;
  for (let d = base.add(impRva); d.add(12).readU32() !== 0; d = d.add(20)) {
    const dll = base.add(d.add(12).readU32()).readAnsiString();
    const oft = d.readU32(), ft = d.add(16).readU32();
    let t = base.add(oft || ft), slot = base.add(ft);
    for (; t.readU32() !== 0; t = t.add(4), slot = slot.add(4)) {
      const v = t.readU32();
      const address = slot.readPointer();
      let name = null, ordinal = null;
      if (v & 0x80000000) {
        ordinal = v & 0xffff;
        try { const sym = DebugSymbol.fromAddress(address); if (sym.name && !sym.name.startsWith('0x')) name = sym.name; } catch (e) { }
        if (!name) name = '#' + ordinal;
      } else {
        name = base.add(v + 2).readAnsiString();
      }
      res.push({ module: dll, name, ordinal, slot, address });
    }
  }
  return res;
}

function patchModule(m) {
  let n = 0, skippedData = 0;
  for (const imp of enumImportsPE(m)) {
    if (!imp.slot || !imp.address) continue;
    const dll = (imp.module || '?').toLowerCase();
    if (!P.internal && INTERNAL.has(dll)) continue;
    const key = imp.slot.toString();
    if (patchedSlots.has(key)) continue;
    if (!isExecutable(imp.address)) { skippedData++; continue; }
    if (P.only && P.only.indexOf(imp.name) < 0) continue;
    if (P.dry) { dryList.push([m.name, dll, imp.name, imp.slot.toString(), imp.address.toString(), (Process.findRangeByAddress(imp.slot) || {}).protection]); continue; }
    if (fns.length >= MAXFN) break;
    const i = fns.length;
    const stub = writeStub(i, imp.address);
    fns.push({ module: m.name, dll, name: imp.name, ordinal: imp.ordinal, slot: imp.slot, orig: imp.address });
    const r = Process.findRangeByAddress(imp.slot);
    const prot = r ? r.protection : 'r--';
    Memory.protect(imp.slot, 4, prot.replace(/^r./, 'rw'));
    imp.slot.writePointer(stub);
    Memory.protect(imp.slot, 4, prot);
    patchedSlots.set(key, i);
    n++;
  }
  return { module: m.name, patched: n, skippedData };
}

const report = [];
const dryList = [];
function patchAll() {
  for (const m of Process.enumerateModules()) {
    if (GAME.indexOf(m.name.toLowerCase()) >= 0 && !report.some(r => r.module === m.name)) report.push(patchModule(m));
  }
}

function start() {
  patchAll();
  enabled.writeU8(1);
  startedAt = Date.now();
  return report;
}

function snapshot() {
  const rows = [];
  const buf = new Uint32Array(table.readByteArray(NB * 16));
  for (let i = 0; i < NB; i++) {
    const count = buf[i * 4 + 3];
    if (!count) continue;
    const f = fns[buf[i * 4 + 1]];
    rows.push({ tid: buf[i * 4], module: f.module, dll: f.dll, name: f.name, cw: '0x' + buf[i * 4 + 2].toString(16).padStart(4, '0'), count });
  }
  if (P.dry) return { dry: dryList, patched: report };
  return { elapsedMs: Date.now() - startedAt, dropped: mem.add(32).readU32(), patched: report,
           imports: fns.map(f => [f.module, f.dll, f.name]), rows };
}

function stop() {
  enabled.writeU8(0);
  for (const [key, i] of patchedSlots) {
    const slot = ptr(key);
    const r = Process.findRangeByAddress(slot);
    const prot = r ? r.protection : 'r--';
    Memory.protect(slot, 4, prot.replace(/^r./, 'rw'));
    slot.writePointer(fns[i].orig);
    Memory.protect(slot, 4, prot);
  }
  return patchedSlots.size;
}

// Plugins loaded later (e.g. Default.dll for random maps) get patched when they appear.
Process.attachModuleObserver({
  onAdded(m) { if (startedAt !== null && GAME.indexOf(m.name.toLowerCase()) >= 0) report.push(patchModule(m)); },
});

rpc.exports = { start, snapshot, stop };
if (!P.manual_start) start();
