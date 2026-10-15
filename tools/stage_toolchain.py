#!/usr/bin/env python3
"""Stage the toolchain EmpireEarth.app bundles for the first run (P6-B1).

    .venv/bin/python tools/stage_toolchain.py [--kit DIR] [--out build/toolchain]
        [--innoextract DIR] [stage.py options]

The kit's tools/toolchain/stage.py does the work: CPython + capstone + pefile,
clang and ld64.lld built from the LLVM source release (AArch64 only, one
binary, ThinLTO + PGO), THIRD_PARTY_NOTICES.txt for the toolchain and the
macOS host, MANIFEST.json. Every input is pinned by SHA-256; nothing of the
game is read. Downloads are cached in ~/Library/Caches/recomp-kit/toolchain
(--cache); the LLVM build takes about 20 minutes and 2.6 GB while it runs.

--innoextract DIR names the output of tools/firstrun/innoextract/build.sh (the
directory holding bin/, licenses/ and build.json): the app carries that binary
too, so its four components' licences join the notices. The output (ignored,
under build/) is what the app carries in Contents/Resources/toolchain/
(docs/reference/build.md, "The bundled toolchain").
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitpath  # noqa: E402

# What innoextract's build links in (firstrun.md §3.1), and the licence files build.sh writes.
INNOEXTRACT = (
    ("innoextract", "innoextract %(innoextract)s", "Zlib", "https://github.com/dscharrer/innoextract",
     ["innoextract-LICENSE.txt"]),
    ("boost", "Boost %(boost)s (iostreams, filesystem, date_time, system, program_options)", "BSL-1.0",
     "https://www.boost.org", ["boost-LICENSE_1_0.txt"]),
    ("xz", "XZ Utils %(xz)s (liblzma)", "0BSD", "https://tukaani.org/xz/", ["xz-COPYING.0BSD.txt", "xz-COPYING.txt"]),
    ("bzip2", "bzip2 %(bzip2)s", "bzip2-1.0.6", "https://sourceware.org/bzip2/", ["bzip2-LICENSE.txt"]),
)


def innoextract_notices(directory, out):
    """Write the notices.load_extra JSON for an innoextract build into `out`; return its path."""
    build = json.loads((directory / "build.json").read_text())
    versions = {name: (s.get("version") or s.get("commit", "")[:7]) for name, s in build["sources"].items()}
    components = [{"key": key, "title": title % versions, "licence": licence, "source": source,
                   "shipped": ("the bundled innoextract binary (%s)" if key == "innoextract"
                               else "statically inside the bundled innoextract (%s)") % build["id"],
                   "texts": [str((directory / "licenses" / name).resolve()) for name in files]}
                  for key, title, licence, source, files in INNOEXTRACT]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(components, indent=1) + "\n")
    return out


def main(argv):
    kit, rest = kitpath.take_kit(argv)
    if "--innoextract" in rest:
        i = rest.index("--innoextract")
        directory = Path(rest[i + 1]).expanduser().resolve()
        rest = rest[:i] + rest[i + 2:]
        spec = innoextract_notices(directory, kitpath.ROOT / "build" / "toolchain-notices" / "innoextract.json")
        rest += ["--notices-extra", str(spec)]
    else:
        print("stage_toolchain: no --innoextract: the notices leave innoextract out", file=sys.stderr)
    if not any(a == "--out" or a.startswith("--out=") for a in rest):
        rest = ["--out", str(kitpath.ROOT / "build" / "toolchain")] + rest
    return subprocess.call([sys.executable, str(kit / "tools/toolchain/stage.py")] + rest, cwd=kitpath.ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
