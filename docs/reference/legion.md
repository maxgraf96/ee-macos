# `legion`: Windows reference machine

Status: set up; menu, startup and in-game captures taken, 2026-10-14. Scripts: [`windows/`](../../windows/README.md).
The machine runs the **unmodified GOG build** as the Windows reference for PLAN.md §5.2, §5.6 and §6.

## 1. Machine

| | |
|---|---|
| Access | `ssh legion`, key auth, user `<host>\<user>` (admin; SSH sessions get a High-integrity token). Default shell `cmd`. |
| OS | Windows 11, build 26200 (kernel reports 26100.9444), Windows PowerShell 5.1 |
| CPU / GPU | Intel Core Ultra 9 275HX (24 cores). Panel 2560×1600 @ 240 Hz, 144 DPI, driven by the **Intel iGPU** (driver 32.0.101.8331). Also RTX 5070 Ti Laptop and a SudoMaker virtual display adapter. **The game renders on the Intel iGPU** (`igd9trinity32.dll`), not the NVIDIA GPU. |
| Network | Wi-Fi (Intel BE200), `<home LAN, DHCP>/24`, profile **Private**. Ethernet I226-V disconnected. OpenVPN TAP/DCO adapters present, disconnected. **No ZeroTier installed.** |
| Other | Nahimic (A-Volute) audio modules are injected into every process with audio. |
| Interactive user | `<host>\<user>`, session 1 (owner of `explorer.exe`). |

## 2. What is installed where

