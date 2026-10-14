// dm_setup_hooks.js: DirectMusic / Miles setup census for docs/reference/audio.md §11.
// Run at SPAWN (windows/frida/run.py --spawn --scripts dm_setup_hooks ...) so the engine's
// SEConductor::InitializeConductor is observed. Answers: synth port parameters as written back by
// IDirectMusic::CreatePort (voices, sample rate, audio channels), port caps and wave format,
// KsProperty volume / effects / Waves-reverb values, performance global params, loader paths and
// objects, AutoTransition arguments, PlaySegment flags, Miles driver format and DirectSound caps,
// SPIM channel counts, and every SEConductor call from startup through the menu into the game.
//
// PARAMS: max_events (default 6000), probe_after_ms (SPIM channel-count probe, default 90000).
'use strict';

const P = Object.assign({ max_events: 6000, probe_after_ms: 90000 }, globalThis.PARAMS || {});
const t0 = Date.now();
const events = [];
const hooked = new Set();

function log(kind, data) {
  if (events.length < P.max_events) events.push([Date.now() - t0, Process.getCurrentThreadId(), kind, data]);
}
function hx(v, n) { return (v >>> 0).toString(16).padStart(n, '0').toUpperCase(); }
function guid(p) {
  if (p.isNull()) return 'NULL';
  const b = new Uint8Array(p.add(8).readByteArray(8));
  const t = Array.from(b, x => hx(x, 2)).join('');
  return `{${hx(p.readU32(), 8)}-${hx(p.add(4).readU16(), 4)}-${hx(p.add(6).readU16(), 4)}-${t.slice(0, 4)}-${t.slice(4)}}`;
}
const D = '-B39B-11D1-8704-00600893B1BD}';
const NAMES = {
  ['{D2AC2892' + D]: 'CLSID_DirectMusicLoader', ['{D2AC2890' + D]: 'CLSID_DirectMusicComposer',
  ['{D2AC2881' + D]: 'CLSID_DirectMusicPerformance', ['{D2AC28A7' + D]: 'GUID_Download',
  ['{D2AC28A8' + D]: 'GUID_Unload', ['{D2AC28A1' + D]: 'GUID_IDirectMusicStyle',
  ['{D2AC28B0' + D]: 'GUID_PerfMasterTempo', ['{D2AC28B1' + D]: 'GUID_PerfMasterVolume',
  ['{D2AC28B2' + D]: 'GUID_PerfMasterGrooveLevel', ['{D2AC2893' + D]: 'GUID_DirectMusicAllTypes',
  '{FB09565B-3631-11D2-BCB8-00A0C922E6EB}': 'GUID_PerfAutoDownload',
  '{FEDFAE25-E46E-11D1-AACE-0000F875AC12}': 'GUID_DMUS_PROP_Volume',
  '{CDA8D611-684A-11D2-871E-00600893B1BD}': 'GUID_DMUS_PROP_Effects',
  '{04CB5622-32E5-11D2-AFA6-00AA0024D8B6}': 'GUID_DMUS_PROP_WavesReverb',
  '{28F54685-06FD-11D2-B27A-00A0C9223196}': 'IID_IKsControl',
};
const name = g => NAMES[g] || g;

// Attach to vtable slot `idx` of `obj` once per implementation address.
function slot(obj, idx, label, cb) {
  if (obj.isNull()) return;
  const fn = obj.readPointer().add(idx * 4).readPointer();
  const key = label + '@' + fn;
  if (hooked.has(key)) return;
  hooked.add(key);
  try { Interceptor.attach(fn, cb); } catch (e) { log('hook-error', { label, error: e.message }); }
}
function hr(r) { return '0x' + hx(r.toUInt32(), 8); }

