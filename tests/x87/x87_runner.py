"""Run x87 instruction vectors on real x86 hardware (legion) or under Unicorn (Mac).

Each vector becomes a tiny machine-code stub (x87ops.build_stub): set the control word, load
the inputs as 80-bit values, execute the op, store every live register as 80 bits plus the
memory output and the status word, restore the caller's control word. The stub addresses its
block through RCX/ECX, so the same bytes run in 32-bit and 64-bit mode; x87 semantics do not
depend on the mode.

  # legion (Windows, 64-bit Python is fine: x87 works in 64-bit processes)
  python -I x87_runner.py --backend native vectors.jsonl -o results-legion.jsonl
  # any x86-64 Linux/macOS-Intel box (AMD friends): same command
  # Mac (Apple Silicon): Unicorn (QEMU softfloat) as a PC/RC arithmetic oracle
  python -I tests/x87/x87_runner.py --backend unicorn vectors.jsonl -o results-unicorn.jsonl

The first output line is {"meta": {...}} with the CPU name/vendor, OS and backend.
Only vectors with masked exceptions (cw & 0x3f == 0x3f) are run; others are skipped.
"""
import argparse
import ctypes
import json
import os
import platform
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87ops as X  # noqa: E402

VERSION = 1


def fill_block(v):
    blk = bytearray(X.BLOCK_SIZE)
    struct.pack_into("<H", blk, X.OFF_CW, int(v["cw"], 16))
    for i, k in enumerate("abc"):
        if k in v:
            blk[X.OFF_IN[i]:X.OFF_IN[i] + 10] = X.to_bytes(v[k])
    if "m" in v:
        b = X.to_bytes(v["m"])
        blk[X.OFF_MEM_IN:X.OFF_MEM_IN + len(b)] = b
    return blk


def read_block(op, blk):
    o = X.OPS[op]
    r = {"out": [X.from_bytes("x80", blk[X.OFF_OUT[k]:X.OFF_OUT[k] + 10]) for k in range(o["depth"])]}
    if o["out_mem"]:
        r["mem"] = X.from_bytes(o["out_mem"], blk[X.OFF_MEM_OUT:X.OFF_MEM_OUT + 10])
    r["sw"] = "%04x" % struct.unpack_from("<H", blk, X.OFF_SW)[0]
    r["sw_end"] = "%04x" % struct.unpack_from("<H", blk, X.OFF_SW_END)[0]
    return r


# --------------------------------------------------------------------------- native backend

class Native:
    """Executes stubs on the host CPU (x86 or x86-64; Windows, Linux or Intel macOS)."""

    def __init__(self):
        mach = platform.machine().lower()
        if mach not in ("amd64", "x86_64", "x86", "i386", "i686"):
            raise SystemExit("native backend needs an x86 CPU (this is %s); use --backend unicorn" % mach)
        self.win = sys.platform == "win32"
        self.bits64 = struct.calcsize("P") == 8
        self.sysv = self.bits64 and not self.win
        self.size = 1 << 16
        if self.win:
            k32 = ctypes.windll.kernel32
            k32.VirtualAlloc.restype = ctypes.c_void_p
            k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
            self.code = k32.VirtualAlloc(None, self.size, 0x3000, 0x40)   # MEM_COMMIT|RESERVE, RWX
        else:
            import mmap
            self._mm = mmap.mmap(-1, self.size, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
            self.code = ctypes.addressof(ctypes.c_char.from_buffer(self._mm))
        if not self.code:
            raise SystemExit("could not allocate executable memory")
        self.cache = {}
        self.off = 0
        self.block = (ctypes.c_ubyte * X.BLOCK_SIZE)()
        if not self.bits64:
            # 32-bit: cdecl with the block pointer on the stack -> mov ecx, [esp+4] first
            self.prefix = bytes.fromhex("8b4c2404")
        else:
            self.prefix = b""

    def fn(self, op):
        f = self.cache.get(op)
        if f:
            return f
        stub = self.prefix + X.build_stub(op, sysv=self.sysv)
        addr = self.code + self.off
        ctypes.memmove(addr, stub, len(stub))
        self.off += (len(stub) + 15) & ~15
        f = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(addr)
        self.cache[op] = f
        return f

    def run(self, v):
        blk = fill_block(v)
        ctypes.memmove(self.block, bytes(blk), X.BLOCK_SIZE)
        self.fn(v["op"])(ctypes.addressof(self.block))
        return read_block(v["op"], bytearray(self.block))

    def meta(self):
        return {"backend": "native", "cpu": cpu_name(), "machine": platform.machine(),
                "os": platform.platform(), "python": sys.version.split()[0], "bits": 64 if self.bits64 else 32}


def cpu_name():
    try:
        if sys.platform == "win32":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return "%s | %s | %s" % (winreg.QueryValueEx(k, "ProcessorNameString")[0].strip(),
                                     winreg.QueryValueEx(k, "VendorIdentifier")[0],
                                     winreg.QueryValueEx(k, "Identifier")[0])
        if os.path.exists("/proc/cpuinfo"):
            info = open("/proc/cpuinfo").read()
            name = [l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("model name")]
            ven = [l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("vendor_id")]
            return "%s | %s" % (name[0] if name else "?", ven[0] if ven else "?")
        import subprocess
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                              text=True).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return "unknown (%s)" % e


