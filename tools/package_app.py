#!/usr/bin/env python3
"""Build the shareable EmpireEarth.app, its zip and its DMG (Phase 6, P6-B3).

    .venv/bin/python tools/package_app.py [--out build/dist] [--kit DIR]
        [--toolchain DIR] [--innoextract DIR] [--toolchain-cache DIR]
        [--no-host-build] [--no-archives] [--release DIR] [--jobs N]

The app holds nothing game-derived (docs/reference/packaging.md §6). On a
player's Mac its first run imports their own GOG files, translates and
compiles them with the bundled toolchain, then plays
(docs/reference/firstrun.md):

  Contents/MacOS/EmpireEarth        the launcher (app/launcher/launcher.m):
                                    the first-run window, then exec() of
  Contents/MacOS/EmpireEarthHost    the kit's player host: the SDL/Metal host
                                    and runtime with no translation linked in
                                    (kit host/code_dir.h), EE's stand-ins in
  Contents/Frameworks/              FFmpeg's dylibs (LGPL, replaceable)
  Contents/Resources/toolchain/     Python + capstone + pefile, clang + ld64.lld
                                    (tools/stage_toolchain.py, symlinks kept)
  Contents/Resources/ee/            what firstrun.py needs, in a repository's
                                    shape: tools/{firstrun,inputs,boot}, kitpath,
                                    game.toml, globals.toml, db/, the kit's
                                    translator, bundle driver and runtime
                                    headers, bin/innoextract
  Contents/Resources/THIRD_PARTY_NOTICES.txt, AppIcon.icns, the host's own
                                    resources (SoundFont, mode list, FFmpeg's
                                    notice)

Steps: build the player host (kit tools/build.py --target player, a tree
with no translation), compile the launcher and draw the icon (the Xcode
clang of this Mac: players need none), assemble, write the notices (the kit's
tools/toolchain/notices.py, with innoextract's components), sign ad hoc
inside out with no hardened runtime (packaging.md §5), run the packaging
check (tools/check_app.py), then zip (ditto) and DMG (hdiutil; the app, an
Applications link and app/ReadMeFirst.txt as "Read Me First.txt").

Inputs: --toolchain (default build/toolchain, from tools/stage_toolchain.py),
--innoextract (the directory tools/firstrun/innoextract/build.sh wrote;
default the newest upstream build in $EE_BUILD_CACHE, else
../ee-build-cache), --toolchain-cache (the downloads the notices are read
from; default ~/Library/Caches/recomp-kit/toolchain).
Outputs (all under build/, ignored): <out>/EmpireEarth.app, EmpireEarth.zip,
EmpireEarth.dmg, check.json. --release DIR also copies the zip and the DMG
there as EmpireEarth-<VERSION>.zip/.dmg, with the FFmpeg source archive the
host was built from (LGPL-2.1: it goes wherever the app is published,
packaging.md §7) and their SHA256SUMS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_app  # noqa: E402
import kitpath  # noqa: E402
import stage_toolchain  # noqa: E402

APP = "EmpireEarth"
DISPLAY = "Empire Earth"
HOST = "EmpireEarthHost"
MIN_MACOS = "13.0"
# CFBundleShortVersionString. CFBundleVersion is <commits>.<UTC yymmddHHMM>
# of the build, so every build has its own.
VERSION = "0.8.1"
BUNDLE_ID = tomllib.loads((ROOT / "game.toml").read_text())["game"]["bundle_id"]
README = ROOT / "app/ReadMeFirst.txt"

# This repository's part of the first run (firstrun.md §6), relative to ROOT.
EE_FILES = ("game.toml", "globals.toml", "db/inputs.json", "db/gog-files.json", "tools/kitpath.py",
            "tools/boot/seed_user.py")
EE_GLOBS = ("db/functions/*.json", "tools/inputs/*.py", "tools/firstrun/*.py")
# Development tools beside them that the first run never runs.
EE_EXCLUDE = ("tools/firstrun/make_test_installer.py", "tools/firstrun/manifest.py")
# The kit's: the translator, the bundle driver, the game config reader.
KIT_FILES = ("tools/recomp/translate.py", "tools/recomp/bundle.py", "tools/game_config.py")


def run(cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def default_innoextract():
    cache = Path(os.environ.get("EE_BUILD_CACHE", ROOT.parent / "ee-build-cache")) / "innoextract"
    for base in (cache, Path("/Users/Shared/ee-build-cache/innoextract")):
        found = sorted(base.glob("innoextract-6e9e34e*/bin/innoextract"), reverse=True) if base.is_dir() else []
        if found:
            return found[0].parents[1]
    # the default checkout layout: ~/Code/<repo> beside ~/Code/ee-build-cache
    for parent in ROOT.parents:
        cand = sorted((parent / "ee-build-cache/innoextract").glob("innoextract-6e9e34e*/bin/innoextract"),
                      reverse=True)
        if cand:
            return cand[0].parents[1]
    return None


def ee_sources(kit):
    """[(source, path under Resources/ee)]: everything firstrun.py runs."""
    out = [(ROOT / f, f) for f in EE_FILES]
    for pattern in EE_GLOBS:
        out += [(p, p.relative_to(ROOT).as_posix()) for p in sorted(ROOT.glob(pattern))
                if p.relative_to(ROOT).as_posix() not in EE_EXCLUDE]
    out += [(kit / f, "kit/" + f) for f in KIT_FILES]
    # The runtime headers the generated C includes: the cache key's include
    # closure (tools/firstrun/pipeline.py), from wherever bundle.py's -I finds them.
    sys.path.insert(0, str(ROOT / "tools/firstrun"))
    import pipeline  # noqa: E402
    for name, path in sorted(pipeline._include_closure(kit).items()):
        out.append((path, "kit/" + path.relative_to(kit).as_posix()))
    missing = [str(s) for s, _ in out if not s.is_file()]
    if missing:
        sys.exit("package_app: missing %s" % ", ".join(missing))
    return out


def build_host(kit_args, jobs):
    """The kit's player host, in a build tree with no translation at all."""
    run([sys.executable, ROOT / "tools/build.py", "--target", "player", "--jobs", jobs] + kit_args, cwd=ROOT)
    app = ROOT / "build/player" / (APP + ".app")
    if not (app / "Contents/MacOS" / APP).is_file():
        sys.exit("package_app: the player host build did not produce %s" % app)
    return app


