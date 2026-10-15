# The Multiplayer lobby: how the WON GUI builds and presents its UI (P5-B, phase 1)

Status: static reverse engineering and spec, 2026-10-15. Workstream P5-B (lobby), phase 1: no kit code. The Windows reference trace (legion mailbox 0021) has not landed; §11 lists what it must confirm. Context: [PLAN.md](../PLAN.md) §2.3 and §5.6, [netcode.md](netcode.md) §3, [graphics.md](graphics.md) §6, [boot.md](boot.md), [phase5.md](../phase5.md).

Labels:
- **[static]**: read from the canonical GOG `Empire Earth.exe` (Ghidra 12.1.4 decompiler on the func-db project, `db/functions/empire-earth.exe.json`, capstone listings);
- **[measured]**: tool output, or a trace an earlier session took (cited);
- **[inferred]**: reasoning.

Addresses are EXE VAs (fixed base 0x400000). "slot n" is an RTTI vtable slot (0-based). Class names are RTTI names; method names in *italics* are ours.

---

## 0. Summary

- **What it is.** *Multiplayer* on the main menu opens the **WONAPI 2 lobby** ("WONLobby", Sierra's WON client library, statically linked: 13,800 of the EXE's entries, 583 RTTI classes) with EE's own glue (`WONAPI\WONLobby\GameCode\EmpireEarth\*.cpp`, EXE 0x677000–0x684000). There is no other multiplayer UI on this path: the engine's `UIForm` screens are not used [static].
- **One native window, everything custom-drawn.** The lobby is a component tree (`Component`/`Container`, `MSButton`, `MSInputBox`, `MSListCtrl`, `MSScrollbar`, `MSComboBox`, `MSTabCtrl`, `ChatCtrl`, `GameBrowserCtrl`, …). No standard Win32 control is ever created: the EXE's only `CreateWindowExA` in WONAPI is `MSWindow::Create` with its own class `WndClass%d`. EE configures dialogs and popups to be drawn **inside** the one lobby window (WindowManager+0xa5 = 0) [static].
- **Drawing: off-screen bitmap, then one `BitBlt`.** Each `MSWindow` keeps its window DC for life (`CS_OWNDC` + `GetDC`), composes the component tree into a window-sized `CreateCompatibleBitmap` in a memory DC with plain GDI (`TextOutW`, `Rectangle`, `Polygon`, `Ellipse`, `Arc`, `Polyline`, `SetPixel`, masked `BitBlt`s of decoded PNG/JPEG/BMP skin images, `AlphaBlend` for translucency), and on `WM_PAINT` copies it to the window DC with one `BitBlt(SRCCOPY)` between `BeginPaint` and `EndPaint`. Invalidation is `InvalidateRect` of a dirty rectangle, driven by a 50 ms `SetTimer` tick [static].
- **Own message loop, on the main thread.** The lobby runs a nested **blocking `GetMessageA` loop** inside the *Multiplayer* button's handler, on the thread that pumps the game window; WONAPI's socket threads wake it with `PostMessageA(WM_USER)` [static; Wine trace: "the main thread spins in the lobby's message loop", PLAN §2.3].
- **DirectDraw stays exclusive.** In the default fullscreen mode the game does **not** change its cooperative level (0x513) or display mode (1024×768 at the menus). It suspends its DirectInput keyboard and mouse, calls **`IDirectDraw7::FlipToGDISurface`**, stops its render thread from presenting, shows the system cursor, and shows the lobby as a **`WS_POPUP` owned by the game window**, covering the game window's rectangle. Leaving reverses this; the next `Flip` resumes the game's own display [static].
- **Why Wine shows nothing:** GOG's `ddraw.dll` is a D3D9 wrapper configured `presentation=windowed` (`dxcfg.ini`); its last frame stays on the game window. On Windows the desktop compositor draws the owned popup above its owner; under Wine/macOS the D3D9 surface covers it (§6) [static + inferred].
- **For the Mac:** the kit serves every API the lobby imports (wininet aside, which is right), and it already gives each window a backing surface and composites them. What is missing is composition **with DirectDraw 7**: GDI frames are composited over black and replace the D3D7 frames (and vice versa), `FlipToGDISurface` is a no-op, owned windows can fall below their owner, the cursor never changes shape, and a few message and GDI details differ (§5). The plan (§9): a two-layer presenter (last D3D7 frame + GDI overlay), owned-window z-order and activation, timer/close/double-click/wheel details, cursors, CP1252 and Unicode text input, `Arc` and font fallback, the clipboard, scripted input for GDI windows. About 3½–4½ weeks of kit work (1½–2 weeks for a usable lobby) plus about 1 week of ee-macos tests.

### Key addresses [static]

| What | Address |
|---|---|
| Main menu *Multiplayer*: `EEUIEMSEMultiPlayer::Execute` (vtable 0x82c74c, slot 1) | 0x5d1653 |
| *Lobby start*: create `WONAPICoreEx`, `MSWindowManager`, `MSWindow`; show; run (thunk 0x67dcef) | 0x67dd3f |
| *ShowLobby(bool)* / *HideLobby* | 0x67dfc6 / 0x67e542 |
| *Lobby run loop* and result dispatch (0 back, 1 join, 2 host, 3 quit) | 0x67e140 |
| *Lobby shutdown* (via 0x67c58f) | 0x67e74d |
| `IDirectDraw7::FlipToGDISurface` call (screen+0x30, vtable +0x28) | 0x67e0fb, in 0x67dfc6 |
| Lobby-shown flag (byte) / reader; lobby-active flag / reader | 0x943845 / 0x67e7cc; 0x943844 / 0x67e7c6 |
| Lobby critical section (render vs. show/hide) | 0x943800 (enter 0x67e811, leave 0x67e81d) |
| Render thread body (skips its frame while the lobby is shown) / frame and present gate | 0x50fe16 / 0x51015d |
| Game WndProc: everything but `WM_SIZE` goes to `DefWindowProcA` while the lobby is shown | 0x6622dd (+0x5c) |
| Fullscreen option (byte; `DX7Screen::Capture(hwnd, fullscreen)`) | 0x91945a |
| Game main window: pointer to its HWND | [0x9197a4] |
| Owner HWND for new top-level WON windows / setter; class-name counter | 0x944ae4 / 0x7bbb30; 0x944ae8 (`"WndClass%d"` at 0x8ca360) |
| Return to the lobby after a game: `EELobbyGameDoneEvent::Execute` → *ReshowLobby* | 0x67dbb8 → 0x67e6dd |
| WONAPI Win32 back end (`MSWindow`, `MSWindowManager`, `MSGraphics`, `MSFont`, `MSNativeImage`, `MSCursor`, decoders) | 0x7b9ae0–0x7bdc00 (§4) |
| `WindowManager::MessageLoop` / `MSWindowManager` *PumpMessage* (`GetMessageA`) | 0x719870 / 0x7bbeb0 |
| `WindowManager` *Tick* (≥ 50 ms: timers, cross-thread events, invalidation) | 0x71a800 |
| `InternetGetConnectedState` (wininet by `LoadLibraryA`) / its caller `InitLogic` | 0x6ab070 / 0x7c87b0 |

---

## 1. Entry: what *Multiplayer* does [static]

1. The main menu's *Multiplayer* button runs **`EEUIEMSEMultiPlayer::Execute`** (0x5d1653). It sets byte 0x917998 = 1 and asks `0x67f2a5(0)`, which returns 1 for the argument 0, so it always takes the WON lobby; the legacy `EEUIEMSOpenMultiplayerScreen` form (id 0x1e) is the dead branch. It calls 0x67dcef, a thunk to **0x67dd3f**.
2. **0x67dd3f** (*lobby start*, runs once: byte 0x943844):
   - creates and starts `WONAPICoreEx` (0x6de050), WONAPI's core with its pump thread;
   - creates the **`MSWindowManager`** (0x7bbb70, 0x14c bytes): `GetModuleHandleA(NULL)`, one `CreateCursor` (32×32 masks at 0x83e538/0x83e4b8, hot spot 12,4: a hand) and `LoadCursorA` of `IDC_ARROW`, `IDC_IBEAM`, `IDC_SIZEWE`, `IDC_SIZENS`, `IDC_SIZEALL`. It stores it as the lobby's window manager and sets **WindowManager+0xa5 = 0**: dialogs and popups live inside the main window (§3.1);
   - creates the lobby's **`MSWindow`** (0x7bc000, 200 bytes), sets byte +0xc5 = 1 (forward key messages to the parent, §3.2) and its flags +0x74 = **0x22** when fullscreen (popup, created hidden) or **2** when windowed;
   - reads the game window's title (`GetWindowTextA`) and passes it to 0x677b60 [inferred: the lobby window's title];
   - when fullscreen, sets the owner HWND global 0x944ae4 to the game window, calls *Create* (slot 12, 0x7bc170) with the rectangle (0,0,100,100), and clears the global again;
   - copies the game window class's icon (`GetClassNameA`, `GetClassInfoA`) into an `MSNativeIcon`;
   - loads the resources (`Data\WONLobby Resources\*.cfg`, 0x67bece), builds the root screen, calls ***ShowLobby(1)*** (0x67dfc6), initialises (0x74da00 → `InitLogic` 0x7c8870, §8), then enters the ***run loop*** (0x67e140).
