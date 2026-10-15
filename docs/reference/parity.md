# Parity with Windows: the menus and the first game against legion's references

Status: P3-F (parity), 2026-10-15. The Mac's frames, from the menu test (`tools/test.py --menus`, [menus.md](menus.md)), are compared with legion's Windows reference screenshots of the same screens (P3-D: `C:\EEPort\captures\menus-ref`, `ingame-ref`; fetched into the ignored `analysis/parity/`). Context: [phase3.md](../phase3.md), [graphics.md](graphics.md), [legion.md](legion.md), [rmg.md](rmg.md).

Labels: **[measured]** = `tools/parity/compare.py` and the runs below; **[static]** = read from the binaries; **[inferred]** = reasoning.

---

## 1. Running it

```sh
.venv/bin/python tools/test.py --menus                     # the Mac's frames: build/menus/<scenario>/*.png
scp -r 'legion:C:/EEPort/captures/menus-ref' 'legion:C:/EEPort/captures/ingame-ref' analysis/parity/
.venv/bin/python tools/parity/compare.py [PAIR...]           # every pair whose two files exist
.venv/bin/python tools/parity/compare.py --pair MAC.png REF.png [--cursor X,Y] [--mask X,Y,W,H]
```

- **How a pair is compared.** The Mac's frame takes the path legion's took: GOG's ddraw wrapper scales the 4:3 game frame up to the 2133×1600 crop of legion's 2560×1600 panel, and `windows/refgrab.py` area-averages the crop back to 1024×768 (800×600 in game). A bilinear wrapper fits best (SSIM on Game Tools: bilinear 0.934, bicubic 0.929, Lanczos 0.927, nearest 0.922, a direct resize 0.923) [measured]. The two are aligned by the best integer shift (≤ 3 px); both cursors are masked.
- **What it reports.** SSIM on luminance (7×7 windows) for the frame and at half size ("coarse", which forgives filtering and one-pixel text), the mean absolute difference per channel, a per-channel fit ref = a·mac + b and a gamma estimate over pixels whose structure matches, and the regions that differ (16-px blocks below SSIM 0.6, boxed). `analysis/parity/out/NAME-side.png` is Mac | Windows | SSIM map, `NAME-diff.png` the difference ×4, `report.json` the numbers.
- **What a match looks like.** A screen with no real difference scores SSIM ≈ 0.92–0.94 and coarse ≈ 0.95–0.96 with a gamma of 1.00 ± 0.02: what remains is the wrapper's filter, the cursors and text rasterization (§2).

## 2. Differences that are expected

| Class | What | Cause |
|---|---|---|
| Scaling | Background art slightly softer or sharper; the whole-frame residual (mean \|d\| 4–7) | GOG's ddraw wrapper scales legion's frames to its panel; the Mac's frames are the game's own pixels. Simulated, not undone |
| Cursor | The Mac's cursor where the last click was, legion's parked at (42,403) in the menus and (690,662) in the editor; a tooltip on the Audio and Interface tabs (the Mac's cursor rests on the tab) | Scripts; masked |
| DPI | Every font sized by `UIFontTable::CreateClosestFont` (LLE+0x6f179) is 1.5× smaller on legion: Options (all tabs), the Civilization Builder, the Game Setup labels | That function asks `GetDeviceCaps(LOGPIXELSY)` and makes a font of `px·72/dpi + 1` points [static]. GOG's `ddraw.dll` calls `SetProcessDpiAwarenessContext`, and legion's panel is 144 DPI (legion.md §1), so EE there sees 144 where a DPI-unaware program, and the Mac, see 96. The Mac matches EE on an ordinary 96-DPI Windows (and the layout the game was made for). With `RECOMP_GDI_DPI=144` (kit, for comparisons only) the Mac's layouts match legion's: Video 0.858 → 0.924 SSIM, Audio 0.902 → 0.938, Interface 0.880 → 0.932, Civilization Builder 0.871 → 0.922 [measured] |
| Text rasterization | Small text is thinner and greyer on the Mac (the copyright line's `ll` in "All" are faint), bold text heavier ("Player Name", Tahoma −16 bold: 25 % more ink than legion's), and a few glyphs sit differently inside an unchanged advance (a gap after `A` in "Area") | The kit draws outlines unhinted, antialiased, linear coverage; Windows draws Tahoma hinted and, below its `gasp` threshold, bi-level. Advances and line breaks are Windows' (the kit's measured metrics): the ink of "Player Name" spans x 222–323 on the Mac and 221–322 on legion [measured] |
| Machine | Screen Resolution lists the machine's modes (legion 2560×1440…, the Mac its own display's); the Interface tab's adapter (legion's registry names its Ethernet adapter, a fresh Mac profile has `Network Adapter` 0 = `<default>`); the player name | Data, not the port |
| Random | The editor's grass tiles and the minimap's grass | Chosen at random per run |

