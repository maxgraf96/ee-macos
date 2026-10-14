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
"""
import json


def export(program, slug, db_path, out, decompile=False):
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.util.task import TaskMonitor

    db = json.loads(db_path.read_text())
    base = int(db["module"]["image_base"], 16)
    space = program.getAddressFactory().getDefaultAddressSpace()
    fm = program.getFunctionManager()
    listing = program.getListing()
    (out / "functions").mkdir(parents=True, exist_ok=True)
    decomp = None
    if decompile:
        from ghidra.app.decompiler import DecompInterface
        decomp = DecompInterface()
        decomp.openProgram(program)

    stats = {"exported": 0, "skipped_drm": 0, "created_here": 0, "missing": []}
    rows = []
    tx = program.startTransaction("kit export (not saved)")
    try:
        for r in db["functions"]:
            if r["tag"] == "drm":
                stats["skipped_drm"] += 1
                continue
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
            name = "fn_%08x" % va
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
            rows.append("%08x\t%s\t%d\tlisting\t" % (va, name, int(f.getBody().getNumAddresses())))
            stats["exported"] += 1
    finally:
        program.endTransaction(tx, False)       # never persist: the saved project is the record
        if decomp is not None:
            decomp.dispose()
    (out / "functions.tsv").write_text("address\tname\tbytes\tstatus\tmessage\n" + "\n".join(rows) + "\n")

    code = [s for s in db["module"]["sections"] if s["code"]]
    sidecar = {
        "schema": "neoee-funcdb-kit/1",
        "module": db["module"]["name"], "sha256": db["module"]["sha256"], "image_base": hex(base),
        # replaces section-flag code classification (EXE sections are all 0xc0000040)
        "code_ranges": [[hex(base + int(s["rva"], 16)), hex(base + int(s["rva"], 16) + int(s["vsize"], 16)),
                         s["name"]] for s in code],
        # never translate or scan: DRM residue and the SizeOfHeaders/.text overlap
        "excluded_ranges": ([[hex(base + int(s["rva"], 16)), hex(base + int(s["rva"], 16) + int(s["vsize"], 16)),
                              s["name"]] for s in db["module"]["sections"] if s["name"] in (".cms_t", ".cms_d")]
                            + ([[hex(base), hex(base + int(db["module"]["size_of_headers"], 16)), "headers"]])),
        # every function start the DB knows, with the evidence that makes it reachable
        "entry_points": [[r["va"], r["kind"], sorted((r.get("xref") or {}).keys())]
                         for r in db["functions"] if r["tag"] != "drm"],
        # interior addresses that data or EH tables name (alternate entries)
        "alternate_entries": [[r["va"], r["alt_entries"]] for r in db["functions"]
                              if r.get("alt_entries") and r["tag"] != "drm"],
        # bytes inside code sections that are data (jump/index tables, constants)
        "data_in_code": [[hex(base + int(d[0], 16)), d[1], d[2]] for d in db["data_in_code"]],
        "jump_tables": [[r["va"], hex(base + int(t[0], 16)), t[1], t[2]]
                        for r in db["functions"] for t in r.get("switch", ())],
        "excluded_functions": [r["va"] for r in db["functions"] if r["tag"] == "drm"],
    }
    (out / "funcdb.json").write_text(json.dumps(sidecar, indent=0) + "\n")
    stats["missing"] = stats["missing"][:50]
    return stats
