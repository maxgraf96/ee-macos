# Driving the game: scripted input, the menus, the first game

Status: Phase 3, workstream P3-E (polish), 2026-10-15. Empire Earth is driven through its menus and into a random-map game **without a person**, headless in strict mode and in the windowed app, by the kit's scripted input (`host/input_script.h`, kit `feat/p3-polish`). Four scenarios pass together in `tools/test.py --menus`, and the random-map scenario renders **the first native in-game frames** and selects the player's town center. Context: [boot.md](boot.md), [graphics.md](graphics.md), [phase3.md](../phase3.md).

Labels: **[measured]** = output of the commands here (M4 Max, macOS 15.5, other workstreams building in parallel); **[static]** = read from the binaries; **[inferred]** = reasoning.

---

## 1. Running it

```sh
.venv/bin/python tools/test.py --menus [SCENARIO...]      # headless: every tests/menus/*.script, 4 at once
build/recomp/ee_boot --script tests/menus/settings.script --script-out /tmp/frames   # one, by hand
# the windowed app (Metal, SDL events): opens a window and takes the focus
open -n -W --env RECOMP_INPUT_SCRIPT=$PWD/tests/menus/app/smoke.script \
     --env RECOMP_INPUT_SCRIPT_OUT=/tmp/app-frames build/EmpireEarth.app
```

- **`--menus`** builds `ee_boot`, seeds a fresh user directory per scenario, runs each with `RECOMP_STRICT=1` and the software renderer (the three that open the game's server one at a time, §3), and keeps every frame a script dumps as `build/menus/<scenario>/<name>.png` (logs beside them). A scenario passes when its script finishes (every `click` reached its target, every `wait change` saw its screen), the exit is clean, nothing faulted, no strict-mode stop, and no import was called without an implementation. `settings` must end through **Exit Game** (the game's own exit, not the deadline), and `random-map` must produce 800×600 in-game frames that change.
- **`ee_boot --script FILE [--script-out DIR]`** starts the script at the main menu's message loop (milestone 10); Escape through the movies stays ee_boot's own. Exit status 7 when a script fails or does not finish; the failure names the line and why.
- **The app** runs `RECOMP_INPUT_SCRIPT` through SDL's own event queue. A launch from a terminal does not take the focus (the window shows `focus no` and DirectInput stays unacquired); `open` activates it, as `tools/run.py` does. The app's scripts skip the movies with `key ESCAPE` and wait for the menu with `wait size 1024 768`.
- **Disk:** at most 64 PNGs a run (the kit's cap); a menu PNG is 0.9 MB, a run of the four scenarios writes 24 frames, about 20 MB.

## 2. The game's own pointer [static + measured]

EE draws its own cursor from DirectInput deltas; `SetCursorPos` and absolute positions do not move it (legion.md §5.6). The scripts name where it lives so clicks can walk it there closed-loop:

```
pointer f32 [0x9197a8]+4 [0x9197a8]+8
```

- `[0x9197a8]` is the `RTSMouse` (RTTI `.?AVRTSMouse`); its position is two floats at +4/+8 in screen pixels, clamped to [+0x18, +0x1c) × [+0x20, +0x14) (0..1024 × 0..768 at the menu, 0..800 × 0..600 in game). The input thread (EXE+0x1110fe) calls `ISMouse::RefreshXY` (GetDeviceState) every 25 ms and then EXE 0x510e5d, which adds `count × (scale + 0.0625) × 4 / min|count|` per axis, where `scale` = (range / [+0x90 or +0x8c]) × sensitivity [+0x88] and **`min|count|` is the smallest non-zero count the game has seen**, starting at 100 (EXE 0x8a84a0/0x8a84a4; it logs `MinXValue now %d`). So the gain depends on history: 0.0223 px/count until a 1-count move, then 2.234 px/count with the default sensitivity 0.31 [measured: −2000 counts moved 44.7 px, then 100 counts 2.25 px; 1 count in y moved 2.23 px].
- Every scenario starts with `moveby 1 1`, which settles the gain at its final value; after that a click reaches its target in 1–2 steps, within 1.2 px [measured].
- UI hit tests use that position: the clicked button is the one under the drawn cursor.

## 3. The scenarios [measured]

Coordinates are the 1024×768 menus' (800×600 in the editor and the game). "Δ" is how much of the picture changed against the previous dump.

| Scenario | Screens reached (dump, size, Δ) | Result |
|---|---|---|
| `settings` | main menu → **Game Settings: Video** (90.6 %) → **Audio** tab (23.1 %) → **Interface** tab (8.8 %) → Video again (22.9 %) → Cancel → main menu (90.5 %) → **Exit Game** | passes; the game ends itself about 5 s after the click (`ExitProcess(0)`) |
| `single-player` | **Single Player Options** (100 %) → Play Random Map → **Game Setup** (36.7 %; defaults Random Map, Small, Continental, Tournament-Low, 1 + 1 computer) → Cancel → Single Player (36.7 %) → Main Menu (85.9 %) | passes |
| `game-tools` | **Game Tools** → **Civilization Builder** (87 %) → exit → Game Tools → **Scenario Editor** (800×600; black, "Creating World…", "Loading Complete", then the editor: a 15×15 grass map) → exit → **"Save scenario before exiting?"** → No → Game Tools (1024×768) | passes; ends at Game Tools (§3.7) |
| `random-map` | Single Player → Play Random Map → **Start Game** → loading screen → **the game** (800×600, Prehistoric Age, the Capitol with five citizens, terrain under fog, HUD with 200/175/210 resources and 5/250 population) at 5, 30, 60, 90 s; a click on the Capitol **selects it** (orange ring, its panel with 5000/5000 hit points and the citizen button) | passes; the game runs at ~15 frames/s on the software renderer with three other runs alongside; the citizens move between 30 s and 90 s |

The four take about 3½ minutes (the three server scenarios in turn). **On Metal** the app's `tests/menus/app/random-map.script` reaches the game at 1920×1080 (the registry's mode): an Atomic Age – WW1 start on snow with 15,000 food and wood, the capitol and five citizens, at about 110 frames/s (3,348 frames in 30 s), and quits cleanly with no presenter fault [measured]. The game-setup defaults the two runs started from differ (Prehistoric headless); both come from the game's own setup screen. The app's `tests/menus/app/smoke.script` (Escape ×3, main menu, Game Settings, Cancel, quit) passes on Metal too: the Video tab lists the display's modes up to 2560×1600 and the registry's 1920×1080×32 is selected, the game quits with exit code 0 through `SDL_EVENT_QUIT` [measured]. Headless, the software renderer offers modes up to 1152×864, so the game falls back to 800×600×16 in game [measured].

