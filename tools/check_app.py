#!/usr/bin/env python3
"""The packaging check: nothing game-derived is inside the shareable app
(docs/reference/packaging.md §6), and what is inside, by size.

    .venv/bin/python tools/check_app.py build/dist/EmpireEarth.app [--against DIR ...]
                                        [--no-default-against] [--json OUT]

It fails (exit 1, every finding listed) when the app holds:

- a PE image (an MZ file with a PE header), whatever its name;
- a file the game's own installation has: a size and MD5 that
  db/gog-files.json lists (the 294 files GOG installs, the nine modules
  among them);
- translated code: a Mach-O file with the translator's symbols (fn_<address>,
  body_<address>) or a strong image table (the runtime's are weak), or any
  .bundle;
- a translation's by-products by name: symbols.json, funcdb.json,
  functions.tsv, listings (*.asm), generated C (chunk_*.c, funcs.h, table.c),
  Ghidra projects (*.gpr, *.rep);
- a file with the same bytes as one under analysis/, build/recomp/ or
  original/ (or each --against DIR; virtual environments and hidden folders
  there are skipped), unless the same bytes are a file this repository or
  the kit tracks (a kit header the translation copies, say);
It warns (without failing) when a file spells out this machine's home
directory: a build path, which says nothing about the game but more than
needed about whoever packaged it (FFmpeg keeps its configure line, prefix
included).

It prints the size of each part of the app. The repository's own data the
first run needs (db/, game.toml) is expected: it holds addresses, sizes and
hashes, no game bytes (packaging.md §6).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

PE_DOS = b"MZ"
MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
TRANSLATED = re.compile(r"(?:^|_)(?:fn|body)_[0-9a-fA-F]{8}\b")
IMAGE_TABLE = ("_recomp_func_addrs", "_recomp_base_ptrs", "_recomp_index_of", "_recomp_call",
               "_recomp_jump", "_recomp_profile_name")
NM_NAME = re.compile(r"\b(?:non-)?external\s+(\S+)")
FORBIDDEN_NAMES = re.compile(r"^(symbols\.json|funcdb\.json|functions\.tsv|funcs\.h|table\.c|chunk_\d+\.c"
                             r"|.*\.asm|.*\.gpr|.*\.bundle|.*\.exe|.*\.dll|.*\.ssa|.*\.bik)$", re.I)
# Not analysis outputs: virtual environments and package caches under analysis/.
SKIP_DIRS = {"site-packages", "venv", "node_modules", "__pycache__"}
FORBIDDEN_DIRS = re.compile(r"^(.*\.rep|analysis|gen|aux-.*|.*\.objects)$", re.I)


def files_of(app):
    """(path, relative posix path, lstat) of every file and symlink in the app."""
    for dirpath, dirnames, names in os.walk(app):
        for n in sorted(dirnames + names):
            p = Path(dirpath) / n
            st = p.lstat()
            if stat.S_ISDIR(st.st_mode):
                continue
            yield p, p.relative_to(app).as_posix(), st


def head(path, n=4096):
    with open(path, "rb") as f:
        return f.read(n)


def is_pe_file(path):
    data = head(path)
    if not data.startswith(PE_DOS) or len(data) < 0x40:
        return False
    off = int.from_bytes(data[0x3c:0x40], "little")
    if off + 4 > 1 << 20:
        return False
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(4) == b"PE\0\0"


_DIGESTS = {}


def digest(path, algo):
    key = (str(path), algo)
    if key in _DIGESTS:
        return _DIGESTS[key]
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _DIGESTS[key] = h.hexdigest()
    return _DIGESTS[key]


def nm_symbols(path):
    """[(name, line)] from `nm -m` (Mach-O; the line says weak or not)."""
    r = subprocess.run(["nm", "-m", str(path)], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        m = NM_NAME.search(line)
        if m:
            out.append((m.group(1), line))
    return out


def game_files(root):
    """{size: [(md5, name)]}: the files GOG installs (db/gog-files.json), the
    nine modules among them."""
    by_size = {}
    manifest = json.loads((root / "db/gog-files.json").read_text())
    for name, size, md5 in manifest["files"]:
        by_size.setdefault(int(size), []).append((md5.lower(), name))
    return by_size


def tracked_digests(repos):
    """SHA-256 of every file git tracks in `repos` (lazily: only asked for a match)."""
    out = set()
    for repo in repos:
        try:
            names = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True,
                                   check=True).stdout.split(b"\0")
        except (OSError, subprocess.CalledProcessError):
            continue
        for n in names:
            p = Path(repo) / n.decode("utf-8", "replace")
            if n and p.is_file() and not p.is_symlink():
                out.add(digest(p, "sha256"))
    return out


def component(rel):
    """Which part of the app a file belongs to, for the size table."""
    parts = rel.split("/")
    if parts[:2] == ["Contents", "MacOS"]:
        return "MacOS/" + parts[2]
    if parts[:2] == ["Contents", "Frameworks"]:
        return "Frameworks (FFmpeg)"
    if parts[:3] == ["Contents", "Resources", "toolchain"]:
        return "Resources/toolchain/" + parts[3] if len(parts) > 4 else "Resources/toolchain (MANIFEST.json)"
    if parts[:3] == ["Contents", "Resources", "ee"] and len(parts) == 4:
        return "Resources/ee (game.toml, globals.toml)"
    if parts[:3] == ["Contents", "Resources", "ee"] and len(parts) > 4:
        sub = parts[3]
        if sub == "kit":
            return "Resources/ee/kit (translator, bundle driver, headers)"
        if sub == "db":
            return "Resources/ee/db (function DB, manifests)"
        if sub == "bin":
            return "Resources/ee/bin (innoextract)"
        return "Resources/ee/" + sub
    if parts[:2] == ["Contents", "Resources"] and len(parts) == 3:
        name = parts[2]
        if name == "general-midi.sf2":
            return "Resources/general-midi.sf2 (SoundFont)"
        return "Resources (other files)"
    return "/".join(parts[:2]) if len(parts) > 2 else rel


def check(app, against=(), repos=(ROOT, ROOT / "kit"), root=ROOT, home=None):
    """(problems, warnings, sizes): what stops the app shipping, what is worth
    knowing, and {component: [bytes, files]}."""
    app = Path(app)
    problems, warnings, sizes = [], [], {}
    gog_by_size = game_files(root)
    home = home if home is not None else str(Path.home())
    entries = list(files_of(app))
    app_sizes = {}
    for path, rel, st in entries:
        comp = component(rel)
        s = sizes.setdefault(comp, [0, 0])
        s[1] += 1
        if stat.S_ISLNK(st.st_mode):
            continue
        s[0] += st.st_size
        name = path.name
        if FORBIDDEN_NAMES.match(name):
            problems.append("%s: a translation's or the game's file by name" % rel)
        if any(FORBIDDEN_DIRS.match(part) for part in rel.split("/")[:-1]):
            problems.append("%s: inside a directory named like an analysis or translation output" % rel)
        if st.st_size >= 64:
            app_sizes.setdefault(st.st_size, []).append((path, rel))
        data = head(path)
        if data.startswith(PE_DOS) and is_pe_file(path):
            problems.append("%s: a PE image" % rel)
        if data[:4] in MACHO_MAGICS:
            syms = nm_symbols(path)
            translated = [n for n, _ in syms if TRANSLATED.search(n)]
            if translated:
                problems.append("%s: %d translated symbols (%s, ...)" % (rel, len(translated), translated[0]))
            strong = sorted({n for n, line in syms if n in IMAGE_TABLE and "undefined" not in line
                             and "weak" not in line})
            if strong:
                problems.append("%s: a linked image table (%s)" % (rel, ", ".join(strong)))
        if st.st_size in gog_by_size:
            md5 = digest(path, "md5")
            for want, game_name in gog_by_size[st.st_size]:
                if md5 == want:
                    problems.append("%s: the game's own %s" % (rel, game_name))
        if home and len(home) > 1:
            with open(path, "rb") as f:
                if home.encode() + b"/" in f.read():
                    warnings.append("%s: names this machine's home directory (%s)" % (rel, home))

    # The same bytes as an analysis or translation output.
    matches = []
    for base in against:
        base = Path(base)
        if not base.exists():
            continue
        for dirpath, dirnames, names in os.walk(base):
            # Third-party packages someone installed beside the analyses are
            # not outputs of them.
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for n in names:
                p = Path(dirpath) / n
                try:
                    st = p.stat()
                except OSError:
                    continue
                for path, rel in app_sizes.get(st.st_size, ()):
                    if digest(path, "sha256") == digest(p, "sha256"):
                        matches.append((rel, p))
    if matches:
        tracked = tracked_digests(repos)
        for rel, p in matches:
            if digest(app / rel, "sha256") not in tracked:
                problems.append("%s: the same bytes as %s" % (rel, p))
    return sorted(set(problems)), sorted(set(warnings)), sizes


def report(sizes, out=sys.stdout):
    total = sum(s[0] for s in sizes.values())
    files = sum(s[1] for s in sizes.values())
    width = max(len(k) for k in sizes) if sizes else 10
    print("%-*s %10s %7s" % (width, "part of the app", "MB", "files"), file=out)
    for comp, (size, count) in sorted(sizes.items(), key=lambda kv: -kv[1][0]):
        print("%-*s %10.1f %7d" % (width, comp, size / 1e6, count), file=out)
    print("%-*s %10.1f %7d" % (width, "total", total / 1e6, files), file=out)
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app", type=Path)
    ap.add_argument("--against", type=Path, action="append", default=[],
                    help="also compare with every file under DIR (default: analysis/ and build/recomp/)")
    ap.add_argument("--no-default-against", action="store_true")
    ap.add_argument("--json", type=Path, help="write the sizes and findings here")
    args = ap.parse_args(argv)
    against = list(args.against)
    if not args.no_default_against:
        against += [ROOT / "analysis", ROOT / "build/recomp", ROOT / "original"]
    problems, warnings, sizes = check(args.app, against)
    total = report(sizes)
    if args.json:
        args.json.write_text(json.dumps({"total_bytes": total, "sizes": sizes, "problems": problems,
                                         "warnings": warnings}, indent=1) + "\n")
    for w in warnings:
        print("warning: " + w)
    if problems:
        print("\nThe app must not ship (%d finding%s):" % (len(problems), "" if len(problems) == 1 else "s"))
        for p in problems:
            print("  " + p)
        return 1
    print("\nNothing game-derived: no PE image, no game file, no translated code, no analysis or "
          "translation output, no build path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
