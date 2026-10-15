# Mac↔Windows LAN sessions (P5-E, M4's first step)

Status: 2026-10-15, 05:03–05:33. **The native Mac build and the unmodified Windows GOG game played two LAN games against each other, one hosted on each side, with no out-of-sync.** In the game legion hosted, the Mac's world checksum equals the Windows host's on **all 1,178 ticks** it reported. Context: [netcode.md](netcode.md) (the protocol), [lobby.md](lobby.md) (the lobby on the Mac), [phase5.md](../phase5.md), [legion.md](legion.md) §7 (the earlier legion↔VM session).

Labels: **[measured]** = the captures and screenshots named in §6; **[inferred]** = reasoning.

---

## 1. Result

| | Session A: the Mac hosts | Session B: legion hosts |
|---|---|---|
| Game | `mac-ref`: Random Map, Small, Continental, Tournament-Low, Prehistoric→Nano, **Use Custom Civs** ticked; 2 humans | `legion-ref`: Random Map, Small, Continental, Deathmatch, Random Epoch (Renaissance)→Nano; 2 humans **+ 1 computer player** |
| Found | legion's LAN list, at its first refresh after the game was created (14 s) | the Mac's LAN list, as soon as it opened |
| Lobby | join, `VersionCheck` `v2.00.2974` / `0x0e998b9e`, chat both ways, Ready, Start Game | the same, the other way round |
| CD key | no complaint (the same GOG key on both machines) | no complaint |
| Game | 05:05:58–05:13:16: 1,247 play ticks, 12,928 PTs (≈5 game minutes at the full speed of session 2) | 05:23:35–05:30:30: 1,186 play ticks, 8,866 PTs, mostly at the 15 PT/s floor |
| Checksums | 1,246 ticks checked by the Mac (the host) against legion's; **no out-of-sync** | **1,178 of 1,178 Mac values equal the Windows host's** (ticks 40–1217) |
| Out-of-sync | none: no dump flag in any CT, no `NEMRemovePlayer(isOOS)`, no `OOS*.log` on either side | none: the same |
| End | legion closed its game (stuck in a civilization dialog); the Mac host carried on: **"You are Victorious!"**, then quit | the Mac **resigned**, quit to the statistics, went back to the lobby and exited; legion saw "Player 'Unknown144' has dropped out of the game!", then quit |
| Mac exit | `ExitProcess(0)` after 857 s; script finished; no strict-mode stop; no import without an implementation | `ExitProcess(0)` after 763 s; the same |

**What this shows:**
- The Mac's lobby works against Windows in both roles: the LAN list (UDP 8585), the room (TCP 33336), the version and data checksum, chat, ready and the hand-off.
- The game protocol (UDP 33334, TCP 33335) works in both roles: the connection request and response, loading, ticks, inputs, the checksum requests.
- The simulation agrees, tick for tick, with an AI player, a deathmatch economy, an attack on the Mac's base, two civilization choices and a resignation in the game.
- The Mac host survives a client that vanishes, and the Mac goes back to the lobby after a game (the second `FlipToGDISurface`, [lobby.md](lobby.md) §7).

## 2. The setup
- **The Mac:** M4 Max, macOS 15.5, `192.168.1.227` on en0; macOS firewall off. The native build, **headless** (`ee_boot`, strict mode, the software Direct3D 7 renderer, 800×600×16 in game), kit `feat/lan-session` 46bd160 on `ee` ec00c32.
- **legion:** Windows 11, `192.168.1.132` on Wi-Fi, the GOG game in `C:\EEPort\game` (original files), Network Adapter at its default, VMware's VMnet1/VMnet8 adapters disabled (so the default adapter is the Wi-Fi), the game's Private-profile firewall rules from an earlier prompt (legion.md §7). Frida `netcode_hooks.js` (`wire=false`, snapshot every 15 s).
- **The same Wi-Fi**, no VPN. Both machines use the user's one GOG CD key.
- **Captures:**
  - the Mac's `ee_boot --lan-pcap`: every UDP datagram and TCP byte the game sends or receives, with wall-clock times (§5);
  - legion's Frida world-checksum stream;
  - screenshots on both sides and legion's screen recording.
  - legion's `pktmon` needs an elevated shell, which neither agent has; the Mac's capture holds the same traffic.

## 3. The procedure

