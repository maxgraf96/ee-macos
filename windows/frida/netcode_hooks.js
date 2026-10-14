// Netcode / OOS oracle hooks for Empire Earth (GOG 2.00) on Windows, for windows/frida/run.py.
// Copy next to run.py (windows/frida/) and run e.g. on both LAN peers during a game:
//   run.py --scripts netcode_hooks --seconds 600 --out C:\EEPort\captures\lan\frida
//
// Records (rpc snapshot()):
//   world:    every world-checksum computation, EXE+0x145ebf(char dump, uint flags) -> u32,
//             with the sim-side tick counter (*(EXE 0x91984c)+0x10) read on entry. This is the
//             per-tick checksum stream; the host's own value never goes on the wire.
//   logged:   NESynchronousServer::LogChecksumResponse (host only): every response it tallies
//             (tick, checksum, originating slot), including its own.
//   removed:  NEMRemovePlayer(player, wasBooted, isOOS, gameIsExiting) constructions.
//   wire:     plaintext of the first max_wire datagrams through TCP Protocol.dll NPIMSend /
//             NPIMSendTo (before RC4) and NPIMReceive / NPIMReceiveFrom (after RC4), as an
//             oracle for tools/lan/eelan.py's decryption.
// PARAMS: max_world (default 200000), max_wire (default 3000), wire_bytes (default 64),
//         wire (default true; false = no TCP Protocol.dll hooks, see below).
'use strict';

const P = Object.assign({ max_world: 200000, max_wire: 3000, wire_bytes: 64 }, globalThis.PARAMS || {});
const exe = Process.getModuleByName('empire earth.exe');
const engine = Process.getModuleByName('low-level engine.dll');
const t0 = Date.now();
const world = [], logged = [], removed = [], wire = [], modules = [];

function exportLike(mod, prefix) {
  for (const e of mod.enumerateExports()) if (e.name.startsWith(prefix)) return e.address;
  return null;
}

// --- world checksum (EXE, fixed base 0x400000, so VA 0x545ebf) ---
const COMM = exe.base.add(0x91984c - 0x400000);       // NESynchronousCommunications* (EE subclass)
Interceptor.attach(exe.base.add(0x145ebf), {
  onEnter(args) {
    this.dump = this.context.esp.add(4).readU8();
    this.flags = this.context.esp.add(8).readU32();
    const comm = COMM.readPointer();
    this.tick = comm.isNull() ? -1 : comm.add(0x10).readU32();
  },
  onLeave(ret) {
    if (world.length < P.max_world)
      world.push([Date.now() - t0, this.threadId, this.tick, this.dump, this.flags, ret.toUInt32() >>> 0]);
  },
});

// --- host tally of checksum responses ---
const logResp = exportLike(engine, '?LogChecksumResponse@NESynchronousServer@@');
if (logResp) Interceptor.attach(logResp, {
  onEnter() {
    const m = this.context.ecx.isNull() ? null : this.context.esp.add(4).readPointer();
    if (m && logged.length < P.max_world)
      logged.push([Date.now() - t0, m.add(0xc).readU32(), m.add(0x10).readU32() >>> 0, m.add(0xa).readU8()]);
  },
});

// --- NEMRemovePlayer(unsigned char &player, bool wasBooted, bool isOOS, bool gameIsExiting) ---
const rmCtor = exportLike(engine, '??0NEMRemovePlayer@@QAE@AAE_N11@Z');
if (rmCtor) Interceptor.attach(rmCtor, {
  onEnter(args) {
    const sp = this.context.esp;
    removed.push([Date.now() - t0, sp.add(4).readPointer().readU8(), sp.add(8).readU8(),
                  sp.add(12).readU8(), sp.add(16).readU8()]);
  },
});

// --- plaintext wire oracle (cdecl NPIM functions) ---
function hex(p, n) {
  const b = new Uint8Array(p.readByteArray(n));
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}
function hookSend(tcp, name, bufArg, lenArg) {
  const a = tcp.getExportByName(name);
  Interceptor.attach(a, {
    onEnter(args) {
      if (wire.length >= P.max_wire) return;
      const len = args[lenArg].toUInt32();
      if (len > 0 && len < 0x10000)
        wire.push([Date.now() - t0, name, len, hex(args[bufArg], Math.min(len, P.wire_bytes))]);
    },
  });
}
function hookRecv(tcp, name, bufArg, lenPtrArg) {
  const a = tcp.getExportByName(name);
  Interceptor.attach(a, {
    onEnter(args) { this.buf = args[bufArg]; this.lenp = args[lenPtrArg]; },
    onLeave(ret) {
      if (ret.toUInt32() !== 0 || wire.length >= P.max_wire) return;
      const len = this.lenp.readU32();
      if (len > 0 && len < 0x10000)
        wire.push([Date.now() - t0, name, len, hex(this.buf, Math.min(len, P.wire_bytes))]);
    },
  });
}
function hookTcp(tcp) {
  hookSend(tcp, 'NPIMSend', 1, 2);        // (NEConnection*, buf, len)
  hookSend(tcp, 'NPIMSendTo', 2, 3);      // (NEServer*, slot, buf, len)
  hookRecv(tcp, 'NPIMReceive', 2, 3);     // (NEConnection*, maxlen, buf, &len)
  hookRecv(tcp, 'NPIMReceiveFrom', 2, 3); // (NEServer*, maxlen, buf, &len, &slot)
}
// The engine loads every plugin DLL at startup to probe it and unloads it again, so TCP
// Protocol.dll is usually absent at the main menu. Hook it whenever it (re)appears.
// PARAM wire=false skips this: with these hooks in place the LAN *host* (legion) crashed in
// ntdll (access violation) the moment the match started (2026-10-14 17:23); the joiner survived.
const isTcp = m => m.name.toLowerCase() === 'tcp protocol.dll';
const tcpNow = P.wire === false ? null : Process.findModuleByName('tcp protocol.dll');
if (tcpNow) hookTcp(tcpNow);
if (P.wire !== false) Process.attachModuleObserver({
  onAdded(m) {
    if (!isTcp(m) || (tcpNow && m.base.equals(tcpNow.base))) return;
    modules.push([Date.now() - t0, 'added', m.name, m.base.toString()]);
    hookTcp(m);
  },
  onRemoved(m) { if (isTcp(m)) modules.push([Date.now() - t0, 'removed', m.name, m.base.toString()]); },
});

send({ type: 'hooked', logResp: !!logResp, rmCtor: !!rmCtor, tcpLoaded: !!tcpNow });

rpc.exports = {
  snapshot() {
    return { elapsedMs: Date.now() - t0,
             world: world.map(([ms, tid, tick, dump, flags, v]) =>
               [ms, tid, tick, dump, flags, '0x' + v.toString(16).padStart(8, '0')]),
             logged: logged.map(([ms, tick, v, slot]) => [ms, tick, '0x' + v.toString(16).padStart(8, '0'), slot]),
             removed, wire, modules };
  },
};