// ---- IKsControl (synth properties) -------------------------------------------------------------
function hookKs(ks) {
  slot(ks, 3, 'KsProperty', {
    onEnter(a) {
      this.set = name(guid(a[1])); this.id = a[1].add(16).readU32(); this.flags = a[1].add(20).readU32();
      this.data = a[3]; this.len = a[4].toUInt32();
      if (this.flags & 2) this.value = readProp(this.data, this.len);
    },
    onLeave(r) {
      const v = (this.flags & 1) ? readProp(this.data, this.len) : this.value;
      log('KsProperty', { set: this.set, id: this.id, op: (this.flags & 1) ? 'GET' : 'SET', value: v, hr: hr(r) });
    },
  });
}
function readProp(p, len) {
  if (p.isNull()) return null;
  if (len === 4) return p.readS32();
  if (len === 16) return [0, 4, 8, 12].map(o => p.add(o).readFloat());
  return Array.from(new Uint8Array(p.readByteArray(Math.min(len, 32))));
}

// ---- IDirectMusicPort ------------------------------------------------------------------------
const GetFormatCache = new Map();
function portFormat(port) {
  try {
    const fn = port.readPointer().add(19 * 4).readPointer(); // IDirectMusicPort::GetFormat
    const f = GetFormatCache.get(fn.toString()) ||
      new NativeFunction(fn, 'uint32', ['pointer', 'pointer', 'pointer', 'pointer'], 'stdcall');
    GetFormatCache.set(fn.toString(), f);
    const wfx = Memory.alloc(64), size = Memory.alloc(4), buf = Memory.alloc(4);
    size.writeU32(64);
    const r = f(port, wfx, size, buf);
    return { hr: '0x' + hx(r, 8), tag: wfx.readU16(), channels: wfx.add(2).readU16(), rate: wfx.add(4).readU32(),
             bits: wfx.add(14).readU16(), bufferBytes: buf.readU32() };
  } catch (e) { return { error: e.message }; }
}
function hookPort(port) {
  slot(port, 11, 'Port.GetCaps', {
    onEnter(a) { this.c = a[1]; },
    onLeave(r) {
      const c = this.c;
      log('Port.GetCaps', { hr: hr(r), flags: '0x' + hx(c.add(4).readU32(), 8), guid: guid(c.add(8)),
        class: c.add(0x18).readU32(), type: c.add(0x1c).readU32(), memory: c.add(0x20).readU32(),
        maxChannelGroups: c.add(0x24).readU32(), maxVoices: c.add(0x28).readU32(),
        maxAudioChannels: c.add(0x2c).readU32(), effects: '0x' + hx(c.add(0x30).readU32(), 8),
        description: c.add(0x34).readUtf16String(128) });
    },
  });
  slot(port, 13, 'Port.SetNumChannelGroups', { onEnter(a) { log('Port.SetNumChannelGroups', a[1].toUInt32()); } });
  slot(port, 15, 'Port.Activate', {
    onEnter(a) { this.p = a[0]; this.on = a[1].toUInt32(); },
    onLeave(r) { log('Port.Activate', { on: this.on, hr: hr(r), format: this.on ? portFormat(this.p) : null }); },
  });
  slot(port, 0, 'Port.QueryInterface', {
    onEnter(a) { this.iid = name(guid(a[1])); this.ppv = a[2]; },
    onLeave(r) { if (this.iid === 'IID_IKsControl' && r.toUInt32() === 0) hookKs(this.ppv.readPointer()); },
  });
}

// ---- IDirectMusic --------------------------------------------------------------------------
function hookDM(dm) {
  slot(dm, 10, 'DM.GetDefaultPort', { onEnter(a) { this.g = a[1]; }, onLeave(r) { log('DM.GetDefaultPort', { hr: hr(r), port: guid(this.g) }); } });
  slot(dm, 3, 'DM.EnumPort', { onEnter(a) { this.i = a[1].toUInt32(); }, onLeave(r) { log('DM.EnumPort', { index: this.i, hr: hr(r) }); } });
  slot(dm, 9, 'DM.Activate', { onEnter(a) { log('DM.Activate', a[1].toUInt32()); } });
  slot(dm, 5, 'DM.CreatePort', {
    onEnter(a) { this.cls = guid(a[1]); this.pp = a[2]; this.out = a[3]; },
    onLeave(r) {
      const p = this.pp, size = p.readU32();
      const f = o => p.add(o).readU32();
      log('DM.CreatePort', { hr: hr(r), clsid: this.cls, size, valid: '0x' + hx(f(4), 8), voices: f(8),
        channelGroups: f(0xc), audioChannels: f(0x10), sampleRate: f(0x14), effects: '0x' + hx(f(0x18), 8),
        share: f(0x1c), features: size >= 0x24 ? f(0x20) : null });
      if (r.toUInt32() === 0 || r.toInt32() > 0) hookPort(this.out.readPointer());
    },
  });
}

