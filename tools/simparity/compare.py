#!/usr/bin/env python3
"""Compare two world-checksum streams (ee-simparity/1), Mac or Windows (P4-C).

    .venv/bin/python tools/simparity/compare.py A.csv B.csv [--context N] [--json OUT]

A stream is what mods/simparity/simparity.c (Mac) or
windows/frida/checksum_trace.js (Windows) writes, one row per line:

  G,seed_orig,seed,humans                    a game starts (EEMStartLoading)
  C,ct,total_pt,pts,ptps,dt_bits,speed_bits  a communications tick's PTs arrive
  T,pt,ct,cs,cs2,rng,inputs,units            after each physics tick (PT)
  S,pt,ct,type,slot                          a message handed to the server
  U,pt,i,vtable,id,x,y,hp,cs,cs2             one object of the checksum list
  W,ct,pt,dump,flags,cs                      the game's own checksum call (a multiplayer CT;
                                             the host's value never goes on the wire)
  R,pt,fn,caller,rng_in,rng_out              a call of a function that draws random numbers,
                                             in the PT window the tracer was asked for
  P,pt,slot,w0,..,w11                        a player's resources and counters (+0xaf0), which
                                             no checksum covers
  K,pt,fn,caller,self,a0,a1,a2,eax           a call of a logged function (self: the player slot
                                             ECX is, or -1); compared without a2 and eax, which
                                             are often stack garbage or a void return

The streams agree when every PT both of them reached has the same row: the
game's checksum (cs, flags 1), the second checksum (cs2), the world RNG state,
the executed-input count, the object count and the CT, counted over the CTs
that brought PTs (how many lobby, loading and pause CTs come between them
depends on wall-clock time). W rows (multiplayer) are matched by PT. The report names the
first PT that differs and which fields, the CT schedule (C rows) and the
messages (S rows) up to there, and, where both streams have per-object rows
(U) at or before that PT, the objects that differ (matched by vtable and id).
Player rows (P) and logged calls (K) are compared on their own: the first PT
at which a player's words differ, and the first logged call that differs.
Exit status: 0 agree, 1 differ, 2 unusable input. docs/reference/simparity.md.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

T_FIELDS = ("ct", "cs", "cs2", "rng", "inputs", "units")


class Stream:
    def __init__(self, path):
        self.path = Path(path)
        self.header = []
        self.games = []  # (G row, ...) per game; the first game with ticks is used
        self.game = None
        self.ticks = {}   # pt -> dict
        self.cts = []     # C rows
        self.sends = []   # S rows
        self.units = {}   # pt -> list of dicts
        self.checks = {}  # W rows by CT
        self.draws = []   # R rows in order
        self.players = {}  # pt -> {slot: words}
        self.calls = []   # K rows in order


def load(path):
    s = Stream(path)
    games = []
    cur = None

    def new_game(g):
        return {"G": g, "ticks": {}, "cts": [], "sends": [], "units": {}, "checks": [], "draws": [],
                "players": {}, "calls": []}

    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            s.header.append(line)
            continue
        f = line.split(",")
        kind = f[0]
        if kind == "G":
            cur = new_game({"seed_orig": int(f[1]), "seed": int(f[2]), "humans": int(f[3])})
            games.append(cur)
            continue
        if cur is None:
            cur = new_game(None)
            games.append(cur)
        try:
            if kind == "T":
                cur["ticks"][int(f[1])] = {"ct": int(f[2]), "cs": f[3].lower(), "cs2": f[4].lower(),
                                           "rng": f[5].lower(), "inputs": int(f[6]), "units": int(f[7])}
            elif kind == "C":
                cur["cts"].append({"ct": int(f[1]), "total_pt": int(f[2]), "pts": int(f[3]),
                                   "ptps": int(f[4]), "dt": f[5].lower(), "speed": f[6].lower()})
            elif kind == "S":
                cur["sends"].append({"pt": int(f[1]), "ct": int(f[2]), "type": f[3].lower(),
                                     "slot": f[4].lower()})
            elif kind == "W":
                cur["checks"].append({"ct": int(f[1]), "pt": int(f[2]), "dump": int(f[3]),
                                      "flags": int(f[4]), "cs": f[5].lower()})
            elif kind == "R":
                cur["draws"].append({"pt": int(f[1]), "fn": f[2].lower(), "caller": f[3].lower(),
                                     "rng_in": f[4].lower(), "rng_out": f[5].lower()})
            elif kind == "P":
                words = tuple(w.lower() for w in f[3:])
                if len(words) != 12:
                    raise ValueError(line)
                cur["players"].setdefault(int(f[1]), {})[int(f[2])] = words
            elif kind == "K":
                cur["calls"].append({"pt": int(f[1]), "fn": f[2].lower(), "caller": f[3].lower(),
                                     "self": int(f[4]), "args": tuple(x.lower() for x in f[5:8]),
                                     "eax": f[8].lower()})
            elif kind == "U":
                cur["units"].setdefault(int(f[1]), []).append(
                    {"i": int(f[2]), "vtable": f[3].lower(), "id": int(f[4]), "x": int(f[5]),
                     "y": int(f[6]), "hp": int(f[7]), "cs": f[8].lower(), "cs2": f[9].lower()})
        except (IndexError, ValueError):
            s.header.append("# unparsed: " + line)  # a truncated last line
    s.games = games
    with_ticks = [g for g in games if g["ticks"]]
    g = with_ticks[0] if with_ticks else (games[0] if games else new_game(None))
    s.game, s.ticks, s.cts, s.sends, s.units = g["G"], g["ticks"], g["cts"], g["sends"], g["units"]
    # CT numbers count the lobby and loading CTs, and the CTs of a pause, and how
    # many of those there are depends on wall-clock time. The CTs compared are
    # the ones that brought PTs (C rows), numbered from 0; a send keeps its
    # distance from the game's first such CT.
    s.ct0 = s.cts[0]["ct"] if s.cts else (s.ticks[min(s.ticks)]["ct"] if s.ticks else 0)
    ordinal = {}
    for i, c in enumerate(s.cts):
        ordinal.setdefault(c["ct"], i)
        c["abs_ct"], c["ct"] = c["ct"], i
    for t in s.ticks.values():
        t["abs_ct"] = t["ct"]
        t["ct"] = ordinal.get(t["ct"], -1)
    for x in s.sends:
        x["ct"] -= s.ct0
    s.checks = {w["ct"]: w for w in g["checks"]}
    s.draws = g["draws"]
    s.players, s.calls = g["players"], g["calls"]
    return s


def flags2(s):
    """The second checksum column's flags, from the stream's first line."""
    for h in s.header:
        if "flags2=" in h:
            return h.split("flags2=")[1].split()[0]
    return "0x1"


def describe(s):
    if not s.ticks:
        return "no ticks"
    last = max(s.ticks)
    ptps = sorted({c["ptps"] for c in s.cts})
    seed = s.game["seed"] if s.game else "?"
    return "seed %s, PTs 1..%d, %d CTs from CT %d, PT/s %s, %d sends, cs %s at PT %d" % (
        seed, last, len(s.cts), s.ct0, ptps, len(s.sends), s.ticks[last]["cs"], last)


def diff_units(a, b, with_cs2=True):
    """Objects that differ between two U snapshots, matched by (vtable, id)."""
    def key(u):
        return (u["vtable"], u["id"])
    ka = {key(u): u for u in a}
    kb = {key(u): u for u in b}
    out = []
    for k in sorted(set(ka) | set(kb)):
        ua, ub = ka.get(k), kb.get(k)
        if ua is None or ub is None:
            out.append({"vtable": k[0], "id": k[1], "only_in": "A" if ub is None else "B"})
            continue
        fields = [f for f in ("x", "y", "hp", "cs", "cs2") if ua[f] != ub[f] and (with_cs2 or f != "cs2")]
        if fields:
            out.append({"vtable": k[0], "id": k[1], "fields": {f: (ua[f], ub[f]) for f in fields}})
    order = [u["i"] for u in a] == [ka[key(u)]["i"] for u in a] and \
        [key(u) for u in a] == [key(u) for u in b]
    return out, order


# The PT entry itself is in the draw list; RTSPhysics::Main calls it from one of two
# sites depending on whether it is catching up, which is timing, not simulation.
TIMING_CALLERS = {"00546c63"}


def same_draw(ra, rb):
    if ra["fn"] in TIMING_CALLERS:
        return {k: v for k, v in ra.items() if k != "caller"} == {k: v for k, v in rb.items() if k != "caller"}
    return ra == rb


def first_draw_difference(da, db):
    """The first R row (in order) that differs, with the rows around it; only PTs both traced."""
    pts = set(r["pt"] for r in da) & set(r["pt"] for r in db)
    da = [r for r in da if r["pt"] in pts]
    db = [r for r in db if r["pt"] in pts]
    for i, (ra, rb) in enumerate(zip(da, db)):
        if not same_draw(ra, rb):
            return {"index": i, "pt": ra["pt"], "a": da[max(0, i - 3):i + 6], "b": db[max(0, i - 3):i + 6]}
    if len(da) != len(db):
        i = min(len(da), len(db))
        return {"index": i, "pt": (da or db)[i - 1]["pt"] if i else None, "a": da[i - 3:i + 6],
                "b": db[i - 3:i + 6]}
    return None


def first_player_difference(pa, pb):
    """The first PT both streams have P rows for at which some player's words differ."""
    for pt in sorted(set(pa) & set(pb)):
        if pa[pt] != pb[pt]:
            slots = sorted(set(pa[pt]) | set(pb[pt]))
            out = []
            for slot in slots:
                wa, wb = pa[pt].get(slot), pb[pt].get(slot)
                if wa != wb:
                    words = None if wa is None or wb is None else \
                        [i for i in range(len(wa)) if wa[i] != wb[i]]
                    out.append({"slot": slot, "words": words, "a": wa, "b": wb})
            return {"pt": pt, "players": out}
    return None


