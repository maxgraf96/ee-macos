"""The first-run importer and pipeline driver (tools/firstrun/), without game
files: synthetic game folders, a fake Wine wrapper, a fake GOG hashdb and
install script, and a stand-in translator and bundle driver for the
pipeline's orchestration (docs/reference/firstrun.md §9).

The fake CD key below must never appear on stdout or in the log.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/firstrun"))
import paths  # noqa: E402
import firstrun  # noqa: E402
import gogdb  # noqa: E402
import importer  # noqa: E402
import pipeline  # noqa: E402
import progress  # noqa: E402
import sources  # noqa: E402
import verify  # noqa: E402

FAKE_KEY = bytes(range(0x41, 0x51))           # 16 bytes, "ABCDEFGHIJKLMNOP"
FAKE_KEY_B64 = base64.b64encode(FAKE_KEY).decode()
SECRETS = (FAKE_KEY_B64, FAKE_KEY.hex(), FAKE_KEY.decode())

MODULE_NAMES = ["Empire Earth.exe", "sxlrt233.dll", "Low-Level Engine.dll", "Default.dll", "Language.dll",
                "DX7HRDisplay.dll", "DX7HRTnLDisplay.dll", "TCP Protocol.dll",
                "Miles Sound System Mixer.dll"]


def content(rel):
    return ("synthetic %s\n" % rel).encode() * 3


GAME_FILES = MODULE_NAMES + ["Data/data.ssa", "Data/Campaigns/EETheGreeks.ssa", "WONLobby.cfg",
                             "ddraw.dll", "binkw32.dll", "readme.txt"]


@pytest.fixture
def synthetic(monkeypatch):
    """A manifest and module list for GAME_FILES, in place of the real ones."""
    manifest = {"base": "Empire Earth", "product": {"gameId": gogdb.GAME_ID},
                "dirs": ["Data/Saved Games"],
                "files": {rel: (len(content(rel)), hashlib.md5(content(rel)).hexdigest()) for rel in GAME_FILES}}
    modules = [(n, hashlib.sha256(content(n)).hexdigest()) for n in MODULE_NAMES]
    monkeypatch.setattr(verify, "load_manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(verify, "load_modules", lambda *a, **k: modules)
    return manifest, modules


def make_game(folder, skip=(), change=()):
    folder = Path(folder)
    for rel in GAME_FILES:
        if rel in skip:
            continue
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content(rel)
        p.write_bytes(data.upper() if rel in change else data)        # same size, other bytes
    return folder


def make_script(path, key_b64=FAKE_KEY_B64):
    actions = [
        {"install": {"action": "setRegistry", "arguments": {
            "root": "HKLM", "subkey": "Software\\Stainless Steel Studios\\Empire Earth", "deleteSubkeys": True}}},
        {"install": {"action": "setRegistry", "arguments": {
            "root": "HKLM", "subkey": "Software\\Sierra\\CDKeys", "valueName": "EmpireEarth",
            "valueType": "binary", "valueData": key_b64}}},
        {"install": {"action": "setRegistry", "arguments": {
            "root": "HKLM", "subkey": "Software\\Sierra\\CDKeys", "valueName": "EEAOC",
            "valueType": "binary", "valueData": base64.b64encode(b"aoc-key-aoc-key!").decode()}}},
    ]
    Path(path).write_text(json.dumps({"actions": actions, "productId": gogdb.GAME_ID}))


def make_hashdb(path, entries):
    rec = b""
    for name, md5 in entries.items():
        rec += name.encode().ljust(1024, b"\0") + md5.encode()
    data = struct.pack("<III", 12, 1, len(entries)) + rec
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(Path(path).name, data)


def make_gold(folder, script=True, aoc=True, hashdb=True, **kw):
    gold = Path(folder)
    make_game(gold / "Empire Earth", **kw)
    if script:
        make_script(gold / gogdb.SCRIPT)
    if aoc:
        (gold / "Empire Earth - The Art of Conquest").mkdir(parents=True)
        (gold / "Empire Earth - The Art of Conquest/EE-AOC.exe").write_bytes(b"MZaoc")
    if hashdb:
        make_hashdb(gold / gogdb.HASHDB, {"Empire Earth\\" + rel.replace("/", "\\"): hashlib.md5(content(rel)).hexdigest()
                                         for rel in GAME_FILES})
    return gold


def make_wrapper(folder):
    """A Wineskin-shaped .app: the game under drive_c, the key only in system.reg."""
    app = Path(folder) / "Empire Earth.app"
    prefix = app / "Contents/SharedSupport/prefix"
    make_gold(prefix / "drive_c/GOG Games/Empire Earth Gold", script=False)
    (prefix / "drive_c/windows/system32").mkdir(parents=True)
    hexkey = ",".join("%02x" % b for b in FAKE_KEY)
    (prefix / "system.reg").write_text(
        "WINE REGISTRY Version 2\n\n[Software\\\\Wow6432Node\\\\Sierra\\\\CDKeys] 1773189281\n"
        "#time=1dcb17b5a2c2f5e\n\"EmpireEarth\"=hex:%s,\\\n  %s\n\n" % (hexkey[:23], hexkey[24:]))
    (app / "Contents/drive_c").symlink_to("SharedSupport/prefix/drive_c")
    return app


def run_cli(capsys, *argv):
    rc = firstrun.main(list(argv))
    out = capsys.readouterr().out
    lines = [json.loads(l) for l in out.splitlines()]
    return rc, lines, out


def assert_schema(lines):
    assert lines, "no output"
    for l in lines:
        assert isinstance(l["phase"], str) and l["phase"]
        assert isinstance(l["done"], int) and isinstance(l["total"], int)
        assert isinstance(l["message"], str)
        assert l["event"] in progress.EVENTS
        if l["event"] == "progress":
            assert l["unit"] in progress.UNITS
            assert 0 <= l["done"] <= max(l["total"], l["done"])
        if l["event"] == "error":
            assert l["code"] in progress.ERROR_CODES
    assert lines[-1]["event"] == "result"
    assert sum(1 for l in lines if l["event"] == "result") == 1


def no_secrets(*texts):
    for t in texts:
        for s in SECRETS:
            assert s not in t


# ------------------------------------------------------------------ GOG files

def test_hashdb_roundtrip_and_garbage(tmp_path):
    make_hashdb(tmp_path / "a.hashdb", {"Empire Earth\\x.dll": "0" * 32, "readme.txt": "f" * 32})
    assert gogdb.read_hashdb(tmp_path / "a.hashdb") == {"Empire Earth\\x.dll": "0" * 32, "readme.txt": "f" * 32}
    (tmp_path / "b.hashdb").write_bytes(b"not a zip")
    assert gogdb.read_hashdb(tmp_path / "b.hashdb") is None
    with zipfile.ZipFile(tmp_path / "c.hashdb", "w") as z:
        z.writestr("c.hashdb", struct.pack("<III", 12, 2, 0))       # another version
    assert gogdb.read_hashdb(tmp_path / "c.hashdb") is None


def test_cd_key_sources(tmp_path):
    make_script(tmp_path / gogdb.SCRIPT)
    hklm = gogdb.hklm_from_script(tmp_path / gogdb.SCRIPT)
    key = "HKEY_LOCAL_MACHINE\\Software\\Sierra\\CDKeys"
    assert hklm[key]["EmpireEarth"]["data"] == FAKE_KEY.hex() and "EEAOC" not in hklm[key]
    (tmp_path / gogdb.GOGLOG).write_text(
        "﻿[setup]\nDoOnce1207658777[abc]=1\n[1207658777_remote]\nRegs=1\nValueName_0=\nRootKey_0=HKLM\n"
        "SubKey_0=Software\\Mad Doc Software\\X\n[1207658777]\nRegs=2\nValueName_0=EmpireEarth\nRootKey_0=HKLM\n"
        "SubKey_0=Software\\Sierra\\CDKeys\nValueType_0=binary\nValueData_0=%s\nDelSubKey_0=false\n"
        "ValueName_1=EEAOC\nRootKey_1=HKLM\nSubKey_1=Software\\Sierra\\CDKeys\nValueType_1=binary\n"
        "ValueData_1=AAAA\n" % FAKE_KEY_B64, encoding="utf-8")
    assert gogdb.hklm_from_goglog(tmp_path / gogdb.GOGLOG) == {key: {"EmpireEarth": hklm[key]["EmpireEarth"]}}
    app = make_wrapper(tmp_path / "w")
    wine = gogdb.hklm_from_wine_registry(app / "Contents/SharedSupport/prefix/system.reg")
    assert wine == {key: {"EmpireEarth": hklm[key]["EmpireEarth"]}}
    assert gogdb.has_cd_key(wine)
    # order: script, goglog, Wine registry
    kind, _, _ = gogdb.find_registry_source(tmp_path, app / "Contents/SharedSupport/prefix")
    assert kind == "script"
    (tmp_path / gogdb.SCRIPT).unlink()
    assert gogdb.find_registry_source(tmp_path, None)[0] == "goglog"
    assert gogdb.find_registry_source(tmp_path / "nowhere", app / "Contents/SharedSupport/prefix")[0] == "wine-registry"


# ------------------------------------------------------------------ sources

def test_locate_layouts(tmp_path):
    gold = make_gold(tmp_path / "Empire Earth Gold")
    (s,) = sources.locate(gold)
    assert s.kind == "gold" and s.game_dir == gold / "Empire Earth" and s.gold_dir == gold
    assert any("Art of Conquest" in n for n in s.notes)
    (s,) = sources.locate(gold / "Empire Earth")
    assert s.kind == "gold" and s.gold_dir == gold                 # the subfolder still finds the script
    (s,) = sources.locate(gold / "Empire Earth" / "Empire Earth.exe")
    assert s.game_dir == gold / "Empire Earth"
    usb = make_game(tmp_path / "USB/Empire Earth")                 # a bare copy from a Windows PC
    (s,) = sources.locate(tmp_path / "USB")
    assert s.kind == "game" and s.gold_dir is None
    app = make_wrapper(tmp_path / "Apps")
    (s,) = sources.locate(app)
    assert s.kind == "wrapper" and s.wine_prefix == app / "Contents/SharedSupport/prefix"
    assert s.game_dir.name == "Empire Earth"
    assert usb


def test_locate_installer_and_failures(tmp_path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    exe = dl / "setup_empire_earth_gold_2.0.0.2974_(123).exe"
    exe.write_bytes(b"MZ" + b"\0" * 100)
    (dl / "setup_empire_earth_gold_2.0.0.2974_(123)-1.bin").write_bytes(b"x")
    (dl / "setup_empire_earth_gold_2.0.0.2974_(123)-2.bin").write_bytes(b"x")
    (s,) = sources.locate(exe)
    assert s.kind == "installer" and [p.name for p in s.installer][1:] == [
        "setup_empire_earth_gold_2.0.0.2974_(123)-1.bin", "setup_empire_earth_gold_2.0.0.2974_(123)-2.bin"]
    (s2,) = sources.locate(dl / "setup_empire_earth_gold_2.0.0.2974_(123)-1.bin")
    assert s2.installer[0] == exe
    (s3,) = sources.locate(dl)
    assert s3.installer[0] == exe
    aoc = tmp_path / "Empire Earth - The Art of Conquest"
    aoc.mkdir()
    (aoc / "EE-AOC.exe").write_bytes(b"MZ")
    assert sources.locate(aoc) == []
    assert sources.explain(aoc)[0] == "aoc-only"
    assert sources.explain(tmp_path / "missing")[0] == "not-found"
    (tmp_path / "notes.txt").write_text("x")
    assert sources.locate(tmp_path / "notes.txt") == []


# ------------------------------------------------------------------ verify

def test_verify_levels(tmp_path, synthetic):
    manifest, modules = synthetic
    good = make_game(tmp_path / "good")
    q = verify.quick(good)
    assert q["ok"] and not q["missing"]
    bad = make_game(tmp_path / "patched", change=("Empire Earth.exe",))
    q = verify.quick(bad)
    assert not q["ok"] and q["code"] == "wrong-version"
    neo = make_game(tmp_path / "neo", change=("Empire Earth.exe",))
    (neo / "neoee.dll").write_bytes(b"MZ")
    assert "NeoEE" in verify.quick(neo)["message"]
    cd = make_game(tmp_path / "cd", change=("Empire Earth.exe",))
    (cd / "Empire Earth.icd").write_bytes(b"x")
    assert "CD" in verify.quick(cd)["message"]
    part = make_game(tmp_path / "partial", skip=("Data/data.ssa",))
    q = verify.quick(part)
    assert q["code"] == "partial-copy" and q["missing"] == ["Data/data.ssa"]
    short = make_game(tmp_path / "short")
    (short / "Data/data.ssa").write_bytes(b"x")
    assert verify.quick(short)["short"] == ["Data/data.ssa"]
    nomod = make_game(tmp_path / "nomod", skip=("Default.dll",))
    assert verify.quick(nomod)["code"] == "partial-copy"
    opt = make_game(tmp_path / "opt", skip=("ddraw.dll",))
    q = verify.quick(opt)
    assert q["ok"] and q["missing_optional"] == ["ddraw.dll"]


# ------------------------------------------------------------------ import

def test_import_wrapper_end_to_end(tmp_path, synthetic, capsys):
    app = make_wrapper(tmp_path / "Apps")
    saves = app / "Contents/SharedSupport/prefix/drive_c/GOG Games/Empire Earth Gold/Empire Earth/Data/Saved Games"
    saves.mkdir(parents=True, exist_ok=True)
    (saves / "mine.sav").write_bytes(b"my save")
    home = tmp_path / "home"
    rc, lines, out = run_cli(capsys, "--home", str(home), "--quiet", "import", str(app), "--player-name", "Ada")
    assert rc == 0, lines[-2:]
    assert_schema(lines)
    assert lines[-1]["ok"] and lines[-1]["data"]["record"]["registry"] == {"source": "wine-registry", "cd_key": True}
    game = home / "Game/Empire Earth"
    for rel in GAME_FILES:
        assert (game / rel).read_bytes() == content(rel)
    assert (game / "Data/Saved Games").is_dir()
    assert (home / "Data/Saved Games/mine.sav").read_bytes() == b"my save"     # into the profile
    assert (home / "game-path.txt").read_text().strip() == str(game / "Empire Earth.exe")
    reg = json.loads((home / "registry.json").read_text())
    assert reg["HKEY_LOCAL_MACHINE\\Software\\Sierra\\CDKeys"]["EmpireEarth"]["data"] == FAKE_KEY.hex()
    assert reg["HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"]["Player Name"]["data"] == "Ada"
    no_secrets(out, (home / "Build/firstrun.log").read_text(), (home / "Game/import.json").read_text())
    # the wrapper is untouched
    assert (saves / "mine.sav").read_bytes() == b"my save"
    # idempotent: again, and the game's own registry values survive
    reg["HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"]["Music Volume"]["data"] = 7
    (home / "registry.json").write_text(json.dumps(reg))
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(app))
    assert rc == 0
    reg = json.loads((home / "registry.json").read_text())
    assert reg["HKEY_CURRENT_USER\\Software\\SSSI\\Empire Earth"]["Music Volume"]["data"] == 7
    assert not (home / "Game.partial").exists() and not (home / "Game.old").exists()


def test_import_rejects_and_keeps_previous(tmp_path, synthetic, capsys):
    home = tmp_path / "home"
    gold = make_gold(tmp_path / "gold")
    assert run_cli(capsys, "--home", str(home), "--quiet", "import", str(gold))[0] == 0
    before = (home / "Game/import.json").read_text()
    modded = make_gold(tmp_path / "modded", change=("Data/data.ssa",))
    rc, lines, out = run_cli(capsys, "--home", str(home), "--quiet", "import", str(modded))
    assert rc == 1 and lines[-2]["code"] == "modified-files" and "Data/data.ssa" in lines[-2]["message"]
    assert_schema(lines)
    assert (home / "Game/import.json").read_text() == before            # the previous import stands
    assert not (home / "Game.partial").exists()                          # the rejected copy is gone
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(modded), "--allow-modified")
    assert rc == 0 and any(l["event"] == "warning" and "data.ssa" in l["message"] for l in lines)
    wrong = make_gold(tmp_path / "wrong", change=("Low-Level Engine.dll",))
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(wrong))
    assert rc == 1 and lines[-2]["code"] == "wrong-version"
    part = make_gold(tmp_path / "part", skip=("Data/Campaigns/EETheGreeks.ssa",))
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(part))
    assert rc == 1 and lines[-2]["code"] == "partial-copy"
    opt = make_gold(tmp_path / "opt", change=("ddraw.dll",))
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(opt))
    assert rc == 0 and any(l["event"] == "warning" and "ddraw.dll" in l["message"] for l in lines)
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(tmp_path / "nothing"))
    assert rc == 1 and lines[-2]["code"] == "not-found"


def test_import_resumes_after_interrupt(tmp_path, synthetic, capsys, monkeypatch):
    home = tmp_path / "home"
    gold = make_gold(tmp_path / "gold")
    real_place, calls = importer.place, []

    def flaky(src, dst, **kw):
        calls.append(dst)
        if len(calls) == 6:
            raise firstrun.Stop()
        return real_place(src, dst, **kw)
    monkeypatch.setattr(importer, "place", flaky)
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(gold))
    assert rc == 130 and lines[-2]["code"] == "interrupted"
    assert (home / "Game.partial").is_dir() and not (home / "Game").exists()
    monkeypatch.setattr(importer, "place", real_place)
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "import", str(gold))
    assert rc == 0
    copied = [l for l in lines if l["phase"] == "copy" and "Copied" in l["message"]][0]["message"]
    assert "5 kept" in copied
    assert not (home / "Game.partial").exists()


def test_publish_recovers_between_renames(tmp_path):
    home = paths.Home(tmp_path)
    (tmp_path / "Game.old/Empire Earth").mkdir(parents=True)
    importer.recover(home)                         # stopped after Game -> Game.old
    assert (tmp_path / "Game/Empire Earth").is_dir() and not (tmp_path / "Game.old").exists()


def test_busy_lock(tmp_path, synthetic, capsys):
    import fcntl
    home = paths.Home(tmp_path / "home")
    home.root.mkdir(parents=True)
    with open(home.lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        rc, lines, _ = run_cli(capsys, "--home", str(home.root), "--quiet", "import", str(tmp_path))
    assert rc == 1 and lines[-2]["code"] == "busy"


def test_usage_and_status(tmp_path, capsys):
    assert firstrun.main(["--home", str(tmp_path), "frobnicate"]) == 2
    capsys.readouterr()
    rc, lines, _ = run_cli(capsys, "--home", str(tmp_path / "empty"), "status", "--cc", "/bin/echo",
                           "--lld", "/bin/echo")
    assert rc == 0 and lines[-1]["data"]["state"] == "needs-import"
    assert_schema(lines)


# ------------------------------------------------------------------ pipeline

def test_jobs_by_memory():
    assert pipeline.plan_jobs(8 << 30, 8) == (4, 1, 2)
    assert pipeline.plan_jobs(16 << 30, 10) == (10, 3, 9)
    assert pipeline.plan_jobs(64 << 30, 16, jobs=6)[0] == 6


def test_guest_defines_match_cmake():
    assert pipeline.guest_defines() == ["GUEST_IMAGE_BASE=0x00400000u", "GUEST_HEAP_BASE=0x01000000u",
                                        "GUEST_SIZE=0x28000000u"]


FAKE_TRANSLATE = r'''
import argparse, json, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("--out"); ap.add_argument("--game"); ap.add_argument("--report"); ap.add_argument("--module")
a = ap.parse_args()
key = a.module or "main"
fail = os.environ.get("FAKE_TRANSLATE_FAIL")
if fail == key:
    sys.exit("fake translator: told to fail on " + key)
open(os.path.join(a.out, "table.c"), "w").write("/* %s */\n" % key)
open(os.path.join(a.out, "chunk_000.c"), "w").write("/* %s */\n" % key)
if key == "main":
    open(os.path.join(a.out, "symbols.json"), "w").write("{}\n")
with open(os.environ["FAKE_TRANSLATE_LOG"], "a") as f:
    f.write(key + "\n")
'''

FAKE_BUNDLE = r'''
import argparse, os
ap = argparse.ArgumentParser()
ap.add_argument("--gen"); ap.add_argument("--out"); ap.add_argument("--module", action="append")
ap.add_argument("-D", action="append"); ap.add_argument("--jobs"); ap.add_argument("--cc"); ap.add_argument("--lld")
ap.add_argument("--min-macos")
a = ap.parse_args()
for k in a.module:
    src = a.gen if k == "main" else os.path.join(a.gen, "aux-" + k)
    assert os.path.isfile(os.path.join(src, "table.c")), src
    os.makedirs(os.path.join(a.out, k + ".objects"), exist_ok=True)
    for n in ("chunk_000", "table"):
        open(os.path.join(a.out, k + ".objects", n + ".flags"), "w").write("x")
    open(os.path.join(a.out, k + ".bundle"), "wb").write(b"bundle " + k.encode())
    print("bundle %s: 2 objects (2 rebuilt), 0 host symbols -> x" % k, flush=True)
'''


@pytest.fixture
def fake_kit(tmp_path, monkeypatch, synthetic):
    kit = tmp_path / "kit"
    (kit / "tools/recomp").mkdir(parents=True)
    (kit / "runtime").mkdir()
    (kit / "tools/recomp/translate.py").write_text(FAKE_TRANSLATE)
    (kit / "tools/recomp/bundle.py").write_text(FAKE_BUNDLE)
    (kit / "tools/game_config.py").write_text((ROOT / "kit/tools/game_config.py").read_text())
    for h in pipeline.GEN_HEADERS:
        (kit / "runtime" / h).write_text('#include "more.h"\n' if h == "x86.h" else "/* %s */\n" % h)
    (kit / "runtime/more.h").write_text("/* more */\n")
    monkeypatch.setenv("FAKE_TRANSLATE_LOG", str(tmp_path / "translated.log"))
    monkeypatch.setattr(pipeline.Build, "run_inputs", lambda self, game: None)
    return kit


def fake_tools(kit):
    return pipeline.Toolchain(cc="/bin/echo", lld="/bin/echo", kit=str(kit))


def test_cache_key_tracks_its_inputs(fake_kit):
    tools = fake_tools(fake_kit)
    a = pipeline.key_inputs(tools, pipeline.guest_defines())
    assert "more.h" in a["headers"]                         # the include closure
    assert pipeline.cache_key(a) == pipeline.cache_key(pipeline.key_inputs(tools, pipeline.guest_defines()))
    (fake_kit / "runtime/more.h").write_text("/* changed */\n")
    b = pipeline.key_inputs(tools, pipeline.guest_defines())
    assert pipeline.cache_key(a) != pipeline.cache_key(b)
    for field, value in (("modules", {"Empire Earth.exe": "0" * 64}), ("cc", "clang version 99"),
                         ("db/inputs.json", "1" * 64), ("translator", {})):
        assert pipeline.cache_key(dict(b, **{field: value})) != pipeline.cache_key(b)


def test_pipeline_builds_resumes_and_caches(tmp_path, fake_kit, capsys, monkeypatch):
    home = tmp_path / "home"
    make_game(home / "Game/Empire Earth")
    (home / "Game/import.json").write_text("{}\n")
    monkeypatch.setenv("FAKE_TRANSLATE_FAIL", "c_default")
    args = ["--home", str(home), "--quiet", "build", "--cc", "/bin/echo", "--lld", "/bin/echo",
            "--kit", str(fake_kit)]
    rc, lines, _ = run_cli(capsys, *args)
    assert rc == 1 and lines[-2]["code"] == "translate-failed" and "c_default" in lines[-2]["message"]
    assert_schema(lines)
    first = (tmp_path / "translated.log").read_text().split()
    assert "c_default" not in first and len(first) == 8          # the others finished
    monkeypatch.delenv("FAKE_TRANSLATE_FAIL")
    rc, lines, _ = run_cli(capsys, *args)
    assert rc == 0, lines[-3:]
    assert_schema(lines)
    second = (tmp_path / "translated.log").read_text().split()[len(first):]
    assert second == ["c_default"]                                   # resumed, not redone
    data = lines[-1]["data"]
    out = Path(data["dir"])
    assert [Path(p).name for p in data["load_order"]][-1] == "main.bundle" and len(data["load_order"]) == 9
    assert (out / "symbols.json").is_file() and (out / "build.json").is_file()
    rec = json.loads((out / "build.json").read_text())
    assert rec["runs"] == 2 and {"translate", "compile", "total"} <= set(rec["seconds"])   # over the stop
    assert json.loads((home / "Build/current.json").read_text())["key"] == data["key"]
    assert not list((home / "Build").glob("work-*"))                 # generated C and objects gone
    phases = [l["phase"] for l in lines if l["event"] == "progress"]
    for p in ("preflight", "translate", "compile", "publish", "cleanup"):
        assert p in phases
    # again: nothing to do
    rc, lines, _ = run_cli(capsys, *args)
    assert rc == 0 and lines[-1]["data"]["cached"]
    rc, lines, _ = run_cli(capsys, "--home", str(home), "status", "--cc", "/bin/echo", "--lld", "/bin/echo",
                           "--kit", str(fake_kit))
    assert lines[-1]["data"]["state"] == "ready"
    # a translator change makes a new key: rebuilt, old bundles removed
    (fake_kit / "tools/recomp/translate.py").write_text(FAKE_TRANSLATE + "\n# v2\n")
    rc, lines, _ = run_cli(capsys, "--home", str(home), "status", "--cc", "/bin/echo", "--lld", "/bin/echo",
                           "--kit", str(fake_kit))
    assert lines[-1]["data"]["state"] == "needs-build"
    rc, lines, _ = run_cli(capsys, *args)
    assert rc == 0 and not lines[-1]["data"]["cached"] and lines[-1]["data"]["key"] != data["key"]
    assert not out.exists()


def test_preflight_disk_space(tmp_path, fake_kit, capsys, monkeypatch):
    home = tmp_path / "home"
    make_game(home / "Game/Empire Earth")
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 << 20})())
    rc, lines, _ = run_cli(capsys, "--home", str(home), "--quiet", "build", "--cc", "/bin/echo",
                           "--lld", "/bin/echo", "--kit", str(fake_kit))
    assert rc == 1 and lines[-2]["code"] == "disk-space"


def test_build_needs_import(tmp_path, fake_kit, capsys):
    rc, lines, _ = run_cli(capsys, "--home", str(tmp_path / "home"), "--quiet", "build", "--cc", "/bin/echo",
                           "--lld", "/bin/echo", "--kit", str(fake_kit))
    assert rc == 1 and lines[-2]["code"] == "not-imported"


# ------------------------------------------------------- a real Inno installer
# Opt-in: EE_TEST_INNO_DIR names the output of tools/firstrun/make_test_installer.py
# (a synthetic game in an Inno Setup 5.6 installer spanning several .bin slices,
# as GOG's are), and innoextract must be found (inno.find_binary).

INNO_DIR = os.environ.get("EE_TEST_INNO_DIR")


@pytest.fixture
def inno_fixture(tmp_path, monkeypatch):
    import inno
    if not INNO_DIR or inno.find_binary() is None:
        pytest.skip("set EE_TEST_INNO_DIR (tools/firstrun/make_test_installer.py) and build innoextract")
    src = Path(INNO_DIR)
    doc = json.loads((src / "manifest.json").read_text())
    doc["files"] = {p: (size, md5) for p, size, md5 in doc["files"]}
    modules = [tuple(m) for m in json.loads((src / "modules.json").read_text())]
    monkeypatch.setattr(verify, "load_manifest", lambda *a, **k: doc)
    monkeypatch.setattr(verify, "load_modules", lambda *a, **k: modules)
    copy = tmp_path / "Downloads"
    shutil.copytree(src, copy, ignore=shutil.ignore_patterns("wineprefix", "*.json"))
    return copy, doc


def test_import_from_a_spanned_inno_installer(tmp_path, inno_fixture, capsys):
    downloads, doc = inno_fixture
    exe = next(downloads.glob("*.exe"))
    home = tmp_path / "home"
    rc, lines, out = run_cli(capsys, "--home", str(home), "--quiet", "import", str(exe))
    assert rc == 0, lines[-2:]
    assert_schema(lines)
    assert lines[-1]["data"]["record"]["source"]["kind"] == "installer"
    assert lines[-1]["data"]["record"]["registry"] == {"source": "script", "cd_key": True}
    game = home / "Game/Empire Earth"
    for rel, (size, md5) in doc["files"].items():
        assert hashlib.md5((game / rel).read_bytes()).hexdigest() == md5
    assert not (home / "Game/Empire Earth - The Art of Conquest").exists()
    assert not list(home.rglob("Manual.pdf")) and not list(home.rglob("EE-AOC.exe"))
    assert any(l["phase"] == "extract" and l["unit"] == "files" for l in lines if l["event"] == "progress")
    no_secrets(out, (home / "Build/firstrun.log").read_text())
    # the .bin parts: one missing, then one damaged
    parts = sorted(downloads.glob("*.bin"))
    hidden = parts[1].with_suffix(".hidden")
    parts[1].rename(hidden)
    rc, lines, _ = run_cli(capsys, "--home", str(tmp_path / "h2"), "--quiet", "import", str(exe))
    assert rc == 1 and lines[-2]["code"] == "installer-parts-missing", lines[-2]
    hidden.rename(parts[1])
    data = bytearray(parts[2].read_bytes())
    data[len(data) // 2:len(data) // 2 + 4096] = bytes(4096)
    parts[2].write_bytes(bytes(data))
    rc, lines, _ = run_cli(capsys, "--home", str(tmp_path / "h3"), "--quiet", "import", str(exe))
    assert rc == 1 and lines[-2]["code"] == "installer-unreadable" and "damaged" in lines[-2]["message"]
    assert not (tmp_path / "h3/Game").exists()


def test_installer_that_is_not_one(tmp_path, inno_fixture, capsys):
    fake = tmp_path / "setup_empire_earth_gold_fake.exe"
    fake.write_bytes(b"MZ" + bytes(4096))
    rc, lines, _ = run_cli(capsys, "--home", str(tmp_path / "home"), "--quiet", "import", str(fake))
    assert rc == 1 and lines[-2]["code"] == "installer-unreadable"
