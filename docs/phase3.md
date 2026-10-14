# Phase 3: platform → M2 (main menu, natively)

Started 2026-10-14, in parallel with the end of Phase 2 (M1 waits only on legion captures). Context: [PLAN.md](PLAN.md) §5.3–§5.5 and §7 Phase 3, [graphics.md](reference/graphics.md), [legion.md](reference/legion.md), [build.md](reference/build.md), [phase2.md](phase2.md) for the protocol (branches, worktrees, merging, hygiene). That protocol applies unchanged.

## M2 definition of done
- **Boot:** the GOG `Empire Earth.exe` and all eight DLLs, translated, boot natively on arm64 macOS in **strict mode** (no unknown calls or traps). The engine's plugin scan finds the translated plugins.
- **Movies:** the intro Bink movies play, or are skippable, and the **main menu renders** correctly at 1024×768. The check is a perceptual comparison with Windows reference screenshots from legion.
- **Navigation:** menu navigation works with mouse and keyboard, covering Options (all tabs), Single Player → Random Map setup, Scenario Editor entry, and the civ builder. Exiting is clean.
- **Out of scope:** audio (Phase 4) and the Multiplayer lobby (Phase 5). The lobby must not crash, but may stay blank.
- **Lifecycle:** windowed and fullscreen (macOS Space) modes, Retina-aware presentation, and Cmd-Tab in and out without losing the device (re-acquire via `WM_ACTIVATE`).

## Workstreams and ownership
Ownership matters because parallel work touches the same runtime. Only the owner edits a shim family. Others request changes in their report.

| ID | Name | Owns | Where |
|---|---|---|---|
| P3-A | exe-boot | Integration: an app target loading all 9 modules with the EXE as main image; `game.toml` for all modules; the VFS (case-insensitive game dir, user data in `~/Library/Application Support/EmpireEarth/`); the **registry** emulation seeded with GOG defaults (CD key read from the player's GOG install script); **kernel32/advapi32/shell32/version/winmm/ole32** gaps found while booting; a null-device boot path until B/C land; boot-trace triage to the main loop. | kit `feat/boot` + ee-macos `phase3/boot` |
| P3-B | d3d7-metal | **ddraw/d3d** shims and the host renderer (graphics.md §10–§11 checklist): DD7/Surface7/D3D7/Device7/VB7, FVF `0x152`, vertex T&L with 8 lights and fog, the stage-0 combiner, render-to-texture, mip chains, 32/16-bit formats, Flip preserving the back buffer, GPU Blts; the TnL plugin variant; Bink presentation via the kit. Offscreen render tests. | kit `feat/d3d7` |
| P3-C | win32-ui | **user32** (window, message queue, `WM_*` synthesis from SDL3, window procs, activation and focus, cursor and capture, clipboard as needed), **gdi32** (DCs, DIB sections, fonts and text via TrueType/CoreText with Windows-like metrics, `GetDC(NULL)` measurement), **dinput** (keyboard/mouse from SDL3, buffered data, the 256-record trap), **imm32** stubs, Cmd-key mappings. | kit `feat/win32-ui` |
| P3-D | references | **legion operator (mailbox):** a boot API trace to the main menu, reference screenshots of every M2 menu screen at fixed settings, and GDI text-metric vectors for the UI fonts. | legion |

Every merge goes through the main session, as in Phase 2. P3-A rebases often, because it integrates B and C as they land.

## Verification
- **Strict-mode boot log:** zero unknown calls or traps to the main menu. Every implemented API is unit-tested in the kit (game-neutral).
- **Screenshots:** perceptual comparison with the legion references at the same resolution and settings, with a documented threshold. Text metrics are checked against the Windows GDI vectors.
- **Cross-check:** the boot API sequence compared with the Windows trace. Order differences are fine; missing functionality isn't.

## Status
| ID | State | Result |
|---|---|---|
| P3-A exe-boot | running | |
| P3-B d3d7-metal | **merged** (`ee` 3534522) | DD7/D3D7 shim behind a C seam (`dx/d3d7_host.h`). Metal renderer plus a software reference renderer (test oracle; headless via `RECOMP_D3D7_SOFTWARE=1`). All 50 COM methods and 3 exports; fixed-function T&L with 8 lights; fog; stage-0 combiner; render-to-texture; mips; Flip preserves the back buffer; GPU-side Blts. 3,694 headless checks; 21/22 GPU scenes within one level per channel, the 22nd off by one pixel; ~1.1 ms CPU per measured in-game frame. Opt-in: `[runtime] ddraw7 = true`. |
| P3-C win32-ui | **merged** (`ee` d4bac5b) | user32 74/74, gdi32 36/36, dinput, imm32 7/7. Per-thread queues; sent and posted messages across threads; activation and focus modelled on EE's own WndProc (self-minimise + wait for a worker); DirectInput exclusive/foreground rules and the N−1 buffer; Windows-measured text on macOS fonts (Tahoma −11 → 13/11/2, Arial −12 → 15/12/3); a GDI surface-DC bridge for D3D7. 461 + 208 checks. Windows metric vectors pending (legion 0011). Config: `relative_mouse`, `system_fonts`. |
| P3-D references | legion 0010 (boot trace, screenshots) + 0011 (GDI metrics via the kit tool) queued | |
