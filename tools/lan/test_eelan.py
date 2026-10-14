"""Synthetic-capture tests for eelan.py: python -I tools/lan/test_eelan.py

Builds small pcapng (Ethernet, pktmon-style duplicate sightings with comments) and pcap
(BSD NULL, as macOS lo0/utun) files from hand-made EE messages, then checks that the
decoder finds them. No game bytes are involved.
"""
import csv
import json
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eelan  # noqa: E402


def uws(s):
    b = s.encode("cp1252")
    return struct.pack("<I", len(b) + 2) + b + b"\0\0"


def ipv4_udp(src, dst, sp, dp, payload, ident=1):
    udp = struct.pack(">HHHH", sp, dp, 8 + len(payload), 0) + payload
    hdr = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), ident, 0, 128, 17, 0,
                      bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    return hdr + udp


def ipv4_tcp(src, dst, sp, dp, seq, flags, payload, ident=1):
    tcp = struct.pack(">HHIIHHHH", sp, dp, seq, 0, (5 << 12) | flags, 65535, 0, 0) + payload
    hdr = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), ident, 0, 128, 6, 0,
                      bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    return hdr + tcp


def ether(ip, dst=b"\xff" * 6):
    return dst + b"\x02\x00\x00\x00\x00\x01" + b"\x08\x00" + ip


def pcapng(frames):
    """frames: list of (ts, bytes, comment or None)."""
    out = bytearray()

    def block(btype, body):
        body += b"\0" * ((-len(body)) % 4)
        ln = 12 + len(body)
        out.extend(struct.pack("<II", btype, ln) + body + struct.pack("<I", ln))

    block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    block(0x00000001, struct.pack("<HHI", 1, 0, 0))
    for ts, data, comment in frames:
        us = int(ts * 1e6)
        body = struct.pack("<IIIII", 0, us >> 32, us & 0xFFFFFFFF, len(data), len(data))
        body += data + b"\0" * ((-len(data)) % 4)
        if comment:
            c = comment.encode()
            body += struct.pack("<HH", 1, len(c)) + c + b"\0" * ((-len(c)) % 4)
            body += struct.pack("<HH", 0, 0)
        block(0x00000006, body)
    return bytes(out)


def pcap_null(frames):
    out = bytearray(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 0))
    for ts, ip in frames:
        d = struct.pack("<I", 2) + ip
        out += struct.pack("<IIII", int(ts), int((ts % 1) * 1e6), len(d), len(d)) + d
    return bytes(out)


def build():
    host, cli = "10.147.17.10", "10.147.17.20"
    enc = eelan.encrypt_datagram
    search = bytearray(67)
    search[1] = 0x15
    search[2:11] = b"EEService"
    advert = bytearray(136)
    advert[1] = 0x16
    advert[2:11] = b"EEService"
    advert[67:75] = b"Max game"
    req = bytes([0, 0x22, 0xFF, 0xFF, 0]) + uws("2.00 1234") + uws("Max") + struct.pack("<I", 777)
    resp = bytes([0, 0x23, 0xFF, 0xFF]) + struct.pack("<IBBI", 0, 0, 1, 0) + uws("2.00 1234")
    sel = bytes([0, 0x48, 0xFF, 1, 1]) + struct.pack("<I", 99)
    frames = []
    t = 1000.0
    for i in range(3):  # client searches every ~1.05 s
        pkt = ipv4_udp(cli, "255.255.255.255", 50000, 33334, enc(bytes(search), 1 + i), 10 + i)
        frames.append((t + i * 1.05, ether(pkt), None))
        adv = ipv4_udp(host, cli, 50001 + i, 33334, enc(bytes(advert), 100 + i), 20 + i)
        frames.append((t + i * 1.05 + 0.001, ether(adv), None))
    t += 4
    frames.append((t, ether(ipv4_udp(cli, host, 50010, 33334, enc(req, 5), 30)), None))
    frames.append((t + 0.01, ether(ipv4_udp(host, cli, 33334, 50010, enc(resp, 6), 31)), None))
    for n in range(40, 44):
        gc = 1 if n >= 42 else 0
        ct = bytes([0, 0x26, 0xFF, 0xFE]) + struct.pack("<I", n) + bytes([3, 15, gc, 0])
        ct += bytes([1]) + sel if n == 41 else bytes([0])
        pkt = ether(ipv4_udp(host, cli, 33334, 50010, enc(ct, 7 + n), 100 + n))
        ts = t + 1 + (n - 40) * 0.2
        # pktmon logs the same packet at two components: same group/number, other appearance
        frames.append((ts, pkt, "PktGroupId %d, PktNumber 1, Appearance 1, Direction Tx" % n))
        frames.append((ts + 1e-5, pkt, "PktGroupId %d, PktNumber 1, Appearance 2, Direction Tx" % n))
        ctr = bytes([0, 0x27, 0xFF, 1]) + struct.pack("<IIH", n - 1, n, 24) + bytes([0])
        frames.append((ts + 0.05, ether(ipv4_udp(cli, host, 50010, 33334, enc(ctr, 9), 200 + n)), None))
        if gc:
            for slot, src, sp, dp, val in ((0xFE, host, 33334, 50010, 0x1111),
                                           (1, cli, 50010, 33334, 0x1111 if n == 42 else 0x2222)):
                w = bytes([0, 0x32, 0xFF, slot]) + struct.pack("<II", n, val)
                dst = cli if src == host else host
                frames.append((ts + 0.1, ether(ipv4_udp(src, dst, sp, dp, enc(w, 11), 300 + n)), None))
    xfer = bytes([0, 0x3B, 0xFF, 0xFF, 0]) + struct.pack("<I", 20) + struct.pack("<II", 1, 2) \
        + b"scenario.scn\0" + bytes(1024 - 8 - 13)
    frames.append((t + 3, ether(ipv4_tcp(host, cli, 33335, 50100, 1000, 0x02, b"")), None))
    frames.append((t + 3.1, ether(ipv4_tcp(host, cli, 33335, 50100, 1001, 0x18, xfer[:600])), None))
    frames.append((t + 3.2, ether(ipv4_tcp(host, cli, 33335, 50100, 1601, 0x18, xfer[600:])), None))
    def u16s(txt):
        return struct.pack("<H", len(txt)) + txt.encode("utf-16-le")
    advert = b"\x01" + struct.pack("<HH", 33336, 0x8B49) + b"\x00" + u16s("Max game") \
        + struct.pack("<HHHH", 0, 2, 8, 14) + b"\x00"
    frames.append((t + 4, ether(ipv4_udp(host, "255.255.255.255", 8585, 8585, advert, 50)), None))
    frames.append((t + 4, ether(ipv4_udp(cli, "255.255.255.255", 8585, 8585, b"\x04", 52)), None))

    def rec(body):
        return struct.pack("<H", len(body) + 2) + body
    room = rec(b"\x02" + struct.pack("<HHHH", 0, 1, 2, 2) + struct.pack("<H", 1) + u16s("Host")
               + struct.pack("<H", 2) + u16s("Max"))
    room += rec(b"\x08\x05" + struct.pack("<HB", 2, 1))
    room += rec(b"\x06" + struct.pack("<HH", 0, 1) + u16s("hi") + b"\0\0")
    start = rec(b"\x08\x07")
    frames.append((t + 4.2, ether(ipv4_tcp(host, cli, 33336, 50200, 5000, 0x18, room[:7])), None))
    frames.append((t + 4.3, ether(ipv4_tcp(host, cli, 33336, 50200, 5007, 0x18, room[7:] + start)), None))
    clear = bytes([0, 0x3A, 0xFF, 0xFE])  # key index 0 = clear text (NEMServerIsAlive)
    frames.append((t + 4.1, ether(ipv4_udp(host, cli, 33334, 50010, clear, 51)), None))
    nul = [(t, ipv4_udp(cli, host, 50010, 33334, enc(req, 77), 40))]
    return pcapng(frames), pcap_null(nul)