3. **The run loop** (0x67e140) calls 0x74da20, which runs **`WindowManager::MessageLoop`** (0x719870): it repeats `MSWindowManager` slot 1 (0x7bbeb0: `GetMessageA(&msg, NULL, 0, 0)`, `TranslateMessage`, `DispatchMessageA`) until the lobby ends its loop level, then returns the lobby's result (LobbyLogic+0x4c):
   - **0** (back) → *shutdown* (0x67c58f), back to the main menu;
   - **1** (join a LAN game) → 0x67e310: `NESynchronousClient::Connect(host, "EEService", …)` (netcode.md §3.2), then *hide* and into the game;
   - **2** (host) → queues an `EEALobbyAction` that runs 0x67e1d6 (the game server, the seed, `StartGame`; netcode.md §3.2), then *hide* and into the game;
   - **3** (quit) → shutdown and `PostQuitMessage` (0x4cb67a).
4. **Thread.** The window is created by the thread that then runs `GetMessageA`, as it must be (a window's messages go to its creating thread's queue). That is the thread that runs the menu's button handlers, which pumps the game window too: the Wine trace saw the **main thread** in the lobby's loop (PLAN §2.3) [measured, Wine]. The game window's messages are dispatched by the same loop while the lobby runs.
5. **Other threads.** WONAPI's networking (`SocketThread`, `TimerThread`, `WONAPICore::PumpThread`; `_beginthreadex`) queues completion events under the window manager's critical section and wakes the GUI thread with `PostMessageA(root window, WM_USER, 0, 0)` (`MSWindowManager` slot 4, 0x7bbf30). `WM_USER` is handled as "run the tick" [static].

## 2. The display: DirectDraw, the game window, input [static]

The fullscreen option (byte 0x91945a) is what `DX7Screen::Capture(hwnd, fullscreen)` receives at window creation (0x5392e9). It is set on GOG installs: the boot measured cooperative levels 0x80 then 0x513 and the menus at 1024×768 (boot.md §2, legion.md §5.1). PLAN §2.3's "menus and lobby run `DDSCL_NORMAL`" applies only to the windowed option.

***ShowLobby*** (0x67dfc6), fullscreen:
1. `GetWindowRect(game window)`; the size is kept (0x943834 width, 0x943830 height).
2. The game's `RTSMouse` ([0x9197a8]) gets mode 6 at +0x98 (its software cursor is no longer drawn) [inferred from the reset to 0 in *HideLobby*].
3. `MSWindow` slot 13 → `MoveWindow(lobby, rect)`; slot 16 → `ShowWindow(lobby, SW_SHOW)`.
4. `SetCursorPos` to the game window's client origin (`ClientToScreen`) plus the game cursor's position [inferred: the two values come from 0x53ba92, a float-to-int helper].
5. **Under the lobby critical section** (0x943800): byte 0x943845 = 1 (*lobby shown*); `ISKeyboard::Suspend` and `ISMouse::Suspend` (DirectInput unacquire; engine vtable slot 6); **`IDirectDraw7::FlipToGDISurface`** on the screen's DirectDraw object (DX7Screen+0x30, the same pointer `Capture` calls `SetCooperativeLevel` on).
6. `ShowCursor(TRUE)` until the display count is 1: the **system cursor** is shown; WONAPI sets its shape with `SetCursor`.

Windowed (byte 0x91945a = 0): the lobby is a `WS_OVERLAPPEDWINDOW` top-level window with no owner at the game window's rectangle, and the game window is hidden (`ShowWindow(SW_HIDE)`); DirectInput is not suspended.

**What stops while the lobby is shown:**
- **Presentation.** The render thread (0x50fe16, the `_PC_24` thread) skips its whole frame when 0x67e7cc says *shown*; the frame method (0x51015d) checks the flag again under the lobby critical section before `ShowScene` (the Flip). So nothing is presented through DirectDraw from the moment the lobby is shown until it is hidden, and the `FlipToGDISurface` cannot race a `Flip`.
- **The game window.** Its WndProc (0x6622dd) passes every message but `WM_SIZE` to `DefWindowProcA` while the lobby is shown, so the activation change to the lobby popup does not trigger its usual self-minimise, input suspend or render-thread pause (`WM_ACTIVATEAPP` → `CloseWindow` and thread message 0x402). The `WM_ACTIVATE(WA_INACTIVE)` it gets from `ShowWindow` (before the flag is set) returns early.
- **The cooperative level and display mode do not change.** The lobby glue's only call on the screen object is `FlipToGDISurface` (the one access to GEScreen+0x2a8 in 0x677000–0x684000). WONAPI's own `MSWindowManager` slot 2 (*SetScreenResolution*: `EnumDisplaySettingsA` + `ChangeDisplaySettingsA(CDS_FULLSCREEN)`) has no caller next to a load of the window manager [static; the trace confirms, §11].

***HideLobby*** (0x67e542): clears 0x943845; windowed: moves the game window to the lobby's position and `ShowWindow(SW_RESTORE)`; `UIManager::RedrawAll`; fullscreen: `ISKeyboard::Resume`, `ISMouse::Resume`; puts the game cursor at the window origin and resets `RTSMouse`+0x98 to 0; hides the lobby window (slot 16 → `ShowWindow(SW_HIDE)`). The render thread's next frame Flips as usual; the cooperative level was never left.

## 3. The WON GUI toolkit [static]

### 3.1 Windows
- **`MSWindow::Create`** (0x7bc170, slot 12): `RegisterClassA` with a fresh name `WndClass<n>` (counter 0x944ae8, so the lobby is `WndClass0`), `style = CS_OWNDC | CS_DBLCLKS` (0x28), `lpfnWndProc = 0x7bc7a0`, no cursor, no background brush, `LoadIconA(NULL, IDI_APPLICATION)`. Then `CreateWindowExA(0, class, title, style, x, y, w, h, parent, NULL, hinst, this)`:
  - style `WS_OVERLAPPEDWINDOW` (0x00cf0000) without an owner, `WS_POPUP | WS_OVERLAPPEDWINDOW` (0x80cf0000) when the window has a parent `Window`, **`WS_POPUP` alone (0x80000000) with flag 0x20**; flags 4/8/0x10 remove `WS_SYSMENU`/`WS_MINIMIZEBOX`/`WS_THICKFRAME|WS_MAXIMIZEBOX`; flag 0x80 is `CW_USEDEFAULT`; flag 0x40 grows the window so the client area has the asked size.
  - the owner is the parent `Window`'s HWND, else the global 0x944ae4 (the game window for the lobby when fullscreen);
  - **never `WS_CHILD`**, and no extended style.
  - Flag 2 clear → shown at once; the lobby has it set and is shown by *ShowLobby*.
- **The lobby window** is therefore `WndClass0`, `WS_POPUP`, owned by `SSSI Empire Earth`, at the game window's rectangle (1024×768 at the menus) when fullscreen.
- **Other `MSWindow`s** exist in WONAPI but EE does not reach them for dialogs and popups: `DialogLogic` (0x7c7a20) makes a window per dialog only when WindowManager+0xa5 is set, and `WindowManager` *DoPopup* (0x7190e0) makes a native popup (`WS_POPUP`, `SW_SHOWNA`, `WM_MOUSEACTIVATE` → `MA_NOACTIVATE`) only when +0xa5 is set or the popup's flags have bit 16. EE clears +0xa5 at 0x67dd3f, so the network-adapter, name and create-game dialogs, combo-box lists, menus and tool tips are drawn inside `WndClass0` [static; whether any popup sets bit 16 is for the window-tree dump, §11]. Two more creators (0x721200, flag 0x40; 0x6ed1c0, flags 0x59) are WONAPI features EE's configuration does not use [inferred].
- **WndProc** 0x7bc7a0: on `WM_NCCREATE`/`WM_CREATE` it stores the `MSWindow*` from `CREATESTRUCT.lpCreateParams` in `GWL_USERDATA` (`SetWindowLongA(-21)`) and the HWND at +0xb8; messages before that go to `DefWindowProcA`; everything else to the handler 0x7bc950.