### What the scenarios found

1. **Game Settings faulted** the main thread before P5-A's IP helper API merged [measured: a null-page read at TCP Protocol.dll+0x1d40, or a `strcpy` into garbage]. The Interface tab lists network adapters: `NPIMEnumerateNetworkAdapters` (TCP Protocol.dll+0x1ce0) mallocs 6,400 bytes, calls `GetAdaptersInfo` and walks the list on `ERROR_SUCCESS`; with no shim the import was a logging-only stub answering 0 over an unfilled buffer. A stand-in answering `ERROR_NO_DATA` carried the work until the kit's `GetAdaptersInfo` (P5-A, `ee` e93e875) arrived; with it the Interface tab shows `<default>`, and the stand-in is gone.
2. **The game's server ports are fixed** [measured + static]. Play Random Map, Start Game and the Scenario Editor open EE's game server, which binds UDP 33334 and TCP 33335 (`htons(0x8236)`/`0x8237`, constants in TCP Protocol.dll; no registry value names a port). Two copies of the game on one machine collide: the second `bind` fails with `WSAEADDRINUSE` (10048), the engine goes on, and its main thread faults in `NESynchronousServer::ProcessLocalUserInput` (engine+0x54578; the server's `NERecordPlayback` at +0x16c is gone by the time it is used) [measured: 3 of 4 concurrent runs]. Whether Windows' EE does the same with its port taken is for legion; the menu test runs those three scenarios one at a time (`# menus: alone`).
3. **A Blt onto the primary was presented as the whole frame** (kit, P3-B area; fixed). The editor's and the game's loading screens draw their progress onto the primary with small Blts; each was presented on its own, a 32×32 frame, which in the app would show the rectangle scaled to the window. A fullscreen program's primary is now presented whole.
4. **Clicks during "Creating World…" are ignored** (as on Windows, [inferred]), and the black phase before it lasts from a few frames to several seconds [measured], so a script waits for the screen itself: `wait pixel` on a pixel of the editor's panel or the game's command panel that no loading screen has.
5. **Not yet explained, for a Windows comparison:** (a) in the editor, a click on the "Random" radio button did not select it, and the terrain view moves while the cursor rests near the bottom panel; (b) after selecting the Capitol, two thin blue-green lines run from under the top bar to the screen's sides; (c) the minimap shows only the camera's trapezoid at the start of a game. Each could be the game's own behaviour; legion's reference screenshots of the same screens decide.
6. **Exit is clean** from the main menu (Exit Game), from the editor (exit, No) and from a game (`quit` = `WM_CLOSE`).
7. **A release can click the next screen's button** [static + measured]. `UIButton::MouseLButtonUp` (engine+0x65322) fires "clicked" for a release inside its rectangle without checking that the button was pressed. Game Tools' "Main Menu" sits exactly where the main menu's Exit Game is, and in 4 of 5 runs the release that left Game Tools also exited the game from the main menu. Whether a player on Windows sees the same depends on the frame the new screen first reads the mouse in; legion can try it. `game-tools` ends at Game Tools for that reason.

## 4. Against Windows

`analysis/legion-captures/menus-ref/` (P3-D, legion 0010) has not landed; when it does, compare each `build/menus/<scenario>/*.png` with the reference of the same screen at the same settings (perceptually, as phase3.md asks). Settings differ in one known way: headless runs fall back to 800×600×16 in game where legion's registry asks 1920×1080×32; the windowed app uses 1920×1080.

## 5. Owners of what is left

| Owner | Item |
|---|---|
| P5-A (winsock) | nothing blocking: Game Settings' adapter list and the game server's sockets work on the kit's Winsock; §3.2 (a taken port faults the engine) is the game's behaviour unless legion shows otherwise |
| P3-D (legion) | reference screenshots of these screens; the items in §3.5 and §3.7; EE with UDP 33334 already taken |
| P3-E follow-up | §3.5 once references exist; scripted input in the app needs the focus (`open`) |
