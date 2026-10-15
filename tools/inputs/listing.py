"""Render 32-bit x86 instructions as the kit's listing lines, from the bytes.

The kit's translator reads one `%08x  <instruction>` line per instruction
(kit/tools/recomp/translate.py LINE_RE) in the syntax of Ghidra's x86 SLEIGH
display, and copies each line into the generated C as a comment. This module
produces that text from a capstone decoding, so the listings can be made on a
machine without Ghidra (docs/reference/build.md §2). The goal is the same text
Ghidra prints, character for character, for every instruction of the
canonical modules: tools/inputs/export.py --compare checks it against a Ghidra
export.

Ghidra's display differs from capstone's in ways that are encoding-dependent,
so the renderer reads the encoding (opcode, ModRM, displacement and immediate
widths), not only capstone's operand list:

  - an 8-bit immediate sign-extended by an arithmetic opcode (83 /0 /2 /3 /5 /7,
    6A, 6B) prints signed (`ADD EAX,-0x8`); by a logical one (83 /1 /4 /6)
    unsigned at the operand width (`OR EDX,0xffffffff`); IMUL's full-width
    immediate (69) signed (`IMUL EAX,EAX,-0x1000`); every other immediate
    unsigned at its width;
  - an 8-bit displacement prints signed (`[EBP + -0x8]`) and is left out when
    zero (`[EBP]`); a 32-bit one prints unsigned (`[EBP + 0xffffff10]`), zero
    included (`[EDI*0x8 + 0x0]`);
  - an absolute address prints as an 8-digit address (`[0x00654321]`), except
    through a segment override (`FS:[0x0]`); the accumulator forms A0-A3 carry
    no size (`MOV EAX,[0x12345678]`), nor does LEA;
  - branch targets are 8-digit addresses; condition codes are Ghidra's (JC,
    JNC, JZ, JNZ); REP prefixes are a mnemonic suffix (`MOVSD.REP ES:EDI,ESI`);
  - x87 register forms follow Ghidra's constructors (FPU_FORMS below), and a
    memory operand of a floating-point x87 instruction is `float`/`double`
    rather than `dword`/`qword`.

Anything outside the vocabulary below raises ListingError rather than
guessing: the canonical modules are hash-pinned, so the vocabulary is fixed
and a new form is a change to review, not to paper over.
"""
import capstone
import capstone.x86_const as X


class ListingError(Exception):
    pass


#: capstone condition-code spellings -> Ghidra's
CONDITIONS = {"b": "C", "ae": "NC", "e": "Z", "ne": "NZ", "a": "A", "be": "BE", "g": "G",
              "ge": "GE", "l": "L", "le": "LE", "s": "S", "ns": "NS", "o": "O", "no": "NO",
              "p": "P", "np": "NP"}

REP_SUFFIX = {"rep": "REP", "repe": "REPE", "repz": "REPE", "repne": "REPNE", "repnz": "REPNE"}

#: String instructions: Ghidra prints their implicit operands.
STRING_OPERANDS = {"MOVS": "ES:EDI,ESI", "CMPS": "ES:EDI,ESI", "STOS": "ES:EDI",
                   "SCAS": "ES:EDI", "LODS": "ESI"}

#: Instructions printed bare: Ghidra shows none of their (implicit) operands.
IMPLICIT = frozenset(("CDQ", "CWDE", "CBW", "CWD", "LEAVE", "SAHF", "LAHF", "PUSHAD", "POPAD",
                      "PUSHFD", "POPFD", "RDTSC", "NOP", "INT3", "HLT", "CLD", "STD", "CLC",
                      "STC", "CMC", "FCHS", "FABS", "FLD1", "FLDZ", "FLDPI", "FLDL2E",
                      "FLDL2T", "FLDLG2", "FLDLN2", "FSIN", "FCOS", "FSQRT", "FRNDINT",
                      "FSCALE", "FPREM", "FPATAN", "FPTAN", "F2XM1", "FYL2X", "FTST", "FXAM",
                      "FCOMPP", "FUCOMPP", "FNINIT", "FNCLEX", "WAIT"))

