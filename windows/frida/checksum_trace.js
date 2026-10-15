// Single-player checksum-stream oracle (P4-C) for Empire Earth (GOG 2.00) on Windows, for
// windows/frida/run.py. The Windows twin of the Mac's mods/simparity/simparity.c: the same hooks,
// the same calls, the same rows, so tools/simparity/compare.py compares the two streams line by
// line. Spec: docs/reference/simparity.md.
//
//   run.py --scripts checksum_trace --seconds 1500 --out C:\EEPort\captures\simparity\win-1 ^
//          --param out=C:\EEPort\captures\simparity\win-1\stream.csv --param seed=1000 ^
//          --stop-file C:\EEPort\captures\simparity\win-1\STOP
//   (attach at the main menu, then Single Player -> Play Random Map -> Start Game)
//
// What it does:
//   * After every call of the physics-tick entry EEPhysics vtable slot 8 (EXE 0x546c63) that ran a
//     PT (EEPhysics+0xb4 advanced), it calls the game's world checksum
//     EEPhysics::WorldChecksum(char dump, uint flags) (EXE 0x545ebf, thiscall on the EEPhysics at
//     [0x919774], RET 8) with dump 0, flags 1 - the multiplayer OOS checksum - and writes a T row.
//     The checksum only reads the world (and writes its result to 0x919b6c, which nothing reads).
//   * On EEMStartLoading's execute (EXE 0x4bee57, ECX = the message) it can force the game seed:
//     message+0x14 becomes both the map seed ([[0x91977c]+0x54]) and the world RNG state
//     (EEPhysics+0x18). Forcing it at the map generator (0x66d402, as rmg_capture.js and
//     seed_force.js do) leaves the world RNG at the game's own random seed.
//   * It can skip the statistics report timer (EXE 0x547365), which sends EEMReportStatistics on
//     a wall-clock schedule (TMGetTime, srand(TMGetTime()) % 2500 ms).
//   * Every message the EXE hands to the server (the EXE thunk 0x683bc8 for
//     NESynchronousServer::ProcessLocalUserInput) is an S row.
//
// Rows (CSV; '#' lines are comments), written straight to PARAMS.out with Frida's File API:
//   G,seed_orig,seed,humans                    a game starts
//   C,ct,total_pt,pts,ptps,dt_bits,speed_bits  a communications tick's PTs arrive
//   T,pt,ct,cs,cs2,rng,inputs,units            after each PT
//   S,pt,ct,type,slot                          a message handed to the server
//   U,pt,i,vtable,id,x,y,hp,cs,cs2             one object of the checksum list
//   W,ct,pt,dump,flags,cs                      the game's own checksum call (a multiplayer CT; the
//                                              host's value never goes on the wire)
//
// PARAMS:
//   out=<path>          the stream file (required)
//   seed=<int>          force the game seed (default: the game's own)
//   no_reports=<bool>   skip the statistics report timer (default true)
//   flags2=<int>        flags of the second checksum column (default 1 = the same as cs)
//   units=<a,b,...>     U rows after these PTs; units_every=<n> also after every nth PT
//   oosdump=<pt>        after this PT, the game's own OOS dump (WorldChecksum with dump = 1)
//   max_pt=<n>          stop writing rows after PT n
//   lockstep=<bool>     the server builds a CT (EEServer vtable +0x28, EXE 0x4c3703, called from
//                       SendCommunicationsTick on its send thread) only once the simulation has started every CT sent so far and run
//                       all their PTs (waits at most 3 s), so a message the simulation sends during
//                       CT n always runs in CT n+1 (default false)
'use strict';

const P = Object.assign({ out: null, seed: null, no_reports: true, flags2: 1, units: '', units_every: 0,
                          oosdump: -1, max_pt: 0xffffffff, lockstep: false, no_civ_timer: false },
                        globalThis.PARAMS || {});
if (!P.out) throw new Error('checksum_trace: PARAMS.out is required');
const exe = Process.getModuleByName('empire earth.exe');
if (!exe.base.equals(ptr('0x400000'))) throw new Error('checksum_trace: EXE not at 0x400000');
const at = (va) => ptr(va);

const VA_WORLD_PTR = at(0x919774), VA_COMM_PTR = at(0x91984c), VA_UNIT_LIST = at(0x918318);
const VA_INPUTS = at(0x919b68), VA_GAME_SPEED = at(0x9183c0);
const W_RNG = 0x18, W_PTPS = 0x58, W_TOTAL_PT = 0x80, W_DT = 0x98, W_PT = 0xb4, COMM_CT = 0x10;

