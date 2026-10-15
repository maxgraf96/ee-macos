"""Export recomp-kit translator input from the Ghidra project, with the function DB as the
authority on what is a function (PyGhidra; called by 'ghidra_run.py kit').

Writes, under OUTDIR (keep it in an ignored directory: listings are derived from game code):

  functions.tsv           address, name, bytes, status, message  (the kit's index format,
                          kit/tools/ExportProgram.java)
  functions/<addr>.asm    one line per instruction, "%08x  <Ghidra instruction text>",
                          exactly what kit/tools/recomp/translate.py LINE_RE parses
  funcdb.json             what the kit needs from the DB beyond the listings (see
                          docs/reference/function-db.md, "Kit integration"):
                          code ranges, entry points with evidence, data-in-code ranges,
                          jump tables, excluded (DRM) functions, alternate entries

Which DB functions are exported: every function whose tag is not "drm". Import thunks,
EH stubs, unwind funclets, catch handlers and their continuations are all exported: the
kit needs each as an entry (the runtime's MSVC EH implementation calls the funclets).

This is the development path (`tools/setup.py --ghidra`). The default inputs come from
tools/inputs/export.py, which writes the same files from the DB and the binaries with no
Ghidra: functions.tsv and funcdb.json are made by the same code (tools/inputs/sidecar.py),
and `export.py --compare DIR` checks its listings against an export from here.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "inputs"))
import sidecar  # noqa: E402


def export(program, slug, db_path, out, decompile=False):
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.util.task import TaskMonitor

    db = json.loads(db_path.read_text())
    space = program.getAddressFactory().getDefaultAddressSpace()
    fm = program.getFunctionManager()
    listing = program.getListing()
    (out / "functions").mkdir(parents=True, exist_ok=True)
    decomp = None
    if decompile:
        from ghidra.app.decompiler import DecompInterface
        decomp = DecompInterface()
        decomp.openProgram(program)

    keep = sidecar.translated(db)
    stats = {"exported": 0, "skipped_drm": len(db["functions"]) - len(keep), "created_here": 0,
             "missing": []}
    rows = []
    tx = program.startTransaction("kit export (not saved)")
    try:
        for r in keep:
            va = int(r["va"], 16)
            a = space.getAddress(va)
            f = fm.getFunctionAt(a)
            if f is None:
                # the DB is authoritative: make the function if this project lacks it
                if listing.getInstructionAt(a) is None:
                    DisassembleCommand(a, None, True).applyTo(program, TaskMonitor.DUMMY)
                CreateFunctionCmd(a).applyTo(program, TaskMonitor.DUMMY)
                f = fm.getFunctionAt(a)
                if f is None:
                    stats["missing"].append(r["va"])
                    continue
                stats["created_here"] += 1
            lines = []
            it = listing.getInstructions(f.getBody(), True)
            while it.hasNext():
                ins = it.next()
                lines.append("%s  %s" % (ins.getAddress(), ins.toString()))
            (out / "functions" / ("%08x.asm" % va)).write_text("\n".join(lines) + "\n")
            if decomp is not None:
                res = decomp.decompileFunction(f, 45, TaskMonitor.DUMMY)
                if res.decompileCompleted():
                    (out / "functions" / ("%08x.c" % va)).write_text(
                        "/* analysis pseudocode, not buildable source */\n" + res.getDecompiledFunction().getC())
            # the DB's best name names the translated function in the kit's symbols.json
            rows.append((va, sidecar.listing_name(r), int(f.getBody().getNumAddresses())))
            stats["exported"] += 1
    finally:
        program.endTransaction(tx, False)       # never persist: the saved project is the record
        if decomp is not None:
            decomp.dispose()
    (out / "functions.tsv").write_text(sidecar.functions_tsv(rows))
    (out / "funcdb.json").write_text(sidecar.funcdb_text(db))
    stats["missing"] = stats["missing"][:50]
    return stats
