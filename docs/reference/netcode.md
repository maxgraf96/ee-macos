# Empire Earth LAN netcode (GOG 2.00 base game)

Status: Phase 1 reference, 2026-10-14. Owner: netcode agent. Tooling: [`tools/lan/`](../../tools/lan/README.md).

## Sources and labels

- **Sources.** Static analysis of the canonical GOG modules (PLAN §2.4):
  - `TCP Protocol.dll`: a Ghidra 12.1.4 project under `analysis/netcode/`, plus all 148 functions read.
  - `Low-Level Engine.dll` and `Empire Earth.exe`: the Phase 0 spike exports.
- **Labels.**
  - **[static]** is read from code.
  - **[emulated]** means the original x86 routine was executed under Unicorn and compared with our reimplementation.
  - **[measured]** is from `legion` ([legion.md](legion.md)).
  - **[inferred]** is a reasoned guess that needs a capture.
- **Addresses** are `module+RVA`. The EXE is fixed at `0x400000`, so EXE VA = RVA + `0x400000`. The DLLs prefer `0x10000000`.
- **Captures.** No LAN captures had reached `analysis/lan-captures/` when this was written. §10 says how to run the decoder on them, and lists what they must confirm.

## 1. Summary

- **Two network layers, four ports** (corrected 2026-10-14 after a Wine `+winsock` trace and community-re input):
  - **LAN lobby: the EXE's WON `LanLogic`, over `wsock32`.** It handles the lobby, the LAN game list, chat and "Host Game".
    - Ports come from `WONLobby.cfg`, which GOG ships: **LanBroadcastPort 8585** (UDP), **LobbyPort 33336** (UDP) and **EEFileTransferPort 33335**. The EXE defaults are 8585, 8888 and 8889 (EXE+`0x343350`, `+0x27b77b`) [static].
    - Opening *Multiplayer* binds a broadcast-enabled, non-blocking **UDP socket on 0.0.0.0:33336**, which lobby threads poll every ~20 ms [measured, Wine].
    - Its wire format is not decoded yet (§3.0).
  - **Game session: `TCP Protocol.dll` over `ws2_32`.** It is entered at the lobby→game hand-off.
    - **UDP 33334** carries all game traffic.
    - **TCP 33335** carries in-game file transfer. The lobby uses the same port number for its own file transfer.
    - There is no transport-level framing, sequencing or ack: **one datagram = one engine message**.
