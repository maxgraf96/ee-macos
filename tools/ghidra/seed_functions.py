"""Create functions at evidence-backed starts that Ghidra's analysis missed (PyGhidra).

Input: the seeds list build_db.py writes (work/seeds-<slug>.json): each entry has the
address, the evidence kinds (vtable slot, EH table, data pointer, immediate, ...) and why
it was accepted. Every seed is disassembled, made a function, and auto-analysis is then
allowed to follow the new code (calls, switch tables, further references): the driver
re-runs full auto-analysis after seeding (incremental analysis needs the GUI's bundle host).

Seeds that land inside an existing function body are skipped (they are alternate entries
and are recorded as such in the DB, not split out).
"""


def seed(program, seeds, accept=("strong", "orphan_code", "validated")):
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.program.model.address import AddressSet
    from ghidra.util.task import TaskMonitor

    space = program.getAddressFactory().getDefaultAddressSpace()
    fm = program.getFunctionManager()
    listing = program.getListing()
    created, skipped_inside, failed, existed = [], 0, [], 0
    switch_fixes, fixed = [], []
    tx = program.startTransaction("funcdb seed")
    ok = False
    try:
        for s in sorted(seeds, key=lambda s: int(s["va"], 16)):
            if s["why"] == "switch_case":
                switch_fixes.append(s)
                continue
            if s["why"] not in accept:
                continue
            a = space.getAddress(int(s["va"], 16))
            if fm.getFunctionAt(a) is not None:
                existed += 1
                continue
            if fm.getFunctionContaining(a) is not None:
                skipped_inside += 1
                continue
            if listing.getInstructionAt(a) is None:
                if listing.getDefinedDataContaining(a) is not None:
                    failed.append([s["va"], "defined data"])
                    continue
                DisassembleCommand(a, None, True).applyTo(program, TaskMonitor.DUMMY)
                if listing.getInstructionAt(a) is None and _clear_orphan_conflicts(program, a):
                    DisassembleCommand(a, None, True).applyTo(program, TaskMonitor.DUMMY)
                if listing.getInstructionAt(a) is None:
                    failed.append([s["va"], "disassembly failed"])
                    continue
            cmd = CreateFunctionCmd(a)
            if cmd.applyTo(program, TaskMonitor.DUMMY) and fm.getFunctionAt(a) is not None:
                created.append(s["va"])
            else:
                failed.append([s["va"], str(cmd.getStatusMsg())])
        # under-resolved jump tables: add the missing case flows and re-derive the body
        from ghidra.program.model.symbol import RefType, SourceType
        rm = program.getReferenceManager()
        for s in switch_fixes:
            jmp = space.getAddress(int(s["va"], 16))
            for t in s["targets"]:
                ta = space.getAddress(int(t, 16))
                rm.addMemoryReference(jmp, ta, RefType.COMPUTED_JUMP, SourceType.ANALYSIS, 0)
                if listing.getInstructionAt(ta) is None:
                    DisassembleCommand(ta, None, True).applyTo(program, TaskMonitor.DUMMY)
            f = fm.getFunctionContaining(jmp)
            if f is not None:
                CreateFunctionCmd.fixupFunctionBody(program, f, TaskMonitor.DUMMY)
            fixed.append(s["va"])
        ok = True
    finally:
        program.endTransaction(tx, ok)
    return {"created": len(created) + len(fixed), "existed": existed, "skipped_inside": skipped_inside,
            "switch_fixed": fixed, "failed": len(failed), "failed_list": failed[:200],
            "created_list": created}


def _clear_orphan_conflicts(program, a):
    """Clear instructions overlapping [a, a+15] that start elsewhere, belong to no function
    and are no flow target (e.g. Ghidra decoded 0x401001 because DRM code names that
    address, which blocks the real function at 0x401000). Returns True if any cleared."""
    listing = program.getListing()
    fm = program.getFunctionManager()
    rm = program.getReferenceManager()
    cleared = False
    for k in range(0, 16):
        x = a.add(k)
        ins = listing.getInstructionContaining(x)
        if ins is None or ins.getAddress().equals(a) or fm.getFunctionContaining(ins.getAddress()):
            continue
        if any(r.getReferenceType().isFlow() for r in rm.getReferencesTo(ins.getAddress())):
            continue
        start, end, cur = ins.getAddress(), ins.getMaxAddress(), ins
        while cur.getFallThrough() is not None:
            nxt = listing.getInstructionAt(cur.getFallThrough())
            if nxt is None or fm.getFunctionContaining(nxt.getAddress()) is not None:
                break
            cur = nxt
            end = cur.getMaxAddress()
        listing.clearCodeUnits(start, end, False)
        cleared = True
    return cleared
