// Who sets the x87 control word, and how threads are created. Best loaded at spawn time.
//  - msvcrt _controlfp / _control87 / __control87_2 / _fpreset / _clearfp: caller thread, args,
//    CW before/after (read with fnstcw on the calling thread), backtrace.
//  - CreateThread / _beginthread / _beginthreadex: creator, start routine, new thread id.
//  - GOG wrapper: IDirectDraw7::SetCooperativeLevel flags (DDSCL_FPUSETUP 0x800,
//    DDSCL_FPUPRESERVE 0x1000, DDSCL_MULTITHREADED 0x400) and d3d9 IDirect3D9::CreateDevice
//    BehaviorFlags (D3DCREATE_FPU_PRESERVE 0x2), each with the CW before/after.
// Events go out with send(); snapshot() returns the same list.
'use strict';

const P = Object.assign({ max_events: 4000, backtrace: true }, globalThis.PARAMS || {});
const cm = new CModule(`
unsigned int getcw(void) { unsigned short cw; __asm__ __volatile__("fnstcw %0" : "=m"(cw)); return cw; }
`);
const getcw = new NativeFunction(cm.getcw, 'uint32', []);
const hex = v => '0x' + (v >>> 0).toString(16);
const events = [];
const t0 = Date.now();

const whereCache = new Map();
function where(addr) {
  const k = addr.toString();
  let r = whereCache.get(k);
  if (r !== undefined) return r;
  const m = Process.findModuleByAddress(addr);
  r = k;
  if (m) {
    r = m.name + '+0x' + addr.sub(m.base).toString(16);
    try { const s = DebugSymbol.fromAddress(addr); if (s.name && !s.name.startsWith('0x')) r += ' (' + s.name + ')'; } catch (e) { }
  }
  whereCache.set(k, r);
  return r;
}
function bt(ctx) {
  if (!P.backtrace) return undefined;
  try { return Thread.backtrace(ctx, Backtracer.FUZZY).slice(0, 10).map(where); } catch (e) { return [e.message]; }
}
function emit(e) {
  e.ms = Date.now() - t0;
  if (events.length < P.max_events) events.push(e);
  send({ type: 'event', event: e });
}
// Hot FPU calls (the game calls _controlfp thousands of times per second) are aggregated:
// one row per distinct (function, thread, args, CW before/after, caller); the first occurrence
// of each combination is also emitted as an event with a backtrace.
const combos = new Map();
function count(ctx, kind, tid, args, ret, cw0, cw1, retAddr) {
  const key = kind + '|' + tid + '|' + args + '|' + ret + '|' + cw0 + '|' + cw1 + '|' + retAddr;
  const c = combos.get(key);
  if (c) { c.count++; return; }
  const e = { kind, tid, args, ret, cw_before: hex(cw0), cw_after: hex(cw1), caller: where(retAddr), bt: bt(ctx), count: 1,
              firstMs: Date.now() - t0 };
  combos.set(key, e);
  emit(Object.assign({ first: true }, e));
}

function hookMsvcrt(crt) {
  for (const name of ['_controlfp', '_control87', '__control87_2', '_fpreset', '_clearfp']) {
    const a = crt.findExportByName(name);
    if (!a) continue;
    const withArgs = name.startsWith('_control');
    Interceptor.attach(a, {
      onEnter(args) {
        this.cw0 = getcw();
        this.args = withArgs ? hex(args[0].toUInt32()) + ',' + hex(args[1].toUInt32()) : '';
      },
      onLeave(ret) {
        count(this.context, name, this.threadId, this.args, hex(ret.toUInt32()), this.cw0, getcw(), this.returnAddress);
      },
    });
  }
}

function hookEngineFpu(m) {
  // Low-Level Engine's own FPU API (imported by the EXE and e.g. TCP Protocol.dll).
  for (const e of m.enumerateExports()) {
    if (!/^\?UM(Set|Get)FloatingPoint(Precision|RoundingMode)@@/.test(e.name)) continue;
    const short = e.name.slice(1, e.name.indexOf('@@'));
    const isSet = short.indexOf('Set') === 2;
    Interceptor.attach(e.address, {
      onEnter(args) { this.cw0 = getcw(); this.args = isSet ? hex(args[0].toUInt32()) : ''; },
      onLeave(ret) {
        count(this.context, short, this.threadId, this.args, isSet ? '' : hex(ret.toUInt32()), this.cw0, getcw(), this.returnAddress);
      },
    });
  }
}