### 3.2 Messages (handler 0x7bc950)
| Message | What WONAPI does |
|---|---|
| `WM_CREATE` | slot 21 (0x7bd530): `GetDC(hwnd)` kept as the window's `DisplayContext`, the back buffer (`CreateCompatibleBitmap` of the client size), **`SetTimer(hwnd, 0, 50, NULL)`** |
| `WM_DESTROY` | `KillTimer`, clears `GWL_USERDATA` |
| `WM_MOVE`, `WM_SIZE` (restored/maximised) | size events; a resize re-creates the back buffer (0x715df0) |
| `WM_ACTIVATE` | when activated with a modal state (0x718cd0), `ShowWindow` of the root window |
| `WM_SETFOCUS` / `WM_KILLFOCUS` | focus events (modifier state reset first) |
| `WM_PAINT` | slot 20 (0x7bd660), §3.3 |
| `WM_CLOSE`, `WM_SYSCOLORCHANGE` | close event; colour scheme |
| `WM_MOUSEACTIVATE` | `MA_NOACTIVATE` for no-activate popups |
| `WM_INPUTLANGCHANGE` | re-reads the keyboard code page (`GetKeyboardLayout(0)`, `GetLocaleInfoA(LOCALE_IDEFAULTANSICODEPAGE)`) |
| `WM_NC*BUTTONDOWN` | closes popups |
| `WM_KEYDOWN`/`UP`, `WM_SYSKEYDOWN`/`UP` | key events (virtual key + modifier state); with +0xc5 also `PostMessageA(GetParent(hwnd), …)`, i.e. to the owner, the game window, which ignores them while the lobby is shown |
| `WM_CHAR`, `WM_IME_CHAR` | `MultiByteToWideChar(keyboard code page, …, 1 char)` → a UTF-16 character event |
| `WM_IME_COMPOSITION` with `GCS_RESULTSTR` | `ImmGetContext`, `ImmGetCompositionStringW(GCS_RESULTSTR)`, one character event per UTF-16 unit, `ImmReleaseContext` |
| `WM_SYSCOMMAND(SC_MAXIMIZE)` | caps the window at `SM_CXMAXIMIZED`/`SM_CYMAXIMIZED` and the window's maximum size |
| `WM_MOUSEMOVE`, `WM_[LRM]BUTTON{DOWN,UP,DBLCLK}`, `WM_MOUSEWHEEL` | mouse events from the client coordinates in `lParam` (the wheel's from screen coordinates) |
| `WM_SIZING` | minimum and maximum size |
| `WM_TIMER`, `WM_USER` | the *tick* |
| every handled message | ends with the window manager's *tick* (0x71a800) |

The *tick* (0x71a800) runs at most every 50 ms (`GetTickCount`): component timers and animations, popup and tooltip timing, the cross-thread event queue (completions from the socket threads), then each window's slot 2 (0x7bd3c0): `InvalidateRect(hwnd, dirty rectangle, FALSE)`. Mouse-exit tracking (slot 3, 0x7bd440) uses `GetCursorPos`, `WindowFromPoint` and `ScreenToClient`: a window not under the cursor gets a mouse-exit event.

### 3.3 Painting
1. A component change marks a dirty rectangle in the root container (+0x29 flag, +0x84..+0x90 rectangle).
2. The tick calls `InvalidateRect(hwnd, dirty, FALSE)`.
3. `WM_PAINT` → slot 20 (0x7bd660): `BeginPaint`; if the dirty rectangle is not inside `rcPaint`, `InvalidateRect` it again; the root container paints into the **back buffer** through an `MSGraphics` on the back buffer's memory DC (slots 0x14, 0x18); then the back buffer's slot 1 (0x7bafb0) copies the **whole back buffer** to the window DC at (0,0) with **`BitBlt(SRCCOPY)`** (clipped by the paint region); `EndPaint`.
4. Nothing is drawn to the window DC outside `WM_PAINT`, and nothing is drawn to the screen DC [static: the only window-DC draw is 0x7bafb0 from 0x7bd660].

So a correct HLE needs: a per-window surface behind the window DC, `WM_PAINT` generated from the update region, and the window's surface shown on screen.

### 3.4 Graphics primitives (`MSGraphics`, vtable 0x83e5e0)
- **State** (0x7ba3f0): a null brush (`CreateBrushIndirect(BS_NULL)`), a solid brush and a pen (`CreateSolidBrush(0)`, `CreatePen(PS_SOLID, 0, 0)`), `SetBkMode(TRANSPARENT)`. *SetColor* (slot 4, 0x7ba500) replaces both with `CreateSolidBrush(c)`/`CreatePen(PS_SOLID, 0, c)`, `DeleteObject`s the old ones and sets `SetTextColor(c)` (colours stored as 0xRRGGBB and swapped to COLORREF).
- **Shapes:** `GetPixel` (2), `SetPixel` (3), `Polyline` (5), `Rectangle` (6, 8; slot 7 goes to the base `Graphics` at 0x7124b0), `Polygon` (9, 10), `Ellipse` (11, 12), `Arc` (13), clip with `IntersectClipRect` (1) and `SelectClipRgn(hdc, NULL)` (0).
- **Translucent fill** (slot 14, 0x7ba770): only if slot 18 (0x7ba6d0) found **`msimg32!AlphaBlend`** (`LoadLibraryA("msimg32.dll")`, `GetProcAddress`). It fills a 100×100 32-bpp `CreateDIBSection` tile with the colour and `AlphaBlend`s it over the rectangle tile by tile with a constant alpha (`BLENDFUNCTION{AC_SRC_OVER, 0, alpha, 0}`). Without msimg32 nothing is drawn.
- **Images** (`MSNativeImage`, vtable 0x83e64c): a `CreateCompatibleBitmap` (or a `CreateDIBitmap`) selected into its own `CreateCompatibleDC`; size read back with `GetObjectA` + `DPtoLP`. Drawing (slot 1, 0x7bafb0) is `BitBlt(SRCCOPY)`, or with a mask: `SetTextColor(0)`, `SetBkColor(0xffffff)`, `BitBlt` of the **1-bpp mask with `SRCAND`** (0x8800c6), then the image with **`SRCPAINT`** (0xee0086), so monochrome-to-colour conversion through the DC's text and background colours matters. Copies (slot 3) are `CreateCompatibleBitmap` + `BitBlt(SRCCOPY)`.
- **Decoding** is the EXE's own: `PNGDecoder` (libpng + zlib 1.1.3 in the EXE), `JPGDecoder` (libjpeg 6b), `BMPDecoder`, `WIMDecoder` → `RawImage32`/`RawImage8`/`RawImage1` → `CreateDIBitmap(hdc, &bih, CBM_INIT, bits, &bmi, DIB_RGB_COLORS)` with **top-down** (negative height) **32-bpp, 8-bpp (palette, one entry cleared for transparency) or 1-bpp** headers (0x7bb680, 0x7bb790, 0x7bb920; BMP files 0x7b9ae0). The skin is 39 PNGs and 6 JPEGs in `Data\WONLobby Resources\{Skin,Icons,Images}` (buttons, tabs, scroll bars, dialog frames, `ArrowCursor.png`, backgrounds `main_menu_compilation.jpg`, `bkg_german.jpg`, a tiled `MainBackgroundTile.jpg`). No OS image codec, no resources from the EXE.
- **Cursors** (`MSCursor`): the window manager's stock set (§1), and skin cursors from images through `CreateIconIndirect(ICONINFO{fIcon = FALSE, hotspot, hbmMask, hbmColor})` (0x7bb4a0, 0x7bb5c0); set with `SetCursor` (MSWindow slots 6, 7). The class cursor is NULL and WONAPI does not answer `WM_SETCURSOR`, so its `SetCursor` sticks.

### 3.5 Text (`MSFont`, vtable 0x83e5bc)
- **Creation** (0x7ba0d0): `CreateCompatibleDC`, **`CreateFontA(-MulDiv(points, 96, 72), 0, 0, 0, bold ? 700 : 400, italic, 0, 0, ANSI_CHARSET, 0, 0, DEFAULT_QUALITY, DEFAULT_PITCH, face)`**, `SelectObject`, `GetTextMetricsA`. Underline is drawn by WONAPI as a line, not by the font. The 96 is a constant: the height ignores the device's DPI.
- **Use:** `SelectObject` (slot 1), **`TextOutW(hdc, x, y, str, n)`** (slot 2), **`GetTextExtentPoint32W`** (slot 3) and **`GetCharWidthW`** (slot 4) for layout and wrapping. Strings are UTF-16 throughout (the lobby protocol is UTF-16, netcode.md §3).
- **Faces** (`Data\WONLobby Resources\_GameResource.cfg`, the active block): `DefaultFont` Tahoma 10, `BoldFont` Tahoma bold 10, `TitleFont` Tahoma bold 12, `MediumFont` Tahoma bold 11, `LargeFont` Tahoma bold 16, `HugeFont` Tahoma bold 24 points, i.e. heights **−13, −15, −16, −21, −32**. `_LobbyResource.cfg`, included after it (`_LobbyMaster.cfg`), names Arial 9 and **"TimesRoman"** bold 10/12 for three of the same fonts; which definition wins is WONAPI's config semantics [static: both files; the trace's `CreateFontA` faces decide, §11]. "TimesRoman" is no Windows face; GDI's mapper substitutes.

