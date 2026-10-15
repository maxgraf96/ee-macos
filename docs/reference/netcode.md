# Empire Earth LAN netcode (GOG 2.00 base game)

Status: Phase 1 reference, 2026-10-14. Revised against real LAN captures (session 2). Owner: netcode agent. Tooling: [`tools/lan/`](../../tools/lan/README.md).

## Sources and labels

- **Sources:**
  - Static analysis of the canonical GOG modules (PLAN §2.4):
    - `TCP Protocol.dll`: a Ghidra 12.1.4 project under `analysis/netcode/`, with all 148 functions read.
    - `Low-Level Engine.dll` and `Empire Earth.exe`: the Phase 0 spike exports.
  - A Wine `+winsock` trace of the Multiplayer screen.
  - **Session-2 LAN captures** (`analysis/lan-captures/session2/`):
    - `legion` (Windows 11, 192.168.1.132) and `EE-VM` (a bridged VM on legion, 192.168.1.192): pktmon pcapng on both.
    - Frida `netcode_hooks` on the VM host.
    - One complete 2-player random-map game (VM hosts, legion joins; 896 CTs, ~5 min of play), plus three games hosted by legion that crashed at start (§3.4).
- **Labels:**
  - **[static]** = read from code.
  - **[emulated]** = original x86 run under Unicorn and compared with our reimplementation.
  - **[captured]** = seen in the session-2 captures.
  - **[measured]** = legion or Wine runtime trace.
  - **[inferred]** = a reasoned guess.
- **Addresses** are `module+RVA`. The EXE is fixed at `0x400000`, so EXE VA = RVA + `0x400000`. The DLLs prefer `0x10000000`.

## 1. Summary

- **Two layers, four ports** [captured]:

| Port | Proto | Owner | Use |
|---|---|---|---|
| **8585** | UDP broadcast | EXE WONAPI `LanLogic` (wsock32) | LAN game list: query / advert / keep-alive / closed. Every lobby binds 8585 and sends from it |
| **33336** | **TCP** | EXE `LanLogic` | Lobby room on the host: join, roster, chat, version check, slots / ready, **StartGame** |
| **33334** | UDP | `TCP Protocol.dll` (ws2_32) | Every in-game message (NE / EEM), RC4-obfuscated. The host also sends one `EEService` search here before publishing |
| **33335** | TCP | `TCP Protocol.dll` (and the lobby's `EEFileTransferPort`) | In-game file transfer. The client connects at game start; no data was sent in a random-map game |

  - Ports come from GOG's `WONLobby.cfg`: `LanBroadcastPort 8585`, `LobbyPort 33336`, `EEFileTransferPort 33335`. The EXE defaults are 8585, 8888 and 8889 (EXE+`0x343350`, `+0x27b77b`).
  - The Wine trace also showed a UDP socket bound to 33336. No UDP 33336 packet appeared in the captures.
- **Obfuscation** [static + emulated + captured]:
  - Every 33334 datagram is `[k][RC4(T[k−1..k+5]) of the rest]`. `T` is a fixed 261-byte table derived from an LCG; `k` is 1..254, and `k = 0` would mean clear text.
  - All 2,687 game datagrams in both captures, and every nested message, decode with no size mismatch.
- **Normal flow** [captured]:
  1. The 8585 game list leads to a TCP 33336 lobby room.
  2. The host sends `StartGame` over 33336.
  3. The client sends `0x22` to `host:33334`, 8 ms later, and opens TCP 33335.
  4. CTs follow on UDP 33334.
  - The plugin's own discovery appears only as the host's single 1-s duplicate-name search; nobody answers it.
- **Lockstep** [captured]:
  - One CT every **350 ms**, constant for the whole game: median 350.0 ms, p5–p95 349–351 ms.
  - The CT carries **15 PTs** at a PT/s field of **44**.
  - CTs 0–11 are lobby/load ticks (PTs `0xff`, PT/s 120).
- **OOS check** [captured + Frida]:
  - From **CT 40**, every CT with PTs > 0 asks for a world checksum (CT 40–857, except one forced CT).
  - The client's `0x32` response carries **tick = CT** and arrives 0–42 ms later.
  - The host's own world checksum (EXE+`0x145ebf`, Frida) equals the client's wire value for **817/817 ticks**.
- **Version gate** [captured]:
  - Both peers sent data checksum `0x0e998b9e` and version string `v2.00.2974`; 2974 = checksum & 0x1fff.
  - The lobby checks the same pair *before* start (TCP 33336 `VersionCheck`), and the 33334 connection request repeats it.
- **Network Adapter** (corrected):
  - It is **not** cosmetic. The lobby's adapter choice sets which interface the 8585 broadcasts leave from.
  - It also becomes the plugin's adapter index, and **any index ≥ 2 crashes `TCP Protocol.dll`**: it frees an interior heap pointer (§3.4).
- **"Debug OOS Handling"** has no reader outside option load/save/copy and an options checkbox [static] (§6.4).

## 2. Architecture

```
EXE WONAPI WONLobby (LanLogic, EELobby*; sockets EXE 0x6aa000-0x6b2000; wsock32, WSAStartup 1.1)
     UDP 8585 game list, TCP 33336 lobby room
     hand-off: host EXE+0x27d797 -> EEServer("TestGame", adapterIdx, private=0, advertise=0)
               client EXE+0x27e310 -> EEClient, Connect(hostIP, "EEService", game)
EXE game (EEM* messages, sim)                          EXE+0xb18a8 registers EEM types 0x41..0xfd
  |  EEServer / EEClient = NESynchronousServer / Client subclasses (EXE+0xc2536 / +0xafdbb)
Low-Level Engine.dll  "Network Engine"
  |  NEMessage (+29 NEM* types 0x20..0x3c), NEMPlayerInput (input lists)
  |  NESynchronousServer: threads Main / Send / Xfer     NESynchronousClient: Main / Receive
  |  NEProtocol: GetProcAddress of 30 NPIM* entry points (engine+0x509af)
TCP Protocol.dll  ("TCP/IP Protocol", "WinSock 2.0"; plugin type 'ntwk')
     ws2_32 (24 imports), iphlpapi!GetAdaptersInfo, kernel32 WaitForSingleObject/Sleep
```

- **Threads** [static; legion measured three network threads, single player included]:
  - **Server:**
    - `Main` (engine+`0x5581d`): waits on 11 events, receives, runs SendToAllAndWait retries.
    - `Send` (`+0x55d2e`): CTs on a timer.
    - `Xfer` (`+0x55f09`).
  - **Client:**
    - `Main` (`+0x526f0`): connect retries, message hand-off.
    - `Receive` (`+0x52d3f`): drains the socket; 75 s server-silence timeout.
  - **Plugin searcher thread** `TCP Protocol.dll+0x32c4`: legacy path only (§3.5).
  - All threads set PC=53 (`UMSetFloatingPointPrecision(0x10000)`) and RC = nearest.
- **Single player** creates a private local server named `"EEName"`, which opens UDP 33334 and TCP 33335. That is why the firewall prompts on the first random map [measured].
- **Message header:** `[byte0][type][forSlot][origSlot]`.
  - Byte 0 is overwritten with the RC4 key index (engine+`0x4d025`).
  - Receivers dispatch on byte 1 through `NEMessage::sConstructors[256]`.
- **Factories:** engine+`0x4d0cf` (NE) and EXE+`0xb1b31` (EEM).
- **Strings:**
  - NE/EEM `UWideString` = `u32 n (=strlen+2)` + MBCS bytes + 2 NULs.
  - The lobby (8585/33336) uses UTF-16LE with a u16 character count.

## 3. Discovery and session setup

### 3.1 LAN game list: UDP 8585 [captured; field names inferred from captures]
Every lobby binds UDP 8585 and broadcasts from it to `255.255.255.255:8585`. One reply was unicast to `requester:8585`. Byte 0 is the type:

| Type | Name | Body | When |
|---|---|---|---|
| `0x04` | LanQuery | — | Entering or refreshing the game list. Clicking refresh sends several within 1 s |
| `0x01` | LanGameAdvert | u16 lobby TCP port (33336), u16 `0x8b49` (constant on all machines), u8 in-progress, u16 n + UTF-16 game name + u16 0, u16 players, u16 max players (8; 7 with a computer player), u16 **starting epoch** (14 Random, 0 Prehistoric; the list's column), u8 0 | On game creation, on every roster change, on start (in-progress = 1), and as a unicast reply to a query |
| `0x02` | LanGameKeepAlive | u16 lobby port | Every **15.0 s** while hosting |
| `0x03` | LanGameClosed | u16 lobby port | When the host leaves or dissolves the game |

**Interface choice** [captured]:
- While legion's lobby used its default adapter, its broadcasts left only from **192.168.153.1**, the VMware host-only adapter, so the VM never saw legion's games.
- After the user picked Wi-Fi in *Network Adapter Options*, they left from 192.168.1.132.
- So the lobby sends on one interface: the selected adapter's, or the route-preferred one when none is selected [inferred: the socket is bound to the chosen local IP].
- On Windows this is the classic "games not visible over VPN / with VMware adapters" trap.

### 3.2 Lobby room: TCP 33336 on the host [captured]
**Framing:** records `[u16 total length, LE, includes itself][u8 type][body]`. Joiner → host:

| Dir | Type | Name | Body |
|---|---|---|---|
| C→H | `0x01` | LobbyJoin | u16 n, UTF-16 player name, u16 0 |
| H→C | `0x02` | LobbyRoster | u16 status (0), u16 host id (1), u16 your id (2), u16 count, count × {u16 id, u16 n, UTF-16 name} |
| C→H | `0x05` | LobbyChat | u16 flags (0), u16 n, UTF-16 text, u16 0 |
| H→C | `0x06` | LobbyChatRelay | u16 flags, u16 sender id, u16 n, UTF-16 text, u16 0 |
| C→H | `0x07` | LobbyKeepAlive | — : 240 s after the join, then about every 120 s, in the lobby and during the game (not a leave: [lansession.md](lansession.md) §7) |
| both | `0x08` | LobbyGameData | u8 sub + EE-specific body, below |

`0x08` subtypes (EE lobby code):

| Sub | Dir | Name | Body |
|---|---|---|---|
| `0x01` | C→H | VersionCheck | u16 `0xfffe`, u16 n, UTF-16 version (`v2.00.2974`), **u32 data checksum** (§8) |
| `0x02` | H→C | GameSetup | 82 bytes of game settings + slot table. Body offsets [inferred from three hosts' settings]: 19 resources (0 Tournament-Low, 4 Deathmatch), 20 starting epoch (0, 14 Random), 21 ending epoch (13 Nano), 29 Use Custom Civs, 45 slot 3's type (1 Computer) |
| `0x03` | H→C | SlotAssign | `0200 feff 0001` (client id 2, …) |
| `0x04` / `0x05` | C→H / H→C | SetReady / ReadyState | u8 ready / u16 client id, u8 ready |
| `0x66` / `0x65` | C→H / H→C | SetSlotAttr / SlotAttr | u8 attr, u8 value / u8 slot, u8 attr, u8 value (attr 1, values 0–2: colour/team/civ, not identified) |
| **`0x07`** | H→C | **StartGame** | — : the hand-off to 33334 |
| `0x68` / `0x69` | C→H / H→C | LaunchAck / reply | `01 01` |

**Hand-off sequence** [captured; complete game at t = 1036–1037 s]:
1. Host (EXE+`0x27d797`): 1-s plugin `EEService` search on UDP 33334 (duplicate-name check; no reply), then `NPIMPublish`.
2. ~1 s later: host broadcasts LanGameAdvert with in-progress = 1, and sends `0x08/0x07 StartGame` on TCP 33336.
3. +8 ms: client sends UDP `0x22` to `host:33334`. The host replies `0x23` at once (result 1, slot 0). The client opens TCP 33335 (SYN/SYN-ACK; no payload), and sends LaunchAck on 33336.
4. CTs follow (§5–6).

### 3.3 Game session handshake, UDP 33334 [captured]

| CT / step | Dir | Message |
|---|---|---|
| — | C→H | `0x22` ConnectionRequest: behindFirewall 0, version `v2.00.2974`, player name, data checksum `0x0e998b9e` |
| — | H→C | `0x23` (top-level): result **1**, u8 1, send-to slot **0**, current CT 0, text `v2.00.2974` |
| CT 0–1 | H→all | nested `EEMFullGameOptions`, `EEMFullPlayerInformation` (CT 0–3), and `0x23` for slot `0xfe` (CT 1) |
| CT 3 | H→all | nested `0x23` for slot 0 (result **0**, u8 1), `EEMStartLoading` |
| CT 6–8 | C→H | `EEMLoadPercent` ×6 (top-level), `EEMStartLoadingResponse` (relayed in CT 8) |
| CT 9–10 | both | `EEMLoadMainScreen` / `…Response`, `EEMPlayerSlotInfo` |
| CT 11 | H→all | nested `NEMStartGame` (value 1707214) |
| CT 12+ | | PTs = 15 per CT. The sim runs |
| CT 857 | C→H | `EEMResign` |
| CT 858 | H→C | `NEMPauseGame` (id 858, aux 858) → `0x34` response. CTs 858–895 carry PTs = 0 |
| CT 895 | H→all | nested `NEMRemovePlayer` (player `0xfe`, gameIsExiting 1) |

- **Client input path:** client inputs went as **standalone datagrams** (`ProcessLocalUserInput`). CT responses carried **no** nested inputs in this session; the guaranteed path was not exercised.
- **Retries and timeouts** [static]:
  - Connection request retries at 10 s, then every 5 s.
  - Client silence timeout 75 s.
  - Host SendToAllAndWait: 3000 ms × 40, then `BootAllLaggers`.
- **Host acceptance rule** (`NPIMReceiveFrom`): an unknown source becomes a client only if:
  - byte 1 is `0x22` or `0x37`;
  - it is ≥ 4 bytes;
  - new connections are allowed;
  - the port is ≠ 0.

  Clients are identified by `ip:port`. The host overwrites the wire originating slot with its own index.

### 3.4 Network Adapter selection and the host crash [static + captured]
**Where the index comes from.** At the hand-off both sides call EXE+`0x27db5c`. It sets `Network Adapter` (options+`0x578`, global VA `0x919420`) to the **1-based position** of the lobby's chosen local IP in WONAPI's adapter map (EXE+`0x2add00`). That map is built from a correctly sized `GetAdaptersInfo`; its order is WONAPI's, not necessarily the OS order. If no adapter was chosen, the index is 0. The index is passed into `EEServer`/`EEClient` and on into `NPIMPublish`/`NPIMConnect`/`NPIMGetLocalAddress`.

**The bug.** `TCP Protocol.dll+0x1bee` maps the index to a hostent address:
1. `buf = malloc(0x1900)`; `GetAdaptersInfo(buf, 6400)`.
2. It walks `buf = buf->Next` while `i < index`, **overwriting the only copy of the allocation pointer** (`[ebp-4]`).
3. It uses that adapter's first `IpAddress.String`.
4. It ends with `free(buf)` (call at `+0x1cd0`, return address **`+0x1cd8`**, through the delete wrapper `+0x4840`, return `+0x484c`).

**What happens per index:**
- **Index 0:** returns before allocating.
- **Index 1:** the loop exits at once, so the original pointer is freed. Safe.
- **Index ≥ 2, adapter exists:** `free()` gets an **interior pointer** (`buf + 640·k`). That corrupts the msvcrt/NT heap, which crashes with an access violation in ntdll. This matches legion's 3/3 host crashes at match start: VMware adapters made Wi-Fi a later entry. [The `+0x7370` frame in the crash report is a bogus `.data` address.]
- **Index ≥ 2, beyond the list:** `buf` becomes NULL; free(NULL) leaks but does not crash.

**When it fires.** The same routine runs on the client side (`NPIMConnect`) and for the IP display, so a joiner that picks adapter ≥ 2 crashes too.

**Other uses of the index** are display only (`NPIMGetLocalAddress`) and an optional stats `NPIMSendSingle`. Game sockets still bind `INADDR_ANY`.

**The other adapter code is safe:**
- `NPIMEnumerateNetworkAdapters` (`+0x1ce0`) has the same walk-then-free shape, but stops at NULL (≤ 10 adapters) or never walks (`ERROR_BUFFER_OVERFLOW` with more than 10). It leaks rather than crashes.
- The lobby's own enumerator (EXE+`0x2add00`) sizes its buffer properly (up to 256,000 bytes).

**Requirements for our port** (also in the §7 checklist):
1. **Make the free harmless.** Either:
   - make HLE `free` / `operator delete` ignore, and log, pointers that are not the start of a live block; or
   - override `TCP Protocol.dll+0x1bee` (PLAN §5.7) to free the original buffer.

   Either one avoids the crash on the Mac whatever the user picks. It is purely local and cannot affect sync.
2. **List ≤ 10 adapters** in `GetAdaptersInfo`, so the plugin's fixed 6,400-byte buffer works.
3. **Order adapters most-useful-first,** so the common choice is index 1 (which also keeps Windows peers out of trouble when they copy our advice):
   1. the primary LAN interface (`en0`/`en1` with IPv4);
   2. VPN/overlay interfaces with IPv4 (`feth*`/`zt*` ZeroTier, `utun*` Tailscale/WireGuard);
   3. other up interfaces with IPv4.

   Omit `lo0`, interfaces without IPv4 and down interfaces (`awdl*`, `llw*`, `bridge*` without address, `anpi*`, `ap*`).
4. **Keep `gethostbyname(gethostname())` consistent:** return the same IPv4 addresses in the same order.

**Advice for Windows friends:** leave Network Adapter at the default, or choose only the **first** listed adapter. With VMware, Hyper-V or VPN adapters, either disable them or raise the LAN adapter's priority (metric) instead of choosing it.

### 3.5 Plugin `EEService` discovery (legacy path) [static; partly captured]
**Search** (`TCP Protocol.dll+0x2ec6`, thread `+0x32c4`):
- An unbound UDP socket with `SO_BROADCAST` sends 67 bytes `[k][0x15]["EEService"…65]` to `255.255.255.255:33334`.
- A socket bound to `*:33334` collects 136-byte `0x16` replies for 1000 ms.
- It repeats about every 1.05 s while the legacy join screen is open.

**Reply** (`NPIMReceiveFrom`):
- Sent only if the service matches, connections are allowed, the game is not private and is advertised.
- 136 bytes `[k][0x16][service 65][game 69]`, from a fresh socket to `searcher-IP:33334`.

**Observed** [captured]:
- Exactly one search per hosted game: the host's duplicate-name check before `NPIMPublish`. The lobby host's `EEServer` has private = 0, so it searches, and advertise = 0, so nobody replies (EXE+`0x27d797`).
- One unexplained client search after a game ended.
- No `0x16` replies at all.

**Legacy UI callers:** `EEUIEMSOpenMultiplayerScreen`, `EEUIEMSJoinGame`, `EEAFindGames`, `EEAJoinOrHostAGame` (EXE+`0x10c0f`, `+0x1021a`, `+0x2091`, `+0x2bb8`).

**Same-host limit:** two instances on one host cannot both bind UDP 33334 or 8585 (no `SO_REUSEADDR`). Mac↔Wine on one Mac must use Join By IP.

## 4. Transport framing

| Channel | Socket | Framing | Encryption |
|---|---|---|---|
| LAN game list | UDP 8585 ↔ 255.255.255.255:8585 / unicast | 1-byte type + body (§3.1) | none |
| Lobby room | host TCP listen *:33336 ← joiner | `[u16 len][u8 type][body]` records (§3.2) | none |
| Game | host UDP *:33334 ↔ client UDP *:ephemeral | 1 datagram = 1 NE message (≤ 576 B: client receive buffer 0x240, host 0x200; CTs capped at 0x23f) | RC4 per datagram |
| Plugin search | UDP → 255.255.255.255:33334 | fixed 67 / 136 bytes | RC4 |
| File xfer | host TCP listen *:33335 ← client `connect` at game start | fixed 1033-byte `NEMXferMessage` records `[hdr4][u8 id 0 start/1 data/2 end/3 abort][u32 size][1024 data]` [static; no payload captured] | none |

- **RC4** [static + emulated + captured]:
  - Table: `URandom` (engine+`0x2117`) with seed `0xffffffff`; `s = (0x343fd·s + 0x269ec3) mod 2³¹`; `T[i] = trunc(s/2³¹·255.0)`, i < 261. SHA-256 `df87db49…4ddf`.
  - KSA `+0x25bb` (key length 7); PRGA `+0x24ba`, fresh per datagram.
  - Key index `k = trunc(r·254+1)`.
  - Unicorn runs of the DLL's own code match. All captured datagrams decode.
- **Reliability** [static + captured]:
  - CTs are acknowledged by CT responses.
  - When a response is missing, the host sends **`NEMServerIsAlive` instead of the next CT** every 350 ms and re-sends the outstanding CT after ~1 s. Captured: CT 147 re-sent after 1051 ms, with two ServerIsAlive in between.
  - The host then sends the next CT immediately; that "forced" CT 148 carried gen-checksum 0.
- **Quirk** [static]: `NPIMSendTo` encrypts in place. `BroadcastMessage` (engine+`0x5402c`) re-sends one buffer to several slots (double-encrypted after the first). Not seen in a 2-player capture.

## 5. Messages

Header: `[0]` key index, `[1]` type, `[2]` for-slot (`0xff` = all), `[3]` originating slot (`0xfe` = host).

### 5.1 Engine messages (NE) [static; ✓ = decoded in captures without size mismatch]

| Type | Class | Body after the 4-byte header |
|---|---|---|
| 0x20 | NEMAddPlayer | u8 player index, UWS name |
| 0x21 | NEMAddPlayerResponse | — |
| 0x22 ✓ | NEMClientConnectionRequest | u8 behindFirewall, UWS version, UWS player name, u32 data checksum |
| 0x23 ✓ | NEMClientConnectionRequestResponse | u32 result (1 = top-level ack, 0 = in-CT confirm), u8 (1), u8 send-to slot, u32 current CT, UWS version |
| 0x24 | NEMClientDisconnectionNotification | u8 isExiting |
| 0x25 | …NotificationResponse | u32 |
| **0x26** ✓ | **NEMCommunicationsTick** | u32 **CT**, u8 **PTs to perform** (`0xff` = lobby/load: run inputs only), u8 **PTs per second**, u8 **generate checksum**, u8 **dump the world**, u8 n, n × nested message |
| **0x27** ✓ | **NEMCommunicationsTickResponse** | u32 processed-CT counter (= CT in 881/896), u32 **CT** acked, u16 avg PT time (always 0 here), u8 n, n × nested |
| 0x28 | NEMPhysicsTick | local only (client EXE+`0xb0580` → sim queue): u32 number of following inputs, u8 PTs, u8 gen-checksum, u8 dump |
| 0x29 / 0x2a | PlayerConnectionCheck / Response | u32 host `TMGetTime` (every 3 s while pinging) |
| 0x2b ✓ | NEMRemovePlayer | u8 player, u8 **isOOS**, u8 gameIsExiting, u8 wasBooted |
| 0x2d ✓ / 0x2e | NEMStartGame / Response | u32 |
| 0x2f / 0x30 | NEMStopGame / Response | — |
| 0x31 | NEMWorldDatabaseChecksum | u32 tick, u8 dump (unused) |
| **0x32** ✓ | **NEMWorldDatabaseChecksumResponse** | u32 **tick (= CT)**, u32 **checksum** |
| 0x33 ✓ / 0x35 | NEMPauseGame / UnPauseGame | u32 id (echoed by the response), u32 aux. Captured: both = CT |
| 0x34 ✓ / 0x36 | …Response | u32 id |
| 0x37 / 0x38 | ClientReconnectionRequest / Response | u32, u32 / u32 result, u8 new slot, u32 |
| 0x39 | NEMClientRequestPause | — |
| 0x3a ✓ | NEMServerIsAlive | — |
| 0x3b | NEMXferMessage | TCP 33335 only |
| 0x3c | NEMXferStatus | u8 slot, u8 percent, u32 file, u8 status |

### 5.2 Game messages (EEM)
- **Classes:** 100 classes, `0x41–0xaa`. Names come from RTTI; sizes from each vtable's `ExportSize`. Full table: `EEM` in `tools/lan/eelan.py`.
- **Captured and decoded without mismatch** (23 types):
  - selection: `SelectUnit`, `UnselectAllUnits`;
  - `PathTo`, `GatherResource`, `BuildUnit`, `ResearchTechnology`, `Explore`, `ReplantFarms`;
  - `CivUpgradesRequested/Purchased`, `CivTimerExpired`, `LoadUnitData`, `InGameChat`;
  - `ReportStatistics` (about every 10 CTs);
  - `LoadPercent`, `StartLoading(+Response)`, `LoadMainScreen(+Response)`;
  - `PlayerSlotInfo`, `FullPlayerInformation`, `FullGameOptions`, `Resign`.
- **Variable sizes:** `0x4e–0x50` 6 + n; the UWS-bearing types (`0x5a`, `0x5b`, `0x60`, `0x64`, `0x6a`, `0x7d`, `0xa9`); `0x7e/0x7f` 11 + 2n + UWS; `0x96` 9 + 4n.

## 6. Synchronisation and OOS detection

### 6.1 Tick cadence [captured; static where noted]

| Claim | Status |
|---|---|
| Initial interval 350 ms, 60 PTs/CT, PT/s = (1000/350)·60 = 120 (engine+`0x53460`) | **Confirmed** for CTs 0–11 (PTs `0xff`, PT/s 120) |
| Interval tuned at run time (`TuneServer`, step up 125 / down 10, max 2250) | **Unobserved**: 350 ms throughout a 2-player LAN game |
| PTs per CT / PT/s | **15 / 44** from CT 12 on (15 × 2.857 = 42.9 PT/s, close to legion's measured ~41 PT/s). The derivation of 15/44 is not traced [open] |
| Checksum requested on every CT ≥ 40 with PTs > 0 (`+0x180` step = 1) | **Confirmed:** CT 40–857 (817 CTs), the only exception being the forced CT 148 after a resend; none after PTs dropped to 0 (CT 858+) |
| Checksum taken before the CT's PTs run; tick = processed-CT counter = CT | **Confirmed:** `0x32` tick equals the requesting CT for all 817; it arrives 0–42 ms after the CT, before the next CT |
| Dump-the-world / OOS removal | **Unobserved** (no mismatch occurred) |

### 6.2 What is hashed (EXE+`0x145ebf`, `__thiscall` on the world object, args `(char dump, uint flags)`) [static; Frida-confirmed call pattern]
- A **32-bit wrapping sum** of:
  - each world subsystem's virtual `+0x4c(flags)`;
  - + world `+0x80` (total PTs) + world `+0x18` + the executed-broadcast-input count (`DAT_919b68`);
  - + flag-gated terms.
- The network path uses `flags = 1`.
- **Frida:** the host computed it once per tick for 40–857 (dump 0, flags 1), and the values equal the client's wire values 817/817.
- **`ESChecksum`** is save/data checksumming, not OOS.

### 6.3 Exchange and verdict [static; exchange confirmed]
1. **Client:** `0x32 {tick, checksum}` as its own datagram [captured].
2. **Host:** logs its own value locally (`LogChecksumResponse`, engine+`0x54154`). Frida saw it called twice per tick, for slot 0 (client) and slot `0xfe` (host); **the host's value is never on the wire** [captured].
3. **Tally** once all players have reported:
   - 2 players: both flagged on a mismatch.
   - ≥ 3 players: majority wins; ties go to the host's group.
4. **Mismatch:** the next CT carries dump = 1, every machine writes `OOS yyyy.mm.dd - hhh.mmm.sss.log`, and losers get `NEMRemovePlayer(isOOS=1)`. [static; unobserved]

### 6.4 "Debug OOS Handling"
- Stored at options+`0x5be`; only read by load / save / copy and an options checkbox (EXE+`0x1c7e4a`) [static].
- No effect on OOS is expected [inferred]. A watchpoint on VA `0x919466` would confirm it.

### 6.5 Oracle
- **Wire:** `eelan.py export` → `checksums.csv/json` (client values per tick), `ticks.csv`, `session.csv`.
- **Host:** the Frida `netcode_hooks.js` `world` stream (EXE+`0x145ebf` per tick on every machine) and `logged` (the host's tally).
  - The Frida `NEMRemovePlayer(uchar&, bool, bool, bool)` hook did **not** fire for the end-of-game removal, which is built another way. Use the wire's nested `0x2b` instead.
- **Session-2 result:** 817/817 equal [captured]. Mac vs Windows streams compare by tick.

## 7. Winsock / iphlpapi HLE checklist (ADR-001 gap 12)

How the Mac port meets each item: §12 (P5-A).

**Scope:**
- **ws2_32** (24 imports, `TCP Protocol.dll`): the game session.
- **wsock32** (28 imports, EXE WONAPI): the lobby. It must be **functional**: UDP 8585 broadcast, TCP 33336 listen/accept/connect, `select`/`__WSAFDIsSet`.
- **iphlpapi** `GetAdaptersInfo`: plugin, fixed buffer; WONAPI, sized buffer.

- [ ] **Start-up.**
  - `WSAStartup(2.0)` (the plugin checks `wVersion == 2.0`) and `WSAStartup(1.1)` (WONAPI).
  - Refcounted `WSACleanup`. The plugin does Cleanup+Startup mid-session (`+0x2269`), so sockets must survive it.
- [ ] **Sockets and options.**
  - TCP/UDP sockets with small-integer handles.
  - `SO_BROADCAST`, `FIONBIO`.
  - `setsockopt` on `INVALID_SOCKET` must fail cleanly (`WSAENOTSOCK`) [measured, Wine].
  - **No `SO_REUSEADDR`:** a second bind of 33334/8585/33336 must fail with `WSAEADDRINUSE`.
- [ ] **Bind / listen / connect.**
  - Binds: `INADDR_ANY` on 33334, 33335 and 0; the lobby on 8585 and TCP 33336, possibly bound to the selected adapter IP.
  - `getsockname`; `listen(0x7fffffff)` mapped to `SOMAXCONN`.
  - Blocking `connect` for xfer before `WSAEventSelect`.
- [ ] **Non-blocking I/O** with `WSAEWOULDBLOCK`:
  - `recvfrom`/`recv`/`accept`/`send` (partial sends retried with `Sleep(1)`);
  - `recv(MSG_PEEK, 1)` for `IsAlive`;
  - WONAPI polls `recvfrom` every ~20 ms from two threads [measured, Wine].
- [ ] **Datagram sizes.** The host receive buffer is 512 bytes, the client's 576; oversize → `WSAEMSGSIZE`.
- [ ] **`WSAEventSelect`.**
  - `FD_READ`/`FD_ACCEPT`; a later call replaces the earlier one; it sets non-blocking.
  - **Re-arm after every `recvfrom`/`accept` while data is still queued.** The host reads one datagram per wake-up (engine+`0x54652`).
  - Implement with a kqueue thread plus a re-arm check.
- [ ] **Broadcast.**
  - **Unbound socket to 255.255.255.255:** send to each up IPv4 interface's directed broadcast (or `IP_BOUND_IF` + 255.255.255.255).
  - **Socket bound to a specific local IP to 255.255.255.255:** send only via that IP's interface.
  - This makes the lobby list work on ZeroTier/LAN by default (§3.1), unlike Windows.
- [ ] **Resolution.**
  - `gethostbyname` accepts dotted quads with no DNS; `gethostbyname(own name)` returns the `GetAdaptersInfo` IPv4 list in the same order.
  - `gethostbyname("empireearth.m1.sierra.com")` fails **immediately**.
  - Windows `WSAGetLastError` codes. Do not surface UDP ECONNREFUSED.
- [ ] **`GetAdaptersInfo`** (§3.4):
  - ≤ **10** `IP_ADAPTER_INFO` (640 B each).
  - Correct `ERROR_BUFFER_OVERFLOW` and required-size behaviour (WONAPI first calls with a NULL buffer).
  - Order: primary LAN, then VPN (`feth*`/`zt*`/`utun*` with IPv4), then others.
  - Skip `lo0` and adapters with no IPv4 or that are down.
  - Readable `Description`; `IpAddressList.IpAddress.String` set.
- [ ] **Heap.** HLE `free`/`delete` must tolerate interior pointers (log and ignore), or override `TCP Protocol.dll+0x1bee`. Without one of them, an adapter choice ≥ 2 crashes us exactly as it crashes Windows.
- [ ] **Firewall.** Inbound UDP 8585, TCP 33336, UDP 33334, TCP 33335 on every peer.

## 8. Version / data checksum (must match across Mac and Windows)

**Computation** (EXE+`0x137fe5`, global `0x919868`) [static]:
- Σ of all bytes (32-bit wrap; EXE+`0x134814`) of:
  - `Low-Level Engine.dll`, `sxlrt233.dll`, `TCP Protocol.dll`;
  - `Data\Db\*.dat`, `Data\Unit AI Scripts\*.tai`, `Data\Models\*.cem` (through the FS layer, archives included);
- + each archive's `Timestamp` tag.

**Captured:**
- Both peers sent `0x0e998b9e` (GOG files).
- Version string `v2.00.2974` = `v2.00.` + `%04d`(checksum & 0x1fff).
- Carried twice: in the lobby `VersionCheck` (TCP 33336) and in `0x22` (UDP 33334).

**Port requirements:**
- Keep the three original DLLs byte-identical in the game folder, visible to the FS layer.
- Never rebase them on disk.
- Check the menu's 4-digit suffix.

## 9. ZeroTier and VPN notes

- **Lobby list** = UDP 8585 broadcast. It works over ZeroTier/Hamachi (both carry broadcast) **if the broadcast leaves through the overlay interface.**
  - **Windows:** the route-preferred adapter wins unless one is chosen, and choosing a non-first adapter crashes at start (§3.4). Windows friends should make the overlay or LAN adapter the preferred route (interface metric), or disable unused virtual adapters.
  - **Mac port:** fan-out (§7).
- **Inbound rules on both sides:** UDP 8585, TCP 33336, UDP 33334, TCP 33335. On Windows, either the ZT network is *Private* or there is a rule. **legion §7's proposed rules need UDP 8585 added, and 33336 as TCP.**
- **Tailscale** (no broadcast): use the lobby's direct connect (`DirectConnectDialog`), which reaches the TCP 33336 room, then the normal hand-off. MagicDNS names resolve via `gethostbyname`.
- **Sizes:** game datagrams are ≤ 576 B, under any overlay MTU.

## 10. Validation against session 2

Run:

```sh
python -I tools/lan/eelan.py summary  analysis/lan-captures/session2/eevm/lan.pcapng
python -I tools/lan/eelan.py timeline analysis/lan-captures/session2/eevm/lan.pcapng --no-ticks
python -I tools/lan/eelan.py export   analysis/lan-captures/session2/*/lan.pcapng --out-dir <dir>
```

Both captures decode identically: 2,687 game datagrams, 0 errors, 0 size mismatches, 0 undecoded types. All lobby records decode.

| Earlier claim | Verdict |
|---|---|
| RC4 obfuscation of every 33334 datagram, key table | **Confirmed** |
| Ports: 33334 UDP game, 33335 TCP xfer | **Confirmed.** 33335: connect at start, no payload in a random-map game |
| 33336 "unused" (first draft) / "UDP lobby" (second draft) | **Corrected:** TCP lobby room. UDP 33336 is bound (Wine) but carried no traffic |
| 8585 role | **Corrected/decoded:** UDP broadcast game list (§3.1) |
| Plugin `EEService` discovery unused in the normal flow | **Corrected:** the host sends one duplicate-name search per hosted game; nobody replies; clients never use it |
| Client connects directly to `host:33334` at the hand-off | **Confirmed**, 8 ms after StartGame |
| Handshake order | **Corrected and refined** (§3.3). `0x23` is sent twice (top-level result 1, then in-CT result 0) |
| Guaranteed inputs ride in CT responses | **Unobserved;** all client inputs were standalone |
| Message types and sizes | **Confirmed** for the 10 NE and 23 EEM types seen |
| 350 ms CT, 120 PT/s initial | **Confirmed;** tuning unobserved; steady state 15 PTs, 44 PT/s |
| Checksum from CT 40, every CT, tick = CT, host value off-wire | **Confirmed** (817/817 equal with Frida) |
| Resend / ServerIsAlive behaviour | **Confirmed** (CT 147) |
| Data checksum equal on both peers | **Confirmed** (`0x0e998b9e`); also exchanged in the lobby |
| Network Adapter is cosmetic | **Corrected:** it picks the lobby's broadcast interface, and index ≥ 2 crashes the plugin (§3.4) |
| OOS dump/removal, pause/unpause pairs, reconnection, xfer payloads | **Unobserved** |

## 11. Open questions

- **Lobby fields:**
  - the rest of `0x08/0x02 GameSetup` (82 bytes; five fields placed in §3.2);
  - `0x08/0x03`;
  - slot attribute ids;
  - the advert's `0x8b49`.

  This needs static work on `LanLogic.cpp` (EXE RVA ≈ `0x3cb220`) and the `EELobby*` code (EXE RVA `0x27xxxx`).
- How 15 PTs / 44 PT/s are derived, and whether tuning ever moves the 350 ms interval (try a slow or lossy link).
- Host-side handling of a version / data-checksum mismatch, in both the lobby `VersionCheck` and the `0x22` handler (engine vtable `+0x6c`).
- Host migration (`0x37`/`0x38`, "Migrated Server"). The one unexplained client `EEService` search after a game.
- Whether the lobby's and the plugin's TCP 33335 can conflict.
- `Debug OOS Handling` watchpoint.

## Note: the version string depends on module bytes (2026-10-15, legion)
The main menu shows **v2.00.2974** with the original GOG files, but **v2.00.3644** with DLL copies rebased via `editbin` (the M1 capture copy, `C:\EEPort\game-m1`) [measured]. The displayed version and the connect-request data checksum (§8) are computed from the module **files**. So:
- M1 captures from rebased copies are fine for single player, but such a copy can't join LAN games with original installs.
- The Mac port must keep the original files byte-identical on disk. It does: the kit's loader relocates in memory only, and the native boot shows v2.00.2974 (`boot.md`).

## 12. Mac Winsock HLE (P5-A, 2026-10-15)

The kit's Winsock, IP helper API and Netbios, on BSD sockets (kit `feat/winsock`; the kit's own documentation is `runtime/README.md`, "Networking"). Labels: **[measured]** = `winsock_tests`, `platform_tests` and the plugin harness below on an M4 Max (macOS 15.5, one active interface `en0` 192.168.1.227, no ZeroTier); **[static]** = read from the code or `db/win32-imports.json`; **[inferred]** = reasoning.

### 12.1 Coverage [static]
| DLL | EE imports | Implemented | Also there |
|---|---|---|---|
| wsock32 (EXE, WONAPI lobby) | 28 | 28 | `getpeername`, the Winsock 1.1 blocking-hook calls, `WSARecvEx` |
| ws2_32 (`TCP Protocol.dll`) | 24 | 24 | `WSAEnumNetworkEvents`, the `WSA*Event` calls, `WSAIoctl` (`FIONBIO`, `FIONREAD`, `SIO_GET_INTERFACE_LIST`), `WSASocketA/W`, `WSARecv*`/`WSASend*` without overlapped I/O |
| iphlpapi | `GetAdaptersInfo` (plugin import; the EXE's by `GetProcAddress`) | yes | `GetAdaptersAddresses`, `GetIpForwardTable`, `GetBestInterface`, `GetBestRoute`, `GetIpAddrTable`, `GetNumberOfInterfaces` |
| netapi32 | none | `Netbios`: `NCBENUM`, `NCBRESET`, `NCBASTAT` (MAC address) | |

The EXE also asks `wininet.dll` for `InternetGetConnectedState` (WONAPI) [static: EXE strings]; the kit does not serve wininet, so that `LoadLibrary` fails. The lobby then assumes "connected", as on a Windows PC with Internet, and tries the WON directory servers; that is the intended behaviour ([lobby.md](lobby.md) §8: add the m2/m3 hosts to `offline_hosts`).

### 12.2 The §7 checklist on the Mac
| Item | Status |
|---|---|
| Start-up | `WSAStartup` 2.0 grants 2.0 (the plugin's `wVersion` check), 1.1 grants 1.1; start-ups are counted; `WSACleanup` never closes sockets, so the plugin's Cleanup + Startup inside `NPIMPublish` keeps them [measured] |
| Handles, `SO_BROADCAST`, `FIONBIO` | small integers from 0x100; `setsockopt(INVALID_SOCKET, SO_BROADCAST)` fails with `WSAENOTSOCK` [measured] |
| No `SO_REUSEADDR` | a second bind of a UDP or TCP port fails with `WSAEADDRINUSE`; TCP sockets carry `SO_REUSEADDR` on the host only so that a port can be bound again while old connections are in TIME_WAIT, as on Windows [measured] |
| Bind / listen / connect | `listen(0x7fffffff)` takes the host maximum; a blocking `connect` is a guest wait (other guest threads run) [measured] |
| Non-blocking I/O | `WSAEWOULDBLOCK` at once; `recv(MSG_PEEK)`; non-blocking `connect` is `WSAEWOULDBLOCK`, a refused one lands in `select`'s except set with `SO_ERROR` `WSAECONNREFUSED` [measured] |
| Datagram sizes | a datagram longer than the buffer: `WSAEMSGSIZE`, the part that fit kept [measured]. No ICMP error is ever surfaced on a UDP socket |
| `WSAEventSelect` | a selection records what already holds, a later one replaces it, an accepted socket inherits it; FD_READ is re-signalled after each `recvfrom` while datagrams are queued and quiet after the last (3 queued datagrams, one per wake-up) [measured]. A host thread polls the selected sockets and sets the event; an arrival during a wait woke it in well under a second [measured] |
| Broadcast | `255.255.255.255` from a wildcard socket goes to every adapter's directed broadcast (point-to-point tunnels: by interface index); from a socket bound to one adapter's address, to that adapter only. On fake adapters: 3 copies for 3 adapters, 1 for a bound socket; on the real `en0`, one copy to 192.168.1.255 that looped back [measured] |
| Resolution | dotted addresses, `localhost` and the own name without DNS; `gethostbyname(gethostname())` lists every adapter address in `GetAdaptersInfo` order [measured]. `empireearth.m1.sierra.com` is in `game.toml` `[network] offline_hosts` and fails with `WSAHOST_NOT_FOUND` in 0.01 ms [measured]. Other names go to the system resolver off the guest baton (Tailscale MagicDNS for direct connect, §9) |
| `GetAdaptersInfo` | ≤ 10 records and never more than the plugin's 6,400 bytes; `ERROR_BUFFER_OVERFLOW` with the size for a NULL or short buffer; order: `en*` (the default route's first), ZeroTier `feth*`/`zt*`, `utun*`/`tun*`/`wg*`..., others; no `lo0`, no down interface, no interface without IPv4; `Description` like "Ethernet or Wi-Fi (en0)", `IpAddress.String` set [measured]. `RECOMP_NET_ADAPTERS=en0,feth1234` picks and orders by hand |
| Heap | the kit CRT ignores and logs the interior `operator delete`: adapter index 2 and 3 freed `buf + 640·k` from `TCP Protocol.dll+0x484c`, exactly the §3.4 path, and the run went on [measured] |
| Firewall | the macOS application firewall (off on this Mac) would ask once per binary for incoming connections [inferred] |

**A socket bound to one adapter's address** (the lobby, when *Network Adapter* is set: §3.1 [inferred]) receives broadcasts on Windows, but BSD delivers broadcasts to wildcard sockets only. The kit binds such a UDP socket to the wildcard on the host, reports the guest's address from `getsockname`, and drops what Windows would not deliver (anything not addressed to that address or broadcast on that interface) [measured on fake adapters].

**Index 1 is the primary `en*`** in our list, so a Mac player who picks the first adapter (or none) never takes the plugin's index-≥ 2 path; and when they pick another, the CRT absorbs the bad free anyway.

### 12.3 The plugin harness: `tests/lan/plugin` [measured]
`ee_tcp_plugin_harness` maps sxlrt233, the engine and `TCP Protocol.dll` with the kit's loader (translations linked, as the M1 harness does) and drives the plugin's NPIM entry points on the kit's Winsock over loopback; `run_plugin.py` runs it and decodes its datagram with `tools/lan/eelan.py`.

```sh
.venv/bin/python tools/build.py --cmake-target ee_tcp_plugin_harness   # needs aux-a_sxlrt233, -b_engine, -g_tcp
.venv/bin/python -I tests/lan/plugin/run_plugin.py
```

All 34 harness checks and the 5 decoding checks pass:
1. `NPIMActivate` → 0 (`WSAStartup(2.0)`, probe socket, key table); `PIMIsValid` 0 → 1; the Sierra lookup fails in 0.01 ms.
2. `NPIMEnumerateNetworkAdapters` → 1 adapter, "Ethernet or Wi-Fi (en0)"; `NPIMGetLocalAddress(1)` → 192.168.1.227. On three fake adapters, indices 1–3 → 10.99.0.1, 10.100.0.1, 10.147.0.1, with the interior free logged twice.
3. `NPIMConnect(UDP, "127.0.0.1")` and `NPIMSend` of a 41-byte `0x22`: a plain socket on 127.0.0.1:33334 caught `01c130933e76…` (key index 1). eelan.py decrypts it to exactly the bytes sent: `NEMClientConnectionRequest`, version `v2.00.2974`, player `MacHarness`, checksum `0x0e998b9e`, no size mismatch.
4. `NPIMPublish` (UDP 33334, TCP 33335, private), `NPIMAllowNewConnections`; the request again: the server's event is signalled by `WSAEventSelect`, `NPIMReceiveFrom` decrypts it and puts the client in slot 0, the event is quiet afterwards; `NPIMSendTo(slot 0)` of a `0x23` is signalled on the client and `NPIMReceive` decrypts it.
5. `NPIMConnectForXfer` (blocking connect to 33335), `FD_ACCEPT` on the server's accept event, `NPIMAcceptForXfer` matches client 0, `NPIMIsAlive`, `NPIMCloseConnection`, `NPIMDeactivate`.

The private-game path (no `EEService` search) is used so that the harness sends nothing beyond the loopback interface.

### 12.4 Left for P5-B
- ~~Mac↔legion sessions: the 8585 lobby list, the 33336 room, the 33334 game and 33335 transfer, with checksum streams.~~ Done in §13. `RECOMP_NET_TRACE=1` prints every socket call, every broadcast per adapter, and every lookup.
- ~~The EXE's WONAPI lobby has not run on the Mac yet.~~ It has, against Windows in both roles (§13). Whether it binds the chosen adapter's address is still untested (both sessions used the default adapter). `InternetGetConnectedState` (§12.1).
- ZeroTier: no ZeroTier interface here; fan-out to `feth*` is tested on fake interfaces only. A limited broadcast out of a point-to-point `utun` by interface index is untested on a real tunnel (Tailscale and WireGuard drop broadcasts anyway).
- The host's `EEService` search (`private = 0`): it broadcasts to 33334 on every adapter; not run here to keep the test bed on loopback.
- Two instances on one Mac still cannot both bind 8585 or 33334, as on Windows (§3.5).

## 13. Mac↔Windows on the LAN (P5-E, 2026-10-15)
Two LAN games between the native Mac build (headless) and legion's unmodified GOG game, one hosted on each side: [lansession.md](lansession.md). [measured]
- **Every channel worked in both roles:**
  - the 8585 list: the advert, the query and its unicast reply;
  - the 33336 room: join, roster, `VersionCheck` (`v2.00.2974`, `0x0e998b9e` on both), chat, ready, StartGame, LaunchAck;
  - the 33334 game: `0x22`/`0x23`, loading, CTs, inputs, `0x32`;
  - the 33335 connect.
- **No out-of-sync.** With legion hosting and a computer player in the game, the Mac's `0x32` value equals the Windows host's own world checksum on all **1,178** ticks it sent (40–1217). The Mac as host tallied 1,246 of legion's ticks against its own without a mismatch.
- **The CD key does not block LAN play** with the same GOG key on both machines.
- **Corrections to this document:** record `0x07` is a keep-alive (§3.2), and the advert's last u16 is the starting epoch (§3.1). The game also slows itself to the slowest machine. The CT interval stayed at 350 ms (1,247 play CTs in 436 s), but the PTs per CT vary between 5 (the 15 PT/s floor, "Min PTs per Second") and 15, with 43 for 33 CTs in session B, and the host sends `EEMLagStatus` (0xa8) meanwhile. This answers part of §11's question on how 15/44 is derived.
