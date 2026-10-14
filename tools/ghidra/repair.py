"""Repair Ghidra disassembly conflicts (PyGhidra; called by ghidra_run.py after analysis).

Ghidra sometimes lays down a code unit at an address that a later, correct flow needs:
  - pointer data at relocation sites inside not-yet-disassembled code (the DLLs'
    "Relocation Table Guide" sees two relocated dwords of one MOV [abs], imm32 as a table);
  - pointers or instructions at targets of DRM references (.cms_t/.cms_d name addresses in
    .text) or of numeric constants that happen to be code addresses (EXE, no relocations).
The flow then stops with an Error bookmark "Failed to disassemble at A due to conflicting
(instruction|data) at B (flow from C)", and the rest of the function is lost.

For each such bookmark: if the code unit at B is not a function entry and nothing but data
references point at it, clear it (data) or its run of instructions outside every function
body, disassemble at A, and re-derive the body of the function containing C. Anything
else is left alone and reported.

One instruction can hold two relocated dwords (MOV [abs], imm32), and the address-table
analyzer makes both into pointers; clearing only B then leaves the instruction blocked by
the second, and Ghidra fails again without a new bookmark (Default.dll+0x6caa, a guarded
static initializer). So every data unit the instruction at A would overlap is cleared, on
the same conditions, and the straight-line flow from A is followed past further such
pointers until it joins existing code.
"""
import re

BAD = re.compile(r"Failed to disassemble at ([0-9a-f]+) due to conflicting (instruction|data) at "
                 r"([0-9a-f]+) \(flow from ([0-9a-f]+)\)")


def clear_for_instruction(program, addr):
    """Clear the data units the instruction that decodes at `addr` would overlap, if
    each is plain data: no function entry, no flow reference to it. False when no
    instruction decodes there or a unit may not be cleared."""
    from ghidra.app.util import PseudoDisassembler
    listing = program.getListing()
    fm = program.getFunctionManager()
    rm = program.getReferenceManager()
    try:
        pi = PseudoDisassembler(program).disassemble(addr)
    except Exception:  # noqa: BLE001 - undecodable bytes
        return False
    if pi is None:
        return False
    end = addr.add(pi.getLength() - 1)
    units = []
    it = listing.getDefinedData(program.getAddressFactory().getAddressSet(addr, end), True)
    while it.hasNext():
        units.append(it.next())
    first = listing.getDefinedDataContaining(addr)
    if first is not None and first not in units:
        units.append(first)
    if listing.getInstructionContaining(addr) is not None or any(
            listing.getInstructionContaining(addr.add(k)) is not None for k in range(pi.getLength())):
        return False
    for d in units:
        start = d.getMinAddress()
        if fm.getFunctionAt(start) is not None or any(
                r.getReferenceType().isFlow() for r in rm.getReferencesTo(start)):
            return False
    for d in units:
        listing.clearCodeUnits(d.getMinAddress(), d.getMaxAddress(), False)
    return True


def disassemble_through(program, addr, limit=64):
    """Disassemble at `addr` and along its fall-through, clearing plain data that blocks
    an instruction (clear_for_instruction), until the flow ends or joins existing code."""
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.util.task import TaskMonitor
    listing = program.getListing()
    fm = program.getFunctionManager()
    at = addr
    for _ in range(limit):
        ins = listing.getInstructionAt(at)
        if ins is None:
            if not clear_for_instruction(program, at):
                return
            DisassembleCommand(at, None, True).applyTo(program, TaskMonitor.DUMMY)
            ins = listing.getInstructionAt(at)
            if ins is None:
                return
        elif at != addr:
            return                      # joined code that was already there
        ft = ins.getFallThrough()
        if ft is None or fm.getFunctionAt(ft) is not None:
            return
        at = ft


def repair_conflicts(program):
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.util.task import TaskMonitor

    space = program.getAddressFactory().getDefaultAddressSpace()
    listing = program.getListing()
    fm = program.getFunctionManager()
    rm = program.getReferenceManager()
    bm = program.getBookmarkManager()
    done, skipped = [], []
    tx = program.startTransaction("funcdb repair")
    ok = False
    try:
        marks = []
        it = bm.getBookmarksIterator("Error")
        while it.hasNext():
            b = it.next()
            m = BAD.search(str(b.getComment()))
            if m:
                marks.append((b, [int(x, 16) for x in (m.group(1), m.group(3), m.group(4))], m.group(2)))
        for b, (a, c_at, frm), what in marks:
            A, B, C = (space.getAddress(x) for x in (a, c_at, frm))
            if fm.getFunctionAt(B) is not None:
                skipped.append([hex(a), "conflict is a function entry"])
                continue
            flow_refs = [r for r in rm.getReferencesTo(B) if r.getReferenceType().isFlow()]
            if flow_refs:
                skipped.append([hex(a), "conflicting unit is a flow target"])
                continue
            if what == "data":
                d = listing.getDataContaining(B)
                if d is None:
                    skipped.append([hex(a), "no data at conflict"])
                    continue
                listing.clearCodeUnits(d.getMinAddress(), d.getMaxAddress(), False)
            else:
                ins = listing.getInstructionContaining(B)
                if ins is None or fm.getFunctionContaining(ins.getAddress()) is not None:
                    skipped.append([hex(a), "conflicting instruction inside a function"])
                    continue
                start = ins.getAddress()
                end = ins.getMaxAddress()
                nxt = ins
                while True:
                    ft = nxt.getFallThrough()
                    if ft is None:
                        break
                    n2 = listing.getInstructionAt(ft)
                    if n2 is None or fm.getFunctionContaining(ft) is not None:
                        break
                    nxt = n2
                    end = nxt.getMaxAddress()
                listing.clearCodeUnits(start, end, False)
            DisassembleCommand(A, None, True).applyTo(program, TaskMonitor.DUMMY)
            disassemble_through(program, A)
            f = fm.getFunctionContaining(C)
            if f is not None:
                CreateFunctionCmd.fixupFunctionBody(program, f, TaskMonitor.DUMMY)
            bm.removeBookmark(b)
            done.append([hex(a), what, hex(c_at), hex(f.getEntryPoint().getOffset()) if f else None])
        ok = True
    finally:
        program.endTransaction(tx, ok)
    return {"repaired": done, "skipped": skipped}
