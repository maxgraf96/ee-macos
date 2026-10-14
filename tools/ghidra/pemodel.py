"""PE model used by the function-DB builder: mapped image, sections, exports, imports,
relocations, and the code ranges we trust (not the section flags, for the EXE).

Reads the user's own game files; nothing read here is written to tracked files except
addresses, sizes and names.
"""
import re
import struct
from bisect import bisect_right

import pefile

import common

RTTI_NAME_RE = re.compile(rb"\.\?A[VUW][\x21-\x7e]{1,2000}?@@\x00")


class PEModule:
    def __init__(self, slug):
        path = common.check_hash(slug)
        self.slug = slug
        self.name = common.BY_SLUG[slug][1]
        self.sha256 = common.BY_SLUG[slug][2]
        pe = pefile.PE(str(path))
        oh = pe.OPTIONAL_HEADER
        self.base = oh.ImageBase
        self.size = oh.SizeOfImage
        self.entry = self.base + oh.AddressOfEntryPoint if oh.AddressOfEntryPoint else None
        img = pe.get_memory_mapped_image()
        if len(img) < self.size:
            img = img + bytes(self.size - len(img))
        self.img = bytes(img[:self.size])
        self.size_of_headers = oh.SizeOfHeaders
        self.first_raw = min((s.PointerToRawData for s in pe.sections if s.SizeOfRawData), default=0)
        self.sections = []
        for s in pe.sections:
            nm = s.Name.rstrip(b"\0").decode("ascii", "replace")
            lo = self.base + s.VirtualAddress
            hi = lo + max(s.Misc_VirtualSize, s.SizeOfRawData)
            self.sections.append({"name": nm, "start": lo, "end": hi, "vsize": s.Misc_VirtualSize,
                                  "raw_size": s.SizeOfRawData, "flags": s.Characteristics})
        if slug == "exe":
            code_names = set(common.EXE_CODE_SECTIONS)
            self.code_sections = [s for s in self.sections if s["name"] in code_names]
        else:
            self.code_sections = [s for s in self.sections if s["flags"] & 0x20000000]
        self.text = next((s for s in self.sections if s["name"] == ".text"), None)
        # exports
        self.exports = {}            # va -> [names]
        self.export_list = []        # (ordinal, name, va)
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
            for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                nm = e.name.decode("ascii", "replace") if e.name else "#%d" % e.ordinal
                va = self.base + e.address
                self.exports.setdefault(va, []).append(nm)
                self.export_list.append((e.ordinal, nm, va))
        # imports: IAT slot va -> (dll, name)
        self.iat = {}
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = pe.get_string_at_rva(entry.struct.Name).decode("ascii", "replace")
            for imp in entry.imports:
                nm = imp.name.decode("ascii", "replace") if imp.name else "#%d" % imp.ordinal
                self.iat[imp.address] = (dll, nm)
        # base relocations (DLLs): slot va -> target va
        self.relocs = {}
        for block in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
            for e in block.entries:
                if e.type == 3:
                    slot = self.base + e.rva
                    self.relocs[slot] = self.u32(slot)
        pe.close()
        self._sec_starts = [s["start"] for s in self.sections]

    # --- byte access -------------------------------------------------------------
    def u32(self, va):
        o = va - self.base
        if o < 0 or o + 4 > self.size:
            return None
        return struct.unpack_from("<I", self.img, o)[0]

    def i32(self, va):
        o = va - self.base
        if o < 0 or o + 4 > self.size:
            return None
        return struct.unpack_from("<i", self.img, o)[0]

    def u8(self, va):
        o = va - self.base
        return self.img[o] if 0 <= o < self.size else None

    def bytes_at(self, va, n):
        o = va - self.base
        return self.img[max(o, 0):max(o, 0) + n]

    def cstr(self, va, limit=4096):
        o = va - self.base
        if not (0 <= o < self.size):
            return None
        end = self.img.find(b"\0", o, o + limit)
        return self.img[o:end] if end >= 0 else None

    # --- classification ----------------------------------------------------------
    def section_of(self, va):
        i = bisect_right(self._sec_starts, va) - 1
        if i >= 0 and va < self.sections[i]["end"]:
            return self.sections[i]["name"]
        if self.base <= va < self.base + self.size_of_headers:
            return "Headers"
        return None

    def in_code(self, va):
        return any(s["start"] <= va < s["end"] for s in self.code_sections)

    def in_image(self, va):
        return self.base <= va < self.base + self.size

    def data_sections(self):
        """Sections that can hold pointers the program reads (not code, .reloc, .rsrc)."""
        return [s for s in self.sections
                if s not in self.code_sections and s["name"] not in (".reloc", ".rsrc")]

    def rtti_names(self):
        """(type-descriptor va, mangled name) for every '.?AV...@@' string whose TD header
        (pVFTable, spare=0) precedes it."""
        out = []
        for m in RTTI_NAME_RE.finditer(self.img):
            name_off = m.start()
            td = self.base + name_off - 8
            if (td - self.base) % 4:
                continue
            if self.u32(td + 4) != 0:
                continue
            out.append((td, m.group(0)[:-1].decode("ascii")))
        return out