# -------------------------------------------------------------------------- unicorn backend

class UnicornBackend:
    """Executes the same stubs in Unicorn's 32-bit x86 (QEMU softfloat x87). Exact for PC/RC
    arithmetic and conversions as far as tested; its transcendentals are computed in double."""

    CODE, BLOCK, STACK = 0x10000, 0x20000, 0x30000

    def __init__(self):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
        from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(self.CODE, 0x30000)
        self.ECX, self.ESP = UC_X86_REG_ECX, UC_X86_REG_ESP
        self.cache = {}
        self.ret = self.CODE + 0xfff0
        self.uc.mem_write(self.ret, b"\xf4")
        self.off = 0

    def run(self, v):
        op = v["op"]
        if op not in self.cache:
            stub = X.build_stub(op)
            addr = self.CODE + self.off
            self.uc.mem_write(addr, stub)
            self.off += (len(stub) + 15) & ~15
            self.cache[op] = (addr, len(stub))
        addr, n = self.cache[op]
        self.uc.mem_write(self.BLOCK, bytes(fill_block(v)))
        self.uc.reg_write(self.ECX, self.BLOCK)
        self.uc.reg_write(self.ESP, self.STACK + 0xff00)
        self.uc.emu_start(addr, addr + n - 1)        # stop before the RET
        return read_block(op, bytearray(self.uc.mem_read(self.BLOCK, X.BLOCK_SIZE)))

    def meta(self):
        import unicorn
        return {"backend": "unicorn", "unicorn": unicorn.__version__, "machine": platform.machine(),
                "os": platform.platform()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vectors")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--backend", choices=["native", "unicorn"], default="native")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    be = Native() if a.backend == "native" else UnicornBackend()
    n = skipped = 0
    with open(a.vectors) as f, open(a.out, "w") as out:
        meta = be.meta()
        meta["runner"] = VERSION
        out.write(json.dumps({"meta": meta}) + "\n")
        for line in f:
            if not line.strip():
                continue
            v = json.loads(line)
            if v.get("kind", "x87") != "x87" or v.get("op") not in X.OPS:
                skipped += 1
                continue
            if int(v["cw"], 16) & 0x3f != 0x3f:
                skipped += 1
                continue
            r = be.run(v)
            r["id"] = v["id"]
            out.write(json.dumps(r) + "\n")
            n += 1
            if a.limit and n >= a.limit:
                break
    print("%s: %d vectors run, %d skipped -> %s" % (meta.get("cpu", a.backend), n, skipped, a.out))


if __name__ == "__main__":
    main()
