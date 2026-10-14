// Executed-code census for the function DB (run on legion with windows/frida/run.py).
//
// Follows every thread with Frida Stalker, with all non-game modules excluded, and records
// each basic block the first time it is compiled: module, RVA, length. Nothing about the
// code's content is recorded. tools/ghidra/legion/check_census.py then checks the blocks
// against db/functions/*.json: every executed block must lie in a DB function body, no
// block may lie in the DRM sections, and every executed entry is a DB function start.
//
// Copy to C:\EEPort\frida\ and run, e.g. (attach at the menu, then play a skirmish):
//   run.py --scripts code_census --seconds 600 --out C:\EEPort\captures\census
//   run.py --spawn --scripts code_census --seconds 300 --out C:\EEPort\captures\census-spawn
// Expect a large slowdown while new code is first compiled; it settles once warm.
'use strict';

const GAME = ['empire earth.exe', 'low-level engine.dll', 'sxlrt233.dll', 'language.dll',
              'dx7hrdisplay.dll', 'dx7hrtnldisplay.dll', 'default.dll', 'tcp protocol.dll',
              'miles sound system mixer.dll'];
const blocks = {};          // module name -> Map(rva -> len)
const followed = new Set();
let mods = [];
let timer = null;

function refreshModules() {
  mods = Process.enumerateModules().filter(m => GAME.indexOf(m.name.toLowerCase()) >= 0)
    .map(m => ({ name: m.name, base: m.base, end: m.base.add(m.size) }));
  Process.enumerateModules().forEach(m => {
    if (GAME.indexOf(m.name.toLowerCase()) < 0) {
      try { Stalker.exclude({ base: m.base, size: m.size }); } catch (e) { /* already */ }
    }
  });
}

function record(start, end) {
  for (const m of mods) {
    if (start.compare(m.base) >= 0 && start.compare(m.end) < 0) {
      const rva = start.sub(m.base).toUInt32();
      let map = blocks[m.name];
      if (!map) { map = new Map(); blocks[m.name] = map; }
      if (!map.has(rva)) map.set(rva, end.sub(start).toUInt32());
      return;
    }
  }
}

function follow(tid) {
  if (followed.has(tid) || tid === Process.getCurrentThreadId()) return;
  followed.add(tid);
  Stalker.follow(tid, {
    events: { compile: true },
    onReceive(ev) {
      for (const e of Stalker.parse(ev, { annotate: false, stringify: false })) record(e[0], e[1]);
    },
  });
}

function sweep() {
  refreshModules();
  Process.enumerateThreads().forEach(t => follow(t.id));
}

function start() {
  if (timer) return;
  sweep();
  timer = setInterval(sweep, 1000);   // threads and plugin DLLs come and go
}

function snapshot() {
  Stalker.flush();
  const out = { modules: {}, threads_followed: followed.size };
  for (const m of mods) out.modules[m.name] = { base: m.base.toString() };
  for (const name in blocks) {
    const arr = [];
    blocks[name].forEach((len, rva) => arr.push([rva, len]));
    arr.sort((a, b) => a[0] - b[0]);
    out.modules[name] = Object.assign(out.modules[name] || {}, { blocks: arr });
  }
  return out;
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
  followed.forEach(tid => { try { Stalker.unfollow(tid); } catch (e) { /* gone */ } });
  Stalker.garbageCollect();
}

start();
rpc.exports = { snapshot, stop, start };