#: x87 register forms, by the first opcode byte and the ModRM row (reg field):
#: (mnemonic, operands) where operands spells STi as "i" and Ghidra prints a
#: bare mnemonic for the ST1 form of some ("1": no operand when i == 1).
FPU_FORMS = {
    (0xd8, 0): ("FADD", "ST0,i"), (0xd8, 1): ("FMUL", "i"), (0xd8, 2): ("FCOM", "1"),
    (0xd8, 3): ("FCOMP", "1"), (0xd8, 4): ("FSUB", "ST0,i"), (0xd8, 5): ("FSUBR", "ST0,i"),
    (0xd8, 6): ("FDIV", "ST0,i"), (0xd8, 7): ("FDIVR", "ST0,i"),
    (0xd9, 0): ("FLD", "i"), (0xd9, 1): ("FXCH", "1"),
    (0xdc, 0): ("FADD", "i,ST0"), (0xdc, 1): ("FMUL", "i,ST0"), (0xdc, 4): ("FSUBR", "i,ST0"),
    (0xdc, 5): ("FSUB", "i,ST0"), (0xdc, 6): ("FDIVR", "i,ST0"), (0xdc, 7): ("FDIV", "i,ST0"),
    (0xdd, 2): ("FST", "i"), (0xdd, 3): ("FSTP", "i"), (0xdd, 4): ("FUCOM", "1"),
    (0xdd, 5): ("FUCOMP", "1"),
    (0xde, 0): ("FADDP", "1,ST0"), (0xde, 1): ("FMULP", "1,ST0"), (0xde, 4): ("FSUBRP", "1,ST0"),
    (0xde, 5): ("FSUBP", "1,ST0"), (0xde, 6): ("FDIVRP", "1,ST0"), (0xde, 7): ("FDIVP", "1,ST0"),
}

#: x87 instructions whose memory operand is a float (`float`/`double ptr`)
#: rather than an integer or a control/status word.
FPU_FLOAT_MEM = frozenset(("FLD", "FST", "FSTP", "FADD", "FSUB", "FSUBR", "FMUL", "FDIV",
                           "FDIVR", "FCOM", "FCOMP"))

INT_WIDTH = {1: "byte", 2: "word", 4: "dword", 8: "qword", 6: "fword"}
FLOAT_WIDTH = {4: "float", 8: "double", 10: "tword"}

#: 83 /r: the arithmetic rows sign-extend and print signed, the logical ones unsigned
SIGNED_83 = frozenset((0, 2, 3, 5, 7))

BRANCH_OPCODES = frozenset((0xe8, 0xe9, 0xeb, 0xe0, 0xe1, 0xe2, 0xe3)) | frozenset(range(0x70, 0x80))


def _hex(v):
    return "-0x%x" % -v if v < 0 else "0x%x" % v


def _sx8(v):
    v &= 0xff
    return v - 0x100 if v & 0x80 else v


