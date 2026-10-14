#!/usr/bin/env python3
"""PyGhidra driver for the function DB (Phase 1).

Subcommands (all run headless through PyGhidra, one JVM per invocation):

  analyze [MOD...]           import each module at its preferred image base, apply the
                             pre-analysis fixes (setup_program.py), run auto-analysis,
                             save the project under analysis/func-db/ghidra/<slug>/.
  seed MOD STARTS.json       open the saved program, disassemble and create functions at
                             the given addresses (evidence-backed starts computed by
                             build_db.py), let auto-analysis follow the changes, save.
  export [MOD...]            write analysis/func-db/facts/<slug>.json.gz (export_facts.py).
  kit MOD DB.json OUTDIR     write recomp-kit listings (functions.tsv + functions/*.asm)
                             for exactly the functions in the DB (kit_export.py).

Run with the func-db venv (pyghidra, pefile, capstone):
  GHIDRA_MAXMEM=8G analysis/func-db/.venv/bin/python tools/ghidra/ghidra_run.py analyze exe
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def project_dir(slug):
    return common.WORK / "ghidra" / slug


def open_project(slug, create=False):
    from ghidra.base.project import GhidraProject
    d = project_dir(slug)
    if create:
        if (d / ("%s.gpr" % slug)).exists():
            import shutil
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
        return GhidraProject.createProject(str(d), slug, False)
    if not (d / ("%s.gpr" % slug)).exists():
        raise SystemExit("no Ghidra project for %s; run 'analyze %s' first" % (slug, slug))
    return GhidraProject.openProject(str(d), slug)


def analyze_and_repair(pyghidra, prog):
    """Full auto-analysis, then repair disassembly conflicts and re-analyse until clean."""
    import repair
    log = str(pyghidra.analyze(prog))
    repairs = []
    for _ in range(3):
        r = repair.repair_conflicts(prog)
        repairs.append(r)
        if not r["repaired"]:
            break
        log += str(pyghidra.analyze(prog))
    return log, repairs


def cmd_analyze(args):
    pyghidra = common.start_ghidra()
    from java.io import File
    import setup_program
    for slug in common.select(args.modules):
        path = common.check_hash(slug)
        t0 = time.time()
        proj = open_project(slug, create=True)
        seeded = common.WORK / "work" / ("seeded-%s.json" % slug)
        if seeded.exists():
            seeded.unlink()          # a fresh analysis has none of the earlier seeds
        try:
            prog = proj.importProgram(File(str(path)))
            if prog.getImageBase().getOffset() != common.BY_SLUG[slug][3]:
                raise SystemExit("%s imported at %s, expected preferred base %#x"
                                 % (slug, prog.getImageBase(), common.BY_SLUG[slug][3]))
            notes = setup_program.setup(prog, slug)
            log, repairs = analyze_and_repair(pyghidra, prog)
            notes["repairs"] = repairs
            proj.saveAs(prog, "/", slug, True)
            dt = time.time() - t0
            nfun = prog.getFunctionManager().getFunctionCount()
            meta = {"module": common.BY_SLUG[slug][1], "sha256": common.BY_SLUG[slug][2],
                    "image_base": "%#x" % prog.getImageBase().getOffset(),
                    "ghidra": str(common.GHIDRA_HOME), "seconds": round(dt, 1),
                    "functions_after_analysis": nfun, "setup": notes}
            (project_dir(slug) / "analyze.json").write_text(json.dumps(meta, indent=1) + "\n")
            (project_dir(slug) / "analysis.log").write_text(str(log))
            print("[analyze] %s: %d functions, %.0f s" % (slug, nfun, dt), flush=True)
        finally:
            proj.close()


def cmd_seed(args):
    pyghidra = common.start_ghidra()
    import seed_functions
    slug = args.module
    starts = json.loads(Path(args.starts).read_text())
    proj = open_project(slug)
    try:
        prog = proj.openProgram("/", slug, False)
        t0 = time.time()
        accept = tuple(args.accept.split(","))
        report = seed_functions.seed(prog, starts, accept)
        _, report["repairs"] = analyze_and_repair(pyghidra, prog)
        proj.save(prog)
        # cumulative record of every function the seeding created (DB field "seeded")
        rec_path = common.WORK / "work" / ("seeded-%s.json" % slug)
        prev = json.loads(rec_path.read_text()) if rec_path.exists() else []
        by_va = {s["va"]: s for s in prev}
        lookup = {s["va"]: s for s in starts}
        for va in report["created_list"]:
            by_va[va] = lookup[va]
        rec_path.write_text(json.dumps(sorted(by_va.values(), key=lambda s: int(s["va"], 16)), indent=0) + "\n")
        report.pop("created_list")
        report["seconds"] = round(time.time() - t0, 1)
        out = common.WORK / "work" / ("seed-%s-report.json" % slug)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
        print("[seed] %s: %s" % (slug, {k: v for k, v in report.items() if not isinstance(v, list)}),
              flush=True)
    finally:
        proj.close()


def cmd_export(args):
    common.start_ghidra()
    import export_facts
    import pemodel
    for slug in common.select(args.modules):
        pm = pemodel.PEModule(slug)
        mangled = [n for names in pm.exports.values() for n in names if n.startswith("?")]
        mangled += [n for _, n in pm.iat.values() if n.startswith("?")]
        mangled += [n for _, n in pm.rtti_names()]
        proj = open_project(slug)
        try:
            prog = proj.openProgram("/", slug, True)
            t0 = time.time()
            out = common.WORK / "facts" / ("%s.json.gz" % slug)
            out.parent.mkdir(parents=True, exist_ok=True)
            n = export_facts.export(prog, slug, out, mangled)
            print("[export] %s: %d functions -> %s (%.0f s)" % (slug, n, out, time.time() - t0),
                  flush=True)
        finally:
            proj.close()


def cmd_kit(args):
    common.start_ghidra()
    import kit_export
    slug = args.module
    proj = open_project(slug)
    try:
        prog = proj.openProgram("/", slug, False)
        stats = kit_export.export(prog, slug, Path(args.db), Path(args.out),
                                  decompile=args.decompile)
        print("[kit] %s: %s" % (slug, stats), flush=True)
        # Functions created for the export are not saved: the saved project stays the
        # analysis record, the DB stays the authority.
    finally:
        proj.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze"); p.add_argument("modules", nargs="*"); p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("seed"); p.add_argument("module"); p.add_argument("starts")
    p.add_argument("--accept", default="strong,orphan_code",
                   help="comma list of seed classes to apply (strong, orphan_code, validated)")
    p.set_defaults(fn=cmd_seed)
    p = sub.add_parser("export"); p.add_argument("modules", nargs="*"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("kit"); p.add_argument("module"); p.add_argument("db"); p.add_argument("out")
    p.add_argument("--decompile", action="store_true", help="also write analysis pseudocode (.c)")
    p.set_defaults(fn=cmd_kit)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
