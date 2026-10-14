"""Pre-analysis fixes applied to a freshly imported program (PyGhidra, called by ghidra_run.py).

Empire Earth.exe (GOG rebuild) needs two fixes before auto-analysis:

1. Every section is flagged 0xc0000040 (initialised data, R/W, no execute). Ghidra's
   disassembly and reference analyzers respect the execute flag, so .text (and .cms_t,
   for the DRM reachability study) are marked executable in the program. The file on
   disk is not modified.
2. SizeOfHeaders = 0x1000, but .text's raw data starts at file offset 0x600. Ghidra's
   "Headers" block therefore holds a second copy of .text's first 0xA00 bytes at
   0x400600-0x400fff, and the spike's analysis created phantom functions there. The
   block is cut back to the bytes before the first section's raw data.

Every module gets the same analyzer options (Ghidra defaults plus the ones set below);
they are recorded in analyze.json.
"""
import common

# Analyzer options that differ from Ghidra 12.1.4 defaults.
OPTIONS = {
    # Off by default; we find function starts ourselves with evidence (build_db.py + seed).
    "Aggressive Instruction Finder": False,
    # Keep FID on (vsOlder_x86 covers some MSVC6 CRT objects); never force FID labels.
    "Function ID": True,
    "Function ID.Always Apply FID Labels": False,
    "Windows x86 PE RTTI Analyzer": True,
    "Windows x86 PE Exception Handling": True,
}


def setup(program, slug):
    from ghidra.util.task import TaskMonitor
    notes = {}
    mem = program.getMemory()
    tx = program.startTransaction("funcdb setup")
    ok = False
    try:
        if slug == "exe":
            marked = []
            for b in mem.getBlocks():
                if b.getName() in common.EXE_CODE_SECTIONS and not b.isExecute():
                    b.setExecute(True)
                    marked.append(b.getName())
            notes["marked_executable"] = marked
            import pefile
            pe = pefile.PE(str(common.module_path(slug)), fast_load=True)
            first_raw = min(s.PointerToRawData for s in pe.sections if s.SizeOfRawData)
            hdr = mem.getBlock("Headers")
            if hdr is not None and hdr.getSize() > first_raw:
                cut = hdr.getStart().add(first_raw)
                mem.split(hdr, cut)
                tail = mem.getBlock(cut)
                mem.removeBlock(tail, TaskMonitor.DUMMY)
                notes["headers_trimmed"] = "%s-%s removed (copy of .text file bytes)" % (
                    cut, hdr.getStart().add(int(pe.OPTIONAL_HEADER.SizeOfHeaders) - 1))
            pe.close()
        opts = program.getOptions("Analyzers")
        applied = {}
        for name, value in OPTIONS.items():
            if name in [str(n) for n in opts.getOptionNames()]:
                opts.setBoolean(name, value)
                applied[name] = value
        notes["analyzer_options"] = applied
        ok = True
    finally:
        program.endTransaction(tx, ok)
    return notes
