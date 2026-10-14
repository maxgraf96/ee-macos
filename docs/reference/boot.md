# Booting the whole game: `ee_boot`

Status: Phase 3, workstream P3-A (exe-boot), 2026-10-14. The GOG `Empire Earth.exe` and all eight DLLs, translated, boot natively on arm64 macOS **in strict mode, headless, to the main menu's message loop**, and exit cleanly on `WM_CLOSE`. Display, sound and input are null devices, and some temporary stand-ins fill gaps other workstreams own (§5). Context: [phase3.md](../phase3.md), [build.md](build.md), [legion.md](legion.md).

Labels: **[measured]** = output of the commands here (M4 Max, macOS 15.5); **[static]** = read from the binaries or the DB; **[inferred]** = reasoning.

---

## 1. How to run

```sh
# once: link the installation and export the translation inputs for all nine modules
.venv/bin/python tools/setup.py --install "/path/to/Empire Earth"
# once: the player's registry (CD key from their GOG install script; never printed)
.venv/bin/python tools/boot/seed_user.py [--gog-script PATH] [--width 1920 --height 1080]
# translate everything and build the host
.venv/bin/python tools/build.py --regenerate --cmake-target ee_boot
# boot it
build/recomp/ee_boot [--seconds 120] [--trace FILE] [--user DIR] [--game DIR] [--play-movies] [--no-strict]
# the test: strict boot to the main menu loop with a fresh user directory
.venv/bin/python tools/test.py --boot
```

- **`--user DIR`** is the player's data, `~/Library/Application Support/EmpireEarth` by default: `registry.json` (the kit's registry store, `RECOMP_REGISTRY`) and the writable tier of the file overlay (`RECOMP_PROFILE_DIR`, kit `mods/overlay.cpp`). Reads prefer it, every write lands in it, and a game file opened for writing is copied up first, so the GOG folder is never modified. The kit also keeps `mod-settings.json` there.
- **`--game DIR`** is the GOG folder (default: the developer copy `game.toml` names). The DLLs are loaded from beside the executable.
- **`--seconds S`** posts `WM_CLOSE` to the game's window after S seconds (default 120). The game then shuts down through its own exit path, saving its options to the registry.
- **`--trace FILE`** writes every import call: thread, `DLL!name`, its stdcall arguments, the string argument of path and name calls, and the result.
- **Movies.** By default Escape is pressed (100 ms of every second) while a Bink movie is open, as a player does on Windows (legion.md §6). `--play-movies` lets the three intro movies run to the end (about 2 minutes).
- **Strict mode** is on unless `--no-strict` or `RECOMP_STRICT=0`.
- **Debugging.** `EE_MSG_DEBUG=1` prints every message a thread takes from the queue; `EE_DDRAW_DEBUG=1` prints the null DirectDraw's surface descriptors.
- Every kit environment variable applies (`RECOMP_LOG=2` traces every import from inside the kit).

The host is `app/ee_main.cpp` over the kit's shared boot (`host/boot.cpp`): mem_init, the loader, the shims, the heartbeat, the watchdog and the fault handler are the kit's. It links the executable's translation and the eight DLLs' (`pop_link_gen`, all `[modules.aux.*]`), `recomp_dx` with no host callbacks (null display and sound), the mod foundation (for the overlay) and `app/pending_*.cpp`.

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
| 6 | DirectDraw created | 0.12 s | Both display plugins probe: `DirectDrawEnumerateExA` → `DirectDrawCreateEx(IID_IDirectDraw7)` → `GetCaps` → `EnumDisplayModes` → cooperative levels 0x408 and 0x8 → a test surface, `IDirect3D7::CreateDevice`, `EnumTextureFormats`, `EnumDevices`. DX7HRDisplay (`Rasterizer Name = Direct3D`) is activated: cooperative levels 0x80 then 0x513 as legion captured (§5.1), `SetDisplayMode(640, 480, 32)`. |
| | sound | 0.13 s | Miles mixer: `AIL_set_redist_directory`, `AIL_open_digital_driver(22050, 16, 2)`, 16 sample handles, `AIL_get_DirectSound_info` → `IDirectSound::GetCaps`. DirectMusic: loader, composer, performance, `Init`, default port, `IKsControl` volume and reverb, `SetSearchDirectory("Data\Music\")`, the `EventHandler` thread. |
| | network | 0.14 s | `TCP Protocol.dll`: `WSAStartup(2.0)`, `socket(AF_INET, SOCK_STREAM)`, `closesocket`; the EXE's WON code `WSAStartup(1.1)`. `Default.dll` is found as the random-map generator. |
| 7 | intro movies | 1.45 s | `Sierra.bik`, `SSSI.bik`, `Empire Earth.bik`, each on its own `DX7HRDisplay+0x2835` thread (`DX7Movie::PlayBink`), decoded by the kit's Bink; each skipped by Escape. |
| 8 | menu video mode | 4.19 s | `SetDisplayMode(1024, 768, 32)` (graphics.md: menus at 1024×768); UI textures load. |
| 9 | menu frames | 5.00 s | The render thread (`EXE+0x10fe16`) presents; 60 `Flip`s since the menu mode. |
| 10 | main menu loop | 5.15 s | The main thread pumps its message loop (100 `PeekMessageA` since). |
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

