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
"""
import re

BAD = re.compile(r"Failed to disassemble at ([0-9a-f]+) due to conflicting (instruction|data) at "
                 r"([0-9a-f]+) \(flow from ([0-9a-f]+)\)")


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
            f = fm.getFunctionContaining(C)
            if f is not None:
                CreateFunctionCmd.fixupFunctionBody(program, f, TaskMonitor.DUMMY)
            bm.removeBookmark(b)
            done.append([hex(a), what, hex(c_at), hex(f.getEntryPoint().getOffset()) if f else None])
        ok = True
    finally:
        program.endTransaction(tx, ok)
    return {"repaired": done, "skipped": skipped}
