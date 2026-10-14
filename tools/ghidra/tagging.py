"""Library / DRM tagging, DRM reachability, and DB assembly for build_db.py.

Tags (one per function):
  game              SSSI code: the game, the Titan engine, its plugins (default)
  lib:dinkumware    Dinkumware C++ library 2.33 (all of sxlrt233.dll; std:: code elsewhere)
  lib:crt-static    MSVC 6 static CRT objects linked into the image (startup, _EH_prolog,
                    64-bit helpers, FID-identified CRT routines)
  lib:import        linker import thunks (JMP [IAT]); the target is in "import"
  lib:mfc           MFC source linked in (AfxDumpStack / CTraceClipboardData)
  lib:won           WON.net SDK (WONAPI namespace classes and their code)
  lib:zlib          zlib 1.1.3
  lib:libpng        libpng
  lib:jpeg          IJG libjpeg 6b
  lib:pkware-dcl    PKWARE Data Compression Library 1.11 (.ssa archives)
  drm               .cms_t residue of the protected retail build (and code only it uses)

Every tag carries its evidence ("tag_ev"): anchor kinds first, then propagation kinds
("callers" = every caller has the tag, "neighbors" = sandwiched between two functions
with the tag in link order and not called from code with another tag).
"""
import json
import re
from bisect import bisect_right
from collections import Counter, defaultdict

import common

STRING_FAMILIES = [
    ("lib:zlib", re.compile(r"inflate \d\.\d|deflate \d\.\d|Jean-loup Gailly|Mark Adler|incorrect header check|"
                            r"invalid block type|invalid stored block lengths|too many length or distance symbols|"
                            r"invalid bit length repeat|invalid literal/length code|invalid distance code|"
                            r"oversubscribed (dynamic|literal|distance)|incomplete (dynamic|literal|distance)|"
                            r"empty distance tree with lengths|^need dictionary$|^stream end$|^insufficient memory$|"
                            r"^buffer error$|^incompatible version$|^file error$|^stream error$|^data error$|"
                            r"^1\.1\.3$")),
    ("lib:libpng", re.compile(r"libpng|\bPNG\b|png_|Not a PNG|IHDR|IDAT|PLTE|tRNS|zlib (version|memory) error|"
                              r"Unknown zlib error|Invalid image (width|height)|Image (width|height) (is|exceeds)|"
                              r"Missing IHDR|CRC error|Incompatible libpng|Width too large for libpng", re.I)),
    ("lib:jpeg", re.compile(r"JFIF|JPEG|Thomas G\. Lane|Huffman|Bogus (message|marker|virtual|input|Huffman|"
                            r"DQT|DAC|DHT)|Quantization table|Corrupt JPEG|Premature end of JPEG|"
                            r"Unsupported JPEG|Adobe APP14|JFXX|Invalid SOS parameters|Inconsistent progression|"
                            r"sampling factors|^6b  27-Mar-1998$|Write to XMS failed|Read from XMS failed|"
                            r"Write to EMS failed|Read from EMS failed|Image too wide for this implementation|"
                            r"Maximum supported image dimension", re.I)),
    ("lib:pkware-dcl", re.compile(r"PKWARE")),
    ("lib:mfc", re.compile(r"AfxDumpStack|IMAGEHLP|CTraceClipboardData")),
    ("lib:won", re.compile(r"^WS_[A-Za-z]+_[A-Za-z]|^WONAPI|^WONStatus")),
    ("lib:dinkumware", re.compile(r"^(vector|deque|list|map/set)<T> too long$|^invalid map/set<T> iterator$|"
                                  r"^invalid vector<T> subscript$|^invalid string (position|argument)$|"
                                  r"^string too long$|^bad allocation$|^bad cast$|^ios_base::|^bad locale name$")),
    ("drm", re.compile(r"SIntf|CmdLineExt|SafeDisc|secdrv|Macrovision|Petite|~df394b|~e5\.0001")),
]

CRT_STARTUP_IMPORTS = {
    "__getmainargs", "__set_app_type", "__p__fmode", "__p__commode", "_initterm", "__setusermatherr",
    "_adjust_fdiv", "_XcptFilter", "_exit", "exit", "_cexit", "_c_exit", "__dllonexit", "_onexit",
    "_amsg_exit", "_controlfp", "__p___initenv", "_acmdln", "_setdefaultprecision", "_except_handler3",
    "__CxxFrameHandler", "_CxxThrowException", "_purecall", "_CIpow", "_ftol", "_matherr",
}

