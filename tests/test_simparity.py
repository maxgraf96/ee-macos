"""tools/simparity/compare.py on synthetic streams (no game, no game data):
agreement, the first differing PT and its fields, the CT schedule, per-object
differences matched by vtable and id, a truncated last line, and the Mac and
Windows tracers' shared row format (docs/reference/simparity.md)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/simparity"))
import compare  # noqa: E402


def _load_runner():
    """tools/simparity/run.py by path (tools/run.py has the same module name)."""
    import importlib.util
    for sub in ("tools/boot", "tools"):
        if str(ROOT / sub) not in sys.path:
            sys.path.append(str(ROOT / sub))
    spec = importlib.util.spec_from_file_location("simparity_run", ROOT / "tools/simparity/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load_runner()


def stream(tmp_path, name, ticks, cts=None, units=None, tail=""):
    lines = ["# ee-simparity/1 test", "G,23147,1000,1"]
    for c in cts or [(12, 14, 14), (13, 28, 14)]:
        lines.append("C,%d,%d,%d,40,41c80000,3f800000" % c)
    for pt, cs, rng in ticks:
        lines.append("T,%d,%d,%08x,%08x,%08x,0,%d" % (pt, 12 + (pt - 1) // 14, cs, cs, rng, 30))
        for u in (units or {}).get(pt, []):
            lines.append("U,%d,%d,%s,%d,%d,%d,%d,%08x,00000000" % ((pt,) + u))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n" + tail)
    return path


def ticks(n, cs_at=None, rng_at=None):
    out = []
    for pt in range(1, n + 1):
        cs, rng = 0x1000 + pt, 0x55 * pt
        if cs_at is not None and pt >= cs_at:
            cs += 1
        if rng_at is not None and pt >= rng_at:
            rng += 7
        out.append((pt, cs, rng))
    return out


def test_agree(tmp_path):
    a = compare.load(stream(tmp_path, "a.csv", ticks(28)))
    b = compare.load(stream(tmp_path, "b.csv", ticks(20)))
    rep = compare.compare(a, b)
    assert rep["agree"] and rep["common_pts"] == 20 and rep["last_common_pt"] == 20
    assert a.game["seed"] == 1000 and "PT/s [40]" in compare.describe(a)


def test_first_difference(tmp_path):
    a = compare.load(stream(tmp_path, "a.csv", ticks(28)))
    b = compare.load(stream(tmp_path, "b.csv", ticks(28, rng_at=9)))
    rep = compare.compare(a, b)
    assert not rep["agree"] and rep["first_pt"] == 9 and rep["fields"] == ["rng"]
    assert rep["summary"].startswith("first difference at PT 9")
    assert [r["pt"] for r in rep["rows"]] == list(range(4, 15))


def test_ct_schedule_and_truncated_line(tmp_path):
    a = compare.load(stream(tmp_path, "a.csv", ticks(14), cts=[(12, 14, 14), (13, 28, 14)]))
    b = compare.load(stream(tmp_path, "b.csv", ticks(14), cts=[(12, 14, 14), (13, 27, 13)],
                            tail="T,15,13,0000"))
    rep = compare.compare(a, b)
    assert rep["agree"]  # the ticks agree...
    assert rep["ct_schedule_first_difference"]["index"] == 1  # ...the schedule does not
    assert 15 not in b.ticks and any("unparsed" in h for h in b.header)


def test_units_matched_by_vtable_and_id(tmp_path):
    ua = [(0, "00831494", 7, 10, 12, 5000, 0x1416), (1, "00831494", 9, 3, 4, 25, 0x20)]
    ub = [(0, "00831494", 9, 3, 4, 25, 0x20), (1, "00831494", 7, 11, 12, 5000, 0x1417)]
    a = compare.load(stream(tmp_path, "a.csv", ticks(5), units={4: ua}))
    b = compare.load(stream(tmp_path, "b.csv", ticks(5, cs_at=5), units={4: ub}))
    rep = compare.compare(a, b)
    assert rep["first_pt"] == 5 and rep["units"]["pt"] == 4
    assert rep["units"]["same_order"] is False
    (d,) = rep["units"]["differ"]
    assert d["id"] == 7 and d["fields"]["x"] == (10, 11)


def test_tracers_write_the_same_rows():
    """Both tracers declare the same row kinds with the same fields."""
    rows = re.compile(r"^\s*(?://|\*)?\s*([GCTSUWRPK]),([a-z_0-9,.]+)", re.M)
    mac = dict(rows.findall((ROOT / "mods/simparity/simparity.c").read_text()))
    win = dict(rows.findall((ROOT / "windows/frida/checksum_trace.js").read_text()))
    doc = dict(rows.findall(compare.__doc__))
    assert set(mac) == set(win) == set(doc) == set("GCTSUWRPK")
    for kind in "GCTSUWRPK":
        assert mac[kind].split() == win[kind].split() == doc[kind].split()


def test_first_draw_difference(tmp_path):
    rows = ["R,9,%08x,00450000,%08x,%08x" % (0x45b064 + i, 0x100 + i, 0x101 + i) for i in range(5)]
    a = stream(tmp_path, "a.csv", ticks(10), tail="\n".join(rows) + "\n")
    rows[2] = "R,9,0045b066,00450000,00000102,00000999"
    b = stream(tmp_path, "b.csv", ticks(10, rng_at=9), tail="\n".join(rows) + "\n")
    rep = compare.compare(compare.load(a), compare.load(b))
    assert rep["first_pt"] == 9 and rep["fields"] == ["rng"]
    assert rep["draws"]["index"] == 2 and rep["draws"]["b"][2]["rng_out"] == "00000999"


def test_players_and_calls(tmp_path):
    words = ["%08x" % w for w in range(12)]
    pa = ["P,%d,2,%s" % (pt, ",".join(words)) for pt in (1, 2, 3)]
    other = list(words)
    other[4] = "000000ff"
    garbage = ["81818181", "92866793"] + words[2:]  # words 0 and 1: never written, not compared
    pb = ["P,1,2,%s" % ",".join(garbage), pa[1], "P,3,2,%s" % ",".join(other)]
    ka = ["K,2,0042f783,00500000,2,00000004,0000000a,00000000,00000000",
          "K,3,0042f783,00500000,2,00000004,0000000b,00000000,00000000"]
    kb = [ka[0].replace(",00000000,00000000", ",12345678,9abcdef0"),  # a2, eax: not compared
          ka[1].replace("0000000b", "0000000c")]
    a = stream(tmp_path, "a.csv", ticks(5), tail="\n".join(pa + ka) + "\n")
    b = stream(tmp_path, "b.csv", ticks(5), tail="\n".join(pb + kb) + "\n")
    rep = compare.compare(compare.load(a), compare.load(b))
    assert rep["agree"]  # the checksums do not see it...
    d = rep["players"]["first_difference"]
    assert d["pt"] == 3 and d["players"][0]["slot"] == 2 and d["players"][0]["words"] == [4]
    c = rep["calls"]["first_difference"]  # ...the hidden state does
    assert c["index"] == 1 and c["pt"] == 3 and c["b"][1]["args"][1] == "0000000c"


def save_rows(pts, eax="00000000"):
    return ["K,%d,00540aca,004b89b3,-1,00000000,00000000,00000000,%s" % (pt, eax) for pt in pts]


def test_failed_and_missing_saves(tmp_path):
    """The bug of PT 24301: the Mac's autosave failed, so it skipped the save's side effect."""
    ok = compare.load(stream(tmp_path, "ok.csv", ticks(10), tail="\n".join(save_rows([5])) + "\n"))
    assert run.save_problems(ok, 40) == []
    bad = compare.load(stream(tmp_path, "bad.csv", ticks(10),
                              tail="\n".join(save_rows([5], eax="00000001")) + "\n"))
    assert run.save_problems(bad, 40) == ["the save at PT 5 failed (0x540aca returned 00000001)"]
    # 10 PTs at 1 PT/s with a 2-s interval: 5 saves due, 1 seen
    assert run.save_problems(ok, 1, autosave_ms=2000) == ["1 saves, 5 autosaves due"]


def test_save_timing(tmp_path):
    """A difference that follows a save which landed at different PTs is single-player timing."""
    a = compare.load(stream(tmp_path, "a.csv", ticks(20), tail="\n".join(save_rows([5, 15])) + "\n"))
    b = compare.load(stream(tmp_path, "b.csv", ticks(20, rng_at=17),
                            tail="\n".join(save_rows([5, 11])) + "\n"))
    assert run.save_timing(a, b) == (15, 11)
    assert run.save_timing(a, a) is None


def _savefile():
    sys.path.insert(0, str(ROOT / "tools/simparity"))
    import savefile
    return savefile


def test_explode_blast_vector():
    """PKWARE DCL explode: the example in zlib's contrib/blast/blast.c."""
    savefile = _savefile()
    assert savefile.explode(bytes.fromhex("00048224258f807f")) == b"AIAIAIAIAIAIA"


