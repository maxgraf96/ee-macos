// music_hooks.js: music and sound-effect reference trace for the audio port (docs/reference/audio.md).
//
// 1. Timestamped log of every SEConductor / SEScore call (thiscall; ECX = this) with decoded
//    arguments: SetIntensity, TransitionTo, Play, Queue, SetVolume, SetReverb(Style), LoadScore, ...
// 2. Per-function call counts and argument samples for the 34 mss32 AIL_* functions the Miles
//    mixer plugin imports, plus SPIMGetMaximumChannelCount / SPIMCreateChannels.
// 3. Optional forced-intensity schedule (for A/B reference recordings): at each [sec, level],
//    lock the conductor's intensity to `level` (the game's own SetIntensity calls are rewritten)
//    and do what EEMusicManager does on a change: SetIntensity(level,true) then
//    TransitionTo(GetCurrentlyPlayingScore(), 0 /*GROOVE*/, 0 /*MEASURE*/).
//
// PARAMS: schedule (e.g. [[0,1],[30,2],[60,3],[90,2],[120,0],[150,1]]; default none),
//         max_events (default 20000), max_samples (per AIL function, default 40).
// Run with windows/frida/run.py (attach at the menu or in game; not --spawn).
'use strict';

const P = Object.assign({ schedule: null, max_events: 20000, max_samples: 40 }, globalThis.PARAMS || {});
const t0 = Date.now();
const engine = Process.getModuleByName('low-level engine.dll');
const events = [];
const counts = {};
const samples = {};

function f32(p) {
  const dv = new DataView(new ArrayBuffer(4));
  dv.setUint32(0, p.toUInt32(), true);
  return Math.round(dv.getFloat32(0, true) * 10000) / 10000;
}
function wstr(p) { // UWideString: narrow char* at +4 (as SEConductor::LoadScore reads it)
  try { return p.add(4).readPointer().readCString(); } catch (e) { return '?'; }
}
function log(name, data) {
  if (events.length < P.max_events) events.push([Date.now() - t0, Process.getCurrentThreadId(), name, data]);
}
function count(name, args) {
  counts[name] = (counts[name] || 0) + 1;
  const s = samples[name] || (samples[name] = []);
  if (s.length < P.max_samples) s.push(args);
}

// ---- SEConductor / SEScore -------------------------------------------------------------------
const decode = {
  SetIntensity: a => [a[0].toUInt32(), a[1].toUInt32() & 0xff],
  TransitionTo: a => [a[0].toInt32(), a[1].toUInt32(), a[2].toUInt32()],
  Play: a => [a[0].toInt32()], Queue: a => [a[0].toInt32()], UnloadScore: a => [a[0].toInt32()],
  GetScore: a => [a[0].toInt32()], SetRepeatCount: a => [a[0].toUInt32()],
  SetVolume: a => [f32(a[0])], SetReverb: a => [a[0].toUInt32()],
  SetReverbStyle: a => [f32(a[0]), f32(a[1]), f32(a[2]), f32(a[3])],
  SetConductorState: a => [a[0].toUInt32()], SetChannelGroupCount: a => [a[0].toUInt32()],
  SetMusicFolder: a => [wstr(a[0])], LoadScore: a => [wstr(a[0]), a[2].toUInt32() & 0xff],
  Stop: a => [],
};
let lockLevel = -1;
for (const e of engine.enumerateExports()) {
  const m = /^\?(\w+)@(SEConductor|SEScore)@@/.exec(e.name);
  if (!m || e.type !== 'function' || !(m[1] in decode)) continue;
  const short = m[2] + '::' + m[1];
  try {
    Interceptor.attach(e.address, {
      onEnter(args) {
        if (m[1] === 'SetIntensity' && lockLevel >= 0 && args[0].toUInt32() !== lockLevel) {
          log(short + ' (rewritten)', [args[0].toUInt32(), lockLevel]);
          args[0] = ptr(lockLevel);
        }
        log(short, decode[m[1]](args));
      },
      onLeave(ret) { if (m[1] === 'LoadScore' || m[1] === 'Play' || m[1] === 'TransitionTo') log(short + ' ->', ret.toInt32()); },
    });
  } catch (err) { send({ type: 'hook-error', name: e.name, error: err.message }); }
}