DRM_SECTIONS = (".cms_t", ".cms_d")
C_LIBS = {"lib:zlib", "lib:libpng", "lib:jpeg", "lib:pkware-dcl", "lib:mfc"}
# (tag, constant set, how many must appear in one function)
CONST_SIGS = [
    # jidctint.c / jfdctint.c FIX_0_298631336 ... FIX_3_072711026 (CONST_BITS 13)
    ("lib:jpeg", {2446, 3196, 4433, 6270, 7373, 9633, 12299, 15137, 16069, 16819, 20995, 25172}, 4),
    # jdcolor.c / jccolor.c YCbCr<->RGB (SCALEBITS 16): FIX(1.402) FIX(1.772) FIX(0.71414) FIX(0.34414)
    ("lib:jpeg", {91881, 116130, 46802, 22554, 19595, 38470, 7471, 0xffff4b2e, 0xffffa7e6}, 2),
    # zlib adler32.c BASE 65521, NMAX 5552
    ("lib:zlib", {65521, 5552}, 2),
]
NO_PROPAGATE = {"game", "lib:import", "lib:crt-static", "lib:dinkumware", "drm"}


def class_tag(name):
    if name.startswith("WONAPI::"):
        return "lib:won"
    if name.startswith("std::") or name in ("type_info",):
        return "lib:dinkumware" if name != "type_info" else "lib:crt-static"
    if name == "CTraceClipboardData":
        return "lib:mfc"
    return None


def class_name(b, td):
    mangled = b.rt["tds"].get(td) or ""
    d = b.dem.get(mangled)
    return d["name"] if d else mangled