## 3. The in-game reference run

Legion's run (`ingame-ref/notes.md`): 800×600×32, Small Continental, one computer player, Tournament, Deathmatch, Random Epoch (it came out Renaissance), seed 4242 forced by `seed_force.js` at the entry of EXE 0x66d402, frames at T+5/30/60/90 s, the Capitol clicked at (400,225) at T+68.8 s.

- **The Mac's equivalent** is `tests/menus/random-map.script`: `# menus: env RECOMP_EE_FORCE_SEED=4242` (ee_boot: a hook before EXE 0x66d402 writes the seed into its fifth argument and `*(0x91977c)+0x54`, as `seed_force.js` does; `run_menus.py` checks the log says so), `# menus: seed --width 800 --height 600 --player-name Unknown1634` (the registry's in-game mode, which headless would otherwise miss and fall back to 800×600×16; legion's profile name), legion's option values from `seed_user.py`, and the same dump times and click.
- **The same game** [measured]: the generator got 4242 (`[ee] random map: seed 961 forced to 4242`), and Random Epoch came out **Renaissance** on the Mac too, with Deathmatch's 15,000 food and wood, the same Capitol at the view's centre, the same tree at the top right and the same terrain patches.
- **"Choose Civilization".** With custom civilizations off, EE can open its civilization chooser instead of the first frame (EXE 0x66062b: settings `0x918410` +0x406, Allow Custom Civs, = 0 and the human player's +0xb46 "chosen" byte still 0) [static]. Legion's run went straight from the loading screen to the game (`session.mkv` frames 120–199); on the Mac the chooser opened in 3 of 5 seeded runs whose setup screens were pixel-identical, so the byte is set by something that races the first frame [inferred]. The script clicks the chooser's OK 15 s and 17 s after the loading screen (on the map, harmlessly, when the game came up instead). Open: which thread sets +0xb46 (EXE 0x668d2d, 0x4b538b) and why it is late on the Mac.
- **The first frames match** [measured]: a seeded Mac frame without the chooser against legion's T+5 s scores SSIM 0.842 / 0.913: the same Capitol, citizens within a few pixels of legion's (the frames are seconds apart), the same tree, terrain patches and HUD. What differs is the crown icons that blink (present in one frame, not the other), text rasterization in the top bar and resource counters, and the terrain's fine texture under the wrapper's filter.
- **(b) The minimap** [measured]: the same on both machines from the first frame: black except a small explored green patch around the Capitol with the player's blue dots, and the white view trapezoid, in the same place (`analysis/parity/crops/minimap-seeded.png`). The Mac's trapezoid is a crisp white line, legion's greyer after the wrapper's scaling. menus.md's "only the trapezoid" came from an unseeded run's frame.
- **(a) The two thin lines** are the same object on both machines: the Capitol's area-effect circle (`UseAreaEffectOverlays` 1), drawn by `DX7Rasterizer::Draw3DLine` (HR+0x3f5a) as one `DrawPrimitiveVB(LINELIST, 72 vertices)` in world space, textured with an 8×8 stripe texture (rows green, blue, blue, green, green, blue, blue, green; v runs 0→1 along each segment), point-sampled, no alpha test or blend, `ZENABLE` on with `ZFUNC` LESS and `ZWRITEENABLE` off [measured: `--trace-match IDirect3DDevice7::`, the software renderer's texture]. The arcs leave the screen at the same places. **The difference:** legion's are dotted (a lit pixel, then a dark one), the Mac's continuous. The texture has no transparent texel, so the gaps are pixels where the line lost the depth test to the terrain it lies on: EE asks for a 16-bit Z-buffer first (graphics.md §3: "try Z16, then Z24, then Z32") and z-fights there, while the kit keeps depth as floats (software) or 32-bit floats (Metal) and the line wins everywhere [inferred; legion can confirm the Z format]. The Mac's is the cleaner picture; matching Windows would mean quantizing depth to the requested format (owner: d3d7, P3-B's area).

