#!/usr/bin/env python3
"""Write the translator's inputs from the function DB and the player's binaries,
with no Ghidra (docs/reference/build.md §2).

    .venv/bin/python tools/inputs/export.py [--kit DIR] [MODULE...] [--jobs N]
    .venv/bin/python tools/inputs/export.py --compare REF [MODULE...]
    .venv/bin/python tools/inputs/export.py --pin

For every module in game.toml (or the named ones: file names such as
"Default.dll", or the listings directory's name such as "default"), into its
`listings` directory (analysis/kit/<module>/, ignored: derived from game code):

  functions.tsv          the DB's non-DRM entries: address, name, body size
  functions/<va>.asm     each entry's body, decoded with capstone and rendered
                         in the listing grammar (tools/inputs/listing.py)
  funcdb.json            the recomp-funcdb/1 sidecar (tools/inputs/sidecar.py)

These are the files tools/ghidra/ghidra_run.py kit exports from the func-db
Ghidra projects, byte for byte. A body is the DB's `chunks` (or [va, va +
size)), decoded front to back: the DB's bodies are Ghidra's, which hold only
instructions. Each body must decode to the DB's own instruction count.

Each module's inputs are then checked against the SHA-256 that db/inputs.json
pins for them: the inputs this repository was verified with. A mismatch stops
the export (a different decoder, or a DB change whose digests were not pinned).

  --compare REF   build in memory and compare with REF/<module>/ (a Ghidra
                  export) instead of writing: the rows, sidecars and listing
                  lines that differ, with the bytes of each differing instruction
  --pin           write and record the digests in db/inputs.json (after a DB or
                  renderer change has been compared with a Ghidra export)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import kitpath  # noqa: E402
import listing  # noqa: E402
import sidecar  # noqa: E402

ROOT = kitpath.ROOT
PINS = ROOT / "db/inputs.json"


def db_path(module_name):
    return ROOT / "db/functions" / (module_name.lower().replace(" ", "-") + ".json")


def load_image(path, image_base):
    """The module as Windows maps it at its preferred base (no relocation: the
    DB, and so the listings, are at the preferred base)."""
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    if pe.OPTIONAL_HEADER.ImageBase != image_base:
        raise SystemExit("%s prefers base %#x, its DB %#x" % (path, pe.OPTIONAL_HEADER.ImageBase, image_base))
    size = pe.OPTIONAL_HEADER.SizeOfImage
    data = pe.get_memory_mapped_image()
    pe.close()
    return (data + bytes(max(0, size - len(data))))[:size]


def build(db, binary):
    """(functions.tsv text, funcdb.json text, {va: listing text}) for one module."""
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    if digest != db["module"]["sha256"]:
        raise SystemExit("%s: SHA-256 %s, but its DB describes %s" % (binary, digest, db["module"]["sha256"]))
    base = int(db["module"]["image_base"], 16)
    r = listing.Renderer(load_image(binary, base), base)
    rows, listings = [], {}
    for rec in sidecar.translated(db):
        va = int(rec["va"], 16)
        lines = r.lines(sidecar.body_ranges(db, rec))
        if len(lines) != rec.get("insns", 0):
            raise SystemExit("%s %s: %d instructions decoded, the DB counts %d"
                             % (db["module"]["name"], rec["va"], len(lines), rec.get("insns", 0)))
        listings[va] = "\n".join(lines) + "\n"
        rows.append((va, sidecar.listing_name(rec), int(rec["size"])))
    return sidecar.functions_tsv(rows), sidecar.funcdb_text(db), listings


def digest(tsv, funcdb, listings):
    """One SHA-256 over a module's inputs: the index, the sidecar and every
    listing under its file name, in address order."""
    h = hashlib.sha256()
    for text in (tsv, funcdb):
        h.update(text.encode())
    for va in sorted(listings):
        h.update(b"%08x.asm\n" % va)
        h.update(listings[va].encode())
    return h.hexdigest()


def write(out, tsv, funcdb, listings):
    """Replace the module's inputs. funcdb.json goes first and comes back last,
    so an interrupted export leaves inputs that read as missing."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "funcdb.json").unlink(missing_ok=True)
    fdir = out / "functions"
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir()
    for va, text in listings.items():
        (fdir / ("%08x.asm" % va)).write_text(text)
    (out / "functions.tsv").write_text(tsv)
    (out / "funcdb.json").write_text(funcdb)


def compare(ref, tsv, funcdb, listings, image=None, limit=20):
    """Differences between generated inputs and a reference export in `ref`."""
    ref = Path(ref)
    report = {"tsv": tsv == (ref / "functions.tsv").read_text(),
              "funcdb": funcdb == (ref / "funcdb.json").read_text(),
              "listings_differ": [], "examples": []}
    for va, text in sorted(listings.items()):
        path = ref / "functions" / ("%08x.asm" % va)
        theirs = path.read_text() if path.exists() else None
        if theirs == text:
            continue
        report["listings_differ"].append("%08x" % va)
        if theirs is None:
            report["examples"].append("%08x: no reference listing" % va)
            continue
        ma = {l[:8]: l for l in text.splitlines()}
        ta = {l[:8]: l for l in theirs.splitlines()}
        for a in sorted(set(ma) | set(ta)):
            if ma.get(a) != ta.get(a) and len(report["examples"]) < limit:
                raw = ""
                if image is not None:
                    data, base = image
                    o = int(a, 16) - base
                    raw = " [%s]" % data[o:o + 8].hex()
                report["examples"].append("%08x %s: ours %r, ref %r%s" % (va, a, ma.get(a), ta.get(a), raw))
    return report