# ------------------------------------------------------------------- tagging --
def tag_functions(b):
    pm = b.pm
    funcs = b.funcs
    tag = {}
    ev = defaultdict(list)

    def anchor(f, t, why):
        if f is None:
            return
        if f.entry not in tag:
            tag[f.entry] = t
        if why not in ev[f.entry]:
            ev[f.entry].append(why)

    # 0. MSVC 6 static-CRT helpers recognised by shape (before the module rule, so the
    #    CRT objects inside sxlrt233.dll are not called Dinkumware)
    for f in funcs:
        if pm.section_of(f.entry) in DRM_SECTIONS:
            continue
        why = crt_signature(b, f)
        if why:
            anchor(f, "lib:crt-static", why)
    # 1. sections / whole modules
    for f in funcs:
        sec = pm.section_of(f.entry)
        if sec in DRM_SECTIONS:
            anchor(f, "drm", "section")
        elif b.slug == "sxlrt233":
            anchor(f, "lib:dinkumware", "module")
    # 2. import thunks
    for f in funcs:
        if b.callee_import(f.entry):
            anchor(f, "lib:import", "jmp_iat")
    # 3. FID (Ghidra Function ID over vsOlder) -> static CRT
    fid = {bm[2] for bm in b.facts["bookmarks"] if bm[1] == "Function ID Analyzer"}
    for f in funcs:
        # only C-runtime-style names: FID's vsOlder set also matches tiny MFC inlines such as
        # CRect::CRect onto any four-store constructor (EXE+0x334520)
        if f.entry in fid and f.name.startswith(("_", "`")):
            anchor(f, "lib:crt-static", "fid")
    # 4. CRT startup: module entry, _EH_prolog, CRT-only import users near the entry
    if pm.entry and pm.entry in b.by_entry:
        anchor(b.by_entry[pm.entry], "lib:crt-static", "entry")
    for e in b.ehprolog:
        anchor(b.by_entry.get(e), "lib:crt-static", "eh_prolog")
    for f in funcs:
        imps = {pm.iat[s][1] for s in f.f["imports"]}
        imps |= {b.callee_import(t)[1] for t in f.f["callees"] if b.callee_import(t)}
        if imps and imps <= CRT_STARTUP_IMPORTS and f.insns < 80 and \
                not any(i in ("__CxxFrameHandler", "_CxxThrowException", "_purecall", "_except_handler3")
                        for i in imps) and len(f.f["callees"]) <= 6:
            if any(i in ("__getmainargs", "_initterm", "__set_app_type", "__dllonexit", "_onexit",
                         "_XcptFilter", "__setusermatherr", "_amsg_exit") for i in imps):
                anchor(f, "lib:crt-static", "crt_imports")
    # 5. names (exports, FID, demangled) in std::
    for f in funcs:
        nm = best_name(b, f)[0] or ""
        if nm.startswith("std::") or nm.startswith("?") and "@std@@" in nm:
            anchor(f, "lib:dinkumware", "name_std")
    # 6. RTTI: vtable slots and vtable writes of library classes
    lib_class = {}
    for td in b.rt["classes"]:
        t = class_tag(class_name(b, td))
        if t:
            lib_class[td] = t
    for vt in b.rt["vtables"]:
        t = lib_class.get(vt["td"])
        if not t:
            continue
        for s in vt["slots"]:
            f = b.by_entry.get(s)
            if f is not None and not b.callee_import(s):
                anchor(f, t, "vtable")
    vt_td = {vt["va"]: vt["td"] for vt in b.rt["vtables"]}
    for f in funcs:
        tds = {vt_td[v] for v in b.vt_writes.get(f, ()) if v in vt_td}
        if tds:
            ts = {lib_class.get(td) for td in tds}
            if len(ts) == 1 and None not in ts:
                anchor(f, ts.pop(), "vtable_write")
    # 7. strings (and pointer tables of strings) referenced from code
    fam_of = {}
    for a, n, t, v in b.facts["data"]:
        if v is None:
            continue
        for fam, rx in STRING_FAMILIES:
            if rx.search(v):
                fam_of[a] = fam
                break
    # one level of indirection: pointer arrays whose entries name family strings
    ptr_items = [(a, n) for a, n, t, v in b.facts["data"] if v is None and n >= 4]
    for a, n in ptr_items:
        fams = Counter()
        for k in range(0, min(n, 4096), 4):
            p = pm.u32(a + k)
            if p in fam_of:
                fams[fam_of[p]] += 1
        if fams:
            fam_of[a] = fams.most_common(1)[0][0]
    # pointer dwords straight in .rdata/.data (tables Ghidra typed as pointers one by one)
    for sec in pm.data_sections():
        if sec["name"] in DRM_SECTIONS:
            continue
        for va in range(sec["start"], sec["start"] + min(sec["vsize"], sec["raw_size"]) - 3, 4):
            p = pm.u32(va)
            if p in fam_of and va not in fam_of:
                fam_of[va] = fam_of[p]
    # a function is anchored by a family only if that family dominates the strings it
    # names (a 30 KB lobby-UI initialiser that mentions "GameSpyFailure" is not GameSpy)
    strings_at = {a for a, n, t, v in b.facts["data"] if v is not None}
    per_func = defaultdict(lambda: [Counter(), set()])
    for r in b.facts["refs"]:
        to = r[1]
        if to is None:
            continue
        if to in fam_of or to in strings_at:
            f = b.owner(r[0])
            if f is None:
                continue
            slot = per_func[f.entry]
            slot[1].add(to)
            if to in fam_of:
                slot[0][fam_of[to]] += 1
    for e, (fams, tos) in per_func.items():
        if not fams:
            continue
        fam, n = fams.most_common(1)[0]
        fam_targets = sum(1 for t in tos if fam_of.get(t) == fam)
        if fam_targets * 2 >= len(tos) or len(tos) <= 3:
            anchor(b.by_entry[e], fam, "string")
    # 7b. distinctive constants (C libraries reached through function pointers have no
    #     string or caller evidence): IJG fixed-point IDCT/colour constants, zlib Adler-32
    nums = re.compile(r"(?<![\w])(?:0x[0-9a-f]+|\d+)(?![\w])")
    for f in funcs:
        if f.entry in tag or f.insns > 4000:
            continue
        consts = set()
        for lo, hi in f.body:
            for a, s_, m, o in b.md.disasm_lite(pm.bytes_at(lo, hi - lo), lo):
                if m in ("imul", "mov", "add", "sub", "lea", "cmp", "push", "and"):
                    for tok in nums.findall(o):
                        v = int(tok, 0)
                        if v > 0xff:
                            consts.add(v & 0xffffffff)
        for lib, sig, need in CONST_SIGS:
            if len(consts & sig) >= need:
                anchor(f, lib, "constants")
                break
    anchors = dict(tag)

    # 7c. C libraries are linked as contiguous object files: fill short gaps between two
    #     anchors of the same C library (before caller propagation, so that libpng code
    #     called only from WON's image classes stays libpng)
    order0 = [f for f in funcs if f.kind == "function"]
    last = {}
    for i, f in enumerate(order0):
        t = tag.get(f.entry)
        if t in C_LIBS and f.entry in anchors:
            j = last.get(t)
            if j is not None and 0 < i - j <= 24:
                between = order0[j + 1:i]
                if all(tag.get(g.entry) in (None, t) for g in between):
                    for g in between:
                        if g.entry not in tag:
                            tag[g.entry] = t
                            ev[g.entry].append("region")
            last[t] = i

    # 8. propagation
    callers = defaultdict(set)
    for t, fs in b.calls.items():
        for f in fs:
            callers[t].add(f.entry)
    for t, fs in b.tail.items():
        for f in fs:
            callers[t].add(f.entry)
    changed = True
    rounds = 0
    while changed and rounds < 50:
        changed = False
        rounds += 1
        for f in funcs:
            if f.entry in tag:
                continue
            cs = callers.get(f.entry)
            if not cs:
                continue
            ts = {tag.get(c) for c in cs}
            if len(ts) == 1:
                t = ts.pop()
                # CRT start-up calls WinMain/DllMain, Dinkumware templates call element
                # constructors/destructors of game types, DRM calls the OEP: their callees
                # are not theirs.
                if t and t not in NO_PROPAGATE:
                    tag[f.entry] = t
                    ev[f.entry].append("callers")
                    changed = True
    # neighbors in link order
    order = [f for f in funcs]
    idx = {f.entry: i for i, f in enumerate(order)}
    for i, f in enumerate(order):
        if f.entry in tag or f.kind in ("eh_stub",):
            continue
        lo = i - 1
        while lo >= 0 and (order[lo].entry not in tag or tag[order[lo].entry] == "lib:import"):
            lo -= 1
        hi = i + 1
        while hi < len(order) and (order[hi].entry not in tag or tag[order[hi].entry] == "lib:import"):
            hi += 1
        if lo < 0 or hi >= len(order) or hi - lo > 12:
            continue
        t1, t2 = tag[order[lo].entry], tag[order[hi].entry]
        if t1 != t2 or t1 in NO_PROPAGATE:
            continue
        cs = callers.get(f.entry, set())
        if any(tag.get(c, "game") not in (t1,) for c in cs):
            continue
        tag[f.entry] = t1
        ev[f.entry].append("neighbors")
    # EH funclets / stubs inherit their parent's tag
    for f in funcs:
        p = b.parent.get(f.entry)
        if p is not None and f.entry not in anchors:
            pt = tag.get(p, "game")
            if tag.get(f.entry) != pt:
                tag[f.entry] = pt
                ev[f.entry] = ["parent"]
    for f in funcs:
        if f.entry not in tag:
            tag[f.entry] = "game"
    return tag, ev, anchors


