"""x87 instruction-class census over a module's function bodies.

Decodes, with capstone, every instruction address that a Ghidra listing export places inside
a function (`<export>/functions/<addr>.asm`, as written by the kit's ExportProgram.java), so
data is never decoded as code. Reads the bytes from the user's own PE file; nothing from the
binary is written except addresses, counts and mnemonics.

Reports, per module:
  * every x87 instruction by class (load/store by operand size, arithmetic, constants,
    transcendentals, control/status, compare idioms);
  * the sources of values with more than 53 significant bits or a non-double exponent:
    FLD constants other than FLD1/FLDZ, FILD m64, FLD m80, FBLD, transcendental results;
  * every FLDCW / FNSTCW site with a guess at the value loaded (OR/AND on the saved word);
  * every FIST/FISTP site, and whether an FLDCW precedes it in the same function (RC switch);
  * the compare idioms after FNSTSW (SAHF, TEST AH, AND AH...);
  * call sites of maths imports (msvcrt sqrt/fabs/_ftol/_CI*/sin..., engine UM*), via direct
    IAT calls, `mov reg,[IAT]; call reg`, and one-jump thunks.

  python -I tests/x87/census.py --pe "<game>/Empire Earth.exe" \
      --listing "<export>/Empire Earth.exe" --out analysis/sync-math/census
  python -I tests/x87/census.py --pe "<game>/Default.dll" --listing <export>/Default.dll \
      --listing-base 0x10200000 --out ...      # when the listing came from a rebased copy

Addresses in the output are RVAs (module + RVA), so they are independent of load address.
"""
import argparse
import collections
import json
import os
import re
import sys

import capstone
import pefile
from capstone import x86 as cx

# Imports whose call sites matter for exact x87 (substring match on "dll!name", lower case).
MATH_IMPORTS = re.compile(
    r"msvcrt\.dll!(sqrt|fabs|_ftol|_ci\w+|sin|cos|tan|asin|acos|atan2?|sinh|cosh|tanh|exp|log|"
    r"log10|pow|fmod|floor|ceil|modf|frexp|ldexp|_hypot|_cabs|_isnan|_finite|_fpclass|_copysign|"
    r"_chgsign|_logb|_nextafter|_scalb|_j0|_j1|_y0|_y1|_controlfp|_control87|_fpreset|_clearfp|"
    r"_statusfp|atof|strtod|_atodbl|_atoflt|_ecvt|_fcvt|_gcvt|rand|srand|sscanf|_snscanf|fscanf|"
    r"sprintf|_snprintf|vsprintf|_vsnprintf|fprintf)$"
    r"|!\?um\w+|umfastmath|!\?(sqrt|length|normalize|distance)\w*@",
    re.I)

CONST_LOADS = {"fldpi", "fldl2e", "fldl2t", "fldlg2", "fldln2"}
EXACT_CONSTS = {"fld1", "fldz"}
TRANSCENDENTAL = {"fsin", "fcos", "fsincos", "fptan", "fpatan", "f2xm1", "fyl2x", "fyl2xp1"}
OTHER_SPECIAL = {"fscale", "fprem", "fprem1", "frndint", "fxtract", "fsqrt"}
ARITH = {"fadd", "faddp", "fsub", "fsubp", "fsubr", "fsubrp", "fmul", "fmulp", "fdiv", "fdivp",
         "fdivr", "fdivrp"}
IARITH = {"fiadd", "fisub", "fisubr", "fimul", "fidiv", "fidivr"}
COMPARE = {"fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp", "ficom", "ficomp", "ftst",
           "fxam", "fcomi", "fcomip", "fucomi", "fucomip", "fcompi", "fucompi"}
CONTROL = {"fldcw", "fnstcw", "fstcw", "fnstsw", "fstsw", "fninit", "finit", "fnclex", "fclex",
           "fnsave", "fsave", "frstor", "fldenv", "fnstenv", "fstenv", "wait", "fwait", "ffree",
           "ffreep", "fdecstp", "fincstp", "fnop"}
MOVES = {"fxch", "fchs", "fabs"}