let GetThreadId = null;
function threadIdOf(h) {
  if (!GetThreadId) {
    GetThreadId = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('GetThreadId'), 'uint32', ['pointer'], 'stdcall');
  }
  return GetThreadId(h);
}
function hookCreateThread(k32) {
  Interceptor.attach(k32.getExportByName('CreateThread'), {
    onEnter(args) { this.start = args[2]; this.param = args[3]; this.idp = args[5]; this.caller = where(this.returnAddress); },
    onLeave(ret) {
      const id = !ret.isNull() ? threadIdOf(ret) : 0;
      emit({ kind: 'CreateThread', tid: this.threadId, new_tid: id, start: where(this.start), param: this.param.toString(),
             caller: this.caller, cw: hex(getcw()) });
    },
  });
}
function hookCrtThreads(crt) {
  for (const name of ['_beginthread', '_beginthreadex']) {
    const a = crt.findExportByName(name);
    if (!a) continue;
    Interceptor.attach(a, {
      onEnter(args) { this.start = name === '_beginthread' ? args[0] : args[2]; this.caller = where(this.returnAddress); this.bt = bt(this.context); },
      onLeave(ret) {
        const h = ret.toInt32();
        const id = (h !== 0 && h !== -1) ? threadIdOf(ret) : 0;
        emit({ kind: name, tid: this.threadId, new_tid: id, start: where(this.start), caller: this.caller, bt: this.bt });
      },
    });
  }
}

const hookedVtbl = new Set();
function hookVtableSlot(obj, index, label, onEnter, onLeave) {
  const vtbl = obj.readPointer();
  const fnp = vtbl.add(index * 4).readPointer();
  const key = fnp.toString();
  if (hookedVtbl.has(key)) return;
  hookedVtbl.add(key);
  Interceptor.attach(fnp, { onEnter, onLeave });
  emit({ kind: 'hooked', what: label, fn: where(fnp) });
}

function hookCreateDevice(d3d, slot, label) {
  // IDirect3D9::CreateDevice = slot 16, IDirect3D9Ex::CreateDeviceEx = slot 20;
  // args: (this, Adapter, DevType, hFocus, BehaviorFlags, ...)
  hookVtableSlot(d3d, slot, label,
    function (args) { this.flags = args[4].toUInt32(); this.adapter = args[1].toUInt32(); this.cw0 = getcw(); this.bt = bt(this.context); },
    function (r) {
      emit({ kind: label, tid: this.threadId, adapter: this.adapter, behavior: hex(this.flags),
             fpu_preserve: (this.flags & 2) !== 0, hr: hex(r.toUInt32()), cw_before: hex(this.cw0), cw_after: hex(getcw()), bt: this.bt });
    });
}

function hookD3D9(m) {
  const a = m.findExportByName('Direct3DCreate9');
  if (a) Interceptor.attach(a, {
    onLeave(ret) {
      emit({ kind: 'Direct3DCreate9', tid: this.threadId, ok: !ret.isNull() });
      if (!ret.isNull()) hookCreateDevice(ret, 16, 'IDirect3D9::CreateDevice');
    },
  });
  const ax = m.findExportByName('Direct3DCreate9Ex');
  if (ax) Interceptor.attach(ax, {
    onEnter(args) { this.pp = args[1]; },
    onLeave(ret) {
      emit({ kind: 'Direct3DCreate9Ex', tid: this.threadId, hr: hex(ret.toUInt32()) });
      if (ret.toUInt32() !== 0) return;
      const d3d = this.pp.readPointer();
      hookCreateDevice(d3d, 16, 'IDirect3D9Ex::CreateDevice');
      hookCreateDevice(d3d, 20, 'IDirect3D9Ex::CreateDeviceEx');
    },
  });
}