def call_key(c):
    """What a K row is compared on: the third stack word and EAX are often not the function's
    (stack garbage beyond its arguments, a void return), so only the first two arguments count."""
    return (c["pt"], c["fn"], c["caller"], c["self"], c["args"][:2])


def first_call_difference(ca, cb, last_pt=None):
    """The first K row (in order) that differs; only PTs up to last_pt (both streams reached)."""
    if last_pt is not None:
        ca = [c for c in ca if c["pt"] <= last_pt]
        cb = [c for c in cb if c["pt"] <= last_pt]
    for i, (x, y) in enumerate(zip(ca, cb)):
        if call_key(x) != call_key(y):
            return {"index": i, "pt": x["pt"], "a": ca[max(0, i - 3):i + 4], "b": cb[max(0, i - 3):i + 4]}
    if len(ca) != len(cb):
        i = min(len(ca), len(cb))
        return {"index": i, "pt": (ca if len(ca) > i else cb)[i]["pt"], "a": ca[i:i + 4], "b": cb[i:i + 4]}
    return None


def compare(a, b, context=5):
    rep = {"a": str(a.path), "b": str(b.path), "agree": True}
    common = sorted(set(a.ticks) & set(b.ticks))
    fields = T_FIELDS if flags2(a) == flags2(b) else tuple(f for f in T_FIELDS if f != "cs2")
    rep["fields_compared"] = list(fields)
    rep["common_pts"] = len(common)
    rep["last_common_pt"] = common[-1] if common else None
    rep["seed"] = (a.game and a.game["seed"], b.game and b.game["seed"])
    first = None
    for pt in common:
        ta, tb = a.ticks[pt], b.ticks[pt]
        bad = [f for f in fields if ta[f] != tb[f]]
        if bad:
            first = (pt, bad)
            break
    # The CT schedule: what each CT brought (C rows), compared in order.
    ct_first = None
    def key(c):
        return {k: v for k, v in c.items() if k != "abs_ct"}
    for i, (ca, cb) in enumerate(zip(a.cts, b.cts)):
        if key(ca) != key(cb):
            ct_first = {"index": i, "a": ca, "b": cb}
            break
    rep["ct_schedule_first_difference"] = ct_first
    # The game's own checksums (multiplayer), matched by the PT they describe.
    wa = {w["pt"]: w["cs"] for w in a.checks.values()}
    wb = {w["pt"]: w["cs"] for w in b.checks.values()}
    common_w = sorted(set(wa) & set(wb))
    rep["game_checksums"] = {"common": len(common_w),
                             "differ": [p for p in common_w if wa[p] != wb[p]][:20]}
    # Hidden state: players' resources (P rows) and logged calls (K rows).
    if a.players and b.players:
        rep["players"] = {"common": len(set(a.players) & set(b.players)),
                          "first_difference": first_player_difference(a.players, b.players)}
    if a.calls and b.calls:
        rep["calls"] = {"a": len(a.calls), "b": len(b.calls),
                        "first_difference": first_call_difference(a.calls, b.calls, rep["last_common_pt"])}
    if first is None:
        rep["summary"] = ("agree on all %d common PTs (1..%s)" % (len(common), rep["last_common_pt"])
                          if common else "no common PTs")
        rep["agree"] = bool(common) and not rep["game_checksums"]["differ"]
        if rep["game_checksums"]["differ"]:
            rep["summary"] += "; the game's own checksums differ at PT %s" % rep["game_checksums"]["differ"][0]
        return rep
    pt, bad = first
    rep["agree"] = False
    rep["first_pt"] = pt
    rep["fields"] = bad
    rep["rows"] = [{"pt": p, "a": a.ticks.get(p), "b": b.ticks.get(p)}
                   for p in range(max(1, pt - context), pt + context + 1) if p in a.ticks or p in b.ticks]
    sa = [x for x in a.sends if x["pt"] <= pt]
    sb = [x for x in b.sends if x["pt"] <= pt]
    rep["sends_up_to_first"] = {"a": sa[-20:], "b": sb[-20:],
                                "types_a": dict(Counter(x["type"] for x in sa)),
                                "types_b": dict(Counter(x["type"] for x in sb))}
    snap = [p for p in sorted(set(a.units) & set(b.units)) if p <= pt]
    if snap:
        p = snap[-1]
        diffs, same_order = diff_units(a.units[p], b.units[p], "cs2" in fields)
        rep["units"] = {"pt": p, "differ": diffs[:200], "count": len(diffs),
                        "objects": (len(a.units[p]), len(b.units[p])), "same_order": same_order}
    if a.draws and b.draws:
        rep["draws"] = first_draw_difference(a.draws, b.draws)
    rep["summary"] = "first difference at PT %d (%s); %d earlier PTs agree" % (
        pt, ", ".join(bad), common.index(pt))
    return rep


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--context", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    args = parser.parse_args()
    try:
        a, b = load(args.a), load(args.b)
    except OSError as e:
        print("cannot read: %s" % e, file=sys.stderr)
        return 2
    print("A %s: %s" % (args.a, describe(a)))
    print("B %s: %s" % (args.b, describe(b)))
    if not a.ticks or not b.ticks:
        return 2
    rep = compare(a, b, args.context)
    print(rep["summary"])
    if rep["game_checksums"]["common"]:
        print("game checksums (W rows): %d common PTs, %d differ %s" % (
            rep["game_checksums"]["common"], len(rep["game_checksums"]["differ"]),
            rep["game_checksums"]["differ"][:5]))
    if rep.get("players"):
        d = rep["players"]["first_difference"]
        print("players (P rows): %d common PTs; %s" % (
            rep["players"]["common"], "agree" if d is None else "first differ at PT %d" % d["pt"]))
        for x in (d or {}).get("players", []):
            print("  slot %d words %s: A %s / B %s" % (x["slot"], x["words"], x["a"], x["b"]))
    if rep.get("calls"):
        d = rep["calls"]["first_difference"]
        print("logged calls (K rows): %d / %d; %s" % (
            rep["calls"]["a"], rep["calls"]["b"], "agree" if d is None else
            "first differ at row %d, PT %d" % (d["index"], d["pt"])))
        for side in ("a", "b"):
            for c in (d or {}).get(side, []):
                print("  %s PT %d %s from %s self %d args %s eax %s" % (
                    side.upper(), c["pt"], c["fn"], c["caller"], c["self"], " ".join(c["args"]), c["eax"]))
    if rep["ct_schedule_first_difference"]:
        d = rep["ct_schedule_first_difference"]
        print("CT schedule differs at C row %d: A %s / B %s" % (d["index"], d["a"], d["b"]))
    if not rep["agree"] and "rows" in rep:
        for r in rep["rows"]:
            print("  PT %6d  A %s" % (r["pt"], r["a"]))
            print("            B %s" % r["b"])
        s = rep["sends_up_to_first"]
        print("messages sent up to there: A %s / B %s" % (s["types_a"], s["types_b"]))
        if rep.get("draws"):
            d = rep["draws"]
            print("draws (R rows) first differ at row %d, PT %s:" % (d["index"], d["pt"]))
            for side in ("a", "b"):
                for r in d[side]:
                    print("  %s PT %d %s from %s rng %s -> %s" % (side.upper(), r["pt"], r["fn"], r["caller"],
                                                               r["rng_in"], r["rng_out"]))
        if "units" in rep:
            u = rep["units"]
            print("objects at PT %d: %d / %d, same order: %s; %d differ" % (
                u["pt"], u["objects"][0], u["objects"][1], u["same_order"], u["count"]))
            for d in u["differ"][:20]:
                print("   ", d)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1))
    return 0 if rep["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
