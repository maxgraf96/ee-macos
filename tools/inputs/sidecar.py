"""The translator's function-DB input (`funcdb.json`, schema recomp-funcdb/1) and
the listing index (`functions.tsv`), from the function DB alone.

Both are pure functions of db/functions/<module>.json: no Ghidra, no game bytes.
tools/inputs/export.py (the default, Ghidra-free inputs) and
tools/ghidra/kit_export.py (the Ghidra export) both write them through here, so
the two pipelines cannot drift apart on anything but the listings themselves.
"""
import json


def translated(db):
    """The DB's records the translator gets: every one whose tag is not "drm".
    Import thunks, EH stubs, unwind funclets, catch handlers and their
    continuations are all included: the kit needs each as an entry (the
    runtime's MSVC EH implementation calls the funclets)."""
    return [r for r in db["functions"] if r["tag"] != "drm"]


def listing_name(r):
    """The name the translated function gets in the kit's symbols.json and
    profiler: the DB's best name (export, import thunk, FID, community), else
    fn_<va>."""
    return " ".join((r.get("name") or "").split()) or "fn_%08x" % int(r["va"], 16)


def body_ranges(db, r):
    """[lo, hi) address ranges of a record's body, at the DB's image base."""
    base = int(db["module"]["image_base"], 16)
    if r.get("chunks"):
        return [(base + int(lo, 16), base + int(hi, 16)) for lo, hi in r["chunks"]]
    va = int(r["va"], 16)
    return [(va, va + int(r["size"]))]


def functions_tsv(rows):
    """functions.tsv text (the kit's index format, kit/tools/ExportProgram.java)
    for (va, name, bytes) rows."""
    return ("address\tname\tbytes\tstatus\tmessage\n"
            + "\n".join("%08x\t%s\t%d\tlisting\t" % row for row in rows) + "\n")


def funcdb(db):
    """The recomp-funcdb/1 document for one module's DB."""
    base = int(db["module"]["image_base"], 16)
    sections = db["module"]["sections"]

    def span(s):
        lo = base + int(s["rva"], 16)
        return [hex(lo), hex(lo + int(s["vsize"], 16)), s["name"]]

    keep = translated(db)
    return {
        # the function-DB input recomp-kit's translator reads ([translate] function_db;
        # kit/tools/recomp/translate.py FunctionDB). Addresses are at image_base, the
        # module's preferred base; the translator moves them to the base it translates for.
        "schema": "recomp-funcdb/1",
        "module": db["module"]["name"], "sha256": db["module"]["sha256"], "image_base": hex(base),
        # replaces section-flag code classification (EXE sections are all 0xc0000040)
        "code_ranges": [span(s) for s in sections if s["code"]],
        # never translate or scan: DRM residue and the SizeOfHeaders/.text overlap
        "excluded_ranges": ([span(s) for s in sections if s["name"] in (".cms_t", ".cms_d")]
                            + [[hex(base), hex(base + int(db["module"]["size_of_headers"], 16)),
                                "headers"]]),
        # every function start the DB knows, with the evidence that makes it reachable
        "entry_points": [[r["va"], r["kind"], sorted((r.get("xref") or {}).keys())] for r in keep],
        # interior addresses that data or EH tables name (alternate entries): each is an
        # instruction boundary inside a DB function, with the evidence that names it
        "alternate_entries": [[va, kinds] for r in keep
                              for va, kinds in sorted((r.get("alt_vas") or {}).items(),
                                                      key=lambda kv: int(kv[0], 16))],
        # bytes inside code sections that are data (jump/index tables, constants)
        "data_in_code": [[hex(base + int(d[0], 16)), d[1], d[2]] for d in db["data_in_code"]],
        "jump_tables": [[r["va"], hex(base + int(t[0], 16)), t[1], t[2]]
                        for r in db["functions"] for t in r.get("switch", ())],
        "excluded_functions": [r["va"] for r in db["functions"] if r["tag"] == "drm"],
    }


def funcdb_text(db):
    return json.dumps(funcdb(db), indent=0) + "\n"
