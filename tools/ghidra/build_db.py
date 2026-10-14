#!/usr/bin/env python3
"""Build the canonical function DB (db/functions/<module>.json) from Ghidra facts + the PE.

Inputs (all local, ignored): analysis/func-db/facts/<slug>.json.gz (ghidra_run.py export)
and the canonical game file. Outputs:
  db/functions/<module>.json                tracked: addresses, sizes, names, signatures,
                                            flags and counts only (no bytes, no code)
  analysis/func-db/work/seeds-<slug>.json   evidence-backed function starts Ghidra lacks
                                            (input to 'ghidra_run.py seed')
  analysis/func-db/work/stats-<slug>.json   coverage / tagging statistics (docs numbers)

Schema and method: docs/reference/function-db.md.
"""
import argparse
import gzip
import json
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import pemodel  # noqa: E402
import rtti  # noqa: E402
import tagging  # noqa: E402

SCHEMA = "neoee-funcdb/1"

# ---------------------------------------------------------------- x87 classes --
X87_CLASS = {}
for _m in ("fld", "fst", "fstp"):
    X87_CLASS[_m] = "ldst"
for _m in ("fild", "fbld"):
    X87_CLASS[_m] = "int_load"
for _m in ("fist", "fistp", "fisttp", "fbstp"):
    X87_CLASS[_m] = "int_store"          # depends on the rounding-control field
for _m in ("fadd", "faddp", "fsub", "fsubp", "fsubr", "fsubrp", "fmul", "fmulp", "fdiv",
           "fdivp", "fdivr", "fdivrp", "fiadd", "fisub", "fisubr", "fimul", "fidiv", "fidivr"):
    X87_CLASS[_m] = "arith"
for _m in ("fchs", "fabs"):
    X87_CLASS[_m] = "sign"
X87_CLASS["fsqrt"] = "sqrt"
for _m in ("fsin", "fcos", "fsincos", "fptan", "fpatan", "f2xm1", "fyl2x", "fyl2xp1"):
    X87_CLASS[_m] = "transcendental"
for _m in ("fprem", "fprem1", "fscale", "fxtract", "frndint"):
    X87_CLASS[_m] = "misc_math"          # frndint also depends on RC
for _m in ("fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp", "ficom", "ficomp",
           "fcomi", "fcomip", "fucomi", "fucomip", "ftst", "fxam"):
    X87_CLASS[_m] = "compare"
for _m in ("fld1", "fldz"):
    X87_CLASS[_m] = "const"
for _m in ("fldpi", "fldl2e", "fldl2t", "fldlg2", "fldln2"):
    X87_CLASS[_m] = "const_ext"          # 64-bit-significand constants
for _m in ("fldcw", "fnstcw", "fstcw"):
    X87_CLASS[_m] = "control_word"
for _m in ("fnstsw", "fstsw"):
    X87_CLASS[_m] = "status_word"
for _m in ("fninit", "finit", "fnclex", "fclex", "fldenv", "fnstenv", "fstenv", "frstor",
           "fnsave", "fsave"):
    X87_CLASS[_m] = "env"
for _m in ("fxch", "ffree", "ffreep", "fincstp", "fdecstp", "fnop"):
    X87_CLASS[_m] = "stack"
for _m in ("fcmovb", "fcmove", "fcmovbe", "fcmovu", "fcmovnb", "fcmovne", "fcmovnbe", "fcmovnu"):
    X87_CLASS[_m] = "cmov"
SIMD_RE = re.compile(r"\b(x?mm\d)\b")
SIMD_MNEM = {"emms", "femms", "cpuid", "movd", "movq"}

HEX = re.compile(r"^0x[0-9a-f]+$")
MEMABS = re.compile(r"^dword ptr \[0x([0-9a-f]+)\]$")
JTAB = re.compile(r"^dword ptr \[(e[a-ds][xip])\*4 \+ 0x([0-9a-f]+)\]$")
IMM_TAIL = re.compile(r", (0x[0-9a-f]+)$")
TERMINATORS = {"ret", "retf", "jmp", "iretd", "hlt", "int3", "ud2"}
# instructions an MSVC 6 compiler never emits in game code: data decoded as code
ODD_MNEM = {"in", "out", "insb", "insd", "outsb", "outsd", "into", "iretd", "les", "lds", "aam",
            "aad", "daa", "das", "aaa", "aas", "arpl", "bound", "retf", "ljmp", "lcall", "salc",
            "hlt", "sti", "cli", "pushal", "popal", "loopne", "loope", "fisttp", "int1", "icebp"}


def load_facts(slug):
    path = common.WORK / "facts" / ("%s.json.gz" % slug)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def hx(v):
    return "%#x" % v


