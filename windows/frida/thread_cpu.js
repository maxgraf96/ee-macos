// Per-thread CPU time (P7-A sched) for Empire Earth (GOG 2.00) on Windows, for windows/frida/run.py:
// how much of a core each of the game's threads uses, which tells the Mac's scheduler work
// whether the game's threads together need more than one core (docs/reference/performance.md,
// "Scheduling").
//
//   run.py --scripts perf_probe,thread_cpu --seconds 600 --out C:\EEPort\captures\perf\NAME ^
//          --param out=C:\EEPort\captures\perf\NAME\probe.csv
//
// writes threads.csv beside PARAMS.out (perf_probe.js's file), or PARAMS.threads_out.
//
// Rows (CSV, written straight to PARAMS.out; times are QueryPerformanceCounter milliseconds since
// the script loaded; CPU times in milliseconds):
//   # ...                        comments
//   T,t_ms,tid,user_ms,kernel_ms every PARAMS.interval_ms, for every thread of the process: its
//                                cumulative user and kernel time (GetThreadTimes)
//   N,t_ms,tid,start,frames      once per thread (and again if they change): its Win32 start
//                                address and the deepest return addresses into the game's
//                                modules found on its stack (module+offset, deepest first), which
//                                name the thread's body
//   S,t_ms,epoch_ms,pts          with every T round: the wall clock and the PTs executed so far
//                                (EEPhysics+0xb4; -1 with no game)
//
// PARAMS: out or threads_out (one is required), interval_ms (default 1000), frames (default 6).
// Read-only: no thread is suspended and nothing is hooked.
'use strict';

const P = Object.assign({ out: null, threads_out: null, interval_ms: 1000, frames: 6 },
  globalThis.PARAMS || {});
if (!P.out && !P.threads_out) throw new Error('thread_cpu: PARAMS.out or threads_out is required');
const OUT = P.threads_out || P.out.replace(/[^\\\/]*$/, 'threads.csv');

const k32 = Process.getModuleByName('kernel32.dll');
const ntdll = Process.getModuleByName('ntdll.dll');
const fn = (m, n, r, a) => new NativeFunction(m.getExportByName(n), r, a, 'stdcall');
const OpenThread = fn(k32, 'OpenThread', 'pointer', ['uint32', 'int', 'uint32']);
const CloseHandle = fn(k32, 'CloseHandle', 'int', ['pointer']);
const GetThreadTimes = fn(k32, 'GetThreadTimes', 'int', ['pointer', 'pointer', 'pointer', 'pointer', 'pointer']);
const NtQIT = fn(ntdll, 'NtQueryInformationThread', 'uint32', ['pointer', 'uint32', 'pointer', 'uint32', 'pointer']);
const qpc = fn(k32, 'QueryPerformanceCounter', 'int', ['pointer']);
const qpf = fn(k32, 'QueryPerformanceFrequency', 'int', ['pointer']);
const THREAD_QUERY_INFORMATION = 0x40;

const cell = Memory.alloc(8);
qpf(cell);
const freq = cell.readU64().toNumber();
qpc(cell);
const t0 = cell.readU64().toNumber();
function now() {
  qpc(cell);
  return (cell.readU64().toNumber() - t0) * 1000 / freq;
}

const file = new File(OUT, 'w');
let rows = 0;
function emit(line) { file.write(line + '\n'); rows++; }
function flush() { try { file.flush(); } catch (e) { /* closed */ } }
emit('# thread_cpu: QPC frequency ' + freq + ' Hz, interval ' + P.interval_ms + ' ms');

const GAME = ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'dx7hrdisplay.dll',
  'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll', 'miles sound system mixer.dll'];
const mods = Process.enumerateModules().filter(m => GAME.indexOf(m.name.toLowerCase()) >= 0);
function where(a) {
  for (const m of mods)
    if (a.compare(m.base) >= 0 && a.compare(m.base.add(m.size)) < 0)
      return (m.name.toLowerCase() === 'empire earth.exe' ? 'EXE' : m.name) + '+0x' + a.sub(m.base).toString(16);
  return null;
}

const ft = [Memory.alloc(8), Memory.alloc(8), Memory.alloc(8), Memory.alloc(8)];
const info = Memory.alloc(0x40);
const named = new Map(); // tid -> the N row last written

function ms100ns(p) { return p.readU64().toNumber() / 10000; }

// The deepest return addresses into the game's modules: scan down from the stack base (TEB+4)
// and keep the first P.frames dwords that point into a game module. Racy by nature (the stack
// is read while the thread runs) and only a name: the deepest frames of a thread's body loop
// do not move.
function frames(h) {
  if (NtQIT(h, 0 /* ThreadBasicInformation */, info, 0x1c, NULL) !== 0) return '';
  const teb = info.add(4).readPointer();
  if (teb.isNull()) return '';
  let base, limit;
  try { base = teb.add(4).readPointer(); limit = teb.add(8).readPointer(); } catch (e) { return ''; }
  const out = [];
  const lo = base.sub(0x4000).compare(limit) > 0 ? base.sub(0x4000) : limit;
  for (let p = base.sub(4); p.compare(lo) >= 0 && out.length < P.frames; p = p.sub(4)) {
    let v;
    try { v = p.readPointer(); } catch (e) { break; }
    const w = where(v);
    if (w) out.push(w);
  }
  return out.join(' ');
}

function start(h) {
  const s = Memory.alloc(4);
  if (NtQIT(h, 9 /* ThreadQuerySetWin32StartAddress */, s, 4, NULL) !== 0) return '?';
  const a = s.readPointer();
  return where(a) || a.toString();
}

const exe = Process.getModuleByName('empire earth.exe');
const physicsCell = exe.base.add(0x919774 - 0x400000);
function pts() {
  try {
    const ph = physicsCell.readPointer();
    return ph.isNull() ? -1 : ph.add(0xb4).readU32();
  } catch (e) { return -1; }
}

let rounds = 0;
function round() {
  const t = now().toFixed(3);
  for (const th of Process.enumerateThreads()) {
    const h = OpenThread(THREAD_QUERY_INFORMATION, 0, th.id);
    if (h.isNull()) continue;
    try {
      if (GetThreadTimes(h, ft[0], ft[1], ft[2], ft[3]))
        emit('T,' + t + ',' + th.id + ',' + ms100ns(ft[3]).toFixed(1) + ',' + ms100ns(ft[2]).toFixed(1));
      // Name each thread on its first rounds and every 30th after: a TSThread's frames settle
      // once its body runs.
      if (!named.has(th.id) || rounds % 30 === 0) {
        const row = start(h) + ',' + frames(h);
        if (named.get(th.id) !== row) {
          named.set(th.id, row);
          emit('N,' + t + ',' + th.id + ',' + row);
        }
      }
    } finally {
      CloseHandle(h);
    }
  }
  emit('S,' + t + ',' + Date.now() + ',' + pts());
  rounds++;
  flush();
}

round();
const timer = setInterval(round, P.interval_ms);

rpc.exports = {
  snapshot() { return { rows, rounds, threads: named.size, pts: pts() }; },
  stop() { clearInterval(timer); flush(); try { file.close(); } catch (e) { /* closed */ } },
};
