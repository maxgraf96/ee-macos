"""Export what Ghidra knows about a program as a gzip'd JSON "facts" file (PyGhidra).

The facts file is an intermediate in analysis/func-db/facts/ (ignored): it contains
string values and other content of the game binary and must never be tracked. The
tracked DB is built from it by build_db.py.

Contents:
  blocks      memory blocks with permissions as analysed
  functions   entry, name, namespace, symbol source, calling convention, prototype,
              no-return, thunk target, stack purge, body ranges, other labels at entry
  refs        every memory/external reference [from, to, type] (no fall-through, no stack)
  data        defined data [addr, length, type name, string value or null]
  orphans     instruction ranges that belong to no function
  symbols     non-default labels [addr, name, namespace, source]
  bookmarks   [type, category, addr, comment] for Error/Warning/Analysis bookmarks
  externals   external functions referenced [library, name, original mangled name]
"""
import gzip
import json


def _ns(sym_or_fn):
    ns = sym_or_fn.getParentNamespace()
    if ns is None or ns.isGlobal():
        return ""
    return str(ns.getName(True))


def demangle_all(names):
    """{mangled: {"name", "full", "cc"}} via Ghidra's Microsoft demangler. RTTI type names
    ('.?AV...') are demangled as types."""
    from ghidra.app.util.demangler.microsoft import MicrosoftDemangler
    d = MicrosoftDemangler()
    out = {}
    for n in names:
        try:
            ctx = d.createMangledContext(n, d.createDefaultOptions(), None, None)
            if n.startswith(".?A"):
                r = d.demangleType(ctx)
                full = str(r.toString())
                for kw in ("class ", "struct ", "union "):
                    if full.startswith(kw):
                        full = full[len(kw):]
                out[n] = {"name": full, "full": str(r.toString())}
                continue
            r = d.demangle(ctx)
            if r is None:
                continue
            ns = r.getNamespace()
            qn = (str(ns.getNamespaceString()) + "::" if ns is not None else "") + str(r.getName())
            rec = {"name": qn, "full": str(r.getOriginalDemangled() or r.toString())}
            if hasattr(r, "getCallingConvention") and r.getCallingConvention():
                rec["cc"] = str(r.getCallingConvention())
            out[n] = rec
        except Exception:  # noqa: BLE001 - undemanglable names stay mangled
            continue
    return out