class Renderer(object):
    """Decode and render instructions of one image (bytes at `base`)."""

    def __init__(self, data, base):
        self.data = data
        self.base = base
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True

    def decode(self, va):
        """(length, text) of the instruction at `va`."""
        o = va - self.base
        ci = next(self.md.disasm(self.data[o:o + 16], va, count=1), None)
        if ci is None:
            raise ListingError("%08x: undecodable bytes %s" % (va, self.data[o:o + 8].hex()))
        try:
            return ci.size, self.render(ci)
        except ListingError as e:
            raise ListingError("%08x %s %s (%s): %s" % (va, ci.mnemonic, ci.op_str, ci.bytes.hex(), e))

    def lines(self, ranges):
        """Listing lines for the instructions that fill `ranges` ([lo, hi) at
        this image's addresses), decoded front to back. An instruction may not
        run past its range: the DB's ranges are instruction boundaries."""
        out = []
        for lo, hi in sorted(ranges):
            va = lo
            while va < hi:
                n, text = self.decode(va)
                if va + n > hi:
                    raise ListingError("%08x: instruction runs past the end of its range %08x-%08x"
                                       % (va, lo, hi))
                out.append("%08x  %s" % (va, text))
                va += n
        return out

    # ------------------------------------------------------------------ render --

    def render(self, ci):
        words = ci.mnemonic.split()
        suffix = ""
        if len(words) > 1:
            if words[0] not in REP_SUFFIX:
                raise ListingError("prefix %r" % words[0])
            suffix = "." + REP_SUFFIX[words[0]]
            words = words[1:]
        mnem = words[0].upper()
        op0 = ci.opcode[0]
        # x87 register forms: Ghidra's constructors, from the encoding
        if 0xd8 <= op0 <= 0xdf and ci.modrm >> 6 == 3:
            return self._fpu_register_form(ci, mnem)
        mnem = self._mnemonic(mnem)
        base = mnem.rstrip("BWD") if mnem[:4] in ("MOVS", "CMPS", "STOS", "SCAS", "LODS") else None
        if base in STRING_OPERANDS and len(mnem) == len(base) + 1:
            return "%s%s %s" % (mnem, suffix, STRING_OPERANDS[base])
        if suffix:
            raise ListingError("REP prefix on %s" % mnem)
        if mnem in IMPLICIT:
            return mnem
        ops = [self._operand(ci, mnem, op) for op in ci.operands]
        return mnem + (" " + ",".join(ops) if ops else "")

    def _mnemonic(self, mnem):
        for stem in ("J", "SET", "CMOV"):
            cc = mnem[len(stem):].lower()
            if mnem.startswith(stem) and cc in CONDITIONS:
                return stem + CONDITIONS[cc]
        return mnem

    def _fpu_register_form(self, ci, mnem):
        op0, modrm = ci.opcode[0], ci.modrm
        if (op0, modrm) == (0xde, 0xd9):
            return "FCOMPP"
        if (op0, modrm) == (0xda, 0xe9):
            return "FUCOMPP"
        if (op0, modrm) == (0xdf, 0xe0):
            return "FNSTSW AX"
        if op0 == 0xd9 and modrm >= 0xd0:
            # D9 D0 FNOP, D9 E0.. FCHS, FABS, FLD1, FLDZ, FSIN, FCOS, ...
            if mnem not in IMPLICIT:
                raise ListingError("x87 form %02x %02x" % (op0, modrm))
            return mnem
        form = FPU_FORMS.get((op0, (modrm >> 3) & 7))
        if form is None:
            raise ListingError("x87 register form %02x %02x" % (op0, modrm))
        name, shape = form
        i = modrm & 7
        if shape.startswith("1"):
            if i == 1:
                return name
            shape = "i" + shape[1:]
        return "%s %s" % (name, ",".join("ST%d" % i if t == "i" else t for t in shape.split(",")))

    def _operand(self, ci, mnem, op):
        if op.type == X.X86_OP_REG:
            name = ci.reg_name(op.reg).upper()
            if name.startswith("ST("):
                return "ST" + name[3:-1]
            return name
        if op.type == X.X86_OP_IMM:
            return self._immediate(ci, op)
        if op.type == X.X86_OP_MEM:
            return self._memory(ci, mnem, op)
        raise ListingError("operand type %d" % op.type)

    def _immediate(self, ci, op):
        op0 = ci.opcode[0]
        v = op.imm
        if op0 in BRANCH_OPCODES or (op0 == 0x0f and 0x80 <= ci.opcode[1] < 0x90):
            return "0x%08x" % (v & 0xffffffff)
        if op0 in (0x6a, 0x6b) or (op0 == 0x83 and ((ci.modrm >> 3) & 7) in SIGNED_83):
            return _hex(_sx8(v))
        if op0 == 0x69:                 # IMUL r, r/m, imm: a signed immediate at full width
            bits = 8 * ci.operands[0].size
            v &= (1 << bits) - 1
            return _hex(v - (1 << bits) if v >> (bits - 1) else v)
        if op0 in (0xd0, 0xd1, 0xd2, 0xd3, 0xc0, 0xc1, 0xc2, 0xca, 0xcd):
            return "0x%x" % (v & (0xffff if op0 in (0xc2, 0xca) else 0xff))
        # unsigned at the operand width (an 83 /1 /4 /6 immediate: sign-extended first)
        width = ci.operands[0].size if ci.operands[0].type != X.X86_OP_IMM else op.size
        if op0 == 0x83:
            v = _sx8(v)
        return "0x%x" % (v & ((1 << (8 * width)) - 1))

    def _memory(self, ci, mnem, op):
        m = op.mem
        seg = ci.reg_name(m.segment).upper() + ":" if m.segment else ""
        parts = []
        if m.base:
            parts.append(ci.reg_name(m.base).upper())
        if m.index:
            parts.append("%s*0x%x" % (ci.reg_name(m.index).upper(), m.scale))
        moffs = ci.opcode[0] in (0xa0, 0xa1, 0xa2, 0xa3)
        if not parts:
            parts.append(("0x%x" if seg else "0x%08x") % (m.disp & 0xffffffff))
        elif moffs:
            raise ListingError("moffs with a register")
        elif ci.disp_size == 1 and ci.disp_offset:
            if m.disp:
                parts.append(_hex(m.disp))
        elif ci.disp_size == 4 and ci.disp_offset:
            parts.append("0x%x" % (m.disp & 0xffffffff))
        elif m.disp:
            raise ListingError("displacement %#x without an encoding" % m.disp)
        inner = "%s[%s]" % (seg, " + ".join(parts))
        if mnem == "LEA" or moffs:
            return inner
        if mnem in FPU_FLOAT_MEM:
            word = FLOAT_WIDTH.get(op.size)
        else:
            word = INT_WIDTH.get(op.size)
        if word is None:
            raise ListingError("memory operand width %d" % op.size)
        return "%s ptr %s" % (word, inner)
