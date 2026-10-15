"""GOG's offline installer, read with innoextract (docs/reference/firstrun.md §3).

The app ships one innoextract binary (tools/firstrun/innoextract/build.sh:
upstream master 6e9e34e, which reads Inno Setup up to 6.4 and re-assembles
GOG Galaxy file parts; static Boost, liblzma and bzip2). Only the base game's
folder and GOG's metadata files are extracted, never The Art of Conquest's
or the manuals: about 413 MB.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import paths
import gogdb
from progress import Failure

ENV = "EE_INNOEXTRACT"
# What to extract: path components innoextract matches case-insensitively
# (its --include). "Empire Earth" is the base game's folder; the expansion's
# is "Empire Earth - The Art of Conquest", a different component.
INCLUDES = ("Empire Earth", gogdb.SCRIPT, gogdb.HASHDB, gogdb.INFO, gogdb.GOGLOG)
_FILE_LINE = re.compile(r'^\s*-\s+"(.+?)"')


def find_binary(explicit=None):
    """--innoextract, $EE_INNOEXTRACT, the app's bin/innoextract, the developer
    build in ee-build-cache, then PATH (Homebrew's 1.9 reads Inno Setup only to
    6.0.5, so it is the last resort)."""
    candidates = [explicit, os.environ.get(ENV), paths.ROOT / "bin/innoextract"]
    cache = Path(os.environ.get("EE_BUILD_CACHE", paths.ROOT.parent / "ee-build-cache")) / "innoextract"
    if cache.is_dir():
        candidates += sorted(cache.glob("innoextract-*/bin/innoextract"), reverse=True)
    candidates.append(shutil.which("innoextract"))
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


def _run(binary, args, timeout=None):
    return subprocess.run([str(binary)] + args, capture_output=True, text=True, errors="replace",
                          timeout=timeout)


def _failure_from(output, installer):
    """innoextract's complaint, as a Failure the player can act on."""
    text = output.strip().splitlines()
    tail = " ".join(text[-3:])[:400]
    low = output.lower()
    if "slice" in low or ".bin" in low:
        return Failure("installer-parts-missing",
                       "The installer's data files are missing or incomplete. GOG's installer comes as "
                       "%s plus one or more %s-1.bin files: download all of them into the same folder "
                       "and choose the .exe. (innoextract: %s)" % (installer.name, installer.stem, tail))
    if "not a supported inno setup installer" in low:
        return Failure("installer-unreadable",
                       "%s is not an installer this app can read. Choose GOG's offline installer "
                       "for Empire Earth Gold (setup_empire_earth_gold_*.exe)." % installer.name)
    return Failure("installer-unreadable",
                   "The installer could not be read; it may be damaged (download it again from "
                   "GOG). innoextract said: %s" % tail)


def check(binary, installer):
    """The installer's GOG game id and the files it would extract. Raises
    Failure when it is not GOG's Empire Earth Gold, or unreadable."""
    installer = Path(installer)
    r = _run(binary, ["--gog-game-id", str(installer)], timeout=120)
    if r.returncode != 0:
        raise _failure_from(r.stdout + r.stderr, installer)
    ids = re.findall(r"\b\d{6,}\b", r.stdout)
    if ids and gogdb.GAME_ID not in ids:
        raise Failure("wrong-version", "%s is GOG's installer for another game (GOG id %s), not "
                      "Empire Earth Gold (%s)." % (installer.name, ids[0], gogdb.GAME_ID))
    r = _run(binary, ["--list", "--color=0", "--progress=0"] + _include_args() + [str(installer)],
             timeout=300)
    if r.returncode != 0:
        raise _failure_from(r.stdout + r.stderr, installer)
    files = [m.group(1) for m in map(_FILE_LINE.match, r.stdout.splitlines()) if m]
    if not any(f.lower().endswith("/empire earth.exe") for f in files):
        raise Failure("wrong-version", "%s does not contain Empire Earth.exe: it is not GOG's "
                      "Empire Earth Gold installer." % installer.name)
    return {"gog_game_id": ids[0] if ids else None, "files": files}


def _include_args():
    out = []
    for inc in INCLUDES:
        out += ["--include", inc]
    return out


def extract(binary, installer, out_dir, on_file=None):
    """Extract the base game into out_dir (emptied first). on_file(done, name)
    is called per file. Returns out_dir."""
    installer, out_dir = Path(installer), Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    cmd = [str(binary), "--extract", "--output-dir", str(out_dir), "--color=0", "--progress=0",
           "--timestamps", "none"] + _include_args() + [str(installer)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", start_new_session=True)
    output, done = [], 0
    try:
        for line in proc.stdout:
            output.append(line)
            m = _FILE_LINE.match(line)
            if m:
                done += 1
                if on_file:
                    on_file(done, m.group(1))
        rc = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if rc != 0:
        raise _failure_from("".join(output[-40:]), installer)
    # innoextract only warns when a file's checksum does not match (exit 0)
    bad = [l for l in output if "checksum mismatch" in l.lower()]
    if bad:
        raise Failure("installer-unreadable",
                      "The installer is damaged (%d file%s failed its checksum): download the .exe "
                      "and its .bin files again from GOG." % (len(bad), "" if len(bad) == 1 else "s"))
    return out_dir