def compile_launcher(work):
    work.mkdir(parents=True, exist_ok=True)
    common = ["xcrun", "clang", "-fobjc-arc", "-fmodules", "-arch", "arm64",
              "-mmacosx-version-min=" + MIN_MACOS, "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
              "-framework", "AppKit"]
    launcher = work / APP
    run(common + ["-o", launcher, ROOT / "app/launcher/launcher.m"])
    run(["strip", "-x", launcher])
    tool = work / "make_icon"
    run(common + ["-o", tool, ROOT / "app/launcher/make_icon.m"])
    iconset = work / "AppIcon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    run([tool, iconset])
    icns = work / "AppIcon.icns"
    run(["iconutil", "-c", "icns", "-o", icns, iconset])
    return launcher, icns


def version_info(kit):
    def git(*args, cwd=ROOT):
        r = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    now = time.gmtime()
    return {"describe": git("describe", "--always", "--dirty"), "count": git("rev-list", "--count", "HEAD"),
            "kit": git("describe", "--always", "--dirty", cwd=kit),
            "stamp": time.strftime("%y%m%d%H%M", now), "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", now)}


def bundle_version(version):
    """CFBundleVersion: the commit count, then the build's minute (UTC)."""
    return "%s.%s" % (version.get("count") or "0", version.get("stamp") or "0")


def info_plist(version):
    return {
        "CFBundleName": APP,
        "CFBundleDisplayName": DISPLAY,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP,
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": bundle_version(version),
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleDevelopmentRegion": "en",
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSArchitecturePriority": ["arm64"],
        "LSApplicationCategoryType": "public.app-category.strategy-games",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        # macOS 15 asks the player before an app may use the local network
        # (Local Network privacy); until they allow it, the LAN game's
        # connections fail with EHOSTUNREACH and other machines' broadcasts
        # never arrive (lansession.md §9). This is the prompt's text.
        "NSLocalNetworkUsageDescription": "Empire Earth finds, hosts and joins multiplayer games "
                                          "on your local network (LAN games, and VPNs such as "
                                          "ZeroTier).",
        "NSHumanReadableCopyright": "Our code only: the game is the player's own GOG copy, "
                                    "imported and built on their Mac.",
        "EEBuild": {"repository": version["describe"], "kit": version["kit"],
                    "built": version.get("built", "")},
    }


def macho_files(app):
    """Every Mach-O file in the app (not symlinks), deepest first."""
    out = []
    for p, rel, st in check_app.files_of(app):
        if p.is_symlink() or not p.is_file():
            continue
        if check_app.head(p, 4) in check_app.MACHO_MAGICS:
            out.append(p)
    return sorted(out, key=lambda p: (-len(p.parts), str(p)))