def crt_signature(b, f):
    """Evidence code if f has the shape of a VC6 static-CRT helper, else None."""
    pm = b.pm
    ins = list(b.md.disasm_lite(pm.bytes_at(f.entry, min(f.size, 160)), f.entry))
    text = [(m, o) for a, s_, m, o in ins]
    ops = " ; ".join("%s %s" % t for t in text)
    seh3 = "seh3" in f.f.get("eh", ())
    # eh vector constructor/destructor iterators, __ArrayUnwind: SEH3 frame + loop calling
    # the element ctor/dtor passed as the 4th argument
    if seh3 and "call dword ptr [ebp + 0x14]" in ops and f.insns < 60:
        return "crt_ehvec"
    # __unwindfilter / C++ exception filters: compare the code with 0xE06D7363 ('msc')
    if "0xe06d7363" in ops and f.insns < 20:
        return "crt_eh_filter"
    # _setdefaultprecision: _controlfp(_PC_53, _MCW_PC)
    if f.insns <= 8 and ("push 0x30000 ; push 0x10000" in ops):
        return "crt_setdefaultprecision"
    # _chkstk / _alloca_probe
    if pm.bytes_at(f.entry, 6) == bytes.fromhex("513d00100000"):
        return "crt_chkstk"
    # module entry of a DLL linked against msvcrt: _DllMainCRTStartup
    return None


