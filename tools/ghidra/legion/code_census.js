// Executed-code census for the function DB (run on legion with windows/frida/run.py).
//
// Follows the game's own threads with Frida Stalker, with all non-game modules excluded, and
// records each basic block the first time it is compiled: module, RVA, length. Nothing about the
// code's content is recorded. tools/ghidra/legion/check_census.py then checks the blocks
// against db/functions/*.json: every executed block must lie in a DB function body, no
// block may lie in the DRM sections, and every executed entry is a DB function start.
//
// Which threads: only those whose Win32StartAddress lies in a game module, i.e. main (EXE CRT
// entry) and the engine's TSThreads (TSThread::WIN32ThreadMain: sim, render, input, music,
// asset worker, sound, network): ~11 of ~50 threads in-game. Game code that runs as a callback
// on a foreign thread is not seen. PARAMS.threads = 'all' takes any thread that hits a hook.
//
// How threads are followed: each thread starts Stalker on *itself*, from an Interceptor hook,
//   - new threads at TSThread::WIN32ThreadMain (the engine's thread entry);
//   - threads already running at a "catcher" they call often (Sleep, waits, PeekMessageA,
//     TSThread::Yield); catchers are detached once every live game thread is followed.
// Following another thread remotely (Stalker.follow(tid) suspends it) deadlocked the target
// against frida-agent.dll in ~1 of 4 attaches on a test target (Frida 17.23.3, WOW64), and
// following all ~50 threads made script load time out in-game.
//
// How blocks are recorded: a native (CModule) transform appends (start, length) of each block
// it compiles to a ring buffer owned by that thread; the JS side drains the rings every sweep.
// Stalker's event sink is not used (it routes every compile through the agent's locks).
// 32-bit targets only (addresses are stored as 32 bits).
//
// Load returns at once (hooks go in from a timer). stop() only requests the unfollows and
// returns; run.py then eternalizes the script instead of unloading it, because unloading waits
// until every followed thread has left Stalker, and a thread blocked in a long wait never does.
//
// PARAMS: threads 'game' | 'all'; modules (names to record, default GAME); start_modules (a
// thread must start in one of these, default = modules); entry_hooks / catchers ([module,
// export] lists); sweep_ms (1000); ring_blocks (per thread, power of 2, default 131072);
// manual_start.
//
// Copy to C:\EEPort\frida\ and run, e.g. (attach at the menu, then play a skirmish):
//   run.py --scripts code_census --seconds 600 --every 60 --out C:\EEPort\captures\census
//   run.py --spawn --scripts code_census --seconds 300 --out C:\EEPort\captures\census-spawn
// Expect a large slowdown while new code is first compiled; it settles once warm.
'use strict';

if (Process.pointerSize !== 4) throw new Error('code_census.js: 32-bit targets only');

const GAME = ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'language.dll',
              'dx7hrdisplay.dll', 'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll',
              'miles sound system mixer.dll'];
const P = Object.assign({
  threads: 'game', modules: GAME, sweep_ms: 1000, ring_blocks: 131072,
  entry_hooks: [['low-level engine.dll', '?WIN32ThreadMain@TSThread@@CGKPAX@Z']],
  catchers: [['kernel32.dll', 'Sleep'], ['kernel32.dll', 'SleepEx'], ['kernel32.dll', 'WaitForSingleObject'],
             ['kernel32.dll', 'WaitForSingleObjectEx'], ['kernel32.dll', 'WaitForMultipleObjects'],
             ['user32.dll', 'PeekMessageA'], ['user32.dll', 'GetMessageA'],
             ['low-level engine.dll', '?Yield@TSThread@@SAXK@Z']],
}, globalThis.PARAMS || {});
const RECORD = P.modules.map(s => s.toLowerCase());
const STARTS = (P.start_modules || P.modules).map(s => s.toLowerCase());
const RING = P.ring_blocks;
if (RING & (RING - 1)) throw new Error('ring_blocks must be a power of 2');

