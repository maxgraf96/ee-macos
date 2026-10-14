// Keeps the game's plugin DLLs at their preferred bases (the M1 base map, mailbox 0007).
//
// Rebasing the DLLs in a game copy (editbin /REBASE) is not enough on its own: by the time the
// plugins load, the 163 MB data.ssa view, thread stacks and Miles filter providers already sit
// in 0x10300000-0x10800000, so the loader relocates them. This script (load it at spawn)
// reserves each guarded DLL's preferred range at once, releases it right before the game's
// LoadLibrary of that DLL, and reserves it again after a FreeLibrary that unloads it (the plugin
// scan loads and unloads every DLL several times; Default.dll is unloaded until map generation).
//
// Bases and sizes come from the DLLs' own PE headers in the EXE's folder. Every guarded load is
// recorded with the base it got; snapshot().all_at_base says whether all of them matched.
//
// PARAMS: dlls (names in the game folder; default: the plugins below).
//   run.py --spawn --exe "C:\EEPort\game-m1\Empire Earth\Empire Earth.exe" --scripts base_guard,modules ...
'use strict';

const P = Object.assign({
  dlls: ['Default.dll', 'Language.dll', 'DX7HRDisplay.dll', 'DX7HRTnLDisplay.dll', 'TCP Protocol.dll',
         'Miles Sound System Mixer.dll'],
}, globalThis.PARAMS || {});

const kbase = Process.getModuleByName('kernelbase.dll');
const k32 = Process.getModuleByName('kernel32.dll');
const fn = (m, n, r, a) => new NativeFunction(m.getExportByName(n), r, a, 'stdcall');
const VirtualAlloc = fn(k32, 'VirtualAlloc', 'pointer', ['pointer', 'size_t', 'uint32', 'uint32']);
const VirtualFree = fn(k32, 'VirtualFree', 'int', ['pointer', 'size_t', 'uint32']);
const MEM_RESERVE = 0x2000, MEM_RELEASE = 0x8000, PAGE_NOACCESS = 0x01;

const dir = Process.mainModule.path.replace(/[^\\]+$/, '');
const guards = {};          // lower-case name -> { name, base, size, reserved }
const events = [];          // { t_ms, op, name, base?, ok?, flags? }
const t0 = Date.now();

function peBaseSize(path) {
  const b = File.readAllBytes(path);
  const v = new DataView(b);
  const pe = v.getUint32(0x3c, true);
  return { base: v.getUint32(pe + 0x34, true), size: v.getUint32(pe + 0x50, true) };
}

function log(e) { e.t_ms = Date.now() - t0; events.push(e); send({ type: 'base_guard', event: e }); }

function reserve(g) {
  const len = (g.size + 0xffff) & ~0xffff;
  const r = VirtualAlloc(ptr(g.base), len, MEM_RESERVE, PAGE_NOACCESS);
  g.reserved = !r.isNull() && r.toUInt32() === g.base;
  log({ op: 'reserve', name: g.name, base: '0x' + g.base.toString(16), ok: g.reserved });
}

function release(g) {
  if (!g.reserved) return;
  VirtualFree(ptr(g.base), 0, MEM_RELEASE);
  g.reserved = false;
}

P.dlls.forEach(name => {
  const { base, size } = peBaseSize(dir + name);
  const g = guards[name.toLowerCase()] = { name, base, size, reserved: false };
  const m = Process.findModuleByAddress(ptr(base));
  if (m) log({ op: 'occupied', name, base: '0x' + base.toString(16), by: m.name });
  else reserve(g);
});

Interceptor.attach(kbase.getExportByName('LoadLibraryExW'), {
  onEnter(args) {
    const path = args[0].isNull() ? '' : args[0].readUtf16String();
    const g = guards[path.replace(/^.*[\\/]/, '').toLowerCase()];
    if (!g) return;
    this.g = g;
    this.flags = args[2].toUInt32();
    release(g);
  },
  onLeave(ret) {
    const g = this.g;
    if (!g) return;
    if (ret.isNull()) { log({ op: 'load_failed', name: g.name }); reserve(g); return; }
    // LOAD_LIBRARY_AS_DATAFILE / AS_IMAGE_RESOURCE map anywhere and don't run code: not counted.
    const dataOnly = (this.flags & 0x62) !== 0;
    const at = ret.and(ptr(0xffff0000)).toUInt32();
    log({ op: 'load', name: g.name, base: '0x' + at.toString(16), ok: at === g.base, flags: '0x' + this.flags.toString(16),
          data_only: dataOnly });
  },
});

Interceptor.attach(kbase.getExportByName('FreeLibrary'), {
  onEnter(args) {
    const h = args[0].toUInt32();
    for (const k in guards) if (guards[k].base === h) this.g = guards[k];
  },
  onLeave() {
    const g = this.g;
    if (g && !Process.findModuleByAddress(ptr(g.base))) {   // really unloaded (refcount hit 0)
      log({ op: 'unloaded', name: g.name });
      reserve(g);
    }
  },
});

function snapshot() {
  const loads = events.filter(e => e.op === 'load' && !e.data_only);
  return { dir, guards: Object.values(guards).map(g => ({ name: g.name, base: '0x' + g.base.toString(16),
           size: '0x' + g.size.toString(16), reserved_now: g.reserved })),
           loads: loads.length, loads_at_base: loads.filter(e => e.ok).length,
           all_at_base: loads.every(e => e.ok), events };
}

function stop() {
  Object.values(guards).forEach(release);
  return {};
}

rpc.exports = { snapshot, stop };