# --------------------------------------------------------------- reachability --
def drm_reachability(b):
    """Can code outside the DRM sections reach .cms_t/.cms_d?

    Checks (all static): direct calls/jumps from non-DRM functions into DRM sections;
    any instruction operand (immediate or displacement) of non-DRM code naming a DRM
    address; any dword (aligned or not) in non-DRM sections whose value lies in a DRM
    section; Ghidra references from non-DRM addresses into DRM sections; the PE entry
    point, import descriptors, TLS and load-config directories."""
    pm = b.pm
    drm = [(s["start"], s["end"]) for s in pm.sections if s["name"] in DRM_SECTIONS]
    if not drm:
        return None

    def in_drm(v):
        return any(lo <= v < hi for lo, hi in drm)

    out = {"entry_point_section": pm.section_of(pm.entry) if pm.entry else None}
    branch, operands = [], []
    hexnum = re.compile(r"0x[0-9a-f]+")
    for f in b.funcs:
        if pm.section_of(f.entry) in DRM_SECTIONS:
            continue
        for t in list(f.f["callees"]) + list(f.f["jumps_out"]):
            if in_drm(t):
                branch.append([hex(f.entry), hex(t)])
        for lo, hi in f.body:
            for a, size, m, o in b.md.disasm_lite(pm.bytes_at(lo, hi - lo), lo):
                if "0x" in o:
                    for h in hexnum.findall(o):
                        v = int(h, 16)
                        if v >= 0x10000 and in_drm(v):
                            operands.append([hex(a), m, hex(v)])
    out["branches_into_drm"] = branch
    out["operands_naming_drm"] = operands[:50]
    out["operands_naming_drm_count"] = len(operands)
    raw = []
    for s in pm.sections:
        if s["name"] in DRM_SECTIONS or s in pm.code_sections or s["name"] == ".rsrc":
            continue          # code: operand scan above; .rsrc: bitmaps, never pointers
        lo = s["start"]
        hi = lo + min(s["vsize"], s["raw_size"])
        data = pm.bytes_at(lo, hi - lo)
        for i in range(0, len(data) - 3):
            v = int.from_bytes(data[i:i + 4], "little")
            if in_drm(v):
                raw.append([hex(lo + i), s["name"], hex(v), (lo + i) % 4 == 0])
    hdr = pm.bytes_at(pm.base, pm.size_of_headers)
    hdr_hits = []
    for i in range(0, len(hdr) - 3):
        v = int.from_bytes(hdr[i:i + 4], "little")
        if in_drm(v):
            hdr_hits.append(hex(pm.base + i))
    out["dwords_naming_drm"] = raw
    out["header_dwords_naming_drm"] = hdr_hits
    refs = []
    for r in b.facts["refs"]:
        if r[1] is not None and in_drm(r[1]) and not in_drm(r[0]):
            refs.append([hex(r[0]), pm.section_of(r[0]) or "?", hex(r[1]), r[2]])
    out["ghidra_refs_into_drm"] = refs
    imports_used_only_by_drm = Counter()
    users = defaultdict(set)
    for f in b.funcs:
        sec = pm.section_of(f.entry)
        for s_ in f.f["imports"]:
            users[s_].add(sec)
        for t in list(f.f["callees"]) + list(f.f["jumps_out"]):
            th = b.import_thunks.get(t)
            if th is None and b.callee_import(t):
                th = pm.u32(t + 2)
            if th is not None:
                users[th].add(sec)
    for slot, (dll, name) in pm.iat.items():
        secs = users.get(slot, set())
        if secs and secs <= set(DRM_SECTIONS):
            imports_used_only_by_drm["%s!%s" % (dll, name)] += 1
    out["imports_used_only_by_drm"] = sorted(imports_used_only_by_drm)
    unused = sorted("%s!%s" % v for s_, v in pm.iat.items() if not users.get(s_))
    out["imports_not_called_by_code"] = len(unused)
    # transfers from DRM into game code (the old protected entry stub)
    into_text = []
    for f in b.funcs:
        if pm.section_of(f.entry) not in DRM_SECTIONS:
            continue
        for lo, hi in f.body:
            for a, size, m, o in b.md.disasm_lite(pm.bytes_at(lo, hi - lo), lo):
                for h in hexnum.findall(o):
                    v = int(h, 16)
                    if pm.section_of(v) == ".text" and (m in ("call", "jmp", "push", "mov")):
                        into_text.append([hex(f.entry), hex(a), m, hex(v)])
    out["drm_code_naming_text"] = into_text
    return out


# ------------------------------------------------------------------- naming --
def best_name(b, f):
    """(name, mangled, signature, source)."""
    pm = b.pm
    imp = b.callee_import(f.entry)
    if imp:
        dll, nm = imp
        d = b.dem.get(nm)
        return ((d["name"] if d else nm), nm if nm.startswith("?") else None,
                (d.get("full") if d else None), "import_thunk")
    exp = pm.exports.get(f.entry)
    if exp:
        mangled = next((n for n in exp if n.startswith("?")), None)
        nm = mangled or exp[0]
        d = b.dem.get(mangled) if mangled else None
        return ((d["name"] if d else nm), mangled, (d.get("full") if d else None), "export")
    if f.src == "ANALYSIS" and not f.name.startswith(("FUN_", "thunk_FUN_", "LAB_")):
        qn = (f.ns + "::" if f.ns else "") + f.name
        return qn, None, None, "fid" if f.entry in b.fid else "ghidra"
    return None, None, None, None


