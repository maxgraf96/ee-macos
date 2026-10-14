"""Summarise PE files: machine, timestamp, linker, sections, imports, exports, rich header."""
import sys, datetime, hashlib, pefile

RICH_PRODIDS = {
    # partial map of common MSVC6-era / later product ids
    0x0001: "Import0", 0x0004: "Linker600", 0x0005: "Cvtomf600", 0x0006: "Cvtres500",
    0x000A: "Utc12_C (VC6 C)", 0x000B: "Utc12_CPP (VC6 C++)", 0x000E: "Masm611",
    0x0013: "Linker612", 0x0015: "Masm613", 0x0019: "Implib700", 0x005D: "Utc13_C",
    0x005F: "Utc13_CPP", 0x0083: "Utc1310_C", 0x0084: "Utc1310_CPP",
    0x0103: "Linker1000+", 0x0104: "Utc1600_C", 0x0105: "Utc1600_CPP",
}

def main(path, verbose):
    pe = pefile.PE(path, fast_load=False)
    data = open(path, "rb").read()
    fh, oh = pe.FILE_HEADER, pe.OPTIONAL_HEADER
    ts = datetime.datetime.utcfromtimestamp(fh.TimeDateStamp)
    print(f"== {path.split('/')[-1]}  size={len(data)} sha256={hashlib.sha256(data).hexdigest()[:16]}")
    print(f"   machine={hex(fh.Machine)} ts={ts:%Y-%m-%d} linker={oh.MajorLinkerVersion}.{oh.MinorLinkerVersion} "
          f"imagebase={hex(oh.ImageBase)} entry={hex(oh.AddressOfEntryPoint)} subsys={oh.Subsystem} "
          f"dllchars={hex(oh.DllCharacteristics)} reloc={'yes' if hasattr(pe,'DIRECTORY_ENTRY_BASERELOC') else 'no'}")
    print("   sections:", ", ".join(f"{s.Name.rstrip(b'\0').decode(errors='replace')}({s.Misc_VirtualSize:#x})" for s in pe.sections))
    rich = pe.parse_rich_header()
    if rich:
        vals = rich["values"]
        comps = []
        for i in range(0, len(vals), 2):
            prodid, build = vals[i] >> 16, vals[i] & 0xFFFF
            comps.append(f"{RICH_PRODIDS.get(prodid, hex(prodid))}/b{build}x{vals[i+1]}")
        print("   rich:", "; ".join(comps))
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        for imp in pe.DIRECTORY_ENTRY_IMPORT:
            names = [ (i.name.decode() if i.name else f"#{i.ordinal}") for i in imp.imports]
            if verbose:
                print(f"   import {imp.dll.decode()} ({len(names)}): {', '.join(names)}")
            else:
                print(f"   import {imp.dll.decode()} ({len(names)}): {', '.join(names[:12])}{' ...' if len(names)>12 else ''}")
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        ex = [ (e.name.decode() if e.name else f"#{e.ordinal}") for e in pe.DIRECTORY_ENTRY_EXPORT.symbols]
        print(f"   exports ({len(ex)}): {', '.join(ex[:40])}{' ...' if len(ex)>40 else ''}")
    if hasattr(pe, "VS_VERSIONINFO") and hasattr(pe, "FileInfo"):
        for fi in pe.FileInfo:
            for entry in fi:
                if hasattr(entry, "StringTable"):
                    for st in entry.StringTable:
                        kv = {k.decode(errors='replace'): v.decode(errors='replace') for k, v in st.entries.items()}
                        keep = {k: kv[k] for k in ("CompanyName","FileDescription","FileVersion","ProductName","ProductVersion","OriginalFilename") if k in kv}
                        if keep: print("   version:", keep)

if __name__ == "__main__":
    verbose = "-v" in sys.argv
    for p in [a for a in sys.argv[1:] if a != "-v"]:
        try:
            main(p, verbose)
        except Exception as e:
            print(f"== {p}: ERROR {e}")
