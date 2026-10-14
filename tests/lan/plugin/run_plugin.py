#!/usr/bin/env python3
"""Run the TCP Protocol.dll harness and decode its datagram with eelan.py.

    .venv/bin/python tests/lan/plugin/run_plugin.py [--harness PATH]

The harness (tcp_plugin_harness.cpp; build it with
`tools/build.py --cmake-target ee_tcp_plugin_harness`) drives the translated
plugin's NPIM entry points over the kit's Winsock on the loopback interface
and writes the one connection request it caught on the wire. This decodes
that datagram with tools/lan/eelan.py - the RC4 and message decoder that reads
the session-2 LAN captures of the Windows game - and checks that it is the
message the plugin was given: the wire format is intact on the Mac.
"""

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def load_eelan():
    spec = importlib.util.spec_from_file_location("eelan", ROOT / "tools/lan/eelan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--harness", type=Path, default=ROOT / "build/recomp/ee_tcp_plugin_harness")
    args = ap.parse_args()
    if not args.harness.is_file():
        sys.exit("No harness at %s: tools/build.py --cmake-target ee_tcp_plugin_harness" % args.harness)
    with tempfile.TemporaryDirectory() as tmp:
        wire_file = Path(tmp) / "wire.txt"
        run = subprocess.run([str(args.harness), "--wire", str(wire_file)], cwd=ROOT)
        lines = wire_file.read_text().split() if wire_file.is_file() else []
    if len(lines) != 2:
        sys.exit("the harness caught no datagram (exit %d)" % run.returncode)
    wire, plain = bytes.fromhex(lines[0]), bytes.fromhex(lines[1])
    eelan = load_eelan()
    decrypted = eelan.decrypt_datagram(wire)
    message = eelan.decode_datagram(wire)
    print("wire:      %s" % wire.hex())
    print("decrypted: %s" % decrypted.hex())
    print("eelan:     %s" % {k: v for k, v in message.items()})
    failures = 0

    def check(ok, what):
        nonlocal failures
        print("  %s %s" % ("ok  " if ok else "FAIL", what))
        failures += 0 if ok else 1

    check(1 <= wire[0] <= 254, "byte 0 is a key index (%d)" % wire[0])
    check(wire[1:] != plain[1:], "the rest is obfuscated on the wire")
    check(decrypted[1:] == plain[1:], "eelan's RC4 recovers exactly what the plugin was given")
    check(message.get("type") == 0x22 and "error" not in message and "size_mismatch" not in message,
          "a %s, decoded without error or size mismatch" % message.get("name"))
    check(message.get("version") == "v2.00.2974" and message.get("player_name") == "MacHarness"
          and message.get("data_checksum") == 0x0E998B9E,
          "version, player name and data checksum as sent")
    print("harness exit %d; decode %s" % (run.returncode, "ok" if not failures else "FAILED"))
    return 1 if failures or run.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