# --------------------------------------------------------------- assemble --
def finish(b):
    pm = b.pm
    b.fid = {bm[2] for bm in b.facts["bookmarks"] if bm[1] == "Function ID Analyzer"}
    # EH parents: stubs, unwind funclets, catch handlers, continuations
    parent, kind = {}, {}
    for stub, f in b.eh_stub_of.items():
        parent[stub] = f.entry
        kind[stub] = "eh_stub"
    for fi, rec in b.funcinfo.items():
        for a in rec["unwind"]:
            parent.setdefault(a, rec["func"].entry)
            kind.setdefault(a, "eh_unwind")
        for a in rec["catches"]:
            parent.setdefault(a, rec["func"].entry)
            kind.setdefault(a, "eh_catch")
    for v, lst in b.eh_targets.items():
        for k, f in lst:
            if k == "catch_cont":
                parent.setdefault(v, f.entry)
                kind.setdefault(v, "eh_continuation")
    for v, lst in b.scope_targets.items():
        for k, f in lst:
            parent.setdefault(v, f.entry)
            kind.setdefault(v, k)
    b.parent = {a: p for a, p in parent.items() if a in b.by_entry and a != p}
    for a, k in kind.items():
        f = b.by_entry.get(a)
        if f is not None and f.kind == "function":
            f.kind = k

    tag, tag_ev, anchors = tag_functions(b)
    reach = drm_reachability(b) if b.slug == "exe" else None

    # classes
    classes = []
    cls_index = {}
    for td in sorted(b.rt["classes"]):
        c = b.rt["classes"][td]
        cls_index[td] = len(classes)
    vt_by_va = {vt["va"]: vt for vt in b.rt["vtables"]}
    for td in sorted(b.rt["classes"]):
        c = b.rt["classes"][td]
        name = class_name(b, td)
        vts = []
        for va in sorted(c["vtables"]):
            vt = vt_by_va[va]
            vts.append({"rva": hx(va - pm.base), "offset": vt["offset"],
                        "slots": [hx(s - pm.base) for s in vt["slots"]]})
        bases = []
        for bc in c["bases"] or []:
            bn = class_name(b, bc["td"]) if bc["td"] in b.rt["tds"] else None
            bases.append({"name": bn, "mdisp": bc["mdisp"], "pdisp": bc["pdisp"],
                          "vdisp": bc["vdisp"], "attr": bc["attr"]})
        rec = {"name": name, "mangled": c["mangled"], "td_rva": hx(td - pm.base),
               "tag": class_tag(name) or ("drm" if pm.section_of(td) in DRM_SECTIONS else "game")}
        if vts:
            rec["vtables"] = vts
        if bases:
            rec["bases"] = bases
        if c["chd_attr"]:
            rec["inheritance"] = {1: "multiple", 2: "virtual", 3: "multiple+virtual"}.get(c["chd_attr"], c["chd_attr"])
        classes.append(rec)
    slot_refs = defaultdict(list)
    for vt in b.rt["vtables"]:
        ci = cls_index[vt["td"]]
        for i, s in enumerate(vt["slots"]):
            slot_refs[s].append([ci, i] if vt["offset"] == 0 else [ci, i, vt["offset"]])
    vt_td = {vt["va"]: vt["td"] for vt in b.rt["vtables"]}
    # owning class of a virtual function: the class among those whose vtables hold it
    # that is a base of all the others (inherited slots repeat the base's function)
    base_sets = {}
    for td, c in b.rt["classes"].items():
        base_sets[cls_index[td]] = {cls_index[bc["td"]] for bc in (c["bases"] or []) if bc["td"] in cls_index}
    owner_class = {}
    for fva, refs in slot_refs.items():
        cs = {r[0] for r in refs}
        for c in cs:
            if all(o == c or c in base_sets.get(o, ()) for o in cs):
                owner_class[fva] = c
                break

    # import table index
    imp_index = {}
    imports = []
    for slot in sorted(pm.iat):
        dll, nm = pm.iat[slot]
        imp_index[slot] = len(imports)
        imports.append({"slot_rva": hx(slot - pm.base), "dll": dll.lower(), "name": nm,
                        **({"demangled": b.dem[nm]["name"]} if nm in b.dem else {})})

    # callers
    callers = defaultdict(set)
    sites_in = Counter()
    for t, fs in b.calls.items():
        for f in fs:
            callers[t].add(f.entry)
        sites_in[t] += len(fs)
    for t, fs in b.tail.items():
        for f in fs:
            callers[t].add(f.entry)

    # switch tables per function
    sw = defaultdict(list)
    for t in b.tables:
        sw[t["func"].entry].append([hx(t["table"] - pm.base), t["entries"],
                                    "ghidra" if t["ghidra"] else "funcdb"])

    out = []
    tagc = Counter()
    kindc = Counter()
    x87_funcs = Counter()
    for f in b.funcs:
        name, mangled, sig, nsrc = best_name(b, f)
        r = {"rva": hx(f.entry - pm.base), "va": hx(f.entry), "size": f.size}
        if len(f.body) > 1 or (f.body and f.body[0][0] != f.entry):
            r["chunks"] = [[hx(a - pm.base), hx(e - pm.base)] for a, e in f.body]
        r["sec"] = pm.section_of(f.entry)
        if name:
            r["name"] = name
            r["name_src"] = nsrc
        if mangled:
            r["mangled"] = mangled
        if sig:
            r["sig"] = sig
        elif nsrc in ("fid", "ghidra") and f.proto:
            r["sig"] = f.proto
        cc = None
        if mangled and mangled in b.dem and b.dem[mangled].get("cc"):
            cc, ccs = b.dem[mangled]["cc"], "demangled"
        elif f.cc not in ("unknown", "default", "", None) and nsrc:
            cc, ccs = f.cc, "ghidra"
        if cc:
            r["cc"] = cc
            r["cc_src"] = ccs
        r["kind"] = f.kind
        kindc[f.kind] += 1
        if f.entry in b.parent:
            r["parent"] = hx(b.parent[f.entry] - pm.base)
        t = tag[f.entry]
        r["tag"] = t
        tagc[t] += 1
        if tag_ev.get(f.entry):
            r["tag_ev"] = tag_ev[f.entry]
        r["insns"] = f.insns
        if f.f["x87"]:
            r["x87"] = f.f["x87"]
            x87_funcs["any"] += 1
            for k in f.f["x87"]:
                x87_funcs[k] += 1
        if f.f["x87_m80"]:
            r["x87_m80"] = f.f["x87_m80"]
        if f.f["simd"]:
            r["simd"] = f.f["simd"]
        if f.f.get("eh"):
            r["eh"] = f.f["eh"]
        if f.f.get("throws"):
            r["throws"] = f.f["throws"]
        cs = callers.get(f.entry, set())
        r["callers"] = len(cs)
        if sites_in.get(f.entry):
            r["call_sites"] = sites_in[f.entry]
        callees = set(f.f["callees"]) | {t for t in f.f["jumps_out"] if t in b.by_entry}
        r["callees"] = len(callees)
        imps = sorted({imp_index[s] for s in f.f["imports"]} |
                      {imp_index[b.import_thunks[c]] for c in f.f["callees"] if c in b.import_thunks})
        if imps:
            r["imports"] = imps
        if f.f["icalls"]:
            r["icalls"] = f.f["icalls"]
        if f.f["vcalls"]:
            r["vcalls"] = f.f["vcalls"]
        xr = {k: v for k, v in b.ev.get(f.entry, {}).items() if k != "call"}
        if xr:
            r["xref"] = dict(sorted(xr.items()))
        r["indirect"] = bool(xr)
        if f.entry in slot_refs:
            sr = slot_refs[f.entry]
            r["vt_slots"] = sr[:8]
            if len(sr) > 8:
                r["vt_slots_total"] = len(sr)
        if f.entry in owner_class:
            r["class"] = owner_class[f.entry]
        vw = sorted({cls_index[vt_td[v]] for v in b.vt_writes.get(f, ()) if v in vt_td})
        if vw:
            r["vt_writes"] = vw
        if f.noreturn:
            r["noreturn"] = True
        rp = [x for x in f.f["rets"]]
        if rp:
            r["ret_pop"] = max(rp) if len(set(rp)) == 1 else rp
        if f.f["ecx"] == "read":
            r["ecx_in"] = True
        if f.f["edx"] == "read":
            r["edx_in"] = True
        if f.entry in b.alt:
            r["alt_entries"] = dict(b.alt[f.entry])
            # the interior addresses themselves: the translator seeds each as an
            # alternate entry (kit_export.py "alternate_entries")
            r["alt_vas"] = {hx(t): k for t, k in sorted(b.alt_at[f.entry].items())}
        if f.entry in sw:
            r["switch"] = sw[f.entry]
        if f.seeded:
            r["seeded"] = True
        out.append(r)

    # community symbols (db/community-symbols.json, agent community-re): names with provenance
    comm = load_community(b)
    comm_hits, comm_miss = 0, []
    by_rva = {r["rva"]: r for r in out}
    for rva, ents in comm.items():
        r = by_rva.get(rva)
        if r is None:
            comm_miss.extend([rva, e["name"], e["status"]] for e in ents)
            continue
        comm_hits += 1
        r["community"] = [{"name": e["name"], "status": e["status"], "source": e["source"]} for e in ents]
        if "name" not in r:
            r["name"] = ents[0]["name"]
            r["name_src"] = "community"
    b.stats["community"] = {"function_entries": sum(len(v) for v in comm.values()),
                            "matched_function_starts": comm_hits, "not_a_function_start": comm_miss}

    # vtable-slot coverage: every RTTI vtable slot must name a function start
    slots_total = sum(len(v["slots"]) for v in b.rt["vtables"])
    targets = {t for v in b.rt["vtables"] for t in v["slots"]}
    entry_targets = {t for t in targets if t in b.by_entry}
    ghidra_own = {t for t in entry_targets if not b.by_entry[t].seeded}
    b.stats["vtable_slot_coverage"] = {
        "vtables": len(b.rt["vtables"]), "slots": slots_total, "distinct_targets": len(targets),
        "targets_function_start": len(entry_targets),
        "slots_function_start": sum(1 for v in b.rt["vtables"] for t in v["slots"] if t in b.by_entry),
        "targets_found_by_ghidra_alone": len(ghidra_own),
        "targets_added_by_seeding": len(entry_targets) - len(ghidra_own),
        "targets_not_function_start": [hx(t) for t in sorted(targets - entry_targets)],
    }

    # data inside code sections (switch tables, Ghidra-defined data)
    dic = []
    for a, n, t in sorted(b.code_data):
        dic.append([hx(a - pm.base), n, t])
    for a, n in sorted(set(b.extra_data)):
        dic.append([hx(a - pm.base), n, "table"])

    b.stats["functions"] = len(out)
    b.stats["kinds"] = dict(kindc)
    b.stats["tags"] = dict(tagc)
    b.stats["tag_anchors"] = dict(Counter(anchors.values()))
    b.stats["classes"] = len(classes)
    b.stats["classes_with_vtables"] = sum(1 for c in classes if c.get("vtables"))
    b.stats["vtables"] = len(b.rt["vtables"])
    b.stats["vtable_slots"] = sum(len(v["slots"]) for v in b.rt["vtables"])
    b.stats["class_tags"] = dict(Counter(c["tag"] for c in classes))
    b.stats["x87_functions"] = dict(x87_funcs)
    b.stats["eh"] = {"cxx_frames": sum(1 for f in b.funcs if "cxx" in f.f.get("eh", ())),
                     "seh3_frames": sum(1 for f in b.funcs if "seh3" in f.f.get("eh", ())),
                     "other_frames": sum(1 for f in b.funcs if "seh_other" in f.f.get("eh", ())),
                     "funcinfo": len(b.funcinfo),
                     "throw_sites": sum(f.f.get("throws", 0) for f in b.funcs)}
    b.stats["indirect_targets"] = sum(1 for r in out if r["indirect"])
    b.stats["seeded"] = sum(1 for f in b.funcs if f.seeded)
    b.stats["named"] = Counter(r.get("name_src", "none") for r in out)
    b.stats["switch_tables"] = {"total": len(b.tables),
                                "ghidra": sum(1 for t in b.tables if t["ghidra"]),
                                "funcdb_only": [hx(t["insn"]) for t in b.tables if not t["ghidra"]]}
    if reach is not None:
        b.stats["drm_reachability"] = reach

    db = {
        "schema": common_schema(),
        "module": {
            "name": pm.name, "sha256": pm.sha256, "image_base": hx(pm.base),
            "size_of_image": hx(pm.size), "entry_rva": hx(pm.entry - pm.base) if pm.entry else None,
            "size_of_headers": hx(pm.size_of_headers),
            "sections": [{"name": s["name"], "rva": hx(s["start"] - pm.base), "vsize": hx(s["vsize"]),
                          "raw_size": hx(s["raw_size"]), "flags": hx(s["flags"]),
                          "code": s in pm.code_sections} for s in pm.sections],
        },
        "generator": {"tool": "tools/ghidra/build_db.py", "ghidra": "12.1.4",
                      "analysis": "Ghidra auto-analysis at the preferred base + evidence seeding"},
        "stats": {k: b.stats[k] for k in ("functions", "kinds", "tags", "coverage", "classes",
                                          "vtables", "vtable_slots", "x87_functions", "eh",
                                          "indirect_targets", "seeded", "switch_tables",
                                          "vtable_slot_coverage", "community")
                  if k in b.stats},
        "imports": imports,
        "exports": [{"ordinal": o, "name": n, "rva": hx(va - pm.base),
                     **({"demangled": b.dem[n]["name"]} if n in b.dem else {})}
                    for o, n, va in sorted(pm.export_list)],
        "classes": classes,
        "data_in_code": dic,
        "functions": out,
    }
    if reach is not None:
        db["drm_reachability"] = {k: v for k, v in reach.items()
                                  if k not in ("dwords_naming_drm",)}
        db["drm_reachability"]["dwords_naming_drm"] = len(reach["dwords_naming_drm"])
    return db


