# Phase 5: LAN multiplayer → M4 (Mac↔Windows LAN game, no OOS)

Started 2026-10-15 (the Winsock layer), in parallel with Phases 3 and 4. Context:
- [PLAN.md](PLAN.md) §5.6;
- **[netcode.md](reference/netcode.md):** the validated protocol model, the §7 Winsock checklist and the adapter-crash analysis;
- the protocol in [phase2.md](phase2.md).

## Done when
A Mac running the native build and a Windows GOG client play a LAN game to the end, with no out-of-sync, on these setups:
- the physical LAN;
- ZeroTier;
- Mac↔Mac.

The per-tick world checksums agree (`windows/frida/netcode_hooks.js` on Windows; the equivalent hook on the Mac).

## Workstreams
| ID | Owns | Where |
|---|---|---|
| P5-A winsock | **wsock32** (the lobby) and **ws2_32** (the game) on BSD sockets:<br>• non-blocking I/O; `WSAEventSelect` with re-arm while data is queued; `select`;<br>• broadcast with per-interface fan-out (ZeroTier!); address reuse;<br>• instant failure for the dead Sierra DNS lookups.<br>**iphlpapi** `GetAdaptersInfo`/`GetAdaptersAddresses`/`GetIpForwardTable`: at most 10 adapters, ordered primary `en*`, then ZeroTier/`utun` with IPv4; nothing that could trigger `TCP Protocol.dll`'s adapter-index heap bug.<br>**netapi32** `Netbios` (MAC address), `gethostbyname(own name)` ordering. | kit `feat/winsock` |
| P5-B lan-tests | Mac↔legion LAN sessions with checksum streams (after P3 interactive + P5-A) | later |