def verifies(path):
    return subprocess.run(["codesign", "--verify", "--strict", str(path)], capture_output=True).returncode == 0


def sign(app):
    """Ad hoc, inside out, no hardened runtime (packaging.md §5). The
    toolchain's and innoextract's binaries arrive signed and are kept as they
    are (their bytes stay those MANIFEST.json and build.json record); our own
    executables and FFmpeg are signed here; then the bundle is sealed."""
    run(["xattr", "-cr", app])
    contents = app / "Contents"
    for p in macho_files(app):
        ours = p.parent in (contents / "MacOS", contents / "Frameworks")
        if ours or not verifies(p):
            run(["codesign", "--force", "--sign", "-", "--timestamp=none", p])
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", app])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])


def assemble(out, host_app, launcher, icns, toolchain, innoextract, notices, kit, version):
    app = out / (APP + ".app")
    if app.exists():
        shutil.rmtree(app)
    out.mkdir(parents=True, exist_ok=True)
    run(["ditto", host_app, app])
    contents = app / "Contents"
    macos = contents / "MacOS"
    (macos / APP).rename(macos / HOST)
    # Debug stabs name this build's object files: dropped. Every symbol stays
    # (the bundles resolve the runtime's entry points in it).
    run(["strip", "-S", macos / HOST])
    shutil.copy2(launcher, macos / APP)
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    for stale in ("symbols.json", "texture-pack"):
        if (resources / stale).exists():
            sys.exit("package_app: the player host carries %s" % stale)
    # The toolchain, symlinks kept; its notices are replaced by the app's.
    run(["ditto", toolchain, resources / "toolchain"])
    (resources / "toolchain/THIRD_PARTY_NOTICES.txt").unlink(missing_ok=True)
    shutil.copy2(notices, resources / "THIRD_PARTY_NOTICES.txt")
    ee = resources / "ee"
    for source, rel in ee_sources(kit):
        dest = ee / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        dest.chmod(0o644)
    (ee / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(innoextract / "bin/innoextract", ee / "bin/innoextract")
    shutil.copy2(icns, resources / "AppIcon.icns")
    (contents / "Info.plist").write_bytes(plistlib.dumps(info_plist(version)))
    return app


def write_notices(kit, toolchain, innoextract, cache, work):
    spec = stage_toolchain.innoextract_notices(innoextract, work / "innoextract-notices.json")
    out = work / "THIRD_PARTY_NOTICES.txt"
    run([sys.executable, kit / "tools/toolchain/notices.py", "--cache", cache, "--python", toolchain / "python",
         "--out", out, "--extra", spec], cwd=kit)
    return out


def archives(app, out):
    """EmpireEarth.zip (ditto keeps symlinks and the signature) and
    EmpireEarth.dmg (the app and an Applications link, to drag onto, and
    "Read Me First.txt")."""
    z = out / (APP + ".zip")
    z.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, z])
    dmg = out / (APP + ".dmg")
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        stage = Path(tmp) / DISPLAY
        stage.mkdir()
        run(["ditto", app, stage / app.name])
        (stage / "Applications").symlink_to("/Applications")
        shutil.copyfile(README, stage / "Read Me First.txt")
        run(["hdiutil", "create", "-quiet", "-volname", DISPLAY, "-srcfolder", stage, "-ov",
             "-format", "ULMO", "-fs", "APFS", dmg])
    return z, dmg


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ffmpeg_pin(kit):
    """(archive name, SHA-256) of the FFmpeg source the kit's CMake builds."""
    m = re.search(r"URL https://ffmpeg\.org/releases/(ffmpeg-[0-9.]+\.tar\.xz)\s+URL_HASH SHA256=([0-9a-f]{64})",
                  (kit / "cmake/Dependencies.cmake").read_text())
    if not m:
        sys.exit("package_app: no FFmpeg pin in %s" % (kit / "cmake/Dependencies.cmake"))
    return m.group(1), m.group(2)


def ffmpeg_source(kit):
    """The FFmpeg source archive the player host's build tree downloaded,
    checked against the pin. The LGPL asks for the exact source of the
    shipped dylibs; it goes beside the app wherever that is published."""
    name, digest = ffmpeg_pin(kit)
    path = ROOT / "build/cmake/macos-player/ffmpeg-prefix/src" / name
    if not path.is_file() or sha256(path) != digest:
        sys.exit("package_app: %s is missing or is not the pinned %s" % (path, name))
    return path


