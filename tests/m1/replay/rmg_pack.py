#!/usr/bin/env python3
"""Turn an rmg_capture.js capture directory into replay packs (.rrp) for rmg_replay.

  rmg_pack.py list CAPTURE_DIR                  table of the captured calls
  rmg_pack.py pack CAPTURE_DIR -o PACK_DIR      one PACK_DIR/<seq>-<export>.rrp per call, index.tsv
        [--seq 3,5-9] [--force] [--verify]

The pack layout is defined in rmg_replay.h; the capture format in docs/reference/rmg.md section 6.
Packs hold game-derived bytes: keep them out of git (analysis/m1-captures/, or C:\\EEPort\\captures).
Standard library only; runs on the Mac and on legion.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

KIND = {"ctx": 1, "stack": 2, "module_data": 3, "heap": 4, "arg": 5, "heap_hw": 6}
OP = {"new": 1, "new[]": 2, "malloc": 3, "calloc": 4, "realloc": 5, "delete": 6, "delete[]": 7, "free": 8,
      "fopen": 16, "fgets": 17, "fclose": 18}
MODS = ["sxlrt233.dll", "Low-Level Engine.dll", "Default.dll"]
CTX_SIZE = 0x3D0


def h2i(s):
    return int(s, 16) if isinstance(s, str) else int(s)


def tag(s):
    return struct.unpack("<I", s.encode())[0]


def pad4(b):
    return b + b"\0" * (-len(b) % 4)


class Capture:
    def __init__(self, root):
        self.root = root
        cdir = os.path.join(root, "calls")
        if not os.path.isdir(cdir):
            sys.exit("%s: no calls/ directory (is this an rmg_capture.js output?)" % root)
        self.calls = []
        for name in sorted(os.listdir(cdir)):
            if name.endswith(".json"):
                with open(os.path.join(cdir, name), encoding="utf-8") as f:
                    self.calls.append(json.load(f))

    def blob(self, h, verify=False):
        path = os.path.join(self.root, "blobs", h[:2], h + ".bin")
        with open(path, "rb") as f:
            data = f.read()
        if verify and hashlib.sha256(data).hexdigest() != h:
            raise ValueError("blob %s is corrupt" % h)
        return data

    def assemble(self, chunks, size, verify=False):
        data = b"".join(self.blob(h, verify) for h in chunks)
        if len(data) != size:
            raise ValueError("assembled %d bytes, expected %d" % (len(data), size))
        return data


def label(region, ctx):
    kind = region["kind"]
    if kind == "ctx":
        return "ctx"
    if kind in ("module_data", "module_data_diag"):
        return "%s .data" % region.get("module", "?").split(".")[0][:18]
    if kind == "stack":
        return "stack"
    if kind == "arg":
        return "arg UString"
    via = h2i(region["via"]) if "via" in region else None
    if via is not None and ctx <= via < ctx + CTX_SIZE:
        return "%s<-ctx+%#x" % (kind, via - ctx)
    if via is not None:
        return "%s<-%s@%#x" % (kind, region.get("from", "?"), via)
    return kind


def region_records(cap, regions, ctx, restore_only, verify):
    out = []
    for r in regions:
        kind = r["kind"]
        if kind not in KIND:
            continue                              # diagnostic regions are neither restored nor compared
        if restore_only and not r.get("restore"):
            continue
        if "error" in r:
            raise ValueError("region %s %s could not be read in the capture: %s" % (kind, r["addr"], r["error"]))
        data = cap.assemble(r["chunks"], r["size"], verify)
        lab = label(r, ctx).encode()[:31]
        out.append(struct.pack("<4I32s", h2i(r["addr"]), r["size"], KIND[kind], 1 if r.get("restore") else 0, lab) + pad4(data))
    return out


def section(t, records):
    body = struct.pack("<I", len(records)) + b"".join(records)
    return struct.pack("<II", tag(t), len(body)) + body


def build_pack(cap, c, verify=False):
    ctx = h2i(c["args"][0])
    mods = {m["name"].lower(): m for m in c["modules"]}
    mrec = []
    for name in MODS:
        m = mods.get(name.lower())
        if not m or not m.get("data"):
            raise ValueError("module %s missing from the manifest" % name)
        mrec.append(struct.pack("<32s5I", name.encode(), h2i(m["base"]), h2i(m["sizeOfImage"]), h2i(m["timestamp"]),
                                h2i(m["data"]["rva"]), m["data"]["size"]))
    inrg = region_records(cap, c["entry"]["regions"], ctx, True, verify)
    outr = region_records(cap, c["exit"]["regions"], ctx, False, verify)
    alog, content = [], True
    for e in c["allocs"]:
        op = OP[e["op"]]
        a, b = h2i(e["a"]), h2i(e.get("b", "0"))
        ret = h2i(e.get("ret", "0"))
        init = b""
        if op <= 5 and ret and e.get("size"):
            if "chunks" in e:
                init = cap.assemble(e["chunks"], e["size"], verify)
            else:
                content = False
        alog.append(struct.pack("<5I", op, a, b, ret, len(init)) + pad4(init))
    flog = []
    for e in c["files"]:
        op = OP[e["op"]]
        if op == 16:
            data = bytes.fromhex(e["path_hex"]) + b"\0" + e["mode"].encode("latin-1") + b"\0"
            flog.append(struct.pack("<5I", op, 0, 0, h2i(e["ret"]), len(data)) + pad4(data))
        elif op == 17:
            data = bytes.fromhex(e.get("data_hex", ""))
            flog.append(struct.pack("<5I", op, h2i(e["fp"]), e["n"], h2i(e["ret"]), len(data)) + pad4(data))
        else:
            flog.append(struct.pack("<5I", op, h2i(e["fp"]), 0, h2i(e["ret"]), 0))
    dbgs = []
    for s in c.get("debug", []):
        b = (s or "").encode("latin-1", "replace")
        dbgs.append(struct.pack("<I", len(b)) + pad4(b))
    sections = [section("MODS", mrec), section("INRG", inrg), section("ALOG", alog), section("FLOG", flog),
                section("OUTR", outr), section("DBGS", dbgs)]
    args = [h2i(a) for a in c["args"]] + [0] * (4 - len(c["args"]))
    fe, fx = c["entry"]["fpu"], c["exit"]["fpu"]
    hdr = struct.pack("<8sI4II4III4III48s", b"EERRP01\0", 128, c["seq"], max(c["gen"], 0), max(c["attempt"], 0), c["tid"],
                      h2i(c["esp"]), h2i(fe["cw"]), h2i(fx["cw"]), h2i(fe["sw"]), h2i(fe["tw"]), h2i(c["exit"]["eax"]),
                      len(c["args"]), *args, 1 if content else 0, len(sections), c["export"].encode())
    assert len(hdr) == 128
    return hdr + b"".join(sections)


def parse_seq(spec):
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def problems(c):
    """Reasons not to pack a call. Foreign values are not among them: they are mostly stale
    addresses in uninitialised memory, and the Windows replay shows whether any is really used."""
    p = list(c.get("warnings", []))
    for phase in ("entry", "exit"):
        p += ["%s region %s %s: %s" % (phase, r["kind"], r["addr"], r["error"]) for r in c[phase]["regions"] if "error" in r]
    return p


def cmd_list(a):
    cap = Capture(a.capture)
    print("seq\tgen\tatt\texport\teax\tin_regions\tin_bytes\tout_regions\tout_bytes\tallocs\tfiles\tforeign\tnotes")
    for c in cap.calls:
        e, x = c["entry"], c["exit"]
        print("\t".join(str(v) for v in (c["seq"], c["gen"], c["attempt"], c["export"].replace("EERMPIM", ""), x["eax"],
                                          len(e["regions"]), e["bytes"], len(x["regions"]), x["bytes"], len(c["allocs"]),
                                          len(c["files"]), "%d/%d" % (e["foreign"]["count"], x["foreign"]["count"]),
                                          len(c.get("warnings", [])))))


def cmd_pack(a):
    cap = Capture(a.capture)
    want = parse_seq(a.seq)
    os.makedirs(a.out, exist_ok=True)
    index, failed = [], 0
    for c in cap.calls:
        if want is not None and c["seq"] not in want:
            continue
        p = problems(c)
        if p and not a.force:
            print("skip seq %d %s: %s (use --force to pack anyway)" % (c["seq"], c["export"], "; ".join(map(str, p))))
            failed += 1
            continue
        try:
            data = build_pack(cap, c, a.verify)
        except (OSError, ValueError, KeyError) as e:
            print("skip seq %d %s: %s" % (c["seq"], c["export"], e))
            failed += 1
            continue
        name = "%06d-%s.rrp" % (c["seq"], c["export"].replace("EERMPIM", ""))
        with open(os.path.join(a.out, name), "wb") as f:
            f.write(data)
        index.append((c["seq"], c["gen"], c["attempt"], c["export"], name, len(data)))
    with open(os.path.join(a.out, "index.tsv"), "w", encoding="utf-8") as f:
        f.write("seq\tgen\tattempt\texport\tpack\tbytes\n")
        for row in index:
            f.write("\t".join(map(str, row)) + "\n")
    print("%d packs written to %s, %d skipped" % (len(index), a.out, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("capture")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("pack")
    p.add_argument("capture")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--seq", help="e.g. 0-10,14")
    p.add_argument("--force", action="store_true", help="pack calls with capture warnings")
    p.add_argument("--verify", action="store_true", help="re-hash every blob while packing")
    p.set_defaults(fn=cmd_pack)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