def demangle(name):
    """Readable form of an MSVC C++ name, good enough for a report: ?Name@Class@@... -> Class::Name."""
    if not name.startswith("?"):
        return name
    m = re.match(r"\?([^@]+)@([^@]*)@?@", name)
    if not m:
        return name
    fn, cls = m.group(1), m.group(2)
    return "%s::%s" % (cls, fn) if cls else fn


class Module:
    def __init__(self, path, listing_base=None):
        self.pe = pefile.PE(path, fast_load=False)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.lbase = listing_base if listing_base is not None else self.base
        self.image = self.pe.get_memory_mapped_image()
        self.iat = {}
        for d in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            # pefile reports names with spaces ("Low-Level Engine.dll") as invalid; read it raw.
            dll = self.pe.get_string_at_rva(d.struct.Name).decode(errors="replace").lower()
            for imp in d.imports:
                nm = imp.name.decode(errors="replace") if imp.name else "#%d" % imp.ordinal
                self.iat[imp.address - self.base] = "%s!%s" % (dll, nm)
        self.exports = collections.defaultdict(list)
        if hasattr(self.pe, "DIRECTORY_ENTRY_EXPORT"):
            for e in self.pe.DIRECTORY_ENTRY_EXPORT.symbols:
                if e.name:
                    self.exports[e.address].append(e.name.decode(errors="replace"))
        self.sections = [(sec.Name.rstrip(b"\0").decode(errors="replace"), sec.VirtualAddress,
                          sec.VirtualAddress + max(sec.Misc_VirtualSize, sec.SizeOfRawData))
                         for sec in self.pe.sections]
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

    def section(self, rva):
        for name, lo, hi in self.sections:
            if lo <= rva < hi:
                return name
        return "?"

    def rva(self, listing_va):
        return listing_va - self.lbase

    def decode(self, rva):
        for ins in self.md.disasm(self.image[rva:rva + 16], rva, 1):
            return ins
        return None


def mem_size(ins):
    for op in ins.operands:
        if op.type == cx.X86_OP_MEM:
            return op.size
    return None


def mem_disp_abs(ins):
    """Absolute displacement of a [disp32] operand (no base/index), else None."""
    for op in ins.operands:
        if op.type == cx.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
            return op.mem.disp & 0xffffffff
    return None


def _mem_key(op):
    return (op.mem.base, op.mem.index, op.mem.scale, op.mem.segment)


def fild64_kind(lst, i):
    """How the 8 bytes loaded by `fild qword [m]` at lst[i] were built.

    MSVC converts an unsigned 32-bit int to floating point by storing it with a zero high
    dword and loading it with FILD m64; that value has at most 32 significant bits and is exact
    in a double. Returns "u32-zero-extended", "sign-extended" (CDQ/SAR high word) or
    "int64/unknown" (may carry more than 53 significant bits)."""
    op = [o for o in lst[i].operands if o.type == cx.X86_OP_MEM][0]
    key, hi = _mem_key(op), op.mem.disp + 4
    pushes = []
    zero_regs = set()
    window = lst[max(0, i - 16):i]
    # registers known to be zero somewhere earlier in the function (xor r,r / mov r,0)
    for ins in lst[:i]:
        if ins.mnemonic == "xor" and len(ins.operands) == 2 and \
           ins.operands[0].type == cx.X86_OP_REG and ins.operands[1].type == cx.X86_OP_REG and \
           ins.operands[0].reg == ins.operands[1].reg:
            zero_regs.add(ins.operands[0].reg)
        elif ins.mnemonic == "mov" and len(ins.operands) == 2 and ins.operands[0].type == cx.X86_OP_REG:
            r = ins.operands[0].reg
            if ins.operands[1].type == cx.X86_OP_IMM and ins.operands[1].imm == 0:
                zero_regs.add(r)
            else:
                zero_regs.discard(r)
    for ins in reversed(window):
        if ins.mnemonic == "mov" and len(ins.operands) == 2 and ins.operands[0].type == cx.X86_OP_MEM:
            d = ins.operands[0]
            if _mem_key(d) == key and d.mem.disp == hi:
                src = ins.operands[1]
                if src.type == cx.X86_OP_IMM and src.imm == 0:
                    return "u32-zero-extended"
                if src.type == cx.X86_OP_REG and src.reg in zero_regs:
                    return "u32-zero-extended"
                return "int64/unknown"
        if ins.mnemonic == "and" and len(ins.operands) == 2 and ins.operands[0].type == cx.X86_OP_MEM and \
           ins.operands[1].type == cx.X86_OP_IMM and ins.operands[1].imm == 0:
            d = ins.operands[0]
            if _mem_key(d) == key and d.mem.disp == hi:
                return "u32-zero-extended"
        if ins.mnemonic in ("cdq",):
            return "sign-extended"
        if ins.mnemonic == "push":
            pushes.append(ins)
    # push 0 / push r32 / fild qword [esp]
    if op.mem.base == cx.X86_REG_ESP and op.mem.disp == 0 and len(pushes) >= 2:
        hi_push = pushes[1]   # pushes are collected newest first: [low, high]
        o = hi_push.operands[0]
        if (o.type == cx.X86_OP_IMM and o.imm == 0) or (o.type == cx.X86_OP_REG and o.reg in zero_regs):
            return "u32-zero-extended"
    return "int64/unknown"