function hookDDraw(m) {
  for (const name of ['DirectDrawEnumerateA', 'DirectDrawEnumerateExA', 'DirectDrawCreate']) {
    const e = m.findExportByName(name);
    if (e) Interceptor.attach(e, { onLeave(ret) { emit({ kind: name, tid: this.threadId, hr: hex(ret.toUInt32()), caller: where(this.returnAddress) }); } });
  }
  const a = m.findExportByName('DirectDrawCreateEx');
  if (!a) return;
  emit({ kind: 'hooked', what: 'DirectDrawCreateEx', fn: where(a), module: m.path });
  Interceptor.attach(a, {
    onEnter(args) { this.pp = args[1]; this.caller = where(this.returnAddress); },
    onLeave(ret) {
      emit({ kind: 'DirectDrawCreateEx', tid: this.threadId, hr: hex(ret.toUInt32()), caller: this.caller, cw: hex(getcw()) });
      if (ret.toUInt32() !== 0) return;
      const dd = this.pp.readPointer();
      // IDirectDraw7::SetCooperativeLevel = vtable slot 20 (this, hWnd, dwFlags)
      hookVtableSlot(dd, 20, 'IDirectDraw7::SetCooperativeLevel',
        function (args) { this.flags = args[2].toUInt32(); this.cw0 = getcw(); },
        function (r) {
          emit({ kind: 'IDirectDraw7::SetCooperativeLevel', tid: this.threadId, flags: hex(this.flags),
                 fpusetup: (this.flags & 0x800) !== 0, fpupreserve: (this.flags & 0x1000) !== 0,
                 multithreaded: (this.flags & 0x400) !== 0, hr: hex(r.toUInt32()), cw_before: hex(this.cw0), cw_after: hex(getcw()) });
        });
    },
  });
}

function hookLoadLibrary(k32) {
  // Module loading as the game does it (plugins, resource DLLs, Miles providers) with flags.
  for (const name of ['LoadLibraryA', 'LoadLibraryExA', 'LoadLibraryW', 'LoadLibraryExW']) {
    const a = k32.findExportByName(name);
    if (!a) continue;
    Interceptor.attach(a, {
      onEnter(args) {
        this.caller = Process.findModuleByAddress(this.returnAddress);
        if (!this.caller || P.all_loads !== true && ['empire earth.exe', 'low-level engine.dll', 'mss32.dll',
            'ddraw.dll', 'dx7hrdisplay.dll', 'dx7hrtnldisplay.dll', 'miles sound system mixer.dll',
            'tcp protocol.dll', 'binkw32.dll'].indexOf(this.caller.name.toLowerCase()) < 0) { this.skip = true; return; }
        this.path = name.endsWith('W') ? args[0].readUtf16String() : args[0].readAnsiString();
        this.flags = name.indexOf('Ex') > 0 ? args[2].toUInt32() : 0;
        this.from = where(this.returnAddress);
      },
      onLeave(ret) {
        if (this.skip) return;
        emit({ kind: name, tid: this.threadId, path: this.path, flags: hex(this.flags), handle: ret.toString(), caller: this.from });
      },
    });
  }
}

// Works attached (modules already present) and spawned (modules appear later).
const seen = new Set();
function onModule(m) {
  const n = m.name.toLowerCase();
  if (seen.has(n)) return;
  if (n === 'ddraw.dll' && m.path.toLowerCase().indexOf('empire earth') < 0) return;   // only GOG's wrapper
  seen.add(n);
  try {
    if (n === 'kernel32.dll') { hookCreateThread(m); hookLoadLibrary(m); }
    else if (n === 'msvcrt.dll') { hookMsvcrt(m); hookCrtThreads(m); }
    else if (n === 'low-level engine.dll') hookEngineFpu(m);
    else if (n === 'd3d9.dll') hookD3D9(m);
    else if (n === 'ddraw.dll') hookDDraw(m);
    else seen.delete(n);
  } catch (e) { emit({ kind: 'hook-error', module: m.name, error: e.message }); }
}
Process.enumerateModules().forEach(onModule);
Process.attachModuleObserver({ onAdded: onModule });
emit({ kind: 'loaded', tid: Process.getCurrentThreadId(), cw_js_thread: hex(getcw()) });

rpc.exports = { snapshot() { return { events, combos: Array.from(combos.values()) }; } };