const k32 = Process.getModuleByName('kernel32.dll');
const ntdll = Process.getModuleByName('ntdll.dll');
const fn = (m, n, r, a) => new NativeFunction(m.getExportByName(n), r, a, 'stdcall');
const OpenThread = fn(k32, 'OpenThread', 'pointer', ['uint32', 'int', 'uint32']);
const CloseHandle = fn(k32, 'CloseHandle', 'int', ['pointer']);
const NtQIT = fn(ntdll, 'NtQueryInformationThread', 'uint32', ['pointer', 'uint32', 'pointer', 'uint32', 'pointer']);
const startBuf = Memory.alloc(4);

// ring: [0] blocks written so far (only the owning thread writes it), [1] capacity, then pairs.
const cm = new CModule(`
#include <gum/gumstalker.h>
void transform(GumStalkerIterator *it, GumStalkerOutput *out, gpointer user_data) {
  volatile guint32 *ring = user_data;
  const cs_insn *insn;
  guint32 first = 0, end = 0, n, slot;
  gboolean any = FALSE;
  while (gum_stalker_iterator_next(it, &insn)) {
    if (!any) { first = (guint32) insn->address; any = TRUE; }
    end = (guint32) (insn->address + insn->size);
    gum_stalker_iterator_keep(it);
  }
  if (!any) return;
  n = ring[0];
  slot = 2 + 2 * (n & (ring[1] - 1));
  ring[slot] = first;
  ring[slot + 1] = end - first;
  ring[0] = n + 1;
}
`);

const blocks = {};          // module name -> Map(rva -> len)
const rings = new Map();    // key -> { buf, read, lost }; kept for the script's lifetime
const active = new Set();   // tids currently followed
const skipped = new Map();  // tid -> start, threads not to follow (pruned when they exit)
const pending = new Set();  // live game threads not followed yet
const history = [];         // every thread ever followed: { tid, start, via, t_ms }
const hooked = new Map();   // 'module!export' -> listener (entry hooks and catchers)
const excluded = new Set();
const t0 = Date.now();
let mods = [];
let timer = null;
let stopping = false;

function refreshModules() {
  const all = Process.enumerateModules();
  mods = all.filter(m => RECORD.indexOf(m.name.toLowerCase()) >= 0)
    .map(m => ({ name: m.name, base: m.base, lo: m.base.toUInt32(), hi: m.base.toUInt32() + m.size }));
  all.forEach(m => {
    const key = m.base.toString();
    if (RECORD.indexOf(m.name.toLowerCase()) < 0 && !excluded.has(key)) {
      excluded.add(key);
      try { Stalker.exclude({ base: m.base, size: m.size }); } catch (e) { /* already */ }
    }
  });
}

// Win32StartAddress of a thread as { module (lower case) | null, text }.
function startOf(tid) {
  const h = OpenThread(0x40, 0, tid);   // THREAD_QUERY_INFORMATION
  if (h.isNull()) return null;
  try {
    if (NtQIT(h, 9, startBuf, 4, NULL) !== 0) return null;
    const a = startBuf.readPointer();
    const m = Process.findModuleByAddress(a);
    return m ? { module: m.name.toLowerCase(), text: m.name + '+0x' + a.sub(m.base).toString(16) }
             : { module: null, text: a.toString() };
  } finally {
    CloseHandle(h);
  }
}

function isGame(s) { return P.threads === 'all' || (s !== null && STARTS.indexOf(s.module) >= 0); }

function record(start, len) {
  for (const m of mods) {
    if (start >= m.lo && start < m.hi) {
      let map = blocks[m.name];
      if (!map) { map = new Map(); blocks[m.name] = map; }
      if (!map.has(start - m.lo)) map.set(start - m.lo, len);
      return;
    }
  }
}

function drain() {
  rings.forEach(r => {
    const n = r.buf.readU32();
    if (n - r.read > RING) { r.lost += n - r.read - RING; r.read = n - RING; }
    while (r.read !== n) {
      const a = r.read & (RING - 1);
      const c = Math.min(n - r.read, RING - a);
      const w = new Uint32Array(r.buf.add(8 + 8 * a).readByteArray(8 * c));
      for (let i = 0; i < 2 * c; i += 2) record(w[i], w[i + 1]);
      r.read += c;
    }
  });
}