def test_save_sections_and_compare(tmp_path, capsys):
    """A save's header strings and sections, and where two saves differ."""
    import struct
    savefile = _savefile()
    packed = bytes.fromhex("00048224258f807f")

    def save(name, when):
        def s(text):
            b = text.encode() + b"\0"
            return struct.pack("<I", len(b)) + b
        body = s(name) + s("No game description provided.") + b"\0\0\0\0" + s(when) + b"\x05\0\0\0"
        sec = b"PK01" + struct.pack("<II", 13, 0) + packed
        body += struct.pack("<III", 0xE7, 4, len(sec)) + sec
        return struct.pack("<I", len(body)) + body

    a, b = tmp_path / "a.ees", tmp_path / "b.ees"
    a.write_bytes(save("AutoSave 2026.10.15 - 12h.01m.21s.ees", "Thu Oct 15 12:01:22 2026\n"))
    b.write_bytes(save("AutoSave 2026.10.15 - 13h.40m.02s.ees", "Thu Oct 15 13:40:03 2026\n"))
    sa, sb = savefile.Save(a), savefile.Save(b)
    assert [t for _, t in sa.strings][:2] == ["AutoSave 2026.10.15 - 12h.01m.21s.ees",
                                              "No game description provided."]
    assert [(sid, body) for _, sid, _, body in sa.sections] == [(4, b"AIAIAIAIAIAIA")]
    assert savefile.compare(sa, sb)  # only the strings differ
    out = capsys.readouterr().out
    assert "header string 0" in out and "section 4: the same" in out