### 3.6 Keyboard, IME, clipboard
- Text fields (`MSInputBox`, `ChatCtrl`) take `WM_CHAR` from `TranslateMessage`, converted with the keyboard layout's ANSI code page; IME results arrive as `WM_IME_COMPOSITION`/`WM_IME_CHAR` (the game's own WndProc also handles IME, but not while the lobby is shown).
- **Clipboard** (0x7b9c70, 0x7b9e00): copy is `OpenClipboard(NULL)`, `EmptyClipboard`, `GlobalAlloc(GMEM_MOVEABLE|GMEM_DDESHARE)`, `GlobalLock`/`GlobalUnlock`, `SetClipboardData(CF_UNICODETEXT or CF_TEXT)`; paste is `GetClipboardData(CF_UNICODETEXT)`, else `CF_TEXT`, **read through the handle as if it were a pointer** (no `GlobalLock`).

## 4. EXE+0x3ba000–0x3bc000: the WONAPI Win32 back end [static]

The GDI block graphics.md §6 left open is WONAPI's platform layer, `WONAPI::MS*`: every user32/gdi32/imm32 call of the lobby's GUI is in 0x7b9ae0–0x7bdc00 and goes through these vtables. `python3 -I tools/lobby/lobby_imports.py` lists each function with its slot and imports from the function DB.

| Class (vtable) | Slots → Win32 |
|---|---|
| `MSWindow` (0x83e6ac) | 2 `InvalidateRect`; 3 mouse-exit (`GetCursorPos`, `WindowFromPoint`, `ScreenToClient`); 4 `SetWindowTextA`; 5 `SendMessageA`; 6, 7 `SetCursor`; 8 `ShowWindow(SW_SHOW / SW_SHOWNA / SW_HIDE)`; 9 `ShowWindow(SW_MINIMIZE / SW_RESTORE)`; 10 `SetWindowPos`; 11 `DestroyWindow`; 12 *Create* (`RegisterClassA`, `CreateWindowExA`, `LoadIconA`); 13 `MoveWindow`; 14 `GetWindowRect`, `GetClientRect`, `ClientToScreen`; 15 `SetFocus`; 17 `SetCapture`/`ReleaseCapture`; 19 `IsWindowVisible`; 20 *OnPaint* (`BeginPaint`, `EndPaint`, `InvalidateRect`); 21 *OnCreate* (`GetDC`, `SetTimer`). WndProc 0x7bc7a0, handler 0x7bc950 |
| `MSWindowManager` (0x83e684) | 1 `GetMessageA`, `TranslateMessage`, `DispatchMessageA`; 2 `EnumDisplaySettingsA` + `ChangeDisplaySettingsA` (unused by EE); 3 `EnumDisplaySettingsA`; 4 `PostMessageA(WM_USER)`; 7 `GetSystemMetrics(SM_CXSCREEN, SM_CYSCREEN)`; 8 `GetSysColor` ×15 (the colour scheme) |
| `MSGraphics` (0x83e5e0) | §3.4; 18 `LoadLibraryA("msimg32.dll")` + `GetProcAddress("AlphaBlend")` |
| `MSFont` (0x83e5bc) | §3.5 |
| `MSNativeImage` (0x83e64c), `MSNativeMask`, `MSNativeIcon`, `MSCursor` (0x83e674) | §3.4: `CreateCompatibleBitmap`, `CreateCompatibleDC`, `CreateDIBitmap`, `BitBlt`, `GetObjectA`, `DPtoLP`, `CreateIconIndirect`, `CreateCursor`, `DestroyCursor`, `DestroyIcon`, `DeleteDC`, `DeleteObject` |
| `BMPDecoder` (0x7b9ae0), `PNGDecoder` (0x7bd7c0, 0x7bd8d0), `RawImage1` (0x7bba50) | `CreateDIBitmap`, `_setjmp3` (libpng errors) |
| clipboard helpers (0x7b9c70, 0x7b9e00) | §3.6 |

The rest of WONAPI's GUI is platform-neutral C++ above these: `Window`/`WindowManager` (0x714d90–0x71ab50), `Component`/`Container`, the `MS*`/`WON*` widgets, layouts, `HTMLDocument` (the help pages), `ConfigParser` (the `.cfg` skin language). The lobby's layout, skin and strings come from the `.cfg` files in `Data\WONLobby Resources`.

## 5. API inventory and kit coverage

The lobby path's imports, from the function DB (`tools/lobby/lobby_imports.py`; counts are functions that call an API, not call rates), against the kit at `ee` de82255. Kit status from a read of `kit/runtime`, `kit/dx` and `kit/host` [static]; nothing here was run. Rates are [inferred] from the code in §3: **T** = per 50 ms tick (20/s), **P** = per repaint, **L** = at load, **E** = per user event.

### 5.1 user32 (46 in WONAPI, 9 in EE's glue)

| API | Lobby use | Kit | Gap that matters |
|---|---|---|---|
| `RegisterClassA`, `CreateWindowExA`, `DestroyWindow` | 1 class + 1 window (L) | implemented | class style stored, never read (no `CS_OWNDC`/`CS_DBLCLKS`); no non-client area (fine for `WS_POPUP`) |
| `DefWindowProcA` | most messages | partial | **`WM_CLOSE` also posts `WM_QUIT`** (user32.cpp:1378), which Windows does not; the game window's `WM_CLOSE` goes to `DefWindowProcA` while the lobby is shown |
| `GetWindowLongA`/`SetWindowLongA(GWL_USERDATA)` | object pointer | implemented | — |
| `GetMessageA`, `TranslateMessage`, `DispatchMessageA` | the loop (continuous) | implemented, blocking | — |
| `PostMessageA` | `WM_USER` wake from socket threads (E); key forward to the owner | implemented, cross-thread | — |
| `SendMessageA` | rare | implemented | — |
| `SetTimer`/`KillTimer` | `SetTimer(hwnd, 0, 50)` once; `WM_TIMER` T | partial | **id 0 is replaced by a fresh id** (user32_vcl.cpp:301), so `KillTimer(hwnd, 0)` at `WM_DESTROY` kills nothing; `WM_TIMER` is ordered before `WM_PAINT` (Windows: after) |
| `GetDC` | once per window, kept | implemented | a new DC with default state on every call (no `CS_OWNDC`); writes reach the window's surface, which is what WONAPI needs |
| `BeginPaint`/`EndPaint` | per paint (P) | implemented | `rcPaint` is always the whole client; harmless here |
| `InvalidateRect` | dirty rectangle, ≤ T | partial | the update region is a flag; harmless (WONAPI re-blits the whole back buffer) |
| `ShowWindow`, `MoveWindow`, `SetWindowPos` | show/hide/move (E) | implemented | **hide or move does not re-present** the old pixels |
| `GetWindowRect`, `GetClientRect`, `ClientToScreen`, `ScreenToClient` | layout, mouse (T) | implemented | — |
| `GetCursorPos`, `WindowFromPoint` | mouse-exit tracking (T) | implemented | position frozen while an exclusive DirectInput mouse is held (not the case in the lobby) |
| `SetCursorPos` | show and hide (E) | stub | the host pointer is not moved |
| `SetCapture`/`ReleaseCapture`, `SetFocus` | drag, focus (E) | implemented | no `WM_CAPTURECHANGED` |
| `SetCursor` | per component under the cursor (E) | stub | **the host never shows a shape**: always the macOS arrow |
| `ShowCursor` | show (E) | counter | hides the SDL pointer below 0 (only with `relative_mouse`) |
| `LoadCursorA`, `CreateCursor`, `CreateIconIndirect`, `DestroyCursor`, `DestroyIcon`, `LoadIconA` | 6 stock + skin cursors (L) | handles only | no pixels kept for cursors; `CreateIconIndirect` ignores the hot spot |
| `IsWindowVisible`, `Set/GetWindowTextA`, `GetClassNameA`, `GetClassInfoA`, `GetParent` | rare | implemented | `GetParent` returns the owner, as Windows does for a popup |
| `GetSysColor` | 15 colours (L) | fixed table 0–29 | — |
| `GetSystemMetrics` | `SM_CX/CYSCREEN`, `SM_CX/CYMAXIMIZED` | table | 61/62 return the display size: fine |
| `EnumDisplaySettingsA`, `ChangeDisplaySettingsA` | not called by EE [static] | implemented / validate-only | — |
| `Open/Close/Empty/Set/GetClipboardData` | copy, paste (E) | process-private | no macOS pasteboard bridge, no `CF_TEXT`↔`CF_UNICODETEXT` synthesis; `GlobalAlloc` handles are pointers, so WONAPI's handle-as-pointer paste works |
| `GetKeyboardLayout` | code page (L) | fixed 0x04090409 | — |

