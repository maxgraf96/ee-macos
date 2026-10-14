"""Shared configuration for the function-DB pipeline (tools/ghidra/).

Everything here is metadata about the canonical GOG modules (PLAN.md §2.4): file
names, hashes and preferred image bases. No game bytes.
"""
import hashlib
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAME_DIR = Path(os.environ.get("EE_GAME_DIR", REPO / "original/gog/Empire Earth"))
WORK = Path(os.environ.get("FUNCDB_WORK", REPO / "analysis/func-db"))
DB_DIR = REPO / "db/functions"
GHIDRA_HOME = Path(os.environ.get("GHIDRA_HOME", "/opt/homebrew/Cellar/ghidra/12.1.4/libexec"))
JAVA_HOME = os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")

# slug, file name, sha256, preferred image base (from the PE header; checked at run time)
MODULES = [
    ("exe", "Empire Earth.exe",
     "957e35d75ba80309d094cbfb7367f7c6d61c5fd19fc5f7ca3f2e1cf636a065ab", 0x00400000),
    ("engine", "Low-Level Engine.dll",
     "e3f6a2f6d1ec5c3144f7cdd918cf54166de86547d4bbb6b3c9177d3eeb003214", 0x10000000),
    ("sxlrt233", "sxlrt233.dll",
     "ccf6f97d21bdf07abfb709a19fd7e75c585a972bada6341d61952b4700b4440b", 0x10000000),
    ("language", "Language.dll",
     "2539f8b76ee3a6c6b6e03e5ce76d43e891c8b41533d058e249feea5ca8ca3456", 0x10000000),
    ("dx7hr", "DX7HRDisplay.dll",
     "46a69ac011e9a934b1edd484a3f91f3482a5c85a3d47a44eddd1e4545746ff53", 0x10000000),
    ("dx7hrtnl", "DX7HRTnLDisplay.dll",
     "f0c5b16a7db0409e7915af7ff6ded3ac61eb29835f557aad5c5a68b37085b154", 0x10000000),
    ("default", "Default.dll",
     "ab0e791039e558ac93f250594003995a4037883864cbdbbb6ded6f8faa4f9d3e", 0x10000000),
    ("tcp", "TCP Protocol.dll",
     "b695886b01f63b7f699d204e89f38fcd03fb374a543866ed898381a194c170f0", 0x10000000),
    ("milesmix", "Miles Sound System Mixer.dll",
     "6e1a5145d31dfe325d2c7c5b62daaa49af233bb24fede22c56bc66ac11265e09", 0x10000000),
]
BY_SLUG = {m[0]: m for m in MODULES}

# EXE sections that hold code although every section is flagged 0xc0000040 (no
# CNT_CODE / MEM_EXECUTE). .cms_t is GOG/DRM residue; it is marked executable
# only so Ghidra can follow references into it for the reachability study.
EXE_CODE_SECTIONS = (".text", ".cms_t")


def module_path(slug):
    return GAME_DIR / BY_SLUG[slug][1]


def db_filename(slug):
    """db/functions/<file>: the module file name, lower case, spaces -> '-'."""
    return BY_SLUG[slug][1].lower().replace(" ", "-") + ".json"


def check_hash(slug):
    path = module_path(slug)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BY_SLUG[slug][2]:
        raise SystemExit("%s: sha256 %s, expected %s (PLAN.md §2.4)" % (path, digest, BY_SLUG[slug][2]))
    return path


def select(names):
    if not names:
        return [m[0] for m in MODULES]
    bad = [n for n in names if n not in BY_SLUG]
    if bad:
        raise SystemExit("unknown module(s) %s; known: %s" % (bad, ", ".join(BY_SLUG)))
    return names


def start_ghidra(maxmem=None):
    """Start the JVM through PyGhidra (idempotent)."""
    os.environ.setdefault("JAVA_HOME", JAVA_HOME)
    import pyghidra
    if pyghidra.started():
        return pyghidra
    launcher = pyghidra.HeadlessPyGhidraLauncher(install_dir=GHIDRA_HOME)
    mem = maxmem or os.environ.get("GHIDRA_MAXMEM", "8G")
    launcher.add_vmargs("-Xmx" + mem, "-XX:+UseG1GC", "-Djava.awt.headless=true")
    launcher.start()
    return pyghidra