- **Obfuscation.** Every UDP datagram is **RC4-obfuscated**.
  - Byte 0 is a random key index `k` (1..254). `k = 0` means clear text: receivers skip decryption, but senders never pick 0.
  - Bytes 1..n−1 are RC4-encrypted with the 7-byte key `T[k−1 .. k+5]`.
  - `T` is a fixed 261-byte table that `TCP Protocol.dll` generates at start-up from an LCG.
  - [static + emulated: our table and RC4 match the DLL's own code bit for bit]
- **Discovery.**
  - **Normal flow.** LAN games are found by the **WON lobby (8585/33336)**. At the hand-off the client connects **directly** to the host IP the lobby supplied, on UDP 33334. This is the same code path as Join By IP (EXE+`0x27e310`), with no plugin search [static].
  - **The plugin also has its own `"EEService"` discovery:** a broadcast to 255.255.255.255:33334 every ≈1.05 s, answered by unicast to `searcher-IP:33334`.
    - Only the legacy UI actions reach it: `EEUIEMSOpenMultiplayerScreen`, `EEUIEMSJoinGame`, `EEAFindGames` and `EEAJoinOrHostAGame`.
    - The Multiplayer button did **not** start it in the Wine trace [measured].
    - It is probably reachable only through those paths and perhaps `-hostgame` / `-joinbyip` [inferred]. §3.1–3.2 describe it.
- **Lockstep.** The host sends a **communications tick (CT)** on a timer.
  - Each CT carries the inputs of all players and the number of **physics ticks (PTs)** to run.
  - Clients answer every CT with a CT response that carries their own inputs.
- **OOS check.** From CT 40 on, every multiplayer CT asks for a world checksum.
  - Every machine computes an additive 32-bit **world checksum** (EXE+`0x145ebf`; this is *not* `ESChecksum`).
  - Clients send it to the host (`NEMWorldDatabaseChecksumResponse`, type `0x32`).
  - The host tallies the values and decides by majority. On a mismatch, all machines dump an OOS log on the next CT, and the losers are removed with `NEMRemovePlayer(isOOS=1)`.
- **Version gate.** The connection request carries a **32-bit byte-sum** of:
  - `Low-Level Engine.dll`, `sxlrt233.dll` and `TCP Protocol.dll`;
  - `Data\Db\*.dat`, `Data\Unit AI Scripts\*.tai` and `Data\Models\*.cem`;
  - the archives' `Timestamp` tags.

  **The Mac port must therefore keep those original files byte-identical in the game folder**, even though it never executes the DLLs (§8).
- **"Network Adapter" is cosmetic.** All sockets bind `INADDR_ANY`. The adapter index only picks which local IP is *displayed* (§3.4).
- **"Debug OOS Handling" has no reader** outside option load/save/copy and an options checkbox [static] (§6.4).

## 2. Architecture

```
EXE (game: lobby UI, EEM* game messages, sim)            EXE+0xb18a8 registers EEM types 0x41..0xfd
  │  NESynchronousServer / NESynchronousClient (subclassed in the EXE)
Low-Level Engine.dll  "Network Engine"
  │  NEMessage (+29 NEM* engine messages, types 0x20..0x3c), NEMPlayerInput (input lists)
  │  NESynchronousServer: threads Main / Send / Xfer      NESynchronousClient: Main / Receive
  │  NEProtocol: GetProcAddress of 30 NPIM* entry points (engine+0x509af)
TCP Protocol.dll  ("TCP/IP Protocol", "WinSock 2.0"; plugin type NPIM)
     ws2_32 (24 imports), iphlpapi!GetAdaptersInfo, kernel32 WaitForSingleObject/Sleep

EXE WONAPI (WONLobby: LanLogic, EELobby*, routing/lobby UI; EXE 0x6aa000-0x6b2000 sockets)
     wsock32 (28 imports, WSAStartup 1.1), UDP 33336 (+ 8585, 33335) for the LAN lobby;
     hands off to EEServer/EEClient (EXE+0xc2536 / +0xafdbb) -> the NE layer above
```

- **Threads** [static; legion measured three network threads, single player included]:
  - **Server:**
    - `NESynchronousServer::Main` (engine+`0x5581d`) waits on 11 events: socket FD_READ, xfer FD_ACCEPT, start/stop/pause/unpause and others. It runs the SendToAllAndWait retries.
    - `::Send` (engine+`0x55d2e`) emits CTs on a timer.
    - `::Xfer` (engine+`0x55f09`) handles file transfer.
  - **Client:**
    - `NESynchronousClient::Main` (engine+`0x526f0`) does connect retries and the message hand-off.
    - `::Receive` (engine+`0x52d3f`) drains the socket and runs the 75 s server-silence timeout.
  - **Searcher thread:** `TCP Protocol.dll+0x32c4` runs only on the legacy plugin-discovery path (§3.0).
  - Every thread starts by calling `UMSetFloatingPointPrecision(0x10000)` (PC=53) and resets RC to nearest each loop.
- **Single player** also creates a local `NESynchronousServer`:
  - It is named `"EEName"`, so it is not advertised.
  - It opens UDP 33334 and TCP 33335, which is why Windows raises the firewall prompt on the first random map [measured, legion §5.6].
- **Message object.**
  - Fields: `+8` type, `+9` for-slot, `+0xa` originating slot, `+0xb` byte 0.
  - `ExportHeader` writes `[byte0][type][forSlot][origSlot]`. Byte 0 is 0 and is **overwritten with the RC4 key index** by the protocol (engine+`0x4d025`).
  - The receiver dispatches on byte 1 through `NEMessage::sConstructors[256]` (engine+`0x4cfee`).
- **Engine message factory:** engine+`0x4d0cf`. **EXE factory:** EXE+`0xb1b31`.
- **Strings on the wire:**
  - `UWideString` is MBCS, not UTF-16. Format: `u32 n = strlen+2`, then the bytes, then two NULs (engine+`0x80eff`).
  - Discovery fields are fixed-size C strings. Bytes after the NUL are uninitialised stack and should be ignored.

## 3. Discovery and session setup

### 3.0 Who does what (reconciled)

| Phase | Code | Ports | Evidence |
|---|---|---|---|
| Plugin activation at engine start | `NPIMActivate`: `WSAStartup(2.0)`, then a probe TCP socket that is closed again | — | [measured, Wine: one `WSAStartup 0x2` and `socket(2,1,0)`/`closesocket` at start-up] |
| Multiplayer screen / LAN lobby, game list, chat, host | EXE WONAPI `LanLogic` over **wsock32** | UDP **33336** bound (`SO_BROADCAST`, `FIONBIO`) and polled every ~20 ms; **8585** = `LanBroadcastPort` | [measured: bind 0.0.0.0:33336 by the EXE after `WSAStartup 0x101`; no 8585 socket and no `sendto` were seen in ~100 s at that screen]. 8585's exact role (broadcast destination vs listen port) is [inferred] from the config name, the "LAN Broadcast" port dialog (`LanPortDialog`, `ChangeLANBroadcast`) and community-re |
| Dead online lobby | WONAPI resolves `empireearth.m1.sierra.com` (`DirServer` in `WONLobby.cfg`) | — | [measured: `gethostbyname` failed at once in Wine]. The HLE must fail fast and never block on DNS |
| Lobby→game hand-off, client | EXE+`0x27e310` creates `EEClient`, then `Connect(hostIP from lobby, "EEService", game)` **without** `SearchForGame` | UDP 33334 direct | [static] |
| Lobby→game hand-off, host | `EEServer` (EXE+`0xc2536`) → `NESynchronousServer` → `NPIMPublish` | UDP 33334 game socket, TCP 33335 xfer listener | [static; community-re `+snoop` A/B]. If the lobby path uses a *private* server (the `"EEName"` sites), the host neither runs the 1-s duplicate-name search nor answers `0x15` [inferred] |
| Legacy plugin discovery | `EEUIEMSOpenMultiplayerScreen` (EXE+`0x10c0f`) / `EEUIEMSJoinGame` (+`0x1021a`) → `SearchForGame(true)`; `EEAFindGames` (+`0x2091`) reads the list; `EEAJoinOrHostAGame` (+`0x2bb8`) hosts or joins | UDP 33334 broadcast | [static]; not triggered by the Multiplayer button [measured] |

Consequences:
- **Firewall and VPN:** allow inbound UDP 8585, UDP 33336, UDP 33334 and TCP 33335. The lobby's broadcasts (8585/33336) need the same per-interface fan-out as §7 describes for 33334.
- **Decoding:** `tools/lan/eelan.py` captures 8585/33336 traffic and shows it raw (hex plus printable strings) until the lobby protocol is decoded.

### 3.1 Plugin search, legacy path (client side, `TCP Protocol.dll+0x2ec6`, driven by thread `+0x32c4`) [static]
1. Wait on the exit event for 50 ms (`WaitForSingleObject(…, 50)`). On timeout, run one search cycle.
2. **Socket A:** UDP, `SO_BROADCAST=1`, not bound. It sends one 67-byte datagram to **255.255.255.255:33334**.
   - Plaintext: `[k][0x15][char service[65]]`, with service `"EEService"`.
   - The address is the limited broadcast whenever the argument is 0, which is the case for every caller.
3. **Socket B:** UDP, bound to **INADDR_ANY:33334**, `FIONBIO` non-blocking.
   - It polls `recvfrom` (512-byte buffer) with `Sleep(1)` for **1000 ms**.
   - Replies of exactly 136 bytes with byte 1 = `0x16` are kept, de-duplicated by source IP.
4. The detected-game list is replaced at the end of each cycle. The UI reads it with `NPIMGetDetectedGame` / `NPIMGetDetectedGameCount`.
   - Entry layout (84 bytes): `{sockaddr tcp (unused, zero), sockaddr udp (reply source), UString ip, UString service, UWideString game}`.
5. Cycle period ≈ 1.05 s plus processing.
6. **Consequence:** a client and a host on the **same machine** cannot both own UDP 33334. No `SO_REUSEADDR` is set, so the second bind fails and discovery does not work on one host. Use Join By IP for Mac↔Wine tests on one Mac (PLAN §6.5).

### 3.2 Plugin advertise, legacy path (host side, in `NPIMReceiveFrom`, `TCP Protocol.dll+0x264c`) [static]
The host's game socket (UDP *:33334) receives the 67-byte search. It answers only when all of these hold:
- the service string equals its own (`strcmp`);
- `AllowNewConnections` is true;
- the game is not *private*;
- the game is *advertised*.

The answer:
- is sent from a fresh unbound UDP socket;
- is a **136-byte** datagram to **search-source-IP:33334** (port 33334, *not* the source port);
- plaintext `[k][0x16][char service[65]][char game_name[69]]`.

Host modes (EXE+`0x2bb8`): mode 0 = normal advertised LAN host; mode 4 = published but **not advertised** (join by IP only).

Before publishing, a non-private host runs one 1-s search. It refuses to host if a game with the same name already answers ("A game with that name already exists!").

### 3.3 Join (client) [static]
- **Normal LAN flow.** The lobby hand-off (EXE+`0x27e310`) calls `Connect` with the host IP string from WONAPI and an empty detected list. That takes the direct `gethostbyname` path below, exactly like Join By IP.
- `NESynchronousClient::Connect` → `NPIMConnect(ip, service, game, mode=2 /*UDP*/, …)`:
  - If the detected list is non-empty, the target must match an entry (ip, service, game); its address is used with the port forced to 33334.
  - If the list is empty (Join By IP clears it with `SearchForGame(false)`), the target is resolved with `gethostbyname(str)`. If that fails, it falls back to `inet_addr` + `gethostbyaddr`.
- The client socket is UDP, bound to `INADDR_ANY:0` (ephemeral), with `WSAEventSelect(FD_READ)`. It is never `connect()`ed.
- The client accepts only datagrams whose source equals `host:33334`.
- **Handshake** (UDP, every message RC4-wrapped):

| # | Dir | Message | Contents |
|---|---|---|---|
| 1 | C→H | `0x22 NEMClientConnectionRequest` | u8 behindFirewall, UWS **version string** (built at EXE+`0x137fe5`; includes `%04d` = data checksum & 0x1fff), UWS player name, u32 **data checksum** (§8) |
| 2 | H→C | `0x23 …ConnectionRequestResponse` | u32 result, u8, u8 **send-to slot** (the client's slot), u32 current CT, UWS (host version text for "To join this game you need version %s") |
| 3 | both | lobby: `0x6a EEMPlayerSlotInfo`, `0x5b EEMFullPlayerInformation`, `0x60 EEMGameOptions` / `0xa9 EEMFullGameOptions`, `0x5a EEMChat`, `0x29/0x2a` connection checks every 3 s | [inferred order] |
| 4 | H→C | `0x5d EEMStartLoading`, then `0x5e …Response`, `0x94 EEMLoadPercent` | [inferred] |
| 5 | H→C | `0x2d NEMStartGame` / C→H `0x2e` | then CTs (§5) |

- **Host acceptance rule** (`NPIMReceiveFrom`): a datagram from an unknown address becomes a new client only if:
  - it is at least 4 bytes;
  - byte 1 is `0x22` (connect) or `0x37` (reconnect);
  - new connections are allowed;
  - the source port is non-zero.
  The host identifies clients purely by source `ip:port`, and overwrites the wire originating slot with its own index.
- **Retries and timeouts:**
  - The client re-sends the connection request after 10 s, then every 5 s, up to `MaxNumConnectionTrys`.
  - The client gives up on a silent host after **75 s** (engine+`0x52d3f`).
  - The host's SendToAllAndWait resends every 3000 ms, up to 40 times, then calls `BootAllLaggers`.

### 3.4 Network adapter selection [static]
- **Options list:** `NPIMEnumerateNetworkAdapters` (`TCP Protocol.dll+0x1ce0`) calls `GetAdaptersInfo` with a **fixed 6400-byte buffer**, which fits 10 `IP_ADAPTER_INFO` records.
  - The list shows the first ≤10 adapters' `Description` strings.
  - On `ERROR_BUFFER_OVERFLOW` (more than 10 adapters, for example Hyper-V or VPN-heavy PCs) the list is **empty**.
- **Registry `Network Adapter`** (options+`0x578`, default 0) is a 1-based index into that list. `TCP Protocol.dll+0x1bee`:
  1. walks to that adapter;
  2. takes its first `IpAddressList` string;
  3. looks it up in `gethostbyname(gethostname())->h_addr_list`;
  4. stores the **index in the hostent list** (0 if not found).
- That index is used only by:
  - `NPIMGetLocalAddress`: the "Show my IP address..." display. With no hostent address it falls back to UDP `connect(128.127.126.125:7)` + `getsockname`.
  - `NPIMSendSingle`: an optional post-game stats datagram.
  - an unused search variant.
- **Every socket binds `INADDR_ANY`**, so the adapter choice does not steer traffic.
- For HLE, `gethostbyname(gethostname())` must list the same IPv4 addresses that `GetAdaptersInfo` reports, so the index lookup succeeds.

## 4. Transport framing

| Channel | Socket | Framing | Encryption |
|---|---|---|---|
| LAN lobby (EXE WONAPI, wsock32) | UDP *:**33336** (`LobbyPort`), polled; **8585** (`LanBroadcastPort`) | not decoded yet | unknown |
| Lobby file transfer (EXE) | `EEFileTransferPort` **33335** | not decoded yet; same port number as the plugin's xfer listener | unknown |
| Plugin discovery (legacy path) | UDP *:33334 ↔ broadcast / unicast | fixed 67 / 136-byte datagrams | RC4 per datagram |
| Game | host UDP *:33334 ↔ client UDP *:ephemeral | 1 datagram = 1 NE message (≤ 576 B: the client receive buffer is 0x240, the host's 0x200; CTs are capped at 0x23f) | RC4 per datagram |
| File xfer | host TCP listen *:33335 (backlog `SOMAXCONN`) ← client `connect` | stream of fixed **1033-byte** `NEMXferMessage` records: `[hdr4][u8 id: 0 start, 1 data, 2 end, 3 abort][u32 size][1024 data]` | none |
| (unused) | `NPIMConnect` mode 1 = TCP game connection | `connect` and then `bind` fails on Windows | — |

- **RC4 details** [static + emulated]:
  - **Table generation** (`TCP Protocol.dll+0x1e73`, run at `NPIMActivate`):
    - `URandom` (engine+`0x2117`) with seed `0xffffffff`;
    - `s = (0x343fd·s + 0x269ec3) mod 2³¹`;
    - `T[i] = trunc(s/2³¹ · 255.0)` for i < 261.
  - **KSA:** `+0x25bb`, standard, with the key length 7.
  - **PRGA:** `+0x24ba`, standard. It is fresh per datagram.
  - **Per-datagram key index:** `k = trunc(r·254 + 1)`, where `r` comes from a second URandom seeded with `TMGetTime()`.
  - SHA-256 of `T` is `df87db49…4ddf` (`eelan.py keytable`).
  - Executing the DLL's own loop and RC4 under Unicorn (with the engine's URandom) reproduced `T` exactly and matched 200 random encryptions.
- **Reliability.** UDP has none of its own [static]:
  - CTs are acknowledged by CT responses.
  - The host's message manager (engine+`0x4feae`, `0x4f786`, `0x4fe4d`) resends unacknowledged "send to all and wait" messages.
  - A client that sees a repeated CT resends its cached CT response, with the same inputs.
  - Guaranteed inputs ride inside CT responses (§5.2).
- **Quirk.** `NPIMSendTo` encrypts **in place**. `NESynchronousServer::BroadcastMessage` (engine+`0x5402c`) exports once and sends the same buffer to every slot, so slots after the first receive a double-encrypted datagram [static]. The normal path (engine+`0x4feae`) re-exports per slot. Our port runs the same code, so the behaviour is preserved; it is only noted here for decoding anomalies.

## 5. Messages

Header: `[0]` key index / 0, `[1]` type, `[2]` for-slot (`0xff` = all), `[3]` originating slot (`0xfe` = host).

### 5.1 Engine messages (NE, engine+`0x4d0cf`) [static]

| Type | Class | Body after the 4-byte header |
|---|---|---|
| 0x20 | NEMAddPlayer | u8 player index, UWS name |
| 0x21 | NEMAddPlayerResponse | — |
| 0x22 | NEMClientConnectionRequest | u8 behindFirewall, UWS version, UWS player name, u32 data checksum |
| 0x23 | NEMClientConnectionRequestResponse | u32 result, u8 ?, u8 send-to slot, u32 current CT, UWS text |
| 0x24 | NEMClientDisconnectionNotification | u8 isExiting |
| 0x25 | …NotificationResponse | u32 |
| **0x26** | **NEMCommunicationsTick** | u32 **CT**, u8 **PTs to perform** (`0xff` = no physics, run the inputs immediately: lobby/load), u8 **PTs per second**, u8 **generate checksum**, u8 **dump the world**, u8 n, n × nested message |
| **0x27** | **NEMCommunicationsTickResponse** | u32 processed-CT counter, u32 **CT** acknowledged, u16 avg PT time (ms), u8 n, n × nested message |
| 0x28 | NEMPhysicsTick | u32 number of inputs that follow it in the sim queue, u8 PTs, u8 gen-checksum, u8 dump; PTs/s is kept in memory but not exported. Local only: the client (EXE+`0xb0580`) turns each CT into one PT message followed by the inputs addressed to it (for-slot `0xff` or its own) |
| 0x29 / 0x2a | NEMPlayerConnectionCheck / Response | u32 host `TMGetTime` stamp (sent every 3 s while pinging) |
| 0x2b | NEMRemovePlayer | u8 player, u8 **isOOS**, u8 gameIsExiting, u8 wasBooted |
| 0x2d / 0x2e | NEMStartGame / Response | u32 |
| 0x2f / 0x30 | NEMStopGame / Response | — |
| 0x31 | NEMWorldDatabaseChecksum | u32 tick, u8 dump (not seen used) |
| **0x32** | **NEMWorldDatabaseChecksumResponse** | u32 **tick**, u32 **checksum** |
| 0x33 / 0x35 | NEMPauseGame / NEMUnPauseGame | u32 id (echoed by the response), u32 aux |
| 0x34 / 0x36 | …Response | u32 id |
| 0x37 | NEMClientReconnectionRequest | u32, u32 |
| 0x38 | …ReconnectionRequestResponse | u32 result, u8 new send-to slot, u32 |
| 0x39 | NEMClientRequestPause | — |
| 0x3a | NEMServerIsAlive | — (sent instead of a CT when `TuneServer` decides the network is too slow) |
| 0x3b | NEMXferMessage | TCP 33335 only, see §4 |
| 0x3c | NEMXferStatus | u8 client slot, u8 percent, u32 file, u8 status |

There is no engine message for type `0x2c`.

### 5.2 Game messages (EEM, EXE)
- **Registration:** EXE+`0xb18a8` registers the EXE factory for types `0x41..0xfd`.
- **Classes:** 100 classes, 0x41..0xaa. Names come from RTTI; export sizes from each vtable's `ExportSize` (+0x1c) [static].
- **Body:** every body starts with u8 player.
- **Full table:** `EEM` in `tools/lan/eelan.py`.
- **Variable-size types:**
  - `0x4e–0x50 SelectUnits` family: 6 + n;
  - `0x5a Chat`, `0x5b FullPlayerInformation`, `0x60 GameOptions`, `0x64 LoadOrSaveGame`, `0x6a PlayerSlotInfo`, `0x7d InGameChat`, `0xa9 FullGameOptions` (each contains a UWS);
  - `0x7e/0x7f CivUpgrades*`: 11 + 2n + UWS;
  - `0x96 SelectTheseUnits`: 9 + 4n.
- **Common in-game types:**
  - selection: `0x48–0x4b`, `0x76`, `0x96`;
  - `0x4c AttackUnit`, `0x4d PathTo`, `0x51 GatherResource`, `0x52 ResearchTechnology`, `0x56 BuildUnit`, `0x59 BuildWall`, `0x5f GameSpeed`, `0x7d InGameChat`;
  - `0xa8 LagStatus`, `0x7a UpdatePingStat`, `0xa3 Resign`.
- **How inputs travel** (engine+`0x52903`):
  - `ProcessLocalUserInput` sends immediately as its own datagram.
  - `ProcessGuaranteedLocalUserInput` queues the input, to ride in the next CT response.
  - The host puts every player's inputs into the next CT, up to the 0x23f-byte cap.
  - Clients throttle themselves to **10 throttled inputs per second**.

## 6. Synchronisation and OOS detection

### 6.1 Tick cadence [static; values to be confirmed by capture]
- **CT timer:**
  - The host `Send` thread fires every `send interval` ms, adjusted at run time by `TuneServer` / `TuneForSlowNetwork` / `TuneForSlowCPU`.
  - The server constructor (EXE+`0xc2536`) passes: 16 players; an initial interval of 350 ms; 60 PTs per CT; and the registry values Step Up 125, Step Down 10, Max/Min PTs per second 125/15, Max CT interval 2250 and PT-rate threshold 30 % (options+`0x558…0x57c`; global options object at EXE VA `0x918ea8`).
  - The initial PTs per second is `(1000/350, integer) × 60 = 120` (engine+`0x53460`).
- **PT rate:** each CT tells clients how many PTs to run and at what PT rate. The sim sets its PT period to 1/PTs-per-second (EXE+`0x1470b0`).
- **Legion single player:** the sim thread ran ~41 PTs/s [measured].
- **Checksum request:** `SendCommunicationsTick(generateChecksum, …)` is called with `true` when `CT ≥ next` (initially 40, then `next += 1`).
  - So **every CT from 40 on asks for a checksum**, as long as PTs > 0, the session is multiplayer and the game is not paused [static: the `+0x180` step is only ever set to 1].
  - Captures must confirm this; otherwise the step is set elsewhere.

### 6.2 What is hashed (EXE+`0x145ebf`, `__thiscall` on the sim world object, args `(char dump, uint flags)`) [static]
- **Return value:** a **plain 32-bit additive sum** (wrapping):
  - Σ over a linked list of world subsystems (`DAT_918318`, virtual `+0x4c(flags)`);
  - + world`+0x80` (total PTs performed);
  - + world`+0x18`;
  - + the count of executed broadcast inputs (`DAT_919b68`);
  - plus optional terms by flag (`0x4`, `0x10`, `0x40`, `0x100` continent manager, `0x200` per-unit IDs).
- **Flags in the network path:** `flags = 1`.
- **`ESChecksum`** (which `windows/frida/sync_hooks.js` hooks) is used for save games and data files, **not** for OOS. That is why legion saw 0 calls [measured + static].
- **Timing:** the checksum is taken when the sim dequeues the `NEMPhysicsTick` for a CT with gen-checksum set, **before** that CT's PTs run (EXE+`0x1470b0`).
- **Tick field:** the response's tick field is the comm object's processed-CT counter (`+0x10`) at that moment.
  - The counter goes up once per CT the client has handled: either when the sim finishes a PT message, or directly for `0xff` CTs.
  - A reconnect response (`0x38`) sets it.
  - So it should equal the CT number when both count from the same origin [inferred; capture item 4].

### 6.3 Exchange and verdict [static]
1. **Clients** send `0x32 {tick, checksum}` to the host with `ProcessLocalUserInput`, as their own datagram.
2. **The host** logs its own value locally (`LogChecksumResponse`, engine+`0x54154`); **the host's value is never on the wire.**
3. **Tally.** When the host has responses from all players for a tick:
   - If they agree, it discards them.
   - If they differ with **2 players**, both are flagged.
   - If they differ with **≥3 players**, the most common value wins; on a tie, the group containing the host's value (slot `0xfe`) wins. Everyone else is flagged.
4. **Dump.** A mismatch sets *dump the world*: the next CT has `dump=1` and `gen=1`. Every machine writes `OOS yyyy.mm.dd - hhh.mmm.sss.log`, a per-player unit/group/air-traffic dump with "OUT OF SYNCH DUMP" lines, and sends no response for that tick.
5. **Removal.** After that CT, the host calls `RemovePlayerFromGameInProgress(slot, booted=1, isOOS=1, …)` for each flagged slot, which broadcasts `0x2b NEMRemovePlayer`.
   - Clients see "Player '%s' is out-of-synch and has been removed…", or "You are out-of-synch with the game and have been dropped".
   - If the host itself loses, host migration follows ("Migrated Server").
   - "The game is out-of-synch. Log has been written. Your checksum is %u." comes from the same path.

### 6.4 "Debug OOS Handling"
- **Where it lives:** registry → options+`0x5be` (EXE ctor default 0; the Wine/legion registry has 1).
- **Readers:** a displacement and absolute-address search over the whole EXE found reads only in options load / save / copy and the options dialog (EXE+`0x1c7e4a` → `UICheckBox::SetChecked`) [static].
- **Conclusion:** it does not change the OOS path in this build [inferred]. A hardware watchpoint on VA `0x919466` during a LAN game would settle it.

### 6.5 Oracle plan
- **Wire:** `eelan.py export` gives `checksums.csv` / `checksums.json` (client values per tick) and `ticks.csv` (CT stream with flags and inputs).
- **Host:** `windows/frida/netcode_hooks.js` records `world` (every EXE+`0x145ebf` call with its tick, on every machine), `logged` (the host's tally) and `removed`.
- **Comparison:** Mac vs Windows streams compare by `tick`. The first differing tick bounds the divergence to the PTs of the previous CT.

## 7. What the Winsock / iphlpapi HLE must do (ADR-001 gap 12)

**Scope** (corrected):
- The game session uses `TCP Protocol.dll`'s 24 **ws2_32** imports plus `GetAdaptersInfo` [static].
- **The LAN lobby uses the EXE's 28 wsock32 imports, so they must be functional** (EXE `0x6aa000–0x6b2000`, `0x801f90`; generic WONAPI socket wrappers such as bind at EXE+`0x2ab370`, sendto at `+0x2abde0` and recvfrom at `+0x2abf20`).
  - Imports include `select`/`__WSAFDIsSet`, `connect`, `listen`, `accept`, `shutdown`, `getsockopt`.
  - Observed at the Multiplayer screen [measured, Wine]:
    - `WSAStartup(1.1)`;
    - `gethostname` plus `gethostbyname(own name)` ×3, to find the local IP;
    - `setsockopt(SO_BROADCAST)` on `INVALID_SOCKET`, which must fail cleanly with `WSAENOTSOCK`;
    - a UDP socket with `SO_BROADCAST` and `FIONBIO`, bound to `0.0.0.0:33336`, then non-blocking `recvfrom` polling every ~20 ms from two threads;
    - `gethostbyname("empireearth.m1.sierra.com")` from a worker thread. It must fail fast: return no host, never wait on DNS.
  - The `GetAdaptersInfo` / `GetIpForwardTable` / `GetAdaptersAddresses` calls in the trace are probably Wine's own implementation of `gethostbyname(<own host>)` rather than game calls [inferred]. The EXE also loads `GetAdaptersInfo` dynamically (EXE+`0x2add00`).

Checklist:

- [ ] **Start-up.**
  - `WSAStartup(MAKEWORD(2,0))`: the caller *checks* that `wVersion == 2.0`.
  - Refcounted `WSACleanup`. The DLL calls Cleanup+Startup in the middle of a session (`+0x2269`), so sockets must survive it.
- [ ] **Sockets.**
  - `socket(AF_INET, SOCK_DGRAM|SOCK_STREAM, 0)` and `closesocket`.
  - SOCKET handles are small integers, distinct from HANDLEs.
- [ ] **Options and modes.**
  - `setsockopt(SOL_SOCKET=0xffff, SO_BROADCAST=0x20, 1)`.
  - `ioctlsocket(FIONBIO=0x8004667e, &1)`.
  - No `SO_REUSEADDR`, and none should be added: a second bind of UDP 33334 must fail with `WSAEADDRINUSE`, as on Windows (§3.1).
- [ ] **Bind and listen.**
  - `bind` to `INADDR_ANY` with ports 33334, 33335 or 0.
  - `getsockname` for the ephemeral port.
  - `listen(s, 0x7fffffff)` mapped to `SOMAXCONN`.
- [ ] **Non-blocking I/O** with `WSAEWOULDBLOCK (10035)`:
  - `recvfrom`, `recv`, `accept`, `send` (TCP: partial sends and a `WOULDBLOCK` retry loop with `Sleep(1)`);
  - `recv(…, 1, MSG_PEEK)` for `IsAlive`;
  - a blocking `connect` for TCP xfer, *before* `WSAEventSelect`.
- [ ] **Datagram sizes.**
  - The host receives into **512 bytes** and clients into 576.
  - A larger datagram must fail with `WSAEMSGSIZE` (Windows truncates and errors) and must not be silently delivered.
- [ ] **`WSAEventSelect`.** This is the critical piece:
  - Network events signal a Win32 event that engine threads wait on with `WaitForMultipleObjects` (up to 11 handles mixing socket and plain events) or `WaitForSingleObject`.
  - Masks used: `FD_READ (1)` and `FD_ACCEPT (8)`. A second `WSAEventSelect` on the same socket **replaces** the first: the xfer listener ends up with FD_ACCEPT only.
  - It sets the socket non-blocking.
  - **Re-enabling semantics:** the host thread calls `ResetEvent`, then reads **one** datagram per wake-up (engine+`0x54652`). The event must be set again *after each `recvfrom` while more data is queued* (Winsock's FD_READ re-enable). Otherwise queued datagrams stall until the next arrival. The same applies to FD_ACCEPT after `accept`.
  - Implement with a kqueue thread that sets the guest event on readability, plus a re-arm check in `recv`/`recvfrom`/`accept`.
- [ ] **Broadcast.**
  - `sendto(255.255.255.255:33334)` from an unbound socket.
  - macOS sends limited broadcast on **one** interface only (the default route), so it never reaches a ZeroTier `feth*` or a second NIC.
  - **Fan out:** for each up, broadcast-capable IPv4 interface (or only the one selected in *Network Adapter Options*), send to its directed broadcast (e.g. `10.147.17.255`), or use `IP_BOUND_IF` with `255.255.255.255`. Receiving needs nothing special (bound to `INADDR_ANY`).
- [ ] **Resolution.**
  - `gethostname`.
  - `gethostbyname`: must accept dotted quads without DNS (Join By IP) and return the host's own IPv4 list in `GetAdaptersInfo` order.
  - `gethostbyaddr` (fallback only).
  - `inet_addr`, `inet_ntoa` (a static buffer per thread is fine), `htons`/`htonl`.
  - `WSAGetLastError` with the Windows codes, especially 10035, 10048, 10040 and 10054. On Windows a UDP ICMP port-unreachable surfaces as `WSAECONNRESET` on `recvfrom`; the engine treats any error other than 10035 as an error return, so we should *not* surface ECONNREFUSED for UDP [inferred].
- [ ] **`GetAdaptersInfo`.**
  - Linked `IP_ADAPTER_INFO` (640 bytes each).
  - Honour the caller's 6400-byte buffer: return ≤ 10 entries and prefer the interfaces with IPv4 addresses.
  - `Description` should be readable ("ZeroTier feth0 10.147.17.20").
  - `IpAddressList.IpAddress.String` must be set.
- [ ] **kernel32.** `WaitForSingleObject`, `Sleep` and `GetLastError` are used directly by the plugin. The engine threads use `WaitForMultipleObjects` on the same events.
- [ ] **wsock32 1.1 for the lobby.**
  - Same socket semantics as above.
  - `select` / `__WSAFDIsSet` (WONAPI waits).
  - Broadcast `sendto` with the same per-interface fan-out, presumably to 8585/33336.
  - Two instances on one host cannot share UDP 33336 either.
- [ ] **Firewall.** The macOS app needs the inbound prompt for **UDP 8585, UDP 33336, UDP 33334 and TCP 33335**. Every peer, not only the host, receives lobby broadcasts and the 33334 game traffic.

## 8. Version / data checksum (must match across Mac and Windows)

**Computation** (EXE+`0x137fe5` at start-up, global VA `0x919868`) [static]:
- Σ of all **bytes** (32-bit wrapping; EXE+`0x134814` with mode 0) of:
  - `Low-Level Engine.dll`, `sxlrt233.dll`, `TCP Protocol.dll`;
  - every `Data\Db\*.dat`, `Data\Unit AI Scripts\*.tai` and `Data\Models\*.cem` found through the engine FS layer (archives included);
- plus each archive's `Timestamp` tag, parsed with `sscanf`.

**Where it is used:**
- The value goes into every connection request.
- `& 0x1fff` is appended as `%04d` to the version string shown in the menu.

**Port requirements:**
- The FS layer must still find the original `Low-Level Engine.dll`, `sxlrt233.dll` and `TCP Protocol.dll` in the game directory, unmodified.
- The importer must not rebase them on disk, and the translated bundles must not replace them in place.
- Compare the menu's 4-digit suffix on Mac and Windows as a cheap check.

## 9. ZeroTier and VPN notes

- **ZeroTier** (and Hamachi) carry Ethernet broadcast, so discovery works if the broadcast actually leaves through the ZT interface. In the normal flow that is the **lobby's** broadcast (8585/33336); the plugin's 33334 search is the legacy path:
  - **Windows** sends `255.255.255.255` out of the interface with the best route metric. A ZeroTier user whose games do not show up needs the ZT adapter's metric lowered, or a broadcast relay [inferred; the classic WinIPBroadcast issue].
  - **Mac port:** our HLE fans out (§7).
  - Both sides need inbound UDP 8585, 33336 and 33334 (and TCP 33335) allowed. On Windows that means the ZT network must be *Private*, or there must be a rule. legion §7's proposed rules cover only 33334–33336, so **add UDP 8585**.
- **Tailscale** (no broadcast): the lobby's LAN game list will not see remote games. Use Join By IP (the lobby's `DirectConnectDialog`), whose hand-off is the direct UDP 33334 path. MagicDNS names also work, because the string goes through `gethostbyname`.
- **Per-packet fit:** game datagrams are ≤ 576 B, well under the ZT/Tailscale MTU. The xfer TCP stream is ordinary TCP.
- **Adapter selection** does not need to point at the VPN for traffic to flow (§3.4). It only changes the displayed IP. Our `GetAdaptersInfo` should still list the tunnel, so that "Show my IP address" is useful for Join By IP.

## 10. Captures: how to analyse

```sh
python -I tools/lan/eelan.py summary  analysis/lan-captures/*.pcapng
python -I tools/lan/eelan.py timeline analysis/lan-captures/lan.pcapng --no-ticks
python -I tools/lan/eelan.py export   analysis/lan-captures/*.pcapng --out-dir analysis/lan-captures/decoded
```

**First captures must confirm or correct:**
1. Every UDP datagram decrypts to a known type with `size_mismatch` absent. This is the end-to-end check of §4 on real traffic.
2. Lobby traffic on 8585/33336: who broadcasts what, at what interval, and which side listens on 8585. `eelan.py` shows it raw as `LobbyDatagram`. Then: does any plugin `0x15`/`0x16` traffic on 33334 appear in a normal lobby→game flow? (Expected: none.)
3. The CT rate, PTs per CT and PTs/s under `TuneServer`, and whether *every* CT ≥ 40 has `generate_checksum=1`.
4. That `0x32` tick numbers equal the CT numbers they answer, and how far they lag.
5. The handshake order (§3.3 row 3–5), and the meaning of the unknown fields (`0x23` u8, `0x27` first u32, `0x33/0x35` aux).
6. The connection-request `data_checksum` from the Wine Mac build vs legion. They must be equal for the game to be joinable.

## 11. Open questions

- **The LAN lobby protocol on 8585/33336** (WONAPI `LanLogic`): its format, which port is the broadcast target and which the listener, how a joiner learns the host IP, and how the lobby uses 33335. It needs static work on `LanLogic.cpp` (EXE RVA around `0x3cb220`) and a capture. Community-re's source (empire-earth-web) is unverified here.
- Which `EEServer` construction site the lobby host uses, and so whether it is private (no 33334 search or advert at game start).
- Whether the lobby's 33335 file transfer and the plugin's TCP 33335 listener can be live at the same time (a bind conflict) or are always sequential.

- The exact host-side handling of the data-checksum / version mismatch: the EXE subclass's `0x22` handler, engine vtable `+0x6c`.
- Whether the `+0x180` checksum step is ever changed at run time. The capture answers this.
- How `TuneServer` / `TuneForSlowNetwork` / `TuneForSlowCPU` move the interval and the PT numbers away from the initial 350 ms / 60 PTs per CT / 120 PTs/s, within the registry bounds. The capture's `ticks.csv` answers this.
- Host migration message flow (`0x37`/`0x38`, "Migrated Server").
- Whether any real path uses `BroadcastMessage` with more than one recipient (the in-place encryption quirk).
- `Debug OOS Handling`: confirm "no effect" with a watchpoint.