Mouse and keyboard routing (kit `user32_vcl.cpp`, `host/sdl/main.cpp`): mouse messages go to the capture window, else the window under the pointer, with `WM_MOUSEACTIVATE` and `WM_SETCURSOR`, but **no `WM_*BUTTONDBLCLK` is ever generated** and **`WM_MOUSEWHEEL` goes to the host's main window**, not to the window under the cursor or with focus; keys go to the focus window as `WM_KEYDOWN` + `WM_CHAR` (CP1252 for an ANSI window; characters outside CP1252 are dropped; no dead keys or Option combinations); no IME. Z-order is a global list with topmost windows last, but **owned windows are not kept above their owner**: activating EE's window (a click on it, or app re-activation through `host_main_window()`) raises it over the lobby popup, after which clicks go to the game window while the popup's pixels stay on screen.

### 5.2 gdi32 (30 in WONAPI), msimg32, imm32, kernel32

| API | Lobby use | Kit | Gap |
|---|---|---|---|
| `CreateCompatibleDC`, `DeleteDC`, `CreateCompatibleBitmap`, `SelectObject`, `DeleteObject`, `GetObjectA`, `DPtoLP` | back buffers, images (L, E) | implemented | `CreateCompatibleBitmap` of a bare memory DC is 32 bpp, not monochrome (WONAPI makes its masks with `CreateDIBitmap` instead) |
| `CreateDIBitmap(CBM_INIT)` | the skin's 45 image files, cropped into many bitmaps, and their masks; 32/8/1 bpp top-down (L) | implemented | — |
| `CreateDIBSection` | one 100×100×32 tile (L) | implemented | — |
| `BitBlt` | `SRCCOPY` per paint (P); `SRCAND` + `SRCPAINT` per masked image (P) | implemented, all 256 ROP3s, mono↔colour with the DC colours | output alpha forced to 0xff (correct for a window surface) |
| `SetBkColor`, `SetTextColor`, `SetBkMode` | per draw (P) | implemented | — |
| `CreatePen`, `CreateSolidBrush`, `CreateBrushIndirect(BS_NULL)` | **one pen + brush created and deleted per colour change** (P) | implemented | handle churn must not leak (it does not by design; worth a soak) |
| `Rectangle`, `Polygon`, `Polyline`, `Ellipse`, `GetPixel`, `SetPixel` | widgets (P) | implemented | — |
| `Arc` | round widgets (P) [inferred: radio buttons] | **a straight line** | needs a real arc |
| `IntersectClipRect`, `SelectClipRgn(NULL)` | per component (P) | implemented | rectangular only: enough |
| `CreateFontA`, `GetTextMetricsA` | 6 fonts (L) | implemented (TrueType, system faces) | Tahoma and Arial map to the macOS faces; **`"TimesRoman"` maps to the 8×16 bitmap cells** (no alias), unlike Windows' mapper |
| `TextOutW`, `GetTextExtentPoint32W`, `GetCharWidthW` | every text run (P), layout (E) | implemented | metrics against Windows at −13/−15/−16/−21/−32 Tahoma: not yet checked (0011 pending) |
| msimg32 `AlphaBlend` (by `LoadLibraryA`) | translucent fills (P) | implemented; `LoadLibraryA("msimg32.dll")` returns a pseudo-handle, `GetProcAddress` resolves the shim | — |
| imm32 `ImmGetContext`, `ImmGetCompositionStringW`, `ImmReleaseContext` | `WM_IME_COMPOSITION` (E) | stubs (NULL, 0, 1) | no `WM_IME_*` is generated |
| kernel32 `MultiByteToWideChar` | every `WM_CHAR` (E) | partial | **code page ignored, bytes zero-extended**: CP1252 0x80–0x9F (€, „, …, ™, œ) come out wrong |
| kernel32 `GetLocaleInfoA(LOCALE_IDEFAULTANSICODEPAGE)` | code page (L) | "1252" | — |
| kernel32 `LoadLibraryA("wininet.dll")` | §8 | fails cleanly (NULL, error 126) | intended |
| wininet `InternetGetConnectedState` | §8 | **missing** (by design) | — |
| kernel32 threads, events, critical sections, `GetTickCount`, `QueryPerformance*`, `_beginthreadex` (msvcrt) | WONAPI threads | implemented | — |
| wsock32 (23 in WONAPI) | LAN lobby | implemented (P5-A, netcode.md §12) | — |
| advapi32 `RegOpenKeyExA`/`RegCreateKeyExA`/`RegDeleteKeyA`/`RegCloseKey` | `SOFTWARE\Sierra\WONLobby` | implemented | — |

### 5.3 DirectDraw 7 and presentation: the real gap

- `IDirectDraw7::FlipToGDISurface` is `DD7_ok`: it returns `DD_OK` and does nothing (dx/ddraw7.cpp:1098); `GetGDISurface` is `DDERR_NOTFOUND`.
- Every window has a backing surface (`Window::surface`, ARGB, alpha 0 until drawn), and `gdi_present_windows` (gdi32.cpp:1274) composites the visible ones in z-order into a display-mode-sized frame for `host_display_present_window`, **over opaque black**, because the only primary it composites over is the legacy `ddraw.cpp` one (`ddraw_gdi_primary_active`, ddraw.cpp:4957, false under `ddraw7`). GDI presents happen from `PeekMessage`/`GetMessage`/`ReleaseDC`/`EndPaint` while a surface is dirty.
- `IDirectDrawSurface7::Flip` presents the D3D7 frame alone. The two producers replace each other's frames.
- **So today the lobby would appear on black** instead of over the main menu's last frame, would vanish behind any later D3D7 present, and would leave stale pixels when hidden; its cursor would be the macOS arrow; its clicks would work until something raises EE's main window over it. This is the work of §9.

## 6. The Wine failure, and how Windows composes the two windows

What the code does (§2) [static]: in fullscreen mode the lobby is a **separate top-level `WS_POPUP` owned by the game window**, at the game window's rectangle. The game calls `FlipToGDISurface` once, then presents nothing until the lobby hides. It neither changes the cooperative level nor hides its own window. So whatever was last presented on the game window stays there, and the lobby is visible only if the window system draws an owned window over its owner's last frame.

**Windows** [inferred, to confirm in §11]:
- GOG's `ddraw.dll` is a D3D9 wrapper whose `dxcfg.ini` says `display=desktop`, `presentation=windowed`, `scaling=fit`: the "exclusive" DirectDraw display is really a windowed D3D9 swap chain on the game window, at desktop resolution. Desktop Window Manager composes every top-level window, and an **owned window is always above its owner in the z-order** (a topmost owner makes its owned windows topmost too). So `WndClass0` is drawn over the game window's last D3D9 frame, and since the game stops presenting, nothing paints over the lobby. `FlipToGDISurface` probably does nothing in a D3D9 wrapper.
- One consequence for reference captures: on legion (2560×1600 at 150 %) the game window's rectangle, and so the lobby's size, may be the wrapper's desktop-sized window rather than 1024×768. The WON layouts adapt to the window size (`PercentLayout`, `SameSize`, …), so legion's screenshots may lay out differently from a true 1024×768 lobby. §11 asks for the rectangles.

