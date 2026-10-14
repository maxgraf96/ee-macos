"""game.toml and globals.toml agree with the function DB, PLAN.md §2.4 and the
kit's schema. No game files needed.

    .venv/bin/python -m pytest -q tests/test_game_config.py
"""
import json
import os
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kitpath  # noqa: E402

KIT, _ = kitpath.take_kit([])
sys.path.insert(0, str(KIT / "tools"))
import game_config  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


def db(name):
    return json.loads((ROOT / "db/functions" / (name.lower().replace(" ", "-") + ".json")).read_text())


@pytest.fixture(scope="module")
def cfg():
    return game_config.load(ROOT)


def plan_hashes():
    """The canonical modules table of PLAN.md §2.4: {file name: sha256}."""
    text = (ROOT / "docs/PLAN.md").read_text()
    return {m.group(1): m.group(2) for m in re.finditer(r"^\| `([^`]+)`[^|]*\| `([0-9a-f]{64})` \|", text, re.M)}


def test_every_module_is_a_canonical_gog_module(cfg):
    hashes = plan_hashes()
    assert hashes[cfg["game"]["executable"]] == cfg["game"]["sha256"]
    for m in cfg["aux_modules"]:
        assert hashes[m["name"]] == m["sha256"], m["name"]
        assert db(m["name"])["module"]["sha256"] == m["sha256"]
    assert db(cfg["game"]["executable"])["module"]["sha256"] == cfg["game"]["sha256"]


def test_m1_modules_are_configured(cfg):
    assert [m["name"] for m in cfg["aux_modules"]] == ["sxlrt233.dll", "Low-Level Engine.dll", "Default.dll"]


def test_the_base_map(cfg):
    """Distinct, 64 KB aligned, the PE's SizeOfImage, above the shims, inside
    the arena, and the executable at its fixed base."""
    exe = db(cfg["game"]["executable"])["module"]
    assert cfg["game"]["image_base"] == int(exe["image_base"], 16) == 0x00400000
    spans = [(0x00400000, 0x00400000 + int(exe["size_of_image"], 16), "exe")]
    for m in cfg["aux_modules"]:
        module = db(m["name"])["module"]
        assert m["size"] == int(module["size_of_image"], 16), m["name"]
        assert m["base"] % 0x10000 == 0 and m["base"] >= 0x10000000
        assert m["base"] + m["size"] <= cfg["game"]["guest_size"]
        spans.append((m["base"], m["base"] + m["size"], m["name"]))
    spans.sort()
    for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
        assert end <= start, "%s overlaps %s" % (a, b)
    bases = {m["name"]: m["base"] for m in cfg["aux_modules"]}
    assert bases == {"sxlrt233.dll": 0x10200000, "Low-Level Engine.dll": 0x10000000, "Default.dll": 0x10300000}


def test_modules_are_mapped_in_dependency_order(cfg):
    """Keys sort in dependency order: a module comes after every module it
    imports (the generated aux-<key> directories follow it)."""
    order = [m["name"].lower() for m in cfg["aux_modules"]]
    for i, m in enumerate(cfg["aux_modules"]):
        imported = {imp["dll"].lower() for imp in db(m["name"])["imports"]}
        later = set(order[i + 1:])
        assert not imported & later, "%s imports %s, mapped later" % (m["name"], imported & later)


def test_translation_inputs_are_the_function_db_exports(cfg):
    assert cfg["function_db_path"] == (ROOT / "analysis/kit/exe/funcdb.json")
    for m in cfg["aux_modules"]:
        assert m["function_db_path"] == m["listings_path"] / "funcdb.json"
        assert m["listings_path"].parent == ROOT / "analysis/kit"


GLOBAL_EXPORTS = {
    # globals.toml [entries] name: (module, export name or None for the entry point)
    "Default_DllMain": ("Default.dll", None), "Engine_DllMain": ("Low-Level Engine.dll", None),
    "Sxlrt233_DllMain": ("sxlrt233.dll", None),
    "UString_ctor_cstr": ("Low-Level Engine.dll", "??0UString@@QAE@PBD@Z"),
    "FSFileSpec_ctor_UString": ("Low-Level Engine.dll", "??0FSFileSpec@@QAE@ABVUString@@@Z"),
    "UMSetFloatingPointPrecision": ("Low-Level Engine.dll", "?UMSetFloatingPointPrecision@@YAXK@Z"),
    "UMSetFloatingPointRoundingMode": ("Low-Level Engine.dll", "?UMSetFloatingPointRoundingMode@@YAXK@Z"),
    "GEInitialize": ("Low-Level Engine.dll", "?GEInitialize@@YAJAAVFSFileSpec@@QAUHINSTANCE__@@@Z"),
    "PIMModule_GetModule": ("Low-Level Engine.dll", "?GetModule@PIMModule@@CAJ_NAAPAV1@@Z"),
    "EERM_ReadScript": ("Default.dll", 0xde77),       # not exported: a DB function start
}


def test_curated_entries_are_the_db_addresses_at_the_load_bases(cfg):
    entries = tomllib.loads((ROOT / "globals.toml").read_text())["entries"]
    bases = {m["name"]: m["base"] for m in cfg["aux_modules"]}
    default = {e["name"]: e["rva"] for e in db("Default.dll")["exports"]}
    for name, addr in entries.items():
        module, export = GLOBAL_EXPORTS.get(name, ("Default.dll", name))
        doc = db(module)
        if export is None:
            rva = int(doc["module"]["entry_rva"], 16)
        elif isinstance(export, int):
            rva = export
            assert any(int(f["rva"], 16) == rva for f in doc["functions"]), name
        else:
            rva = int(({e["name"]: e["rva"] for e in doc["exports"]} if module != "Default.dll" else default)[export], 16)
        assert addr == bases[module] + rva, name
    assert set(default) <= set(entries)            # the whole random-map plugin API


def test_sentinels_are_outside_every_module(cfg):
    spans = [(0x00400000, 0x00a82000)] + [(m["base"], m["base"] + m["size"]) for m in cfg["aux_modules"]]
    values = [v for v in cfg["hooks"].values() if isinstance(v, int)] + [cfg["translate"]["animation_counter"]]
    values += [g["addr"] for g in cfg["globals"].values()]
    for v in values:
        assert not any(lo <= v < hi for lo, hi in spans), hex(v)
        assert v < 0x01000000                        # below the heap
