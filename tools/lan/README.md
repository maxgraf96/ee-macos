# tools/lan: Empire Earth LAN capture analysis

These tools decode the base game's LAN protocol from packet captures and turn it into
streams we can diff. The protocol itself is described in
[docs/reference/netcode.md](../../docs/reference/netcode.md).

| File | What it is |
|---|---|
| `eelan.py` | Pure-stdlib decoder: pcap/pcapng reader, RC4 de-obfuscation, NE/EEM message decoder, WON LAN lobby decoder (UDP 8585 game list, TCP 33336 lobby room), timeline/summary/export commands |
| `test_eelan.py` | Builds synthetic captures (pktmon-style pcapng, macOS-style NULL pcap) and checks the decoder |
| `windows/frida/netcode_hooks.js` | Frida script for `windows/frida/run.py`: world-checksum stream, host checksum tally, OOS removals, plaintext wire oracle |

No game bytes are in here. The 261-byte RC4 key table is re-derived from the generator's
algorithm and constants at import time (`python -I tools/lan/eelan.py keytable` prints its
SHA-256: `df87db49…`).

## Usage

Run with `python -I` (stdlib only, Python ≥ 3.9):

```sh
python -I tools/lan/eelan.py summary  analysis/lan-captures/lan.pcapng
python -I tools/lan/eelan.py timeline analysis/lan-captures/lan.pcapng --no-ticks      # session events only
python -I tools/lan/eelan.py timeline analysis/lan-captures/lan.pcapng --nested --limit 200
python -I tools/lan/eelan.py export   analysis/lan-captures/*.pcapng --out-dir analysis/lan-captures/decoded
python -I tools/lan/test_eelan.py      # self-test, no capture needed
```

`export` writes:

| File | Content |
|---|---|
| `messages.jsonl` | every decoded datagram / TCP record, with timestamps and endpoints |
| `ticks.csv` | communications ticks (NE type 0x26) as sent by the host: CT number, PTs to perform, PTs/s, checksum and dump flags, nested input types |
| `checksums.csv` | `NEMWorldDatabaseChecksumResponse` (0x32): tick, checksum, originating slot. This is the wire half of the OOS oracle |
| `checksums.json` | the same grouped per tick, with `agree` true/false across the captured responses |
| `discovery.csv` | search (0x15) and advertisement (0x16) packets |
| `session.csv` | connect, add/remove player (OOS verdicts), start/stop, pause, load events, plus lobby events (game adverts, join, roster, chat, ready, StartGame) |

The host computes its own checksum locally and never sends it. A capture therefore shows
the clients' values only. To get the host's value, use `windows/frida/netcode_hooks.js` on the host
(`world` records), or run a capture on a machine that is a client.

## Capturing

**Windows (`legion`), inbox pktmon:** see `docs/reference/legion.md` §7, and add
`pktmon filter add EE8585 -p 8585` for the lobby's broadcast port. Use
`--pkt-size 0`, because the default of 128 bytes truncates CT datagrams. Then convert with
`pktmon etl2pcap lan.etl --out lan.pcapng`. The decoder handles pktmon's quirks:
- **Duplicate sightings.** pktmon logs a packet once per stack component it crosses.
  Duplicates are dropped by the `PktGroupId`/`PktNumber` in the per-packet comment. Without
  comments, duplicates are dropped by identical IP bytes within 50 ms.
- **Truncated packets** are counted in the `stats` line.
- **Link types:** Ethernet with 802.1Q tags, and 802.11 data frames with LLC/SNAP in case pktmon logs at the Wi-Fi miniport.

**macOS:**
`sudo tcpdump -i en0 -w lan.pcapng 'udp port 8585 or udp port 33336 or udp port 33334 or tcp port 33335'`. Use the ZeroTier
`feth*` interface, or `-i any` (PKTAP), for VPN play. The NULL/LOOP (lo0, utun), raw-IP,
Linux SLL/SLL2 and PKTAP link types are supported.

Ports, confirmed by the session-2 captures. Use `udp port 8585 or port 33336 or udp port 33334 or tcp port 33335`:
- **UDP 8585:** the lobby's LAN game list, decoded as `LanQuery`, `LanGameAdvert`, `LanGameKeepAlive`
  (every 15 s) and `LanGameClosed`.
- **TCP 33336:** the lobby room on the host, decoded as `LobbyJoin`, `LobbyRoster`, `LobbyChat`/`ChatRelay`,
  `LobbyKeepAlive` and `LobbyGameData.*` (VersionCheck with the data checksum, GameSetup, SlotAssign,
  SetReady/ReadyState, SetSlotAttr/SlotAttr, **StartGame**, LaunchAck). UDP 33336 is shown raw
  if it ever appears; none has been seen.
- **UDP 33334:** all in-game traffic from `TCP Protocol.dll`. The host also sends one `EEService` search
  per hosted game.
- **TCP 33335:** in-game file transfer from the plugin. The client connects at game start. Lobby
  file-transfer records on the same port would show up as `tcp_unparsed`.

The pktmon filter needs `-p 33336` without a protocol restriction, because the lobby room is TCP.

## Frida hooks

Run `windows/frida/netcode_hooks.js` on each peer during a LAN
game:
`run.py --scripts netcode_hooks,threads --seconds 600 --out C:\EEPort\captures\lan\frida`.

- `world`: `[ms, tid, tick, dump, flags, checksum]` for every call to the world-checksum
  method (EXE `0x545ebf`, RVA `0x145ebf`).
- `logged`: the host's tally, as `[ms, tick, checksum, slot]`.
- `removed`: player removals.
- `wire`: plaintext datagrams before encryption (send) or after decryption (receive).
  Compare `wire` against `messages.jsonl` to validate the decoder on real traffic.