// ---- Performance, loader, composer, segment ---------------------------------------------------
function hookPerformance(perf) {
  slot(perf, 3, 'Perf.Init', {
    onEnter(a) { this.ppdm = a[1]; this.ds = a[2]; this.hwnd = a[3]; },
    onLeave(r) {
      log('Perf.Init', { hr: hr(r), pDirectSound: this.ds.toString(), hwnd: this.hwnd.toString() });
      if (r.toUInt32() === 0) hookDM(this.ppdm.readPointer());
    },
  });
  slot(perf, 4, 'Perf.PlaySegment', { onEnter(a) { log('Perf.PlaySegment', { seg: a[1].toString(), flags: '0x' + hx(a[2].toUInt32(), 4) }); } });
  slot(perf, 5, 'Perf.Stop', { onEnter(a) { log('Perf.Stop', { seg: a[1].toString(), flags: a[5].toUInt32() }); } });
  slot(perf, 24, 'Perf.AddPort', { onLeave(r) { log('Perf.AddPort', hr(r)); } });
  slot(perf, 26, 'Perf.AssignPChannelBlock', { onEnter(a) { log('Perf.AssignPChannelBlock', [a[1].toUInt32(), a[3].toUInt32()]); } });
  slot(perf, 34, 'Perf.SetGlobalParam', {
    onEnter(a) {
      const g = name(guid(a[1])), size = a[3].toUInt32(), d = a[2];
      const v = size === 1 ? d.readS8() : size === 4 ? d.readS32() : null;
      log('Perf.SetGlobalParam', { guid: g, size, value: v });
    },
  });
}
function hookSegment(seg) {
  slot(seg, 6, 'Seg.SetRepeats', { onEnter(a) { log('Seg.SetRepeats', a[1].toUInt32()); } });
  slot(seg, 19, 'Seg.SetParam', { onEnter(a) { log('Seg.SetParam', name(guid(a[1]))); }, onLeave(r) { log('Seg.SetParam ->', hr(r)); } });
}
function hookLoader(ld) {
  slot(ld, 5, 'Loader.SetSearchDirectory', { onEnter(a) { log('Loader.SetSearchDirectory', { types: name(guid(a[1])), path: a[2].readUtf16String() }); } });
  slot(ld, 10, 'Loader.EnableCache', { onEnter(a) { log('Loader.EnableCache', { types: name(guid(a[1])), on: a[2].toUInt32() }); } });
  slot(ld, 3, 'Loader.GetObject', {
    onEnter(a) { this.desc = a[1]; this.ppv = a[3]; },
    onLeave(r) {
      const d = this.desc;
      log('Loader.GetObject', { hr: hr(r), valid: '0x' + hx(d.add(4).readU32(), 4), cls: name(guid(d.add(0x18))),
                                file: d.add(0x138).readUtf16String(260) });
      if (r.toInt32() >= 0) hookSegment(this.ppv.readPointer());
    },
  });
}
function hookComposer(c) {
  slot(c, 6, 'Composer.AutoTransition', {
    onEnter(a) { this.x = { toSeg: a[2].toString(), command: a[3].toUInt32() & 0xffff, flags: '0x' + hx(a[4].toUInt32(), 6) }; },
    onLeave(r) { this.x.hr = hr(r); log('Composer.AutoTransition', this.x); },
  });
}

// ---- CoCreateInstance ------------------------------------------------------------------------
let coHooked = false;
function hookCo(mod) {
  if (coHooked) return;
  let fn = null;
  try { fn = mod.getExportByName('CoCreateInstance'); } catch (e) { return; }
  coHooked = true;
  Interceptor.attach(fn, {
    onEnter(a) { this.cls = name(guid(a[0])); this.ppv = a[4]; },
    onLeave(r) {
      if (!this.cls.startsWith('CLSID_DirectMusic')) return;
      const obj = this.ppv.readPointer();
      log('CoCreateInstance', { clsid: this.cls, hr: hr(r) });
      if (r.toInt32() < 0) return;
      if (this.cls === 'CLSID_DirectMusicPerformance') hookPerformance(obj);
      else if (this.cls === 'CLSID_DirectMusicLoader') hookLoader(obj);
      else if (this.cls === 'CLSID_DirectMusicComposer') hookComposer(obj);
    },
  });
  log('hooked', 'CoCreateInstance in ' + mod.name);
}