## 4. The Scenario Editor's radio buttons (c)

Legion: all three options of the first screen's group (Blank, Random, Seed) select on the first click (`editor-radio-1..3`). menus.md §3.5 had recorded a click on Random that did not select on the Mac.

- **Not reproduced** on the current build [measured]: 29 of 29 first clicks selected, in two runs: Random, Seed, Blank and Random again with a 250 ms hold; then 25 clicks cycling Random → Seed → Blank with 80 ms and 250 ms holds, every fourth one walking in from the map at (400,150), and the first one sent the moment the editor's panel appeared. The picture changed by the expected amount every time (2.1 % Random/Blank, 0.3 % Seed) and the map view never moved, so the second half of the old note ("the terrain view moves while the cursor rests near the bottom panel") did not reproduce either.
- **How a click reaches a radio button** [static]: `UIRadioButton::MouseLButtonDown` (LLE+0x79845) passes the press to `UIButton::MouseLButtonDown` unless the button is already checked; the release goes to `UIButton::MouseLButtonUp` (LLE+0x65322), which fires for any release inside the rectangle. Double presses are decided by time alone (`ISMouseButton::CheckForDoublePress`, LLE+0x4c97f: no distance test) and reach `UIControl::MouseLButtonDoublePressed`, the same on both machines. Nothing in that path depends on the Mac's host; the old failure was most likely the script of the time (it was not kept), not the port.
- **Regression:** `tests/menus/game-tools.script` now clicks Random, Seed and Blank and checks each from the seed field under the group (absent, greyed, black), so a click that does not select fails `--menus`. The three dumps compare with legion's at SSIM 0.90–0.91 over the panel (the map and minimap masked: legion's view had scrolled).

## 5. Found and fixed

| What | Effect | Fix |
|---|---|---|
| **`ee_boot --user` did not own its registry** | `ee_standins.cpp`'s static constructor (shared with the app) sets `RECOMP_REGISTRY` to the default profile's before `main()` runs, and `ee_main.cpp` only set it when unset. Every headless run (`--menus`, `--boot`, `--lobby`, the LAN sessions) read and wrote `~/Library/Application Support/EmpireEarth/registry.json`, and the registry `seed_user.py` wrote into the run's own directory was never read. The Mac's Game Setup showed that file's options (Standard, Tournament – Low, Prehistoric, custom civilizations on, cheat codes off or on depending on the last run anywhere on the Mac) where legion shows its registry's (Tournament, Deathmatch, Random Epoch, custom off, cheats on); Video showed Hi-Res Filter and Lighting on and no "Custom" quality [measured: the game's `RegQueryValueExA` got `Starting Epoch` 0 with 14 in the run's file] | ee-macos: the stand-ins remember whether the caller set `RECOMP_REGISTRY`; `ee_boot` points it at `<user>/registry.json` otherwise. Video now matches legion's settings exactly |
| **Hooks wrote past a one-byte array** | The translation's `uint8_t recomp_hooked[40598];` is a C tentative definition, a common symbol on Mach-O, and the linker took `runtime/dispatch.cpp`'s weak `recomp_hooked[1]` over it. A hook on the EXE (the seed force) set its flag 17,923 bytes past that byte, and the mod teardown at exit zeroed 40 KB of the host's globals behind it, `ee_main.cpp`'s mutex among them: `mutex lock failed`, exit 6 after the game had ended [measured with lldb and nm] | kit `feat/parity`: the translator writes `= {0}`, a real definition; `nm` shows the 40,598-byte array linked and the run exits 0 |
| **`FindFirstFile` order** | 3D Acceleration listed "Direct3D Hardware TnL" before "Direct3D" (legion: the reverse), and the plugin scan loaded the DLLs in APFS's hash order | kit `feat/parity`: matches are returned in NTFS order (by upper-case name), as every Windows disk lists them. The scan is now binkw32, ddraw, Default, DX7HRDisplay, DX7HRTnLDisplay, Language, … |