### 3.1 How the two sides kept in step
The legion agent drives its game; the Mac agent drives the Mac's. They share no clock or script, only the mailbox and the network:
- **Request** `to-legion\0028-mac-lan-session.md` gave legion both sessions as steps, each waiting for something it can see with a long timeout: `mac-ref` in its list (30 min), "hello from mac" in the chat (10 min), the game starting (15 min), the Mac's player in its room (30 min), the Mac's ready mark (10 min). `0029` told it to skip pktmon.
- **legion** appended a timestamped line per step to `to-mac\0028-lan-session.progress.md` and wrote `0028-lan-session.reply.md` at the end.
- **The Mac** ran its script to its LAN page (or its room) and then *followed* a live file (`follow`, §5.1). The Mac agent watched the Mac's own capture (`mac_session.py status`: who joined, chat, ready, StartGame, ticks, checksums) and appended the next steps: the chat line, Ready or Start Game, in-game clicks, the menu, quit. Each action took effect within a second; a round trip through legion's mailbox took about a minute and was needed only once (pktmon).

### 3.2 Session A: the Mac hosts
| legion (local time) | Mac |
|---|---|
| 05:02 LAN page, refreshing | 05:03:57 `mac-ref` created; its advert on 8585 |
| 05:04:06 query | 05:04:07 the Mac answers by unicast |
| 05:04:11 `mac-ref` listed (Prehistoric Age, Continental, 1/8) | |
| 05:04:52 Join Game | 05:04:52 `LobbyJoin` Unknown1634 → roster, `VersionCheck` v2.00.2974 / 0x0e998b9e → GameSetup, SlotAssign; the room shows both players |
| 05:05:10 "hello from legion" | relayed; 05:05:20 "hello from mac" |
| 05:05:34 Ready | `SetReady` → `ReadyState` |
| | 05:05:44 Start Game: `StartGame`, legion's `0x22` on 33334, `0x23`, `EEMStartLoading` |
| in game | 05:05:58 `NEMStartGame`; the Mac selects the Capitol and queues two citizens |
| 05:08:49 civilization warning; the custom-civ builder; "You have not spent all your civilization points…" — its clicks on Yes missed (legion's automation) | the Mac sends CTs; legion's `0x32` answers every one |
| 05:13:16 closes its game (WM_CLOSE) | `NEMServerIsAlive` while legion's answers stop, then the game goes on alone |
| | the Mac's civilization screen: the same dialog, Yes works; **"You are Victorious!"**, 7 citizens; `quit` |

### 3.3 Session B: legion hosts
| legion | Mac |
|---|---|
| 05:15:30 hosting `legion-ref` (Deathmatch, Random Epoch); 05:16:31 slot 3 = Computer | 05:18:38 `legion-ref` in the Mac's list (Random Epoch, Continental, 1/7) |
| | 05:18:52 Join Game: `LobbyJoin` Unknown144 → roster, `VersionCheck`, GameSetup, SlotAssign; the room shows legion, the Mac and Computer |
| | 05:19:09 "hello from mac" |
| 05:22:49 "hello from legion" | relayed |
| 05:23:07 the Mac's ready mark; Start Game enabled | 05:23:06 Ready (`SetReady` → `ReadyState`) |
| 05:23:23 Start Game | `StartGame`, the Mac's `0x22`, `0x23`, loading; 05:23:35 `NEMStartGame` (Renaissance) |
| capitol, two citizens, a forager; the lag (snail) icon at times; 05:26:56 "Choose Civilization" by itself | the Mac queues units; its civilization screen by itself: Kingdom of Italy (Capitol 7500 → 10000 HP); the computer's army attacks the Mac's base |
| | 05:29:48 F10 → Resign → "Do you wish to resign this game and become an observer?" Yes: `EEMResign`, **"You have been Defeated!"** |
| 05:30:40 "--GAME-- Player 'Unknown144' has dropped out of the game!" | F10 → Quit this Game → statistics; 05:30:30 the host's `NEMRemovePlayer(player 2, isOOS 0, exiting 1)` |
| 05:31:40 quits (WM_CLOSE) | Exit Game Summary → the lobby's LAN page → Logout → Back → main menu → Exit Game |

## 4. The checksums
`python -I tools/lan/mac_session.py checksums DIR --frida netcode_hooks.json` lines the streams up by tick [measured]:

| Session | Streams | Shared ticks | Agree |
|---|---|---|---|
| B | the Mac's `0x32` (wire) vs legion's own world checksum (Frida `world`, the host's value, never on the wire) | 1,178 (40–1217) | **1,178** |
| B | the Mac's `0x32` vs legion's tally for the Mac's slot (`logged`, slot 0) | 1,177 (40–1216) | 1,177 |
| B | legion's tally, slot 0 vs slot 254 (its own) | 1,177 | 1,177 |
| A | legion's `0x32` (wire, received by the Mac) vs legion's own (Frida) | 1,205 (41–1245) | 1,205 (one machine against itself) |

- **In A the Mac's own values are not on record:** a host never sends its checksum. Its verdict is: it tallied legion's value against its own on 1,246 ticks and never flagged a mismatch (no dump flag in the next CT, no `NEMRemovePlayer(isOOS)`, no `OOS*.log`). B gives the per-tick Mac-against-Windows comparison. A host-side record needs the Mac's own stream (P4-C's `RECOMP_EE_CHECKSUM_TRACE`, or a hook on EXE 0x545ebf as `netcode_hooks.js` does on Windows).
- **The tick at which each side computes is the same** (CT 40 on, every CT with PTs, tick = CT), as netcode.md §6.1 measured on Windows.

## 5. Tools

### 5.1 The kit: `follow` and `wait file` (kit `feat/lan-session`)
- `follow PATH`: the lines appended to PATH are the script's next steps, run as they arrive, until a line `end`.
  - A line that does not parse is reported and skipped.
  - `timeout` there extends the run.
  - `within MS` is an idle limit.
- `wait file PATH`: go on once the file exists.
- The dump cap counts file names: a name dumped again replaces its file.
- `host/input_script.h` documents both; `input_script_tests` covers them (189 checks).

### 5.2 ee-macos
- **`ee_boot --lan-pcap FILE`** now records the TCP connections too (the lobby room on 33336, the transfer port 33335) as data segments with running sequence numbers and a FIN at `closesocket`, and stamps packets with the wall clock, so a Mac capture lines up with the other machine's. `tools/lan/eelan.py` reads it unchanged.
- **`tools/lan/mac_session.py`**:
  - `run host|join [--name G] [--minutes M] [--out DIR]`: seeds a fresh user directory, holds `tools/game_ports.py`'s lock, runs `ee_boot` with `tools/lan/scripts/{host,join}.script` and `RECOMP_NET_TRACE=1`, keeps the game's own logs and deletes the user directory (it holds the CD key).
  - `say DIR 'step' …`: appends steps to the live file.
  - `status DIR`: the latest script lines, the lobby and game events decoded from the growing capture, the last CT, the checksum count.
  - `checksums DIR [--frida JSON]`: §4.

Running it on this Mac (the crash-reporter wrapper first, as for every binary here):
```sh
nice -n 10 env DYLD_INSERT_LIBRARIES=$SHIM timeout 4200 \
  .venv/bin/python -I tools/lan/mac_session.py run host --out analysis/lan-sessions/session3-a-mac-host
.venv/bin/python -I tools/lan/mac_session.py status analysis/lan-sessions/session3-a-mac-host
.venv/bin/python -I tools/lan/mac_session.py say analysis/lan-sessions/session3-a-mac-host \
  'click 265 333' 'type hello from mac' 'key RETURN' 'dump chat'
```
- **Coordinates used:**
  - the room (1024×768): chat line (265, 333); Start Game, or the joiner's Ready, (660, 206); Dissolve or Leave Game (660, 265);
  - the LAN page: first game in the list (140, 168); Join Game (614, 662); Host Game (774, 662); Logout (937, 733); then Back (781, 644) and Exit Game (511, 680) at the menu;
  - in game (800×600, `pointer f32 [0x9197a8]+4 [0x9197a8]+8`): the Capitol at the centre (352, 290); its first unit button (193, 526);
  - F10 opens the game menu: Quit this Game (400, 74), Resign (290, 204), Return to Game (400, 284); its Yes/No dialogs have Yes at (350, 373);
  - the civilization screens: OK (400, 520); the custom builder's check (448, 548);
  - the statistics: Exit Game Summary (511, 733).

## 6. Evidence (not in git: game state)
- **Mac:** `analysis/lan-sessions/session3-a-mac-host/` and `…/session3-b-mac-joins/`:
  - `lan.pcap`, `ee_boot.log` (script, net trace and boot report);
  - `live.script` (every step sent), the generated `host.script` and `join.script`;
  - `frames/*.png` (14 and 22 frames);
  - `legion/`: legion's Frida `netcode_hooks.json` and its screenshots, fetched.
- **legion:**
  - `C:\EEPort\captures\lan\session3\{a,b}\`: PNGs, `a-session.mkv` and `b-session.mkv`, `frida\`;
  - the mailbox: `to-mac\0028-lan-session.progress.md` (minute by minute, dialog texts verbatim) and `0028-lan-session.reply.md`.

## 7. Findings
1. **The CD key does not block LAN play** between two machines with the same GOG key, in either direction, from the LAN list [measured]. The earlier "Invalid CDKey" between two copies on one Mac by Direct Connect to 127.0.0.1 (lobby.md §13.4) remains unexplained; it was not reproduced here and Direct Connect between machines was not tried. Nothing needs the user's retail key.
2. **Record type 7 on TCP 33336 is a client keep-alive, not "LobbyLeave"** [measured]. The client sends the 3-byte record 240 s after joining and then periodically, game or no game, and the connection stays open.
   - legion (Windows): 05:08:52, 05:10:52 and 05:12:52 after its 05:04:52 join, i.e. +240 s, then every 120 s.
   - The Mac: 05:22:52, 05:26:52 and 05:28:52 after its 05:18:52 join, i.e. +240, +480 and +600 s. The net trace confirms three `send(…, 3)` calls, so the Mac skipped the one due at +360 s, about a minute into the game [measured; cause open: the WONAPI timer that drives it is not identified]. The host did not mind.
   - Session 2's first was at join + 240 s too.
3. **The advert's unknown u16 is the starting epoch** [measured + inferred]. It reads 14 (Random) for legion's and the VM's games and 0 (Prehistoric) for the Mac's. The list's Starting Epoch column follows it.
   - In `GameSetup`, body offsets 19, 20 and 21 are resources (0 Tournament-Low, 4 Deathmatch), starting epoch (0, 14) and ending epoch (13, Nano). Offset 29 is Use Custom Civs, and offset 45 is slot 3's type (1 = Computer).
4. **Speed follows the slowest machine.** The host lowers the PTs per CT from 15 to as few as 5, the floor of 15 PT/s ("Min PTs per Second"), and sends `EEMLagStatus`; legion showed EE's lag (snail) icon. Session A spent 466 of 1,247 ticks at full speed, session B 958 of 1,186 at the floor.
   - The headless Mac renders in software at about 180 % CPU and is the likely laggard [inferred]. The app on Metal ran single-player at about 110 frames/s (menus.md §3) but has not played a LAN game yet.
   - Speed does not touch sync: the checksums agree at every rate.
5. **Every LAN game raises the civilization timer** after about three minutes: the custom-civ builder when Use Custom Civs is ticked, else "Choose Civilization". The game runs behind the screen. On the Mac both screens and their dialogs work.
6. **A vanished client** (legion's WM_CLOSE) leaves the Mac host sending `NEMServerIsAlive`; it then plays on alone and declares victory, as the protocol intends (netcode.md §3.3).
7. **No firewall prompt** on legion (its rules from legion.md §7 cover the game) and none on the Mac (the macOS firewall is off).

## 8. What M4 still needs
- **A game played to its end** (a victory, not a resignation) and longer games: tens of thousands of ticks rather than about 1,200.
- **The windowed app on Metal** in a LAN game (speed, the overlay, the pointer over the lobby), with a person at the Mac.
- **The Mac host's own checksum stream** (P4-C), so that a hosted game has a per-tick record as well as a verdict.
- **ZeroTier** (not installed on legion: a driver, so the user's decision) and **Mac↔Mac** (a second Mac).
- **Direct Connect** between machines, and the one-Mac CD-key question.
- **The Mac's lobby keep-alive** skipped one period in session B (§7 item 2): find the WONAPI timer that sends it.
- **legion's pktmon** needs an elevated shell. The user can start `C:\EEPort\captures\pktmon-session3a.cmd` from one, but the Mac's capture already holds the session's traffic.
