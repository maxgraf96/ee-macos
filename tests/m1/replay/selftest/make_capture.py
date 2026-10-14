#!/usr/bin/env python3
"""Write a synthetic rmg_capture.js-style capture for the replay self-test (no game data).

The "export" it describes is the C function fake_export() in selftest.c: it reads a counter from
the context, allocates a 16-byte block with operator new, reads one line with fopen/fgets/fclose,
stores line length and counter+1 in the block, links the block into the context and frees an
old block. The capture is what rmg_capture.js would record for that function.

  make_capture.py OUT_DIR [--break]      --break: corrupt one expected output byte
"""
import hashlib
import json
import os
import struct
import sys

CTX = 0x0A000004
OLD = 0x0B000000          # heap block referenced by ctx+0x20 at entry, freed by the call
NEW = 0x0B100000          # block allocated by the call
ESP = 0x0019F000
DATA = 0x0F219000         # Default.dll .data (fake)
FP = 0x7600A000
LINE = b"UseMapGenerator \"Default\"\n\0"


def main():
    out = sys.argv[1]
    broken = "--break" in sys.argv
    blobs = {}

    def store(b):
        h = hashlib.sha256(b).hexdigest()
        blobs[h] = b
        return [h]

    ctx_in = bytearray(0x3D0)
    struct.pack_into("<II", ctx_in, 0x10, 41, 0)             # counter at +0x10
    struct.pack_into("<I", ctx_in, 0x20, OLD)                # pointer to the old block
    old = bytes(range(16))
    stack = bytearray(0x1000)
    struct.pack_into("<II", stack, 0x800, 0x0066B4A0, CTX)   # [esp] = return address, [esp+4] = ctx
    data = bytearray(0x40)
    garbage = b"\xcd" * 16

    ctx_out = bytearray(ctx_in)
    struct.pack_into("<II", ctx_out, 0x10, 42, 0)
    struct.pack_into("<I", ctx_out, 0x20, NEW)
    new_out = struct.pack("<IIII", len(LINE) - 1, 42, 0x12345678, 0)
    if broken:
        new_out = new_out[:4] + b"\x2b" + new_out[5:]

    def region(kind, addr, b, restore=True, scan=True, **kw):
        r = {"kind": kind, "addr": hex(addr), "size": len(b), "restore": restore, "scan": scan, "chunks": store(bytes(b))}
        r.update(kw)
        return r

    mods = [{"name": n, "base": hex(b), "size": s, "sizeOfImage": hex(s), "timestamp": hex(t), "data": {"rva": hex(d), "size": 0x40}}
            for n, b, s, t, d in (("sxlrt233.dll", 0x10000000, 0xE4000, 0x3B7D8F2A, 0xDB000),
                                  ("Low-Level Engine.dll", 0x01410000, 0x130000, 0x3D7F7B3C, 0xD4000),
                                  ("Default.dll", 0x0F200000, 0x1D000, 0x3C1A2B3D, 0x19000))]
    call = {
        "schema": "ee-rmg-capture/1", "case": "selftest", "seq": 0, "gen": 0, "attempt": 0,
        "export": "EERMPIMCreateFlatMap", "tid": 1, "esp": hex(ESP), "args": [hex(CTX)], "modules": mods,
        "entry": {"esp": hex(ESP), "args": [hex(CTX)], "fpu": {"cw": "0x27f", "sw": "0x0", "tw": "0xffff"},
                  "regions": [region("ctx", CTX, ctx_in),
                              region("module_data", DATA, data, module="Default.dll"),
                              region("heap", OLD, old, via=hex(CTX + 0x20), **{"from": "ctx"}),
                              region("stack", ESP - 0x800, stack, scan=False)],
                  "bytes": 0, "foreign": {"count": 0, "sample": []}},
        "allocs": [{"op": "new", "a": hex(16), "b": "0x0", "ret": hex(NEW), "size": 16, "caller": "Default.dll+0x1234",
                    "chunks": store(garbage)},
                   {"op": "delete", "a": hex(OLD), "caller": "Default.dll+0x1240"}],
        "files": [{"op": "fopen", "path": "C:\\x\\Test.rmv", "path_hex": b"C:\\x\\Test.rmv".hex(), "mode": "r", "ret": hex(FP)},
                  {"op": "fgets", "fp": hex(FP), "n": 512, "ret": hex(ESP - 0x400), "data_hex": LINE.hex()},
                  {"op": "fgets", "fp": hex(FP), "n": 512, "ret": "0x0"},
                  {"op": "fclose", "fp": hex(FP), "ret": "0x0"}],
        "debug": ["selftest\n"], "printf": [], "warnings": [],
        "exit": {"eax": "0x0", "fpu": {"cw": "0x27f", "sw": "0x0", "tw": "0xffff"},
                 "regions": [region("ctx", CTX, ctx_out), region("module_data", DATA, data, module="Default.dll"),
                             region("heap", NEW, new_out, via=hex(CTX + 0x20), **{"from": "ctx"})],
                 "bytes": 0, "foreign": {"count": 0, "sample": []}},
    }
    os.makedirs(os.path.join(out, "calls"), exist_ok=True)
    with open(os.path.join(out, "calls", "000000.json"), "w") as f:
        json.dump(call, f, indent=1)
    for h, b in blobs.items():
        os.makedirs(os.path.join(out, "blobs", h[:2]), exist_ok=True)
        with open(os.path.join(out, "blobs", h[:2], h + ".bin"), "wb") as f:
            f.write(b)


if __name__ == "__main__":
    main()
