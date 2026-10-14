#!/usr/bin/env python3
"""TEMPORARY: stage pre-relocated DLL copies for the kit's current loader.

Remove this tool (and tools/build.py --prerebased, tools/test.py --prerebased)
once the multi-module loader (Phase 2 gap 1, kit branch feat/runtime-core)
is on the kit's `ee` branch: that loader applies base relocations itself.

game.toml translates every DLL for its own base (the base map). Until the
loader can relocate, it refuses a module whose PE ImageBase is not the
configured base. This writes, all in ignored directories:

  build/prerebased/install/
                         a copy of Empire Earth.exe, Data/ (a link to the installation),
                         each DLL relocated to its game.toml base (a copy; the
                         engine, already at its preferred base, is a link).
                         Never under original/: that is the installation.
  build/prerebased/      a derived game directory: game.toml with the staged
                         paths and the copies' SHA-256s (everything else
                         identical; paths absolute), and mods/CMakeLists.txt
                         including this repository's

The translation from the staged copies is the same C as from the originals:
the translator applies the same relocations itself (tests/test_prerebase.py
checks one module end to end when the inputs are present).

    .venv/bin/python -I tools/prerebase.py        # then tools/build.py --prerebased ...
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build/prerebased/install"
DERIVED = ROOT / "build/prerebased"


def relocate(data, new_base):
    """A copy of PE image `data` (file bytes) relocated to `new_base`: every
    IMAGE_REL_BASED_HIGHLOW site gets the base delta, then ImageBase is
    patched. pefile.relocate_image is not used: it also rewrites the import
    thunks of these MSVC 6 DLLs (spike finding)."""
    import pefile
    pe = pefile.PE(data=bytes(data))
    out = bytearray(data)
    old = pe.OPTIONAL_HEADER.ImageBase
    delta = (new_base - old) & 0xffffffff
    count = 0
    for block in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", ()):
        for entry in block.entries:
            if entry.type == 0:                       # ABSOLUTE: block padding
                continue
            if entry.type != 3:
                raise ValueError("unsupported relocation type %d at %#x" % (entry.type, entry.rva))
            off = pe.get_offset_from_rva(entry.rva)
            value = struct.unpack_from("<I", out, off)[0]
            struct.pack_into("<I", out, off, (value + delta) & 0xffffffff)
            count += 1
    struct.pack_into("<I", out, pe.OPTIONAL_HEADER.get_field_absolute_offset("ImageBase"), new_base)
    return bytes(out), count


def link(target, link_path):
    if link_path.is_symlink() or link_path.exists():
        if link_path.is_symlink() and os.readlink(link_path) == str(target):
            return
        link_path.unlink()
    link_path.symlink_to(target)


def stage(cfg):
    """Write the staged installation and return {aux key: (staged path, sha256)}."""
    STAGE.mkdir(parents=True, exist_ok=True)
    install = cfg["developer_exe_path"].parent
    # A copy, not a link: game_config resolves links, and the loader looks
    # for the DLLs beside the executable's real path.
    exe = STAGE / cfg["game"]["executable"]
    if exe.is_symlink():
        exe.unlink()
    if not exe.is_file() or exe.stat().st_size != cfg["developer_exe_path"].stat().st_size:
        shutil.copyfile(cfg["developer_exe_path"], exe)
    link(install / "Data", STAGE / "Data")
    staged = {}
    for m in cfg["aux_modules"]:
        source = Path(m["path"])
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != m["sha256"]:
            raise SystemExit("%s does not match game.toml's SHA-256" % source)
        target = STAGE / m["name"]
        import pefile
        preferred = pefile.PE(data=data, fast_load=True).OPTIONAL_HEADER.ImageBase
        if preferred == m["base"]:
            link(source, target)
            staged[m["key"]] = (target, m["sha256"])
            continue
        copy, count = relocate(data, m["base"])
        if target.is_symlink():
            target.unlink()
        target.write_bytes(copy)
        digest = hashlib.sha256(copy).hexdigest()
        staged[m["key"]] = (target, digest)
        print("%s: %#x -> %#x, %d relocations, sha256 %s" % (m["name"], preferred, m["base"], count, digest))
    return staged


PATH_KEYS = ("developer_exe", "path", "listings", "function_db", "globals")


def derived_toml(text, staged):
    """game.toml with the staged paths and hashes; every path absolute."""
    out, section = [], None
    for line in text.splitlines():
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            section = m.group(1)
        m = re.match(r'^(\s*)(\w+)(\s*=\s*)"([^"]*)"(.*)$', line)
        if m:
            indent, key, eq, value, rest = m.groups()
            aux = section.split(".", 2)[2] if section and section.startswith("modules.aux.") else None
            if key == "developer_exe":
                value = str(STAGE / Path(value).name)
            elif aux and key == "path":
                value = str(staged[aux][0])
            elif aux and key == "sha256":
                value = staged[aux][1]
            elif key in PATH_KEYS:
                value = str((ROOT / value).resolve())
            line = '%s%s%s"%s"%s' % (indent, key, eq, value, rest)
        out.append(line)
    return "\n".join(out) + "\n"


def main():
    sys.path.insert(0, str(ROOT / "tools"))
    import kitpath
    kit, _ = kitpath.take_kit(sys.argv[1:])
    sys.path.insert(0, str(kit / "tools"))
    import game_config
    cfg = game_config.load(ROOT)
    staged = stage(cfg)
    DERIVED.mkdir(parents=True, exist_ok=True)
    (DERIVED / "game.toml").write_text(
        "# GENERATED by tools/prerebase.py (TEMPORARY, pre-relocated DLLs) - do not edit\n"
        + derived_toml((ROOT / "game.toml").read_text(), staged))
    (DERIVED / "mods").mkdir(exist_ok=True)
    (DERIVED / "mods/CMakeLists.txt").write_text(
        "# GENERATED by tools/prerebase.py: this repository's own targets\n"
        'include("%s")\n' % (ROOT / "mods/CMakeLists.txt").as_posix())
    game_config.load(DERIVED)       # the derived directory must load as a game
    print("Derived game directory: %s" % DERIVED)


if __name__ == "__main__":
    main()
