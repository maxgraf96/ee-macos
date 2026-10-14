# Research notes: NeoEE and the Empire Earth community (2026-10-14)

Gathered by a research agent, then spot-checked: the setup script was read directly, and the Steam release was confirmed via the store API. **[V]** = verified against a source, **[I]** = inference.

## What NeoEE is
- **What it re-creates.** A free, non-profit re-creation of the Sierra WON lobby, claiming "100% of the original lobby functionality". Activision shut WON down in Nov 2008. [V] https://neoee.net/about
- **Original team.** Ghost and Omega (server admins, ex-Save-EE), RealForce (installer and patching), Jodocus (lobby re-creation). [V]
- **Predecessor.** Save-EE (2008) ran a VB.NET lobby, now GPL-3.0: https://github.com/save-ee/save-ee-lobby [V]
- **Launch.** NeoEE launched 29 Nov 2013. [V] https://neoee.net/news
- **Current maintainers.** In Mar 2023 the **Empire Earth Reborn (EE-modders)** team got server access; on 30 Dec 2023 the server moved to their hosting. [V]
- **Client patch.** 2.0.0.5 (base) / 1.0.0.2 (AoC), Nov 2016; no client patch since. [V] https://neoee.net/update
  - Delivered via the *Empire Earth Community Setup*: latest v1.7.2 (Dec 2023); installer file last modified 26 May 2025. [V] https://github.com/EE-modders/Empire-Earth-Setup
- **Reported versions.**
  - NeoEE builds report 2.0.0.3750 / 1.0.0.7590.
  - Originals report 2.0.0.3466 / 1.0.0.2657.
  
  [V] https://empireearth.eu/help/
- **Live server.** `titan.neoee.net:10005` status was `5.1.5 Nautilus`, ONLINE, 24 players when queried. [V]
- **Platforms.** Windows 7 SP1 → 11, plus Win11 ARM64. No official macOS/Linux. [V] https://empireearth.eu/download/
- **Ownership (honour system).** Download requires confirming you own a legal copy, and you're told "DO NOT SHARE the Setup". [V]
- **Rights holder.** Rebellion owns the IP. *Empire Earth: Gold Edition* came out on Steam on 26 May 2026, Windows only. [V via Steam store API]

## Components
**Replaced original binaries:**
- `Empire Earth.exe` / `EE-AOC.exe`: NeoEE variants ~11.4–12.1 MiB vs 6.04 MiB.
- `Low-Level Engine.dll`, `TCP Protocol.dll`, `Language.dll`.
- Admin/User EXE builds differ in where CD keys are read from (HKLM vs HKCU). [V] setup changelog

**Added:** `neoee.dll`, `NeoEE.cfg`, `WONLobby.cfg`, `zzWONVersion.cfg`, `_wonkver.pub` (WON key-verification public key), `NeoEE Updater/NeoEEUp.exe`.

**Unchanged originals:** `DX7HRDisplay`/`DX7HRTnLDisplay`, Miles (`Mss32.dll` + ASI), `binkw32.dll`, `sxlrt233.dll`. BASS is used only for the installer's music.

**Graphics wrapper choices:**
- DDrawCompat 0.5.1 (dx7), GOG wrapper (dx9), dgVoodoo 2.82.1 (dx11/12).
- Under Wine the setup forces `Rasterizer Name=Direct3D` (the non-T&L path).

**dreXmod (yukon aka drex, closed source):** resolution and camera; v3 adds ranking, "anti cheat", lobby themes, mod presets, 10-player lobbies. The site lists v3.6. https://empireearth.eu/drexmod

**Reborn.dll:** hard-coded GOG EXE addresses; disabled in the setup. https://github.com/EE-modders/Reborn-DLL

**Content:** "no gameplay changes". Adds variants (Hyperactive/Hybrid/Extreme), "Unlimited"/"None" resources, Omega maps, RMS packs, and a "Gate crash" bug fix. https://neoee.net/update

**EEStats.dll:** GPL-3.0 opt-in telemetry covering OS/hardware, Wine detection, location and the EXE's SHA-1, sent to api.empireearth.eu. https://github.com/EE-modders/Empire-Earth-Stats

**How add-on DLLs load:** dropping them next to the EXE is enough. [I] The engine's plugin scan loads every DLL in the game folder (`PIMModule::sDirectory`).

