// Module inventory: name, base, size, path, PE preferred ImageBase, relocated?, link timestamp.
// run.py adds SHA-256 of each file. Works attached or spawned (call snapshot() late).
'use strict';

function peInfo(base) {
  try {
    const lfanew = base.add(0x3c).readU32();
    const nt = base.add(lfanew);
    if (nt.readU32() !== 0x4550) return null;               // 'PE\0\0'
    const fh = nt.add(4);
    const oh = nt.add(24);
    const magic = oh.readU16();
    return {
      timestamp: fh.add(4).readU32(),
      preferred: magic === 0x10b ? '0x' + oh.add(28).readU32().toString(16) : null,  // PE32 ImageBase
      dllchars: '0x' + oh.add(70).readU16().toString(16),
    };
  } catch (e) { return null; }
}

function snapshot() {
  return Process.enumerateModules().map(m => {
    const pe = peInfo(m.base) || {};
    return {
      name: m.name, base: m.base.toString(), size: m.size, path: m.path,
      preferred: pe.preferred || null,
      relocated: pe.preferred ? (pe.preferred !== m.base.toString()) : null,
      timestamp: pe.timestamp || null, dllchars: pe.dllchars || null,
    };
  });
}

rpc.exports = { snapshot };