def export(program, slug, out, mangled=()):
    from ghidra.program.model.address import AddressSet
    from ghidra.program.model.symbol import SourceType
    import common

    fm = program.getFunctionManager()
    listing = program.getListing()
    st = program.getSymbolTable()
    rm = program.getReferenceManager()
    mem = program.getMemory()

    facts = {"module": common.BY_SLUG[slug][1], "sha256": common.BY_SLUG[slug][2],
             "image_base": program.getImageBase().getOffset(), "blocks": [], "functions": []}
    for b in mem.getBlocks():
        facts["blocks"].append({"name": str(b.getName()), "start": b.getStart().getOffset(),
                                "end": b.getEnd().getOffset() + 1, "r": bool(b.isRead()),
                                "w": bool(b.isWrite()), "x": bool(b.isExecute()),
                                "init": bool(b.isInitialized())})

    covered = AddressSet()
    for f in fm.getFunctions(True):
        entry = f.getEntryPoint()
        body = f.getBody()
        covered.add(body)
        rec = {"entry": entry.getOffset(), "name": str(f.getName()), "ns": _ns(f),
               "src": str(f.getSymbol().getSource()),
               "cc": str(f.getCallingConventionName()),
               "proto": str(f.getPrototypeString(False, True)),
               "noreturn": bool(f.hasNoReturn()), "purge": int(f.getStackPurgeSize()),
               "varargs": bool(f.hasVarArgs()),
               "body": [[r.getMinAddress().getOffset(), r.getMaxAddress().getOffset() + 1]
                        for r in body.getAddressRanges()]}
        if f.isThunk():
            t = f.getThunkedFunction(True)
            if t is not None:
                if t.isExternal():
                    loc = t.getExternalLocation()
                    rec["thunk"] = {"external": str(loc.getLibraryName()), "name": str(t.getName()),
                                    "original": str(loc.getOriginalImportedName() or "")}
                else:
                    rec["thunk"] = {"entry": t.getEntryPoint().getOffset(), "name": str(t.getName())}
        labels = []
        for s in st.getSymbols(entry):
            if s.isPrimary():
                continue
            labels.append([str(s.getName()), _ns(s), str(s.getSource())])
        if labels:
            rec["labels"] = labels
        facts["functions"].append(rec)

    # References out of every initialised address (code and data alike).
    refs = []
    it = rm.getReferenceSourceIterator(mem.getLoadedAndInitializedAddressSet(), True)
    while it.hasNext():
        src = it.next()
        s = src.getOffset()
        for r in rm.getReferencesFrom(src):
            if r.isStackReference() or r.isRegisterReference():
                continue
            rt = str(r.getReferenceType())
            if rt == "FALL_THROUGH":
                continue
            if r.isExternalReference():
                loc = r.getExternalLocation() if hasattr(r, "getExternalLocation") else None
                name = str(loc.getLabel()) if loc is not None else str(r.getToAddress())
                lib = str(loc.getLibraryName()) if loc is not None else ""
                refs.append([s, None, rt, r.getOperandIndex(), lib + "::" + name])
            elif r.isMemoryReference() or r.isOffsetReference() or r.isShiftedReference():
                refs.append([s, r.getToAddress().getOffset(), rt, r.getOperandIndex()])
    facts["refs"] = refs

    data = []
    dit = listing.getDefinedData(True)
    while dit.hasNext():
        d = dit.next()
        dt = d.getDataType()
        name = str(dt.getName())
        val = None
        if d.hasStringValue():
            try:
                val = str(d.getValue())
            except Exception:  # noqa: BLE001 - malformed string data only loses its value
                val = None
        data.append([d.getAddress().getOffset(), int(d.getLength()), name, val])
    facts["data"] = data

    # Instructions not owned by any function.
    exec_set = AddressSet()
    for b in mem.getBlocks():
        if b.isExecute() and b.isInitialized():
            exec_set.add(b.getStart(), b.getEnd())
    rest = exec_set.subtract(covered)
    orphans = []
    iit = listing.getInstructions(rest, True)
    cur = None
    while iit.hasNext():
        ins = iit.next()
        a = ins.getAddress().getOffset()
        n = int(ins.getLength())
        if cur is not None and cur[1] == a:
            cur[1] = a + n
        else:
            cur = [a, a + n]
            orphans.append(cur)
    facts["orphans"] = orphans

    syms = []
    sit = st.getAllSymbols(True)
    while sit.hasNext():
        s = sit.next()
        if s.getSource() == SourceType.DEFAULT or s.isExternal():
            continue
        a = s.getAddress()
        if not a.isMemoryAddress():
            continue
        syms.append([a.getOffset(), str(s.getName()), _ns(s), str(s.getSource()),
                     str(s.getSymbolType())])
    facts["symbols"] = syms

    bms = []
    bm = program.getBookmarkManager()
    for btype in ("Error", "Warning", "Analysis"):
        bit = bm.getBookmarksIterator(btype)
        while bit.hasNext():
            b = bit.next()
            bms.append([btype, str(b.getCategory()), b.getAddress().getOffset(), str(b.getComment())])
    facts["bookmarks"] = bms

    exts = []
    em = program.getExternalManager()
    for lib in em.getExternalLibraryNames():
        lit = em.getExternalLocations(lib)
        while lit.hasNext():
            loc = lit.next()
            exts.append([str(lib), str(loc.getLabel()), str(loc.getOriginalImportedName() or "")])
    facts["externals"] = exts

    facts["demangled"] = demangle_all(sorted(set(mangled)))

    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(facts, fh, separators=(",", ":"))
    return len(facts["functions"])
