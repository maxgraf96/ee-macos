"""Write the external-API inventory of the canonical game modules to db/win32-imports.json.

Usage: python -I tools/inventory.py <game dir> [<out json>]

Records, per external DLL, every imported function and which game modules import it.
Imports between game modules (engine C++ symbols, Dinkumware) are counted but not listed:
they are resolved by translation, not by the HLE layer. The output is the HLE coverage
checklist; it contains only API names, never game bytes.
"""
import hashlib, json, os, sys

import pefile

MODULES = [
    "Empire Earth.exe", "Low-Level Engine.dll", "sxlrt233.dll", "Language.dll",
    "DX7HRDisplay.dll", "DX7HRTnLDisplay.dll", "Default.dll", "TCP Protocol.dll",
    "Miles Sound System Mixer.dll",
]
INTERNAL = {"low-level engine.dll", "sxlrt233.dll"}


def import_dll_name(pe, entry):
    # pefile rejects DLL names containing spaces ("Low-Level Engine.dll") as invalid.
    raw = pe.get_string_at_rva(entry.struct.Name)
    return raw.decode("ascii", "replace").lower()


def main(game_dir, out_path):
    apis, modules, internal_counts = {}, {}, {}
    for name in MODULES:
        path = os.path.join(game_dir, name)
        data = open(path, "rb").read()
        pe = pefile.PE(data=data)
        modules[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                         "image_base": hex(pe.OPTIONAL_HEADER.ImageBase)}
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = import_dll_name(pe, entry)
            if dll in INTERNAL:
                internal_counts[f"{name} -> {dll}"] = len(entry.imports)
                continue
            for imp in entry.imports:
                fn = imp.name.decode() if imp.name else f"#{imp.ordinal}"
                apis.setdefault(dll, {}).setdefault(fn, []).append(name)
    out = {
        "modules": modules,
        "internal_imports": internal_counts,
        "external": {dll: {fn: sorted(set(users)) for fn, users in sorted(fns.items())}
                     for dll, fns in sorted(apis.items())},
        "totals": {dll: len(fns) for dll, fns in sorted(apis.items())},
    }
    out["totals"]["_all"] = sum(len(f) for f in apis.values())
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(json.dumps(out["totals"], indent=1))


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "db", "win32-imports.json"))
