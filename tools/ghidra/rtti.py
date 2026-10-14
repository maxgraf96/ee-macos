"""MSVC 6 RTTI parser: type descriptors, complete object locators, class hierarchy
descriptors and vtables, straight from the mapped PE image.

Ghidra 12.1.4's "Windows x86 PE RTTI Analyzer" finds nothing in the GOG Empire Earth.exe
(no relocations, every section writable), so the function DB does not depend on it.

Layouts (32-bit, MSVC 6):
  TypeDescriptor           {pVFTable, spare=0, char name[]}            name '.?AV...@@'
  CompleteObjectLocator    {sig=0, offset, cdOffset, pTypeDescriptor, pClassHierarchyDescriptor}
  ClassHierarchyDescriptor {sig=0, attributes, numBaseClasses, pBaseClassArray}
  BaseClassDescriptor      {pTypeDescriptor, numContainedBases, mdisp, pdisp, vdisp, attributes}
  vftable[-1] = &CompleteObjectLocator
"""
from collections import Counter


def parse(pm, code_starts=None):
    """Return {"tds": {va: mangled}, "cols": {...}, "vtables": [...], "classes": {...}}.

    code_starts, if given, is a set of addresses that are known function entries; it only
    affects the 'slot_known' statistic, not where a vtable ends.
    """
    tds = {}
    for td, name in pm.rtti_names():
        tds[td] = name
    if not tds:
        return {"tds": {}, "cols": {}, "vtables": [], "classes": {}, "type_info_vftable": None}
    # All real TDs share one pVFTable (type_info's vftable); keep the majority value.
    vft = Counter(pm.u32(td) for td in tds).most_common(1)[0][0]
    tds = {td: n for td, n in tds.items() if pm.u32(td) == vft}

    def chd_ok(chd):
        if pm.u32(chd) != 0:
            return False
        attr, nb, bca = pm.u32(chd + 4), pm.u32(chd + 8), pm.u32(chd + 12)
        if attr is None or attr > 3 or nb is None or not (1 <= nb <= 256) or not pm.in_image(bca or 0):
            return False
        return True

    def bases_of(chd):
        nb, bca = pm.u32(chd + 8), pm.u32(chd + 12)
        out = []
        for i in range(nb):
            bcd = pm.u32(bca + 4 * i)
            if bcd is None or not pm.in_image(bcd):
                break
            td = pm.u32(bcd)
            out.append({"td": td, "name": tds.get(td), "contained": pm.u32(bcd + 4),
                        "mdisp": pm.i32(bcd + 8), "pdisp": pm.i32(bcd + 12),
                        "vdisp": pm.i32(bcd + 16), "attr": pm.u32(bcd + 20)})
        return out

    # Complete object locators: scan every 4-aligned dword of the data sections.
    cols = {}
    for sec in pm.data_sections():
        lo, hi = sec["start"], sec["start"] + min(sec["vsize"], max(sec["raw_size"], sec["vsize"]))
        for va in range(lo, hi - 20, 4):
            td = pm.u32(va + 12)
            if td not in tds or pm.u32(va) != 0:
                continue
            chd = pm.u32(va + 16)
            if chd is None or not pm.in_image(chd) or not chd_ok(chd):
                continue
            cols[va] = {"offset": pm.u32(va + 4), "cd_offset": pm.u32(va + 8), "td": td, "chd": chd}

    # vtables: a dword equal to a COL address, followed by code pointers.
    vtables = []
    col_slots = set()
    for sec in pm.data_sections():
        lo, hi = sec["start"], sec["end"]
        for va in range(lo, hi - 4, 4):
            v = pm.u32(va)
            if v in cols:
                col_slots.add(va)
    for meta in sorted(col_slots):
        start = meta + 4
        slots = []
        p = start
        while True:
            if p in col_slots:
                break
            t = pm.u32(p)
            if t is None or not pm.in_code(t):
                break
            slots.append(t)
            p += 4
        col = cols[pm.u32(meta)]
        vtables.append({"va": start, "col": pm.u32(meta), "td": col["td"], "offset": col["offset"],
                        "slots": slots, "section": pm.section_of(start)})

    classes = {}
    for col_va, col in cols.items():
        td = col["td"]
        c = classes.setdefault(td, {"td": td, "mangled": tds[td], "vtables": [], "bases": None,
                                    "chd_attr": None})
        if c["bases"] is None:
            b = bases_of(col["chd"])
            c["bases"] = b[1:]          # first entry is the class itself
            c["chd_attr"] = pm.u32(col["chd"] + 4)
    for vt in vtables:
        classes.setdefault(vt["td"], {"td": vt["td"], "mangled": tds[vt["td"]], "vtables": [],
                                      "bases": [], "chd_attr": None})["vtables"].append(vt["va"])
    # Type descriptors without a COL: classes known only as types (catch/throw types,
    # dynamic_cast targets, bases of other classes).
    for td, name in tds.items():
        classes.setdefault(td, {"td": td, "mangled": name, "vtables": [], "bases": [], "chd_attr": None})
    return {"tds": tds, "cols": cols, "vtables": vtables, "classes": classes,
            "type_info_vftable": vft}
