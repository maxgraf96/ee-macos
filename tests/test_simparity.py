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
    rows = re.compile(r"^\s*(?://|\*)?\s*([GCTSUWR]),([a-z_0-9,]+)", re.M)
    mac = dict(rows.findall((ROOT / "mods/simparity/simparity.c").read_text()))
    win = dict(rows.findall((ROOT / "windows/frida/checksum_trace.js").read_text()))
    doc = dict(rows.findall(compare.__doc__))
    assert set(mac) == set(win) == set(doc) == set("GCTSUWR")
    for kind in "GCTSUWR":
        assert mac[kind].split() == win[kind].split() == doc[kind].split()
