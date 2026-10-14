"""Frida driver for the Empire Earth reference captures on legion.

Attach to the running game (works from an SSH session) or spawn it (must run in the
interactive session: use `launch.ps1 frida '<args>'`). Loads one or more scripts from this
folder, waits, then calls each script's rpc snapshot() and stop(), writes JSON and detaches.
The game keeps running.

  run.py --scripts modules,threads,iat_census --seconds 60 --out C:\\EEPort\\captures\\menu
  run.py --spawn --scripts fpu_hooks,threads,modules --seconds 90 --out C:\\EEPort\\captures\\spawn
  run.py ... --param interval_ms=20 --param internal=true

Outputs in --out: <script>.json (snapshot), events.jsonl (send() messages), meta.json.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_EXE = r"C:\EEPort\game\Empire Earth\Empire Earth.exe"


def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def parse_params(items):
    params = {}
    for item in items:
        k, _, v = item.partition("=")
        try:
            params[k] = json.loads(v)
        except ValueError:
            params[k] = v
    return params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spawn", action="store_true")
    ap.add_argument("--exe", default=GAME_EXE)
    ap.add_argument("--name", default="Empire Earth.exe")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--scripts", required=True, help="comma-separated names in this folder (no .js)")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--log", help="write stdout/stderr here (used by launch.ps1 frida)")
    args = ap.parse_args()
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "w", encoding="utf-8", buffering=1)

    os.makedirs(args.out, exist_ok=True)
    params = parse_params(args.param)
    events = open(os.path.join(args.out, "events.jsonl"), "w", encoding="utf-8")
    t0 = time.time()

    def on_message(name):
        def handler(msg, data):
            rec = {"t": round(time.time() - t0, 3), "script": name}
            if msg["type"] == "send":
                rec["payload"] = msg["payload"]
            else:
                rec["error"] = msg
                print(f"[{name}] {msg.get('description', msg)}", file=sys.stderr)
            events.write(json.dumps(rec) + "\n")
            events.flush()
        return handler

    device = frida.get_local_device()
    if args.spawn:
        # stdio="pipe": the game must not inherit our stdout, or a caller waiting on that pipe
        # (launch.ps1's job wrapper) blocks until the game exits.
        pid = device.spawn([args.exe], cwd=os.path.dirname(args.exe), stdio="pipe")
    else:
        pid = args.pid or device.get_process(args.name).pid
    session = device.attach(pid)
    scripts = {}
    for name in args.scripts.split(","):
        src = open(os.path.join(HERE, name + ".js"), encoding="utf-8").read()
        prelude = "globalThis.PARAMS = %s;\n" % json.dumps(params)
        s = session.create_script(prelude + src, name=name)
        s.on("message", on_message(name))
        s.load()
        scripts[name] = s
    if args.spawn:
        device.resume(pid)
    print(f"pid {pid}: loaded {', '.join(scripts)}; waiting {args.seconds}s", flush=True)

    meta = {"pid": pid, "spawned": args.spawn, "frida": frida.__version__, "scripts": list(scripts),
            "params": params, "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": args.seconds}
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass

    for name, s in scripts.items():
        try:
            snap = s.exports_sync.snapshot()
        except Exception as e:  # script without snapshot() or the game died
            snap = {"error": str(e)}
        if name == "modules" and isinstance(snap, list):
            for m in snap:
                m["sha256"] = sha256(m["path"])
        with open(os.path.join(args.out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=1)
    for name, s in scripts.items():
        try:
            s.exports_sync.stop()
        except Exception:
            pass
    time.sleep(1.0)  # let threads leave any IAT stub before the scripts unload
    for s in scripts.values():
        try:
            s.unload()
        except Exception:
            pass
    session.detach()
    meta["end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    events.close()
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
