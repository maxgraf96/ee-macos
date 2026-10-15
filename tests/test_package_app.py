"""The shareable app's packaging: what goes into Contents/Resources/ee, the
Info.plist, and the packaging check (tools/check_app.py) on synthetic apps.

    .venv/bin/python -m pytest -q tests/test_package_app.py

No game files: the "game file" is a synthetic one listed in a synthetic
db/gog-files.json. tools/package_app.py itself is run by hand (it builds the
player host and needs the staged toolchain; docs/reference/packaging.md §8).
"""
import hashlib
import json
from pathlib import Path
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
    plist = package_app.info_plist({"describe": "abc", "count": "7", "kit": "def"})
    assert plist["CFBundleExecutable"] == "EmpireEarth"
    assert plist["LSMinimumSystemVersion"] == "13.0"
    assert plist["LSArchitecturePriority"] == ["arm64"]
    assert plist["CFBundleIconFile"] == "AppIcon"
    assert "local network" in plist["NSLocalNetworkUsageDescription"]


def test_the_launcher_names_game_ports_something_else_holds(tmp_path):
    """`EmpireEarth --check-ports` (app/launcher/launcher.m): the game's fixed
    ports that cannot be bound - a UDP port someone holds, a TCP port someone
    listens on - and not a free one. EE_GAME_PORTS replaces 33334-33336 with
    ports this test owns, so a game running on this Mac changes nothing."""
    import os
    import socket
    if not shutil.which("xcrun"):
        pytest.skip("no Xcode clang to build the launcher with")
    launcher = tmp_path / "EmpireEarth"
    subprocess.run(["xcrun", "clang", "-fobjc-arc", "-fmodules", "-arch", "arm64",
                    "-mmacosx-version-min=" + package_app.MIN_MACOS, "-O2", "-framework", "AppKit",
                    "-o", str(launcher), str(ROOT / "app/launcher/launcher.m")], check=True)
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