// Runs on the thread itself, inside an Interceptor callback: Stalker follows the current thread
// once the callback returns, without suspending anything.
function followSelf(tid, via) {
  if (stopping || active.has(tid) || skipped.has(tid)) return;
  const s = startOf(tid);
  if (!isGame(s)) { skipped.set(tid, s ? s.text : '?'); return; }
  const buf = Memory.alloc(8 + 8 * RING);
  buf.writeU32(0);
  buf.add(4).writeU32(RING);
  rings.set(tid + ':' + history.length, { buf, read: 0, lost: 0 });
  active.add(tid);
  pending.delete(tid);
  history.push({ tid, start: s ? s.text : '?', via, t_ms: Date.now() - t0 });
  Stalker.follow(tid, { transform: cm.transform, data: buf });
}

function hook(mod, name) {
  const key = mod + '!' + name;
  if (hooked.has(key)) return;
  const m = Process.findModuleByName(mod);
  const a = m && m.findExportByName(name);
  if (!a) return;
  hooked.set(key, Interceptor.attach(a, { onEnter() { followSelf(this.threadId, key); } }));
}

function unhook(key) {
  const l = hooked.get(key);
  if (l) { l.detach(); hooked.delete(key); }
}

function sweep() {
  drain();
  if (stopping) return;
  refreshModules();
  P.entry_hooks.forEach(([m, n]) => hook(m, n));   // the engine may load after us (spawn)
  const live = new Set();
  Process.enumerateThreads().forEach(t => {
    live.add(t.id);
    if (active.has(t.id) || skipped.has(t.id) || pending.has(t.id) || t.id === Process.getCurrentThreadId()) return;
    const s = startOf(t.id);
    if (isGame(s)) pending.add(t.id); else skipped.set(t.id, s ? s.text : '?');
  });
  // Thread ids get reused: forget exited threads so a new thread with an old id is looked at again.
  for (const tid of [...skipped.keys()]) if (!live.has(tid)) skipped.delete(tid);
  for (const tid of [...active]) if (!live.has(tid)) active.delete(tid);
  for (const tid of [...pending]) if (!live.has(tid)) pending.delete(tid);
  // Catchers only while some live game thread is not followed yet.
  if (pending.size) P.catchers.forEach(([m, n]) => hook(m, n));
  else P.catchers.forEach(([m, n]) => unhook(m + '!' + n));
}

function start() {
  if (timer || stopping) return;
  sweep();
  timer = setInterval(sweep, P.sweep_ms);   // threads and plugin DLLs come and go
}

function snapshot() {
  drain();
  const starts = {};
  skipped.forEach(s => { const k = s.replace(/\+0x[0-9a-f]+$/, ''); starts[k] = (starts[k] || 0) + 1; });
  let lost = 0;
  rings.forEach(r => { lost += r.lost; });
  const out = { modules: {}, threads_followed: history.length, threads_active: active.size,
                threads_pending: [...pending], threads: history, skipped_by_start_module: starts,
                hooks: [...hooked.keys()], mode: P.threads, blocks_lost: lost, elapsed_ms: Date.now() - t0 };
  for (const m of mods) out.modules[m.name] = { base: m.base.toString() };
  for (const name in blocks) {
    const arr = [];
    blocks[name].forEach((len, rva) => arr.push([rva, len]));
    arr.sort((a, b) => a[0] - b[0]);
    out.modules[name] = Object.assign(out.modules[name] || {}, { blocks: arr });
  }
  return out;
}

// Non-blocking: remove the hooks, request the unfollows and return. Followed threads leave
// Stalker on their next block; run.py eternalizes the script instead of unloading it.
function stop() {
  drain();
  stopping = true;
  if (timer) clearInterval(timer);
  timer = null;
  [...hooked.keys()].forEach(unhook);
  let n = 0;
  active.forEach(tid => { try { Stalker.unfollow(tid); n++; } catch (e) { /* gone */ } });
  return { unfollow_requested: n, eternalize: true };
}

if (!P.manual_start) setTimeout(start, 0);
rpc.exports = { snapshot, stop, start };
