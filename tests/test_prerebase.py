"""TEMPORARY with tools/prerebase.py: a DLL relocated ahead of time is the
image the translator relocates itself. Skips without the game files.

    .venv/bin/python -m pytest -q tests/test_prerebase.py
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kitpath  # noqa: E402
import prerebase  # noqa: E402

KIT, _ = kitpath.take_kit([])
sys.path.insert(0, str(KIT / "tools/recomp"))
import translate  # noqa: E402

DLL = ROOT / "original/gog/Empire Earth/Default.dll"


@pytest.mark.skipif(not DLL.is_file(), reason="needs your Empire Earth installation (tools/setup.py)")
def test_a_relocated_copy_maps_like_the_translator_relocates(tmp_path):
    copy, count = prerebase.relocate(DLL.read_bytes(), 0x10300000)
    assert count == 1644
    (tmp_path / "Default.dll").write_bytes(copy)
    ours = translate.Image(str(DLL))
    ours.relocate(0x10300000)
    theirs = translate.Image(str(tmp_path / "Default.dll"))
    assert theirs.base == ours.base == 0x10300000
    # Identical but for the header's ImageBase, which only the copy rewrites.
    import pefile
    field = pefile.PE(data=copy, fast_load=True).OPTIONAL_HEADER.get_field_absolute_offset("ImageBase")
    differ = [i for i, (a, b) in enumerate(zip(theirs.data, ours.data)) if a != b]
    assert len(theirs.data) == len(ours.data) and set(differ) <= set(range(field, field + 4))
    assert theirs.reloc_sites == ours.reloc_sites


def test_the_derived_game_toml_keeps_everything_but_paths_and_hashes():
    text = (ROOT / "game.toml").read_text()
    staged = {"a_sxlrt233": (Path("/stage/sxlrt233.dll"), "1" * 64),
              "b_engine": (Path("/stage/Low-Level Engine.dll"), "2" * 64),
              "c_default": (Path("/stage/Default.dll"), "3" * 64)}
    derived = prerebase.derived_toml(text, staged)
    assert 'path = "/stage/Default.dll"' in derived and 'sha256 = "%s"' % ("3" * 64) in derived
    assert 'globals = "%s"' % (ROOT / "globals.toml") in derived
    strip = lambda t: [l for l in t.splitlines() if not l.lstrip().startswith(("path", "sha256", "developer_exe",
                                                                                 "listings", "function_db", "globals"))]
    assert strip(derived) == strip(text)