// ---- Miles (mss32) and the SPIM mixer plugin ---------------------------------------------------
const AIL = ['set_digital_master_volume@8', 'end_sample@4', 'digital_CPU_percent@4', 'set_sample_pan@8',
  'set_sample_position@8', 'set_sample_playback_rate@8', 'set_sample_volume@8', 'last_error@0',
  'get_preference@4', 'release_sample_handle@4', 'get_DirectSound_info@12', 'allocate_sample_handle@4',
  'set_DirectSound_HWND@8', 'open_digital_driver@16', 'set_preference@8', 'startup@0',
  'set_redist_directory@4', 'shutdown@0', 'close_digital_driver@4', 'sample_granularity@4',
  'set_named_sample_file@20', 'get_timer_highest_delay@0', 'sample_position@4', 'sample_ms_position@12',
  'sample_status@4', 'mem_free_lock@4', 'sample_playback_rate@4', 'file_read@8', 'init_sample@4',
  'mem_alloc_lock@4', 'start_sample@4', 'file_size@4', 'serve@0', 'set_sample_loop_count@8'];
const mss = Process.findModuleByName('mss32.dll');
if (mss) {
  for (const n of AIL) {
    const addr = mss.findExportByName('_AIL_' + n);
    if (!addr) { send({ type: 'missing', name: n }); continue; }
    const argc = parseInt(n.split('@')[1], 10) / 4;
    Interceptor.attach(addr, {
      onEnter(args) {
        const a = [];
        for (let i = 0; i < argc; i++) a.push(args[i].toUInt32());
        if (n.startsWith('set_named_sample_file')) { try { a[1] = args[1].readCString(); } catch (e) { } }
        this.a = a;
      },
      onLeave(ret) { count(n, this.a.concat(['=', ret.toUInt32()])); },
    });
  }
}
const spim = Process.findModuleByName('miles sound system mixer.dll');
if (spim) {
  for (const n of ['SPIMGetMaximumChannelCount', 'SPIMCreateChannels', 'SPIMGetChannelCount', 'SPIMSetLatency']) {
    const addr = spim.findExportByName(n);
    if (addr) Interceptor.attach(addr, {
      onEnter(args) { this.a0 = args[0].toUInt32(); },
      onLeave(ret) { count(n, [this.a0, '=', ret.toUInt32()]); },
    });
  }
}

// ---- forced-intensity schedule ---------------------------------------------------------------
const GetActiveMixer = new NativeFunction(engine.getExportByName('?GetActiveMixer@SEMixer@@SAPAV1@XZ'), 'pointer', []);
const GetConductor = new NativeFunction(engine.getExportByName('?GetConductor@SEMixer@@QAEAAVSEConductor@@XZ'),
  'pointer', ['pointer'], 'thiscall');
const SetIntensity = new NativeFunction(engine.getExportByName('?SetIntensity@SEConductor@@QAEJK_N@Z'),
  'int', ['pointer', 'uint32', 'uint8'], 'thiscall');
const TransitionTo = new NativeFunction(engine.getExportByName('?TransitionTo@SEConductor@@QAEJJKK@Z'),
  'int', ['pointer', 'int', 'uint32', 'uint32'], 'thiscall');
const Current = new NativeFunction(engine.getExportByName('?GetCurrentlyPlayingScore@SEConductor@@QAEJXZ'),
  'int', ['pointer'], 'thiscall');

function force(level) {
  const mixer = GetActiveMixer();
  if (mixer.isNull()) { log('force: no active mixer', level); return; }
  const c = GetConductor(mixer);
  lockLevel = level;
  const r1 = SetIntensity(c, level, 1);
  const score = Current(c);
  const r2 = score >= 0 ? TransitionTo(c, score, 0, 0) : -1;
  log('force', { level, score, setIntensity: r1, transitionTo: r2 });
}
if (Array.isArray(P.schedule)) {
  for (const [sec, level] of P.schedule) setTimeout(() => force(level), sec * 1000);
}

send({ type: 'hooked', conductor: Object.keys(decode), mss: !!mss, spim: !!spim, t0Epoch: t0 });

function snapshot() {
  return { t0Epoch: t0, elapsedMs: Date.now() - t0, events, counts, samples, lockLevel };
}
function unlock() { lockLevel = -1; }
rpc.exports = { snapshot, force, unlock };