def main():
    ng, pc = build()
    with tempfile.TemporaryDirectory() as td:
        p1, p2 = os.path.join(td, "lan.pcapng"), os.path.join(td, "lo0.pcap")
        open(p1, "wb").write(ng)
        open(p2, "wb").write(pc)
        evs = [e for e in eelan.iter_events([p1]) if e["proto"] != "stats"]
        names = [e["msg"]["name"] for e in evs]
        assert names.count("DiscoverySearch") == 3 and names.count("DiscoveryAdvert") == 3, names
        assert names.count("NEMCommunicationsTick") == 4, names  # pktmon duplicates dropped
        assert names.count("NEMWorldDatabaseChecksumResponse") == 4, names
        xf = [e for e in evs if e["msg"].get("type") == 0x3B]
        assert xf and xf[0]["msg"]["file_name"] == "scenario.scn", xf
        ct41 = [e for e in evs if e["msg"].get("ct") == 41 and e["msg"]["type"] == 0x26][0]
        assert ct41["msg"]["inputs"][0]["name"] == "EEMSelectUnit", ct41
        adv = [e["msg"] for e in evs if e["msg"]["name"] == "LanGameAdvert"]
        assert adv and adv[0]["game"] == "Max game" and adv[0]["players"] == 2, adv
        assert "LanQuery" in names, names
        roster = [e["msg"] for e in evs if e["msg"]["name"] == "LobbyRoster"]
        assert [m_["name"] for m_ in roster[0]["members"]] == ["Host", "Max"], roster
        rdy = [e["msg"] for e in evs if e["msg"]["name"] == "LobbyGameData.ReadyState"]
        assert rdy and rdy[0]["client_id"] == 2 and rdy[0]["ready"] == 1, rdy
        chat = [e["msg"] for e in evs if e["msg"]["name"] == "LobbyChatRelay"]
        assert chat and chat[0]["text"] == "hi" and chat[0]["sender_id"] == 1, chat
        assert "LobbyGameData.StartGame" in names, names
        assert "NEMServerIsAlive" in names, names
        evs2 = [e for e in eelan.iter_events([p2]) if e["proto"] != "stats"]
        assert evs2[0]["msg"]["player_name"] == "Max", evs2
        out = os.path.join(td, "out")
        eelan.main(["export", p1, "--out-dir", out])
        rows = list(csv.DictReader(open(os.path.join(out, "checksums.csv"))))
        assert len(rows) == 4, rows
        verdict = json.load(open(os.path.join(out, "checksums.json")))
        assert [v["agree"] for v in verdict] == [True, False], verdict
        eelan.main(["summary", p1])
    eelan.main(["selftest"])
    print("test_eelan ok")


if __name__ == "__main__":
    main()
