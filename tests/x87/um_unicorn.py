"""Run game-function call vectors (kind "call") on the Mac: the user's own engine DLL / EXE
mapped into Unicorn at their preferred bases, with the few msvcrt imports they need replaced by
stubs that follow msvcrt's x87 behaviour (sqrt = FLD m64; FSQRT; _finite; _controlfp query).

The result format is the one windows/frida/ummath_vectors.js writes, so compare.py can diff
legion (real Windows, real msvcrt) against this model:

  python -I tests/x87/um_unicorn.py --game "original/gog/Empire Earth" um.jsonl -o um-unicorn.jsonl

Vectors for functions that need anything else (pow, sin, atof, ...) are reported as
{"error": "not modelled"}: they characterise Windows only.
"""
import argparse
import json
import os
import struct
import sys

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402

STUBS, BLOCK, STACK, HALT = 0x00100000, 0x00200000, 0x00300000, 0x0010fff0

# msvcrt stand-ins, as x87 code (cdecl). sqrt: FLD qword [esp+4]; FSQRT; RET  (the x87 path of
# msvcrt's sqrt, which honours the caller's precision control).
CRT_CODE = {
    "sqrt": bytes.fromhex("dd442404d9fac3"),
    # _finite(double): (hi & 0x7ff00000) != 0x7ff00000
    "_finite": bytes.fromhex("8b442408250000f07f3d0000f07f0f95c00fb6c0c3"),
    "fabs": bytes.fromhex("dd442404d9e1c3"),
}


class Image:
    def __init__(self, uc, path):
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        size = (self.pe.OPTIONAL_HEADER.SizeOfImage + 0xfff) & ~0xfff
        uc.mem_map(self.base, size)
        uc.mem_write(self.base, self.pe.get_memory_mapped_image()[:size])
        self.exports = {}
        if hasattr(self.pe, "DIRECTORY_ENTRY_EXPORT"):
            for e in self.pe.DIRECTORY_ENTRY_EXPORT.symbols:
                if e.name:
                    self.exports[e.name.decode()] = self.base + e.address
        self.imports = []
        for d in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = self.pe.get_string_at_rva(d.struct.Name).decode().lower()
            for imp in d.imports:
                self.imports.append((dll, imp.name.decode() if imp.name else "#%d" % imp.ordinal, imp.address))


class Machine:
    def __init__(self, game):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc = self.uc
        uc.mem_map(STUBS, 0x10000)
        uc.mem_map(BLOCK, 0x10000)
        uc.mem_map(STACK, 0x10000)
        uc.mem_write(HALT, b"\xf4")
        self.mods = {}
        for name in (X.ENGINE, X.EXE):
            p = os.path.join(game, name)
            if os.path.exists(p):
                self.mods[name] = Image(uc, p)
        self.next = STUBS + 0x1000
        self.trap = {}
        self.cw = 0x027f
        for m in self.mods.values():
            for dll, fn, slot in m.imports:
                if dll != "msvcrt.dll":
                    addr = self.alloc(b"\xc3")
                    self.trap[addr] = "%s!%s" % (dll, fn)
                elif fn in CRT_CODE:
                    addr = self.alloc(CRT_CODE[fn])
                elif fn == "_controlfp":
                    addr = self.alloc(b"\xc3")
                    self.trap[addr] = "_controlfp"
                else:
                    addr = self.alloc(b"\xc3")
                    self.trap[addr] = "msvcrt!" + fn
                uc.mem_write(slot, struct.pack("<I", addr))
        self.stub_cache = {}
        self.crt_direct = {}
        self.err = None
        uc.hook_add(UC_HOOK_CODE, self.on_trap, begin=STUBS + 0x1000, end=STUBS + 0xffff)

    def alloc(self, code):
        a = self.next
        self.uc.mem_write(a, code)
        self.next += (len(code) + 15) & ~15
        return a

    def on_trap(self, uc, addr, size, _):
        name = self.trap.get(addr)
        if name is None:
            return
        if name == "_controlfp":
            # query form only: map the x87 CW to msvcrt's abstract word (as legion measured:
            # 0x027f -> 0x9001f, 0x007f -> 0xa001f)
            esp = uc.reg_read(UC_X86_REG_ESP)
            new, mask = struct.unpack("<II", uc.mem_read(esp + 4, 8))
            if mask:
                self.err = "_controlfp set not modelled"
                uc.emu_stop()
                return
            cw = self.cw
            pc = {0: 0x20000, 2: 0x10000, 3: 0x0}.get((cw >> 8) & 3, 0x30000)
            rc = ((cw >> 10) & 3) << 8
            em = (0x10 if cw & 1 else 0) | (0x80000 if cw & 2 else 0) | (0x8 if cw & 4 else 0) | \
                 (0x4 if cw & 8 else 0) | (0x2 if cw & 0x10 else 0) | (0x1 if cw & 0x20 else 0)
            uc.reg_write(UC_X86_REG_EAX, pc | rc | em | (0x40000 if cw & 0x1000 else 0))
            return
        self.err = "import not modelled: " + name
        uc.emu_stop()

    def resolve(self, v):
        if v["mod"] == X.CRT and v["sym"] in CRT_CODE:
            if v["sym"] not in self.crt_direct:
                self.crt_direct[v["sym"]] = self.alloc(CRT_CODE[v["sym"]])
            return self.crt_direct[v["sym"]]
        mod = self.mods.get(v["mod"])
        if mod is None:
            return None
        sym = v["sym"]
        if sym.startswith("+"):
            return mod.base + int(sym[1:], 16)
        return mod.exports.get(sym)

    def run(self, v):
        if v["mod"] not in self.mods and not (v["mod"] == X.CRT and v["sym"] in CRT_CODE):
            return {"error": "not modelled"}
        fn = self.resolve(v)
        if fn is None:
            return {"error": "symbol not found"}
        sig = v["sig"]
        if sig not in self.stub_cache:
            self.stub_cache[sig] = self.alloc(X.build_call_stub(sig))
        stub = self.stub_cache[sig]
        uc = self.uc
        self.cw = int(v.get("cw", "027f"), 16)
        uc.mem_write(BLOCK, bytes(X.fill_call_block(v, fn)))
        esp = STACK + 0xf000
        uc.mem_write(esp, struct.pack("<II", HALT, BLOCK))
        uc.reg_write(UC_X86_REG_ESP, esp)
        self.err = None
        try:
            uc.emu_start(stub, HALT, count=5_000_000)
        except UcError as e:
            return {"error": "unicorn: %s at %08x" % (e, uc.reg_read(UC_X86_REG_EIP))}
        if self.err:
            return {"error": self.err}
        if uc.reg_read(UC_X86_REG_EIP) != HALT:
            return {"error": "did not return (instruction budget)"}
        return X.read_call_block(v, bytearray(uc.mem_read(BLOCK, X.CB_SIZE)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vectors")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--game", default="original/gog/Empire Earth")
    a = ap.parse_args()
    m = Machine(a.game)
    n = err = 0
    with open(a.vectors) as f, open(a.out, "w") as out:
        out.write(json.dumps({"meta": {"backend": "unicorn-um", "modules": sorted(m.mods)}}) + "\n")
        for line in f:
            if not line.strip():
                continue
            v = json.loads(line)
            if v.get("kind") != "call":
                continue
            r = m.run(v)
            r["id"] = v["id"]
            err += "error" in r
            out.write(json.dumps(r) + "\n")
            n += 1
    print("um_unicorn: %d call vectors (%d not modelled/errors) -> %s" % (n, err, a.out))


if __name__ == "__main__":
    main()
