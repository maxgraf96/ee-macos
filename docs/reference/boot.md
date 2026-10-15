# Booting the whole game: `ee_boot`

Status: Phase 3, workstream P3-A (exe-boot), 2026-10-14. The GOG `Empire Earth.exe` and all eight DLLs, translated, boot natively on arm64 macOS **in strict mode, headless, to the main menu's message loop**, render the main menu at 1024×768 through the kit's DirectDraw 7 (P3-B, software renderer) with the kit's user32/gdi32 text in the system's Tahoma (P3-C), and exit cleanly on `WM_CLOSE`. The menu shows the version **`v2.00.2974`**, the data checksum both Windows peers sent (netcode.md §8) [measured]. Sound and input are null; temporary stand-ins remain only for audio (Phase 4) and one user32 behaviour (§5). Kit `ee` 3c71e54 + `feat/boot`. Context: [phase3.md](../phase3.md), [build.md](build.md), [legion.md](legion.md).

Labels: **[measured]** = output of the commands here (M4 Max, macOS 15.5); **[static]** = read from the binaries or the DB; **[inferred]** = reasoning.

---

## 0. Try it: the windowed game

```sh
cd /Users/max/Code/neoee-macos          # this repository, with the kit at an `ee` that has feat/boot
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r kit/requirements-dev.txt   # once
.venv/bin/python tools/setup.py --install "original/gog/Empire Earth"   # once: link + write the nine modules' inputs (no Ghidra)
.venv/bin/python tools/run.py            # seed the registry, translate (when stale, ~5 min), build, open the app
```

`tools/run.py` builds `build/EmpireEarth.app` (the kit's SDL host, `tools/build.py --target app`) and opens it. It rewrites a module's `analysis/kit/` inputs when they are missing or older than the DB (`tools/inputs/export.py`, [build.md](build.md) §2), and translates again when a module's generated code is missing or older than its inputs or the kit's translator; otherwise it rebuilds only what changed, and `open build/EmpireEarth.app` works too. The registry and everything the game writes live in `~/Library/Application Support/EmpireEarth` (`--user DIR` for another place); `tools/boot/seed_user.py` seeds the registry with legion's options and the CD key from your own GOG install script (it finds the Wineskin build's, or pass `--gog-script`).

In the game: click in the window to capture the mouse (the game draws its own cursor from relative motion); Escape skips the intro movies (~2 minutes otherwise); hold Escape to release the mouse; Command-Q quits through the game's own exit. Display is Metal at the game's 1024×768 menu mode in a 4:3 window. **No audio yet** (Phase 4), and Multiplayer's networking is Phase 5.

Smoke test [measured, 2026-10-15]: the window opens with focus, the three intro movies play on Metal at 640×480, the main menu renders at 1024×768 (frame dumped with `RECOMP_D3D7_DUMP_DIR`, which writes every presented frame: ~3 MB each, 40 GB in five minutes, so stop the app soon after the menu appears), and SIGTERM / Command-Q ends in `ExitProcess(0)`. DirectInput creates the keyboard and mouse and acquires both on activation (headless trace: 6 `Acquire` → DI_OK, 1 → S_FALSE). Clicking through the menus is the player's to try: input could not be driven from here.

## 1. How to run headless

```sh
# once: link the installation and export the translation inputs for all nine modules
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth"
# once: the player's registry (CD key from their GOG install script; never printed)
.venv/bin/python tools/boot/seed_user.py [--gog-script PATH] [--width 1920 --height 1080]
# translate everything and build the host
.venv/bin/python tools/build.py --regenerate --cmake-target ee_boot
# boot it
build/recomp/ee_boot [--seconds 120] [--trace FILE] [--frames DIR [--frame-every N]] [--user DIR] [--game DIR] [--play-movies] [--no-strict]
# the test: strict boot to the main menu loop with a fresh user directory
.venv/bin/python tools/test.py --boot
```

