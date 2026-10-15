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

## Status
| ID | State | Result |
|---|---|---|
| P5-A winsock | **merged** (`ee` 1220f89, ee-macos 0cc7e1e) | wsock32 28/28 and ws2_32 24/24 of EE's imports (one shared implementation), iphlpapi, Netbios NCBASTAT (MAC address), per-interface broadcast fan-out with `IP_PKTINFO`, `WSAEventSelect` re-arming, `[network] offline_hosts` (the dead Sierra host fails in 0.01 ms). About 800 `winsock_tests` checks. **EE check:** the translated `TCP Protocol.dll` activates, enumerates en0 (192.168.1.227), connects and sends `NEMClientConnectionRequest` (v2.00.2974, checksum 0x0e998b9e); `eelan.py` decrypts it exactly. Adapter index ≥ 2 hits the interior `delete` and continues, as on Windows. |
| P5-B lan-tests | phase 1 (static spec) done 2026-10-15, branch `phase5/lobby` | [lobby.md](reference/lobby.md): the lobby is WONAPI's GUI, one owned `WS_POPUP` custom-drawn with GDI into a back buffer and `BitBlt` on `WM_PAINT`, shown after `FlipToGDISurface` while the render thread stops presenting; the cooperative level stays 0x513. Kit gaps: GDI frames are composited over black and replace D3D7 frames, `FlipToGDISurface` is a no-op, owned windows can fall below their owner, cursor shapes, timer id 0, `WM_CLOSE`→`WM_QUIT`, double clicks, wheel routing, CP1252, `Arc`, `"TimesRoman"`. Plan: ~3½–4½ weeks kit (1½–2 for a usable lobby) + ~1 week tests, after P3-E. wininet stays unserved (by design; add m2/m3 to `offline_hosts`). Waiting on legion 0021 (lobby trace). Then Mac↔legion LAN sessions with checksum streams. ZeroTier fan-out is tested on fake interfaces only. |