Everything is under `C:\EEPort\` except Python (user scope) and the registry keys.

| Path | Contents |
|---|---|
| `C:\EEPort\game\Empire Earth\` | GOG base-game folder copied from `original/gog/Empire Earth/` (295 files, 413 MB; tar over SSH) |
| `C:\EEPort\game\goggame-1207658777.script` | GOG's install script (registry actions; read by `setup.ps1`) |
| `C:\EEPort\scripts\` | `setup.ps1`, `launch.ps1`, `session.ps1`, `hidden.vbs` (copies of `windows/`) |
| `C:\EEPort\frida\` | `run.py`, `*.js` (copies of `windows/frida/`) |
| `C:\EEPort\tools\venv\` | Python venv: frida 17.23.3, frida-tools 14.11.0, pefile 2024.8.26 |
| `%LOCALAPPDATA%\Programs\Python\Python312\` | Python 3.12.10 (winget, `--scope user`) |
| `C:\EEPort\tools\sysinternals\` | ListDLLs, Process Monitor, Process Explorer, PsTools (PsExec), Handle, VMMap. EULAs pre-accepted in HKCU. |
| `C:\EEPort\tools\ffmpeg\` | ffmpeg (BtbN win64 build): `ddagrab` screen capture |
| `C:\EEPort\tools\x64dbg\` | x64dbg snapshot 2026-05-27 (`release\x32\x32dbg.exe`) |
| `C:\EEPort\captures\` | capture outputs (`menu-attach`, `spawn`, `spawn2`, `spawn-check`, `jobs/`) |
| built in | `pktmon` for packet capture. No Npcap or Wireshark. |

Tools take about 790 MB. No drivers were installed, there was no reboot, and Defender, UAC and Windows features are unchanged.

### Module hashes on legion
`setup.ps1` checks every run. All 11 canonical modules match PLAN.md §2.4. The GOG extras that Windows friends also have:

| File | SHA-256 |
|---|---|
| `ddraw.dll` ("GOG.com DirectX 1-7 wrapper", 1.6 MB, D3D9-based) | `ec5ab4c9d737b18fc9842ea05382a08c54a032d6a608173369645e9cb0040a8a` |
| `dxcfg.exe` (wrapper settings UI) | `b30a63708da66067675667d596cf1b6dc1f9de7a018eca529647532c51d68280` |
| `dxcfg.ini` (as shipped) | `display=desktop, presentation=windowed, aspect=enabled, scaling=fit, vsync=application, gamma=1.0, anisotropic=application, antialiasing=application` |

## 3. Registry (base game only)

`setup.ps1` recreates what GOG's installer writes for the base game, then applies the option values the game wrote under Wine on the Mac (`original/gog/sssi-wine-user.reg`).

- **`HKCU\Software\SSSI\Empire Earth`**: 60 values, plus `\Game Options` (16 values), copied from Wine with these changes:
  - `Installed From Volume = C:\EEPort\game` (GOG's `{app}`) and `Installed From Directory = \Empire Earth\` (GOG's value).
  - **The game rewrites both values in upper case on exit** (`C:\EEPORT\GAME`, `\EMPIRE EARTH\`). The Wine values were upper case for the same reason.
  - **Resolution 1920×1080** instead of Wine's 1728×1080. 1920×1080 is a standard mode the driver and wrapper enumerate; 1728×1080 probably is not. The wrapper presents windowed at desktop size and letterboxes. Menus render 4:3, pillarboxed.
  - Kept from Wine, though GOG's own defaults differ: `Rasterizer Name = Direct3D`, so `DX7HRDisplay.dll`, our Stage-1 target, is the plugin used. `Game/Texture Bit Depth = 32`.
  - **GOG's installer defaults are `Direct3D Hardware TnL`, 16-bit, 1024×768.** A Windows friend who never changed options therefore runs the **TnL plugin**. This is worth checking per friend before LAN tests (the render thread only, so sync should not be affected).
- **HKLM** (32-bit installer, so the keys sit under `WOW6432Node`, as in the Wine prefix): an empty key `SOFTWARE\WOW6432Node\Stainless Steel Studios\Empire Earth`, and `SOFTWARE\WOW6432Node\Sierra\CDKeys` `EmpireEarth` (REG_BINARY, GOG's key from the script). The AoC and Mad Doc keys were skipped.
- **Not applied:**
  - GOG's `directplay.cmd`: DISM enables DirectPlay, may need a reboot, and base EE does not import dplay.
  - GOG's `firewall.cmd`: see §7.
- Re-run with `setup.ps1 -ResetRegistry [-Width 1728 -Height 1080]`. By default an existing key is left alone, because the game saves options on exit.

## 4. Deploy, launch, capture

Full details are in [`windows/README.md`](../../windows/README.md). In short:

- `windows/deploy.sh` copies the game (if missing), the GOG script and the scripts, then runs `setup.ps1`.
- **Launching from SSH.**
  - SSH runs in session 0, which has no desktop.
  - `launch.ps1 start` registers a scheduled task `\EEPort\EmpireEarth` for the user who owns `explorer.exe`: `LogonType Interactive`, `RunLevel Limited` (like a normal launch), normal priority, battery-safe.
  - The game starts in session 1 with its working directory set to the game folder.
  - `launch.ps1 cleanup` removes the tasks. It has been run; there are no leftover tasks.
- **Frida attaches across sessions from SSH**: `run.py --scripts ...`.
- **Spawn-mode captures** must run in session 1: `launch.ps1 frida '--spawn ...'` runs hidden via `hidden.vbs`.
- **Seeing the screen.**
  - The wrapper presents via D3D9 flip/fullscreen, so GDI and PrintWindow give black or stale images.
  - `session.ps1 -Action grab` uses Desktop Duplication (ffmpeg `ddagrab`) and works.
- **Exiting.**
  - `launch.ps1 stop` posts `WM_CLOSE` and the game exits cleanly (no WER entry, no dialog).
  - **During the intro movies `WM_CLOSE` is ignored**, so retry once the menu is up.
- Startup takes about 2 min to reach the main menu: three Bink movies (Sierra, SSSI, intro), each on its own TSThread plus a Bink decoder thread.
- **Crash dialogs.** None in normal runs. Event Log has 5 `APPCRASH` entries in `frida-agent.dll` (13:38–13:44), all caused by my Frida scripts while developing them (TinyCC and `enumerateImports` bugs, see `windows/README.md` → Gotchas). All are fixed.
  - A failed sampler also left the main thread suspended for a few minutes once. It was resumed, and the sampler can no longer leave threads suspended.

## 5. Results

### 5.1 Display path
- **Plugin choice.**
  - `Rasterizer Name = Direct3D` selects **`DX7HRDisplay.dll`**.
  - `DX7HRTnLDisplay.dll` is also loaded and stays resident, because the plugin scan loads every plugin (§5.3).
  - The render thread is an EXE TSThread (it sets its CW at `EXE+0x10fe36`), which drives `DX7HRDisplay` → GOG `DDRAW.dll` → `d3d9.dll` → Intel `igd9trinity32.dll`.
- **D3D9 device.**
  - The GOG wrapper creates it **on the main thread** with `BehaviorFlags = 0x82` = `D3DCREATE_FPU_PRESERVE | D3DCREATE_MIXED_VERTEXPROCESSING`, adapter 0.
  - It does this twice: once for the movies, once when the menu starts.
  - The wrapper probes for `d3dref9.dll` (absent).
  - **So the wrapper never changes the x87 control word.**
- **DirectDraw cooperative level** (captured at game start, §6). `DX7Rasterizer` calls `DirectDrawCreateEx` on the main thread, then `SetCooperativeLevel` four times:
  - `0x408` (NORMAL | MULTITHREADED)
  - `0x8` (NORMAL)
  - `0x80` (SETFOCUSWINDOW)
  - `0x513` (FULLSCREEN | ALLOWREBOOT | EXCLUSIVE | SETDEVICEWINDOW | MULTITHREADED)
- **Neither `DDSCL_FPUSETUP` (0x800) nor `DDSCL_FPUPRESERVE` (0x1000) is set.** On real D3D7 the default FPU handling would therefore apply. The game guards against it: it re-asserts 53-bit on the main thread 1 ms after device creation, at `EXE+0x1358f5`.
- **The menu → game switch re-creates DirectDraw and D3D9.** D3D9 is again `0x82`.

### 5.2 Modules and base addresses
- **EXE, `sxlrt233.dll` and `binkw32.dll`** load at their preferred bases.
- **Every other game DLL is relocated, to a different place on every run.** Plugin probing loads and unloads DLLs, so the free address space differs each time.

| Module | Size | Preferred | Base, run A (attach at menu) | Base, run B (spawn) | SHA-256 |
|---|---|---|---|---|---|
| Empire Earth.exe (linked 2017-06-23, GOG) | 0x682000 | 0x400000 | 0x400000 | 0x400000 | `957e35d7…065ab` ✓ |
| sxlrt233.dll | 0xe4000 | 0x10000000 | 0x10000000 | 0x10000000 | `ccf6f97d…440b` ✓ |
| Low-Level Engine.dll (2002-09-11) | 0x130000 | 0x10000000 | 0x012d0000 | 0x01410000 | `e3f6a2f6…3214` ✓ |
| Language.dll | 0x31000 | 0x10000000 | 0x01760000 | 0x047d0000 | `2539f8b7…3456` ✓ |
| DX7HRDisplay.dll | 0x18000 | 0x10000000 | 0x03ce0000 | 0x04c20000 | `46a69ac0…ff53` ✓ |
| DX7HRTnLDisplay.dll | 0x1a000 | 0x10000000 | 0x2ff60000 | 0x0ef10000 | `f0c5b16a…b154` ✓ |
| Miles Sound System Mixer.dll | 0x7000 | 0x10000000 | 0x302d0000 | 0x0daf0000 | `6e1a5145…5e09` ✓ |
| TCP Protocol.dll | 0x9000 | 0x10000000 | 0x30ed0000 | 0x0fc20000 | `b695886b…70f0` ✓ |
| mss32.dll | 0x5d000 | 0x21100000 | 0x30800000 | 0x0fa50000 | `d0db426e…87c` ✓ |
| binkw32.dll | 0x59000 | 0x30000000 | 0x30000000 | 0x30000000 | `7ef2f1d2…c0e6` ✓ |
| DDRAW.dll (GOG wrapper) | 0x56f000 | 0x18000000 | 0x0dc90000 | 0x06940000 | `ec5ab4c9…8a` |
| Default.dll | — | 0x10000000 | not resident at menu | (loaded/unloaded during probing) | `ab0e7910…9d3e` ✓ |
| msvcrt.dll (system) | 0xc7000 | | 0x761b0000 | 0x761b0000 | `a1bd0acd64e1a603…` |
| MSVCP60.dll (system) | 0x76000 | | 0x640d0000 | 0x640d0000 | `4b7d8e819274e42f…` |
| d3d9.dll (system) | 0x17a000 | | 0x652d0000 | 0x652d0000 | `87e157e78536c66f…` |

122–144 modules in total. The full lists with paths and hashes are in `C:\EEPort\captures\*/modules.json`.

**Tooling note.** ListDLLs, run from the 64-bit SSH session, shows only the WOW64 side. Frida's `modules.js` is the authoritative 32-bit list.

### 5.3 Loader behaviour (matters for the HLE PE loader)
- **Plugin scan.** `PIMModule::GetModule` (engine) calls `LoadLibraryExA(<game dir>\X.dll, LOAD_WITH_ALTERED_SEARCH_PATH=0x8)` on **every DLL in the game folder**. That covers `binkw32`, the GOG `ddraw`, `Default`, both display plugins, `Language`, the engine itself, the Miles mixer, `Mss32`, `sxlrt233` and `TCP Protocol`.
  - It does this 5 times within the first 2 s.
  - It calls `UMSetFloatingPointPrecision(_PC_53)` after each load, 44 times in total.
- **`Language.dll`** is loaded with plain `LoadLibraryA` by `UResourceFile::UResourceFile`. The path comes out upper-case: `C:\EEPORT\GAME\EMPIRE EARTH\`.
- **Miles** `LoadLibraryA`s 29 providers from `redist\win32\` (`.asi`, `.m3d`, `.flt`), twice, then `DSOUND.DLL`.
- **The GOG wrapper loads itself again** by name (`LoadLibraryA DDRAW.dll`), plus `d3d9.dll` and the absent `d3dref9.dll`.

### 5.4 Threads and x87 control word
I used three independent methods, and they agree:
1. Suspend + `GetThreadContext` sampling every 50 ms (`threads.js`; 1056 rounds at the menu, 2768 rounds from spawn).
2. `fnstcw` executed at every external API call the game makes, via IAT stubs (`iat_census.js`).
3. Hooks on `_controlfp`/`_control87`/`_fpreset` and on the engine's own `UM{Set,Get}FloatingPoint{Precision,RoundingMode}` exports (`fpu_hooks.js`).

Thread roles at the menu:

| Thread (EXE address = return site of its first `UMSetFloatingPointPrecision` call) | Role | x87 CW | Who sets it |
|---|---|---|---|
| main (`EXE+0x283798`, CRT entry) | message pump (432 msg/s), engine critical sections, `timeGetTime`, `memcpy`; creates the D3D9 device | **0x027F** | CRT startup `_controlfp(_PC_53, _MCW_PC)` (`EXE+0x283923`). `UMSetFloatingPointPrecision(_PC_53)` at `EXE+0x135d30`, again after display init (`EXE+0x1358f5`, `EXE+0x2604cf`), and after each plugin load. |
| TSThread `EXE+0x10fe36` | **render** (DX7HRDisplay → DDRAW/d3d9; EIP samples in DDRAW, d3d9, igd9) | **0x007F** | **The game itself**: `UMSetFloatingPointPrecision(_PC_24)` at thread start (`EXE+0x10fe36`). Not D3D. |
| TSThread `EXE+0xce97f` | own message loop: `PeekMessageA` + `Sleep`, ~430 iterations/s; texture/asset worker in-game | 0x027F | `UMSetFloatingPointPrecision(_PC_53)` at start |
| TSThread `EXE+0x15332b` | **simulation** (idle at the menu: `PeekMessageA` + `Sleep` + `SetEvent`, ~430/s) | 0x027F | same |
| TSThread `EXE+0x111134` | input (`ISMouse`), ~35/s | 0x027F | same |
| TSThread `EXE+0x12711d` | music intensity (`SEConductor::SetIntensity`) | 0x027F | same |
| TSThread `SEConductor::EventHandler` | music (DirectMusic) | 0x027F | same, engine |
| TSThread `SESoundManager::Scavenger` | sound (Miles; `AIL_sample_status`) | 0x027F | same, engine |
| TSThreads `NESynchronousClient::Main` / `::Receive` | network | 0x027F | same, engine |
| TSThread `DX7HRDisplay+0x287f` (×3, during intro) | Bink movie player, each with a `binkw32` decoder thread | 0x027F | `UMSetFloatingPointPrecision(_PC_53)` |
| system threads (DirectSound, DirectMusic, DirectInput, winmm, Miles timer, Intel driver ×~30, thread pool) | | 0x027F | Windows default |

What this means:
- **0x027F** = precision 53-bit, round to nearest, all exceptions masked. **0x007F** = precision 24-bit, round to nearest, all masked.
- **The plan's expectation is confirmed:**
  - Every game thread except render runs at **0x027F**, including the main thread and all EXE TSThreads, so whichever one is the simulation.
  - The render thread runs at **24-bit**.
- **The cause of the 24-bit mode is different from the plan's assumption.** The plan assumed D3D7 sets it. In fact **the game sets 24-bit itself on its render thread**, and the GOG wrapper preserves the FPU state (`D3DCREATE_FPU_PRESERVE`).
- **Implications for the port:**
  - The runtime must implement `_controlfp` per thread exactly (`UMSetFloatingPointPrecision` is `_controlfp(p, _MCW_PC)`).
  - The D3D7 HLE must *not* change the CW.
  - The game re-asserts 53-bit after display init, which suggests it was written to undo real D3D7's FPU change.
- **The rounding mode is never set** (no `UMSetFloatingPointRoundingMode` calls) and stays round-to-nearest. No `_control87`, `__control87_2`, `_fpreset` or `_clearfp` calls were seen.
- **Hot polling.**
  - EXE TSThreads, the render thread and the main thread call `UMGetFloatingPointPrecision` and `UMGetFloatingPointRoundingMode` in their loops.
  - That is ≈ **4,200 `_controlfp(0,0)` calls/s at the menu**, returning `0x9001F` (PC53) or `0xA001F` (PC24).
  - It looks like a per-iteration FPU-state check. The HLE `_controlfp` must be cheap.
- **MXCSR** (not used by game code) is 0x1F80 on most threads. Main and render threads show sticky PE/UE flags (0x1FA0/0x1FB0) set by system and driver SSE code. This is irrelevant to the x87 simulation.
- **The simulation thread is the TSThread `EXE+0x15332b`, at 0x027F** (in-game, §6).

### 5.5 API census at the main menu (60 s)
- **Method.** IAT stubs on all **762 import slots / 512 distinct names** of the loaded game modules that come from non-game DLLs. This includes the 23 ws2_32 and the wsock32 imports by ordinal that Frida's own import enumeration misses.
- **What it counts.** Only calls *made by game code*, not by the wrapper or drivers. COM methods (DirectDraw/D3D, DirectInput, DirectMusic) are vtable calls and are not counted here.
- **Result.** **Only 26 of the 519 inventoried externals were called at the menu.**

| # | DLL | Function | calls/s | from | threads |
|---|---|---|---|---|---|
| 1 | kernel32 | `LeaveCriticalSection` | 5,899 | engine, EXE | main, render, T-111134, sound |
| 2 | kernel32 | `EnterCriticalSection` | 5,899 | engine, EXE | main, render, T-111134, sound |
| 3 | kernel32 | `GetCurrentThreadId` | 4,757 | engine | main, render, T-111134, sound |
| 4 | msvcrt | `_controlfp` | 4,239 | engine (`UMGet*`) | main, T-ce97f, T-15332b, render |
| 5 | kernel32 | `SetEvent` | 2,005 | engine | main, render, T-15332b |
| 6 | kernel32 | `Sleep` | 1,687 | EXE, engine | T-ce97f, T-15332b, main, render |
| 7 | user32 | `PeekMessageA` | 1,686 | engine | T-ce97f, T-15332b, main, render |
| 8 | kernel32 | `WaitForSingleObject` | 1,573 | engine | main, render |
| 9 | winmm | `timeGetTime` | 1,326 | engine | main, render, net |
| 10 | msvcrt | `memcpy` | 1,296 | engine | main |
| 11 | msvcrt | `operator new` (`??2@YAPAXI@Z`) | 1,143 | DX7HRDisplay, engine | render, main, net |
| 12 | msvcrt | `operator delete` (`??3@YAXPAX@Z`) | 1,143 | DX7HRDisplay, engine | render, main, net |
| 13 | kernel32 | `InterlockedIncrement` | 787 | engine | main, render |
| 14 | kernel32 | `InterlockedDecrement` | 787 | engine | main, render |
| 15 | kernel32 | `ResetEvent` | 786 | engine | main, render |
| 16 | msvcrt | `strcpy` | 434 | engine | main, net |
| 17 | user32 | `DispatchMessageA` | 432 | engine | main |
| 18 | user32 | `TranslateMessage` | 432 | engine | main |
| 19–26 | msvcrt / kernel32 / mss32 / user32 | `_mbslen`, `_mbsnbcnt`, `WaitForMultipleObjects`, `AIL_sample_status`, `strcat`, `LoadStringA`, `vsprintf`, `PostThreadMessageA` | ≤ 2 | | net, sound |

What the census shows:
- **The menu is dominated by engine threading primitives** (`TSCriticalSection`, events, per-loop FPU checks) and the message pump.
- **No x87 math imports run at the menu.** No `_ftol`, `sin`/`cos`, `floor`, etc.: the menu simulation is idle.
- In-game, see §6.

### 5.6 Other surprises
- **First random-map setup triggers a Windows Firewall prompt.**
  - Clicking *Single Player → Play Random Map* makes the game open a listening socket, even for a single-player game.
  - Windows first created **Block** rules (`empire earth`, Inbound, TCP+UDP, Private).
  - The user then answered *Allow* (§7).
- **The game draws its own cursor from DirectInput deltas.**
  - `SetCursorPos` / absolute moves do not move it.
  - Automation has to send relative `SendInput` motion (`session.ps1 -Action move`) and click with `-Here`.
  - Legacy DirectInput computes the deltas from the system cursor, which is accelerated and clipped at screen edges. So `move` re-centres the system cursor first, and the gain still depends on speed. Navigate closed-loop with `grab` (§6).
  - Clicks are taken at the game cursor.
- **Game Setup defaults** (from the registry): Random Map, Tournament, Small, Continental, Medium, Standard speed, 3 wonders, cheat codes on, 1 human + 1 computer.

## 6. In-game capture (2026-10-14, 14:22)
- **Setup.**
  - `launch.ps1 start`, ESC skips the intro movies, and the menu appears after about 30 s.
  - Automated route: Single Player → Play Random Map → Start Game. Defaults: Small, Continental, Deathmatch resources, Random Epoch, 1 Medium AI, Standard speed.
  - No unexpected dialogs. The game exited cleanly with `WM_CLOSE` from inside the match.
- **Captures** (under `C:\EEPort\captures\`):
  - `transition` (900 s, attached from the menu through game start: `fpu_hooks`, `threads`, `sync_hooks`)
  - `ingame` (60 s, after 60 s of play: `modules`, `threads`, `iat_census`)
  - `ingame-internal` (20 s, `iat_census internal=true`, so EXE → engine calls are counted too)
- **Cursor automation.** It works only if the system cursor is re-centred before each relative move. `session.ps1 -Action move` now does that, because legacy DirectInput derives deltas from the clipped system cursor. The gain is speed-dependent, so it is closed-loop with `grab`: about 6 iterations per screen.

### Threads in-game (EXE thread = return site of its `UMSetFloatingPointPrecision`)

| Thread | Role (evidence) | CW | Rate |
|---|---|---|---|
| TSThread `EXE+0x15332b` | **Simulation / world tick.** Per tick: `TSEvent::Wait` → `TSReadWriteLock::WriteLock` → `NESynchronousServer::GetCurrentCommunicationsTick` → `TSEvent::Set`. Heavy `GETransformation::Apply` (2.7k/s), `GETerrainMesh::GetMeshZ` (1.7k/s), `__RTDynamicCast` (1.5–2k/s), `new`/`delete` (3.8k/s). | **0x027F** throughout | **~41 ticks/s** |
| main `EXE+0x283798` | message pump (485/s), takes the world **read** lock 454/s, UI (`UIManager::GetCurrentForm`) | 0x027F | |
| TSThread `EXE+0x10fe36` | render (DX7HRDisplay; GDI text via `GetDC`/`GetTextExtentPoint32A` ~92/s); world read lock | **0x007F** throughout | ~83 frames/s |
| TSThread `EXE+0xce97f` | texture/asset worker: `GEUnpack{Red,Green,Blue,Alpha}` 1.9k/s each, `memcpy` 5.2k/s, `FindFirstFileA`/`GetFileAttributesA`, reloads plugin DLLs at game start | 0x027F | |
| TSThread `EXE+0x111134` (re-created at game start) | input (`ISMouse::RefreshXY`) | 0x027F | 35/s |
| TSThread `EXE+0x12711d` | music intensity (`SEConductor::SetIntensity`) | 0x027F | |
| `NESynchronousServer::Main` / `Send` / `Xfer` (new at game start) | network server: single player is hosted locally too | 0x027F | |

### Floating-point math on the sim thread (calls/s, all at CW 0x027F)
- **msvcrt:** `sqrt` 2,914, `fabs` 24.
- **Engine wrappers:** `UMArcTan2` 1,501, `UMArcCos` 631, `UMCos` 134, `UMArcTan` <1.
- **`_ftol` is imported but was never called in-game.** Neither was any `_CI*` intrinsic (`_CIsin`, `_CIpow`, …) or msvcrt `sin`/`cos`.
  - So float→int conversion is not done through msvcrt's `_ftol` here. It is either inline or a static routine.
  - The sampler saw the main thread at **0x0E7F** (53-bit, RC = truncate) in 2 of 17,339 samples, typical of a `_ftol`-style temporary RC switch.
  - This needs a Ghidra check: those samples may also come from Frida's own hook code, which ran on that thread.
- **Render thread** (24-bit): `sqrt` 4,261/s, `UMArcTan2` 667/s, `sin` 35/s (`DX7HRDisplay`).
- **No thread changed its CW in-game.** That was checked by sampling (17k samples per thread over 15 min) and at every API call. No `_controlfp` sets occurred except at thread start and plugin reloads.

### API census in-game vs menu (60 s, external imports, calls/s)
- **64 distinct externals called in-game, against 26 at the menu.**
- **New hot entries:**
  - `operator new` / `delete` 18.4k each (menu 1.1k)
  - `memcpy` 9.6k
  - `sqrt` 8.8k
  - `__RTDynamicCast` 2.2k
  - `_mbscmp` 559
  - GDI text: `SelectObject` 185, `GetDC` / `ReleaseDC` / `GetTextExtentPoint32A` 92 each
  - `sin` 35, `atoi` 24, `fabs` 19, `FindFirstFileA` 2
- **Still hot from the menu:** `Enter/LeaveCriticalSection` 9.5k, `GetCurrentThreadId` 8.8k, `_controlfp` 3.3k, `timeGetTime` 2.7k, `PeekMessageA` 1.1k.
- **Miles is barely called:** `AIL_sample_status` 3/s.
- **With engine imports counted** (2,363 IAT slots), the top engine calls are:
  - render: `GETransformation` ctor 5.9k/s, `GETexture::Promote`/`Dereference` 5.3k/s, `TSCriticalSection` 4.9k/s
  - sim: `GETransformation::Apply` 2.7k/s

### Sync / checksum exports
- **`sync_hooks.js` (new) hooked 13 `ESChecksum*` / `ESVerify` exports. In 15 min, including about 6 min of single-player play, they had 0 calls.**
- The EXE imports `NEMWorldDatabaseChecksumResponse`, `NESynchronousServer::LogChecksumResponse` and `FSRestorable::GetChecksum`.
- **So world checksums are exchanged only in multiplayer.** The checksum-stream oracle has to be captured in a LAN game; run `sync_hooks` during the §7 session.
- The sim tick (~41/s) is visible as `GetCurrentCommunicationsTick` on the sim thread. That is where a per-tick hook can sit.

## 7. LAN capture: what's still needed

### Firewall state on legion
I read the rules; I did not change them. The user clicked **Allow** on the prompt, and these rules now exist (they were Block before):

| DisplayName | Direction | Action | Profile | Protocol | Ports | Remote | Program | Edge traversal |
|---|---|---|---|---|---|---|---|---|
| `empire earth` (`TCP Query User{C6BC4287-…}`) | Inbound | Allow | **Private** only | TCP | any | any | `C:\eeport\game\empire earth\empire earth.exe` | DeferToUser |
| `empire earth` (`UDP Query User{46B0DCA2-…}`) | Inbound | Allow | **Private** only | UDP | any | any | same | DeferToUser |

What the existing rules cover, and the gaps:
- **Covered.**
  - On the Private Wi-Fi (home Wi-Fi), inbound UDP broadcast discovery to 33334/33335 and TCP/UDP game traffic on 33334–33336 are allowed for this exact exe path. Path matching is case-insensitive.
  - Outbound is allowed by default.
  - This is enough for **legion ↔ Mac on the same physical LAN**.
- **Gap 1: ZeroTier.**
  - ZeroTier is not installed on legion. Installing it adds a virtual network adapter, which is a driver, so it needs the user's OK.
  - Windows usually classifies a new ZeroTier network as **Public** ("Unidentified network"), and the rules are Private-only, so game traffic over ZeroTier would be blocked.
  - Fix, either:
    - set the ZeroTier network to Private (`Set-NetConnectionProfile -InterfaceAlias 'ZeroTier One [<id>]' -NetworkCategory Private`), or
    - add a Public-profile rule scoped to the ZeroTier subnet (below).
- **Gap 2: scope.** The rules allow *all* ports for the program. That is fine on a home LAN, but broader than needed (GOG's own `firewall.cmd` is broader still: all profiles).
- **Gap 3: path-bound.** If the game folder moves, the rules no longer apply.

### Proposed rules (not applied; for the user to run in an elevated PowerShell if wanted)
```powershell
$exe = 'C:\EEPort\game\Empire Earth\Empire Earth.exe'
# Only if ZeroTier is installed and its network should stay Public: allow EE ports from the ZeroTier subnet only.
New-NetFirewallRule -DisplayName 'EE LAN UDP (ZeroTier)' -Direction Inbound -Action Allow -Profile Public `
  -Program $exe -Protocol UDP -LocalPort 33334-33336 -RemoteAddress 10.147.0.0/16   # use the real ZT subnet
New-NetFirewallRule -DisplayName 'EE LAN TCP (ZeroTier)' -Direction Inbound -Action Allow -Profile Public `
  -Program $exe -Protocol TCP -LocalPort 33334-33336 -RemoteAddress 10.147.0.0/16