- **`--user DIR`** is the player's data, `~/Library/Application Support/EmpireEarth` by default: `registry.json` (the kit's registry store, `RECOMP_REGISTRY`) and the writable tier of the file overlay (`RECOMP_PROFILE_DIR`, kit `mods/overlay.cpp`). Reads prefer it, every write lands in it, and a game file opened for writing is copied up first, so the GOG folder is never modified. The kit also keeps `mod-settings.json` there.
- **`--game DIR`** is the GOG folder (default: the developer copy `game.toml` names). The DLLs are loaded from beside the executable.
- **`--seconds S`** posts `WM_CLOSE` to the game's window after S seconds (default 120). The game then shuts down through its own exit path, saving its options to the registry.
- **`--frames DIR`** writes every Nth presented frame (default 60) as `frame-NNNNNN.ppm`. The boot test keeps the last one, the main menu, as `build/boot/main-menu.ppm` for comparison with legion's references. (The kit's own `RECOMP_D3D7_DUMP_DIR` dumps from the GPU renderer, which this headless host does not have.)
- **Display.** `game.toml [runtime] ddraw7 = true` has the kit serve `IID_IDirectDraw7` (`dx/ddraw7.cpp`, `dx/d3d7.cpp`). With no GPU host, `ee_boot` sets `RECOMP_D3D7_SOFTWARE=1` unless it is set, so the kit's reference renderer draws; `RECOMP_D3D7_MODES` and `RECOMP_D3D7_VSYNC=0` apply as the kit documents.
- **`--trace FILE`** writes every import call: thread, `DLL!name`, its stdcall arguments, the string argument of path and name calls, and the result.
- **Movies.** By default Escape is pressed (100 ms of every second) while a Bink movie is open, as a player does on Windows (legion.md §6). `--play-movies` lets the three intro movies run to the end (about 2 minutes).
- **Strict mode** is on unless `--no-strict` or `RECOMP_STRICT=0`.
- **Focus.** The host reports the application focused for the whole run (`host_app_state(true, false)`), so the kit's user32 activates each window the game shows from inside `ShowWindow`, as Windows does for the foreground application; the report says whether the application is active at the end.
- Every kit environment variable applies (`RECOMP_LOG=2` traces every import from inside the kit).

