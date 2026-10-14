"""Frida driver for the Empire Earth reference captures on legion.

Attach to the running game (works from an SSH session) or spawn it (must run in the
interactive session: use `launch.ps1 frida '<args>'`). Loads one or more scripts from this
folder, waits, then calls each script's rpc snapshot() and stop(), writes JSON and detaches.
The game keeps running.

  run.py --scripts modules,threads,iat_census --seconds 60 --out C:\\EEPort\\captures\\menu
  run.py --spawn --scripts fpu_hooks,threads,modules --seconds 90 --out C:\\EEPort\\captures\\spawn
  run.py ... --param interval_ms=20 --param internal=true

Outputs in --out: <script>.json (snapshot), events.jsonl (send() messages), meta.json.
A script may also write files under --out: send({"type": "file", "path": "rel/path", "json": obj})
writes JSON, send({"type": "file", "path": "rel/path"}, bytes) writes raw bytes (rmg_capture.js).
Existing blobs/ files of the same size are kept (content-addressed, written once).

Every blocking Frida call (load, snapshot, stop, unload, detach) runs under a watchdog. A call
that hangs (Stalker teardown can) is logged in meta.json "timeouts" and skipped, the capture
still finishes, and the process exits hard at the end. Frida's own D-Bus calls (load, unload,
detach) time out after ~25 s regardless, so scripts must not do heavy work while loading.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
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


def bounded(what, f, timeout, timeouts):
    """Run a blocking Frida call in a daemon thread; give up after `timeout` seconds."""
    box = {}

    def work():
        try:
            box["value"] = f()
        except Exception as e:  # noqa: BLE001
            box["error"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f"{what}: no answer after {timeout:g}s, going on", flush=True)
        timeouts.append(what)
        raise TimeoutError(what)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def parse_params(items):
    params = {}
    for item in items:
        k, _, v = item.partition("=")
        try:
            params[k] = json.loads(v)
        except ValueError:
            params[k] = v
    return params


def write_file(out_dir, payload, data, counts):
    """{"type": "file"} message from a script: write payload["json"] or the binary data under out_dir."""
    rel = str(payload.get("path", ""))
    parts = rel.replace("\\", "/").split("/")
    if not rel or os.path.isabs(rel) or ".." in parts or ":" in rel:
        raise ValueError("bad file path from script: %r" % rel)
    dest = os.path.join(out_dir, *parts)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if "json" in payload:
        body = json.dumps(payload["json"], indent=1).encode("utf-8")
    elif data is not None:
        body = data
    else:
        raise ValueError("file message without json or data: %r" % rel)
    if rel.startswith("blobs/") and os.path.exists(dest) and os.path.getsize(dest) == len(body):
        return
    with open(dest + ".tmp", "wb") as f:
        f.write(body)
    os.replace(dest + ".tmp", dest)
    counts["files"] += 1
    counts["bytes"] += len(body)


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
    ap.add_argument("--every", type=float, default=0,
                    help="also write snapshots every N seconds, so a game exit keeps the last one")
    ap.add_argument("--stop-file", help="stop early (and snapshot) once this file exists")
    ap.add_argument("--load-timeout", type=float, default=120, help="seconds per script load")
    ap.add_argument("--rpc-timeout", type=float, default=120, help="seconds per snapshot()/stop() call")
    ap.add_argument("--teardown-timeout", type=float, default=30, help="seconds per unload/detach")
    args = ap.parse_args()
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "w", encoding="utf-8", buffering=1)

    os.makedirs(args.out, exist_ok=True)
    params = parse_params(args.param)
    events = open(os.path.join(args.out, "events.jsonl"), "w", encoding="utf-8")
    t0 = time.time()
    file_counts = {"files": 0, "bytes": 0, "errors": 0}

    def on_message(name):
        def handler(msg, data):
            payload = msg.get("payload") if msg["type"] == "send" else None
            if isinstance(payload, dict) and payload.get("type") == "file":
                try:
                    write_file(args.out, payload, data, file_counts)
                except Exception as e:  # keep capturing; record the failure
                    file_counts["errors"] += 1
                    print(f"[{name}] file write failed: {e}", file=sys.stderr)
                return
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
    detached = []
    session.on("detached", lambda reason, crash: detached.append(reason))
    timeouts = []
    scripts = {}
    for name in args.scripts.split(","):
        src = open(os.path.join(HERE, name + ".js"), encoding="utf-8").read()
        prelude = "globalThis.PARAMS = %s;\n" % json.dumps(params)
        s = session.create_script(prelude + src, name=name)
        s.on("message", on_message(name))
        try:
            bounded(f"load {name}", s.load, args.load_timeout, timeouts)
        except Exception as e:  # noqa: BLE001
            print(f"{name}: load failed: {e}", flush=True)
            os._exit(2)
        scripts[name] = s
    if args.spawn:
        device.resume(pid)
    print(f"pid {pid}: loaded {', '.join(scripts)}; waiting {args.seconds}s", flush=True)

    meta = {"pid": pid, "spawned": args.spawn, "frida": frida.__version__, "scripts": list(scripts),
            "params": params, "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": args.seconds,
            "timeouts": timeouts}
    def save_snapshots():
        for name, s in scripts.items():
            path = os.path.join(args.out, name + ".json")
            try:
                snap = bounded(f"snapshot {name}", s.exports_sync.snapshot, args.rpc_timeout, timeouts)
            except Exception as e:  # script without snapshot(), the game died, or no answer
                if os.path.exists(path):
                    continue  # keep the last periodic snapshot
                snap = {"error": str(e)}
            if name == "modules" and isinstance(snap, list):
                for m in snap:
                    m["sha256"] = sha256(m["path"])
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(snap, f, indent=1)
            os.replace(path + ".tmp", path)

    deadline = time.time() + args.seconds
    next_save = time.time() + args.every if args.every > 0 else None
    try:
        while time.time() < deadline and not detached:
            if args.stop_file and os.path.exists(args.stop_file):
                print("stop file seen", flush=True)
                break
            if next_save and time.time() >= next_save:
                save_snapshots()
                next_save += args.every
            time.sleep(max(0.0, min(1.0, deadline - time.time())))
    except KeyboardInterrupt:
        pass
    if detached:
        print(f"detached: {detached[0]}", flush=True)
        meta["detached"] = str(detached[0])

    save_snapshots()
    teardown = meta["teardown"] = {}

    def step(what, f, timeout):  # seconds taken, or the error, go to meta.json
        t = time.time()
        try:
            r = bounded(what, f, timeout, timeouts)
            teardown[what] = round(time.time() - t, 2)
            return r
        except Exception as e:  # noqa: BLE001  (also: already gone with the game)
            teardown[what] = "%s after %.1fs" % (type(e).__name__, time.time() - t)
            return None

    # A script whose stop() returns {"eternalize": true} stays in the game instead of being
    # unloaded: unloading Stalker waits until every followed thread has left it, and a thread
    # blocked in a long wait never does.
    keep = set()
    for name, s in scripts.items():
        r = step(f"stop {name}", s.exports_sync.stop, args.rpc_timeout)
        if isinstance(r, dict) and r.get("eternalize"):
            keep.add(name)
    time.sleep(1.0)  # let threads leave any IAT stub before the scripts unload
    for name, s in scripts.items():
        if name in keep:
            step(f"eternalize {name}", s.eternalize, args.teardown_timeout)
        else:
            step(f"unload {name}", s.unload, args.teardown_timeout)
    step("detach", session.detach, args.teardown_timeout)
    meta["end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta["files_written"] = file_counts
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    events.close()
    print(f"wrote {args.out}" + (f" (timeouts: {', '.join(timeouts)})" if timeouts else ""), flush=True)
    if timeouts:
        os._exit(0)  # a hung Frida call would otherwise keep the interpreter from exiting


if __name__ == "__main__":
    main()