| Change | Why | Tests |
|---|---|---|
| Translator: a DLL with an empty relocation table and `IMAGE_FILE_RELOCS_STRIPPED` clear is movable; a module with no code lists no functions | `Language.dll` is resources only (no code, no entry point, an 8-byte `.reloc`) and must sit at 0x10400000 | `test_function_db.py` |
| kernel32 resources of any mapped module: `FindResourceA`, `FindResourceExA/W`, `EnumResourceNamesA`, `EnumResourceLanguagesA/W`, `GetUserDefaultLangID`, `GetSystemDefaultLangID`; `resources.h` takes a module and a LANGID; `resource_string_in` | the engine reads its UI strings from `Language.dll`'s handle | `loader_tests` (a synthetic DLL with RT_STRING in two languages and a named RT_RCDATA) |
| `MapViewOfFile` maps the file's pages (`os_vm_map_file`, `os_vm_reset`, `os_vm_page_size`); misaligned offsets are `ERROR_MAPPED_ALIGNMENT` | two 156 MB views of `data.ssa` were copied into the heap and the second did not fit | `loader_tests` |
| Winsock: WS2_32 shares WSOCK32's `WSAStartup`/`WSACleanup` (versions, counting), `WSAGetLastError`/`WSASetLastError`, byte order, `inet_addr`, a `gethostbyname` without DNS | `TCP Protocol.dll` checks `wVersion == 2.0` and aborts otherwise; the dead Sierra lookup must fail at once (netcode.md §7) | `loader_tests` |
| `com_register_guest_class`: `CoCreateInstance` serves classes a host builds | an inert DirectMusic without touching the kit's COM model | `dx_tests` |

---

## 5. Stand-ins and gaps by owner

Called at boot with nothing behind them, after this workstream: only `WS2_32!socket` and `WS2_32!closesocket` (Phase 5). Everything else below is a **temporary stand-in in this repository** (`app/pending_*.cpp`), registered so the kit's shim wins the moment it exists; each says so once on stderr.

### P3-B (ddraw/d3d)
- **`pending_ddraw.cpp`: a null DirectDraw 7 / Direct3D 7.** The kit's `DirectDrawCreateEx` refuses `IID_IDirectDraw7`, so no display plugin validates, no rasterizer registers and the executable dereferences a null rasterizer (EXE 0x5393ae). The stand-in has one primary display (modes 640×480–1920×1080 at 16 and 32 bits), a HAL and a T&L HAL device, system-memory surfaces with flip chains (`GetAttachedSurface(DDSCAPS_FLIP)` finds the back buffer), mipmap chains (`DDSCAPS_MIPMAP`), z-buffers, `GetDC` through a GDI DIB section, vertex buffers, gamma control and clippers. It draws nothing. What the plugins use that P3-B needs [measured]: `DDDEVICEIDENTIFIER2` (1,068 bytes) from `GetDeviceIdentifier`, `GetAvailableVidMem`, `EnumZBufferFormats`, the back buffer found by `DDSCAPS_FLIP`, mip levels found by `DDSCAPS_MIPMAP`, textures created as complex mip chains with `DDSD_MIPMAPCOUNT`, and `IDirectDrawGammaControl` through `QueryInterface`.
- **`BinkDDSurfaceType`** answers for the stand-in's surfaces (the kit's asks its own COM objects).
- **`Flip` does not wait**: the render thread presents ~1,200 frames/s against legion's ~83. `DDFLIP_WAIT` with `Wait for VSync = 1` should pace it.