**Wine** [measured by session b4, legion.md §7; mechanism inferred]: the lobby window exists and is visible (`winedbg info wnd` lists `WndClass0` under `SSSI Empire Earth`; it is owned, not a child: the style is never `WS_CHILD`), button sounds play, the loop runs, but the screen keeps the main menu's last frame. Wine's D3D9 swap chain on the game window (a GL or Vulkan/Metal surface in the window's Cocoa view) stays above the owned popup's window: either winemac's window levels do not lift an owned popup above a topmost or "fullscreen" owner, or the wrapper keeps its device window topmost. With Wine's built-in ddraw the game does not start (exit code 3). This is a Wine composition problem, not something the game does differently there.

**For the Mac** the lesson is that the host must do what DWM does here: once the game has flipped to GDI and stopped presenting, show the guest's GDI top-level windows in z-order over the last DirectDraw frame (§9.1).

## 7. Leaving the lobby [static]

- **Back to the main menu** (result 0): *shutdown* (0x67c58f → 0x67e74d) hides the lobby (*HideLobby*, §2), runs the window manager's last tick (0x718650), destroys the lobby objects (0x74d430), shuts down and deletes `WONAPICoreEx`, releases the window manager and the window (`DestroyWindow`, `UnregisterClassA` is not called for `WndClass0`), restores the music volume (`UIManager::SetMusicVolume`) and returns from the button handler. The main menu is presented again by the render thread's next frame. No cooperative-level or mode change: the menus never left 0x513 at 1024×768.
- **Start game** (results 1 and 2): after the lobby room's *Start game* (`0x08/0x07 StartGame` on TCP 33336, netcode.md §3.2), the run loop:
  1. joins (0x67e310: `NESynchronousClient::Connect` to the host, `"EEService"`) or hosts (an `EEALobbyAction` for 0x67e1d6: the game server, seed, `StartGame`);
  2. pushes the old multiplayer form 0x1e under the game, with **`EELobbyGameDoneEvent`** as its open event (0x67e659);
  3. tells the lobby a game is running (0x74daf0) and **hides** it (*HideLobby*): DirectInput resumes, the render thread presents again;
  4. returns from the button handler, and the game loads through the usual path: the menu → game switch that sets the in-game mode (1920×1080 in legion's registry; legion.md §5.1 saw DirectDraw and D3D9 re-created there) and keeps the cooperative level 0x513.

  The lobby stays alive while the game runs (byte 0x943844 = 1; its LAN socket stays open, which is why `LobbyLeave` was captured mid-game, netcode.md §3.2).
- **After the game**, the UI returns to form 0x1e; its open event (0x67dbb8) disables the form's 18 controls and on its second call, with the lobby active, re-shows the lobby (0x67e6dd): *ShowLobby(1)* again (another `FlipToGDISurface`), `UIManager::Pop` if form 0x1e is on top, and the run loop again.
- **Quit** (result 3): shutdown, then `PostQuitMessage(0)`. A `WM_CLOSE`/`WM_DESTROY` of the game window while the lobby is active but hidden (during a game) also shuts the lobby down first (0x6622dd → 0x67c58f). **While the lobby is shown**, the game window's `WM_CLOSE` goes to `DefWindowProcA`: on Windows that is `DestroyWindow` of the game window (which destroys the owned lobby too) and no `WM_QUIT`, so the lobby's loop would go on with no windows. A host quit that reaches the game window as `WM_CLOSE` (the headless deadline does, boot.md §1) must not be sent there while the lobby is shown (§9.1, K4).

## 8. wininet `InternetGetConnectedState` [static]

- **Where:** 0x6ab070 loads `wininet.dll` once with `LoadLibraryA` and asks `GetProcAddress("InternetGetConnectedState")`. If the DLL or the export is missing it returns **"connected"** (true); otherwise `InternetGetConnectedState(&flags, 0) == TRUE`.
- **Who asks:** `InitLogic` (0x7c87b0), at lobby start (0x67dd3f → 0x74da00 → 0x7c8870) and on its retry event. If `WONLobby.cfg` has `CheckForInternet: true` (GOG's has) **and** the answer is "not connected", it posts the lobby's status event with code 3 (no Internet connection) and stops there. Otherwise it posts "connecting" (code 1) and starts the WON directory-server query (0x7d0ca0) for the `DirServer` list: `empireearth.m1/m2/m3.sierra.com:15101`.
- **It is on the lobby path but not on the LAN path.** The LAN tab (`LanLogic`, UDP 8585 / TCP 33336) does not depend on it.
- **What it must return:** nothing. Leave wininet unserved: `LoadLibraryA` fails, the lobby takes "connected", as on any Windows PC with Internet, and tries the directory servers. Those lookups must fail at once: `game.toml`'s `[network] offline_hosts` lists only `empireearth.m1.sierra.com`; add **`empireearth.m2.sierra.com` and `empireearth.m3.sierra.com`** (`WONLobby.cfg`; `zzWONVersion.cfg` names the same three hosts on port 15104) so no lookup waits on DNS. Answering "not connected" instead would show the lobby's no-Internet status, a flow Windows players with Internet never see.

## 9. Implementation plan

Game-neutral kit work on a `feat/gdi-windows` branch (owner: the P3-C user32/gdi32 family, so it is P5-B's only after P3-E merges), then a small ee-macos part. Sizes are focused working days, including kit tests; they assume P3-E's scripted input is merged.

### 9.1 Kit

| # | Piece | What | Size |
|---|---|---|---|
| K1 | **Two-layer presentation** | The presenter keeps the last D3D7 frame (layer 0) and a GDI overlay (layer 1: the composite of the visible top-level windows' surfaces, alpha 0 where nothing is drawn) and shows layer 0 ∘ layer 1, both at the display mode's size and scaled to the SDL window together. `gdi_present_windows` produces the overlay instead of a black-backed frame whenever a DirectDraw 7 primary exists; `Flip`/primary `Blt` replaces only layer 0. Metal: the overlay is one BGRA texture, uploaded only when dirty, drawn as a second full-screen quad with alpha; software/headless: the same blend on the CPU, so `ee_boot --frames` and `host_display_present_window` dumps show the lobby. No GPU readback. | 4–5 |
| K2 | `FlipToGDISurface` and re-presenting | `IDirectDraw7::FlipToGDISurface`: mark the primary "GDI showing" (layer 0 frozen; a later `Flip` clears it) and return `DD_OK`; `GetGDISurface` can stay `DDERR_NOTFOUND`. Hiding, moving or destroying a visible top-level window marks the overlay dirty, so its old pixels go. | 1 |
| K3 | **Owned-window z-order and activation** | An owned window stays above its owner (and becomes topmost with a topmost owner); activating or clicking the owner never raises it over its owned windows; the host's app re-activation activates the topmost enabled top-level window of the main thread, not `host_main_window()` blindly; activation between two windows of one thread sends `WM_ACTIVATE`/`WM_NCACTIVATE`, never `WM_ACTIVATEAPP` (EE's WndProc minimises itself on `WM_ACTIVATEAPP(FALSE)`, §2). | 2 |
| K4 | Message details | `SetTimer(hwnd, 0, …)` keeps id 0 and returns non-zero; `KillTimer(hwnd, 0)` kills it; `WM_TIMER` after `WM_PAINT`. `DefWindowProcA(WM_CLOSE)` → `DestroyWindow` only (no `WM_QUIT`; check the kit's other games for reliance first). `WM_*BUTTONDBLCLK` for `CS_DBLCLKS` classes (Windows' double-click time and rectangle). `WM_MOUSEWHEEL` to the focus window with screen coordinates. `CS_OWNDC`: one persistent DC per window (low priority: WONAPI only needs the handle to keep working). Host quit (Command-Q, window close) while a GDI top-level window of the main thread is shown and active: send `WM_CLOSE` to that window (WONAPI's close event) rather than to `host_main_window()`; ee-macos can map it to the lobby's exit if the trace shows that is what Alt+F4 does. | 2–3 |
| K5 | **Cursor** | `ShowCursor` count and `SetCursor` drive the SDL cursor whenever the pointer is not relative: stock IDs → SDL system cursors (arrow, I-beam, size, hand, wait); `CreateIconIndirect` (keep the hot spot) and `CreateCursor` (AND/XOR planes) → `SDL_CreateColorCursor`, cached per handle. `SetCursorPos` warps the host pointer when not relative. Relative mode already ends when EE unacquires its mouse (`ISMouse::Suspend` sets cooperative level 6 and `Unacquire`, §2); test it. | 2–3 |
| K6 | **Text input** | `MultiByteToWideChar`/`WideCharToMultiByte` honour CP1252 (and `CP_ACP` = 1252), not Latin-1. Characters come from SDL text input (dead keys, Option, layouts) instead of `SDL_GetKeyFromScancode`. Characters outside CP1252 for an ANSI window go through the IME path WONAPI already handles: `WM_IME_COMPOSITION(GCS_RESULTSTR)` with a non-NULL `ImmGetContext` and `ImmGetCompositionStringW` returning the UTF-16 text (and `WM_IME_CHAR` for windows that pass it on). Lobby chat then takes any Unicode text, as the UTF-16 wire format allows. | 2–3 |
| K7 | GDI fidelity | `Arc` (and `Chord`/`Pie` on the same code) as real elliptical arcs; the font mapper: an unknown face falls back the way Windows' does (pitch/family/charset → a real face, `"TimesRoman"` → Times New Roman or whatever the trace shows), never the 8×16 cells; Tahoma regular/bold at −13, −15, −16, −21, −32 checked against the 0011 vectors when they land; a soak for pen/brush churn. | 2–3 |
| K8 | Clipboard | Bridge `CF_UNICODETEXT` (and synthesised `CF_TEXT`) to the macOS pasteboard through SDL; same-window re-open of `OpenClipboard` as Windows allows. | 1 |
| K9 | Scripted input for GDI windows | `host/input_script.h`: a `pointer system` mode whose `move`/`click` are absolute positions in display-mode pixels delivered as host mouse events (so they route by `WindowFromPoint`, not by the game's `RTSMouse`), `type TEXT` through the text-input path, and `wait pixel`/`wait change` on the composited frame. | 1–2 |

**Kit total: about 17–23 days (3½–4½ weeks).** The minimum that makes the lobby visible and clickable is K1, K2, K3 and K4 plus the `MultiByteToWideChar` fix: about 1½–2 weeks. K5–K8 are fidelity (cursor shapes, non-ASCII chat, fonts, paste).

Not needed for EE: native child controls (none exist), `ChangeDisplaySettingsA` (not called), standard dialogs, more than rectangular clip regions, a GDI surface for DirectDraw.

### 9.2 ee-macos

| # | Piece | Size |
|---|---|---|
| E1 | `game.toml [network] offline_hosts` += `empireearth.m2.sierra.com`, `empireearth.m3.sierra.com` (§8) | 0.1 |
| E2 | `tests/lobby/*.script` and `tools/test.py --lobby` (§10), reference comparison with legion's `step-<n>.png` | 3–4 |
| E3 | The LAN check inside the test: `RECOMP_NET_TRACE=1` (or a loopback capture) shows the host's `LanGameAdvert` for the scripted game name, decoded with `tools/lan/eelan.py` | 1 |
| E4 | Then P5-B proper: Mac↔legion lobby sessions (the 8585 list, the 33336 room, chat both ways, *Start game*), netcode.md §12.4 | (next phase) |

**Windowed mode** (the fullscreen option off) is a different path (game window hidden, the lobby a plain top-level window, no DirectDraw involvement); K1's overlay over an empty layer 0 covers it, but it is not a target.

### 9.3 Status of K5–K8 (P5-D, kit `feat/lobby-text`, 2026-10-15)

All four are done in the kit, game-neutral, with tests. The ground truth is Windows 11 build 26200 on legion: code page and GDI-metric captures taken over `ssh` in `C:\EEPort\p5d` [measured], and the lobby traces 0021 and 0025 [measured]. The commits are on `ee` 844917f (K6a, merged) and then `feat/lobby-text` on `ee` f4eb731: K6b 5006cb8, K5 7af4ee3, K7 b2c2d94 with its follow-up 220ac8a, K8 ae58d00. The EE menus regression check (`tests/menus` settings, single-player, game-tools, against this kit) passes 3 of 3.

**K6a: code pages** (`runtime/codepages.cpp`).
- `MultiByteToWideChar` and `WideCharToMultiByte` follow Windows' tables for 1252 (`CP_ACP`), 437 (`CP_OEMCP`), 850, 10000 (`CP_MACCP`), 20127 and 28591. That covers 0x80–0x9F, best fit, the default character, every flag and the short-buffer behaviour.
- UTF-8 follows Windows' replacement rules, and `CP_SYMBOL` is supported. `GetCPInfo` and `IsValidCodePage` answer per code page.
- `crt_tests` replays the whole capture: 6 tables and 226 edge cases. The capture tool is `tools/codepages`.
- WONAPI's `MultiByteToWideChar(1252, …)` at 0x7bcdcf now turns `WM_CHAR 0xE9` into U+00E9 and 0x80 into €.

**K6b: text input.**
- The SDL host keeps text input on, so dead keys, Option characters, every layout and input methods arrive as text. A held key repeats instead of opening the accent picker.
- A key that types is posted already translated (`HOST_KEY_TEXT_FOLLOWS`), and its `WM_CHAR` is the text that follows. Return, Tab, Backspace, Escape and Ctrl/Command keys keep their old path, as do the host's own scripted and on-screen keys (SDL keyboard id 0).
- Text reaches an ANSI window such as `WndClass0` as `WM_CHAR` in 1252. Legion's 0025 trace confirms `é` arrives as `WM_CHAR 0xE9` with no IME messages.
- Text that 1252 cannot spell arrives as an IME result: `WM_IME_STARTCOMPOSITION`, `WM_IME_COMPOSITION(GCS_RESULTSTR)`, `WM_IME_ENDCOMPOSITION`. `ImmGetContext` is non-NULL and `ImmGetCompositionStringW` returns the UTF-16 text, which is the path WONAPI's 0x7bcba8 reads.
  - The result travels with its message, so two queued results each read their own.
  - `DefWindowProc` turns a result into `WM_IME_CHAR` and then `WM_CHAR` ('?') for a window that does not handle it.
- **For P5-C (K9):** scripted `type TEXT` can call `host_text_input(utf8)` (`host/input_gate.h`) under the baton, which is the path SDL's text events take.

**K5: cursor.**
- The cursor shims moved to `runtime/user32_cursor.cpp` and keep each shape:
  - stock `IDC_*` handles map to SDL system cursors (`IDC_IBEAM` to the I-beam, which is what WONAPI sets over edit fields);
  - `CreateCursor`'s AND/XOR planes and `CreateIconIndirect`'s bitmaps become colour cursors with their hot spots. The 32×32 hand keeps its hot spot 12,4.
- `SetCursor(NULL)` hides the pointer over the window; WON draws its own yellow arrow then (0025 §7).
- `SetCursorPos` warps the Mac pointer through the scene mapping. *ShowLobby* calls it while the DirectInput mouse is still exclusive, so the move waits for relative mode to end and then puts the pointer where the game's cursor was.
- Headless: `RECOMP_TRACE_CURSOR=1` logs every change (`cursor: SetCursor -> ibeam (IDC 32513, …)`), and `host_guest_cursor_state()` and `host_guest_cursor_description()` answer in-process.
- Hiding still needs `[runtime] relative_mouse`, which EE sets.

**K7: GDI.**
- `Arc`, `Chord` and `Pie` are real elliptical arcs: the matching part of `Ellipse`'s outline. They run counterclockwise unless `SetArcDirection(AD_CLOCKWISE)`; `Chord` fills its side of the chord and `Pie` its wedge. `ArcTo` and `AngleArc` move the current position. They are tested against a reference raster written from the documented rules.
- A soak of 200,000 pen and brush changes leaves the object table flat.
- **Line metrics now match Windows 11.** GDI reads a VDMX table only through its Windows ANSI group (`bCharSet` 1). Tahoma's only group is for all glyphs, so its line heights come from the Windows ascent and descent: −16 is 19 px, not 21.
  - A cell (positive) height is the TEXTMETRIC height, split by the Windows ascent.
  - Its em is the rounded em part of the cell, one smaller while that em's scaled Windows ascent and descent overflow the cell (Tahoma 20 is a 16-pixel em). Wine has the same rule.
  - Held by `gdi_metrics_windows11`: 169 requests over six faces, with 0 differences in face, height, ascent, descent and leadings.
  - Held by `gdi_metrics_windows11_tahoma`: all 89 Tahoma requests also agree in every advance (U+007F apart) and every string extent.
  - The lobby's seven Tahoma fonts match exactly in height, ascent, descent, internal leading and average width: −13/400, −13/700, −13 italic, −15/700, −16/700, −21/700, −32/700. These agree with 0025 §4.
  - What still differs is the font version: macOS 15 ships Tahoma 5.01 and Windows 11 Tahoma 7.05. The bounding box differs, so `tmMaxCharWidth` is 28 against 32 at −13, and U+007F's width differs.
- **"TimesRoman" is Arial on Windows 11** [measured], not Times New Roman: the mapper falls back by pitch and family, and `DEFAULT_PITCH | FF_DONTCARE` gives Arial. The kit now does the same for any unknown face (Arial, Times New Roman, Courier New, Comic Sans MS or Symbol by pitch, family and charset). With nothing installed it uses Open Sans, never the 8×16 cells (except fixed-pitch, raster, symbol and OEM requests). §3.5's guess is corrected by this.
- **Fonts available to the kit:**
  - Bundled: Open Sans 1.101 Regular and Semibold (Apache 2.0) and the 8×16 cells.
  - With `[runtime] system_fonts`, the user's installed fonts are read in place, never copied. macOS 15 has Arial (and Black, Narrow), Tahoma (regular, bold), Verdana, Times New Roman, Courier New, Georgia, Trebuchet MS, Comic Sans MS, Impact, Microsoft Sans Serif, Wingdings, Webdings and Andale Mono.
  - It does not have Segoe UI, Calibri, Consolas, Lucida Console, Palatino Linotype, or the raster faces.
  - No Microsoft font file is in either repository.

**K8: clipboard** (`runtime/user32_clipboard.cpp`).
- Windows' rules apply, including that the same window or thread may open the clipboard again while it has it open. `CF_UNICODETEXT`, `CF_TEXT` (1252) and `CF_OEMTEXT` (437) are converted into each other.
- `GetClipboardData`'s handle is the text itself, as 0025 §8 shows WONAPI needs: no `GlobalLock`, the bytes at the handle.
- The SDL host bridges text to the Mac pasteboard on the main thread:
  - a copy is published at `CloseClipboard`, with LF line ends;
  - another application's text is read only when the game pastes, with CR LF, and never merely because the clipboard changed.

**Open.**
1. The SDL-window paths (text input, cursor shapes, the warp, the pasteboard) are built but have not been run on a screen. The headless suites cover the runtime side, and the windowed check is §10's Metal run.
2. While an input method composes (marked text), SDL still sends key events, so Return that commits a composition also reaches the game. Western layouts are unaffected.
3. Cursor images are shown at guest-pixel size; they are not scaled with the window.
4. The request-0011 capture (`tools/gdi_metrics/cases.txt`, now with the lobby's sizes) would turn on the full `gdi_metrics_reference`, whose tolerances include `tmMaxCharWidth`. With macOS's older Tahoma that test would fail on the bounding box alone; `compare.py --only`/`--faces`/`--skip-chars` exist for that.

## 10. Tests

**Kit (game-neutral, headless):** a synthetic program per piece in `win32_ui_tests`/`dx_tests`:
- a DirectDraw 7 exclusive primary flips a known frame, then `FlipToGDISurface`, then an owned `WS_POPUP` with `CS_OWNDC` paints a back buffer through `BitBlt` in `WM_PAINT`: the presented frame is the D3D frame with the popup's pixels on top, and hiding the popup restores the D3D frame (K1, K2);
- activation and z-order: a click on the owner or an app re-activation keeps the owned popup on top and its clicks routed to it; no `WM_ACTIVATEAPP` between them (K3);
- `SetTimer(hwnd, 0, 50)` → `WM_TIMER` wParam 0 at 20 Hz, `KillTimer(hwnd, 0)` stops it; double clicks; wheel routing (K4);
- `MultiByteToWideChar(1252)` over 0x80–0x9F; an Option-key character and a non-CP1252 character reaching a window as `WM_CHAR`/`WM_IME_COMPOSITION` (K6);
- masked image blit (`SRCAND` mask + `SRCPAINT`) against expected pixels; `Arc` against a reference raster; font fallback for an unknown face (K7).

**ee-macos (`tools/test.py --lobby`, headless, strict, software renderer, fresh user directory; one at a time because of the fixed ports):**
1. `lobby-open`: main menu → *Multiplayer* → `wait change` until the lobby's first frame → dump `lobby-first.png` → *Back* (or the lobby's exit) → main menu (`wait change`, the menu again) → *Exit Game*. Passes if the lobby's frame differs from the menu's, the menu frame afterwards matches the one before (the render thread resumed), no strict-mode stop, clean exit.
2. `lobby-lan-host`: *Multiplayer* → LAN tab → *Host*/*Create game* → name `mac-ref` → the room → `type hello mac` + Enter → dump `room.png`; open one dropdown, dump, close; leave the room → back → main menu → exit. Passes if every `wait` saw its screen, the chat line is in the room's chat area (`wait pixel` on the text colour where it lands) and the trace shows a `LanGameAdvert` named `mac-ref` on UDP 8585 (E3).
3. **Against Windows:** each dump against legion's `step-<n>.png` at the same step, perceptually (as phase3.md asks for the menus), after scaling legion's to the Mac's size if the trace shows a desktop-sized lobby (§11 item 2). Text-heavy regions get a looser threshold until K7's metric check passes.
4. **The app on Metal** (when someone is at the Mac): the same `lobby-open` script through `RECOMP_INPUT_SCRIPT`; checks that the overlay shows over the menu's last frame and that the macOS pointer takes WONAPI's shapes.

The click coordinates come from the trace's screenshots (§11 item 11) and stay in the scripts; they are window-client coordinates of a 1024×768 lobby.

## 11. For the Windows trace (0021)

Request 0021 (`to-legion/0021-lobby-reference.md`, status *working* when checked) asks for the ordered user32/gdi32/ddraw trace, the window trees and screenshots in `C:\EEPort\captures\lobby-trace\`; nothing has landed yet (`dir` says *File Not Found*). Static analysis already answers its central question (one custom-drawn window, no standard controls). What the trace should confirm or measure, as an addendum for the legion agent (0021a):

1. **Window tree** at steps 3, 4 and 5: `WndClass0` with style `WS_POPUP` (plus `WS_VISIBLE`), no extended style, **owner** `SSSI Empire Earth`, no children; whether any further `WndClass<n>` window appears while a dropdown or tooltip is open (a popup with flag bit 16, §3.1). For both windows: z-order, `WS_EX_TOPMOST`, window and client rectangles. Whether GOG's wrapper creates windows of its own.
2. **Sizes under DPI scaling:** the game window's rectangle, the lobby's rectangle, `GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)` and `EnumDisplaySettingsA(ENUM_CURRENT_SETTINGS)` as the game sees them at 150 % (is the lobby 1024×768, or desktop-sized?).
3. **DirectDraw:** exactly one `IDirectDraw7::FlipToGDISurface` per lobby entry (return address EXE 0x67e0fe) and what the wrapper returns; **no** `SetCooperativeLevel`, `SetDisplayMode` or `ChangeDisplaySettingsA` from *Multiplayer* to back; **no `Flip`** while the lobby is visible; the first `Flip` after leaving.
4. **Fonts:** every `CreateFontA` (face, height, weight, italic, charset), `GetTextFaceA` after `SelectObject` (what GDI substituted for `"TimesRoman"`, if asked), and `GetTextMetricsA` for each (height, ascent, descent, internal leading, average and maximum width). This decides Tahoma vs Arial/TimesRoman (§3.5) and gives the metric targets.
5. **Painting:** per `WM_PAINT`, one `BitBlt(SRCCOPY)` from a memory DC to the window DC; `WM_PAINT` and `WM_TIMER` rates idle and while typing; confirm nothing draws to the window DC outside `BeginPaint`/`EndPaint` and nothing to `GetDC(NULL)`.
6. **GDI set:** the ROPs `BitBlt` is given (expect `SRCCOPY`, `SRCAND`, `SRCPAINT` only), the `CreateDIBitmap` header formats (expect top-down 32/8/1 bpp), `CreateDIBSection` (expect only 100×100×32), whether `msimg32.dll` loads and how often `AlphaBlend` runs with which alphas.
7. **Cursor:** the `ShowCursor` sequence, which `SetCursor` handles (stock IDs, `CreateCursor`, `CreateIconIndirect`), and whether the game's own software cursor disappears.
8. **Input:** in the room's chat box type `hello mac`, then one non-ASCII character (é) and a paste (Ctrl+V of `paste-test`): the messages (`WM_KEYDOWN`, `WM_CHAR`, any `WM_IME_*`), and whether paste works at all (WONAPI reads the clipboard handle as a pointer, §3.6).
9. **Activation:** the message sequence to both windows when the lobby appears and disappears (`WM_ACTIVATE`, `WM_NCACTIVATE`, `WM_ACTIVATEAPP`, `WM_SETFOCUS`, `WM_KILLFOCUS`), and the thread ids of `CreateWindowExA(WndClass0)` and the `GetMessageA` loop against the game window's thread.
10. **Internet check:** `LoadLibraryA("wininet.dll")`, the `InternetGetConnectedState` result, the `gethostbyname` calls (m1, m2, m3) and their timing, and a screenshot of what the lobby shows first (status dialog, tab).
11. **Labels and coordinates** for the Mac's scripted test: the first screen, the LAN tab, the host/create dialog and its fields, the room, the chat box, the *Start game* and *Back* controls (window-client coordinates).
12. Optional: one short hosted game and the return to the lobby afterwards (a second `FlipToGDISurface`, `EELobbyGameDoneEvent`).

## 12. Reproduction

- **The function DB view** (no game bytes): `python3 -I tools/lobby/lobby_imports.py [--kit kit] [--md]` prints the `MS*` back end (§4) with vtable slots and imports, the lobby's import surface per DLL (§5) and, with `--kit`, which names the kit's shim tables mention.
- **Decompilation** was done on a scratch copy of `analysis/func-db/ghidra/exe/` (163 MB; copy it rather than opening the shared project) with PyGhidra's `DecompInterface` from the func-db venv, `analysis/func-db/.venv/bin/python`, about 7 s per batch of 100 functions. The functions read for this document are the ones the tables name. The decompiled text stays in scratch: it is derived from game code.
- **Kit coverage** (§5) is a read of `kit/` at `ee` de82255 (`runtime/user32*.cpp`, `runtime/gdi32*.cpp`, `runtime/kernel32.cpp`, `runtime/misc.cpp`, `runtime/msimg32.cpp`, `dx/ddraw7.cpp`, `dx/ddraw.cpp`, `host/sdl/main.cpp`, `host/present*.cpp`).
- **Lobby resources:** `Data\WONLobby Resources\*.cfg` and `WONLobby.cfg`, `dxcfg.ini` in the GOG folder (read, not copied).
