"""The Ghidra-free translation inputs (tools/inputs/): the listing renderer on
hand-written encodings, the sidecar from the tracked DBs, and, when the game is
linked, the DLLs' inputs against the digests db/inputs.json pins.

    .venv/bin/python -m pytest -q tests/test_inputs.py
"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/inputs"))
import kitpath  # noqa: E402
import listing  # noqa: E402
import sidecar  # noqa: E402

KIT, _ = kitpath.take_kit([])
sys.path.insert(0, str(KIT / "tools"))
import game_config  # noqa: E402

#: (bytes, text) at 0x00401000: Ghidra's display for each rule listing.py implements.
CASES = [
    # displacements: disp8 signed and left out when zero, disp32 unsigned and kept when zero
    ("8b45f8", "MOV EAX,dword ptr [EBP + -0x8]"),
    ("8b8510ffffff", "MOV EAX,dword ptr [EBP + 0xffffff10]"),
    ("8b4500", "MOV EAX,dword ptr [EBP]"),
    ("8b0cfd00000000", "MOV ECX,dword ptr [EDI*0x8 + 0x0]"),
    ("8b4c2404", "MOV ECX,dword ptr [ESP + 0x4]"),
    ("8b440804", "MOV EAX,dword ptr [EAX + ECX*0x1 + 0x4]"),
    ("ff248578563412", "JMP dword ptr [EAX*0x4 + 0x12345678]"),
    # absolute addresses: 8 digits, except through a segment; A0-A3 carry no size
    ("8b0d21436500", "MOV ECX,dword ptr [0x00654321]"),
    ("ff1578563412", "CALL dword ptr [0x12345678]"),
    ("a178563412", "MOV EAX,[0x12345678]"),
    ("a221436500", "MOV [0x00654321],AL"),
    ("64a100000000", "MOV EAX,FS:[0x0]"),
    ("64892500000000", "MOV dword ptr FS:[0x0],ESP"),
    ("8d4c2404", "LEA ECX,[ESP + 0x4]"),
    # immediates
    ("83c0f8", "ADD EAX,-0x8"),
    ("83f8ff", "CMP EAX,-0x1"),
    ("83caff", "OR EDX,0xffffffff"),
    ("83e4f8", "AND ESP,0xfffffff8"),
    ("66838e00010000ff", "OR word ptr [ESI + 0x100],0xffff"),
    ("6aff", "PUSH -0x1"),
    ("6878563412", "PUSH 0x12345678"),
    ("6bf6fd", "IMUL ESI,ESI,-0x3"),
    ("69c000f0ffff", "IMUL EAX,EAX,-0x1000"),
    ("3cff", "CMP AL,0xff"),
    ("804809ff", "OR byte ptr [EAX + 0x9],0xff"),
    ("c745fc78563412", "MOV dword ptr [EBP + -0x4],0x12345678"),
    ("a9f0ffffff", "TEST EAX,0xfffffff0"),
    ("d1e9", "SHR ECX,0x1"),
    ("c1e005", "SHL EAX,0x5"),
    ("d3fa", "SAR EDX,CL"),
    ("c20400", "RET 0x4"),
    ("c3", "RET"),
    # branches: 8-digit targets, Ghidra's condition names
    ("e800000000", "CALL 0x00401005"),
    ("eb05", "JMP 0x00401007"),
    ("7405", "JZ 0x00401007"),
    ("7505", "JNZ 0x00401007"),
    ("7205", "JC 0x00401007"),
    ("0f8300010000", "JNC 0x00401106"),
    ("0f94c1", "SETZ CL"),
    ("0f92c1", "SETC CL"),
    ("ffd3", "CALL EBX"),
    # string instructions: implicit operands, REP as a suffix
    ("f3a5", "MOVSD.REP ES:EDI,ESI"),
    ("a5", "MOVSD ES:EDI,ESI"),
    ("f2ae", "SCASB.REPNE ES:EDI"),
    ("f3a6", "CMPSB.REPE ES:EDI,ESI"),
    ("f3ab", "STOSD.REP ES:EDI"),
    ("aa", "STOSB ES:EDI"),
    # x87
    ("d8c1", "FADD ST0,ST1"),
    ("d8c9", "FMUL ST1"),
    ("d8d1", "FCOM"),
    ("d8d2", "FCOM ST2"),
    ("d8d9", "FCOMP"),
    ("d8e2", "FSUB ST0,ST2"),
    ("d8f9", "FDIVR ST0,ST1"),
    ("d9c0", "FLD ST0"),
    ("d9c9", "FXCH"),
    ("dcc0", "FADD ST0,ST0"),
    ("ddd8", "FSTP ST0"),
    ("dec1", "FADDP"),
    ("dee9", "FSUBP"),
    ("ded9", "FCOMPP"),
    ("dfe0", "FNSTSW AX"),
    ("d9e8", "FLD1"),
    ("d9ee", "FLDZ"),
    ("d9e0", "FCHS"),
    ("d945fc", "FLD float ptr [EBP + -0x4]"),
    ("dd45f8", "FLD double ptr [EBP + -0x8]"),
    ("d91c24", "FSTP float ptr [ESP]"),
    ("db45f8", "FILD dword ptr [EBP + -0x8]"),
    ("df6df0", "FILD qword ptr [EBP + -0x10]"),
    ("db5df8", "FISTP dword ptr [EBP + -0x8]"),
    ("da4df8", "FIMUL dword ptr [EBP + -0x8]"),
    # plain
    ("0fbe08", "MOVSX ECX,byte ptr [EAX]"),
    ("660fb610", "MOVZX DX,byte ptr [EAX]"),
    ("0fafc1", "IMUL EAX,ECX"),
    ("0fadd0", "SHRD EAX,EDX,CL"),
    ("99", "CDQ"),
    ("c9", "LEAVE"),
    ("9e", "SAHF"),
    ("90", "NOP"),
    ("0f31", "RDTSC"),
]


@pytest.mark.parametrize("raw,text", CASES)
def test_renderer_prints_ghidras_syntax(raw, text):
    code = bytes.fromhex(raw)
    r = listing.Renderer(code + bytes(16), 0x00401000)
    assert r.decode(0x00401000) == (len(code), text)


@pytest.mark.parametrize("raw", ["f00108",       # LOCK ADD: no such instruction in the modules
                                 "f3c3"])        # REP RET
def test_renderer_refuses_what_it_does_not_know(raw):
    r = listing.Renderer(bytes.fromhex(raw) + bytes(16), 0x00401000)
    with pytest.raises(listing.ListingError):
        r.decode(0x00401000)


def test_lines_follow_the_ranges_and_stop_at_their_ends():
    code = bytes.fromhex("5589e5c3" "cccc" "c3")
    r = listing.Renderer(code + bytes(16), 0x1000)
    assert r.lines([(0x1006, 0x1007), (0x1000, 0x1004)]) == [
        "00001000  PUSH EBP", "00001001  MOV EBP,ESP", "00001003  RET", "00001006  RET"]
    with pytest.raises(listing.ListingError):
        r.lines([(0x1000, 0x1002)])              # MOV EBP,ESP runs past the range


@pytest.fixture(scope="module")
def cfg():
    return game_config.load(ROOT)


def modules(cfg):
    yield cfg["game"]["executable"], cfg["developer_exe_path"]
    for m in cfg["aux_modules"]:
        yield m["name"], m["path"]


def load_db(name):
    return json.loads((ROOT / "db/functions" / (name.lower().replace(" ", "-") + ".json")).read_text())


def test_sidecar_carries_every_translated_entry(cfg):
    for name, _ in modules(cfg):
        db = load_db(name)
        doc = sidecar.funcdb(db)
        keep = sidecar.translated(db)
        assert doc["schema"] == "recomp-funcdb/1" and doc["sha256"] == db["module"]["sha256"]
        assert [e[0] for e in doc["entry_points"]] == [r["va"] for r in keep]
        assert len(doc["excluded_functions"]) == len(db["functions"]) - len(keep)
        for r in keep:
            lo, hi = sidecar.body_ranges(db, r)[0][0], sidecar.body_ranges(db, r)[-1][1]
            assert lo <= int(r["va"], 16) < hi
            assert sum(b - a for a, b in sidecar.body_ranges(db, r)) == r["size"]


def test_functions_tsv_format():
    assert sidecar.functions_tsv([]) == "address\tname\tbytes\tstatus\tmessage\n\n"
    assert sidecar.functions_tsv([(0x401000, "fn_00401000", 7)]) == (
        "address\tname\tbytes\tstatus\tmessage\n00401000\tfn_00401000\t7\tlisting\t\n")
    assert sidecar.listing_name({"va": "0x10001000", "name": "a  b\tc"}) == "a b c"
    assert sidecar.listing_name({"va": "0x10001000"}) == "fn_10001000"


def test_every_module_has_pinned_inputs(cfg):
    pins = json.loads((ROOT / "db/inputs.json").read_text())["inputs"]
    assert sorted(pins) == sorted(name for name, _ in modules(cfg))


def test_dll_inputs_are_the_pinned_ones(cfg):
    """The DLLs' inputs (the executable's take 12 s more) match db/inputs.json:
    the renderer, the DB and the decoder agree with what was compared against
    Ghidra. Needs the game linked (tools/setup.py)."""
    import export
    pins = json.loads((ROOT / "db/inputs.json").read_text())["inputs"]
    found = [(n, p) for n, p in modules(cfg) if n != cfg["game"]["executable"] and Path(p).is_file()]
    if not found:
        pytest.skip("the game is not linked (tools/setup.py --install)")
    for name, path in found:
        tsv, funcdb, listings = export.build(load_db(name), path)
        assert export.digest(tsv, funcdb, listings) == pins[name], name
