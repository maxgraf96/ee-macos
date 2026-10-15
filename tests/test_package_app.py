"""The shareable app's packaging: what goes into Contents/Resources/ee, the
Info.plist, the release copies, the packaging check (tools/check_app.py) on
synthetic apps, and the launcher's own checks: game ports, quarantine and
App Translocation (packaging.md §5.1).

    .venv/bin/python -m pytest -q tests/test_package_app.py

No game files: the "game file" is a synthetic one listed in a synthetic
db/gog-files.json. tools/package_app.py itself is run by hand (it builds the
player host and needs the staged toolchain; docs/reference/packaging.md §8).
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_app  # noqa: E402
import kitpath  # noqa: E402
import package_app  # noqa: E402

KIT, _ = kitpath.take_kit([])


def fake_root(tmp_path, files=()):
    """A repository root with only a db/gog-files.json listing `files` (name, bytes)."""
    root = tmp_path / "root"
    (root / "db").mkdir(parents=True)
    entries = [[name, len(data), hashlib.md5(data).hexdigest()] for name, data in files]
    (root / "db/gog-files.json").write_text(json.dumps({"files": entries}))
    return root


def fake_app(tmp_path):
    app = tmp_path / "Test.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/ee/tools").mkdir(parents=True)
    shutil.copyfile("/bin/echo", app / "Contents/MacOS/Test")  # a Mach-O with nothing translated
    (app / "Contents/MacOS/Test").chmod(0o755)
    (app / "Contents/Resources/ee/tools/a.py").write_text("print('ours')\n" * 10)
    (app / "Contents/Info.plist").write_text("<plist/>\n")
    return app


def problems(app, root, against=(), repos=(), home="/nonexistent-home"):
    found, warnings, sizes = check_app.check(app, against, repos=repos, root=root, home=home)
    return found, warnings, sizes


def test_a_clean_app_passes_and_is_sized(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    found, warnings, sizes = problems(app, root)
    assert found == [] and warnings == []
    assert sizes["MacOS/Test"][1] == 1
    assert sum(s[1] for s in sizes.values()) == 3


def test_pe_images_are_found_whatever_their_name(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    pe[0x3c:0x40] = (0x80).to_bytes(4, "little")
    pe[0x80:0x84] = b"PE\0\0"
    (app / "Contents/Resources/harmless.dat").write_bytes(bytes(pe))
    (app / "Contents/Resources/not-pe.dat").write_bytes(b"MZ" + bytes(510))  # MZ, no PE header
    found, _, _ = problems(app, root)
    assert found == ["Contents/Resources/harmless.dat: a PE image"]


def test_translation_outputs_by_name(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    for rel in ("Contents/Resources/symbols.json", "Contents/Resources/x/funcdb.json",
                "Contents/Resources/x/chunk_001.c", "Contents/Resources/main.bundle",
                "Contents/Resources/analysis/notes.txt"):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_text("x" * 100)
    found, _, _ = problems(app, root)
    assert len(found) == 5, found
    assert any("symbols.json" in f for f in found)
    assert any("analysis" in f and "directory" in f for f in found)


def test_the_games_own_files_by_size_and_md5(tmp_path):
    data = b"a game file's bytes, " * 50
    app, root = fake_app(tmp_path), fake_root(tmp_path, [("Data/thing.dat", data)])
    (app / "Contents/Resources/renamed.bin").write_bytes(data)
    (app / "Contents/Resources/same-size.bin").write_bytes(b"x" * len(data))
    found, _, _ = problems(app, root)
    assert found == ["Contents/Resources/renamed.bin: the game's own Data/thing.dat"]


def test_the_same_bytes_as_an_analysis_output_unless_tracked(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    analysis = tmp_path / "analysis"
    (analysis / "kit/exe").mkdir(parents=True)
    listing = b"00401000  PUSH EBP\n" * 20
    (analysis / "kit/exe/0x401000.asm").write_bytes(listing)
    (app / "Contents/Resources/listing.txt").write_bytes(listing)
    # a virtual environment beside the analyses is not an output of them
    (analysis / "tool/.venv/lib").mkdir(parents=True)
    shutil.copy2(app / "Contents/Resources/ee/tools/a.py", analysis / "tool/.venv/lib/a.py")
    found, _, _ = problems(app, root, against=[analysis])
    assert len(found) == 1 and "listing.txt: the same bytes as" in found[0]
    # A file this repository tracks may have a copy under analysis/ (a kit header the
    # translation copies, say): that is ours.
    tracked = ROOT / "globals.toml"
    shutil.copy2(tracked, analysis / "kit/globals.toml")
    shutil.copy2(tracked, app / "Contents/Resources/ee/globals.toml")
    found, _, _ = problems(app, root, against=[analysis], repos=(ROOT,))
    assert len(found) == 1 and "listing.txt" in found[0]


def test_build_paths_are_a_warning(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    (app / "Contents/Resources/config.txt").write_text("--prefix=/Users/someone/build/x\n")
    found, warnings, _ = problems(app, root, home="/Users/someone")
    assert found == [] and len(warnings) == 1 and "home directory" in warnings[0]


@pytest.mark.skipif(not shutil.which("xcrun"), reason="needs a C compiler")
def test_translated_symbols_in_a_mach_o(tmp_path):
    app, root = fake_app(tmp_path), fake_root(tmp_path)
    src = tmp_path / "t.c"
    src.write_text("void fn_00401000(void) {}\nconst unsigned recomp_func_addrs[1] = {0x401000};\n"
                   "int main(void) { fn_00401000(); return 0; }\n")
    out = app / "Contents/MacOS/Translated"
    r = subprocess.run(["xcrun", "clang", "-o", str(out), str(src)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot compile: %s" % r.stderr.decode()[:200])
    found, _, _ = problems(app, root)
    assert any("translated symbols" in f and "fn_00401000" in f for f in found), found
    assert any("linked image table" in f for f in found), found


def test_the_first_run_files_the_app_carries():
    rels = {rel for _, rel in package_app.ee_sources(KIT)}
    for rel in ("tools/firstrun/firstrun.py", "tools/firstrun/pipeline.py", "tools/inputs/export.py",
                "tools/boot/seed_user.py", "tools/kitpath.py", "game.toml", "globals.toml",
                "db/inputs.json", "db/gog-files.json", "db/functions/empire-earth.exe.json",
                "kit/tools/recomp/translate.py", "kit/tools/recomp/bundle.py", "kit/tools/game_config.py",
                "kit/runtime/x86.h", "kit/runtime/freestanding.h"):
        assert rel in rels, rel
    assert "tools/firstrun/make_test_installer.py" not in rels
    assert not any(r.startswith(("analysis/", "build/", "original/")) for r in rels)
    assert not any(r.endswith("symbols.json") for r in rels)


def test_info_plist():
    plist = package_app.info_plist({"describe": "abc", "count": "7", "kit": "def", "stamp": "2610151730"})
    assert plist["CFBundleExecutable"] == "EmpireEarth"
    assert plist["LSMinimumSystemVersion"] == "13.0"
    assert plist["LSArchitecturePriority"] == ["arm64"]
    assert plist["CFBundleIconFile"] == "AppIcon"
    assert "local network" in plist["NSLocalNetworkUsageDescription"]
    assert plist["CFBundleShortVersionString"] == package_app.VERSION == "0.7"
    # every build its own: the commit count, then the build's minute
    assert plist["CFBundleVersion"] == "7.2610151730"


def test_every_build_has_its_own_number():
    v = package_app.version_info(KIT)
    assert v["count"].isdigit() and len(v["stamp"]) == 10 and v["stamp"].isdigit()
    assert package_app.bundle_version(v) == "%s.%s" % (v["count"], v["stamp"])


def test_the_release_copies_and_sums(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "EmpireEarth.dmg").write_bytes(b"dmg")
    (out / "EmpireEarth.zip").write_bytes(b"zip")
    dest = tmp_path / "release"
    copies = package_app.release([out / "EmpireEarth.dmg", out / "EmpireEarth.zip"], dest)
    assert [c.name for c in copies] == ["EmpireEarth-0.7.dmg", "EmpireEarth-0.7.zip"]
    assert (out / "EmpireEarth.dmg").exists() and (out / "EmpireEarth.zip").exists()  # copied, not moved
    assert (dest / "SHA256SUMS").read_text() == "%s  EmpireEarth-0.7.dmg\n%s  EmpireEarth-0.7.zip\n" % (
        hashlib.sha256(b"dmg").hexdigest(), hashlib.sha256(b"zip").hexdigest())


def test_the_read_me_in_the_dmg():
    text = package_app.README.read_text()
    assert len(text.splitlines()) <= 40 and max(len(line) for line in text.splitlines()) <= 80
    for needed in ("Applications", "Open Anyway", "Privacy & Security", "local network", "Allow",
                   "setup_empire_earth_gold_", "offline backup", "minutes",
                   "~/Library/Application Support/EmpireEarth", "Bin", "ZeroTier",
                   "Direct Connect does not work", "game.log", "launcher.log"):
        assert needed in text, needed
    text.encode("ascii")  # plain text that any editor shows as it is


@pytest.fixture(scope="module")
def launcher(tmp_path_factory):
    """app/launcher/launcher.m, built once for the tests below."""
    if not shutil.which("xcrun"):
        pytest.skip("no Xcode clang to build the launcher with")
    out = tmp_path_factory.mktemp("launcher") / "EmpireEarth"
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-fmodules", "-arch", "arm64",
                    "-mmacosx-version-min=" + package_app.MIN_MACOS, "-O2", "-framework", "AppKit",
                    "-o", str(out), str(ROOT / "app/launcher/launcher.m")], check=True)
    return out


QUARANTINE = "com.apple.quarantine"
DOWNLOADED = "0081;6ad0ff50;Safari;"  # what a browser's download carries


def clean_app(tmp_path, launcher, python_script):
    """A minimal EmpireEarth.app around the launcher whose "Python" is a shell
    script: what the launcher runs (firstrun.py status) answers from it."""
    app = tmp_path / "clean/EmpireEarth.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    shutil.copy2(launcher, app / "Contents/MacOS/EmpireEarth")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "EmpireEarth", "CFBundleIdentifier": "dev.neoee.empireearth.test",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.7", "CFBundleVersion": "1.1"}))
    py = app / "Contents/Resources/toolchain/python/bin/python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n" + python_script)
    py.chmod(0o755)
    return app


STATUS = ('echo \'{"event": "result", "phase": "status", "done": 1, "total": 1, "ok": true, '
          '"message": "", "data": {"state": "needs-import"}}\'\n')


def quarantined_app(tmp_path):
    """An app-shaped folder, every file, folder and symlink quarantined, one file
    and one folder read-only. Nothing in it is ever run: starting anything
    inside a quarantined app makes Gatekeeper ask whoever is at the screen."""
    app = tmp_path / "q/EmpireEarth.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/bin").mkdir(parents=True)
    (app / "Contents/MacOS/EmpireEarth").write_bytes(b"not run")
    (app / "Contents/Resources/data.txt").write_text("x")
    (app / "Contents/Resources/readonly.txt").write_text("x")
    (app / "Contents/Resources/bin/tool").write_text("x")
    (app / "Contents/Resources/link").symlink_to("data.txt")
    subprocess.run(["xattr", "-r", "-w", QUARANTINE, DOWNLOADED, str(app)], check=True)
    subprocess.run(["xattr", "-s", "-w", QUARANTINE, DOWNLOADED, str(app / "Contents/Resources/link")], check=True)
    (app / "Contents/Resources/readonly.txt").chmod(0o444)
    (app / "Contents/Resources/bin").chmod(0o555)
    return app


def same_place(text):
    """Paths as the launcher writes them: Foundation drops /private from /private/var."""
    return str(text).replace("/private/var/", "/var/").replace("/private/tmp/", "/tmp/")


def run_launcher(app_or_exe, *args, env=None):
    exe = app_or_exe / "Contents/MacOS/EmpireEarth" if app_or_exe.suffix == ".app" else app_or_exe
    full = {k: v for k, v in os.environ.items() if not k.startswith("EE_")}
    full.update(env or {})
    return subprocess.run([str(exe)] + list(args), env=full, capture_output=True, text=True, timeout=60)


def check_install(launcher, app, env=None):
    r = run_launcher(launcher, "--check-install", str(app), env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_launcher_clears_its_apps_quarantine(tmp_path, launcher):
    """Before anything nested runs (here firstrun's `status`), the launcher
    clears com.apple.quarantine from every file, folder and symlink of its
    app, read-only ones included, and logs it. $EE_TEST_APP points it at the
    quarantined app while it runs from a clean one."""
    clean = clean_app(tmp_path, launcher, STATUS)
    q = quarantined_app(tmp_path)
    before = check_install(launcher, q)
    assert before["quarantined"] == before["files"] == 10 and not before["translocated"]
    home = tmp_path / "home"
    r = run_launcher(clean, "--status", env={"EE_HOME": str(home), "EE_TEST_APP": str(q)})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["data"]["state"] == "needs-import"
    assert "quarantine: cleared from 10 files (of 10 in the app" in r.stderr
    assert check_install(launcher, q)["quarantined"] == 0
    assert QUARANTINE not in subprocess.run(["xattr", "-s", "-l", str(q / "Contents/Resources/link")],
                                            capture_output=True, text=True).stdout
    # the read-only ones are read-only again
    assert (q / "Contents/Resources/readonly.txt").stat().st_mode & 0o777 == 0o444
    assert (q / "Contents/Resources/bin").stat().st_mode & 0o777 == 0o555
    log = (home / "Logs/launcher.log").read_text()
    assert "launcher 0.7 (1.1) at " in log and "quarantine: cleared from 10 files" in log
    # nothing left: the next start says nothing about it
    r = run_launcher(clean, "--status", env={"EE_HOME": str(home), "EE_TEST_APP": str(q)})
    assert r.returncode == 0 and "quarantine" not in r.stderr
    (q / "Contents/Resources/bin").chmod(0o755)


def test_a_start_blocked_by_quarantine_says_how_to_fix_it(tmp_path, launcher):
    """A file the launcher cannot clear (immutable here; another user's app in
    real life) keeps the quarantine. When something nested then dies without a
    word - as macOS kills code it refuses - the launcher says to run
    `xattr -cr` on the app."""
    clean = clean_app(tmp_path, launcher, "kill -9 $$\n")
    q = quarantined_app(tmp_path)
    locked = q / "Contents/Resources/data.txt"
    subprocess.run(["chflags", "uchg", str(locked)], check=True)
    try:
        r = run_launcher(clean, "--status", env={"EE_HOME": str(tmp_path / "home"), "EE_TEST_APP": str(q)})
        assert r.returncode != 0
        said = same_place(r.stderr)
        assert "1 keep it (first %s: Operation not permitted)" % same_place(locked) in said, said
        assert "1 files of the app are still quarantined" in said
        assert 'xattr -cr "%s"' % same_place(q) in said
        # A failure with nothing quarantined is just a failure.
        subprocess.run(["chflags", "nouchg", str(locked)], check=True)
        subprocess.run(["xattr", "-d", QUARANTINE, str(locked)], check=True)
        r = run_launcher(clean, "--status", env={"EE_HOME": str(tmp_path / "home"), "EE_TEST_APP": str(q)})
        assert r.returncode != 0 and "xattr -cr" not in r.stderr
    finally:
        subprocess.run(["chflags", "nouchg", str(locked)])
        (q / "Contents/Resources/bin").chmod(0o755)


def test_translocation_is_detected(tmp_path, launcher):
    """Security's SecTranslocateIsTranslocatedURL, or the place: a path under
    …/AppTranslocation/ is translocated, an ordinary one is not.
    $EE_TEST_TRANSLOCATED_FROM stands in for a real translocation, which only
    Launch Services can make."""
    q = quarantined_app(tmp_path)
    assert check_install(launcher, q)["translocated"] is False
    fake = "/private/var/folders/xy/abc/T/AppTranslocation/0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9/d/EmpireEarth.app"
    assert check_install(launcher, fake)["translocated"] is True
    info = check_install(launcher, q, env={"EE_TEST_TRANSLOCATED_FROM": "/Volumes/Empire Earth/EmpireEarth.app"})
    assert info["translocated"] is True and info["original"] == "/Volumes/Empire Earth/EmpireEarth.app"
    (q / "Contents/Resources/bin").chmod(0o755)


def test_a_translocated_app_asks_before_anything_runs(tmp_path, launcher):
    """Translocated, with a window: the alert comes first (answered "Quit" by
    $EE_TEST_ALERT, so nothing is shown) and nothing nested runs."""
    marker = tmp_path / "python-ran"
    clean = clean_app(tmp_path, launcher, "touch '%s'\n%s" % (marker, STATUS))
    home = tmp_path / "home"
    r = run_launcher(clean, env={"EE_HOME": str(home), "EE_TEST_ALERT": "quit",
                                 "EE_TEST_TRANSLOCATED_FROM": str(tmp_path / "Downloads/EmpireEarth.app")})
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    log = (home / "Logs/launcher.log").read_text()
    assert "(translocated from %s/Downloads/EmpireEarth.app)" % same_place(tmp_path) in same_place(log)
    assert log.rstrip().endswith("translocated: quit")
    # Without a window it only says so, and carries on.
    r = run_launcher(clean, "--status", env={"EE_HOME": str(home),
                                             "EE_TEST_TRANSLOCATED_FROM": str(tmp_path / "x.app")})
    assert r.returncode == 0 and "translocated" in r.stderr and marker.exists()


@pytest.mark.skipif(os.environ.get("EE_TEST_LAUNCH_SERVICES") != "1",
                    reason="starts an app through Launch Services: EE_TEST_LAUNCH_SERVICES=1")
def test_move_to_applications(tmp_path, launcher):
    """`--move-to-applications` (the alert's button with no window): ditto into
    $EE_APPLICATIONS_DIR, the quarantine cleared from the copy, an older copy
    replaced whole, then the copy started through Launch Services with the
    remaining arguments, not translocated."""
    clean = clean_app(tmp_path, launcher, STATUS)
    src = tmp_path / "Downloads/EmpireEarth.app"
    src.parent.mkdir()
    shutil.copytree(clean, src, symlinks=True)
    subprocess.run(["xattr", "-r", "-w", QUARANTINE, DOWNLOADED, str(src)], check=True)
    apps, home = tmp_path / "Applications", tmp_path / "home"
    apps.mkdir()
    env = {"EE_HOME": str(home), "EE_APPLICATIONS_DIR": str(apps), "EE_TEST_TRANSLOCATED_FROM": str(src)}
    dest = apps / "EmpireEarth.app"
    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
                  "Support/lsregister")
    try:
        for round in (1, 2):
            r = run_launcher(clean, "--move-to-applications", "--status", env=env)
            assert r.returncode == 0, r.stderr
            assert Path(r.stdout.strip()).resolve() == dest.resolve()
            assert check_install(launcher, dest)["quarantined"] == 0
            assert check_install(launcher, src)["quarantined"] > 0  # the original is only read
            # the second copy replaced the first whole: nothing of it is left
            assert not (dest / "Contents/stale").exists()
            (dest / "Contents/stale").write_text("from the copy before")
            import time
            for _ in range(30):
                log = (home / "Logs/launcher.log").read_text()
                if log.count("EmpireEarth.app\n") >= round and log.count("move: started") >= round:
                    break
                time.sleep(0.5)
            started = [line for line in log.splitlines() if " at " in line and "Applications/EmpireEarth.app" in line
                       and "move to Applications" not in line]
            assert len(started) == round and "translocated" not in started[-1], log
        assert "replacing the copy there" in log
    finally:
        subprocess.run([lsregister, "-u", str(dest)], capture_output=True)


def test_the_launcher_names_game_ports_something_else_holds(launcher):
    """`EmpireEarth --check-ports` (app/launcher/launcher.m): the game's fixed
    ports that cannot be bound - a UDP port someone holds, a TCP port someone
    listens on - and not a free one. EE_GAME_PORTS replaces 33334-33336 with
    ports this test owns, so a game running on this Mac changes nothing."""
    import socket
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", 0))
    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(("0.0.0.0", 0))
    tcp_port = free.getsockname()[1]
    free.close()
    env = dict(os.environ, EE_GAME_PORTS="udp:%d,tcp:%d" % (udp.getsockname()[1], tcp_port))

    def check():
        r = subprocess.run([str(launcher), "--check-ports"], env=env, capture_output=True, text=True)
        return r.returncode, r.stdout.strip()

    try:
        assert check() == (1, "in use: UDP %d" % udp.getsockname()[1])
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", tcp_port))
        listener.listen(1)
        try:
            assert check() == (1, "in use: UDP %d, TCP %d" % (udp.getsockname()[1], tcp_port))
        finally:
            listener.close()
    finally:
        udp.close()
    assert check() == (0, "")