// ---- Miles -----------------------------------------------------------------------------------
function hookMiles(m) {
  const at = (n, cb) => { const f = m.findExportByName(n); if (f) Interceptor.attach(f, cb); };
  at('_AIL_open_digital_driver@16', {
    onEnter(a) { this.x = [a[0].toUInt32(), a[1].toUInt32(), a[2].toUInt32(), a[3].toUInt32()]; },
    onLeave(r) { log('AIL_open_digital_driver', { rate: this.x[0], bits: this.x[1], channels: this.x[2], flags: this.x[3], ret: r.toString() }); },
  });
  at('_AIL_set_preference@8', { onEnter(a) { log('AIL_set_preference', [a[0].toUInt32(), a[1].toInt32()]); } });
  at('_AIL_get_DirectSound_info@12', {
    onEnter(a) { this.pds = a[1]; },
    onLeave() {
      const ds = this.pds.isNull() ? NULL : this.pds.readPointer();
      log('AIL_get_DirectSound_info', ds.toString());
      if (!ds.isNull()) slot(ds, 4, 'IDirectSound.GetCaps', {
        onEnter(a) { this.c = a[1]; },
        onLeave(r) {
          const c = this.c, f = o => c.add(o).readU32();
          log('IDirectSound.GetCaps', { hr: hr(r), flags: '0x' + hx(f(4), 8), maxHwMixingAll: f(0x14), freeHwMixingAll: f(0x20),
                                        minSecondaryRate: f(8), maxSecondaryRate: f(0xc) });
        },
      });
    },
  });
}
function probeSpim(m) {
  setTimeout(() => {
    const call = n => { try { return new NativeFunction(m.getExportByName(n), 'uint32', [])(); } catch (e) { return e.message; } };
    log('SPIM', { maximumChannelCount: call('SPIMGetMaximumChannelCount'), channelCount: call('SPIMGetChannelCount') });
  }, P.probe_after_ms);
}

// ---- SEConductor (startup, menu, game) --------------------------------------------------------
function hookEngine(m) {
  const want = /^\?(Play|Stop|SetIntensity|TransitionTo|LoadScore|SetConductorState|SetMusicFolder|SetVolume|SetReverb|SetChannelGroupCount)@SEConductor@@/;
  for (const e of m.enumerateExports()) {
    const mm = want.exec(e.name);
    if (!mm || e.type !== 'function') continue;
    Interceptor.attach(e.address, {
      onEnter(a) {
        let v;
        if (mm[1] === 'LoadScore' || mm[1] === 'SetMusicFolder') { try { v = a[0].add(4).readPointer().readCString(); } catch (x) { v = '?'; } }
        else if (mm[1] === 'SetVolume') { const b = new DataView(new ArrayBuffer(4)); b.setUint32(0, a[0].toUInt32(), true); v = b.getFloat32(0, true); }
        else v = [a[0].toInt32(), a[1].toInt32() & 0xffff];
        log('SEConductor::' + mm[1], v);
      },
    });
  }
}

const done = {};
function onModule(m) {
  const n = m.name.toLowerCase();
  if (done[n]) return;
  if (n === 'ole32.dll' || n === 'combase.dll') hookCo(m);
  else if (n === 'mss32.dll') { done[n] = 1; hookMiles(m); }
  else if (n === 'miles sound system mixer.dll') { done[n] = 1; probeSpim(m); }
  else if (n === 'low-level engine.dll') { done[n] = 1; hookEngine(m); }
}
Process.attachModuleObserver({ onAdded: onModule });

function snapshot() { return { t0Epoch: t0, elapsedMs: Date.now() - t0, events }; }
rpc.exports = { snapshot };