The headless host is `app/ee_main.cpp` over the kit's shared boot (`host/boot.cpp`): mem_init, the loader, the shims, the heartbeat, the watchdog and the fault handler are the kit's. It links the executable's translation and the eight DLLs' (`pop_link_gen`, all `[modules.aux.*]`), `recomp_dx` with no GPU or audio host (presentation reaches `ee_main.cpp`'s `host_display_present_window`), the mod foundation (for the overlay) and `app/ee_standins.cpp` with `app/pending_*.cpp`. The windowed app is the kit's `recomp_app` with the same `ee_standins.cpp` linked in (`app/CMakeLists.txt`); its constructor sets the user directory and installs the stand-ins of §5 before the kit registers its own shims.

---

## 2. The boot sequence [measured]

`tools/test.py --boot`, Escape through the movies. Times are from `boot_run`.

| # | Milestone | Time | What happens |
|---|---|---|---|
| 0 | modules loaded | 0.00 s | The executable at 0x400000 and the eight DLLs at their base-map addresses (build.md §3), 9,655 / 0 / 1,644 / 0 / 1,628 / 1,786 / 607 / 346 relocations. Static DllMains: sxlrt233, then the engine. |
| 1 | CRT started | 0.00 s | `WinMainCRTStartup`, x87 CW 0x027f. |
| 3 | plugin scan | 0.01 s | `UResourceFile` loads `C:\EMPIRE EARTH\Language.dll` (upper case, as on legion) and reads its string table: `EnumResourceNamesA`/`EnumResourceLanguagesA` on RT_STRING, then `LoadStringA`. |
| 2 | registry read | 0.01 s | `HKCU\Software\SSSI\Empire Earth`: 79 values asked, 76 present; `Load Game Name`, `Starting Keyboard Layout` and `Victory Allowed` are absent here and in legion's registry, and are written on exit. No HKLM key is read during the boot. |
| | data | 0.02–0.1 s | `data.ssa` (156 MB) is opened three times and mapped whole twice (`MapViewOfFile`, kept mapped). The game checks `0_Error.log` with `CREATE_NEW`. |
| 4, 5 | window, threads | 0.09 s | The splash thread (`EXE+0x1344fb`) loads bitmap 0x70, registers a class, creates the popup splash window and runs its own message loop; the main thread waits for it (`byte [0x919874]`). |
| | plugin scan | 0.1 s | `FindFirstFileA("*.dll")` in the game folder, then `LoadLibraryExA(path, 8)` and `GetProcAddress(PIMGetName/PIMGetType/PIMIsValid)` on every DLL, twice: the five game plugins answer; `binkw32`, `Mss32`, GOG's `ddraw.dll`, `sxlrt233`, the engine and `Language.dll` do not and are freed. **GOG's `ddraw.dll` is never mapped: the name resolves to the kit's DDRAW shims** [measured]. |
| 6 | DirectDraw created | 0.13 s | Both display plugins probe the kit's DirectDraw 7: `DirectDrawEnumerateExA` → `DirectDrawCreateEx(IID_IDirectDraw7)` → `GetCaps` → `EnumDisplayModes` → cooperative levels 0x408 and 0x8 → a test surface, `IDirect3D7::CreateDevice`, `EnumTextureFormats`, `EnumDevices`. DX7HRDisplay (`Rasterizer Name = Direct3D`) is activated: cooperative levels 0x80 then 0x513 as legion captured (§5.1), `SetDisplayMode(640, 480, 32)`. |
| | sound | 0.13 s | Miles mixer: `AIL_set_redist_directory`, `AIL_open_digital_driver(22050, 16, 2)`, 16 sample handles, `AIL_get_DirectSound_info` → `IDirectSound::GetCaps`. DirectMusic: loader, composer, performance, `Init`, default port, `IKsControl` volume and reverb, `SetSearchDirectory("Data\Music\")`, the `EventHandler` thread. |
| | network | 0.14 s | `TCP Protocol.dll`: `WSAStartup(2.0)`, `socket(AF_INET, SOCK_STREAM)`, `closesocket`; the EXE's WON code `WSAStartup(1.1)`. `Default.dll` is found as the random-map generator. |
| 7 | intro movies | 1.45 s | `Sierra.bik`, `SSSI.bik`, `Empire Earth.bik`, each on its own `DX7HRDisplay+0x2835` thread (`DX7Movie::PlayBink`), decoded by the kit's Bink; each skipped by Escape. |
| 8 | menu video mode | 4.22 s | `SetDisplayMode(1024, 768, 32)` (graphics.md: menus at 1024×768); UI textures load. |
| 9 | menu frames | 5.26 s | The render thread (`EXE+0x10fe16`) presents; 60 `Flip`s since the menu mode. The frame is the main menu: Single Player, Multiplayer, Game Tools, Game Settings, Exit Game, `v2.00.2974` top left. |
| 10 | main menu loop | 5.47 s | The main thread pumps its message loop (100 `PeekMessageA` since). |
| | exit | on `WM_CLOSE` | The game saves its options (`RegSetValueExA` × ~80 into `registry.json`), releases DirectDraw and its threads, `ExitProcess(0)`. |

With `--play-movies` the movies take their natural time and the menu appears after them, as on legion (~2 min).

### Threads [measured]

Every thread the game starts goes through the engine's `TSThread` trampoline (engine+0x5f5d0: `CoInitialize`, `param->fn(param->arg)`, `CoUninitialize`, `ExitThread`); the body is the function at `param+0x28` [static]. The boot report lists them with the x87 control word at their first and latest import call:

| Thread body | Role (legion.md §5.4) | CW |
|---|---|---|
| process entry | main: message pump | 0x027f → 0x027f |
| EXE+0x1344fb | splash screen (ends at the menu) | 0x027f |
| engine+0x57f9d | `SEConductor::EventHandler` (DirectMusic) | 0x027f |
| engine+0x5ba83 | `SESoundManager::Scavenger` (Miles) | 0x027f |
| DX7HRDisplay+0x2835 ×3 | Bink movie players (intro) | 0x027f |
| EXE+0x1270bf | `EEMusicManager::MusicManagerMain` (legion's `EXE+0x12711d`) | 0x027f |
| EXE+0x1110fe | input (`ISMouse`; legion's `EXE+0x111134`) | 0x027f |
| **EXE+0x10fe16** | **render** (legion's `EXE+0x10fe36`) | **0x027f → 0x007f**, set by the game's own `UMSetFloatingPointPrecision(_PC_24)` |
| EXE+0xce947 | texture/asset worker (legion's `EXE+0xce97f`) | 0x027f |
| EXE+0x1532fb | simulation (legion's `EXE+0x15332b`) | 0x027f |
| engine+0x526f0, engine+0x52d3f | `NESynchronousClient::Main` / `::Receive` | 0x027f |

Legion names threads by the return site of their first `UMSetFloatingPointPrecision`; the bodies here are the functions themselves, 0x20–0x38 bytes earlier. Every thread legion saw at the menu is here, with the same control word; the kit starts each at 0x027f and nothing but the game changes it.

---

## 3. Files and registry

- **Guest root** `C:\Empire Earth` (`game.toml` `guest_root`), the working directory. Paths are case-insensitive (the kit's resolver); the game builds its paths in upper case (`C:\EMPIRE EARTH\data\data.ssa`).
- **User data** goes through the kit's overlay: on this boot only `registry.json` is written (the options on exit). The game would create `0_Error.log` there; it probes it with `CREATE_NEW` and finds the one a Wine session left in this GOG copy. Saves, `Users\` and the WON cache are written there when the game writes them.
- **Registry** (`tools/boot/seed_user.py`): `HKCU\Software\SSSI\Empire Earth` (+ `\Game Options`) with legion's values (`windows/setup.ps1`: 1920×1080, `Direct3D`, 32-bit), `Installed From Volume = C:` and `Installed From Directory = \Empire Earth\` (GOG's `{app}` for the guest root), and the base game's HKLM keys from the player's own `goggame-1207658777.script`: `HKLM\Software\Stainless Steel Studios\Empire Earth` and `HKLM\Software\Sierra\CDKeys` `EmpireEarth` (REG_BINARY). A 32-bit program sees `HKLM\Software` (no `WOW6432Node`). The script is found next to the game folder, under `original/`, or in the Wineskin build; the key goes only into the player's `registry.json`.
- **`data.ssa`** is mapped whole, twice. The kit maps file views with the host (`mmap MAP_FIXED|MAP_PRIVATE`) instead of copying them into the heap, and `guest_size` is 640 MB (game.toml) so both views fit with room to spare.

---

## 4. What changed in the kit (`feat/boot`)

Still logging-only in those DLLs, by choice: `GetThreadContext` (the executable's crash report only, EXE 0x6a2340), `TerminateThread`, `CreateProcessA`; each answers FALSE.

| Change | Why | Tests |
|---|---|---|
| Translator: a DLL with an empty relocation table and `IMAGE_FILE_RELOCS_STRIPPED` clear is movable; a module with no code lists no functions | `Language.dll` is resources only (no code, no entry point, an 8-byte `.reloc`) and must sit at 0x10400000 | `test_function_db.py` |
| kernel32 resources of any mapped module: `FindResourceA`, `FindResourceExA/W`, `EnumResourceNamesA`, `EnumResourceLanguagesA/W`, `GetUserDefaultLangID`, `GetSystemDefaultLangID`; `resources.h` takes a module and a LANGID (one API with P3-C's `resource_find_in`; by default the loader's language order: neutral, the user's, its primary language, the first); `resource_string_in` | the engine reads its UI strings from `Language.dll`'s handle | `loader_tests` (a synthetic DLL with RT_STRING in two languages and a named RT_RCDATA) |
| `MapViewOfFile` maps the file's pages (`os_vm_map_file`, `os_vm_reset`, `os_vm_page_size`); misaligned offsets are `ERROR_MAPPED_ALIGNMENT` | two 156 MB views of `data.ssa` were copied into the heap and the second did not fit | `loader_tests` |
| Winsock: WS2_32 shares WSOCK32's `WSAStartup`/`WSACleanup` (versions, counting), `WSAGetLastError`/`WSASetLastError`, byte order, `inet_addr`, a `gethostbyname` without DNS | `TCP Protocol.dll` checks `wVersion == 2.0` and aborts otherwise; the dead Sierra lookup must fail at once (netcode.md §7) | `loader_tests` |
| `com_register_guest_class`: `CoCreateInstance` serves classes a host builds | an inert DirectMusic without touching the kit's COM model | `dx_tests` |
| mss32: Miles 6's starting preferences (`DIG_DS_FRAGMENT_SIZE` 8, `_CNT` 96, `DIG_DS_MIX_FRAGMENT_CNT` 8) | the sound plugin divides by the first before it sets any | `dx_tests` |
| `GetFileTime`, `CompareFileTime`, `GetFileAttributesExA`, `GlobalSize`, `GetWindowsDirectoryA`, `GetTempPathA`/`GetTempFileNameA` (Temp under the guest root), `GetShortPathNameA`, `WaitForSingleObjectEx`, `lstrcpynA`, `RegDeleteKeyA`/`RegDeleteValueA`, no WINMM aux devices | the rest of what the executable and the engine import from kernel32/advapi32/winmm [static, `db/win32-imports.json`]; not reached by the boot, reached by the menus and the game | `loader_tests` |

---

## 5. Stand-ins and gaps by owner

Called at boot with nothing behind them: only `WS2_32!socket` and `WS2_32!closesocket` (Phase 5). The stand-ins below that live in this repository (`app/pending_*.cpp`, and one function in `app/ee_standins.cpp`) are registered so the kit's own shim wins the moment it exists; each says so once on stderr.

### P3-B (ddraw/d3d)
- **Merged and in use** (`ee` 3534522, `[runtime] ddraw7 = true`): both display plugins validate, DX7HRDisplay activates, the intro movies play through the kit's Bink onto DirectDraw 7 surfaces and the main menu renders. No DirectDraw or Direct3D call at boot logs anything [measured]. Until the merge a null DirectDraw 7 stood in here; what it showed the plugins need is in the triage log (§6, step 5).
- `GetDisplayMode` reports a refresh rate of 0: the game logs `Video mode changed to 1024x768x32 at 0Hz.` where legion logs 60 Hz.
- `RECOMP_D3D7_DUMP_DIR` dumps only from the GPU renderer; a headless host takes the software renderer's frames from `host_display_present_window`.
- The windowed app's Metal presenter logs `presenter: FAULT: drawable acknowledgement exceeded queue grace; using command completion fallback` once at start-up, in every smoke run, and goes on presenting [measured].

### P3-C (user32/gdi32/dinput/imm32)
- **Merged and in use** (`ee` d4bac5b, `[runtime] relative_mouse` and `system_fonts`): per-thread message queues and `PostThreadMessageA`, module-aware `LoadStringA`/`LoadBitmapA` (over `resource_find_in`, §4), ANSI GDI text with the installed Tahoma, Arial, Times New Roman and Courier New, the refused `WH_CALLWNDPROC` hook, and activation from `host_app_state`. `pending_win32.cpp`, which stood in for all of these, is gone; what it showed the game needs is in the triage log (§6, steps 3–4, 11–13).
- **Still a stand-in (`app/ee_standins.cpp`, `destroy_thread_windows`): a thread's windows are destroyed when it ends.** The splash thread (EXE 0x5344fb) leaves its loop on `WM_QUIT` and returns without `DestroyWindow`; Windows destroys the window at thread exit. Without that, the splash stays the window `host_main_window()` names: the boot activates it and the deadline's `WM_CLOSE` goes to it, and the game never exits [measured]. The stand-in runs on the kit's thread-exit observer (`sched_set_thread_exit_observer`) and calls `DestroyWindow` for the thread's top-level windows on that thread.
- `GetProcAddress(user32, "GetMonitorInfoA")` → 0 (DX7HRDisplay's multi-monitor path; it copes). With the null DirectDraw (before P3-B merged) the splash screen's `BitBlt` was refused; not seen since.
- DirectInput at boot [measured]: one `SetCooperativeLevel` answers `DIERR_ACQUIRED` (legion's capture has every one succeed) and one `GetObjectInfo` fails with 0x80070002.
- The windowed host logs `guest pointer unavailable: reason=vtable-mismatch … object=0x00f00234`: game.toml's `[hooks]` mouse sentinels (Populous-shaped, kept outside every module) name no object, and the host falls back to window position differences, as it says.

### Phase 4 (audio)
- **`pending_mss32.cpp`**: the Miles calls the sound plugin makes that the kit lacks — `AIL_open_digital_driver` (over the kit's `AIL_waveOutOpen`), `close_digital_driver`, `set_redist_directory`, `last_error`, `get_DirectSound_info` (a kit `IDirectSound`; the mixer asks its caps for the voice count), `mem_alloc_lock`, `file_size`, and the sample position and rate calls. The preferences the mixer reads back (`DIG_DS_FRAGMENT_SIZE` 8 ms, `_CNT` 96, `DIG_DS_MIX_FRAGMENT_CNT` 8, Miles 6's defaults) are now the kit's (`feat/boot`); with 0 the mixer divided by zero (Miles Sound System Mixer.dll+0x194f, a strict-mode divide error).
- **DirectMusic is the kit's** (P4-A, `game.toml [runtime] dmusic`; audio.md §13). Until then `pending_dmusic.cpp` stood in, an inert DirectMusic: "unavailable" is not a path the game has, since `SEConductor::InitializeConductor` (engine+0x582e6) checks every HRESULT with a release-build VERIFY that prints `%s (%d) : Verification failure (%s)` and calls `abort()` (engine+0x1c43e) [measured].

### Phase 5 (network)
- **`socket`/`closesocket`** (ws2_32) are logging-only: `TCP Protocol.dll` probes `socket(AF_INET, SOCK_STREAM)` at start-up, gets 0 and closes it. `gethostbyname` of the host's own name answers 127.0.0.1, not the adapter list netcode.md §7 asks for. Nothing waits on the network at boot.

---

## 6. Triage log

Each step is one blocker, in the order the boot met them [measured].

1. `Language.dll` would not translate: no relocation entries, so "only at its preferred base". Kit translator: movable unless stripped (§4).
2. 163 MB `heap_alloc` failure: `MapViewOfFile` of `data.ssa`. Kit: views are host mappings; `guest_size` 640 MB.
3. Main thread spins in `Sleep(1)` forever: the splash thread ended at once because `LoadBitmapA` was missing. Stand-in (P3-C).
4. `EnumResourceNamesA`, `GetUserDefaultLangID`, `LoadStringA` missing; the kit's resources were executable-only. Kit: module-aware resources; `LoadStringA` stand-in.
5. Null rasterizer dereference (EXE 0x5393ae): no display plugin valid because `DirectDrawCreateEx` refuses DD7. Null DD7/D3D7 stand-in; then its `IID_IDirect3D7`, `DDDEVICEIDENTIFIER2` size (a 12-byte stack overrun), mip chains and `DDSCAPS_FLIP` back buffer.
6. `abort()` after `AIL_open_digital_driver` returned 0, then a null `IDirectSound` from `AIL_get_DirectSound_info`. Miles stand-ins.
7. `abort()` on `CoCreateInstance(CLSID_DirectMusicLoader)` failing (VERIFY). Kit `com_register_guest_class`; inert DirectMusic.
8. Strict-mode divide error in the Miles mixer: `AIL_get_preference(DIG_DS_FRAGMENT_SIZE)` = 0. Miles defaults.
9. `abort()` after `WS2_32!WSAStartup` (logging-only) left `wVersion` 0. Kit Winsock start-up.
10. `BinkDDSurfaceType` on a non-kit surface → a null `DX7Surface` copy. Answered for the stand-in's surfaces.
11. Two threads spinning on `PostThreadMessageA`. Thread messages (stand-in).
12. `WM_CLOSE` ignored: a worker's `PeekMessageA` took it (and the activation, and `WM_SIZE`). Per-thread `PeekMessageA` stand-in; posts from the host's tick deferred to the next `PeekMessageA` so a worker inside the kit's pump cannot take them. Clean exit.
13. ANSI GDI text and hooks: stand-ins. Boot clean but for the Phase 5 sockets.
14. P3-B's DirectDraw 7 merged: `[runtime] ddraw7 = true`, the null stand-in removed, the software renderer headless. Same milestones; the main menu renders.
15. P3-C's user32/gdi32/dinput/imm32 merged: `pending_win32.cpp` removed. The splash window then outlived its thread and took the activation and the deadline's `WM_CLOSE` (a forced unwind, then a fault in the simulation thread during teardown); a thread's windows are destroyed at its end (stand-in), the host reports focus, and the boot is clean again with the UI in Tahoma.
16. The windowed app: the kit's `recomp_app` with `ee_standins.cpp` (constructor-installed), Metal Direct3D 7, SDL input; Miles' preference defaults moved into the kit. Movies and the main menu render; quitting exits cleanly.

---

## 7. For legion (P3-D)

- The requested boot API trace (`analysis/legion-captures/boot-trace/`) has not landed yet. When it does, compare the sequence in §2: plugin scan order and count, the two `data.ssa` views, the DirectDraw probe sequence per plugin, Miles and DirectMusic init, `WSAStartup` versions and the `socket` probe.
- Please confirm: the `CreateFileA("…\0_Error.log", 0, 0, NULL, CREATE_NEW)` result on a clean install; and whether `Load Game Name`, `Starting Keyboard Layout`, `Victory Allowed` exist in legion's `HKCU\Software\SSSI\Empire Earth` after a run.