**Open source:**
- Setup (Inno, GPL-3.0), launcher (C#, GPL-3.0; WON/NeoEE critical parts withheld), EELibrary (MinHook mod loader), EE-DLL-Template, EEDiscord, EEStats.
- **Not open:** the NeoEE server, the client patches, `neoee.dll`, `authtools` (CD keys), dreXmod. https://github.com/EE-modders

## Stance and contacts
- Jodocus (2018) on Mac: "Just install Windows on an extra partition." http://www.save-ee.com/viewtopic.php?t=12086
- Jodocus (2020): no VMs; CD keys use "some sort of hardware-identification" so cheaters can be banned. http://www.save-ee.com/viewtopic.php?t=89373
- The Reborn team publishes binary-patching code and says "we have everything to benefit by sharing". The setup README asks modders not to create new game versions "to avoid fracturing the community".
- Contacts: Reborn Discord https://discord.com/invite/BjUXbFB · NeoEE Discord https://discord.gg/7ujua95 · forum save-ee.com · support@neoee.net

## Multiplayer
- **Lobby:** the game's WON lobby pointed at `titan.neoee.net` (same IP as api.empireearth.eu).
- **CD keys:** `authtools.dll` `generate_cdkeys` against `neoee.net:10003`, written to `HKLM`/`HKCU\Software\Sierra\CDKeys`. `WONLobby.cfg` has `CDKeyCheck`.
- **Hosting:** the host is a micro game server on TCP/UDP 33334–33336.
- **RIP hosting:** firewall server 10002, chat 10005, relay 33340, UPnP, UDP hole punching (PortRange 20, TTL 2).
- **Version gate:** joining requires the same version; changes to `Data\db`, `Data\Models` or game DLLs change it.
- **Lockstep:** the setup cleans `OOS *.log` files, so there is lockstep with out-of-sync detection.
- **WON SDK source** (Half-Life-oriented, unclear provenance): https://github.com/shefben/world-opponent-network

## Existing Mac/Linux efforts
- **00xJS/Empire-Earth** (Sept 2026, no licence): https://github.com/00xJS/Empire-Earth
  - Approach: GOG build on Apple Silicon with Wine 11 + Rosetta, D7VK → DXVK → MoltenVK, ddraw + `version.dll` proxies, a wow64cpu fix, and x87 hot paths moved to SSE2 with verified identical results.
  - Single player only; patches check exact GOG bytes.
  - Its source documents the measured x87 control words:
    - **simulation thread: 53-bit, round-nearest (`0x027f`)**;
    - render thread: 24-bit.
- **Linux AppImage:** needs native DirectMusic DLLs, because Wine's built-in DirectMusic crashed. https://github.com/lozza/Empire-Earth-Linux-Appimage
- **Lutris:** slow menu under NeoEE, 1920×1080 menu cap, T&L problems on NVIDIA, CD-key issues. https://lutris.net/games/empire-earth-gold-edition/
- **empireearth.eu help:** `winetricks directmusic` fixes crashes after a few minutes of play; `ShowGUI=false` in `NeoEE.cfg` fixes crashes when creating a game.
- **Porting Kit (2019):** LAN/direct connect worked between Macs, but the NeoEE patch errored.
- **CrossOver:** rated "Limited Functionality" (25.1.1).

## Reimplementations and formats
- **No decompilation or engine reimplementation exists.** RE knowledge lives in:
  - Reborn-DLL and EELibrary;
  - Cheat Engine tables (Hexorg/CheatEngineTables);
  - j-frei/EmpireEarthZoomChanger, povka/EmpireEarthArchipelago;
  - 00xJS.
- **SSA:** magic `rass`, PKWARE DCL implode. Tools: EE Studio II (Python, GPL-3.0), SSA-tool.
- **SST textures:** SST-tool/viewer, Studio II.
- **CEM models:** CEM-tool (Blender), Rust `cem`/`cemconv` (MIT): https://github.com/coderbot16/cem
- **Scenarios:** lump structure, DCL-compressed: https://github.com/coderbot16/scn/wiki/EE1-and-EEAOC-Format
- **DB files:** DB Editor (Java, GPL-3.0). File-type index: https://github.com/EE-modders/Empire-Earth-toolbox
