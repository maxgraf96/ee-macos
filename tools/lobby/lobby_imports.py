#!/usr/bin/env python3
"""The Multiplayer lobby's Win32 surface, from the committed function DB (docs/reference/lobby.md).

  python3 -I tools/lobby/lobby_imports.py [--kit kit] [--md]

Reads only db/functions/empire-earth.exe.json (addresses, tags, RTTI classes, import indices; no
code) and, with --kit, greps the kit's sources (not its tests, not import_argc.cpp) for each API name as a string literal,
which is how the kit's shim tables name their entries: a heuristic "registered?" column, not proof
of behaviour.

Prints:
1. the WONAPI Win32 back end: every function of the MS* display classes (MSWindow, MSWindowManager,
   MSGraphics, MSFont, MSNativeImage, MSNativeMask, MSCursor, ...) with its vtable slot and imports;
2. the external imports used by the lobby code, per DLL, counted in functions:
   - "won": functions the DB tags lib:won (the WONAPI library, 24 % of the EXE);
   - "glue": EE's own lobby glue, EXE 0x677000-0x684000 (WONAPI/WONLobby/GameCode/EmpireEarth);
   - plus the APIs the lobby reaches by LoadLibraryA/GetProcAddress (not in the import table).

Pure standard library.
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "db", "functions", "empire-earth.exe.json")
BASE = 0x400000
GLUE = (0x677000, 0x684000)
GUI_DLLS = ("user32.dll", "gdi32.dll", "imm32.dll", "kernel32.dll", "advapi32.dll", "wsock32.dll",
            "shell32.dll", "comctl32.dll", "msimg32.dll", "wininet.dll", "iphlpapi.dll")
# Reached through LoadLibraryA + GetProcAddress (strings next to the code; lobby.md §5).
DYNAMIC = [("msimg32.dll", "AlphaBlend", "EXE 0x7ba6d0 (MSGraphics, translucent fills)"),
           ("wininet.dll", "InternetGetConnectedState", "EXE 0x6ab070 (InitLogic, CheckForInternet)"),
           ("iphlpapi.dll", "GetAdaptersInfo", "EXE 0x6add00 (WONAPI adapter map, netcode.md §3.4)")]
SKIP_DLLS = ("low-level engine.dll", "sxlrt233.dll", "msvcrt.dll")


def load():
    with open(DB) as f:
        return json.load(f)


def slot_map(db):
    """function RVA -> [(class, slot)] from the RTTI vtables."""
    m = collections.defaultdict(list)
    for c in db["classes"]:
        for vt in c.get("vtables", []):
            for i, s in enumerate(vt["slots"]):
                m[int(s, 16)].append((c["name"], i))
    return m


def kit_literals(kit_dir, names):
    found = {n: [] for n in names}
    for sub in ("runtime", "dx", "host"):
        d = os.path.join(kit_dir, sub)
        for dirpath, _, files in os.walk(d):
            if "tests" in dirpath.split(os.sep):
                continue
            for fn in files:
                # import_argc.cpp lists every known import's argument count, served or not.
                if not fn.endswith((".cpp", ".h", ".c", ".mm")) or fn == "import_argc.cpp":
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    text = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for n in names:
                    if '"%s"' % n in text:
                        found[n].append(os.path.relpath(p, kit_dir))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kit", help="kit checkout to grep for shim names (e.g. kit)")
    ap.add_argument("--md", action="store_true", help="Markdown tables")
    a = ap.parse_args()
    db = load()
    imps = db["imports"]
    slots = slot_map(db)

    # 1. The MS* back end.
    print("## WONAPI Win32 back end (MS* display classes)\n")
    if a.md:
        print("| VA | Class[slot] | Imports |\n|---|---|---|")
    for f in db["functions"]:
        rva = int(f["rva"], 16)
        cls = [(c, i) for c, i in slots.get(rva, []) if c.startswith("WONAPI::MS") and
               c.split("::")[1] in ("MSWindow", "MSWindowManager", "MSGraphics", "MSFont", "MSNativeImage",
                                     "MSNativeMask", "MSNativeIcon", "MSCursor")]
        if not cls:
            continue
        names = sorted({imps[i]["name"] for i in f.get("imports", []) if imps[i]["dll"] not in SKIP_DLLS})
        label = ", ".join("%s[%d]" % (c.split("::")[1], i) for c, i in sorted(set(cls)))
        if a.md:
            print("| `%#x` | %s | %s |" % (rva + BASE, label, " ".join(names)))
        else:
            print("  %#x  %-34s %s" % (rva + BASE, label, " ".join(names)))
    print()

    # 2. The import surface.
    use = {"won": collections.defaultdict(collections.Counter), "glue": collections.defaultdict(collections.Counter)}
    for f in db["functions"]:
        va = int(f["va"], 16)
        bucket = "glue" if GLUE[0] <= va < GLUE[1] else ("won" if f["tag"] == "lib:won" else None)
        if not bucket:
            continue
        for i in f.get("imports", []):
            im = imps[i]
            if im["dll"] in SKIP_DLLS:
                continue
            use[bucket][im["dll"]][im["name"]] += 1
    names = sorted({n for b in use.values() for d in b.values() for n in d} | {n for _, n, _ in DYNAMIC})
    kit = kit_literals(a.kit, names) if a.kit else None
    print("## Lobby imports (functions that call each; won = lib:won, glue = EXE %#x-%#x)\n" % GLUE)
    if a.md:
        print("| DLL | API | won | glue |%s\n|---|---|---|---|%s" % (" kit |" if kit else "", "---|" if kit else ""))
    dlls = sorted({d for b in use.values() for d in b})
    for dll in dlls:
        for n in sorted(set(use["won"][dll]) | set(use["glue"][dll])):
            row = (dll, n, use["won"][dll][n], use["glue"][dll][n])
            k = (", ".join(sorted(set(kit[n]))[:2]) or "-") if kit else None
            if a.md:
                print("| %s | `%s` | %d | %d |%s" % (row + ((" %s |" % k) if kit else "",)))
            else:
                print("  %-14s %-28s won=%-4d glue=%-3d%s" % (row + (("  kit: " + k) if kit else "",)))
    print("\n## Reached by LoadLibraryA/GetProcAddress\n")
    for dll, n, where in DYNAMIC:
        k = (", ".join(sorted(set(kit[n]))[:2]) or "-") if kit else ""
        print("  %-14s %-28s %s%s" % (dll, n, where, ("  kit: " + k) if kit else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