class Func:
    __slots__ = ("entry", "name", "ns", "src", "cc", "proto", "noreturn", "purge", "varargs",
                 "thunk", "body", "labels", "size", "insns", "f", "kind", "seeded")

    def __init__(self, rec):
        self.entry = rec["entry"]
        self.name = rec["name"]
        self.ns = rec["ns"]
        self.src = rec["src"]
        self.cc = rec["cc"]
        self.proto = rec["proto"]
        self.noreturn = rec["noreturn"]
        self.purge = rec["purge"]
        self.varargs = rec.get("varargs", False)
        self.thunk = rec.get("thunk")
        self.body = [tuple(r) for r in rec["body"]]
        self.labels = rec.get("labels", [])
        self.size = sum(b - a for a, b in self.body)
        self.insns = 0
        self.f = {}            # computed features
        self.kind = "function"
        self.seeded = False


class Builder:
    def __init__(self, slug):
        self.slug = slug
        self.pm = pemodel.PEModule(slug)
        self.facts = load_facts(slug)
        if self.facts["sha256"] != self.pm.sha256:
            raise SystemExit("facts for %s do not match the module hash" % slug)
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.stats = {"module": self.pm.name}
        self.dem = self.facts.get("demangled", {})
        seeds_path = common.WORK / "work" / ("seeded-%s.json" % slug)
        self.seeded = set()
        if seeds_path.exists():
            self.seeded = {int(s["va"], 16) for s in json.loads(seeds_path.read_text())}

    # ------------------------------------------------------------------ setup --
    def load_functions(self):
        pm = self.pm
        funcs, dropped = [], []
        for rec in self.facts["functions"]:
            if not pm.in_code(rec["entry"]):
                dropped.append(rec["entry"])
                continue
            funcs.append(Func(rec))
        funcs.sort(key=lambda f: f.entry)
        self.funcs = funcs
        self.by_entry = {f.entry: f for f in funcs}
        self._entries = [f.entry for f in funcs]
        ranges = sorted((a, b, f) for f in funcs for a, b in f.body)
        self.ranges = ranges
        self.range_starts = [r[0] for r in ranges]
        self.stats["ghidra_functions"] = len(self.facts["functions"])
        self.stats["dropped_outside_code"] = [hx(a) for a in dropped]
        for f in funcs:
            if f.entry in self.seeded:
                f.seeded = True

    def next_entry(self, va):
        i = bisect_right(self._entries, va)
        return self._entries[i] if i < len(self._entries) else va + 0x10000

    def owner(self, va):
        i = bisect_right(self.range_starts, va) - 1
        if i >= 0 and va < self.ranges[i][1]:
            return self.ranges[i][2]
        return None

    # ----------------------------------------------------------------- decode --
    def decode_all(self):
        pm = self.pm
        n = pm.size
        self.istart = bytearray(n)       # 1 = instruction start (function or orphan code)
        self.calls = defaultdict(list)   # callee va -> [caller func]
        self.tail = defaultdict(list)    # jump target outside own body -> [func]
        self.imm_refs = defaultdict(list)  # code-address immediates -> [func]
        self.vt_writes = defaultdict(set)  # func -> vtable addresses stored
        self.jtabs = []                  # (func, table va, index reg, insn va)
        self.fs0 = {}                    # func -> list of pushes before mov fs:[0],esp
        self.ehprolog_calls = []         # (func, eax imm, call target)
        for f in self.funcs:
            self._decode_func(f)
        # orphan instructions (Ghidra code outside every function)
        self.orphans = [tuple(r) for r in self.facts["orphans"] if pm.in_code(r[0])]
        self.orphan_starts = set()
        for a, b in self.orphans:
            for va, size, m, o in self.md.disasm_lite(pm.bytes_at(a, b - a), a):
                self.istart[va - pm.base] = 2
                self.orphan_starts.add(va)

    def _decode_func(self, f):
        pm = self.pm
        base = pm.base
        feats = f.f
        x87 = Counter()
        mem80 = 0
        simd = 0
        icalls = 0
        vcalls = 0
        imports = []
        callees = Counter()
        jumps_out = set()
        pushes = []
        rets = set()
        insns = 0
        last_m = None
        ecx_state = None   # None unknown, 'read' read-before-write, 'write'
        edx_state = None
        first_block = True
        odd = 0
        undecodable = 0
        for lo, hi in f.body:
            end = lo
            for va, size, m, o in self.md.disasm_lite(pm.bytes_at(lo, hi - lo), lo):
                end = va + size
                insns += 1
                if m in ODD_MNEM:
                    odd += 1
                self.istart[va - base] = 1
                last_m = m
                if m[0] == "f" or m == "wait":
                    cls = X87_CLASS.get(m)
                    if cls:
                        x87[cls] += 1
                        if "xword ptr" in o:
                            mem80 += 1
                    elif m == "wait":
                        x87["wait"] += 1
                if m in SIMD_MNEM or ("mm" in o and SIMD_RE.search(o)):
                    simd += 1
                if first_block:
                    # crude this/fastcall evidence: first use of ECX/EDX in the entry block
                    for reg, st in (("ecx", "c"), ("edx", "d")):
                        cur = ecx_state if st == "c" else edx_state
                        if cur is None and reg in o:
                            ops = o.split(", ")
                            dst_only = (m in ("mov", "lea", "pop", "movzx", "movsx") and ops[0] == reg
                                        and reg not in ", ".join(ops[1:]))
                            xor_self = m in ("xor", "sub") and ops == [reg, reg]
                            state = "write" if (dst_only or xor_self) else "read"
                            if st == "c":
                                ecx_state = state
                            else:
                                edx_state = state
                    if m in TERMINATORS or m.startswith("j") or m == "call":
                        first_block = False
                if m == "call":
                    if HEX.match(o):
                        t = int(o, 16)
                        self.calls[t].append(f)
                        callees[t] += 1
                    else:
                        mm = MEMABS.match(o)
                        if mm and int(mm.group(1), 16) in pm.iat:
                            imports.append(int(mm.group(1), 16))
                        elif "[" in o and "+" in o or o.startswith("dword ptr [e"):
                            vcalls += 1
                        else:
                            icalls += 1
                elif m == "jmp" or (m[0] == "j" and m != "jmp"):
                    if HEX.match(o):
                        t = int(o, 16)
                        if not any(a <= t < b for a, b in f.body):
                            jumps_out.add(t)
                            self.tail[t].append(f)
                    elif m == "jmp":
                        mm = MEMABS.match(o)
                        if mm and int(mm.group(1), 16) in pm.iat:
                            imports.append(int(mm.group(1), 16))
                        jt = JTAB.match(o)
                        if jt:
                            self.jtabs.append((f, int(jt.group(2), 16), jt.group(1), va))
                elif m == "ret":
                    rets.add(int(o, 0) if o else 0)
                elif m == "push":
                    if HEX.match(o):
                        v = int(o, 16)
                        pushes.append((va, v))
                        if pm.in_code(v):
                            self.imm_refs[v].append(f)
                elif m == "mov":
                    if o == "dword ptr fs:[0], esp":
                        self.fs0.setdefault(f, []).append([p for p in pushes[-4:]])
                    t = IMM_TAIL.search(o)
                    if t:
                        v = int(t.group(1), 16)
                        if pm.in_code(v):
                            self.imm_refs[v].append(f)
                            if o.startswith("eax, "):
                                pushes.append((va, v))   # MOV EAX, stub; CALL __EH_prolog
                        elif o.startswith("dword ptr [e") and pm.in_image(v):
                            self.vt_writes[f].add(v)
            if end < hi:
                undecodable += hi - end
        f.insns = insns
        feats["odd"] = odd
        feats["undecodable"] = undecodable
        feats["x87"] = dict(x87)
        feats["x87_m80"] = mem80
        feats["simd"] = simd
        feats["icalls"] = icalls
        feats["vcalls"] = vcalls
        feats["imports"] = imports
        feats["callees"] = callees
        feats["jumps_out"] = jumps_out
        feats["rets"] = sorted(rets)
        feats["ecx"] = ecx_state
        feats["edx"] = edx_state
        feats["last"] = last_m
        feats["pushes"] = pushes

    # ------------------------------------------------------------- thunks/EH --
    def classify_thunks(self):
        """Import thunks (JMP [IAT]) and jump thunks (single JMP rel32)."""
        pm = self.pm
        self.import_thunks = {}      # func entry -> iat slot
        self._thunk_cache = {}
        for f in self.funcs:
            if f.insns == 1:
                ins = next(self.md.disasm_lite(pm.bytes_at(f.entry, 16), f.entry, count=1), None)
                if ins and ins[2] == "jmp":
                    mm = MEMABS.match(ins[3])
                    if mm and int(mm.group(1), 16) in pm.iat:
                        self.import_thunks[f.entry] = int(mm.group(1), 16)
                        f.kind = "import_thunk"
                    elif HEX.match(ins[3]):
                        f.kind = "thunk"
        # functions reachable only to forward to an import (via an import thunk)
        self.thunk_target_name = {}
        for e, slot in self.import_thunks.items():
            self.thunk_target_name[e] = pm.iat[slot]

    def import_name(self, slot):
        dll, name = self.pm.iat[slot]
        return dll, name

    def callee_import(self, va):
        """(dll, name) when the instruction at va is JMP [IAT] (an import thunk), else None."""
        cache = self._thunk_cache
        if va in cache:
            return cache[va]
        r = None
        slot = self.import_thunks.get(va)
        if slot is None and self.pm.in_code(va) and self.pm.bytes_at(va, 2) == b"\xff\x25":
            t = self.pm.u32(va + 2)
            if t in self.pm.iat:
                slot = t
        if slot is not None:
            r = self.pm.iat[slot]
        cache[va] = r
        return r

    def find_eh(self):
        """C++ EH / SEH frames per function, FuncInfo tables, funclets, throw sites."""
        pm = self.pm
        # helpers: who is __CxxFrameHandler / _except_handler3 / _CxxThrowException / _EH_prolog
        self.eh_stub_of = {}        # stub va -> func
        self.funcinfo = {}          # funcinfo va -> {...}
        self.eh_targets = defaultdict(list)   # code va -> [(kind, owner func)]
        self.scope_targets = defaultdict(list)
        ehprolog = set()
        # _EH_prolog: body starts PUSH -1; PUSH EAX; MOV EAX, FS:[0]
        for f in self.funcs:
            b = pm.bytes_at(f.entry, 10)
            if b.startswith(b"\x6a\xff\x50\x64\xa1\x00\x00\x00\x00"):
                ehprolog.add(f.entry)
        self.ehprolog = ehprolog
        cxx_handlers = {"__CxxFrameHandler"}
        seh_handlers = {"_except_handler3", "_except_handler2", "__except_handler3"}
        for f in self.funcs:
            frames = []
            for pushes in self.fs0.get(f, []):
                frames.append([v for _, v in pushes])
            # MOV EAX, stub; CALL _EH_prolog
            for t in f.f["callees"]:
                if t in ehprolog:
                    stubs = [v for _, v in f.f["pushes"]]
                    frames.append(stubs[-1:])
            kinds = []
            for vals in frames:
                kind = "seh_other"
                for v in reversed(vals):
                    imp = self.callee_import(v)
                    if imp and imp[1] in seh_handlers:
                        kind = "seh3"
                        # scope table is the push before the handler
                        i = vals.index(v)
                        if i > 0:
                            self._scope_table(f, vals[i - 1])
                        break
                    ins = list(self.md.disasm_lite(pm.bytes_at(v, 10), v, count=2))
                    if len(ins) == 2 and ins[0][2] == "mov" and ins[0][3].startswith("eax, 0x") \
                            and ins[1][2] == "jmp" and HEX.match(ins[1][3]):
                        fi = int(ins[0][3][5:], 16)
                        tgt = int(ins[1][3], 16)
                        imp = self.callee_import(tgt)
                        if (imp and imp[1] in cxx_handlers) or pm.u32(fi) == 0x19930520:
                            kind = "cxx"
                            self.eh_stub_of[v] = f
                            self._funcinfo(f, fi)
                            break
                kinds.append(kind)
            f.f["eh"] = sorted(set(kinds))
            throws = 0
            for t, n in f.f["callees"].items():
                imp = self.callee_import(t)
                if imp and imp[1] == "_CxxThrowException":
                    throws += n
            for t in f.f["jumps_out"]:
                imp = self.callee_import(t)
                if imp and imp[1] == "_CxxThrowException":
                    throws += 1
            for slot in f.f["imports"]:
                if pm.iat[slot][1] == "_CxxThrowException":
                    throws += 1
            f.f["throws"] = throws

    def _funcinfo(self, f, fi):
        pm = self.pm
        if fi in self.funcinfo or pm.u32(fi) != 0x19930520:
            return
        rec = {"func": f, "max_state": pm.u32(fi + 4), "unwind": [], "catches": [], "try": 0}
        n, um = pm.u32(fi + 4), pm.u32(fi + 8)
        if n and um and n < 10000:
            for i in range(n):
                act = pm.u32(um + 8 * i + 4)
                if act and pm.in_code(act):
                    rec["unwind"].append(act)
                    self.eh_targets[act].append(("unwind", f))
        nt, tm = pm.u32(fi + 12), pm.u32(fi + 16)
        rec["try"] = nt or 0
        if nt and tm and nt < 1000:
            for i in range(nt):
                e = tm + 20 * i
                nc, ha = pm.u32(e + 12), pm.u32(e + 16)
                if nc and ha and nc < 100:
                    for j in range(nc):
                        h = pm.u32(ha + 16 * j + 12)
                        if h and pm.in_code(h):
                            rec["catches"].append(h)
                            self.eh_targets[h].append(("catch", f))
                            cont = self._catch_continuation(h)
                            if cont is not None:
                                self.eh_targets[cont].append(("catch_cont", f))
        self.funcinfo[fi] = rec

    def _catch_continuation(self, h):
        """A catch funclet ends 'MOV EAX, offset continuation; RET': the runtime jumps there."""
        prev = None
        for a, size, m, o in self.md.disasm_lite(self.pm.bytes_at(h, 4096), h):
            if m == "ret":
                if prev and prev[0] == "mov" and prev[1].startswith("eax, 0x"):
                    v = int(prev[1][5:], 16)
                    if self.pm.in_code(v):
                        return v
                return None
            prev = (m, o)
        return None

    def _scope_table(self, f, st):
        pm = self.pm
        if not pm.in_image(st) or pm.in_code(st):
            return
        for i in range(64):
            prev, flt, hnd = pm.u32(st + 12 * i), pm.u32(st + 12 * i + 4), pm.u32(st + 12 * i + 8)
            if prev is None or not (prev == 0xffffffff or prev < i):
                break
            ok = False
            for v, k in ((flt, "seh_filter"), (hnd, "seh_handler")):
                if v and pm.in_code(v):
                    self.scope_targets[v].append((k, f))
                    ok = True
            if not ok:
                break

    # --------------------------------------------------------------- evidence --
    def collect_evidence(self):
        pm = self.pm
        ev = defaultdict(Counter)          # target va -> Counter(kind)
        detail = defaultdict(list)         # target va -> [(kind, info)]
        self.rt = rtti.parse(pm)
        vt_slot_pos = set()
        for vt in self.rt["vtables"]:
            vt_slot_pos.add(vt["va"] - 4)
            for i, t in enumerate(vt["slots"]):
                vt_slot_pos.add(vt["va"] + 4 * i)
                ev[t]["vt"] += 1
                detail[t].append(("vt", (vt["td"], vt["va"], i)))
        # data pointers (4-aligned) from data sections, excluding vtable slots and
        # slots inside strings Ghidra defined (the EXE has no relocations to tell
        # pointers from text such as "nit\0" = 0x0074696e)
        in_string = bytearray(pm.size)
        for a, n, t, v in self.facts["data"]:
            if v is not None and pm.in_image(a):
                o = a - pm.base
                in_string[o:o + n] = b"\x01" * min(n, pm.size - o)
        self.data_ptrs = defaultdict(list)
        drm = {".cms_t", ".cms_d"}
        for sec in pm.data_sections():
            lo = sec["start"]
            hi = sec["start"] + min(max(sec["vsize"], sec["raw_size"]), sec["end"] - sec["start"])
            for va in range(lo, hi - 3, 4):
                if va in vt_slot_pos:
                    continue
                v = pm.u32(va)
                if v is None or not pm.in_code(v):
                    continue
                if pm.relocs and va not in pm.relocs:
                    continue          # DLL: an unrelocated dword is not a pointer
                if in_string[va - pm.base]:
                    continue
                if sec["name"] in drm and pm.section_of(v) not in drm:
                    continue          # DRM data says nothing about game code
                kind = "reloc" if pm.relocs else "dp"
                ev[v][kind] += 1
                self.data_ptrs[v].append(va)
        # DLL relocations inside code sections that point into code (switch tables, etc.)
        self.code_reloc_targets = Counter()
        for slot, v in pm.relocs.items():
            if pm.in_code(slot) and pm.in_code(v):
                self.code_reloc_targets[v] += 1
        drm = {".cms_t", ".cms_d"}
        in_drm = lambda va: pm.section_of(va) in drm   # noqa: E731
        for v, fs in self.imm_refs.items():
            # DRM code names .text ranges it decrypted/checked (0x401001, 0x4020ab, ...):
            # not evidence of game code entries
            n = sum(1 for f in fs if in_drm(v) or not in_drm(f.entry))
            if n:
                ev[v]["imm"] += n
        for v, lst in self.eh_targets.items():
            for k, f in lst:
                ev[v][k] += 1
        for v, lst in self.scope_targets.items():
            for k, f in lst:
                ev[v][k] += 1
        for stub in self.eh_stub_of:
            ev[stub]["eh_stub"] += 1
        for va, names in pm.exports.items():
            if pm.in_code(va):
                ev[va]["export"] += 1
        if pm.entry:
            ev[pm.entry]["entry"] += 1
        for t, fs in self.calls.items():
            n = sum(1 for f in fs if in_drm(t) or not in_drm(f.entry))
            if n:
                ev[t]["call"] += n
        self.ev = ev
        self.ev_detail = detail

    # ----------------------------------------------------------------- seeds --
    def seeds(self):
        """Addresses with evidence that are not function entries in Ghidra."""
        pm = self.pm
        out = []
        conflicts = Counter()
        alt = defaultdict(Counter)       # owner entry -> Counter(kind) for interior entries
        # a relocation proves a pointer, not code (the engine keeps data tables in .text),
        # so "reloc" and "dp" seeds must be Ghidra-decoded or pass validate_code
        strong = {"vt", "export", "entry", "unwind", "catch", "catch_cont", "seh_filter",
                  "seh_handler", "eh_stub", "call"}
        for t, kinds in sorted(self.ev.items()):
            if t in self.by_entry or not pm.in_code(t):
                continue
            own = self.owner(t)
            st = self.istart[t - pm.base]
            if own is not None:
                if st:
                    for k in kinds:
                        alt[own.entry][k] += kinds[k]
                else:
                    for k in kinds:
                        conflicts["interior_not_insn:" + k] += 1
                continue
            # not inside any function: orphan code or undecoded
            kset = set(kinds)
            if kset & strong:
                reason = "strong"
            elif st == 2:
                reason = "orphan_code"
            else:
                ok, why = self.validate_code(t)
                if not ok:
                    conflicts["rejected_" + why + ":" + ",".join(sorted(kset))] += 1
                    continue
                reason = "validated"
            out.append({"va": hx(t), "kinds": dict(kinds), "why": reason,
                        "section": pm.section_of(t)})
        self.alt = alt
        self.conflicts = conflicts
        return out

    def at_boundary(self, va):
        """Is va where a compiler could have started a function? After padding, after a
        RET / RET n / JMP, or right where another function's body ends."""
        pm = self.pm
        if any(sec["start"] == va for sec in pm.code_sections):
            return True
        o = va - pm.base
        b1 = pm.img[o - 1]
        if b1 in (0xCC, 0x90, 0xC3):
            return True
        if pm.img[o - 3] == 0xC2 or pm.img[o - 5] == 0xE9:
            return True
        if pm.img[o - 2] == 0xEB or pm.img[o - 6:o - 4] == b"\xff\x25":
            return True
        f = self.owner(va - 1)
        return f is not None and any(b == va for a, b in f.body)

    def validate_code(self, va, limit=4096):
        """Decode from va: plausible code ends in RET/JMP with no invalid or odd opcodes,
        and starts at a function boundary, not inside padding."""
        pm = self.pm
        if not self.at_boundary(va):
            return False, "not_at_boundary"
        n = 0
        datalike = 0
        for a, size, m, op in self.md.disasm_lite(pm.bytes_at(va, limit), va):
            n += 1
            if n == 1 and m in ("nop", "int3"):
                return False, "padding"
            if m == "add" and ("byte ptr [e" in op) and n <= 8:
                datalike += 1
                if datalike >= 2:
                    return False, "data_like"
            if m in ("int3", "hlt", "in", "out", "insb", "insd", "outsb", "outsd", "into", "iretd",
                     "les", "lds", "aam", "aad", "daa", "das", "aaa", "aas", "arpl", "bound",
                     "retf", "ljmp", "lcall", "salc", "sti", "cli", "pushal", "popal") \
                    or "es:" in op or "ss:" in op or "cs:" in op:
                return False, "odd_opcode"
            if self.istart[a - pm.base] or self.owner(a):
                return (n > 1), "runs_into_code" if n <= 1 else "ok"
            if m in ("ret", "jmp"):
                return True, "ok"
        return False, "no_terminator"

    # -------------------------------------------------------------- coverage --
    def coverage(self):
        pm = self.pm
        res = {}
        data_items = [(a, n, t) for a, n, t, v in self.facts["data"] if pm.in_code(a)]
        self.code_data = data_items
        for sec in pm.code_sections:
            lo = sec["start"]
            hi = lo + sec["vsize"]
            raw_hi = lo + min(sec["vsize"], sec["raw_size"])
            cls = bytearray(hi - lo)
            for f in self.funcs:
                for a, b in f.body:
                    if a < hi and b > lo:
                        cls[max(a, lo) - lo:min(b, hi) - lo] = b"\x01" * (min(b, hi) - max(a, lo))
            for a, b in self.orphans:
                for x in range(max(a, lo), min(b, hi)):
                    if cls[x - lo] == 0:
                        cls[x - lo] = 2
            for a, n, t in data_items:
                for x in range(max(a, lo), min(a + n, hi)):
                    if cls[x - lo] == 0:
                        cls[x - lo] = 3
            for tv, n in self.extra_data:
                for x in range(max(tv, lo), min(tv + n, hi)):
                    if cls[x - lo] == 0:
                        cls[x - lo] = 3
            img = pm.img
            for i in range(hi - lo):
                if cls[i] == 0:
                    if lo + i >= raw_hi or img[lo - pm.base + i] in (0xCC, 0x90):
                        cls[i] = 4
            # zero runs between code (>= 4 bytes) count as padding too
            i = 0
            n = hi - lo
            while i < n:
                if cls[i] == 0 and img[lo - pm.base + i] == 0:
                    j = i
                    while j < n and cls[j] == 0 and img[lo - pm.base + j] == 0:
                        j += 1
                    if j - i >= 4:
                        cls[i:j] = b"\x04" * (j - i)
                    i = j
                else:
                    i += 1
            # DLLs: an unknown run full of relocation slots is a pointer table, i.e. data
            # (the engine's .text holds DirectInput c_dfDIKeyboard/c_dfDIMouse formats)
            if pm.relocs:
                i = 0
                n = hi - lo
                rel = set(pm.relocs)
                while i < n:
                    if cls[i] == 0:
                        j = i
                        while j < n and cls[j] == 0:
                            j += 1
                        k = sum(1 for x in range(lo + i, lo + j) if x in rel)
                        if k and (j - i) <= 32 * k:
                            cls[i:j] = b"\x03" * (j - i)
                            self.extra_data.append((lo + i, j - i))
                        i = j
                    else:
                        i += 1
            c = Counter(cls)
            unknown_runs = []
            i = 0
            while i < n:
                if cls[i] == 0:
                    j = i
                    while j < n and cls[j] == 0:
                        j += 1
                    unknown_runs.append((lo + i, j - i))
                    i = j
                else:
                    i += 1
            total = hi - lo
            res[sec["name"]] = {
                "bytes": total, "function": c[1], "orphan_code": c[2], "data": c[3],
                "padding": c[4], "unknown": c[0],
                "pct": {"function": round(100.0 * c[1] / total, 3),
                        "orphan_code": round(100.0 * c[2] / total, 3),
                        "data": round(100.0 * c[3] / total, 3),
                        "padding": round(100.0 * c[4] / total, 3),
                        "unknown": round(100.0 * c[0] / total, 3),
                        "classified": round(100.0 * (total - c[0] - c[2]) / total, 3)},
                "unknown_runs": len(unknown_runs),
                "largest_unknown": [[hx(a), s] for a, s in sorted(unknown_runs, key=lambda r: -r[1])[:20]],
            }
            self.unknown_runs = getattr(self, "unknown_runs", []) + unknown_runs
        return res

    # ---------------------------------------------------------- switch tables --
    def switch_tables(self):
        """Jump tables: Ghidra-resolved (COMPUTED_JUMP refs) and the MSVC shape Ghidra misses
        (index reloaded from a stack slot after the bounds check)."""
        pm = self.pm
        resolved = defaultdict(set)
        for r in self.facts["refs"]:
            if r[2] == "COMPUTED_JUMP" and r[1] is not None:
                resolved[r[0]].add(r[1])
        data_at = {a: (n, t) for a, n, t, v in self.facts["data"]}
        self.extra_data = []
        self.switch_fixes = []
        tables = []
        for f, tab, reg, insn in self.jtabs:
            entry = {"func": f, "table": tab, "insn": insn, "ghidra": insn in resolved}
            n_entries = None
            if insn in resolved:
                # Ghidra's switch analysis: the table runs while entries name its targets
                tset = resolved[insn]
                k = 0
                while pm.u32(tab + 4 * k) in tset and k < 4096:
                    k += 1
                n_entries = k or len(tset)
            else:
                # MSVC shape: CMP idx, N ; JA default ; [MOV idx, [EBP-x]] ; JMP [idx*4+T]
                lo = max(insn - 24, f.entry)
                for a, sz, m, o in reversed(list(self.md.disasm_lite(pm.bytes_at(lo, insn - lo), lo))):
                    if m == "cmp":
                        last = o.split(", ")[-1]
                        if re.match(r"^(0x[0-9a-f]+|\d+)$", last):
                            n_entries = int(last, 0) + 1
                        break
            if n_entries is None:
                k = 0
                while pm.in_code(pm.u32(tab + 4 * k) or 0) and k < 1024:
                    k += 1
                n_entries = k
            # Ghidra can under-resolve a direct table (0x7c985f: 2 of 4 cases); the CMP
            # bound decides for a one-level table, and the missing cases join the body
            if insn in resolved:
                lo2 = max(insn - 32, f.entry)
                prev2 = list(self.md.disasm_lite(pm.bytes_at(lo2, insn - lo2), lo2))
                two_level = any(m in ("mov", "movzx") and "byte ptr [" in o for a, sz, m, o in prev2[-3:])
                bound = None
                for a, sz, m, o in reversed(prev2):
                    if m == "cmp":
                        last = o.split(", ")[-1]
                        if re.match(r"^(0x[0-9a-f]+|\d+)$", last):
                            bound = int(last, 0) + 1
                        break
                nxt = self.next_entry(f.entry)
                if not two_level and bound and bound > n_entries and bound <= 1024:
                    tg = [pm.u32(tab + 4 * i) for i in range(bound)]
                    if all(t is not None and f.entry <= t < nxt and pm.in_code(t) for t in tg):
                        missing = sorted({t for t in tg if not any(a <= t < b_ for a, b_ in f.body)})
                        if missing:
                            self.switch_fixes.append({"va": hx(insn), "why": "switch_case",
                                                      "kinds": {"switch": len(missing)},
                                                      "table": hx(tab), "entries": bound,
                                                      "targets": [hx(t) for t in missing],
                                                      "section": pm.section_of(insn)})
                        n_entries = bound
            entry["entries"] = n_entries
            targets = [pm.u32(tab + 4 * i) for i in range(n_entries or 0)]
            entry["targets"] = [t for t in targets if t is not None]
            if pm.in_code(tab) and n_entries:
                self.extra_data.append((tab, 4 * n_entries))
            tables.append(entry)
        self.tables = tables
        return tables

    def qa_overlaps(self):
        """Function entries that fall inside an instruction of another function body, and
        bodies that overlap: both mean one of the two decodes is wrong."""
        pm = self.pm
        bad = []
        for f in self.funcs:
            e = f.entry
            for s_ in range(max(e - 14, pm.base), e):
                if self.istart[s_ - pm.base] != 1:
                    continue
                own = self.owner(s_)
                if own is None or own is f:
                    continue
                ins = next(self.md.disasm_lite(pm.bytes_at(s_, 16), s_, count=1), None)
                if ins and s_ + ins[1] > e:
                    bad.append([hx(e), hx(s_), hx(own.entry)])
                    break
        overlaps = []
        prev = None
        for a, b_, f in self.ranges:
            if prev and a < prev[1] and prev[2] is not f:
                overlaps.append([hx(prev[2].entry), hx(f.entry), hx(a)])
            if prev is None or b_ > prev[1]:
                prev = (a, b_, f)
        odd = [[hx(f.entry), f.f["odd"], f.f["undecodable"], pm.section_of(f.entry)]
               for f in self.funcs if f.f["odd"] or f.f["undecodable"]]
        self.stats["qa"] = {"functions_with_odd_or_undecodable": odd[:200],
                            "functions_with_odd_or_undecodable_count": len(odd),
                            "functions_with_odd_or_undecodable_outside_drm":
                                sum(1 for o in odd if o[3] not in (".cms_t", ".cms_d")),
                            "entries_inside_other_instruction": bad[:100],
                            "entries_inside_other_instruction_count": len(bad),
                            "overlapping_bodies": overlaps[:100], "overlapping_bodies_count": len(overlaps)}

    # ---------------------------------------------------------------- build --
    def build(self):
        self.load_functions()
        self.decode_all()
        self.classify_thunks()
        self.find_eh()
        self.collect_evidence()
        self.switch_tables()
        seeds = self.seeds() + [x for x in self.switch_fixes if x["section"] not in (".cms_t",)]
        self.qa_overlaps()
        cov = self.coverage()
        self.stats["coverage"] = cov
        self.stats["seeds"] = Counter(s["why"] for s in seeds)
        self.stats["seed_kinds"] = Counter(k for s in seeds for k in s["kinds"])
        self.stats["evidence_conflicts"] = dict(self.conflicts.most_common())
        return seeds