COMMUNITY_MODULES = {m[1]: m[0] for m in common.MODULES}


def load_community(b):
    """{rva hex: [entries]} for function entries of this module in db/community-symbols.json."""
    path = common.REPO / "db/community-symbols.json"
    if not path.exists():
        return {}
    d = json.loads(path.read_text())
    out = defaultdict(list)
    for e in d.get("entries", []):
        if COMMUNITY_MODULES.get(e.get("module")) != b.slug or e.get("kind") != "function":
            continue
        if e.get("status") not in ("verified", "unverified", "inferred"):
            continue
        if e.get("rva"):
            rva = int(e["rva"], 16)
        elif e.get("va"):
            rva = int(e["va"], 16) - b.pm.base
        else:
            continue
        src = e.get("source") or {}
        out[hx(rva)].append({"name": e.get("name"), "status": e.get("status"),
                             "source": src.get("repo") if isinstance(src, dict) else str(src)})
    return out


def common_schema():
    return "neoee-funcdb/1"


def hx(v):
    return "%#x" % v


def write_db(db, out):
    """One JSON document; top-level arrays hold one record per line (diff-friendly)."""
    lines = ["{"]
    keys = list(db)
    for i, k in enumerate(keys):
        v = db[k]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v and isinstance(v[0], (dict, list)):
            lines.append(" %s: [" % json.dumps(k))
            for j, item in enumerate(v):
                lines.append("  " + json.dumps(item, separators=(",", ":")) + ("," if j < len(v) - 1 else ""))
            lines.append(" ]" + comma)
        else:
            lines.append(" %s: %s%s" % (json.dumps(k), json.dumps(v, separators=(",", ":")), comma))
    lines.append("}")
    out.write_text("\n".join(lines) + "\n")