def release(files, dest, sources=()):
    """Copies (never moves) the archives into `dest` as EmpireEarth-<VERSION>.<ext>
    and `sources` under their own names, and writes SHA256SUMS beside them, in
    shasum -a 256's format."""
    dest.mkdir(parents=True, exist_ok=True)
    copies = [dest / ("%s-%s%s" % (APP, VERSION, f.suffix)) for f in files] + [dest / f.name for f in sources]
    for f, c in zip(list(files) + list(sources), copies):
        shutil.copy2(f, c)
    (dest / "SHA256SUMS").write_text("".join("%s  %s\n" % (sha256(c), c.name) for c in copies))
    return copies


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "build/dist")
    ap.add_argument("--toolchain", type=Path, default=ROOT / "build/toolchain")
    ap.add_argument("--innoextract", type=Path, default=None)
    ap.add_argument("--toolchain-cache", type=Path,
                    default=Path.home() / "Library/Caches/recomp-kit/toolchain")
    ap.add_argument("--no-host-build", action="store_true", help="use build/player/EmpireEarth.app as it is")
    ap.add_argument("--no-archives", action="store_true")
    ap.add_argument("--release", type=Path, default=None,
                    help="also copy the zip and DMG there as EmpireEarth-%s.*, with FFmpeg's source "
                         "and SHA256SUMS" % VERSION)
    ap.add_argument("--jobs", default=str(min(os.cpu_count() or 4, 12)))
    kit, rest = kitpath.take_kit(list(sys.argv[1:] if argv is None else argv))
    args = ap.parse_args(rest)
    if args.release and args.no_archives:
        ap.error("--release needs the archives")
    kit_args = ["--kit", str(kit)] if kit != ROOT / "kit" else []
    out = args.out.resolve()
    if ROOT / "build" not in out.parents and out != ROOT / "build":
        print("package_app: note: %s is outside build/; it holds no game files either way" % out)
    toolchain = args.toolchain.resolve()
    if not (toolchain / "python/bin/python3").is_file() or not (toolchain / "llvm/bin/clang").exists():
        sys.exit("package_app: no staged toolchain in %s: run tools/stage_toolchain.py --innoextract DIR "
                 "(about 20 minutes), or pass --toolchain DIR" % toolchain)
    innoextract = (args.innoextract or default_innoextract())
    if not innoextract or not (Path(innoextract) / "bin/innoextract").is_file():
        sys.exit("package_app: no innoextract build: run tools/firstrun/innoextract/build.sh, or pass "
                 "--innoextract DIR")
    innoextract = Path(innoextract).resolve()
    if not args.toolchain_cache.is_dir():
        sys.exit("package_app: no toolchain downloads in %s (pass --toolchain-cache)" % args.toolchain_cache)

    host_app = ROOT / "build/player" / (APP + ".app") if args.no_host_build else build_host(kit_args, args.jobs)
    work = ROOT / "build/package"
    launcher, icns = compile_launcher(work)
    notices = write_notices(kit, toolchain, innoextract, args.toolchain_cache.resolve(), work)
    version = version_info(kit)
    app = assemble(out, host_app, launcher, icns, toolchain, innoextract, notices, kit, version)
    sign(app)

    print("\nPackaging check (tools/check_app.py):")
    problems, warnings, sizes = check_app.check(app, [ROOT / "analysis", ROOT / "build/recomp",
                                                      ROOT / "original"], repos=(ROOT, kit))
    total = check_app.report(sizes)
    for w in warnings:
        print("warning: " + w)
    result = {"app": str(app), "app_bytes": total, "sizes": sizes, "problems": problems,
              "warnings": warnings, "version": version}
    if problems:
        (out / "check.json").write_text(json.dumps(result, indent=1) + "\n")
        print("\nThe app must not ship:")
        for p in problems:
            print("  " + p)
        shutil.rmtree(app)
        return 1
    print("Nothing game-derived.")
    if not args.no_archives:
        z, dmg = archives(app, out)
        result.update(zip=str(z), zip_bytes=z.stat().st_size, dmg=str(dmg), dmg_bytes=dmg.stat().st_size)
        print("\n%s: %.1f MB\n%s: %.1f MB" % (z, z.stat().st_size / 1e6, dmg, dmg.stat().st_size / 1e6))
        if args.release:
            copies = release([dmg, z], args.release.resolve(), [ffmpeg_source(kit)])
            result.update(release=[str(c) for c in copies])
            print("\nRelease %s:" % VERSION)
            print((args.release.resolve() / "SHA256SUMS").read_text(), end="")
    (out / "check.json").write_text(json.dumps(result, indent=1) + "\n")
    print("\nBuilt %s (%.1f MB)" % (app, total / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
