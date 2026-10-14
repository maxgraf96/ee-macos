"""Summarise a capture directory written by run.py into Markdown (stdout).

  python3 -I windows/frida/summarize.py <capture dir> [--inventory db/win32-imports.json] [--top 40]

Sections are emitted only for the scripts present in the capture (modules, threads,
fpu_hooks, iat_census). Pure standard library; runs on the Mac or on legion.
"""
import argparse
import collections
import json
import os

GAME = {"empire earth.exe", "low-level engine.dll", "sxlrt233.dll", "language.dll", "dx7hrdisplay.dll",
        "dx7hrtnldisplay.dll", "default.dll", "tcp protocol.dll", "miles sound system mixer.dll",
        "mss32.dll", "binkw32.dll", "ddraw.dll"}
CW_PC = {0: "24-bit", 2: "53-bit", 3: "64-bit", 1: "reserved"}
CW_RC = {0: "nearest", 1: "down", 2: "up", 3: "truncate"}


def load(d, name):
    p = os.path.join(d, name + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cw_meaning(cw):
    v = int(cw, 16)
    return f"PC={CW_PC[(v >> 8) & 3]}, RC={CW_RC[(v >> 10) & 3]}, EM=0x{v & 0x3f:02x}"


def role(th, api):
    st = th.get("start") or ""
    frames = " ".join(" ".join(s.get("frames") or []) + " " + s.get("eip", "") for s in th.get("scans", []))
    mods = th.get("eip_modules", {})
    calls = api.get(th["tid"], collections.Counter())
    names = {n for (_, n, _) in calls}
    if "TSThread" in st and (mods.get("DDRAW.dll") or mods.get("d3d9.dll")):
        return "render (DX7HRDisplay via GOG ddraw/d3d9)"
    if "?WriteLock@TSReadWriteLock@@QAEXXZ" in names and "?GetCurrentCommunicationsTick@NESynchronousServer@@UBEKXZ" in names:
        return "simulation (world tick: TSEvent wait, world write lock, NE comms tick)"
    if "?RefreshXY@ISMouse@@QAEXXZ" in names:
        return "input (ISMouse)"
    if "?GEUnpackRed@@YAEKK@Z" in names:
        return "texture/asset worker (GEUnpack*, memcpy, file finds)"
    if st.startswith("Empire Earth.exe+0x283798"):
        return "main (WinMain: message loop, UI, game loop)"
    if "SEConductor" in frames:
        return "music (SEConductor/DirectMusic)"
    if "SESoundManager" in frames:
        return "sound (SESoundManager::Scavenger)"
    if "NESynchronousClient" in frames:
        return "network (NESynchronousClient)"
    if "TSThread" in st and (mods.get("DDRAW.dll") or mods.get("d3d9.dll") or "Present" in frames):
        return "render (DX7HRDisplay via GOG ddraw/d3d9)"
    if "TSThread" in st and "binkw32" in frames:
        return "movie (Bink player)"
    if "TSThread" in st and "PeekMessageA" in names:
        return "engine TSThread (own message queue)"
    if "TSThread" in st:
        return "engine TSThread"
    for key, r in (("binkw32", "Bink decoder"), ("mss32", "Miles timer"), ("igd9", "Intel D3D9 driver"),
                   ("nvd", "NVIDIA driver"), ("Tp", "ntdll thread pool"), ("DSOUND", "DirectSound"),
                   ("dmime", "DirectMusic"), ("dmsynth", "DirectMusic synth"), ("DINPUT", "DirectInput"),
                   ("WINMM", "winmm timer"), ("inputhost", "system input"), ("SHCORE", "shell"),
                   ("Nahimic", "audio vendor plugin")):
        if key in st:
            return r
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--inventory")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--all-threads", action="store_true")
    a = ap.parse_args()

    meta = load(a.dir, "meta") or {}
    print(f"# Capture `{os.path.basename(os.path.normpath(a.dir))}`\n")
    print(f"pid {meta.get('pid')}, spawned={meta.get('spawned')}, {meta.get('start')} .. {meta.get('end')}, "
          f"{meta.get('seconds')} s, frida {meta.get('frida')}, scripts {', '.join(meta.get('scripts', []))}\n")

    census = load(a.dir, "iat_census")
    api = collections.defaultdict(collections.Counter)
    if census and "rows" in census:
        for r in census["rows"]:
            api[r["tid"]][(r["dll"], r["name"], r["cw"])] += r["count"]

    mods = load(a.dir, "modules")
    if mods:
        print("## Modules (game + graphics path)\n")
        print("| Module | Base | Preferred | Relocated | SHA-256 (first 16) | Path |")
        print("|---|---|---|---|---|---|")
        for m in mods:
            n = m["name"].lower()
            if n in GAME or n in {"d3d9.dll", "dxgi.dll", "msvcrt.dll", "msvcp60.dll"} or n.startswith("igd9") or n.startswith("nvd"):
                sha = (m.get("sha256") or "")[:16]
                print(f"| {m['name']} | {m['base']} | {m.get('preferred')} | {'yes' if m.get('relocated') else 'no'} | `{sha}` | {m['path']} |")
        print(f"\n{len(mods)} modules loaded in total.\n")

    th = load(a.dir, "threads")
    if th and "threads" in th:
        print(f"## Threads and x87 control word (sampled every {th['interval_ms']} ms, {th['samples']} rounds)\n")
        print("| TID | Start address | Role | FCW (samples) | MXCSR | alive |")
        print("|---|---|---|---|---|---|")
        for t in th["threads"]:
            r = role(t, api)
            if not a.all_threads and not r and t["tid"] not in api:
                continue
            fcw = ", ".join(f"{k}×{v}" for k, v in sorted(t["fcw"].items(), key=lambda kv: -kv[1]))
            mx = ", ".join(sorted(t["mxcsr"]))
            print(f"| {t['tid']} | {t.get('start') or ''} | {r} | {fcw} | {mx} | {t['alive']} |")
        seen = sorted({k for t in th["threads"] for k in t["fcw"]})
        print("\nControl words seen: " + "; ".join(f"`{c}` = {cw_meaning(c)}" for c in seen) + "\n")

    fpu = load(a.dir, "fpu_hooks")
    if fpu:
        print("## FPU control calls\n")
        rows = collections.Counter()
        first = {}
        for c in fpu.get("combos", []):
            caller = (c.get("caller") or "")
            if caller.startswith("0x"):
                caller = "(via engine UMGet*)"
            k = (c["kind"], c["tid"], c["args"], c["ret"], c["cw_before"], c["cw_after"], caller)
            rows[k] += c["count"]
            first.setdefault(k, c.get("firstMs"))
        print("| Function | TID | args | ret | CW before → after | caller | calls | first ms |")
        print("|---|---|---|---|---|---|---|---|")
        for k, n in sorted(rows.items(), key=lambda kv: (first[kv[0]] or 0)):
            print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {k[4]} → {k[5]} | {k[6]} | {n} | {first[k]} |")
        other = [e for e in fpu.get("events", []) if e["kind"] not in
                 {"_controlfp", "_control87", "__control87_2", "_fpreset", "_clearfp", "UMSetFloatingPointPrecision",
                  "UMGetFloatingPointPrecision", "UMSetFloatingPointRoundingMode", "UMGetFloatingPointRoundingMode"}]
        if other:
            print("\n### Other events\n")
            for e in other:
                e = {k: v for k, v in e.items() if k != "bt"}
                print("- " + json.dumps(e))
        print()

    if census and "rows" in census:
        secs = census["elapsedMs"] / 1000
        print(f"## API census ({secs:.0f} s, calls from game modules via IAT, dropped={census['dropped']})\n")
        total = collections.Counter()
        by_mod = collections.defaultdict(collections.Counter)
        threads = collections.defaultdict(set)
        for r in census["rows"]:
            k = (r["dll"], r["name"])
            total[k] += r["count"]
            by_mod[k][r["module"]] += r["count"]
            threads[k].add(f"{r['tid']}@{r['cw']}")
        print("| # | DLL | Function | calls | calls/s | callers | threads@CW |")
        print("|---|---|---|---|---|---|---|")
        for i, (k, n) in enumerate(total.most_common(a.top), 1):
            callers = ", ".join(f"{m} {c}" for m, c in by_mod[k].most_common(3))
            print(f"| {i} | {k[0]} | `{k[1]}` | {n} | {n / secs:.0f} | {callers} | {', '.join(sorted(threads[k]))} |")
        imported = {(m[1], m[2]) for m in census["imports"]}
        called = set(total)
        print(f"\n{len(called)} distinct functions called out of {len(imported)} patched import names "
              f"({len(census['imports'])} IAT slots).\n")
        if a.inventory and os.path.exists(a.inventory):
            inv = json.load(open(a.inventory, encoding="utf-8"))
            inv_names = {(dll.lower(), fn) for dll, fns in inv["external"].items() for fn in fns}
            hit = {(d.lower(), n) for (d, n) in called}
            print(f"Inventory: {len(inv_names & hit)} of {len(inv_names)} inventoried externals were called; "
                  f"{len(inv_names - {(d.lower(), n) for (d, n) in imported})} inventoried names were not seen as "
                  f"patched imports (modules not loaded, or naming differences).\n")


if __name__ == "__main__":
    main()