def summary():
    """Cross-module summary (work/summary.json) and Markdown tables for the docs."""
    rows = []
    allstats = {}
    for slug, name, sha, base in common.MODULES:
        p = common.WORK / "work" / ("stats-%s.json" % slug)
        if not p.exists():
            continue
        st = json.loads(p.read_text())
        allstats[slug] = st
    (common.WORK / "work" / "summary.json").write_text(json.dumps(allstats, indent=1, default=str) + "\n")
    print("| Module | Functions | Ghidra alone | Seeded | Code bytes | in functions | data | padding | orphan | unknown | classified |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for slug, st in allstats.items():
        for sec, c in st.get("coverage", {}).items():
            print("| %s %s | %s | %s | %s | %s | %.3f%% | %.3f%% | %.3f%% | %.3f%% | %.3f%% | **%.3f%%** |" % (
                st["module"], sec, st.get("functions", "?"),
                st.get("functions", 0) - st.get("seeded", 0) - len(st.get("dropped_outside_code", [])),
                st.get("seeded", "?"), c["bytes"], c["pct"]["function"], c["pct"]["data"],
                c["pct"]["padding"], c["pct"]["orphan_code"], c["pct"]["unknown"], c["pct"]["classified"]))
    print()
    tags = sorted({t for st in allstats.values() for t in st.get("tags", {})})
    print("| Module | " + " | ".join(tags) + " |")
    print("|---|" + "---|" * len(tags))
    for slug, st in allstats.items():
        print("| %s | " % st["module"] + " | ".join(str(st.get("tags", {}).get(t, "")) for t in tags) + " |")
    print()
    print("| Module | vtables | slots | distinct targets | targets = function start | Ghidra alone | added by seeding |")
    print("|---|---|---|---|---|---|---|")
    for slug, st in allstats.items():
        v = st.get("vtable_slot_coverage")
        if v:
            print("| %s | %d | %d | %d | %d | %d | %d |" % (st["module"], v["vtables"], v["slots"],
                  v["distinct_targets"], v["targets_function_start"], v["targets_found_by_ghidra_alone"],
                  v["targets_added_by_seeding"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*")
    ap.add_argument("--seeds-only", action="store_true", help="only write work/seeds-<slug>.json")
    ap.add_argument("--summary", action="store_true", help="cross-module summary from work/stats-*.json")
    args = ap.parse_args()
    if args.summary:
        summary()
        return
    for slug in common.select(args.modules):
        b = Builder(slug)
        seeds = b.build()
        work = common.WORK / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / ("seeds-%s.json" % slug)).write_text(json.dumps(seeds, indent=0) + "\n")
        if not args.seeds_only:
            db = tagging.finish(b)
            out = common.DB_DIR / common.db_filename(slug)
            out.parent.mkdir(parents=True, exist_ok=True)
            tagging.write_db(db, out)
        (work / ("stats-%s.json" % slug)).write_text(json.dumps(b.stats, indent=1, default=str) + "\n")
        cov = b.stats["coverage"]
        print("[db] %s: %d functions, seeds %s, coverage %s" % (
            slug, len(b.funcs), dict(b.stats["seeds"]),
            {k: v["pct"] for k, v in cov.items()}), flush=True)


if __name__ == "__main__":
    main()
