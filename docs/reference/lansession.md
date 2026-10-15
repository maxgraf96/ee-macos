# Mac↔Windows LAN sessions (P5-E, P5-F; M4)

**Session 4 (P5-F, §9), the player app on Metal:** the app joined legion's game from its LAN list and played 10.8 minutes in sync (1,846 ticks equal) until an **out-of-sync at tick 1888, right after the 10-minute autosave, which failed on the Mac** (§9.3; the cause, a `CreateFile` with no access, is fixed in the kit, §9.2 item 4); the app also hosted a game legion joined from its list (§9.1, 4F). Two blockers for players were found and documented: macOS's **Local Network** permission, without which the app neither sees nor joins anyone (§9.2 item 1), and **Direct Connect**, which the original game refuses locally with "Invalid CDKey" (§9.2 item 2).

Status of sessions 1–3: 2026-10-15, 05:03–05:33. **The native Mac build and the unmodified Windows GOG game played two LAN games against each other, one hosted on each side, with no out-of-sync.** In the game legion hosted, the Mac's world checksum equals the Windows host's on **all 1,178 ticks** it reported. Context: [netcode.md](netcode.md) (the protocol), [lobby.md](lobby.md) (the lobby on the Mac), [phase5.md](../phase5.md), [legion.md](legion.md) §7 (the earlier legion↔VM session).

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
1. **The CD key does not block LAN play from the list** [measured]. *Corrected by session 4 (§9.2 item 2):* `ee_boot` here read the developer registry, which holds **no CD key** (P4-C found that `--user` was ignored), so the Mac played sessions A and B keyless; in session 4 the app, with the same GOG key as legion, joined and hosted from the list too. "Invalid CDKey" is Direct Connect's own local check, on Windows as on the Mac.
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
- **A game played to its end** (a victory, not a resignation) and longer games: tens of thousands of ticks rather than about 1,200. Session 4 reached tick 1,888; what stops longer games now is the next item.
- **The autosave failed on the Mac** ("Error Creating File", headless and in the app) and the first simulation difference followed it, in LAN (§9.3) and in P4-C's single-player runs. Fixed in kit `feat/lan-app` (§9.2 item 4); a long LAN game with the fix, past two autosaves, is the test.
- ~~The windowed app on Metal in a LAN game~~: §9 (with a person at the Mac still to do: the Local Network prompt, the civilization screen with a real mouse).
- **The Mac host's own checksum stream** (P4-C), so that a hosted game has a per-tick record as well as a verdict.
- **ZeroTier** (not installed on legion: a driver, so the user's decision) and **Mac↔Mac** (a second Mac).
- ~~Direct Connect between machines, and the one-Mac CD-key question~~: §9.2 item 2 (refused locally by the original game).
- **The Mac's lobby keep-alive** skipped one period in session B (§7 item 2): find the WONAPI timer that sends it.
- **legion's pktmon** needs an elevated shell. The user can start `C:\EEPort\captures\pktmon-session3a.cmd` from one, but the Mac's capture already holds the session's traffic.

---

## 9. Session 4: the player app on Metal (P5-F, 2026-10-15 06:57–09:06)

The shareable app (`tools/package_app.py`, first run from the user's Wine wrapper into a scratch `EE_HOME`), on Metal at 1920×1080, against legion's unmodified GOG game. Kit `feat/lan-app` (4d89bbb, d643d42, 7abd861 during the sessions, then f4f711c and 35912bd; rebased on `ee` ba5749a). Labels as above; the evidence is listed in §9.6.

### 9.1 Result

| | What was tried | What happened |
|---|---|---|
| **4C** | The app hosts `mac-app` (Prehistoric, Standard-Low, Easy; two computers and the Mac on team 1); legion joins from its list | **legion's list stayed empty** (61 refreshes, 07:36–08:02): the app never received legion's queries. Its Direct Connect join (address 192.168.1.227) gave **"Invalid CDKey"**, twice. No game. Cause: §9.2 items 1 and 2 |
| **4D** | legion hosts `legion-dc` (Direct Connect test); the app joins by Direct Connect to 192.168.1.132 | The list entry appears (192.168.1.132:33336, defaults: Prehistoric, Continental, 0 players), **Join Game → "Invalid CDKey"** at once. The app's net trace shows **no socket call at all** for the add or the join |
| **4E** | legion hosts `legion-dc` (Prehistoric, Standard-Low, Easy; legion and a computer on team 1); **the app joins from its LAN list**; 25 minutes planned, then the Mac resigns | Join, `VersionCheck` (v2.00.2974, 0x0e998b9e), chat both ways, Ready, Start Game 08:33:08, in game 08:33:18. **10.8 minutes, 30,831 PTs** (≈12 game minutes at full speed). **1,846 ticks equal (40–1887)**, then **out of sync at tick 1888** (08:44:10), 16 s after the 10-minute **autosave, which failed on the Mac** ("Error Creating File"). §9.3 |
| **4F** | **The app hosts `mac-dc`** (two computers and the Mac on team 1, legion teamless), started as a child of the terminal (§9.2 item 1); legion first tries Direct Connect, then joins from its list; legion resigns after about 6 minutes, before the autosave | legion found `mac-dc` at its first refresh. Its **Direct Connect join (08:54:20) → "Invalid CDKey", and no packet from legion reached the Mac** between its queries at 08:53:51 and 08:54:37. List join 08:54:42, chat both ways, Ready, Start Game 08:55:27, in game 08:55:38. **8.2 minutes, 22,441 PTs**, full speed, no lag; the Mac host tallied legion's checksum on **1,392 ticks without a mismatch** (no dump flag), and legion's 1,392 wire values equal its own (Frida). 09:03:47 legion resigns → the Mac: **"You are Victorious!"** → F10 → Quit this Game → "Do you wish to quit this game and view the game statistics?" Yes → Post Game Statistics → Exit Game Summary → the LAN page → Logout → Back → main menu → Exit Game; guest exit code 0, script 117 of 117 steps. legion: "You have been Defeated!", "--GAME-- Player 'Unknown144' is victorious!", statistics, lobby, exit |

### 9.2 Findings

1. **macOS Local Network privacy blocks the app's LAN play until the player allows it** [measured]. macOS 15 asks before an app talks to the local network. Unanswered or denied (unattended here: nobody could click), the app:
   - receives **no broadcast from another machine** (legion's queries and keep-alives on 8585 never reached its socket, while its own came back);
   - gets **EHOSTUNREACH** (`WSAEHOSTUNREACH` 10065) for a TCP connect to a LAN host: Join Game from the list did nothing visible;
   - still sends its own broadcasts (legion answered the app's query with a unicast advert, which arrived).

   **4C against 4F on UDP 8585** [measured, the app's own captures]: the app sent its broadcasts in both (4C 111, 4F 68; no send error), and in 4C legion's game even answered the app's own query (2 unicast adverts arrived). But in 4C it received **none** of legion's broadcasts (legion's 61 refreshes, its keep-alives), and in 4F, with the access, it received 3 queries and 12 keep-alives. A list fills from the hosts' answers to its queries, so legion's stayed empty in 4C: the inbound filtering alone explains it.

   The same connect from this Mac's terminal succeeded, and Python broadcasts from legion (bound to 192.168.1.132, as the game's are) arrived at a listener here; `ee_boot` in session 3 ran under the terminal's permission. Started as a child of the terminal (`mac_session.py run --app --direct`, the window brought forward with `open -a`), the very same app saw legion's game, joined and played (4E). Fixes: `NSLocalNetworkUsageDescription` in the app's Info.plist (the prompt's text), INSTALL.md §5.1 step 2 (Allow; where to turn it on later). The prompts did appear: the user found two macOS networking prompts on this Mac in the morning and clicked Allow at about 09:42. **Ad-hoc-signed binaries may be asked again after every rebuild** (the permission follows the code signature): a developer's `EmpireEarth.app` or `ee_boot` started outside a terminal that has the access, and a player's app after an update.
2. **Direct Connect refuses to join with "Invalid CDKey", locally, on both platforms** [measured + static].
   - The joining game shows the dialog **before it sends anything**: the app's net trace has no socket call for the add or the join (4D), and nothing from legion reached the Mac in 4C (though there the app could not have received it anyway, item 1).
   - The check [static]: the Direct Connect join (EXE 0x7ce720) calls 0x746820, which returns the byte at `WONLobbyPrv + 0x90` (the lobby's private state, global 0x944254); when it is 0 the join shows `InvalidCDKeyTitle`/`InvalidCDKeyDetails` (string objects 0x94463c/0x944774) and stops. The LAN list's join does not look at it. What sets the byte is not found yet (not in `WONLobbyPrv`'s construction and init, 0x74db80/0x750d80, to depth 3).
   - **Keys** (compared by hash, never printed): the app's imported key, `seed_user.py`'s and legion's (`HKLM\SOFTWARE\WOW6432Node\Sierra\CDKeys`) are the same 16 bytes. The kit hands the game the 16 bytes (REG_BINARY stored as hex in `registry.json`, decoded by `runtime/misc.cpp`). The developer registry that `ee_boot` read in session 3 (P4-C found that `--user` was ignored) has **no CD key at all**. So: list joins work with no key (session 3) and with a key equal to the host's (4E); Direct Connect fails with no key (lobby.md §13.4, one Mac) and with the GOG key (4D), before contacting the host.
   - **The controlled pair** [measured]: the app with the key removed from its `registry.json` (session `session4-g-dc-nokey`) gets the same dialog and makes no connect either; with the key it is 4D. And in 4F, legion's Direct Connect join (08:54:20) sent nothing to the Mac, which by then received everything else (its queries, then its list join at 08:54:42). So the refusal is local on both platforms, with and without a key, and duplicate keys are not what stops it.
   - **No key travels on the list's path** [measured]: the joiner's `LobbyJoin` (27 bytes), `VersionCheck` (32) and `NEMClientConnectionRequest` (41) are the same size with the same fields keyless (session 3 B) and keyed (4E); none has room for a key. So a keyless game neither skips a check nor sends an empty key there: there is nothing to send.
   - **Do all GOG copies share one key?** The key comes from GOG's install script (`goggame-1207658777.script`, read by the importer and `seed_user.py`); legion's install, from the same account, has the same 16 bytes. Whether other accounts' scripts differ is not observable here.
   - For players: **use the list**; Direct Connect is not an option with GOG copies, and a VPN must carry the game's broadcasts (ZeroTier does, Tailscale does not). Nothing suggests the importer should offer "no key": the list works with and without one.
3. **F10 belonged to the kit's settings page** in the app: the page froze the game and took the pointer, so Empire Earth's own menu (Resign, Quit this Game) was unreachable by keyboard [measured]. Kit: game.toml `[settings] key` (default F10; `"none"`), and ⌘, opens the page on a Mac; Empire Earth sets `key = "none"`. With it, F10 opens the game menu in the app (1920×1080: Quit this Game (960, 136), Resign (695, 370), Return to Game (960, 514)).
4. **Every autosave failed on the Mac** ("Error Creating File", 4E, and in P4-C's headless runs), and the session's only out-of-sync followed it (§9.3) [measured]. A headless repro with `AutoSave In Milliseconds` = 20 000 and `--trace` names the call: the game makes each save file with `CreateFileA("C:\EMPIRE EARTH\data\Saved Games\AutoSave ….ees", 0 /* no access */, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL)` before it opens it to write, and the kit treated an open without `GENERIC_WRITE` as a read, found no such file in any tier and returned `INVALID_HANDLE_VALUE`. Windows creates the file whatever the access. **Fixed in the kit** (`feat/lan-app` f4f711c, `runtime/kernel32.cpp`): `CREATE_NEW`, `CREATE_ALWAYS` and `TRUNCATE_EXISTING` are writes whatever the access, `OPEN_ALWAYS` with read access creates a file no tier has, and a second `CREATE_NEW` fails with `ERROR_FILE_EXISTS`; `loader_tests` +5 checks (4 of them fail without the fix). In the rebuilt app a single-player game with a 20-s interval wrote two autosaves (80 KB each, `<home>/data/Saved Games/`) with no dialog. Whether this also ends the out-of-sync is the next long game's (and P4-C's PT 24 301) question.
5. **The civilization screen** (OK at (960, 937) at 1920×1080): in the rehearsal a scripted closed-loop click could not reach its OK (the game's pointer stayed at the screen centre, 960, 540); in 4F the same click worked. The timer chooses for the player when nobody does (4E: Ancient Greece); Enter opens the chat instead. A person with a real mouse should try it.
6. **Rules the room enforces**: "All players can't be on the same team" stops Start Game [measured]. Computers on the host's team plus a teamless guest is the set-up for "resign and the other side wins".
7. **The app's scripted `type`** reaches the lobby's edit fields but not the in-game chat line (no `EEMInGameChat` was sent).
8. **Performance** [measured; M4 Max; `report`]. The game ran at full speed throughout: **no `EEMLagStatus`**, every play CT at the full 15 PTs or catching up (35, 43), where session 3's headless build held the game at the 15 PT/s floor most of the time.

   | | 4E: the app joins, 1 computer | 4F: the app hosts, 2 computers |
   |---|---|---|
   | CPU in game, % of one core (10-s samples) | median 192, p90 203, max 213 | median 164, p90 209, max 248 |
   | new frames/s in game (10-s bins) | median 60, p10 49 | median 120, p10 59 |
   | RSS | 1.1 GB | 1.1 GB |

   Frames are paced by the display with Wait for VSync on (the median is the refresh rate in use); the drawable is 1920×1080.

### 9.3 The out-of-sync in 4E

- **First diverging tick: 1888** (PT 30,816 → 30,831). The Mac's `0x32` is 0x5ef992e4, legion's own world checksum (Frida) and its tally for slot 254 are 0x01edbeba. Ticks 40–1887 agree, 1,846 of them, and legion's tally for the Mac's slot equals the Mac's wire values on all 1,847.
- **Before it:** 08:43:54, CT ≈1843: legion's server sent `EEMLoadOrSaveGame` "AutoSave 2026.10.15 - 08h.43m.53s.ees"; both answered; **the Mac's save failed** ("Error Creating File" on its screen), legion's succeeded.
- **The dump** (CT 1889, `dump_the_world`, PTs 0): both machines wrote `OOS 2026.10.15 - 08h.44m.10s.log`, 15,691 lines each. They differ in 16 lines only: four units of player 0 (nature; object types 261 and 325, 12 hit points, action 5000: animals) in their animation (7 or 8) and current frame, and `mRandomNumberGenerator.GetSeed()` (Windows 503,083,501, the Mac 1,419,499,471). No position, hit points or goal differs.
- **Reading** [inferred]: the world RNG diverged, and the animals' random animation choice is where the dump shows it. P4-C (simparity.md §7.6) finds the same shape in single player: Mac = Windows for 24,300 PTs, then at **PT 24,301** the Mac's computer player reviews its strategy when Windows' does not (75 extra `random_shuffle` draws), on AI state no checksum covers. **24,000 PTs at P4-C's 40 PT/s is 600 s, the default autosave interval**, and here the difference appears 45 CTs after the autosave. The working hypothesis for P4-C: the failed save leaves the Mac's AI bookkeeping (the review schedule) unlike Windows', whose save succeeded. The save failure is fixed now (item 4); the hypothesis is tested by a long game with the fixed kit.
- **Confirmed** (P4-D, simparity.md §9) [measured, static]: every machine executes the host's `EEMLoadOrSaveGame`, and a save that succeeds serializes each computer player (0x43ded9), which clears its "review scheduled" flag; at its next update the computer player reschedules its strategy review 30 s later. Legion saved, the Mac did not, so the Mac's computer player reviewed (and drew from the world RNG) when legion's did not. In single player with the fixed kit the Mac equals Windows through the autosave to the end of the game (29,827 PTs).
- The game then stopped: no CT after 1889; the app was quit at 08:48.

### 9.4 The procedure

- **legion**: mailbox 0040 (4C, 4D), 0041 (timing), 0042 (Direct Connect fallback), 0043 (broadcast test with `0043-bcast.py`, which only sends), 0044 (4E) and its addenda (4F, the out-of-sync). Progress in `to-mac\0040-lan-app.progress.md`.
- **The Mac**: one game server at a time; the ports were contended with P4-C's and P3-F's runs all night, so a long session must wait for the ports in the kernel rather than poll (`main`'s `game_ports_lock` now blocks in flock by default; P3-F's polling runs still won twice tonight). A rehearsal against the computers (`analysis/lan-sessions/session4-rehearsal-*`) found items 3 and 6 and the in-game coordinates.

### 9.5 Tools

- **Kit** (`feat/lan-app`):
  - `RECOMP_NET_PCAP=FILE`: the program's traffic as a raw-IP pcap with wall-clock times, flushed per packet, in any host (`runtime/winsock.cpp`, "The capture"; `winsock_tests`). It is what `ee_boot --lan-pcap` records, with the sockets' real addresses.
  - `[settings] key` and ⌘, (item 3).
  - A followed script line that fails is reported and skipped: a missed click no longer ends a live game (`input_script_tests`).
- **ee-macos**:
  - `tools/lan/mac_session.py run host|join --app build/dist/EmpireEarth.app --home DIR [--direct] [--wait-ports M]` (without `--wait-ports` it waits for the ports as long as it takes): the app through Launch Services (`open -n -W`), or with `--direct` as a child of the terminal (item 1); `RECOMP_NET_PCAP`, `RECOMP_NET_TRACE`, `RECOMP_FRAME_TIMINGS` (`frames.csv`), the host's CPU every 10 s (`cpu.csv`); `tools/lan/scripts/app-{host,join}.script`.
  - `report DIR`: the game's span and speed, the checksum exchange and its verdicts, the end, frame rate and CPU.
  - `say`, `status` and `checksums` work on app sessions too (the log is `<home>/Logs/game.log` while the app runs).

### 9.6 Evidence (not in git)

- `analysis/lan-sessions/session4-c-mac-hosts/` (4C and 4D in one app run: `lan.pcap`, `game.log`, `frames/`, `legion/c-lan-page.png`), `session4-e-mac-joins/` (4E: also `oos/{mac,legion}.log`, `legion/netcode_hooks.json`), `session4-f-mac-hosts/` (4F, also `legion/netcode_hooks.json`), `session4-g-dc-nokey/` (Direct Connect without a key), `session4-rehearsal-{1,2}/`, `autosave-repro/` (`ee_boot.log`), `autosave-app/` (the fixed app's autosave).
- legion: `C:\EEPort\captures\lan\session4\{c,d,…}\` (screenshots, `*.mkv`, `frida\`), the mailbox progress file and reply.
