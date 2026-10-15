"""Import the player's game into <home>/Game (docs/reference/firstrun.md §4).

    locate -> [extract the installer] -> check modules and presence -> copy and
    hash every file -> publish by rename -> seed the registry -> game-path.txt

The copy is staged in <home>/Game.partial and published by two renames, so
<home>/Game is always either the previous complete import or the new one.
An interrupted import resumes: files already staged are re-hashed and kept
when they match. On the same APFS volume (a Wine wrapper, an installer just
extracted) files are cloned or moved, not duplicated.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import time

import paths
import gogdb
import inno
import sources
import verify
from progress import Failure

SCHEMA = 1
CHUNK = 1 << 20


def recover(home):
    """Undo a publish that stopped between its two renames."""
    if not home.game_root.exists() and home.game_old.exists():
        home.game_old.rename(home.game_root)
    elif home.game_old.exists():
        shutil.rmtree(home.game_old, ignore_errors=True)


# ---------------------------------------------------------------- copying --

_libc = None


def _clonefile(src, dst):
    """APFS clonefile(2): a copy that shares blocks until either side changes.
    False when the volume or the pair cannot clone."""
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            _libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        return _libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0
    except (OSError, AttributeError):
        return False


def _hash(path, want_sha256):
    md5, sha = hashlib.md5(), hashlib.sha256() if want_sha256 else None
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            md5.update(b)
            if sha is not None:
                sha.update(b)
    return md5.hexdigest(), sha.hexdigest() if sha is not None else None


def _copy_hash(src, dst, want_sha256):
    md5, sha = hashlib.md5(), hashlib.sha256() if want_sha256 else None
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            b = fi.read(CHUNK)
            if not b:
                break
            fo.write(b)
            md5.update(b)
            if sha is not None:
                sha.update(b)
    return md5.hexdigest(), sha.hexdigest() if sha is not None else None


def place(src, dst, move=False, want_sha256=False):
    """Put src at dst (atomically: via dst.part) and return (md5, sha256, how)."""
    part = dst.with_name(dst.name + ".part")
    part.unlink(missing_ok=True)
    how = None
    if move:
        try:
            os.rename(src, part)
            how = "moved"
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
    if how is None and _clonefile(src, part):
        how = "cloned"
    if how is None:
        md5, sha = _copy_hash(src, part, want_sha256)
        how = "copied"
    else:
        md5, sha = _hash(part, want_sha256)
    os.chmod(part, 0o644)
    os.replace(part, dst)
    return md5, sha, how


# ------------------------------------------------------------------ import --

def _choose(path, reporter):
    found = sources.locate(path)
    if not found:
        code, message = sources.explain(path)
        raise Failure(code, message)
    return found


def _extract(home, src, binary_hint, reporter):
    binary = inno.find_binary(binary_hint)
    if binary is None:
        raise Failure("installer-tool-missing", "No innoextract to read the installer with "
                      "(set EE_INNOEXTRACT, or build it: tools/firstrun/innoextract/build.sh).")
    installer = src.installer[0]
    reporter.progress("extract", 0, 1, "Reading %s" % installer.name)
    listing = inno.check(binary, installer)
    total = len(listing["files"])
    out = home.game_partial / "extract"
    reporter.log("innoextract %s: %d files to extract" % (binary, total))

    def on_file(done, name):
        reporter.progress("extract", done, total, "Extracting %s" % name.rsplit("/", 1)[-1],
                          unit="files", force=False)
    inno.extract(binary, installer, out, on_file=on_file)
    games = sources.find_games(out, depth=4)
    if not games:
        raise Failure("installer-unreadable", "The installer held no Empire Earth.exe.")
    extracted = sources._game_source(games[0], kind="installer", origin=installer)
    extracted.installer = src.installer
    reporter.progress("extract", total, total, "Extracted %d files" % total, unit="files")
    return extracted


def run_import(home, path, reporter, allow_modified=False, innoextract=None, width=1920, height=1080,
               player_name=None):
    """Import from path; returns the import record (also <home>/Game/import.json)."""
    home.root.mkdir(parents=True, exist_ok=True)
    recover(home)
    manifest = verify.load_manifest()
    modules = verify.load_modules()
    reporter.progress("locate", 0, 1, "Looking for Empire Earth in %s" % path)
    found = _choose(path, reporter)
    src, failure = None, None
    for cand in found:
        if cand.kind == "installer":
            src = cand
            break
        q = verify.quick(cand.game_dir, manifest, modules)
        if q["ok"]:
            src = cand
            break
        failure = failure or Failure(q["code"], q["message"], {"folder": q["folder"]})
    if src is None:
        raise failure
    for note in src.notes:
        reporter.warning("locate", note)
    reporter.progress("locate", 1, 1, "Found %s: %s" % (src.kind, src.game_dir or src.installer[0]))

    t0 = time.time()
    move = False
    if src.kind == "installer":
        src = _extract(home, src, innoextract, reporter)
        move = True                                # our own extraction: move, do not copy

    files = verify.index(src.game_dir)
    reporter.progress("verify", 0, len(modules), "Checking the game's version", unit="files")
    module_report = verify.check_modules(files, modules, manifest)      # raises wrong-version
    missing, short, missing_optional = verify.check_presence(files, manifest)
    if missing or short:
        raise Failure("partial-copy", verify.partial_message(missing, short),
                      {"missing": missing, "short": short})
    reporter.progress("verify", len(modules), len(modules), "GOG Empire Earth Gold (all nine modules match)",
                      unit="files")
    their_hashdb = gogdb.read_hashdb(src.gold_dir / gogdb.HASHDB) if src.gold_dir else None
    hashdb_diff = verify.compare_hashdb(their_hashdb, manifest)
    if hashdb_diff:
        reporter.warning("verify", "This install's GOG file list differs from the one this app knows "
                         "in %d files (%s): a different GOG build." % (len(hashdb_diff), ", ".join(hashdb_diff[:3])))

    # copy, hashing every file
    stage = home.game_partial / manifest["base"]
    stage.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(size for rel, (size, _) in manifest["files"].items() if rel.lower() in files)
    done_bytes, modified, optional_modified, ways = 0, [], [], {}
    module_sha = {name.lower(): sha for name, sha in modules}
    for rel, (size, md5) in sorted(manifest["files"].items()):
        p = files.get(rel.lower())
        if p is None:
            continue
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        want_sha = rel.lower() in module_sha
        got = None
        if dst.is_file() and dst.stat().st_size == size:
            got, _ = _hash(dst, False)            # staged by an interrupted run
            how = "kept"
        if got != md5:
            got, sha, how = place(os.path.realpath(p), dst, move=move, want_sha256=want_sha)
            if want_sha and sha != module_sha[rel.lower()]:
                raise Failure("wrong-version", "%s changed while it was being copied." % rel)
        ways[how] = ways.get(how, 0) + 1
        if got != md5:
            (optional_modified if verify.is_optional(rel) else modified).append(rel)
        done_bytes += size
        reporter.progress("copy", done_bytes, total_bytes, "Copying %s" % rel, unit="bytes", force=False)
    reporter.progress("copy", total_bytes, total_bytes, "Copied %d files (%s)" %
                      (sum(ways.values()), ", ".join("%d %s" % (n, w) for w, n in sorted(ways.items()))),
                      unit="bytes")
    for rel in optional_modified:
        reporter.warning("verify", "%s differs from GOG's; the native game does not use it." % rel)
    for rel in missing_optional:
        reporter.warning("verify", "%s is missing; the native game does not use it." % rel)
    if modified and (src.kind == "installer" or not allow_modified):
        shutil.rmtree(home.game_partial, ignore_errors=True)    # a rejected copy is worth nothing
    if modified and src.kind == "installer":
        raise Failure("installer-unreadable",
                      "%d file%s from the installer differ from GOG's Empire Earth Gold (%s): the "
                      "download is damaged or it is another build. Download it again from GOG."
                      % (len(modified), "" if len(modified) == 1 else "s", ", ".join(modified[:3])),
                      {"modified": modified})
    if modified and not allow_modified:
        raise Failure("modified-files",
                      "%d game file%s differ from GOG's Empire Earth Gold (%s): modded or damaged. "
                      "Import a fresh GOG installation or the installer; a modified game goes out of "
                      "sync with other players." % (len(modified), "" if len(modified) == 1 else "s",
                                                    ", ".join(modified[:3]) + (", ..." if len(modified) > 3 else "")),
                      {"modified": modified})
    for rel in modified:
        reporter.warning("verify", "%s differs from GOG's (kept: --allow-modified)." % rel)
    for d in manifest.get("dirs", []):
        (stage / d).mkdir(parents=True, exist_ok=True)

    # the player's own files go to the profile, where the game writes
    extra = verify.player_files(files, manifest)
    carried = 0
    for rel in extra:
        target = home.root / files[rel].relative_to(src.game_dir)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(files[rel], target)
        carried += 1
    if carried:
        reporter.progress("copy", 1, 1, "Carried over %d of your saved games, scenarios and profiles" % carried)

    # registry: options + the CD key from the player's own GOG files (never logged)
    kind, reg_path, hklm = gogdb.find_registry_source(src.gold_dir, src.wine_prefix)
    if kind is None:
        reporter.warning("registry", "No GOG install script (goggame-%s.script) or goglog.ini with "
                         "the CD key was found; the game starts, but multiplayer may refuse it. "
                         "Import the \"Empire Earth Gold\" folder or the installer to include it."
                         % gogdb.GAME_ID)
    seed_registry(home, hklm, width, height, player_name)
    reporter.progress("registry", 1, 1, "Registry seeded (%s; CD key %s)"
                      % (kind or "no GOG script", "present" if gogdb.has_cd_key(hklm) else "missing"))

    record = {
        "schema": SCHEMA,
        "imported": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seconds": round(time.time() - t0, 1),
        "source": {"kind": src.kind, "path": str(src.path)},
        "product": manifest.get("product"),
        "files": len(manifest["files"]) - len(missing_optional),
        "bytes": total_bytes,
        "modules": {r["name"]: r["sha256"] for r in module_report},
        "missing_optional": missing_optional,
        "optional_modified": optional_modified,
        "modified_allowed": modified,
        "hashdb": None if hashdb_diff is None else {"differs": hashdb_diff},
        "player_files": carried,
        "registry": {"source": kind, "cd_key": gogdb.has_cd_key(hklm)},
    }
    (home.game_partial / "import.json").write_text(json.dumps(record, indent=1) + "\n")
    shutil.rmtree(home.game_partial / "extract", ignore_errors=True)
    publish(home)
    write_game_path(home)
    reporter.progress("publish", 1, 1, "Imported into %s" % home.game)
    return record


def publish(home):
    """Game.partial -> Game, keeping a complete Game at every instant but
    between the two renames (recover() finishes that case)."""
    if home.game_root.exists():
        shutil.rmtree(home.game_old, ignore_errors=True)
        home.game_root.rename(home.game_old)
    home.game_partial.rename(home.game_root)
    shutil.rmtree(home.game_old, ignore_errors=True)


def write_game_path(home):
    """The kit host's saved game path (host/game_path.cpp): the imported EXE."""
    tmp = home.game_path.with_suffix(".txt.tmp")
    tmp.write_text(str(home.game / "Empire Earth.exe") + "\n")
    os.replace(tmp, home.game_path)