def modules(cfg):
    """(file name, binary, listings dir) for the executable and every module."""
    yield cfg["game"]["executable"], cfg["developer_exe_path"], cfg["listings_path"]
    for m in cfg["aux_modules"]:
        yield m["name"], m["path"], m["listings_path"]


def pinned():
    return json.loads(PINS.read_text())["inputs"] if PINS.is_file() else {}


def run_one(job):
    name, binary, out, ref, pin = job
    t0 = time.time()
    db = json.loads(db_path(name).read_text())
    tsv, funcdb, listings = build(db, binary)
    rep = {"listings": len(listings), "lines": sum(t.count("\n") for t in listings.values()),
           "sha256": digest(tsv, funcdb, listings)}
    if ref is not None:
        base = int(db["module"]["image_base"], 16)
        rep.update(compare(Path(ref) / Path(out).name, tsv, funcdb, listings,
                           (load_image(binary, base), base)))
    elif pin or rep["sha256"] == pinned().get(name):
        write(out, tsv, funcdb, listings)
    rep["seconds"] = time.time() - t0
    return name, rep


def export_all(cfg, only=(), ref=None, jobs=None, pin=False):
    """Export (or, with `ref`, compare) every module; returns {name: report}.
    Exits if a module's inputs are not the pinned ones (unless `pin`)."""
    from concurrent.futures import ProcessPoolExecutor
    work = [(n, b, o, ref, pin) for n, b, o in modules(cfg)
            if not only or n in only or Path(o).name in only]
    work.sort(key=lambda j: -Path(j[1]).stat().st_size)      # the executable first: most of the work
    pins = pinned()
    out = {}
    with ProcessPoolExecutor(max_workers=jobs or min(len(work), os.cpu_count() or 4)) as ex:
        for name, rep in ex.map(run_one, work):
            out[name] = rep
            state = ("pinned" if rep["sha256"] == pins.get(name)
                     else "NOT PINNED (%s)" % ("none" if name not in pins else pins[name][:12]))
            print("[inputs] %s: %d listings, %d instructions, %.1f s, sha256 %s %s%s"
                  % (name, rep["listings"], rep["lines"], rep["seconds"], rep["sha256"][:12], state,
                     "" if ref is None else
                     "; tsv %s, funcdb %s, %d listings differ"
                     % ("same" if rep["tsv"] else "DIFFERS", "same" if rep["funcdb"] else "DIFFERS",
                        len(rep["listings_differ"]))), flush=True)
            for e in rep.get("examples", ()):
                print("    " + e)
    if pin:
        doc = json.loads(PINS.read_text()) if PINS.is_file() else {}
        doc.setdefault("about", "SHA-256 of each module's translation inputs as tools/inputs/export.py "
                                "writes them (docs/reference/build.md §2). Update with --pin, after "
                                "--compare against a Ghidra export.")
        doc["inputs"] = dict(sorted({**doc.get("inputs", {}),
                                     **{n: r["sha256"] for n, r in out.items()}}.items()))
        PINS.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        print("Pinned %d modules in %s" % (len(out), PINS.relative_to(ROOT)))
    elif ref is None:
        bad = sorted(n for n, r in out.items() if r["sha256"] != pins.get(n))
        if bad:
            sys.exit("The translation inputs of %s are not the ones %s pins, so nothing was written for "
                     "them. A changed DB needs `tools/inputs/export.py --pin` (after --compare against a "
                     "Ghidra export); otherwise this decoder differs from the one the inputs were "
                     "verified with (capstone %s)." % (", ".join(bad), PINS.relative_to(ROOT),
                                                       capstone_version()))
    return out


def capstone_version():
    try:
        from importlib.metadata import version
        return version("capstone")
    except Exception:  # noqa: BLE001 - only for the message
        return "?"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*", help="module file names or listings directory names (default: all)")
    ap.add_argument("--compare", metavar="REF", default=None,
                    help="compare with the exports under REF/<module>/ instead of writing")
    ap.add_argument("--pin", action="store_true", help="record the digests in db/inputs.json")
    ap.add_argument("--jobs", type=int, default=None)
    kit, rest = kitpath.take_kit(sys.argv[1:] if argv is None else list(argv))
    args = ap.parse_args(rest)
    if args.pin and args.compare:
        ap.error("--pin writes; --compare does not")
    sys.path.insert(0, str(kit / "tools"))
    import game_config
    cfg = game_config.load(ROOT)
    reports = export_all(cfg, set(args.modules), args.compare, args.jobs, args.pin)
    if args.compare:
        same = all(r["tsv"] and r["funcdb"] and not r["listings_differ"] for r in reports.values())
        print("identical" if same else "DIFFERENT")
        return 0 if same else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