const worldChecksumRaw = new NativeFunction(at(0x545ebf), 'uint32', ['pointer', 'uint8', 'uint32'], 'thiscall');
let ownCall = false; // the tracer's own checksum calls are not W rows
function worldChecksum(world, dump, flags) {
  ownCall = true;
  try { return worldChecksumRaw(world, dump, flags); } finally { ownCall = false; }
}
const unitFns = new Map();
function unitChecksum(unit, flags) {
  const vt = unit.readPointer();
  const fn = vt.add(0x4c).readPointer();
  let f = unitFns.get(fn.toString());
  if (!f) {
    f = new NativeFunction(fn, 'uint32', ['pointer', 'uint32'], 'thiscall');
    unitFns.set(fn.toString(), f);
  }
  return f(unit, flags) >>> 0;
}

const h8 = (v) => (v >>> 0).toString(16).padStart(8, '0');
const h2 = (v) => (v >>> 0).toString(16).padStart(2, '0');
const unitPts = new Set(String(P.units).split(',').filter((s) => s.length).map(Number));
const flags2 = Number(P.flags2) >>> 0;
const maxPt = Number(P.max_pt) >>> 0;
const unitsEvery = Number(P.units_every) >>> 0;
const oosdump = Number(P.oosdump);
const skipReports = P.no_reports === true || P.no_reports === 'true' || P.no_reports === 1;
const haveSeed = P.seed !== null && P.seed !== undefined && P.seed !== '';
const seed = haveSeed ? Number(P.seed) >>> 0 : 0;

const file = new File(P.out, 'w');
let rows = 0, lastPt = -1, lastTotal = -1, done = false, ticks = 0, firstPt = -1;
function emit(line) { file.write(line + '\n'); rows++; }
function flush() { try { file.flush(); } catch (e) { /* closed */ } }
emit('# ee-simparity/1 windows flags2=0x' + flags2.toString(16) + ' seed=' + (haveSeed ? seed : 'game') +
     ' no_reports=' + (skipReports ? 1 : 0) + ' lockstep=' +
     ((P.lockstep === true || P.lockstep === 'true' || P.lockstep === 1) ? 1 : 0) + ' no_civ_timer=' +
     ((P.no_civ_timer === true || P.no_civ_timer === 'true' || P.no_civ_timer === 1) ? 1 : 0));

function nextUnit(u) {
  const link = u.add(8).readPointer();
  return link.isNull() ? NULL : link.readPointer();
}

function writeUnits(pt) {
  let i = 0;
  for (let u = VA_UNIT_LIST.readPointer(); !u.isNull() && i < 100000; u = nextUnit(u), i++) {
    emit(['U', pt, i, h8(u.readU32()), u.add(0x24).readU32(), u.add(0x1c).readS32(), u.add(0x20).readS32(),
          u.add(0x3c).readS32(), h8(unitChecksum(u, 1)), h8(flags2 === 1 ? 0 : unitChecksum(u, flags2))].join(','));
  }
}

Interceptor.attach(at(0x546c63), {
  onLeave() {
    const world = VA_WORLD_PTR.readPointer();
    if (world.isNull() || done) return;
    const comm = VA_COMM_PTR.readPointer();
    const pt = world.add(W_PT).readU32(), total = world.add(W_TOTAL_PT).readU32();
    const ct = comm.isNull() ? 0 : comm.add(COMM_CT).readU32();
    if (total !== lastTotal) {
      const pts = lastTotal < 0 ? total : (total - lastTotal) | 0;
      emit(['C', ct, total, pts, world.add(W_PTPS).readU32(), h8(world.add(W_DT).readU32()),
            h8(VA_GAME_SPEED.readU32())].join(','));
      lastTotal = total;
    }
    if (pt === lastPt) return;
    if (lastPt >= 0 && pt < lastPt) emit('# pt went back from ' + lastPt + ' to ' + pt + ': a new game');
    lastPt = pt;
    if (firstPt < 0) firstPt = pt;
    ticks++;
    let units = 0;
    for (let u = VA_UNIT_LIST.readPointer(); !u.isNull() && units < 100000; u = nextUnit(u)) units++;
    const cs = worldChecksum(world, 0, 1) >>> 0;
    const cs2 = flags2 === 1 ? cs : worldChecksum(world, 0, flags2) >>> 0;
    emit(['T', pt, ct, h8(cs), h8(cs2), h8(world.add(W_RNG).readU32()), VA_INPUTS.readU32(), units].join(','));
    if ((unitsEvery && pt % unitsEvery === 0) || unitPts.has(pt)) writeUnits(pt);
    if (pt === oosdump) {
      worldChecksum(world, 1, 1);
      emit('# OOS dump written after PT ' + pt + " (the game's OOS *.log)");
    }
    if (pt >= maxPt) {
      emit('# end after PT ' + pt);
      done = true;
    }
    if (pt % 400 === 0) flush();
  },
});