def default_player_name():
    try:
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (KeyError, OSError):
        full = ""
    name = full.split()[0] if full else ""
    return (name or "Player")[:20]


def seed_registry(home, hklm, width, height, player_name=None):
    """<home>/registry.json: tools/boot/seed_user.py's options plus the HKLM
    values; merged, so the game's own saved options win."""
    import seed_user
    seed = seed_user.defaults(width, height, None)
    sssi = seed[seed_user.HKCU_SSSI]
    sssi["Player Name"] = seed_user.value(player_name or default_player_name())
    for key, values in hklm.items():
        seed.setdefault(key, {}).update(values)
        for parent in seed_user.parents(key):
            seed.setdefault(parent, {})
    existing = {}
    if home.registry.is_file():
        try:
            existing = json.loads(home.registry.read_text())
        except ValueError:
            existing = {}
    added = seed_user.merge(existing, seed)
    tmp = home.registry.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, home.registry)
    return added


def status(home):
    """The import's state for `status`."""
    recover(home)
    out = {"imported": home.import_record.is_file(), "game": str(home.game),
           "partial": home.game_partial.exists()}
    if out["imported"]:
        try:
            rec = json.loads(home.import_record.read_text())
        except ValueError:
            rec = {}
        out["record"] = {k: rec.get(k) for k in ("imported", "source", "files", "bytes", "registry")}
        out["exe_present"] = (home.game / "Empire Earth.exe").is_file()
    return out
