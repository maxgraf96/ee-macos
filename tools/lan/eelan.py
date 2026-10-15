"""Decode Empire Earth (base game, GOG 2.00) LAN traffic from packet captures.

Usage (run with `python -I`; stdlib only):

    python -I tools/lan/eelan.py summary  CAPTURE...
    python -I tools/lan/eelan.py timeline CAPTURE... [--nested] [--no-ticks] [--limit N]
    python -I tools/lan/eelan.py export   CAPTURE... --out-dir DIR
    python -I tools/lan/eelan.py keytable          # print the derived RC4 key table digest
    python -I tools/lan/eelan.py selftest          # encode/decode round trip, no capture needed

CAPTURE is a .pcap or .pcapng file: Windows `pktmon etl2pcap` output (Ethernet, with
per-packet comments and duplicate sightings, which are de-duplicated), macOS tcpdump on
en*/feth*/utun*/lo0 (Ethernet, NULL/LOOP and raw-IP link types), or Wireshark.

What is decoded (see docs/reference/netcode.md for the protocol description):
  * UDP 8585: the EXE's WON LAN lobby game list (LanQuery / LanGameAdvert / KeepAlive /
    Closed), and TCP 33336: the lobby room on the host (join, roster, chat, version check,
    slot/ready state, StartGame). Record framing is decoded; some payloads stay hex.
  * UDP 33334: all game traffic, plus the plugin's legacy discovery (search 0x15 /
    advertisement 0x16), which the normal lobby flow does not use. Every
    datagram is one NE message. Byte 0 is a per-packet key index k; bytes 1.. are RC4
    encrypted with the 7-byte key T[k-1:k+6] from a fixed 261-byte table T that
    TCP Protocol.dll derives from an LCG at start-up (re-derived here; no game bytes).
  * TCP 33335: file transfer (NEMXferMessage records, 1033 bytes each, not encrypted).
  * NE message types 0x20-0x3c (engine) and EEM game messages 0x41-0xaa (EXE), including
    the player-input lists nested in communications ticks (0x26) and their responses (0x27).

Exports (export subcommand):
  messages.jsonl     every decoded datagram/record, one JSON object per line
  ticks.csv          communications ticks (0x26) as sent by the host, one row per CT/receiver
  checksums.csv      world-checksum responses (0x32): the OOS oracle stream
  checksums.json     the same, grouped per tick with an agree/disagree verdict
  discovery.csv      search/advertisement packets
  session.csv        connection, start/stop, pause, remove-player (OOS verdict) events

Checksum responses are collected both as top-level datagrams and nested in other messages
(guaranteed inputs ride inside CT responses). The host logs its own checksum locally, so a
capture shows the clients' values only; hook EXE+0x145ebf for the host's (netcode.md).
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import struct
import sys

UDP_GAME_PORT = 33334   # 0x8236: TCP Protocol.dll game traffic (+ legacy EEService discovery)
TCP_XFER_PORT = 33335   # 0x8237: plugin file transfer (TCP); also WONLobby.cfg EEFileTransferPort
LOBBY_PORT = 33336      # WONLobby.cfg LobbyPort (EXE WONAPI LAN lobby, UDP; EXE default 8888)
LAN_BCAST_PORT = 8585   # WONLobby.cfg LanBroadcastPort (EXE WONAPI LAN lobby)
LOBBY_PORTS = {LOBBY_PORT, LAN_BCAST_PORT}
EE_PORTS = {UDP_GAME_PORT, TCP_XFER_PORT} | LOBBY_PORTS

# ----------------------------------------------------------------------------------------
# Obfuscation (TCP Protocol.dll RVA 0x1e73 table, 0x25bb RC4 KSA, 0x24ba RC4 PRGA)
# ----------------------------------------------------------------------------------------

def derive_key_table():
    """261-byte table: URandom LCG (a=0x343fd, c=0x269ec3, m=2^31), seed 0xffffffff,
    byte = trunc(state / 2^31 * 255.0). Exact at x87 PC=53 (the protocol thread's mode)."""
    state = 0xFFFFFFFF
    out = bytearray()
    for _ in range(0x105):
        state = ((0x343FD * state + 0x269EC3) & 0xFFFFFFFF) % 0x80000000
        out.append(int(state / 2147483648.0 * 255.0) & 0xFF)
    return bytes(out)


KEY_TABLE = derive_key_table()


def _rc4(key, data):
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (s[i] + j + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray(len(data))
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (s[i] + j) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def packet_key(k):
    if not 1 <= k <= 0xFF:
        return None
    return KEY_TABLE[k - 1:k + 6]


def decrypt_datagram(payload):
    """Return payload with bytes 1.. decrypted (byte 0, the key index, is kept).
    Key index 0 means clear text (TCP Protocol.dll+0x24ba skips RC4); senders use 1..254."""
    if len(payload) < 2:
        return None
    if payload[0] == 0:
        return bytes(payload)
    key = packet_key(payload[0])
    if key is None:
        return None
    return payload[:1] + _rc4(key, payload[1:])


def encrypt_datagram(plain, k):
    """Inverse of decrypt_datagram (for tests and synthetic captures)."""
    return bytes([k]) + _rc4(packet_key(k), plain[1:])

# ----------------------------------------------------------------------------------------
# Message catalogue
# ----------------------------------------------------------------------------------------

DISCOVERY_SEARCH = 0x15      # 67-byte datagram:  [k][0x15][char service[65]]
DISCOVERY_ADVERT = 0x16      # 136-byte datagram: [k][0x16][char service[65]][char game[69]]

NE_NAMES = {
    0x20: "NEMAddPlayer", 0x21: "NEMAddPlayerResponse",
    0x22: "NEMClientConnectionRequest", 0x23: "NEMClientConnectionRequestResponse",
    0x24: "NEMClientDisconnectionNotification", 0x25: "NEMClientDisconnectionNotificationResponse",
    0x26: "NEMCommunicationsTick", 0x27: "NEMCommunicationsTickResponse",
    0x28: "NEMPhysicsTick", 0x29: "NEMPlayerConnectionCheck",
    0x2a: "NEMPlayerConnectionCheckResponse", 0x2b: "NEMRemovePlayer",
    0x2d: "NEMStartGame", 0x2e: "NEMStartGameResponse", 0x2f: "NEMStopGame",
    0x30: "NEMStopGameResponse", 0x31: "NEMWorldDatabaseChecksum",
    0x32: "NEMWorldDatabaseChecksumResponse", 0x33: "NEMPauseGame", 0x34: "NEMPauseGameResponse",
    0x35: "NEMUnPauseGame", 0x36: "NEMUnPauseGameResponse",
    0x37: "NEMClientReconnectionRequest", 0x38: "NEMClientReconnectionRequestResponse",
    0x39: "NEMClientRequestPause", 0x3a: "NEMServerIsAlive", 0x3b: "NEMXferMessage",
    0x3c: "NEMXferStatus",
}

# Game messages registered by the EXE (EXE RVA 0xb18a8 registers 0x41..0xfd; factory at
# RVA 0xb1b31). Value: (class, export size) with None = variable size (see _eem_size).
EEM = {
    0x41: ("EEMEdictProposal", 7), 0x42: ("EEMEdictVote", 9), 0x43: ("EEMNewEdictEnacted", 12),
    0x44: ("EEMPayTribute", 26), 0x46: ("EEMEdictPreProposal", 7),
    0x47: ("EEMEdictPreProposalResponse", 9), 0x48: ("EEMSelectUnit", 9),
    0x49: ("EEMAddUnitToSelectedGroup", 9), 0x4a: ("EEMRemoveUnitFromSelectedGroup", 9),
    0x4b: ("EEMUnselectAllUnits", 5), 0x4c: ("EEMAttackUnit", 10), 0x4d: ("EEMPathTo", 21),
    0x4e: ("EEMSelectUnits", None), 0x4f: ("EEMAddUnitsToSelectedGroup", None),
    0x50: ("EEMAddAndRemoveUnitsFromSelectedGroup", None), 0x51: ("EEMGatherResource", 10),
    0x52: ("EEMResearchTechnology", 14), 0x53: ("EEMRemoveTechnology", 15),
    0x54: ("EEMSetWaterLevel", 6), 0x55: ("EEMMakeWorldVisible", 5), 0x56: ("EEMBuildUnit", 18),
    0x57: ("EEMRepairUnit", 10), 0x58: ("EEMHaltSelectedGroup", 5), 0x59: ("EEMBuildWall", 29),
    0x5a: ("EEMChat", None), 0x5b: ("EEMFullPlayerInformation", None), 0x5c: ("EEMKillUnit", 6),
    0x5d: ("EEMStartLoading", 10), 0x5e: ("EEMStartLoadingResponse", 8),
    0x5f: ("EEMGameSpeed", 9), 0x60: ("EEMGameOptions", None), 0x61: ("EEMDropoffResource", 9),
    0x62: ("EEMToggleDayNight", 5), 0x63: ("EEMSnapToDaylight", 5),
    0x64: ("EEMLoadOrSaveGame", None), 0x65: ("EEMToggleCollisionDetection", 13),
    0x66: ("EEMSetRallyGoal", 22), 0x67: ("EEMLoadTransport", 14),
    0x68: ("EEMUnloadTransport", 17), 0x6a: ("EEMPlayerSlotInfo", None),
    0x6b: ("EEMHackANewUnit", 18), 0x6c: ("EEMIncrementEpic", 5), 0x6d: ("EEMRecordFrames", 6),
    0x6e: ("EEMCameraLocationInfo", 17), 0x6f: ("EEMZoomInfo", 9), 0x70: ("EEMAttackPlayers", 10),
    0x71: ("EEMLoadUnitData", 10), 0x72: ("EEMAddPatrolPoint", 13), 0x73: ("EEMPatrol", 9),
    0x74: ("EEMSelectPatrolGroup", 9), 0x75: ("EEMCastCalamity", 25),
    0x76: ("EEMAlwaysAddUnitToSelectedGroup", 9), 0x77: ("EEMImproveUnit", 13),
    0x78: ("EEMChangeFormation", 10), 0x79: ("EEMExplore", 5), 0x7a: ("EEMUpdatePingStat", 10),
    0x7b: ("EEMLoadGarrison", 14), 0x7c: ("EEMMigrationSlotInfo", 7),
    0x7d: ("EEMInGameChat", None), 0x7e: ("EEMCivUpgradesRequested", None),
    0x7f: ("EEMCivUpgradesPurchased", None), 0x80: ("EEMSetAirportCommand", 22),
    0x81: ("EEMReplantFarms", 9), 0x82: ("EEMSavedGroupAddSelection", 6),
    0x83: ("EEMSavedGroupCreate", 6), 0x84: ("EEMSavedGroupSelect", 7),
    0x85: ("EEMCheatCode", 13), 0x86: ("EEMBuildGate", 25), 0x87: ("EEMClearAirportCommands", 5),
    0x88: ("EEMEnterShiftMode", 9), 0x89: ("EEMExitShiftMode", 9),
    0x8a: ("EEMStopActiveGroup", 5), 0x8b: ("EEMRemoveImprovement", 13),
    0x8c: ("EEMLockUnlock", 6), 0x8d: ("EEMChangeSelectedUnitCollisionSphere", 6),
    0x8e: ("EEMIncrementRallyTimer", 6), 0x8f: ("EEMSmartFarmers", 10),
    0x90: ("EEMReturnToBase", 10), 0x91: ("EEMGameRestarting", 5), 0x92: ("EEMHealUnit", 9),
    0x93: ("EEMSavedGroupClear", 6), 0x94: ("EEMLoadPercent", 13),
    0x95: ("EEMImmediateCargoUnload", 14), 0x96: ("EEMSelectTheseUnits", None),
    0x97: ("EEMLoadMainScreen", 6), 0x98: ("EEMLoadMainScreenResponse", 7),
    0x99: ("EEMSetDiplomaticStance", 13), 0x9a: ("EEMLoadOrSaveGameResponse", 9),
    0x9b: ("EEMAddRemoveResources", 18), 0x9c: ("EEMSendFlare", 13),
    0x9e: ("EEMSetUnitBehavior", 9), 0x9f: ("EEMAttackGround", 13),
    0xa0: ("EEMScrambleAircraft", 17), 0xa1: ("EEMSelectPlayerChatType", 9),
    0xa2: ("EEMSelectCustomPlayerForChat", 10), 0xa3: ("EEMResign", 5), 0xa4: ("EEMDisband", 5),
    0xa5: ("EEMSelfDestruct", 5), 0xa6: ("EEMReportStatistics", 13),
    0xa7: ("EEMCivTimerExpired", 6), 0xa8: ("EEMLagStatus", 8),
    0xa9: ("EEMFullGameOptions", None), 0xaa: ("EEMPlayerInformation", 21),
}

# Fixed export sizes of engine messages (header included); None = variable.
NE_SIZE = {
    0x20: None, 0x21: 4, 0x22: None, 0x23: None, 0x24: 5, 0x25: 8, 0x26: None, 0x27: None,
    0x28: 11, 0x29: 8, 0x2a: 8, 0x2b: 8, 0x2d: 8, 0x2e: 8, 0x2f: 4, 0x30: 4, 0x31: 9,
    0x32: 12, 0x33: 12, 0x34: 8, 0x35: 12, 0x36: 8, 0x37: 12, 0x38: 13, 0x39: 4, 0x3a: 4,
    0x3b: 1033, 0x3c: 11,
}

# EEM layouts with a UWideString: offset of the string, fixed bytes after it.
_EEM_UWS = {0x5a: (6, 0), 0x5b: (17, 19), 0x60: (13, 0), 0x64: (20, 0), 0x6a: (8, 0),
            0x7d: (5, 0), 0xa9: (53, 0)}


class DecodeError(Exception):
    pass


def type_name(t):
    if t in NE_NAMES:
        return NE_NAMES[t]
    if t in EEM:
        return EEM[t][0]
    return "type_0x%02x" % t


def _u8(b, o):
    if o >= len(b):
        raise DecodeError("short u8 at %d" % o)
    return b[o]


def _u16(b, o):
    if o + 2 > len(b):
        raise DecodeError("short u16 at %d" % o)
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    if o + 4 > len(b):
        raise DecodeError("short u32 at %d" % o)
    return struct.unpack_from("<I", b, o)[0]


def _uws(b, o):
    """UWideString export: u32 n (= strlen + 2), strlen bytes (MBCS, not UTF-16), 2 x NUL."""
    n = _u32(b, o)
    if n < 2 or o + 4 + n > len(b) or n > 4096:
        raise DecodeError("bad UWideString length %d at %d" % (n, o))
    raw = bytes(b[o + 4:o + 4 + n - 2])
    return raw.decode("cp1252", errors="replace"), o + 4 + n


def _uws_size(b, o):
    return 4 + _u32(b, o)


def _cstr(b):
    end = b.find(b"\0")
    return bytes(b if end < 0 else b[:end]).decode("cp1252", errors="replace")


def message_size(b, o):
    """Export size of the NE/EEM message starting at b[o] (header included)."""
    t = _u8(b, o + 1)
    if t in NE_SIZE:
        s = NE_SIZE[t]
        if s is not None:
            return s
        if t == 0x20:
            return 5 + _uws_size(b, o + 5)
        if t == 0x22:
            s1 = _uws_size(b, o + 5)
            return 5 + s1 + _uws_size(b, o + 5 + s1) + 4
        if t == 0x23:
            return 14 + _uws_size(b, o + 14)
        if t == 0x26:
            return 12 + _inputs_size(b, o + 12)
        if t == 0x27:
            return 14 + _inputs_size(b, o + 14)
    if t in EEM:
        s = EEM[t][1]
        if s is not None:
            return s
        if t in (0x4e, 0x4f, 0x50):
            return 6 + _u8(b, o + 5)
        if t in (0x7e, 0x7f):
            n = _u8(b, o + 10)
            return 11 + 2 * n + _uws_size(b, o + 11 + 2 * n)
        if t == 0x96:
            return 9 + 4 * _u32(b, o + 5)
        if t in _EEM_UWS:
            at, tail = _EEM_UWS[t]
            return at + _uws_size(b, o + at) + tail
    raise DecodeError("unknown message type 0x%02x" % t)


def _inputs_size(b, o):
    n = _u8(b, o)
    p = o + 1
    for _ in range(n):
        p += message_size(b, p)
    return p - o


def _decode_inputs(b, o, end):
    n = _u8(b, o)
    p = o + 1
    items = []
    for _ in range(n):
        if p >= end:
            raise DecodeError("input list overruns message")
        sz = message_size(b, p)
        items.append(decode_message(b[p:p + sz], nested=True))
        p += sz
    return items, p


def decode_message(b, nested=False):
    """Decode one NE/EEM message (plaintext, header at b[0:4]). Returns a dict."""
    b = bytes(b)
    t = _u8(b, 1)
    m = {"type": t, "name": type_name(t), "len": len(b),
         "for_slot": b[2] if len(b) > 2 else None, "orig_slot": b[3] if len(b) > 3 else None}
    try:
        if t == 0x20:
            m["player_index"] = _u8(b, 4)
            m["player_name"], _ = _uws(b, 5)
        elif t == 0x22:
            m["behind_firewall"] = _u8(b, 4)
            m["version"], o = _uws(b, 5)
            m["player_name"], o = _uws(b, o)
            m["data_checksum"] = _u32(b, o)
        elif t == 0x23:
            m["result"] = _u32(b, 4)
            m["unknown_u8"] = _u8(b, 8)
            m["send_to_slot"] = _u8(b, 9)
            m["current_ct"] = _u32(b, 10)
            m["text"], _ = _uws(b, 14)
        elif t == 0x24:
            m["is_exiting"] = _u8(b, 4)
        elif t == 0x25:
            m["value"] = _u32(b, 4)
        elif t == 0x26:
            m["ct"] = _u32(b, 4)
            m["pts_to_perform"] = _u8(b, 8)
            m["pts_per_second"] = _u8(b, 9)
            m["generate_checksum"] = _u8(b, 10)
            m["dump_the_world"] = _u8(b, 11)
            m["inputs"], _ = _decode_inputs(b, 12, len(b))
        elif t == 0x27:
            m["processed"] = _u32(b, 4)
            m["ct"] = _u32(b, 8)
            m["avg_pt_ms"] = _u16(b, 12)
            m["inputs"], _ = _decode_inputs(b, 14, len(b))
        elif t == 0x28:
            m["n_inputs"] = _u32(b, 4)
            m["pts_to_perform"] = _u8(b, 8)
            m["generate_checksum"] = _u8(b, 9)
            m["dump_the_world"] = _u8(b, 10)
        elif t in (0x29, 0x2a):
            m["stamp"] = _u32(b, 4)
        elif t == 0x2b:
            m["player_index"] = _u8(b, 4)
            m["is_oos"] = _u8(b, 5)
            m["game_is_exiting"] = _u8(b, 6)
            m["was_booted"] = _u8(b, 7)
        elif t in (0x2d, 0x2e):
            m["value"] = _u32(b, 4)
        elif t == 0x31:
            m["tick"] = _u32(b, 4)
            m["dump_world"] = _u8(b, 8)
        elif t == 0x32:
            m["tick"] = _u32(b, 4)
            m["checksum"] = _u32(b, 8)
        elif t in (0x33, 0x35):
            m["id"] = _u32(b, 4)
            m["aux"] = _u32(b, 8)
        elif t in (0x34, 0x36):
            m["id"] = _u32(b, 4)
        elif t == 0x37:
            m["a"] = _u32(b, 4)
            m["b"] = _u32(b, 8)
        elif t == 0x38:
            m["result"] = _u32(b, 4)
            m["new_send_to_slot"] = _u8(b, 8)
            m["value"] = _u32(b, 9)
        elif t == 0x3b:
            m["xfer_id"] = _u8(b, 4)
            m["data_size"] = _u32(b, 5)
            data = b[9:9 + 1024]
            if m["xfer_id"] == 0 and len(data) >= 8:
                m["file_a"] = _u32(data, 0)
                m["file_b"] = _u32(data, 4)
                m["file_name"] = _cstr(data[8:])
        elif t == 0x3c:
            m["client_slot"] = _u8(b, 4)
            m["percent"] = _u8(b, 5)
            m["file"] = _u32(b, 6)
            m["status"] = _u8(b, 10)
        elif t in EEM:
            if len(b) > 4:
                m["player"] = b[4]
            if t in (0x5a, 0x7d):
                at = _EEM_UWS[t][0]
                m["text"], _ = _uws(b, at)
            elif t in (0x5b, 0x6a, 0x60, 0x64, 0xa9):
                at = _EEM_UWS[t][0]
                m["text"], _ = _uws(b, at)
            elif t == 0x96:
                n = _u32(b, 5)
                m["units"] = [_u32(b, 9 + 4 * i) for i in range(n)]
            m["body_hex"] = b[4:].hex()
        else:
            m["body_hex"] = b[4:].hex()
    except DecodeError as e:
        m["error"] = str(e)
    if nested:
        m.pop("len", None)
    return m


def _printable_runs(b, minlen=4):
    out, cur = [], bytearray()
    for c in b:
        if 32 <= c < 127:
            cur.append(c)
        else:
            if len(cur) >= minlen:
                out.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append(cur.decode("ascii"))
    return out


def decode_lobby(payload):
    """Unknown EXE WONAPI lobby traffic: keep it raw."""
    return {"name": "LobbyDatagram", "len": len(payload), "hex": bytes(payload[:256]).hex(),
            "strings": _printable_runs(payload)[:16]}


def _u16s(b, o):
    """u16 character count n, then n UTF-16LE characters. Returns (str, new offset)."""
    n = _u16(b, o)
    if o + 2 + 2 * n > len(b):
        raise DecodeError("short UTF-16 string at %d" % o)
    return bytes(b[o + 2:o + 2 + 2 * n]).decode("utf-16-le", errors="replace"), o + 2 + 2 * n


# WON LAN lobby, UDP 8585 (LanBroadcastPort): field names are from the session-2 captures.
LAN_UDP = {1: "LanGameAdvert", 2: "LanGameKeepAlive", 3: "LanGameClosed", 4: "LanQuery"}


def decode_lan_udp(b):
    b = bytes(b)
    if not b:
        return decode_lobby(b)
    t = b[0]
    m = {"name": LAN_UDP.get(t, "LanUdp_0x%02x" % t), "lan_type": t, "len": len(b)}
    try:
        if t in (1, 2, 3):
            m["lobby_port"] = _u16(b, 1)
        if t == 1:
            m["magic"] = "%04x" % _u16(b, 3)
            m["in_progress"] = _u8(b, 5)
            m["game"], o = _u16s(b, 6)
            o += 2  # NUL
            m["players"] = _u16(b, o)
            m["max_players"] = _u16(b, o + 2)
            m["starting_epoch"] = _u16(b, o + 4)  # 14 Random, 0 Prehistoric (lansession.md §7)
            m["tail_hex"] = b[o + 6:].hex()
        elif t not in (2, 3, 4):
            m["hex"] = b.hex()
    except DecodeError as e:
        m["error"] = str(e)
        m["hex"] = b.hex()
    return m


# WON LAN lobby room, TCP 33336 (LobbyPort): records [u16 len incl. itself][u8 type][body].
LAN_TCP = {1: "LobbyJoin", 2: "LobbyRoster", 5: "LobbyChat", 6: "LobbyChatRelay",
           7: "LobbyKeepAlive", 8: "LobbyGameData"}
LAN_GAME = {0x01: "VersionCheck", 0x02: "GameSetup", 0x03: "SlotAssign", 0x04: "SetReady",
            0x05: "ReadyState", 0x07: "StartGame", 0x65: "SlotAttr", 0x66: "SetSlotAttr",
            0x68: "LaunchAck", 0x69: "LaunchAckReply"}


def decode_lan_tcp_record(r):
    r = bytes(r)
    t = r[2]
    m = {"name": LAN_TCP.get(t, "LobbyRec_0x%02x" % t), "lan_type": t, "len": len(r)}
    try:
        if t == 1:
            m["player_name"], _ = _u16s(r, 3)
        elif t == 2:
            m["status"], m["host_id"], m["your_id"] = _u16(r, 3), _u16(r, 5), _u16(r, 7)
            n, o, members = _u16(r, 9), 11, []
            for _ in range(n):
                cid = _u16(r, o)
                name, o = _u16s(r, o + 2)
                members.append({"id": cid, "name": name})
            m["members"] = members
        elif t == 5:
            m["flags"] = _u16(r, 3)
            m["text"], _ = _u16s(r, 5)
        elif t == 6:
            m["flags"], m["sender_id"] = _u16(r, 3), _u16(r, 5)
            m["text"], _ = _u16s(r, 7)
        elif t == 8:
            sub = _u8(r, 3)
            m["sub"] = sub
            m["name"] = "LobbyGameData." + LAN_GAME.get(sub, "0x%02x" % sub)
            if sub == 0x01:
                m["target"] = "%04x" % _u16(r, 4)
                m["version"], o = _u16s(r, 6)
                m["data_checksum"] = _u32(r, o)
            elif sub == 0x04:
                m["ready"] = _u8(r, 4)
            elif sub == 0x05:
                m["client_id"], m["ready"] = _u16(r, 4), _u8(r, 6)
            elif sub == 0x65:
                m["slot"], m["attr"], m["value"] = _u8(r, 4), _u8(r, 5), _u8(r, 6)
            elif sub == 0x66:
                m["attr"], m["value"] = _u8(r, 4), _u8(r, 5)
            else:
                m["body_hex"] = r[4:].hex()
        elif t != 7:
            m["body_hex"] = r[3:].hex()
    except DecodeError as e:
        m["error"] = str(e)
        m["body_hex"] = r[3:].hex()
    return m


def decode_datagram(payload):
    """Decode one UDP payload from/to port 33334. Returns a dict (never raises)."""
    plain = decrypt_datagram(payload)
    if plain is None:
        return {"name": "undecodable", "len": len(payload)}
    k = plain[0]
    t = plain[1]
    n = len(plain)
    if t == DISCOVERY_SEARCH and n == 0x43:
        return {"type": t, "name": "DiscoverySearch", "key": k, "len": n,
                "service": _cstr(plain[2:67])}
    if t == DISCOVERY_ADVERT and n == 0x88:
        return {"type": t, "name": "DiscoveryAdvert", "key": k, "len": n,
                "service": _cstr(plain[2:67]), "game": _cstr(plain[67:136])}
    m = decode_message(plain)
    m["key"] = k
    if "error" not in m and (t in NE_NAMES or t in EEM):
        try:
            expect = message_size(plain, 0)
            if expect != n:
                m["size_mismatch"] = [expect, n]
        except DecodeError as e:
            m.setdefault("error", str(e))
    return m

# ----------------------------------------------------------------------------------------
# Capture reading: pcap, pcapng (pktmon, tcpdump, Wireshark)
# ----------------------------------------------------------------------------------------

class Frame:
    __slots__ = ("ts", "linktype", "data", "orig_len", "comment", "iface")

    def __init__(self, ts, linktype, data, orig_len, comment=None, iface=0):
        self.ts, self.linktype, self.data = ts, linktype, data
        self.orig_len, self.comment, self.iface = orig_len, comment, iface


def read_capture(path):
    with open(path, "rb") as f:
        magic = f.read(4)
        f.seek(0)
        if magic == b"\x0a\x0d\x0d\x0a":
            yield from _read_pcapng(f)
        elif magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1",
                       b"\xa1\xb2\x3c\x4d"):
            yield from _read_pcap(f)
        else:
            raise ValueError("%s: not a pcap/pcapng file (magic %s)" % (path, magic.hex()))


def _read_pcap(f):
    hdr = f.read(24)
    magic = hdr[:4]
    endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
    linktype = struct.unpack(endian + "I", hdr[20:24])[0] & 0x0FFFFFFF
    div = 1e9 if nano else 1e6
    while True:
        rh = f.read(16)
        if len(rh) < 16:
            return
        sec, frac, incl, orig = struct.unpack(endian + "IIII", rh)
        data = f.read(incl)
        if len(data) < incl:
            return
        yield Frame(sec + frac / div, linktype, data, orig)


def _pcapng_options(buf, endian):
    o = 0
    while o + 4 <= len(buf):
        code, ln = struct.unpack_from(endian + "HH", buf, o)
        o += 4
        if code == 0:
            return
        yield code, buf[o:o + ln]
        o += (ln + 3) & ~3


def _read_pcapng(f):
    endian = "<"
    ifaces = []
    while True:
        bh = f.read(8)
        if len(bh) < 8:
            return
        btype, blen = struct.unpack(endian + "II", bh)
        if btype == 0x0A0D0D0A:
            bom = f.read(4)
            endian = "<" if bom == b"\x4d\x3c\x2b\x1a" else ">"
            blen = struct.unpack(endian + "I", bh[4:])[0]
            body = bom + f.read(blen - 12)
            ifaces = []
        else:
            body = f.read(blen - 8)
        if len(body) < blen - 8:
            return
        body = body[:-4]  # trailing block length
        if btype == 0x00000001:  # IDB
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            tsresol = 6
            for code, val in _pcapng_options(body[8:], endian):
                if code == 9 and val:
                    tsresol = val[0]
            if tsresol & 0x80:
                div = float(2 ** (tsresol & 0x7F))
            else:
                div = float(10 ** tsresol)
            ifaces.append((linktype, div))
        elif btype == 0x00000006:  # EPB
            iface, th, tl, cap, orig = struct.unpack_from(endian + "IIIII", body, 0)
            data = body[20:20 + cap]
            comment = None
            for code, val in _pcapng_options(body[20 + ((cap + 3) & ~3):], endian):
                if code == 1:
                    comment = val.decode("utf-8", errors="replace")
            lt, div = ifaces[iface] if iface < len(ifaces) else (1, 1e6)
            yield Frame(((th << 32) | tl) / div, lt, data, orig, comment, iface)
        elif btype == 0x00000003:  # SPB
            orig = struct.unpack_from(endian + "I", body, 0)[0]
            lt, _ = ifaces[0] if ifaces else (1, 1e6)
            yield Frame(0.0, lt, body[4:4 + orig], orig)
        elif btype == 0x00000002:  # obsolete PB
            iface, _drops, th, tl, cap, orig = struct.unpack_from(endian + "HHIIII", body, 0)
            lt, div = ifaces[iface] if iface < len(ifaces) else (1, 1e6)
            yield Frame(((th << 32) | tl) / div, lt, body[20:20 + cap], orig, None, iface)

# ----------------------------------------------------------------------------------------
# Link layer / IPv4 / UDP / TCP
# ----------------------------------------------------------------------------------------

def _l2_to_ip(linktype, d):
    """Return the IPv4 packet inside a frame, or None."""
    if linktype == 1:  # Ethernet
        if len(d) < 14:
            return None
        o, et = 14, struct.unpack_from(">H", d, 12)[0]
        while et in (0x8100, 0x88A8) and len(d) >= o + 4:
            et = struct.unpack_from(">H", d, o + 2)[0]
            o += 4
        return d[o:] if et == 0x0800 else None
    if linktype in (0, 108):  # BSD NULL / OpenBSD LOOP (macOS lo0, utun)
        if len(d) < 4:
            return None
        fam = struct.unpack_from("<I", d, 0)[0]
        if fam not in (2,):
            fam = struct.unpack_from(">I", d, 0)[0]
        return d[4:] if fam == 2 else None
    if linktype in (101, 12, 14, 228):  # raw IP
        return d if d and d[0] >> 4 == 4 else None
    if linktype == 113:  # Linux SLL
        return d[16:] if len(d) >= 16 and struct.unpack_from(">H", d, 14)[0] == 0x0800 else None
    if linktype == 276:  # Linux SLL2
        return d[20:] if len(d) >= 20 and struct.unpack_from(">H", d, 0)[0] == 0x0800 else None
    if linktype == 105 and len(d) >= 32:  # 802.11 data frame + LLC/SNAP (pktmon on Wi-Fi)
        fc = d[0]
        if (fc >> 2) & 3 != 2:
            return None
        o = 24 + (2 if fc & 0x80 else 0) + (6 if (d[1] & 3) == 3 else 0)
        if d[o:o + 6] == b"\xaa\xaa\x03\x00\x00\x00" and d[o + 6:o + 8] == b"\x08\x00":
            return d[o + 8:]
        return None
    if linktype == 258 and len(d) >= 12:  # macOS PKTAP (tcpdump -i pktap / -i any)
        hlen, _type_id, inner = struct.unpack_from("<III", d, 0)
        return _l2_to_ip(inner, d[hlen:]) if 12 <= hlen <= len(d) else None
    return None


class IPv4Reassembler:
    def __init__(self):
        self.frags = {}

    def feed(self, ip):
        """Return (src, dst, proto, payload) for a complete datagram, else None."""
        if len(ip) < 20 or ip[0] >> 4 != 4:
            return None
        ihl = (ip[0] & 0x0F) * 4
        total = struct.unpack_from(">H", ip, 2)[0]
        ident, flags_frag = struct.unpack_from(">HH", ip, 4)
        proto = ip[9]
        src = "%d.%d.%d.%d" % tuple(ip[12:16])
        dst = "%d.%d.%d.%d" % tuple(ip[16:20])
        if total == 0:  # TSO / offloaded on the capture host
            total = len(ip)
        payload = ip[ihl:total]
        mf = flags_frag & 0x2000
        off = (flags_frag & 0x1FFF) * 8
        if not mf and off == 0:
            return src, dst, proto, payload
        key = (src, dst, ident, proto)
        parts = self.frags.setdefault(key, {})
        parts[off] = (payload, bool(mf))
        data = b""
        while len(data) in parts:
            chunk, more = parts[len(data)]
            data += chunk
            if not more:
                del self.frags[key]
                return src, dst, proto, data
        return None


class TCPStream:
    """Minimal in-order reassembly per direction (no SACK; good enough for LAN xfer)."""

    def __init__(self):
        self.next_seq = None
        self.pending = {}
        self.buf = bytearray()

    def feed(self, seq, data, syn):
        if syn:
            self.next_seq = (seq + 1) & 0xFFFFFFFF
            return
        if not data:
            return
        if self.next_seq is None:
            self.next_seq = seq
        self.pending[seq] = data
        while self.next_seq in self.pending:
            chunk = self.pending.pop(self.next_seq)
            self.buf += chunk
            self.next_seq = (self.next_seq + len(chunk)) & 0xFFFFFFFF
        # drop stale retransmissions
        for s in [s for s in self.pending if ((s - self.next_seq) & 0xFFFFFFFF) > 0x7FFFFFFF]:
            del self.pending[s]

# ----------------------------------------------------------------------------------------
# Session analysis
# ----------------------------------------------------------------------------------------

def _pktmon_dedup_key(frame):
    """pktmon logs a packet once per component it crosses; its comment carries
    'PktGroupId N, PktNumber M, Appearance A, ...'. Use (group, number) when present."""
    c = frame.comment
    if not c or "PktGroupId" not in c:
        return None
    fields = {}
    for part in c.replace(";", ",").split(","):
        bits = part.strip().split(None, 1)
        if len(bits) == 2:
            fields[bits[0]] = bits[1].strip()
    if "PktGroupId" in fields and "PktNumber" in fields:
        return ("pktmon", fields["PktGroupId"], fields["PktNumber"])
    return None


class Event(dict):
    pass


def iter_events(paths, dedup_window=0.050):
    """Yield decoded events (dicts) from one or more captures, time-ordered per file."""
    for path in paths:
        reasm = IPv4Reassembler()
        streams = collections.defaultdict(TCPStream)
        seen_pktmon = set()
        recent = collections.OrderedDict()
        stats = collections.Counter()
        for fr in read_capture(path):
            stats["frames"] += 1
            if fr.orig_len and len(fr.data) < fr.orig_len:
                stats["truncated"] += 1
            k = _pktmon_dedup_key(fr)
            if k is not None:
                if k in seen_pktmon:
                    stats["dup_pktmon"] += 1
                    continue
                seen_pktmon.add(k)
            ip = _l2_to_ip(fr.linktype, fr.data)
            if ip is None:
                continue
            # content de-dup (pktmon without comments, or capture on bridged interfaces)
            h = hashlib.blake2b(ip[:20] + ip[20:], digest_size=12).digest()
            prev = recent.get(h)
            if prev is not None and abs(fr.ts - prev) <= dedup_window:
                stats["dup_content"] += 1
                continue
            recent[h] = fr.ts
            while len(recent) > 4096:
                recent.popitem(last=False)
            got = reasm.feed(ip)
            if got is None:
                continue
            src, dst, proto, l4 = got
            if proto == 17 and len(l4) >= 8:
                sp, dp, ulen = struct.unpack_from(">HHH", l4, 0)
                if sp not in EE_PORTS and dp not in EE_PORTS:
                    continue
                payload = l4[8:ulen] if ulen >= 8 else l4[8:]
                stats["udp"] += 1
                ev = Event(file=os.path.basename(path), ts=fr.ts, proto="udp",
                           src="%s:%d" % (src, sp), dst="%s:%d" % (dst, dp), size=len(payload))
                if UDP_GAME_PORT in (sp, dp):
                    ev["msg"] = decode_datagram(payload)
                elif LAN_BCAST_PORT in (sp, dp):
                    ev["msg"] = decode_lan_udp(payload)
                elif sp in LOBBY_PORTS or dp in LOBBY_PORTS:
                    ev["msg"] = decode_lobby(payload)
                else:
                    ev["msg"] = {"name": "udp_other", "hex": payload[:64].hex()}
                yield ev
            elif proto == 6 and len(l4) >= 20:
                sp, dp, seq, ack, off_flags = struct.unpack_from(">HHIIH", l4, 0)
                if sp not in EE_PORTS and dp not in EE_PORTS:
                    continue
                doff = (off_flags >> 12) * 4
                flags = off_flags & 0x3F
                data = l4[doff:]
                stats["tcp"] += 1
                key = (src, sp, dst, dp)
                st = streams[key]
                before = len(st.buf)
                st.feed(seq, data, bool(flags & 0x02))
                base = Event(file=os.path.basename(path), ts=fr.ts, proto="tcp",
                             src="%s:%d" % (src, sp), dst="%s:%d" % (dst, dp))
                if flags & 0x02:
                    yield Event(base, msg={"name": "tcp_syn" + ("_ack" if flags & 0x10 else ""),
                                           "len": 0})
                if flags & 0x01:
                    yield Event(base, msg={"name": "tcp_fin", "len": 0})
                if flags & 0x04:
                    yield Event(base, msg={"name": "tcp_rst", "len": 0})
                if len(st.buf) > before and (sp in LOBBY_PORTS or dp in LOBBY_PORTS):
                    while len(st.buf) >= 2:
                        n = st.buf[0] | st.buf[1] << 8
                        if n < 3 or n > 0x4000:
                            yield Event(base, size=len(st.buf), msg=dict(
                                decode_lobby(bytes(st.buf)), name="LobbyTCP"))
                            st.buf.clear()
                            break
                        if len(st.buf) < n:
                            break
                        rec = bytes(st.buf[:n])
                        del st.buf[:n]
                        yield Event(base, size=n, msg=decode_lan_tcp_record(rec))
                elif len(st.buf) > before:
                    # TCP game messages are not encrypted; xfer records are fixed size
                    while len(st.buf) >= 4:
                        try:
                            sz = message_size(st.buf, 0)
                        except DecodeError:
                            yield Event(base, msg={"name": "tcp_unparsed",
                                                   "len": len(st.buf),
                                                   "hex": bytes(st.buf[:64]).hex()})
                            st.buf.clear()
                            break
                        if len(st.buf) < sz:
                            break
                        rec = bytes(st.buf[:sz])
                        del st.buf[:sz]
                        yield Event(base, size=sz, msg=decode_message(rec))
        yield Event(file=os.path.basename(path), ts=None, proto="stats", msg=dict(stats))


def _brief(m):
    n = m.get("name", "?")
    skip = {"type", "name", "len", "for_slot", "orig_slot", "key", "inputs", "body_hex"}
    bits = ["%s=%s" % (k, v) for k, v in m.items() if k not in skip]
    fs, os_ = m.get("for_slot"), m.get("orig_slot")
    if fs is not None and (fs != 0xFF or os_ != 0xFF):
        bits.append("slots=%s>%s" % (os_, fs))
    if "inputs" in m:
        bits.append("inputs=[%s]" % ", ".join(
            "%s(p%s,s%s)" % (i.get("name"), i.get("player", "-"), i.get("orig_slot"))
            for i in m["inputs"]))
    return n + (" " + " ".join(bits) if bits else "")

# ----------------------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------------------

def cmd_timeline(args):
    t0 = None
    shown = 0
    for ev in iter_events(args.captures):
        if ev["proto"] == "stats":
            print("# %s: %s" % (ev["file"], json.dumps(ev["msg"])))
            continue
        m = ev["msg"]
        if args.no_ticks and m.get("type") in (0x26, 0x27):
            continue
        if t0 is None:
            t0 = ev["ts"]
        print("%10.3f %-3s %-21s -> %-21s %4s  %s" % (
            ev["ts"] - t0, ev["proto"], ev["src"], ev["dst"], ev.get("size", ""), _brief(m)))
        if args.nested:
            for i in m.get("inputs", []):
                print("%62s  - %s" % ("", _brief(i)))
        shown += 1
        if args.limit and shown >= args.limit:
            break


def cmd_summary(args):
    by_name = collections.Counter()
    by_flow = collections.Counter()
    nested = collections.Counter()
    errors = collections.Counter()
    first = last = None
    cts = []
    searches = []
    adverts = {}
    versions = set()
    lobby = collections.Counter()
    lan_games = {}
    for ev in iter_events(args.captures):
        if ev["proto"] == "stats":
            print("%s: %s" % (ev["file"], json.dumps(ev["msg"])))
            continue
        m = ev["msg"]
        first = ev["ts"] if first is None else first
        last = ev["ts"]
        by_name[m.get("name")] += 1
        by_flow["%s %s -> %s" % (ev["proto"], ev["src"], ev["dst"])] += 1
        if "error" in m or "size_mismatch" in m:
            errors[m.get("name")] += 1
        for i, carrier in walk(m):
            if carrier is not None:
                nested["%s (in %s)" % (i.get("name"), carrier)] += 1
        if m.get("type") == 0x26:
            cts.append((ev["ts"], m.get("ct"), m.get("pts_to_perform"), m.get("pts_per_second"),
                        m.get("generate_checksum"), ev["dst"]))
        if m.get("name") == "DiscoverySearch":
            searches.append((ev["ts"], ev["src"]))
        if m.get("name") == "DiscoveryAdvert":
            adverts[(ev["src"].split(":")[0], m.get("game"))] = m.get("service")
        if "lan_type" in m or m.get("name") in ("LobbyDatagram", "LobbyTCP"):
            lobby[(ev["proto"], ev["src"], ev["dst"])] += 1
        if m.get("name") == "LanGameAdvert":
            lan_games[(ev["src"].split(":")[0], m.get("game"))] = m
        if m.get("type") == 0x22:
            versions.add((m.get("version"), m.get("data_checksum"), m.get("player_name")))
    if first is None:
        print("no Empire Earth traffic found")
        return
    print("span: %.1f s" % (last - first))
    print("\nmessages:")
    for k, v in by_name.most_common():
        print("  %6d  %s" % (v, k))
    if nested:
        print("\nnested inputs:")
        for k, v in nested.most_common():
            print("  %6d  %s" % (v, k))
    print("\nflows:")
    for k, v in by_flow.most_common(30):
        print("  %6d  %s" % (v, k))
    if errors:
        print("\ndecode problems:", dict(errors))
    if searches:
        srcs = collections.defaultdict(list)
        for ts, s in searches:
            srcs[s].append(ts)
        for s, tl in srcs.items():
            gaps = [b - a for a, b in zip(tl, tl[1:])]
            print("\nsearch from %s: %d packets, median interval %.3f s" % (
                s, len(tl), sorted(gaps)[len(gaps) // 2] if gaps else 0))
    if lobby:
        for (ip, game), adv in lan_games.items():
            print("LAN lobby game: %s %r port=%s players=%s/%s in_progress=%s" % (
                ip, game, adv.get("lobby_port"), adv.get("players"), adv.get("max_players"),
                adv.get("in_progress")))
        print("\nWON LAN lobby flows (UDP 8585 adverts, TCP 33336 rooms):")
        for (proto, s_, d_), v in lobby.most_common(20):
            print("  %6d  %s %s -> %s" % (v, proto, s_, d_))
    for (ip, game), svc in adverts.items():
        print("advert: %s game=%r service=%r" % (ip, game, svc))
    for v in versions:
        print("connection request: version=%r data_checksum=%s (0x%08x, menu %%04d=%04d) player=%r"
              % (v[0], v[1], v[1] or 0, (v[1] or 0) & 0x1FFF, v[2]))
    if cts:
        uniq = {}
        for ts, ct, pts, pps, gc, dst in cts:
            uniq.setdefault(ct, (ts, pts, pps, gc))
        ks = sorted(uniq)
        span = uniq[ks[-1]][0] - uniq[ks[0]][0] if len(ks) > 1 else 0
        print("\ncommunications ticks: %d distinct (CT %s..%s), %.2f CT/s" % (
            len(ks), ks[0], ks[-1], (len(ks) - 1) / span if span else 0))
        pts = collections.Counter(v[1] for v in uniq.values())
        pps = collections.Counter(v[2] for v in uniq.values())
        gc = sum(1 for v in uniq.values() if v[3])
        print("  PTs per CT: %s" % dict(pts.most_common(8)))
        print("  PTs per second: %s" % dict(pps.most_common(8)))
        print("  CTs requesting a checksum: %d" % gc)


def walk(m, carrier=None):
    """Yield (message, carrier_name) for a message and the inputs nested in it."""
    yield m, carrier
    for i in m.get("inputs", []):
        yield from walk(i, m.get("name"))


def cmd_export(args):
    os.makedirs(args.out_dir, exist_ok=True)
    files = []

    def writer(name, header):
        f = open(os.path.join(args.out_dir, name), "w", newline="")
        files.append(f)
        w = csv.writer(f)
        w.writerow(header)
        return w

    mj = open(os.path.join(args.out_dir, "messages.jsonl"), "w")
    files.append(mj)
    tk = writer("ticks.csv", ["file", "ts", "src", "dst", "ct", "pts_to_perform",
                              "pts_per_second", "generate_checksum", "dump_the_world",
                              "n_inputs", "input_types"])
    ck = writer("checksums.csv", ["file", "ts", "src", "dst", "carrier", "orig_slot", "tick",
                                  "checksum", "checksum_hex"])
    dc = writer("discovery.csv", ["file", "ts", "src", "dst", "kind", "key", "service", "game"])
    se = writer("session.csv", ["file", "ts", "src", "dst", "carrier", "name", "details"])
    per_tick = collections.defaultdict(list)
    session_types = {0x20, 0x22, 0x23, 0x24, 0x25, 0x2b, 0x2d, 0x2e, 0x2f, 0x30, 0x31, 0x33,
                     0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x5d, 0x5e, 0x91, 0xa3}
    for ev in iter_events(args.captures):
        mj.write(json.dumps(ev, default=str) + "\n")
        if ev["proto"] == "stats":
            continue
        top = ev["msg"]
        for m, carrier in walk(top):
            t = m.get("type")
            if t == 0x26 and carrier is None:
                tk.writerow([ev["file"], "%.6f" % ev["ts"], ev["src"], ev["dst"], m.get("ct"),
                             m.get("pts_to_perform"), m.get("pts_per_second"),
                             m.get("generate_checksum"), m.get("dump_the_world"),
                             len(m.get("inputs", [])),
                             " ".join("%02x" % i.get("type", 0) for i in m.get("inputs", []))])
            elif t == 0x32:
                ck.writerow([ev["file"], "%.6f" % ev["ts"], ev["src"], ev["dst"], carrier or "",
                             m.get("orig_slot"), m.get("tick"), m.get("checksum"),
                             "%08x" % (m.get("checksum") or 0)])
                per_tick[m.get("tick")].append({"slot": m.get("orig_slot"), "src": ev["src"],
                                                "checksum": m.get("checksum")})
            elif m.get("name") in ("DiscoverySearch", "DiscoveryAdvert"):
                dc.writerow([ev["file"], "%.6f" % ev["ts"], ev["src"], ev["dst"], m["name"],
                             m.get("key"), m.get("service"), m.get("game", "")])
            if t in session_types or (t == 0x26 and m.get("dump_the_world")) or \
                    ("lan_type" in m and m.get("name") not in ("LanGameKeepAlive",)):
                se.writerow([ev["file"], "%.6f" % ev["ts"], ev["src"], ev["dst"], carrier or "",
                             m.get("name"), _brief(m)])
    out = []
    for tick in sorted(k for k in per_tick if k is not None):
        rows = per_tick[tick]
        vals = {r["checksum"] for r in rows}
        out.append({"tick": tick, "agree": len(vals) == 1, "responses": rows})
    with open(os.path.join(args.out_dir, "checksums.json"), "w") as f:
        json.dump(out, f, indent=1)
    for f in files:
        f.close()
    print("wrote %s (%d checksum ticks, %d disagreeing between captured responses)" % (
        args.out_dir, len(out), sum(1 for o in out if not o["agree"])))


def cmd_keytable(args):
    print("key table: %d bytes, sha256 %s" % (len(KEY_TABLE),
                                               hashlib.sha256(KEY_TABLE).hexdigest()))
    print("first 16: %s" % KEY_TABLE[:16].hex())


def cmd_selftest(args):
    import random
    rng = random.Random(1)
    # discovery round trip
    srch = bytearray(67)
    srch[1] = 0x15
    srch[2:11] = b"EEService"
    for k in (1, 77, 254, 255):
        pkt = encrypt_datagram(bytes(srch), k)
        d = decode_datagram(pkt)
        assert d["name"] == "DiscoverySearch" and d["service"] == "EEService", d
    # communications tick with nested inputs
    sel = bytes([0, 0x48, 0xFF, 1, 1]) + struct.pack("<I", 1234)          # EEMSelectUnit, 9 B
    path = bytes([0, 0x4D, 0xFF, 1, 1]) + bytes(16)                         # EEMPathTo, 21 B
    chat_s = b"hello"
    chat = bytes([0, 0x7D, 0xFF, 1, 1]) + struct.pack("<I", len(chat_s) + 2) + chat_s + b"\0\0"
    ct = bytes([0, 0x26, 0xFF, 0xFE]) + struct.pack("<I", 4242) + bytes([3, 15, 1, 0, 3]) \
        + sel + path + chat
    pkt = encrypt_datagram(ct, rng.randint(1, 254))
    d = decode_datagram(pkt)
    assert d["name"] == "NEMCommunicationsTick" and d["ct"] == 4242, d
    assert [i["name"] for i in d["inputs"]] == ["EEMSelectUnit", "EEMPathTo", "EEMInGameChat"], d
    assert d["inputs"][2]["text"] == "hello" and "size_mismatch" not in d, d
    wdc = bytes([0, 0x32, 0xFF, 2]) + struct.pack("<II", 4242, 0xDEADBEEF)
    d = decode_datagram(encrypt_datagram(wdc, 9))
    assert d["tick"] == 4242 and d["checksum"] == 0xDEADBEEF and d["orig_slot"] == 2, d
    req = bytes([0, 0x22, 0xFF, 0xFF, 0]) + struct.pack("<I", 9) + b"2.00 12\0\0" \
        + struct.pack("<I", 5) + b"Max\0\0" + struct.pack("<I", 0x12345678)
    d = decode_datagram(encrypt_datagram(req, 200))
    assert d["version"] == "2.00 12" and d["player_name"] == "Max", d
    assert d["data_checksum"] == 0x12345678 and "size_mismatch" not in d, d
    print("selftest ok (key table sha256 %s)" % hashlib.sha256(KEY_TABLE).hexdigest()[:16])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("timeline")
    p.add_argument("captures", nargs="+")
    p.add_argument("--nested", action="store_true", help="also print nested player inputs")
    p.add_argument("--no-ticks", action="store_true", help="hide CT/CT-response traffic")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_timeline)
    p = sub.add_parser("summary")
    p.add_argument("captures", nargs="+")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("export")
    p.add_argument("captures", nargs="+")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("keytable")
    p.set_defaults(fn=cmd_keytable)
    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