### P3-C (user32/gdi32/dinput/imm32)
- **Per-thread message queues** (`pending_win32.cpp`). The kit's queue is the process's: a worker's `PeekMessageA` loop took the main window's `WM_SIZE`, its activation and `WM_CLOSE`, and the splash thread's `WM_QUIT` reached the main loop. The stand-in sets other threads' window messages and pending paints aside while the kit's `PeekMessageA` runs, keeps thread messages per thread, and routes `PostQuitMessage` to the caller's thread.
- **`PostThreadMessageA`** is missing in the kit: the main thread posts 0x404 to a worker and retries with `Sleep(1)` until the post succeeds, a worker posts 0x418 back, and without it both spin forever.
- **Window destruction at thread exit**: the splash thread ends with its window alive; Windows destroys a thread's windows when it exits.
- **Activation of a later window**: the kit's boot activates the first window shown (the splash); the game's window (`0x2001c`) needs `WM_ACTIVATEAPP`/`WM_ACTIVATE`/`WM_SETFOCUS` when it appears. `ee_main.cpp` posts them, and `WM_CLOSE` goes to the game's window, not `host_main_window()`.
- **`LoadStringA`** (kit has only `LoadStringW`, and it reads the executable's table whatever the `hInstance`) and **`LoadBitmapA`** (likewise): the stand-ins use the kit's module-aware `resource_string_in`/`resource_find_in`.
- **`CreateFontA`, `ExtTextOutA`, `GetTextExtentPoint32A`**: stand-ins convert to the kit's `CreateFontIndirectW`, `ExtTextOutW`, `GetTextExtentPointA`.
- **`SetWindowsHookExA`/`UnhookWindowsHookEx`/`CallNextHookEx`**: the executable asks for `WH_CALLWNDPROC` on every thread with no module (EXE 0x536099), which Windows refuses with `ERROR_HOOK_NEEDS_HMOD` [inferred]; the stand-in does the same.
- **The splash screen's `BitBlt` is refused** (`gdi: REFUSED blit … src=00060001(-1x-1)`): the memory DC's selected bitmap (from `LoadBitmapA`) is not seen. Cosmetic, ~1,100 log lines per boot.
- `GetProcAddress(user32, "GetMonitorInfoA")` → 0 (DX7HRDisplay's multi-monitor path; it copes). `dinput: GetObjectInfo is not supported`.

### Phase 4 (audio)
- **`pending_mss32.cpp`**: the Miles calls the sound plugin makes that the kit lacks — `AIL_open_digital_driver` (over the kit's `AIL_waveOutOpen`), `close_digital_driver`, `set_redist_directory`, `last_error`, `get_DirectSound_info` (a kit `IDirectSound`; the mixer asks its caps for the voice count), `mem_alloc_lock`, `file_size`, the sample position and rate calls, and **`AIL_get_preference` with Miles 6 defaults** (`DIG_DS_FRAGMENT_SIZE` 8 ms, `_CNT` 96, `DIG_DS_MIX_FRAGMENT_CNT` 8): the kit's table starts at 0 and the mixer divides by it (Miles Sound System Mixer.dll+0x194f, a strict-mode divide error).
- **`pending_dmusic.cpp`: an inert DirectMusic.** "Unavailable" is not a path the game has: `SEConductor::InitializeConductor` (engine+0x582e6) checks every HRESULT with a release-build VERIFY that prints `%s (%d) : Verification failure (%s)` and calls `abort()` (engine+0x1c43e) [measured]. The stand-in serves the loader, composer and performance through `com_register_guest_class`, with one default port (output class, DLS) and `IKsControl` on it; every call succeeds and nothing plays. audio.md §2 is the contract Phase 4 implements.

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

---

## 7. For legion (P3-D)

- The requested boot API trace (`analysis/legion-captures/boot-trace/`) has not landed yet. When it does, compare the sequence in §2: plugin scan order and count, the two `data.ssa` views, the DirectDraw probe sequence per plugin, Miles and DirectMusic init, `WSAStartup` versions and the `socket` probe.
- Please confirm: `SetWindowsHookExA(WH_CALLWNDPROC, EXE+0x136099, NULL, 0)`'s result and last error on Windows 11; the `CreateFileA("…\0_Error.log", 0, 0, NULL, CREATE_NEW)` result on a clean install; and whether `Load Game Name`, `Starting Keyboard Layout`, `Victory Allowed` exist in legion's `HKCU\Software\SSSI\Empire Earth` after a run.