// The game's own calls (multiplayer: once per CT from CT 40, in the simulation thread's message pump).
Interceptor.attach(at(0x545ebf), {
  onEnter() {
    this.mine = ownCall;
    if (this.mine) return;
    this.dump = this.context.esp.add(4).readU8();
    this.flags = this.context.esp.add(8).readU32();
  },
  onLeave(ret) {
    if (this.mine) return;
    const world = VA_WORLD_PTR.readPointer(), comm = VA_COMM_PTR.readPointer();
    emit(['W', comm.isNull() ? 0 : comm.add(COMM_CT).readU32(), world.isNull() ? 0 : world.add(W_PT).readU32(),
          this.dump, this.flags >>> 0, h8(ret.toUInt32())].join(','));
  },
});

Interceptor.attach(at(0x4bee57), {
  onEnter() {
    const msg = this.context.ecx;
    const orig = msg.add(0x14).readU32();
    if (haveSeed) msg.add(0x14).writeU32(seed);
    emit(['G', orig, haveSeed ? seed : orig, msg.add(0x10).readU8()].join(','));
    lastPt = -1; lastTotal = -1; done = false;
    flush();
  },
});

Interceptor.attach(at(0x683bc8), {
  onEnter() {
    const sp = this.context.esp;
    const msg = sp.add(4).readPointer();
    const slot = sp.add(8).readU8();
    const world = VA_WORLD_PTR.readPointer(), comm = VA_COMM_PTR.readPointer();
    emit(['S', world.isNull() ? 0 : world.add(W_PT).readU32(), comm.isNull() ? 0 : comm.add(COMM_CT).readU32(),
          h2(msg.isNull() ? 0 : msg.add(8).readU8()), h2(slot)].join(','));
  },
});

if (skipReports) Interceptor.replace(at(0x547365), new NativeCallback(function () {}, 'void', []));
const noCivTimer = P.no_civ_timer === true || P.no_civ_timer === 'true' || P.no_civ_timer === 1;
if (noCivTimer) Interceptor.replace(at(0x54778a), new NativeCallback(function () {}, 'void', []));

const lockstep = P.lockstep === true || P.lockstep === 'true' || P.lockstep === 1;
let lockWaits = 0, lockTimeouts = 0;
// Only the call from NESynchronousServer::SendCommunicationsTick (engine RVA 0x54c6b is its return
// address; the engine is relocated on Windows).
const gateReturn = Process.getModuleByName('low-level engine.dll').base.add(0x54c6b);
if (lockstep) Interceptor.attach(at(0x4c3703), {
  onEnter() {
    if (!this.returnAddress.equals(gateReturn)) return;
    const server = this.context.ecx;
    for (let n = 0; ; n++) {
      const world = VA_WORLD_PTR.readPointer(), comm = VA_COMM_PTR.readPointer();
      if (world.isNull() || comm.isNull()) return;
      if (comm.add(COMM_CT).readU32() >= server.add(0x190).readU32() && world.add(0xb8).readU32() === 0) {
        if (n) lockWaits++;
        if (n >= 1000) emit('# lockstep: waited ' + n + ' ms before CT ' + server.add(0x190).readU32());
        return;
      }
      if (n >= 60000) {
        lockTimeouts++;
        emit('# lockstep: gave up waiting at CT ' + server.add(0x190).readU32() + ' (sim CT ' +
             comm.add(COMM_CT).readU32() + ', PTs left ' + world.add(0xb8).readU32() + ')');
        return;
      }
      Thread.sleep(0.001);
    }
  },
});

send({ type: 'hooked', out: P.out, seed: haveSeed ? seed : null, no_reports: skipReports, flags2, lockstep,
       no_civ_timer: noCivTimer });

rpc.exports = {
  snapshot() { flush(); return { rows, ticks, firstPt, lastPt, done, lockWaits, lockTimeouts, out: P.out }; },
  stop() { flush(); },
};