# Optional tightening on Private (then the user's any-port 'empire earth' rules could be disabled):
New-NetFirewallRule -DisplayName 'EE LAN UDP' -Direction Inbound -Action Allow -Profile Private -Program $exe -Protocol UDP -LocalPort 33334-33336
New-NetFirewallRule -DisplayName 'EE LAN TCP' -Direction Inbound -Action Allow -Profile Private -Program $exe -Protocol TCP -LocalPort 33334-33336
```
The port list comes from PLAN.md §5.6 and is not yet verified on the wire. The capture below will confirm or correct it.

### Capture plan
1. **Topology.** Choose one:
   - **(a)** the Mac and legion on the same Wi-Fi: works today, no installs; or
   - **(b)** ZeroTier on both: needs the ZeroTier install on legion, which is a driver, so it needs the user's approval. This matches how the group plays.
   - **Peer.** The Mac side is the Wineskin build `/Applications/Empire Earth.app`: the same GOG base-game files under `drive_c/GOG Games/Empire Earth Gold/Empire Earth`.
2. **Capture on legion** (inbox `pktmon`, elevated SSH is enough):
   ```
   pktmon filter remove
   pktmon filter add EE33334 -p 33334
   pktmon filter add EE33335 -p 33335
   pktmon filter add EE33336 -p 33336
   pktmon start --capture --pkt-size 0 --file-name C:\EEPort\captures\lan\lan.etl
   ...  host on one side, join from the other, play ~2 min, quit  ...
   pktmon stop
   pktmon etl2pcap C:\EEPort\captures\lan\lan.etl --out C:\EEPort\captures\lan\lan.pcapng
   ```
   - Copy the `.pcapng` to the Mac (`scp`) and open it with Wireshark or tshark there.
   - Also capture on the Mac with `tcpdump -i en0` (or the ZeroTier `feth*` interface) `udp portrange 33334-33336 or tcp portrange 33334-33336`, so both ends are recorded.
   - Verify that pktmon sees Wi-Fi frames on this build. If it doesn't, use Ethernet: the I226-V port exists but is unplugged.
3. **During the session**, run `iat_census` + `threads` on legion for the netcode thread picture: `TCP Protocol.dll` imports, `NESynchronousClient`.
4. **Check before the test.** Both sides need the same `Network Adapter` index semantics, and `Debug OOS Handling = 1` is already set on legion (from Wine).

### Session 1: legion ↔ EE-VM (2026-10-14, 17:10–17:40)
The plan above changed in two ways. The Mac's Wine build can't be a peer, and the second peer became a VM on legion.
- **The Wine build can't show the lobby.**
  - Clicking Multiplayer leaves the main-menu frame frozen. The lobby is running: button-click sounds still play, and `winedbg info wnd` shows its visible child window `WndClass0` under `SSSI Empire Earth`.
  - The GOG `ddraw.dll` wrapper's D3D9 surface stays on top of the GDI child. This happens on Wine 10.0 (Wineskin) and on Wine 11.0 (Homebrew) alike.
  - With Wine's builtin ddraw, the game exits with code 3 at startup.
- **The second peer is EE-VM** (`windows/vm/`): Windows 11 build 26300 in VMware Workstation 25H2 on legion, bridged to the Wi-Fi, at `192.168.1.192`.
  - Both peers run on the same Core Ultra CPU.
  - For VMware, legion needed *Windows Hypervisor Platform* (memory integrity keeps Hyper-V running) and a restart.
  - In-game mouse: VMware *Preferences → Input → Optimize mouse for games: Always*. Without it, the DirectInput cursor sticks to the screen edges.
- **What was recorded.** Captures are in `analysis/lan-captures/session2/{legion,eevm}/`.
  - The usable match had the VM hosting, with legion joining from the LAN list.
  - `pktmon` ran on both machines, filtered to the peer IP, both broadcast addresses and 8585/33334–33336, with `--pkt-size 0`. legion's file also holds all of the VM's internet traffic, because bridged frames cross legion's stack: 147k frames against the VM's 12.7k.
  - `netcode_hooks` (`wire=false`) and `sync_hooks` ran on the host. `run.py --every 15 --stop-file …` kept the snapshots safe through game exits.
- **Results.**
  - `tools/lan/eelan.py` decodes both captures identically: 897 `NEMCommunicationsTick`, 817 `NEMWorldDatabaseChecksumResponse`, the chat, build and gather orders, and a pause.
  - **Sync oracle.** The host's own world checksum (Frida, EXE `0x545ebf`) equals the client's wire response on **817/817 ticks** (CT 40–857). There are about 2.85 checksums/s, all from one host thread, with `flags=1`. The host tally (`LogChecksumResponse`) has 2 slots (0 and 254), equal on every tick.
  - The `ESChecksum*` exports had **0 calls in multiplayer too**. The world checksum is the EXE's own function only.
- **Gotchas found.**
  - **Installing VMware moved the game's Default adapter.** It became `VMnet8` (192.168.157.1), so legion's lobby broadcasts went to the VMware host-only network and the VM never saw legion's games.
    - After disabling VMnet1/VMnet8 (`Disable-NetAdapter`; the bridged VM doesn't use them), Default was fine.
    - Windows players with VPN or virtualisation adapters will hit the same problem. The adapter choice made in Options didn't persist across the crashes below.
  - **legion crashed 3/3 as LAN host at match start.** The setup was: VMnet adapters enabled, Wi-Fi picked explicitly in *Choose Network Adapter*.
    - Two of the three runs had Frida attached; the crash is the game's own.
    - Each was an access violation in SysWOW64 `ntdll` heap code, with the exception address near the `RtlEnterCriticalSection` export.
    - The main thread's stack ran through `TCP Protocol.dll` +0x1cd8 / +0x484c / +0x7370 into msvcrt. +0x1cd8 lies just before `NPIMEnumerateNetworkAdapters` (+0x1ce0), at the end of the adapter-selection routine (netcode.md §3.4).
    - A full dump is at `C:\EEPort\captures\dumps\`. WER LocalDumps is now on for `Empire Earth.exe`. The triage script is at `analysis/lan-captures/session2/dumpscan.py`.
    - The VM, with one adapter and Default selected, never crashed as joiner or as host. The trigger, adapter count or explicit choice, is not isolated yet.
  - With the TCP Protocol.dll wire hooks in place, those hooks were the first suspect for the crash; they turned out not to be the cause. `netcode_hooks.js` now attaches them only when the DLL is loaded (module observer). `wire=false` skips them.

## 8. Open items
- Ghidra: how the EXE converts float to int (no `_ftol` calls in-game; the transient CW 0x0E7F on the main thread).
- Add a COM vtable census for DirectDraw7/D3D7, DirectInput and DirectMusic. This was planned for Phase 1.
- Per-tick hook on the sim thread (`GetCurrentCommunicationsTick`, ~41/s) to timestamp world ticks, for PLAN §6.4.
- LAN (§7 session 1):
  - a legion-hosted match, with legion's own Frida world stream, now that VMnet1/8 are off;
  - isolate the host crash (adapter count vs explicit *Choose Network Adapter*) and decompile `TCP Protocol.dll` +0x1bee–0x1cd8 / +0x484c from the dump;
  - a longer match for the oracle.

### Phase 1 confirmation runs (2026-10-14, 17:43–18:25)
Outputs are on legion under `C:\EEPort\captures\` and copied to `analysis/legion-captures/` (git-ignored).

| Capture | Status |
|---|---|
| `com-spawn` | `com_census`, spawned, 150 s, intro skipped to the menu |
| `com-menu` | 120 s at the menu |
| `com-ingame` | `com_census` + `iat_census`, 60 s, after 60 s of a random map (Small, 1 AI) |
| `music-trace` | `music_hooks`, 240 s in-game, plus `music-trace-loopback.wav` (250 s WASAPI loopback) |
| `music-sched` | intensity schedule, 200 s, plus `music-sched-loopback.wav` |
| `census` | `code_census`, **main menu only** (600 s, never reached a game) |

Caveats and gaps:
- **Loopback audio may be contaminated.** It records the whole default output device, so any other sound on legion is mixed in. The VMware VM `EE-VM` was running, and Nahimic effects are active.
- **The code census misses gameplay.**
  - Results: check_census reports 0 blocks outside DB functions and 0 DRM blocks. Functions run: 61 (EXE), 117 (engine), 36 (DX7HRDisplay), 1 (Miles mixer).
  - The run would not finish on its own. After the final snapshot, `stop()` (Stalker unfollow) never returned, so I killed the client and the game survived.
  - A second, in-game attempt could not start: `script.load()` timed out while Stalker followed all ~50 threads.
  - Navigation was paused anyway, because the user was using legion: VS Code was in the foreground, and an idle check showed recent input.
  - **Still to do:** an in-game `code_census` run.
    - Attach at the menu with a longer load timeout, or follow only the game's own threads.
    - Run it while nobody is using legion.
- **Input safety.**
  - `session.ps1` now refuses to send input unless the game window is in the foreground (`Ensure-Foreground`), so a focused VM console or editor never gets keystrokes.
  - `-Action idle` reports time since the last input.
  - `launch.ps1 record '<s> <wav>'` runs `loopback_record.py` in the user session; soundcard and numpy are in the venv.
- **Known crash (from the LAN session, not these runs).** With the VMware host adapters (VMnet1/VMnet8) present and **Wi-Fi explicitly chosen in Network Adapter Options**, the game crashed at match start as LAN host: an access violation in the ntdll heap via `TCP Protocol.dll` +0x1cd8/+0x484c/+0x7370.
  - The VMnet adapters are now disabled. Don't change Network Adapter Options on legion.
  - Dumps: `C:\EEPort\captures\dumps\`, where WER LocalDumps is enabled for `Empire Earth.exe`.