## 6. Per screen

SSIM (whole frame / half size) against legion's reference, with the default 96 DPI and with `RECOMP_GDI_DPI=144` where the DPI matters [measured, after the fixes in §5].

| Screen | 96 DPI | 144 DPI | Result | What differs, and why |
|---|---|---|---|---|
| Main menu | 0.920 / 0.953 | — | same | Cursor; the copyright line's small Tahoma thinner and greyer (rasterization, §2) |
| Game Settings: Video | 0.861 / 0.866 | 0.924 / 0.948 | same at 144 | Text 1.5× (DPI, §2); Screen Resolution lists each machine's modes. Fixed: rasterizer order (was TnL first), Hi-Res Filter and Lighting (were on) and the "Custom" quality (was missing) — §5 |
| Audio | 0.905 / 0.917 | 0.938 / 0.959 | same at 144 | DPI; the Mac's cursor rests on the tab and shows its tooltip |
| Interface | 0.883 / 0.893 | 0.932 / 0.954 | same at 144 | DPI; tooltip; Adapter Selection: legion's registry has `Network Adapter` 1 (its Ethernet adapter), a fresh Mac profile 0 (`<default>`) |
| Single Player | 0.928 / 0.953 | 0.926 / 0.953 | same | "Player Name" (Tahoma −16 bold) heavier on the Mac (rasterization); the name in the field is each registry's |
| Random Map setup | 0.901 / 0.936 | — | same | The options are now legion's (Tournament, Deathmatch, Random Epoch, custom civilizations off, cheat codes on; §5); the player name; label text |
| Game Tools | 0.936 / 0.962 | 0.934 / 0.961 | same | Scaling only |
| Civilization Builder | 0.874 / 0.886 | 0.922 / 0.955 | same at 144 | DPI; a glyph placed differently inside Windows' advance (the gap after `A` in "Area") |
| Scenario Editor | 0.866 / 0.910 | — | same | The grass tiles are chosen at random; cursors |
| Editor: Random / Seed / Blank | 0.910 / 0.910 / 0.903 (panel) | — | same | §4 |

## 7. Open, and requests for legion

| Owner | Item |
|---|---|
| d3d7 (P3-B area) | Depth precision: the kit keeps float depth where EE asked for Z16, so the area-effect circle is continuous instead of Windows' z-fought dots (§3). Quantizing to the requested format would match Windows, artefacts included; a decision, not a defect |
| kit text | Hinting: small Tahoma is unhinted and antialiased on the Mac, hinted (and bi-level below its `gasp` size) on Windows (§2) |
| ee (P3-E follow-up) | Why "Choose Civilization" opens on the Mac in some seeded runs and never in legion's (§3) |
| legion (read-only requests, not sent: legion is busy with P5-E) | 1. The reference game's Z-buffer format (`CreateSurface` with `DDSCAPS_ZBUFFER`: 16 or 24 bits) and whether the circle is still dotted with the TnL rasterizer. 2. `GetDeviceCaps(LOGPIXELSY)` as EE sees it under GOG's wrapper (expected 144), or Options at 100 % scaling, to close §2's DPI row on Windows' side. 3. Whether a fresh profile ("Max the Conqueror") gets "Choose Civilization" at Start Game with the seed registry. 4. A reference of the Scenario Editor's radio group at 800×600 without the scrolled view, for a full-frame comparison |