def classify(ins):
    """x87 class label for an instruction, or None if it is not x87."""
    m = ins.mnemonic
    if not (m.startswith("f") or m in ("wait", "fwait")):
        return None
    sz = mem_size(ins)
    if m in ("fld", "fst", "fstp", "fild", "fist", "fistp", "fisttp", "fbld", "fbstp"):
        return "%s m%d" % (m, sz * 8) if sz else "%s st(i)" % m
    if m in ARITH or m in IARITH:
        return "%s m%d" % (m, sz * 8) if sz else "%s st" % m
    if m in COMPARE:
        return "%s m%d" % (m, sz * 8) if sz else m
    if m.startswith("fcmov"):
        return "fcmovcc"
    return m


def load_listing(listing_dir):
    """{function_va: [instruction_va, ...]} and {function_va: name} from a Ghidra export."""
    names = {}
    tsv = os.path.join(listing_dir, "functions.tsv")
    if os.path.exists(tsv):
        with open(tsv, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 2:
                    names[int(p[0], 16)] = p[1]
    funcs = {}
    fdir = os.path.join(listing_dir, "functions")
    for fn in os.listdir(fdir):
        if not fn.endswith(".asm"):
            continue
        fva = int(fn[:-4], 16)
        if names and fva not in names:
            continue  # filtered out of functions.tsv (e.g. header overlap)
        addrs = []
        with open(os.path.join(fdir, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                if len(line) > 8 and line[8] == " ":
                    try:
                        addrs.append(int(line[:8], 16))
                    except ValueError:
                        pass
        funcs[fva] = sorted(set(addrs))
    return funcs, names


def guess_cw_value(window, fldcw_ins):
    """Best-effort: find `or/and ah|ax|eax, imm` / `mov word [mem], imm` feeding the FLDCW."""
    notes = []
    for ins in window:
        t = "%s %s" % (ins.mnemonic, ins.op_str)
        if re.match(r"(or|and|xor) (ah|ax|eax|ch|cx|ecx|dh|dx|edx|bh|bx|ebx)?, ", t) or \
           re.match(r"(or|and|xor|mov) word ptr \[.*\], 0x", t) or \
           re.match(r"(or|and) (ah|ch|dh|bh), 0x", t):
            notes.append(t)
    return notes


def census(mod, funcs, names):
    counts = collections.Counter()
    by_func = collections.defaultdict(collections.Counter)
    sites = collections.defaultdict(list)       # category -> [ (func_rva, ins_rva, text) ]
    idioms = collections.Counter()
    calls = collections.Counter()
    call_sites = collections.defaultdict(list)
    thunks = {}
    by_section = collections.Counter()
    total = x87_total = 0
    decoded = {}

    # Pass 0: decode everything; find one-jump thunks to the IAT.
    for fva, addrs in funcs.items():
        lst = []
        for va in addrs:
            ins = mod.decode(mod.rva(va))
            if ins is not None:
                lst.append(ins)
        decoded[fva] = lst
        if len(lst) == 1 and lst[0].mnemonic == "jmp":
            d = mem_disp_abs(lst[0])
            if d is not None and (d - mod.base) in mod.iat:
                thunks[mod.rva(fva)] = mod.iat[d - mod.base]

    for fva, lst in decoded.items():
        frva = mod.rva(fva)
        reg_imp = {}
        fldcw_seen_at = []
        for i, ins in enumerate(lst):
            total += 1
            m = ins.mnemonic
            # import call resolution ------------------------------------------------------
            if m == "mov" and len(ins.operands) == 2 and ins.operands[0].type == cx.X86_OP_REG:
                d = mem_disp_abs(ins)
                r = ins.operands[0].reg
                if d is not None and (d - mod.base) in mod.iat:
                    reg_imp[r] = mod.iat[d - mod.base]
                else:
                    reg_imp.pop(r, None)
            target = None
            if m in ("call", "jmp") and ins.operands:
                op = ins.operands[0]
                if op.type == cx.X86_OP_MEM:
                    d = mem_disp_abs(ins)
                    if d is not None:
                        target = mod.iat.get(d - mod.base)
                elif op.type == cx.X86_OP_REG:
                    target = reg_imp.get(op.reg)
                elif op.type == cx.X86_OP_IMM:
                    target = thunks.get(op.imm & 0xffffffff)   # decoded at RVA, so imm is an RVA
            if target and m == "call" or (target and m == "jmp" and len(lst) > 1):
                if MATH_IMPORTS.search(target.lower()):
                    calls[target] += 1
                    call_sites[target].append((frva, ins.address))
            # x87 ------------------------------------------------------------------------
            c = classify(ins)
            if c is None:
                continue
            x87_total += 1
            sec = mod.section(ins.address)
            by_section[sec] += 1
            if m not in ("fist", "fistp", "fisttp"):
                counts[c] += 1
            by_func[frva][c] += 1
            text = "%s %s" % (m, ins.op_str)
            irva = ins.address          # instructions are decoded at their RVA
            if m in CONST_LOADS:
                sites["ext: FLD constant (64-bit significand)"].append((frva, irva, text))
            if m == "fild" and mem_size(ins) == 8:
                kind = fild64_kind(lst, i)
                counts["fild m64 [%s]" % kind] += 1
                if kind != "u32-zero-extended":
                    sites["ext: FILD m64 (%s)" % kind].append((frva, irva, text))
            if m == "fld" and mem_size(ins) == 10:
                sites["ext: FLD m80"].append((frva, irva, text))
            if m in ("fstp", "fst") and mem_size(ins) == 10:
                sites["FSTP m80 (spill/save)"].append((frva, irva, text))
            if m in ("fbld", "fbstp"):
                sites["ext: BCD"].append((frva, irva, text))
            if m in TRANSCENDENTAL:
                sites["ext: transcendental instruction"].append((frva, irva, text))
            if m in OTHER_SPECIAL:
                sites["special: %s" % m].append((frva, irva, text))
            if m == "fldcw":
                fldcw_seen_at.append(i)
                window = lst[max(0, i - 8):i]
                sites["FLDCW"].append((frva, irva, text + "  <= " + "; ".join(guess_cw_value(window, ins))))
            if m in ("fnstcw", "fstcw"):
                sites["FNSTCW"].append((frva, irva, text))
            if m in ("fist", "fistp", "fisttp"):
                prev = [j for j in fldcw_seen_at if i - j <= 12]
                cat = "FIST* after FLDCW in same function (RC switch)" if prev else "FIST* under ambient RC"
                sites[cat].append((frva, irva, text))
                counts["%s m%d [%s]" % (m, mem_size(ins) * 8, "rc-switch" if prev else "ambient")] += 1
            if m in ("fnstsw", "fstsw"):
                nxt = [lst[j] for j in range(i + 1, min(i + 4, len(lst)))]
                pat = "fnstsw " + ins.op_str
                for n in nxt:
                    t = "%s %s" % (n.mnemonic, n.op_str)
                    if n.mnemonic == "sahf":
                        pat += " ; sahf"
                        break
                    if n.mnemonic in ("test", "and", "cmp", "xor") and ("ah" in n.op_str or "ax" in n.op_str):
                        pat += " ; " + re.sub(r"0x[0-9a-f]+", "imm", t)
                        if n.mnemonic == "test":
                            break
                    elif n.mnemonic.startswith("j"):
                        break
                idioms[pat] += 1
            if m.startswith("fcmov") or m in ("fcomi", "fcomip", "fucomi", "fucomip", "fcompi", "fucompi"):
                sites["P6 x87 (fcomi/fcmov)"].append((frva, irva, text))
    # tag every site with its section (game .text vs e.g. the DRM residue .cms_t)
    for cat in list(sites):
        sites[cat] = [(f, a, t + ("" if mod.section(a).startswith(".text") else "  [%s]" % mod.section(a)))
                      for f, a, t in sites[cat]]
    return dict(total=total, x87_total=x87_total, counts=counts, by_func=by_func, sites=sites, by_section=by_section,
                idioms=idioms, calls=calls, call_sites=call_sites, thunks=thunks)


def func_name(mod, names, frva):
    va = frva + mod.lbase
    ex = mod.exports.get(frva)
    if ex:
        return demangle(ex[0]) + (" (+%d aliases)" % (len(ex) - 1) if len(ex) > 1 else "")
    return names.get(va, "FUN_%08x" % va)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pe", required=True)
    ap.add_argument("--listing", required=True, help="Ghidra export dir with functions/ and functions.tsv")
    ap.add_argument("--listing-base", type=lambda s: int(s, 0))
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-sites", type=int, default=400)
    a = ap.parse_args()

    mod = Module(a.pe, a.listing_base)
    funcs, names = load_listing(a.listing)
    r = census(mod, funcs, names)
    name = os.path.basename(a.pe)
    os.makedirs(a.out, exist_ok=True)
    text_size = sum(s.Misc_VirtualSize for s in mod.pe.sections if s.Characteristics & 0x20000000
                    or s.Name.startswith(b".text"))

    js = {
        "module": name, "image_base": hex(mod.base), "functions": len(funcs),
        "instructions": r["total"], "x87_instructions": r["x87_total"],
        "x87_functions": len(r["by_func"]),
        "x87_by_section": dict(r["by_section"]),
        "counts": dict(r["counts"].most_common()),
        "compare_idioms": dict(r["idioms"].most_common()),
        "math_calls": dict(r["calls"].most_common()),
        "math_call_sites": {k: [("%x" % f, "%x" % s) for f, s in v] for k, v in r["call_sites"].items()},
        "sites": {k: [{"func": "%x" % f, "func_name": func_name(mod, names, f), "rva": "%x" % s, "ins": t}
                      for f, s, t in v] for k, v in r["sites"].items()},
        "thunks": {"%x" % k: v for k, v in r["thunks"].items()},
    }
    with open(os.path.join(a.out, name + ".json"), "w") as f:
        json.dump(js, f, indent=1)

    L = ["# x87 census: %s" % name, "",
         "functions %d, instructions %d (text %d KB), x87 instructions %d in %d functions" % (
             len(funcs), r["total"], text_size // 1024, r["x87_total"], len(r["by_func"])), "",
         "x87 instructions by section: " + ", ".join("%s %d" % kv for kv in r["by_section"].most_common()), "",
         "## Instruction classes", "", "| class | count |", "|---|---|"]
    for k, v in sorted(r["counts"].items(), key=lambda kv: -kv[1]):
        L.append("| `%s` | %d |" % (k, v))
    L += ["", "## FNSTSW idioms", "", "| idiom | count |", "|---|---|"]
    for k, v in r["idioms"].most_common():
        L.append("| `%s` | %d |" % (k, v))
    L += ["", "## Maths import call sites", "", "| import | sites |", "|---|---|"]
    for k, v in r["calls"].most_common():
        L.append("| `%s` | %d |" % (k, v))
    for cat in sorted(r["sites"]):
        v = r["sites"][cat]
        fs = collections.Counter(f for f, _s, _t in v)
        L += ["", "## %s: %d sites in %d functions" % (cat, len(v), len(fs)), ""]
        for f, s, t in v[:a.max_sites]:
            L.append("- `+0x%x` in `%s` (`+0x%x`): `%s`" % (s, func_name(mod, names, f), f, t))
        if len(v) > a.max_sites:
            L.append("- ... %d more" % (len(v) - a.max_sites))
    with open(os.path.join(a.out, name + ".md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("%s: %d funcs, %d ins, %d x87 in %d funcs -> %s" % (
        name, len(funcs), r["total"], r["x87_total"], len(r["by_func"]), a.out))


if __name__ == "__main__":
    sys.exit(main())
