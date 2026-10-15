# Graphics, video and input: COM inventory (Phase 1)

Status: static inventory and a first dynamic census (`windows/frida/com_census.js` on `legion`, menu and in game) both done, 2026-10-14. §11.3 has the measurements and §11.4 the status of each static claim.
Scope: the base game, GOG build, hashes as in PLAN.md §2.4.

This is the reference for the Direct3D 7 / DirectDraw 7 shim (ADR-001 gap 8), the DirectInput shim and the Bink path.

**Labels.**
- **[static]**: read from the code (Ghidra 12.1.4 pseudocode and listings, pefile, capstone).
- **[measured]**: from `legion` captures (`reference/legion.md`).
- **[inferred]**: an interpretation that has not been checked yet.

**Addresses** are module + RVA:
- `HR+` is `DX7HRDisplay.dll` (preferred base 0x10000000).
- `TnL+` is `DX7HRTnLDisplay.dll`.
- `LLE+` is `Low-Level Engine.dll`.
- `EXE+` is `Empire Earth.exe` (base 0x400000, so EXE VA = 0x400000 + RVA).

**Constants.** Names are the DirectX 7 SDK names. Numeric values are given where the code passes literals.

**Where the bulky material lives.** Under the git-ignored `analysis/com-inventory/`:
- `dx7hr_com_calls.md`: every COM call site with its arguments.
- `dinput_notes.md`: data formats, layouts and the DIK list.
- `rasterizer_iface.md`: full vtables.
- `kit_survey.md`: the kit tables with file:line references.
- `export/`: the Ghidra exports of both plugins.
- `tools/`: the extractors. How to reproduce everything is in §13.

---

## 0. Key findings

1. **DX7HRDisplay submits *untransformed* vertices only. Direct3D does the transform and the lighting.** [static]
   - The plugin never uses pre-transformed vertices: it uses no `D3DFVF_XYZRHW` and no `D3DTLVERTEX` anywhere.
   - All geometry, including 2D UI quads, text and bitmaps, goes through **one** system-memory vertex buffer:
     - FVF `0x152` = `XYZ | NORMAL | DIFFUSE | TEX1`, a 36-byte vertex;
     - 2,442 vertices;
     - drawn with `DrawPrimitiveVB` / `DrawIndexedPrimitiveVB`.
   - Matrices go through `SetTransform(WORLD/VIEW/PROJECTION)`. `ConvertToD3DMatrix` (HR+0xa0ce) is a plain transpose of the engine's `GETransformation`.
   - 2D is drawn as 3D: `Set2DMode` (HR+0xa5f6) sets WORLD = VIEW = identity and an orthographic PROJECTION, x' = 2x/W − 1 and y' = 1 − 2y/H.
   - **D3D fixed-function lighting is used too:**
     - `SetLight` with up to 8 point or directional lights, and `LightEnable`;
     - `SetMaterial`, and `AMBIENT`;
     - `COLORVERTEX` and the material-source states, plus `NORMALIZENORMALS = TRUE`.
     - The plugin reads the lights back with `GetLight` and lights terrain on the CPU (HR+0x6d19).
   - **So the shim needs a vertex T&L stage:**
     - world/view/projection and viewport;
     - up to 8 point/directional lights with range and attenuation;
     - material and colour sources;
     - linear vertex fog.
   - It does **not** need pre-transformed vertices, multitexturing, clip planes, state blocks, palettes, clippers or gamma.
2. **The API surface is small.** The plugin calls **50 distinct COM methods** plus 3 `DDRAW.dll` exports. It sets **32 render states** and **12 texture-stage-state types**, all on stage 0; stage 1 is only ever disabled. [static]
   - The full checklist is in §10. The PLAN's "texture-stage-state ubershader" reduces to one stage with three ops: `SELECTARG1`, `SELECTARG2` and `MODULATE`.
3. **The hardware-T&L plugin uses the same API surface.** `DX7HRTnLDisplay` is the same code plus a subclass `DX7TnLRasterizer` that overrides only three methods. [static]
   - It creates the device with `IID_IDirect3DTnLHalDevice` instead of `IID_IDirect3DHALDevice`.
   - It requires `D3DDEVCAPS_HWTRANSFORMANDLIGHT` in `EnumDevices`.
   - Its vertex buffer drops `D3DVBCAPS_SYSTEMMEMORY`.
   - Supporting both plugins costs about a day more than supporting one. This matters because GOG's installer default is the TnL plugin.
4. **GDI onto surfaces is mostly not per frame.** [static + measured]
   - `IDirectDrawSurface7::GetDC` is called only from `DX7Surface::TextLock` (HR+0xed44), and the only plugin GDI is `ExtTextOutA` in `Draw(GEText)`.
   - Text normally goes through **bitmap-font atlases**: `GEFontBitmap` (LLE+0x2724e) renders glyphs into texture surfaces through that GDI path once, when the atlas is built. After that, text is drawn as textured quads.
   - The measured in-game GDI rate exactly matches `GEText::ComputeTextExtent` (LLE+0x446f4) measuring text on a *screen* DC. It is not drawing onto surfaces: `GetDC` / `ReleaseDC` / `GetTextExtentPoint32A` run 92/s each and `SelectObject` 185/s, with no `ExtTextOutA` among the hot calls.
   - The frame loop does release a DC on the render target after `EndScene` (§2.4), so GDI-font text directly on the back buffer is possible. **The census measured 0 surface `GetDC` calls per frame** at the menu and in game (§11.3).
5. **Recommended design: extend the kit's D3D2/3 recorder and GPU renderer** (`dx/ddraw.cpp`, `dx/d3d.cpp`, `host/d3d_render.cpp`) with version-7 interfaces. Do not build on the D3D9 path. (§10.3)
   - The recorder path already keeps DirectDraw surfaces coherent across Lock, Blt, GetDC and Flip, and already renders untransformed vertices with fog, alpha and z.
   - The D3D9 path has no fixed-function generator at all.
   - **Effort:** about **4.5–6 focused weeks** for DD7 + D3D7 + the renderer extensions, including screenshot diffs against `legion` (§10.4). That fits inside ADR-001's 5–7 weeks. GDI text (gap 9) and DirectInput fixes add about 1–1.5 weeks.
6. **Measured on `legion`** (§11.3–11.5):
   - **Coverage:** no game call fell outside the static lists.
   - **Frame cost:** about 850 COM calls and about 1,800 triangles per frame in game, at 79 fps. The menu issues **no draws at all**, only 3 cursor Blts per frame, with a save-under per flip-chain buffer, so `Flip` must exchange the buffers as Windows does (corrected in §11.6; this said "keep the back buffer's contents" until P3-G).
   - **Lighting:** LIGHTING is on for one span per frame, with one directional light.
   - **Textures:** A8R8G8B8 with full mip chains, plus a per-frame A4R4G4B4 64×64 texture.
   - **GDI:** surface `GetDC` = 0 per frame.

---

## 1. Plugin structure

### 1.1 Objects
The plugin exports its classes with full mangled names. [static]

| Class | Vtable | Size | Holds |
|---|---|---|---|
| `DX7Rasterizer` : `GERasterizer` (engine) | HR+0x10334 | — | engine part (0x68 bytes, §2.3): `+0x20` per-screen state vector, `+0x4`/`+0x3c` ambient colour/intensity, `+0x4c` cull mode. Plugin part: `+0x68` vector (`+0x6c/+0x70` begin/end) of per-screen **device contexts** (below) |
| `DX7Screen` : `GEScreen` | HR+0x10480 | 0x44 | `+0x30` **IDirectDraw7**; `+0x10/+0x14` vector of `GEVideoMode` (20 bytes: w, h, refresh, bpp, pixel-format index); `+0x38` current mode; `+0x3d` captured; `+0x3f/+0x40` can mag/min filter; `+0x41/+0x42` 32-bit device / texture support |
| `DX7Surface` : `GESurface` | HR+0x104d4 (32 slots) | 0x60 | `+0x8` copy of `GESurfaceDescription` (0x24 bytes); `+0x2c` vector of attached surfaces; `+0x3c` transparency colour; `+0x48` **IDirectDrawSurface7**; `+0x4c` parent screen; `+0x50` locked pointer, `+0x58` pitch; `+0x54` HDC; `+0x5c` locked, `+0x5d` text-locked |
| `DX7Movie` : `GEMovie` | HR+0x10558 | — | Bink movie on its own TSThread (§7) |
| `DX7PolygonArray`, `DX7StandardVertexArray` (32-byte D3DVERTEX layout), `DX7StdColorVertexArray` (36-byte `D3DVERTEX_STD_COLOR`) | | | CPU arrays; copied into the VB at draw time |

**Per-screen device context.** This is a plain struct, owned by the `DX7Rasterizer` vector at `+0x6c`. [static]

| Off | Field |
|---|---|
| +0x00 | `D3DVIEWPORT7`: X, Y, W, H, MinZ = 0, MaxZ = 1. It is passed to `SetViewport` as is. |
| +0x18 | **IDirect3D7** |
| +0x1c | **IDirect3DDevice7** |
| +0x20 | `DX7Screen*` |
| +0x24 | **IDirect3DVertexBuffer7** (the only VB) |
| +0x28 | VB write cursor, in vertices |
| +0x2c–0x5c | cached texture-stage state, with the texture handle at +0x30 |
| +0x60/+0x64 | cached SRC/DEST blend |
| +0x68/+0x6e/+0x6f | cached ZENABLE, ZWRITE, ALPHABLEND |
| +0x6c/+0x6d | 3D-mode / viewport-dirty flags |
| +0x70 | quirk: "RAGE"/"PRO" in the device description, or less than 8 MB of VRAM |
| +0x71 | quirk: not ATI 0x1002:0x5046, not 3dfx 0x121a:0x0009 |

### 1.2 Threads (from `legion`)
| Calls | Thread |
|---|---|
| `DirectDrawEnumerateExA`, `DirectDrawCreateEx`, `SetCooperativeLevel`, mode setting, surface and device creation, `Test32BitSupport` | main thread [measured for `SetCooperativeLevel`] |
| `BeginScene` … draws … `EndScene`, `ShowScene` (Flip or Blt) | render TSThread `EXE+0x10fe36`, which runs at **CW 0x007f** [measured] |
| `DX7Movie::PlayBink`: Bink decode into a system-memory surface (`Lock`/`Unlock`) | its own TSThread, HR+0x2835, CW 0x027f [measured]; it sets `_PC_53` itself [static] |
| `DX7Movie::RenderFrame`: Blt of the decoded frame to the back buffer | render thread [static] |

`SetCooperativeLevel` always includes `DDSCL_MULTITHREADED` (0x400) and never `FPUSETUP`/`FPUPRESERVE` [measured + static]. The shim must not touch the x87 control word (ADR-001 gap 6).

### 1.3 Error handling the shim must match
Every DirectDraw/D3D call that can return `DDERR_WASSTILLDRAWING` (0x8876021c) is wrapped in a loop with `Sleep(0)` or `Sleep(1)`. This covers BeginScene, EndScene, Blt, Flip, Lock, GetDC and the draws. The plugin never passes `DDLOCK_WAIT` or `DDBLT_WAIT`. [static]

| Return code | How the plugin handles it |
|---|---|
| `DDERR_SURFACELOST` (0x887601c2) | Mapped to engine result 9 or 0xd. The engine then calls `RestoreAllSurfaces`, which runs `TestCooperativeLevel` then `RestoreAllSurfaces` (HR+0xcd0e). |
| `DDERR_SURFACEBUSY` (0x887601ae) | Retried around VB Lock/Unlock. |
| `DDERR_NOTLOCKED` | Tolerated by Unlock. |

**The shim should never return WASSTILLDRAWING.** It should return `DD_OK` or a real error; a busy loop on a fake busy code would spin forever.

---

## 2. Rasterizer plugin interface

Full slot tables (signature, plugin target, call-site counts) are in `analysis/com-inventory/rasterizer_iface.md`.

### 2.1 Entry points and selection [static]
- **Plugin exports:**

  | Export | HR+ | Behaviour |
  |---|---|---|
  | `PIMGetName` | 0x1c9b | `UString` "Direct3D" (TnL: "Direct3D Hardware TnL") |
  | `PIMGetType` | 0x1ca1 | `0x72617374` ('rast') |
  | `PIMIsValid` | 0x1ca7 | Engine `Activate()`, then `GetScreenCount() > 0`, then `Deactivate()`. This is where the whole §3.1 enumeration first runs. |
  | `RPIMGetRasterizer` | 0x1cdd | Returns a **static** `DX7Rasterizer` at HR+0x16998 (TnL+0x179d8): 0x7c bytes, built by CRT static init through ctor HR+0x90b3 → `GERasterizer(name)` (LLE+0x350c6). |

- **Loading.** `GEInitialize` (LLE+0x4a2cc) loads every `*.dll` in the folder and keeps those whose type is 'rast'. A plugin that passes `PIMIsValid` has its rasterizer appended to `GERasterizer::sRasterizers`.
- **Selection.** The EXE (EXE+0x1392e9) matches the registry `Rasterizer Name` against the name at rasterizer+0x54, falling back to rasterizer 0. The result goes into the EXE global at VA 0x919794.

### 2.2 Classes and vtables [static, RTTI]
All single inheritance.

| Engine base | Slots | Plugin class | Slots | Notes |
|---|---|---|---|---|
| `GERasterizer` (LLE+0x948b0) | 78 | `DX7Rasterizer` (HR+0x10334) → `DX7TnLRasterizer` (TnL only) | 82 | details below |
| `GEScreen` | 20 | `DX7Screen` (HR+0x10480) | 20 | Capture, CreateSurface, CreateAWindow, modes, Release, RestoreAllSurfaces, SetMode, … |
| `GESurface` : `UReferenceCounter` | 32 | `DX7Surface` (HR+0x104d4) | 32 | slots 1–31: AttachSurface, Clear×2, CopyTo, CopyWithFilter, DetachSurface, Flip, getters, Lock (26), RestoreSurface, SetTransparencyColor, TextLock (29), TextUnlock (30), Unlock (31) |
| `GEPolygonArray` | 10 | `DX7PolygonArray` | 10 | 16-bit index triples |
| `GEVertexArray` | 16 | `DX7StandardVertexArray` / `DX7StdColorVertexArray` | 16 | 32- / 36-byte vertices |
| `GEMovie` : `UReferenceCounter` | 8 | `DX7Movie` | 8 | |

**GERasterizer → DX7Rasterizer in detail:**
- **48 pure slots** are implemented by DX7:
  - activation: ActivateScreen (0), ActivateRenderingSurface (1);
  - lights: AddLight (2), Light, RemoveLight, RemoveAllLights, UpdateLight (68);
  - frame: BeginScene (4), CanDoCurrentState, Clear (6), ClearZBuffer (7), EndScene (28), ShowScene (66);
  - creation: CreateMovie ×2, CreatePolygonArray, CreateVertexArray;
  - drawing: nine `Draw` overloads (13–21), Draw3DLine, DrawGridLines, DrawSky, DrawTerrainGrid, DrawTerrainHighlight, DrawWater;
  - queries: video-memory and caps;
  - other: SetDebugColor (46), SetTexture (61), TransformToCamera (67);
  - protected (70–77): ActivateRasterizer, ActivateTheScreen, the texture-handle quartet, DrawModelHierarchy, SetRenderTarget.
- **28 slots override engine state-caching setters:** AddValidScreen (3), Deactivate (12) and the setters 37–65 (SetBlending, SetFog…, SetZBuffer…). 26 of them call the engine version afterwards.
- **Inherited:** only slot 57, `SetProjectionMode`.
- **Destructor:** slot 69.
- **Added in DX7 (78–81):** GetIsScreenValid, CreateVertexBuffers, DrawTerrainMaterial, GetVideoMemory.
- **Texture handles** (Create, Demote, Promote → one `return 0` stub; Destroy → `ret 4`): no-ops. A texture *is* its DirectDraw surface.

### 2.3 Engine-side layout the plugin and the EXE share [static]
- **Size.** `GERasterizer` is 0x68 bytes.
- **`+0x20` is a vector of per-screen states,** 0x340 bytes each. Each state holds:
  - the viewport and the fog state;
  - the projection matrices (+0x1b0, +0x1f0) and the camera (+0xf0);
  - `+0x2a8` the screen;
  - `+0x2ac` / `+0x2b0` / `+0x2b4` the primary, back and render-target surfaces;
  - the window and frame statistics;
  - the state cache at +0x2e8…+0x33e.
- **This layout is ABI.** The EXE reads `+0x20` inline about 49 times, so a native rasterizer rewrite (PLAN Phase 7) must keep it.

### 2.4 What the game asks for [static; rates inferred from legion census]
- **Per frame** (`RTSGraphics::Render`, EXE+0x11015d, on the render thread EXE+0x10fe36 at ~83 fps):
  1. `Clear`
  2. `BeginScene(rt)`, which does `SetRenderTarget` and then D3D `BeginScene`.
  3. The scene passes (terrain, water, units, effects, shadows, UI).
  4. `EndScene`
  5. **`rtSurface->TextUnlock()`**, which releases a DC if GDI text was drawn this frame.
  6. HUD and cursor helpers.
  7. `ShowScene`, which is a Flip or a windowed Blt.
- **Per object (thousands per second):**
  - `Draw(GEModel*)` (53 EXE call sites), with state churn around it: SetBlending, SetZBuffer, SetAlphaTestingRefValue, SetShadingColorSource, SetFog, SetAmbientColor, SetCulling.
  - Plugin-internal `SetTexture`, about 5.3k/s ≈ 64 per frame [inferred from `GETexture::Promote` / `Dereference`, 5.3k/s measured].
- **UI per frame:** `Draw(GEBitmap / GELine / GERhombus / GEText)` from the engine's and the EXE's `UI*` code.
- **Cold:** activation, mode enumeration, `Create{Polygon,Vertex}Array` (model load), `CreateMovie`, caps and video-memory queries.
- **Caution for other Phase 1 users.** The spike's Ghidra exports **miss functions that are reached only through vtables**: 2,853 of the 6,669 RTTI-vtable targets in the EXE and 108 of 680 in the engine. One of them is `RTSGraphics::Render`. The counts above come from a capstone sweep, not from the pseudocode.

---

## 3. DirectDraw 7 usage (DX7HRDisplay)

### 3.1 Creation and enumeration
- **`DirectDrawEnumerateExA(cb, ctx, 7)`** (HR+0x9100). The flags are `ATTACHEDSECONDARYDEVICES | DETACHEDSECONDARYDEVICES | NONDISPLAYDEVICES`. `DirectDrawEnumerateA(cb, ctx)` is used instead when the engine's `UGetOSType()` returns 1; that value's meaning was not checked. [static]
- **Per enumerated device** (callbacks HR+0x8c73 and HR+0x8e6a):
  - `DirectDrawCreateEx(guid, &dd, IID_IDirectDraw7, NULL)`.
  - `GetCaps(&DDCAPS{dwSize = 0x17c}, NULL)`.
  - The device is kept only if `dwCaps & DDCAPS_3D` (bit 0x1). It becomes a `DX7Screen`; otherwise it is released.
- **`DX7Screen` constructor** (HR+0xc195):
  - `GetCaps` again. It tests `dwFXCaps` for shrink/stretch. **This is a bug:** the test uses `|` instead of `&`, so filtering always counts as supported.
  - `EnumDisplayModes(0, NULL, ctx, EnumVideoModeCallback)`.
  - `GetDisplayMode` to find the current mode.
  - `Test32BitSupport`.
- **`EnumVideoModeCallback`** (HR+0xcb74) keeps a mode only if its `DDPIXELFORMAT` matches one of the plugin's 12 formats exactly (§3.3). The match is on bpp and R/G/B/A masks. The callback stores w, h, refresh and the format index.
- **`Test32BitSupport`** (HR+0xcdb4):
  1. Opens a 16×16 test window and captures the screen.
  2. Picks the **800×600×16** mode, or the last mode if that is missing.
  3. Creates a primary, then `QueryInterface(IID_IDirect3D7)` and `CreateDevice(IID_IDirect3DHALDevice, primary)`.
  4. Calls `GetCaps(D3DDEVICEDESC7)`. "32-bit device" means `dwDeviceRenderBitDepth & DDBD_32`.
  5. Calls `EnumTextureFormats`. "32-bit textures" means an A8R8G8B8 format exists *and* a 16×16 32-bit texture can be created.
  6. Releases everything.
- **`GetIsScreenValid`** (HR+0xa3bf): `QI(IID_IDirect3D7)`, then `EnumDevices`. The callback accepts a device with `dwDevCaps & D3DDEVCAPS_HWRASTERIZATION` (0x80000).
- **`ActivateTheScreen`** (HR+0x98ca):
  - `QI(IID_IDirect3D7)` and `CreateDevice(IID_IDirect3DHALDevice, back-or-primary surface)`.
  - `SetViewport`, then initial render states (§4.5).
  - `GetDeviceIdentifier(&DDDEVICEIDENTIFIER2, 0)`. The upper-cased description is searched for `"RAGE"` and `"PRO"`.
  - `GetAvailableVidMem`, through `GetVideoMemory` (HR+0xa499) with `DDSCAPS_VIDEOMEMORY`.
  - `TSS(0, TEXCOORDINDEX, 0)`.

### 3.2 Cooperative levels, modes, presentation
- **`DX7Screen::Capture(window, fullscreen)`** (HR+0xc3c6) [static; first four confirmed by measurement]:

  | Case | Flags |
  |---|---|
  | Windowed | `0x408` (NORMAL \| MULTITHREADED) |
  | Release | `0x8` (NORMAL) |
  | Fullscreen, primary display | `0x80` (SETFOCUSWINDOW), then `0x513` (FULLSCREEN \| ALLOWREBOOT \| EXCLUSIVE \| SETDEVICEWINDOW \| MULTITHREADED) |
  | Fullscreen on a screen not attached to the desktop | `0x413` |
  | Further screens (multi-monitor) | `0x691` (… \| CREATEDEVICEWINDOW) |

- **`SetMode`** (HR+0xcd4b) calls `SetDisplayMode(w, h, bpp, refresh, 0)`. The values come from the enumerated mode list.
- **Present: `ShowScene`** (HR+0xbadd). There is **no clipper, no palette and no gamma** anywhere in either plugin. [static]
  - **Fullscreen:** `Flip(NULL, DDFLIP_DONOTWAIT (0x20))`, or `0x28` (adding `DDFLIP_NOVSYNC`) when vsync is off.
  - **Windowed:** `primary->Blt(&clientRectOnScreen, back, &{0,0,w,h}, 0, NULL)`. That is a plain copy with flags 0. The destination is the window's client origin plus the back-buffer size.

### 3.3 Surfaces: `DX7Screen::CreateSurface` (HR+0xc6f1)
**The engine's `GESurfaceDescription`** (0x24 bytes) [static]:

| Off | Field |
|---|---|
| +0x00 | type |
| +0x04 | format index |
| +0x08 | static hint: 1 → `HINTSTATIC`, 0 → `HINTDYNAMIC` (corrected from "dynamic hint" by measurement) |
| +0x0c | width |
| +0x10 | height |
| +0x14 | back-buffer count |
| +0x18 | mip count |
| +0x1c | depth bits |
| +0x20 (byte) | 3D-renderable |
| +0x21 (byte) | colour-keyed |

**How each type maps to DirectDraw:**

| GE type | DDSD flags | ddsCaps.dwCaps | dwCaps2 | Used for |
|---|---|---|---|---|
| 0 primary | `CAPS` | `PRIMARYSURFACE \| VIDEOMEMORY \| LOCALVIDMEM` (+`3DDEVICE`) | — | windowed primary |
| 1 offscreen | `CAPS\|HEIGHT\|WIDTH\|PIXELFORMAT` | `OFFSCREENPLAIN \| VIDEOMEMORY \| LOCALVIDMEM` (+`3DDEVICE`) | — | windowed back buffer |
| 2 flip chain | `CAPS\|BACKBUFFERCOUNT` | `PRIMARYSURFACE \| FLIP \| COMPLEX \| VIDEOMEMORY \| LOCALVIDMEM` (+`3DDEVICE`) | — | fullscreen. Back buffers come from `GetAttachedSurface(DDSCAPS_FLIP)`. |
| 3 z-buffer | `CAPS\|HEIGHT\|WIDTH\|PIXELFORMAT` | `ZBUFFER \| VIDEOMEMORY \| LOCALVIDMEM` | — | Attached to the back buffer with `AddAttachedSurface`. Format 0xc means "try Z16, then Z24, then Z32"; 0xd tries the reverse. |
| 4 system memory | `CAPS\|HEIGHT\|WIDTH\|PIXELFORMAT` | `OFFSCREENPLAIN \| SYSTEMMEMORY` (+`3DDEVICE`) | — | Bink frame buffer |
| 5 texture | `CAPS\|HEIGHT\|WIDTH\|PIXELFORMAT` | `TEXTURE` | `TEXTUREMANAGE` + (`HINTSTATIC` or `HINTDYNAMIC`): 0x18 or 0x14 | textures, font atlases. Measured: 0x18. |
| 5 + 3D flag | same | `TEXTURE \| 3DDEVICE \| VIDEOMEMORY \| LOCALVIDMEM` | `HINTSTATIC` (8) or `HINTDYNAMIC` (4) | render-target textures (not observed) |
| 6 mipmapped texture | `…\|MIPMAPCOUNT` | `TEXTURE \| MIPMAP \| COMPLEX` (+`3DDEVICE`) | as type 5; measured 0x18 | Sub-levels come from `GetAttachedSurface(DDSCAPS_MIPMAP)`. Measured: full chains down to 1×1. |

Notes on the table [static]:
- `DDSD_CKSRCBLT` is added when the colour-keyed byte is set, with a key of 0 at creation.
- Surface `+0x21` is the colour-key flag.

**Pixel formats.** The format index points into the plugin's `DDPIXELFORMAT` table at HR+0x16754: 12 entries of 7 dwords (flags, FourCC, bpp, R, G, B, A). [static]

| # | Format | # | Format |
|---|---|---|---|
| 0 | A8R8G8B8 | 6 | R5G5B5X1 (masks f800/7c0/3e) |
| 1 | X8R8G8B8 | 7 | R5G6B5 |
| 2 | A4R4G4B4 | 8 | DXT1 (`DDPF_FOURCC`) |
| 3 | A1R5G5B5 | 9 | Z32 |
| 4 | R5G5B5A1 (masks f800/7c0/3e/1) | 10 | Z24 (mask ffffff) |
| 5 | X1R5G5B5 | 11 | Z16 |

Which texture formats the engine actually requests is decided engine-side. That is an open question for the census (§11).

### 3.4 Surface operations
| Op | Plugin function | DirectDraw call and arguments [static] |
|---|---|---|
| Colour fill | `DX7Surface::Clear` HR+0xdfee, HR+0xe07c | `Blt(rect or NULL, NULL, NULL, DDBLT_COLORFILL (0x400), &DDBLTFX{dwSize = 100, dwFillColor = GEPackColor(...)})` |
| Copy / stretch | `DX7Surface::CopyTo` HR+0xe13b | `dst->Blt(&dstRect, src, &srcRect, DDBLT_DONOTWAIT (0x8000000) [\| DDBLT_KEYSRC (0x8000)], NULL)`. Rectangles may differ (stretch). Used by the movie path. |
| Software-filtered copy | `Copy16BitWithFilter` HR+0xe22f, `Copy32BitWithFilter` HR+0xe6c0 | `Lock`/`Unlock` on both surfaces and a CPU bilinear loop. No Blt. |
| Lock | `DX7Surface::Lock` HR+0xec58 | `Lock(NULL, &DDSURFACEDESC2, 0, NULL)`. **Flags 0.** Keeps `lpSurface` and `lPitch`. |
| Unlock | HR+0xedc7 | `Unlock(NULL)` |
| Colour key | `SetTransparencyColor` HR+0xecfb | `SetColorKey(DDCKEY_SRCBLT (8), {c, c})` with c = the packed colour |
| GDI | `TextLock` / `TextUnlock` HR+0xed44 / HR+0xed98 | `GetDC(&hdc)` / `ReleaseDC(hdc)` |
| Flip | HR+0xebc8 | see §3.2 |
| Attach / detach | HR+0xdfa9 / HR+0xeb50 | `AddAttachedSurface(z)` / `DeleteAttachedSurface` |
| Restore | HR+0xecdc | `Restore()` |

**Not used anywhere** [static]: BltFast, BltBatch, GetSurfaceDesc, IsLost, overlays, SetPalette, SetClipper, GetPixelFormat. Bink calls `GetPixelFormat` *inside* `binkw32`, but that is our HLE (§7).

---

## 4. Direct3D 7 usage

### 4.1 Vertex data and draws
- **The one vertex buffer.** `CreateVertexBuffers` (HR+0xa1f7) calls `IDirect3D7::CreateVertexBuffer(&{dwSize = 0x10, dwCaps = 0x10800 (WRITEONLY | SYSTEMMEMORY), dwFVF = 0x152, dwNumVertices = 2442}, &vb, 0)`. TnL uses `dwCaps = 0x10000`. [static]
- **Locking** (`LockVB`, HR+0xa508):
  - If the request does not fit, the cursor wraps to 0 and the lock uses `DDLOCK_DISCARDCONTENTS | DDLOCK_WRITEONLY` (0x2020).
  - Otherwise it uses `DDLOCK_NOOVERWRITE | DDLOCK_WRITEONLY` (0x1020).
  - The vertices are memcpy'd in, then Unlock, then a draw starting at the cursor.
  - A `DX7StandardVertexArray` (32-byte layout without colour) is expanded with diffuse = 0xffffffff.
- **Vertex layout `D3DVERTEX_STD_COLOR`:** x, y, z, nx, ny, nz, diffuse (ARGB), u, v.
- **Draw calls.** Only these two exist; there is no `DrawPrimitive`, no strided draw, no `ProcessVertices` and no `ComputeSphereVisibility`. [static]

  | Call | Primitive | Callers |
  |---|---|---|
  | `DrawPrimitiveVB(type, vb, start, n, 0)` | POINTLIST (1) | `Draw(GEPoint)` HR+0x316c |
  | | LINELIST (2) | `Draw(GELine)` HR+0x2fc5, `Draw3DLine` HR+0x3f5a, `DrawGridLines` HR+0x57fb, `DrawTerrainGrid` HR+0x5d04 |
  | | TRIANGLELIST (4) | terrain HR+0x4dcc, HR+0x6137, HR+0x6789 |
  | `DrawIndexedPrimitiveVB(TRIANGLELIST, vb, start, n, WORD* idx, nIdx, 0)` | TRIANGLELIST (4) | polygon arrays HR+0x3d62, models HR+0x42d3, bitmaps/sprites HR+0x2d9f (4 vertices, indices 0,1,2,2,1,3), rhombus HR+0x32cd |

  - The indices come from system memory: `GEPolygonArray`, 16-bit.
  - All draw flags are 0.

### 4.2 Transforms and viewport: the T&L answer
- **`SetTransform` is called at 25 sites** [static]:
  - WORLD (1): 9 sites;
  - VIEW (2): 8 sites;
  - PROJECTION (3): 8 sites.
- **Sources of the matrices:**
  - The camera view comes from the engine state at `+0xf0`, combined with a fixed axis-flip matrix.
  - The projection is perspective (state `+0x1b0`) or orthographic (state `+0x1f0`, when projection mode = 1).
  - WORLD is the model's transform times `GERasterizer::sTransformStack`.
  - In 2D mode, WORLD = VIEW = identity and PROJECTION = ortho(2/W, −2/H, translate −1, +1).
- **`CLIPPING`** is set TRUE for 2D and for models that are not fully visible. It is FALSE at device creation.
- **`SetViewport`** takes the context's `D3DVIEWPORT7`, taken from the `GEViewport` rectangle (rounded) with MinZ/MaxZ from the viewport.
- **D3D does both transform and lighting.** The device is the **HAL** device, so on real DX7 hardware that means D3D's software T&L; on the TnL plugin it is hardware T&L. [static]
- **The plugin does some vertex maths of its own:**
  - Smoothing of model normals (`CopyAndSmooth*`).
  - Terrain lighting on the CPU, using the D3D lights read back (`Light`, HR+0x6d19).
  - `TransformToCamera` (HR+0x7204) for terrain culling, through engine `GETransformation::Apply`.

### 4.3 Lighting and materials [static]
- **Lights.**
  - `AddLight` / `RemoveLight` (HR+0x9cd3 / HR+0xa5ae) run `LightEnable(i, TRUE/FALSE)` on 8 slots, tracked in a global table at HR+0x16cec.
  - `UpdateLight` (HR+0xbd24) calls `SetLight(i, D3DLIGHT7)`:
    - the type is `D3DLIGHT_POINT` or `D3DLIGHT_DIRECTIONAL`;
    - the fields are diffuse r, g, b, position, the *negated* direction, range and attenuation 0/1/2;
    - specular and ambient are 0, and there is no spot light.
  - `Light()` (HR+0x6d19) reads the lights back with `GetLight`.
- **`SetMaterial`**:
  - per model in `DrawModelHierarchy` (HR+0x42d3), from `GEMaterial`;
  - for lines and grids;
  - in `SetShadingColorSource` (HR+0xb1d7), as diffuse = ambient = (1,1,1,1), specular = emissive = 0, power = 0.
- **`SetShadingColorSource(1)`** sets `COLORVERTEX = 1`, `AMBIENTMATERIALSOURCE = COLOR1`, `DIFFUSEMATERIALSOURCE = COLOR1`. `SetShadingColorSource(0)` sets them back to MATERIAL.
- **`AMBIENT`** = 0xFF000000 | rgb(ambientColour × intensity × 255).
- **Whether `LIGHTING` is on at run time** is up to the engine (`SetLighting`, HR+0xb0ae). The census will show the values (§11).

### 4.4 Texture stage states [static]
**`DX7Rasterizer::SetTexture`** (HR+0xb34c). Texture state is cached in the device context and set only when it changes.

| Engine field (GETexture) | TSS (stage 0) | Values written |
|---|---|---|
| texture | `SetTexture(0, surf7)`, or `SetTexture(0, NULL)` plus COLOROP = ALPHAOP = DISABLE | — |
| colour mode +0x10 (0/1/2/3) | COLOROP / COLORARG1 / COLORARG2 | 0: SELECTARG2 (DIFFUSE); 1: SELECTARG1 (TEXTURE); 2: MODULATE(TEXTURE, DIFFUSE); 3: MODULATE(TEXTURE, CURRENT) |
| alpha mode +0x14 | ALPHAOP / ALPHAARG1 / ALPHAARG2 | same mapping as colour |
| address +0x18 | ADDRESS (12) | 0: CLAMP, 1: MIRROR, 2: WRAP |
| min filter +0x1c | MINFILTER (17) | POINT / LINEAR / ANISOTROPIC |
| mag filter +0x20 | MAGFILTER (16) | POINT / LINEAR / **FLATCUBIC (3)**, the plugin's mapping for "best" |
| mip filter +0x24 | MIPFILTER (18) | NONE / POINT / LINEAR |
| LOD bias +0x28 | MIPMAPLODBIAS (19) | float |
| — | TEXCOORDINDEX (11) | 0, at activation |
| — | stage 1 | `SetTexture(1, NULL)`, COLOROP(1) = ALPHAOP(1) = DISABLE, every time |

### 4.5 Render states [static]
**32 states**, set through the `DX7Rasterizer` setters. The engine's enumerations are remapped as shown.

| State | Value(s) | Setter (HR+) |
|---|---|---|
| ANTIALIAS (2) | NONE, or SORTINDEPENDENT (engine 1) | SetAntiAliasing 0xaaad |
| ZENABLE (7) | TRUE if engine flags & 3 | SetZBuffer 0xb9ba |
| ZWRITEENABLE (14) | engine flags & 1 | SetZBuffer |
| FILLMODE (8) | SOLID / WIREFRAME / POINT (engine 0/1/2) | SetDrawStyle 0xaea8 |
| SHADEMODE (9) | FLAT / GOURAUD | SetShading 0xb18d |
| ALPHATESTENABLE (15) | engine value | SetAlphaTesting 0xa83b |
| ALPHAFUNC (25) | engine 0..7 → NEVER, LESS, LESSEQUAL, EQUAL, NOTEQUAL, GREATEREQUAL, GREATER, ALWAYS | 0xa87c |
| ALPHAREF (24) | int from float | 0xa90a |
| SRCBLEND (19), DESTBLEND (20) | D3DBLEND = engine + 1 (all 13) | SetBlending 0xaafa |
| ALPHABLENDENABLE (27) | off only for (ONE, ZERO) | SetBlending |
| CULLMODE (22) | NONE / CW / CCW (engine 0/1/2), set on every screen | SetCulling 0xad79 |
| ZFUNC (23) | same 8-way mapping as ALPHAFUNC | SetZCompare 0xba4f |
| DITHERENABLE (26) | bool | 0xae5e |
| FOGENABLE (28) | bool | SetFog 0xaf02 |
| FOGCOLOR (34) | 0xFFrrggbb | 0xaf4c |
| FOGSTART (36), FOGEND (37) | floats | SetFogRange 0xb00a |
| SPECULARENABLE (29) | bool; FALSE at activation | SetSpecular 0xb302 |
| COLORKEYENABLE (41) | bool | SetColorKeying 0xad2f |
| CLIPPING (136) | per draw (§4.2) | |
| LIGHTING (137) | bool | SetLighting 0xb0ae |
| AMBIENT (139) | ARGB | 0xa96b, 0xaa13 |
| COLORVERTEX (141), DIFFUSEMATERIALSOURCE (145), AMBIENTMATERIALSOURCE (147) | 0/1 | 0xb1d7 |
| EXTENTS (138) = 0, NORMALIZENORMALS (143) = 1, LOCALVIEWER (142) = 0, FOGVERTEXMODE (140) = LINEAR, SPECULARMATERIALSOURCE (146) = 0, EMISSIVEMATERIALSOURCE (148) = 0 | set once at activation | ActivateTheScreen 0x98ca |

**Never set** [static]:
- `FOGTABLEMODE` and `FOGDENSITY`, so fog is always *vertex* fog, linear, from eye-space depth. `SetFogType` and `SetFogValue` only update engine state.
- `RANGEFOGENABLE`, `ZBIAS` and the stencil states.
- `TEXTUREFACTOR`, `WRAPn` and the legacy `TEXTUREMAPBLEND`.

### 4.6 Frame, clear and validation [static]
- **Scene:** `BeginScene` (HR+0x9da0) and `EndScene` (HR+0xa314) once per frame and screen.
- **Clear:**
  - `Clear(1, &rect, D3DCLEAR_TARGET, 0xFFrrggbb, 0, 0)` (HR+0x9e49);
  - `Clear(1, &rect, D3DCLEAR_ZBUFFER, 0, z, 0)` (HR+0x9fb3 `ClearZBuffer`).
- **Render target:** `SetRenderTarget(surf7, 0)` followed by `SetViewport` (HR+0xb0f8). This is render-to-texture through the type-5 3D surfaces.
- **Validation:** `ValidateDevice(&passes)` in `CanDoCurrentState` (HR+0x9e01).

### 4.7 The complete method list (DX7HRDisplay) [static]
Slot numbers count IUnknown as 0–2. The 50 methods are:

| Interface | Methods (slot) |
|---|---|
| IDirectDraw7 (12) | QueryInterface (0), Release (2), CreateSurface (6), EnumDisplayModes (8), GetCaps (11), GetDisplayMode (12), SetCooperativeLevel (20), SetDisplayMode (21), GetAvailableVidMem (23), RestoreAllSurfaces (25), TestCooperativeLevel (26), GetDeviceIdentifier (27) |
| IDirectDrawSurface7 (12) | Release (2), AddAttachedSurface (3), Blt (5), DeleteAttachedSurface (8), Flip (11), GetAttachedSurface (12), GetDC (17), Lock (25), ReleaseDC (26), Restore (27), SetColorKey (29), Unlock (32) |
| IDirect3D7 (4) | Release (2), EnumDevices (3), CreateDevice (4), CreateVertexBuffer (5) |
| IDirect3DDevice7 (19) | Release (2), GetCaps (3), EnumTextureFormats (4), BeginScene (5), EndScene (6), SetRenderTarget (8), Clear (10), SetTransform (11), SetViewport (13), SetMaterial (16), SetLight (18), GetLight (19), SetRenderState (20), DrawPrimitiveVB (31), DrawIndexedPrimitiveVB (32), SetTexture (35), SetTextureStageState (37), ValidateDevice (38), LightEnable (44) |
| IDirect3DVertexBuffer7 (3) | Release (2), Lock (3), Unlock (4) |

The `DDRAW.dll` exports are `DirectDrawEnumerateA`, `DirectDrawEnumerateExA` and `DirectDrawCreateEx`. Call sites per method are in `analysis/com-inventory/dx7hr_com_calls.md`.

**Not used:**
- **Interfaces:** IDirectDrawClipper, IDirectDrawPalette, IDirectDrawGammaControl.
- **D3D state:** GetRenderState, GetTransform, MultiplyTransform, state blocks, clip planes, `Load`, `PreLoad`.
- **Elsewhere in the game:** nothing outside the two display plugins imports `DDRAW.dll`, and no gamma API is imported anywhere (`db/win32-imports.json`). [static]

---

## 5. DX7HRTnLDisplay differences [static]
- **Same code.** The 515 vs 540 Ghidra functions are the same code plus `DX7TnLRasterizer`, which derives from `DX7Rasterizer`. It overrides only three methods:

  | Method | TnL+ | Difference |
  |---|---|---|
  | `ActivateTheScreen` | 0xf0ea | `CreateDevice(IID_IDirect3DTnLHalDevice {f5049e78-…})`. It sets the RAGE quirk flag `+0x70` unconditionally and skips the identifier checks. |
  | `CreateVertexBuffers` | 0xf438 | `dwCaps = D3DVBCAPS_WRITEONLY` (video-memory VB). FVF and size are unchanged. |
  | `GetIsScreenValid` | 0xf4b7 | Requires `dwDevCaps & 0x90000` (HWRASTERIZATION \| HWTRANSFORMANDLIGHT). |

- **Identical:** every other COM call, state value, surface type and format. The `DX7Rasterizer` versions are still present (TnL+0x9964 etc.).
- **For the shim:**
  - accept both device IIDs;
  - report a second device with `HWTRANSFORMANDLIGHT` in `EnumDevices`, if we want the TnL plugin to load;
  - ignore VB memory hints.
- **What it buys:** nothing visual. It is the GOG default for unchanged Windows installs, and it renders on the render thread only, so it does not affect lockstep.

---

## 6. GDI onto surfaces and text
- **Plugin.** The plugin's only GDI-on-surface code is `DX7Rasterizer::Draw(GEText, [rect,] GESurface*)` (HR+0x33c0, HR+0x35ff). [static]
  1. `TextLock` (GetDC), unless the surface is already locked.
  2. `SetBkMode` (TRANSPARENT/OPAQUE), `SetTextColor`, `SetBkColor`, then `SelectObject(font HFONT from GEFont+0x18)`.
  3. `GetTextExtentPoint32A` for centre/right alignment.
  4. An optional shadow pass, when the font flag 0x10 is set: `ExtTextOutA` at (x+1, y+1) in black or white.
  5. `ExtTextOutA(hdc, x, y, 0 or ETO_CLIPPED (4), rect, str, len, NULL)`.
  6. Restore the DC state. **The DC is not released here.** The caller calls `TextUnlock`, which is ReleaseDC.
- **Fonts with a bitmap atlas skip GDI.** If the font has a bitmap atlas (`GEFont+0x24 != 0`), the plugin calls `GEFontBitmap::Draw` instead. That is the engine drawing textured quads (HR+0x3617). [static]
- **Atlas build.** `GEFontBitmap::GEFontBitmap` (LLE+0x2724e) builds the atlas:
  - it walks `tmFirstChar..tmLastChar`;
  - it draws each glyph into a texture surface through the rasterizer's `Draw(GEText, rect, surface)`;
  - it calls `TextUnlock` and then `CreateSurfaceAlpha` to derive alpha from the glyph pixels. [static]
- **So GDI-on-surface happens mostly at font/atlas creation, on *texture* surfaces.** [static]
  - Confirmed for steady state [measured]: 0 surface `GetDC` calls at the menu and in game.
  - The atlas build itself was not captured (§11.2).
- **The exception: fonts without an atlas.** Text in such a font is drawn with GDI straight onto the render target during the frame. The DC is then held until `RTSGraphics::Render` calls `TextUnlock` after `EndScene` (§2.4).
- **The measured in-game GDI rate** is `GetDC` / `ReleaseDC` / `GetTextExtentPoint32A` at 92/s each and `SelectObject` at 185/s, with no `ExtTextOutA` [measured]. That is exactly one `GEText::ComputeTextExtent` (LLE+0x446f4) per string: `GetDC(NULL)` through `GEDeviceContext::GetDeviceContext` (LLE+0x49db5), `SelectObject`, `GetTextExtentPoint32A`, `SelectObject` again, `ReleaseDC`. This is text measurement on a screen DC.
- **Fonts** are created with `CreateFontA` in `GEFont::GEFont` (LLE+0x2711c), followed by `GetTextMetricsA`.
- **Shim consequences:**
  - The kit's `GetDC`-on-surface (a DIB over guest memory) fits, because the targets are CPU-side textures, so no GPU readback is needed.
  - The kit is missing `ExtTextOutA`, `GetTextExtentPoint32A`, `CreateFontA` and `GetTextMetricsA` (ADR-001 gap 9).
  - Glyph metrics must match Windows, or the atlas layout and UI text widths will differ (cosmetic only).
- **The EXE's own GDI** (`CreateDIBSection`, `BitBlt`, `Polygon`, `Ellipse`, `TextOutW`, … at EXE+0x3ba000–0x3bc000) is not on the plugin path, and nothing shows it running in the census [measured: not among the in-game hot calls]. It is WONAPI's Win32 back end (`MSWindow`, `MSGraphics`, `MSFont`, `MSNativeImage`), used only by the Multiplayer lobby: [lobby.md](lobby.md) §4 [static].

---

## 7. Bink movie path (`DX7Movie`, HR+0x2251…0x2a9a) [static]
1. **Thread.** `Initialize` (HR+0x2616) starts `PlayBink` (HR+0x2835) as a TSThread at priority 0. The thread sets `_PC_53` and round-to-nearest every frame.
2. **Sound.** `BinkSetSoundSystem(BinkOpenDirectSound, IDirectSound*)`. The DirectSound pointer comes from the Miles mixer (`SEMixer::sActiveMixer` vtable `+0xa8`).
3. **Open.** `BinkOpen(path, 0)`.
4. **Frame buffer.** `CreateFrameBuffer` (HR+0x2553) creates a **GE type-4 system-memory surface**. It has the Bink width and height and the **back buffer's pixel format**, which in practice is R5G6B5 or X8R8G8B8.
5. **Per frame:**
   1. `BinkWait` loop with `Sleep(1)`.
   2. `BinkDoFrame`.
   3. Lock the frame surface (`Lock(NULL, desc, 0, NULL)`).
   4. `BinkDDSurfaceType(IDirectDrawSurface7*)`. The real binkw32 queries the surface's pixel format here.
   5. `BinkCopyToBuffer(bink, lpSurface, pitch, height, 0, 0, type)`.
   6. Unlock, set the event, then `BinkNextFrame`.
6. **Display.** On the render thread, `RenderFrame` (HR+0x2747) copies the frame to the back buffer with `DX7Surface::CopyTo`. That is a stretched `Blt` with `DDBLT_DONOTWAIT`, filling the back buffer for full-screen movies.
7. **Kit fit.** The kit's `bink.cpp` exports all nine imports. `DDSurfaceType` reads the surface masks, and `CopyToBuffer` writes 565, 555 or XRGB8888. It only needs to accept our DD7 surface object. [kit survey]

---

## 8. DirectInput in the engine (`IS*`) [static]
Source: the engine pseudocode. Details and tables are in `analysis/com-inventory/dinput_notes.md`.

### 8.1 Creation and devices
- **`DirectInputCreateA(hInst, 0x0700, &g_pDI, NULL)`**, once, in `ISInitialize` (LLE+0x4a60f). `g_pDI` is at LLE+0x128b94.
- **`IDirectInputA`** is used only for `CreateDevice` (3: `GUID_SysKeyboard`, then `GUID_SysMouse`) and `Release` (2). There is no EnumDevices, no joystick and no Device2.
- **IDirectInputDeviceA methods used:**

  | Slot | Method | Use |
  |---|---|---|
  | 11 | SetDataFormat | **embedded copies** of `c_dfDIKeyboard` (LLE+0x9679c) and `c_dfDIMouse` (LLE+0x96784). Both are byte-identical to dinput.lib with `DIDF_RELAXIS`; the mouse has 7 objects and the Z axis is optional. |
  | 13 | SetCooperativeLevel | keyboard `NONEXCLUSIVE \| FOREGROUND` (6); mouse `EXCLUSIVE \| FOREGROUND` (5), reset to 6 in `ISMouse::Suspend` |
  | 6 | SetProperty | `DIPROP_BUFFERSIZE` = 256 on both (`DIPROP_AXISMODE` is in dead code) |
  | 5 | GetProperty | `DIPROP_GRANULARITY` by offset, for each axis that `GetObjectInfo` found. It is used as an integer **divisor**, so 0 would raise #DE. Measured on legion: X = 1, Y = 1; that mouse has no Z axis, so Z was not queried. |
  | 14 | GetObjectInfo | 7 calls, `dwSize = 0x13c`, `DIPH_BYOFFSET`. X, Y and Right are dereferenced without null checks. |
  | 7 / 8 | Acquire / Unacquire | from Resume/Suspend only. There is **no re-acquire on error.** |
  | 9 | GetDeviceState | mouse `cb = 0x10` (`DIMOUSESTATE`); keyboard `cb = 0x100` |
  | 10 | GetDeviceData | 16-byte records, 256 entries, flags 0, flush form `(0x10, NULL, &INFINITE, 0)`. `DI_BUFFEROVERFLOW` triggers a resync. |

- **Focus.** `WM_ACTIVATE` / `WM_ACTIVATEAPP` in the EXE's WndProc (EXE+0x2622dd) call Resume/Suspend.
- **Text input** comes from `WM_CHAR`, not from DirectInput.
- **Cursor.** `WM_SETCURSOR` calls `SetCursor(NULL)`; the game draws its own cursor.

### 8.2 Threads and the cursor
- **Input thread** (EXE+0x1110fe, which contains the measured EXE+0x111134):
  - each pass: lock, `ISMouse::RefreshXY` (GetDeviceState), integrate the cursor, unlock, `Sleep(25)`;
  - that gives about 35 passes/s [measured: 35/s].
- **Main loop** (EXE+0x261889): keyboard `Refresh`, then mouse `Refresh` (GetDeviceData).
- **Cursor maths** (EXE+0x110e5d) normalise by the smallest |delta| ever seen. Integer mickeys with ±1 steps keep the gain sane.

### 8.3 Behaviours the HLE must reproduce
- **Relative state.** `GetDeviceState` returns motion since the previous `GetDeviceState`, independently of the buffer. Two threads call it.
- **Motion in the buffer.** Mouse motion must also produce `DIMOFS_X/Y` buffer records, even though their values are ignored. Otherwise `Refresh` never reads X/Y and UI hover breaks. [inferred]
- **Granularity:** X = 1, Y = 1, Z = 120. Wheel input comes in multiples of 120; carry trackpad fractions over to the next event.
- **One record per button transition.**
- **Keyboard buffer cap.** Never return exactly 256 keyboard records with `DI_OK`, because a byte loop counter (LLE+0x4bffa) would hang. Cap at 255, or return `DI_BUFFEROVERFLOW`.
- **Focus loss.** While focus is lost, return `DIERR_NOTACQUIRED`. On regaining focus, deliver `WM_ACTIVATE` / `WM_ACTIVATEAPP` so the game re-acquires.

### 8.4 Kit fit
- **Already there** (`dx/dinput.cpp`): the 8-slot `IDirectInputA`, `GetDeviceState`, buffered `GetDeviceData` and `DIPROP_BUFFERSIZE`.
- **Missing:** `GetObjectInfo` and `GetProperty(GRANULARITY)`.
- **To check:** the record semantics above.
- **Effort:** about 0.5 week.

---

## 9. What the kit already provides
Source: the survey of `spikes/ee-recomp/kit/` at `eb32cc4`. File:line detail is in `analysis/com-inventory/kit_survey.md`.

### 9.1 DirectDraw (`dx/ddraw.cpp`)
- **Interfaces:** DD 1/2/4 and Surface 1–4, plus Palette, Clipper and ColorControl.
- **No version 7.** `DirectDrawCreateEx` deliberately returns `DDERR_UNSUPPORTED` (`ddraw.cpp:4808`).
- **Surfaces:**
  - guest-heap memory with explicit masks, so 555, 1555, 4444, 565, X888 and 8888 all work;
  - display modes and the primary are **8- or 16-bit only**;
  - **no mip chains**.
- **Blt:** colour and depth fill, nearest-neighbour stretch and colour keys, with no format conversion.
- **Flip** swaps only the front buffer and the first back buffer.
- **GetDC** binds the surface memory as a GDI DIB (565 hard-coded for 16-bit).
- **Present:** goes to an SDL3/Metal layer.

### 9.2 Direct3D (`dx/d3d.cpp`, `host/d3d_render.cpp`)
- **Interfaces:** D3D 1/2/3, Device2/3, Texture2, Viewport, Material and Light.
- **Recorder and renderer.** A command recorder plus a GPU renderer. It handles **both TL and untransformed** vertices, building the MVP from World/View/Projection.
- **Render states honoured:** z, cull, shading, alpha test/blend, fog (vertex and table), specular, colour key, filters and address modes. Real mipmaps exist in the renderer.
- **Gaps:**
  - **No lighting:** an unlit `D3DVERTEX` draws white (`d3d_render.cpp:24-29`).
  - **Fixed vertex formats:** only the three 32-byte ones (FVF 0x112, 0x1e2, 0x1c4).
  - **Texture stages:** stage 0, folded into the legacy blend modes.
  - **No VB, no strided draws**, and no version 7.
- **Coherence.** GPU output is read back before any Lock, Blt, GetDC or Flip touches a render target.

### 9.3 Direct3D 9 (`dx/d3d9*.cpp`)
- **Backend.** A capable Metal backend: 16 samplers, many formats and its own render thread.
- **No fixed-function generator.** `SetFVF` and the shader setters are stubs, and draws without shaders are skipped (`d3d9.cpp:1584-1591`).
- **Separate world.** It does not share coherence with DirectDraw surfaces.

### 9.4 Other components
- **DirectInput:** see §8.4.
- **Bink:** all nine imports are there, through FFmpeg.
- **GDI:**
  - `CreateFontA` and `ExtTextOutA` are logging-only stubs that return 0;
  - `GetTextExtentPoint32A` is absent;
  - the W variants are real (stb_truetype; Open Sans as the Tahoma/Arial stand-in).

### 9.5 Structural cautions
- **Populous-specific heuristics must be disabled for EE:**
  - frame sealing from the message-pump path (`ddraw.cpp:884-888`), which would split EE frames, because EE pumps messages on several threads;
  - the terrain-tile clamp and detail shader (`d3d_render.cpp:2742-2770`);
  - cursor and HUD detection.
- **Single render target.** The kit has one global render target. EE needs render-to-texture (§4.6).

---

## 10. The minimal D3D7/DD7 shim

### 10.1 Checklist: methods
**Exports:**
- [ ] `DirectDrawEnumerateA` and `DirectDrawEnumerateExA(…, 7)`: one device, the primary display (NULL GUID).
- [ ] `DirectDrawCreateEx(NULL, &dd, IID_IDirectDraw7, NULL)`.

**IDirectDraw7** (30-slot vtable, 0–29 including StartModeTest and EvaluateMode; 12 implemented, the rest `DDERR_UNSUPPORTED`):
- [ ] QueryInterface (`IID_IDirect3D7`) and Release.
- [ ] GetCaps (`DDCAPS_DX7`, 0x17c): set `DDCAPS_3D` (0x1).
- [ ] EnumDisplayModes(0, NULL): 16-bit R5G6B5 and 32-bit X8R8G8B8 modes. Include **800×600×16**, the desktop size, and the registry's `Resolution` and bpp.
- [ ] GetDisplayMode, SetCooperativeLevel (store the flags: 0x408, 0x8, 0x80, 0x513, 0x413, 0x691), SetDisplayMode(w, h, bpp, refresh, 0).
- [ ] GetAvailableVidMem(`DDSCAPS_VIDEOMEMORY`): total and free above 8 MB.
- [ ] TestCooperativeLevel and RestoreAllSurfaces: return `DD_OK`.
- [ ] GetDeviceIdentifier(0): the description must contain neither `RAGE` nor `PRO`, and the vendor/device must not be 0x1002:0x5046 or 0x121a:0x0009.
- [ ] CreateSurface for GE types 0–6 (§3.3), including:
  - flip chains with `GetAttachedSurface(DDSCAPS_FLIP)`;
  - **mip chains** with `GetAttachedSurface(DDSCAPS_MIPMAP)`;
  - Z16/Z24/Z32;
  - `TEXTUREMANAGE`;
  - `DDSD_CKSRCBLT` at creation.

**IDirectDrawSurface7** (49-slot vtable, 12 implemented):
- [ ] Release, AddAttachedSurface(z), DeleteAttachedSurface.
- [ ] Blt: `COLORFILL` (0x400); copy or stretch with `DONOTWAIT` (0x8000000) and optional `KEYSRC` (0x8000); flags 0 for the windowed present to the primary.
  - Measured: 3–4 per frame, all copies between video-memory surfaces (the cursor save/restore and a 64×64 texture). Do them **on the GPU**, without a CPU readback.
- [x] Flip(NULL, 0x20 / 0x28). **Flip exchanges the front and back buffers**, as Windows does: after a Flip the back buffer holds the frame shown before. The menu's cursor keeps one save surface per buffer (§11.6). (This said "the back buffer must keep its contents" until P3-G: a copy-style present left a cursor trail.)
- [ ] GetAttachedSurface.
- [ ] GetDC / ReleaseDC: a DIB over the surface (32- and 16-bit).
- [ ] Lock(NULL, desc, 0, NULL) / Unlock(NULL): return `lpSurface` and `lPitch`.
- [ ] Restore, SetColorKey(`DDCKEY_SRCBLT`).
- [ ] For Bink: whatever `BinkDDSurfaceType` reads, i.e. the pixel format.

**IDirect3D7:**
- [ ] Release.
- [ ] EnumDevices: one HAL device with `HWRASTERIZATION`. Add a TnLHAL device with `HWTRANSFORMANDLIGHT` for the TnL plugin.
- [ ] CreateDevice(`IID_IDirect3DHALDevice` or `IID_IDirect3DTnLHalDevice`, render target).
- [ ] CreateVertexBuffer(FVF 0x152, 2442 vertices).

**IDirect3DDevice7** (49-slot vtable, 19 implemented):
- [ ] Release.
- [ ] GetCaps (`D3DDEVICEDESC7`: `dwDeviceRenderBitDepth` with `DDBD_16 | DDBD_32`, and the devcaps above).
- [ ] EnumTextureFormats: A8R8G8B8, X8R8G8B8, A1R5G5B5, A4R4G4B4, R5G6B5, X1R5G5B5. **Must include A8R8G8B8** for 32-bit textures.
  - Measured in use: A8R8G8B8 with full mip chains, and A4R4G4B4.
  - Not seen: DXT1, R5G5B5A1, R5G5B5X1.
  - Texture creation and locking run on the texture worker thread, so the shim must be thread-safe.
- [ ] BeginScene, EndScene.
- [ ] SetRenderTarget(surface, 0) to the back buffer *or a 3D texture*.
- [ ] Clear(1, rect, TARGET or ZBUFFER, …).
- [ ] SetTransform(1, 2, 3), SetViewport(`D3DVIEWPORT7`). The viewport includes MinZ = MaxZ ranges (0..0, 1..1) [measured].
- [ ] SetMaterial, SetLight / GetLight (8 slots, point and directional), LightEnable.
- [ ] SetRenderState (32 states, §10.2), SetTexture(0/1), SetTextureStageState (§10.2).
- [ ] DrawPrimitiveVB (point, line and triangle lists).
- [ ] DrawIndexedPrimitiveVB (triangle list, 16-bit system-memory indices).
- [ ] ValidateDevice: return `D3D_OK` with 1 pass.

**IDirect3DVertexBuffer7:**
- [ ] Release.
- [ ] Lock(0x2020 / 0x1020, &p, NULL), Unlock. The VB is a plain guest-memory array.

**Total: 50 methods plus 3 exports.** This is the entire D3D7/DD7 surface of both plugins.

### 10.2 Checklist: state
**Render states.**
- [ ] **Must render** (24):
  - ZENABLE, ZWRITEENABLE, ZFUNC;
  - ALPHATESTENABLE, ALPHAFUNC, ALPHAREF;
  - ALPHABLENDENABLE, SRCBLEND, DESTBLEND (13 factors);
  - CULLMODE;
  - FILLMODE (SOLID; WIREFRAME and POINT are debug only);
  - SHADEMODE;
  - FOGENABLE, FOGCOLOR, FOGSTART, FOGEND, with FOGVERTEXMODE = LINEAR;
  - LIGHTING, AMBIENT, COLORVERTEX, DIFFUSE/AMBIENTMATERIALSOURCE;
  - SPECULARENABLE;
  - COLORKEYENABLE.
- [ ] **Accept and ignore** (8): ANTIALIAS, DITHERENABLE, CLIPPING, EXTENTS, LOCALVIEWER (always FALSE), NORMALIZENORMALS (always TRUE, so always normalise), SPECULAR/EMISSIVEMATERIALSOURCE (always MATERIAL).

**Texture stage states.**
- [ ] **Stage 0:**
  - COLOROP / ALPHAOP: DISABLE, SELECTARG1, SELECTARG2, MODULATE;
  - ARG1 / ARG2: TEXTURE, DIFFUSE, CURRENT;
  - TEXCOORDINDEX = 0;
  - ADDRESS: WRAP, MIRROR, CLAMP;
  - MAGFILTER: POINT, LINEAR, FLATCUBIC → linear;
  - MINFILTER: POINT, LINEAR, ANISOTROPIC;
  - MIPFILTER: NONE, POINT, LINEAR;
  - MIPMAPLODBIAS.
- [ ] **Stage 1:** DISABLE only.

**Vertex pipeline.**
- [ ] FVF 0x152 only.
- [ ] Transform: P · V · W with D3D row-vector matrices.
- [ ] D3D7 viewport mapping, **including D3D's pixel-centre convention**. The 2D orthographic UI has no half-pixel offset, so Metal needs a −0.5 px shift to match Windows sampling.
- [ ] Lighting: per-vertex D3D7 fixed-function, ≤ 8 lights, point (range, attenuation 0/1/2) and directional, diffuse plus ambient, specular only if enabled.
  - Measured: one directional light, LIGHTING on for one span per frame, material or vertex colour sources, AMBIENT.
  - Point lights are static code only, so implement them second.
- [ ] Linear vertex fog from view-space z.
- [ ] Clipping by the GPU.

### 10.3 Recommended design
**Build on the kit's D3D3 recorder path, not on its D3D9 path.**

1. **DD7 and Surface7** become two new vtables over the existing DirectDraw object and surface (DDSURFACEDESC2 / DDSCAPS2 adapters). Add to them:
   - 32-bpp display modes and primary;
   - mip chains;
   - `TEXTUREMANAGE`;
   - attached-surface queries by caps.
2. **D3D7 and Device7** become adapters onto Device3's internals. D3D7's `DrawPrimitive` signature is Device3's; a VB draw is a draw from the VB's guest memory with the VB's FVF.
3. **Generalise the command record** for the vertex format and lighting:
   - FVF decode for 0x152 (normal and diffuse);
   - the 8-light table and the material;
   - the colour-source states;
   - the fog vertex mode.
4. **Extend the existing MSL shader in `host/d3d_render.cpp`** with:
   - a lit vertex path (D3D7 lighting equations);
   - linear vertex fog;
   - the stage-0 combiner (four ops × three args, encoded as uniforms).
   The legacy blend folding becomes one case of this.
5. **Render-to-texture.** Allow a 3D-capable texture as a render target. Coherence uses the existing readback-before-CPU-access rule.
6. **Presentation.** Present only on `Flip`, or on a `Blt` whose destination is the primary. **Turn the pump-based frame sealing off.**
7. **GDI on surfaces** keeps the kit's DIB-over-guest-memory approach for textures (glyph atlases). Add the ANSI text calls (gap 9).
   - ~~Overlay for per-frame GDI on the back buffer~~: **not needed**. The census measured 0 surface `GetDC` calls per frame at the menu and in game (§11.3).

**Why this route:**
- EE mixes DirectDraw CPU access (colour-fill Blt, Lock for Bink, GetDC for glyph atlases, windowed-present Blt) with D3D rendering. Only the recorder path already keeps the two coherent.
- The D3D9 route would need both a fixed-function→MSL generator and DirectDraw coherence written from scratch.
- EE's fixed-function needs are narrow (§10.2), so one ubershader with a handful of uniforms covers them.
- Software T&L in the shim (CPU) is the fallback if bit-matching lighting against Windows ever matters. It doesn't for lockstep: rendering never feeds the simulation.

**Later (PLAN Phase 7).** A native rewrite of `DX7Rasterizer` against the engine's `GERasterizer` interface (§2) would remove D3D7 entirely. It is a larger API, with about 150 virtuals across the GE classes. It is the right step for widescreen and HiDPI, but not for Stage 1.

### 10.4 Effort
Rough focused weeks, one developer with agents:

| Item | Weeks |
|---|---|
| DD7 + Surface7 adapters, 32-bpp modes, mip chains, flip chains, Z formats | 1–1.5 |
| D3D7 + Device7 + VB7 adapters, enumeration and caps | 0.75–1 |
| Renderer: FVF 0x152, lighting, vertex fog, stage-0 combiner, sampler states, render-to-texture, present rules | 1.5–2 |
| Bink frame-buffer path on DD7 surfaces | 0.25 |
| Bring-up and screenshot diffs against `legion` (menus, map, movies, editor) | 1 |
| **D3D7/DD7 total** (ADR-001 gap 8 estimated 5–7) | **4.5–5.75** |
| GDI ANSI text (gap 9) | 0.5–1 |
| DirectInput fixes (§8.4) | 0.5 |
| TnL plugin support | ~0.2 |

---

## 11. Dynamic confirmation: `windows/frida/com_census.js`

### 11.1 What it does
- **Discovery.** It hooks `DDRAW!DirectDrawCreateEx` (the GOG wrapper) and `DINPUT!DirectInputCreateA`. When attached to a running game, it finds the live interfaces through the globals listed in its header comment.
- **Counting.** It patches each vtable slot of every reached interface with a native counting stub (the `iat_census.js` technique). Each call is counted per thread, caller module and selected arguments, for example SetRenderState (state, value), TSS (stage, type, value), draw type plus the primitive-count sum, and Blt, Lock and Flip flags.
- **Decoding.** Rare calls are decoded in JS: CreateSurface descriptions, VB descriptions, IIDs, caps, device identifier, enumerated modes, texture/Z formats and D3D devices, and the DirectInput data formats, properties and buffered-read results.
- **Samplers.** It samples transforms (classified as identity, orthographic or perspective), materials, lights, viewports and surface locks.
- **Caller filter.** A caller of `other` means the GOG wrapper's own internal calls. Read the rows with caller = `dx7hrdisplay.dll` / `low-level engine.dll`.

### 11.2 How it was run, and a known gap
The captures were taken on 2026-10-14 with GOG's D3D9-based `ddraw.dll` and the "Direct3D" rasterizer (`DX7HRDisplay`), at `Texture Bit Depth = 32` and 1920×1080. They are on the Mac under `analysis/legion-captures/`.

| Capture | What |
|---|---|
| `com-spawn` | spawned, 150 s, intro skipped to the menu |
| `com-menu` | attached, 120 s at the main menu |
| `com-ingame` | attached, 60 s with `iat_census`; Small random map vs one AI, Copper Age |

Summarise them with `python3 -I tools/graphics/com_summary.py analysis/legion-captures/com-{spawn,menu,ingame}`. It prints Markdown, including the checklist diff against §4.7.

**Gap: the spawn capture missed every DirectDraw/D3D creation call.** This is a script bug.
- The plugin scan loads and unloads `ddraw.dll`, then the plugin's import maps it again at a new base.
- `onModule()` in `com_census.js` dedupes by path, so the second mapping's `DirectDrawCreateEx` was never hooked.
- DirectInput, which is not unloaded, was captured fully.
- **Fix:** key the `seen` set by `path + '@' + base`, or remove the entry in `onRemoved`.
- **What is still unmeasured as a result:** enumeration (`EnumDisplayModes`, `EnumDevices`, `EnumTextureFormats`), `SetCooperativeLevel` / `SetDisplayMode`, start-up texture and atlas creation, and Bink. Re-run the spawn capture with the fix.

### 11.3 Results [measured]
**Frame rate.**
- Menu: 396.9 fps.
- In game: 78.7 fps.
- All D3D calls come from `dx7hrdisplay.dll` on the render thread, except texture creation and upload (below).
- Per-frame counts are very regular: whole numbers per frame over 4,946 frames.

**In-game calls per frame.** Game callers only:

| Call | Per frame | Detail |
|---|---|---|
| SetRenderState | 226 | CLIPPING 128, AMBIENT 38, the rest 1–3 each |
| SetTextureStageState | 172 | stage 1 COLOROP/ALPHAOP = DISABLE 67 + 67; stage 0 COLOROP/ALPHAOP MODULATE ↔ DISABLE 5 + 5 each; MIPMAPLODBIAS 6; MIPFILTER 4; ADDRESS 4; MIN/MAGFILTER 2 + 2 |
| SetTexture | 139 | stage 0 non-null 67, stage 0 NULL 5, stage 1 NULL 67. This confirms the ≈64/frame inferred from `GETexture::Promote`. |
| VB Lock / Unlock | 76 / 76 | `NOOVERWRITE\|WRITEONLY` 74.8, `DISCARDCONTENTS\|WRITEONLY` 1.2 |
| DrawPrimitiveVB | 40 | TRIANGLELIST 36 (avg 31.6 vertices); LINELIST 4 (2 vertices) |
| DrawIndexedPrimitiveVB | 36 | TRIANGLELIST, avg 118 indices |
| SetTransform | 38 | WORLD 20, VIEW 9, PROJECTION 9 |
| SetMaterial | 24 | |
| SetViewport | 12 | |
| Surface Blt | 4 | |
| Device Clear | 2 | `D3DCLEAR_ZBUFFER` only |
| Surface Lock / Unlock | 1 / 1 | |
| BeginScene / EndScene / Flip | 1 each | Flip flags 0x20 |

Geometry per frame is about 1,800 triangles: 1,420 indexed plus 380 non-indexed. That is trivial for Metal. The shim's cost is in call volume: about 850 COM calls per frame, of which about 540 are state changes (render state, TSS and SetTexture).

**The menu draws nothing per frame.** In 120 s at the menu, every frame had:
- BeginScene, `Clear(ZBUFFER)`, `SetViewport(0,0,1024,768)`;
- the blend toggles: ALPHABLEND 0/1, SRC ONE/SRCALPHA, DEST ZERO/INVSRCALPHA;
- **3 Blts**;
- EndScene and Flip.

There were **zero draw calls and zero texture changes.** The menu image is drawn once into each buffer of the flip chain and then kept. The 3 Blts are the software cursor, all through `CopyTo` (HR+0xe1d1), on a 32×32 rectangle at the cursor:
1. restore the saved background to the back buffer;
2. save the back buffer under the cursor to a temporary surface;
3. draw the cursor with `KEYSRC|DONOTWAIT`.

There are **two** temporary surfaces, used in alternate frames, one per buffer of the two-buffer chain (§11.6).

In game, a fourth Blt per frame copies a 64×64 surface into another 64×64 surface (below).

**Textures.**
- **Creation and upload run on the texture/asset worker thread,** concurrently with rendering. That thread did all 14 `CreateSurface` calls and the 182 mip-level Lock/Unlock pairs in the 60 s window.
- **All 14 creations were A8R8G8B8** with caps2 `0x18` (`TEXTUREMANAGE|HINTSTATIC`):
  - 6 × 128² with 8 mip levels;
  - 3 × 256² with 9 levels;
  - 3 × 64² with 7 levels;
  - 2 × 64² without mips.
  Mip chains go all the way down to 1×1, and every level is locked (flags 0) and filled.
- **A 64×64 A4R4G4B4 surface is locked and rewritten every frame** on the render thread, then blitted into another 64×64 surface. It is probably the minimap or a fog texture [inferred].
- **Not seen:** DXT1, R5G5B5A1, R5G5B5X1 and render-target textures. Start-up textures were created before the attach, so this is a lower bound.

**Lighting and fog.**
- **`LIGHTING` is on for one span every frame:** it is set TRUE once and FALSE once per frame.
- **Light:** one light, index 0, **directional**, range 0, attenuation (1,0,0).
  - It was re-set 5 times in 60 s, each time framed by `LightEnable(0, FALSE/TRUE)`.
  - Each re-set was followed by `GetLight(0)`, the CPU terrain relight.
  - No point light was seen.
- **Materials:**
  - white (1,1,1,1) and **blue (0,0,1,1)**, the player colour;
  - power 0, specular 0, emissive 0;
  - `COLORVERTEX` and the diffuse/ambient material sources switch 0 ↔ 1 twice per frame;
  - `AMBIENT` takes 5 values, 38 changes per frame.
- **Which draws are lit:** most likely the model pass (`DrawModelHierarchy`: `SetMaterial` + `DrawIndexedPrimitiveVB`) [inferred]. Proving it needs an ordered one-frame trace.
- **Fog:** `FOGENABLE` toggles twice per frame. `FOGSTART` / `FOGEND` / `FOGCOLOR` did not change during the capture, because they are set once and cached.

**Transforms and viewports.**
- **WORLD:** affine or identity.
- **VIEW:** affine or identity.
- **PROJECTION:**
  - perspective, with m22 = 1.100, m32 = −3.517, or m22 = 1.000, m32 = −3.196;
  - orthographic, (2/W, −2/H, −1, +1) for W×H = 1920×945, 1920×1080 and 305×227.
- **Viewports:**
  - the game view `0,32,1920,945`, with z ranges **0..1, 0..0 and 1..1**;
  - the full screen `0,0,1920,1080`;
  - the minimap `1611,785,305,227`.
- So the game also uses degenerate depth ranges (MinZ = MaxZ) to force depth to 0 or 1.

**GDI.**
- **There is no GDI onto surfaces:** `IDirectDrawSurface7::GetDC` was called 0 times at the menu and in game. The plugin's own GDI imports (`ExtTextOutA`, `SetTextColor`, …) were never called in game.
- **The only GDI is the engine's text measuring:** `GetDC` / `ReleaseDC` / `GetTextExtentPoint32A` at 82/s each and `SelectObject` at 165/s, about once per frame.

**Present.** Every frame is `Flip(NULL, DDFLIP_DONOTWAIT)`, without `NOVSYNC`; the GOG wrapper does not hold it to vsync (397 fps at the menu). No Blt ever targeted the primary, so the windowed present path did not run.

**Display modes.** The menu ran at 1024×768 and the game at 1920×1080, which is the registry `Resolution`. The enumeration itself was not captured (§11.2).

**DirectInput** (spawn + menu + game). Everything matches §8:
- `DirectInputCreateA(…, 0x700)`.
- Keyboard and then mouse are created.
- **Cooperative levels:** keyboard 6, mouse 5, and the mouse goes back to 6 on suspend.
- **Data formats:** `size = 24, objsize = 16, flags = DIDF_RELAXIS`; keyboard 256 bytes / 256 objects, mouse 16 bytes / 7 objects.
- **`BUFFERSIZE`** = 256 on both.
- **`GetObjectInfo`:** offsets 0, 4, 8, 0xc, 0xd, 0xe, 0xf with `BYOFFSET` and `dwSize = 0x13c`. On legion's mouse the Z axis and buttons 4 and 5 return `DIERR_OBJECTNOTFOUND`.
- **`GetProperty(GRANULARITY)`:** X = 1, Y = 1.
- **`GetDeviceData`:** about 548/s per device on the main thread, almost always 0 records; there is also the `(NULL, INFINITE)` flush.
- **`GetDeviceState(16)`** on the mouse: once per frame on the render thread, plus about 37/s on the input thread. The keyboard's `GetDeviceState(256)` runs only on resume.
- **Results:** all hr = 0.
- **Not called by the game:** `IDirectInputDeviceA::Initialize`; it shows up only as dinput's own internal call.

**Coverage.** Across all three captures, **no game call fell outside the static method list (§4.7) or the static render-state and TSS lists (§10.2).** The only calls outside it were `AddRef` and `Initialize` made by the wrapper and dinput themselves.

### 11.4 Static claims against the measurements
| Claim (section) | Status |
|---|---|
| Untransformed FVF 0x152 vertices through one VB; only DrawPrimitiveVB / DrawIndexedPrimitiveVB (§0, §4.1) | **confirmed**: only these draws, locks 0x1020 / 0x2020 |
| SetTransform W/V/P; 2D as orthographic projection (§4.2) | **confirmed**: orthographic matrices match `Set2DMode`, sized from the viewport |
| D3D lighting used (§4.3) | **confirmed**: LIGHTING on once per frame; materials; AMBIENT. Point lights are **unobserved**: one directional light only. |
| 32 render states (§4.5) | **confirmed as a superset**: 16 changed in game; the other 16 are set once and cached, or never set during these windows. None outside the list. |
| TSS: stage 0 only, stage 1 always DISABLE (§4.4) | **confirmed**. MODULATE and DISABLE observed; SELECTARG1/2 **unobserved**; the ARG states were never re-set (cached). |
| Clear TARGET and ZBUFFER (§4.6) | ZBUFFER **confirmed**; TARGET **unobserved** |
| Texture types and caps2 (§3.3) | **corrected**: managed caps2 is 0x14/0x18, not 0x10/0x14; desc+0x08 is the *static* hint. Mipmapped A8R8G8B8 with full chains **confirmed**; DXT1 / 5551 / render-target textures **unobserved** |
| Blt flags DONOTWAIT, +KEYSRC; no BltFast (§3.4) | **confirmed**, 3–4 per frame (cursor and 64×64 copy). COLORFILL **unobserved**. |
| Present by Flip(0x20 / 0x28); windowed Blt to primary (§3.2) | Flip 0x20 **confirmed**; windowed path **unobserved** |
| GDI onto surfaces mostly at atlas build (§6) | **confirmed for steady state**: 0 surface `GetDC` at the menu and in game. Atlas build **unobserved** (spawn gap). |
| Engine text measuring explains the GDI rate (§6) | **confirmed**: about 1 `GetTextExtentPoint32A` per frame |
| Device creation, enumeration, cooperative-level flags (§3.1–3.2) | **unobserved this time** (spawn gap); the flags were measured earlier (`legion.md` §5.1) |
| Bink path (§7) | **unobserved** (intro skipped, spawn gap) |
| DirectInput (§8) | **confirmed** in every measured detail; Z granularity **unobserved** (no wheel) |
| Threads (§1.2) | **confirmed** for render; **new**: textures are created and locked on the texture worker thread |
| Rates (§2.4) | **confirmed**: SetTexture about 64/frame (measured 67) |

### 11.5 Changes to the shim checklist (§10) from the measurements
1. **Flip must keep each buffer's contents** (no discard), and it must **exchange** the buffers rather than copy: corrected in §11.6. The menu is drawn once into each buffer and then only patched by the cursor Blts, so a discard-style present would show an empty menu. **[new requirement]**
2. **Blt between video-memory surfaces should be a GPU copy, not a CPU readback.** That covers back buffer ↔ cursor-save surface, cursor → back buffer with `KEYSRC`, and the 64×64 per-frame copy. Three or four per frame. **[new]**
3. **Viewport with MinZ = MaxZ** (0..0 and 1..1) must work. Metal accepts it. **[new]**
4. **Thread safety.** `CreateSurface`, `GetAttachedSurface` and `Lock`/`Unlock` on textures run on the texture worker thread while the render thread draws. **[new]**
5. **Lighting can start with directional lights plus ambient and material sources.** Keep point lights from the static code, but at lower priority.
6. **Drop the "GDI overlay on the render target" contingency** (§10.3, item 7). Surface GDI is not used per frame.
7. **`EnumTextureFormats`:** keep A8R8G8B8 and A4R4G4B4, both measured in use. Keep R5G6B5 / A1R5G5B5 / X1R5G5B5 for `Texture Bit Depth = 16` (unmeasured). Omit DXT1 and the 5551 / 5550 formats unless a capture shows them requested.
8. **Effort is unchanged at about 4.5–6 weeks.** The overlay and point-light work shrink; Flip and Blt-on-GPU are small additions.

---

### 11.6 Correction (P3-G): Flip exchanges, it does not copy [measured, headless]

P3-B implemented §11.5's first item as a copy: `Flip` copied the back buffer into the front buffer, so the back buffer kept its contents. A player saw **copies of the gold cursor left all over the main menu** (fragments, more where it moved slowly), and the same trail shows headless at 16 and at 32 bpp (`tests/menus/cursor-trail-16.script`; the menu tests had never compared two frames).

- **The trace** (`ee_boot --trace-match "IDirectDrawSurface7::|IDirectDraw7::"`, main menu, render thread) has, per frame: `Blt(back, save X)` (restore), `Blt(save X, back)` (save), `Blt(back, cursor, KEYSRC)`, `Flip`, with X alternating between two surfaces every frame (`0100fba0`, `0100fb90`, then `0100fba0` again; 16 and 32 bpp alike). Each save surface belongs to one buffer: a frame restores, on the buffer it draws to, what it saved there two frames before. That is right on Windows, where `Flip` exchanges the buffers' memory, so the back buffer is the buffer of two frames ago.
- **With a copy** there is one buffer in effect: the restore of frame N puts back the background saved at frame N−2 while the cursor drawn at N−1 is still there; it goes only at N+1. A save that overlaps the cursor still on screen captures part of it, and a later restore stamps that fragment back: the trail. A rest saves a whole cursor into one surface, which comes back when the cursor leaves.
- **The fix** (kit `feat/cursor16`): `Flip` exchanges the buffers' guest memory and backend resources (new seam call `host_d7_swap`; the GPU renderer swaps two textures, nothing is copied), and two back buffers rotate. After it, `rest-before` and `rest-after` of the scenario are the same picture (hash-identical headless), and every screen the menu tests reach is still from one frame to the next (`wait still`: both buffers hold the same picture). In game the frame is redrawn every frame, so the game itself looked the same with either Flip (800×600×16 and ×32, seeded; parity.md's SSIM unchanged).

## 12. Open questions
**Closed by the census:**
- the texture formats and mips in steady state;
- `LIGHTING` is used;
- surface GDI per frame is 0;
- the windowed present is unused (with GOG's wrapper);
- per-frame call budgets.

**Still open:**
1. **Start-up textures, glyph atlases, enumeration, mode setting and Bink.** Re-run `com-spawn` after the `onModule` fix (§11.2), and play one movie instead of skipping it.
2. **Which draws run with `LIGHTING` on.** This needs an ordered one-frame trace (a future `trace_frame` option in `com_census.js`).
3. **16-bit textures.** Capture once with `Texture Bit Depth = 16`, the GOG default, to see the 16-bit formats requested.
4. **What do `EnumDisplayModes` results drive?** The options screen's mode list comes from them. We need to decide which modes to report on a Mac: native Retina sizes vs classic 4:3 sizes.
5. **Pixel-exactness target for UI text.** Glyph metrics from our TrueType renderer vs Windows GDI decide whether atlases and line breaks match.

---

## 13. Reproduction
Everything below writes only under `analysis/com-inventory/`, which is ignored.

```sh
cd analysis/com-inventory
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r ../../tools/requirements.txt
export GHIDRA_MAXMEM=6G GHIDRA_HEADLESS_MAXMEM=6G JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH=/opt/homebrew/opt/openjdk@21/bin:$PATH
cp ../../spikes/ee-recomp/kit/tools/ExportProgram.java scripts/
G="../../original/gog/Empire Earth"
/opt/homebrew/Cellar/ghidra/12.1.4/libexec/support/analyzeHeadless ghidra plugins \
  -import "$G/DX7HRDisplay.dll" "$G/DX7HRTnLDisplay.dll" -scriptPath scripts -postScript ExportProgram.java export   # ~30 s
for m in DX7HRDisplay DX7HRTnLDisplay; do
  .venv/bin/python -I tools/comcalls.py export/$m.dll "$G/$m.dll" work/$m      # pseudocode vtable calls, IAT names
  .venv/bin/python -I tools/asmcalls.py export/$m.dll work/$m/asmcalls.tsv     # asm-level COM vs thiscall
done
.venv/bin/python -I tools/annotate.py work/DX7HRDisplay DX7HRDisplay > work/DX7HRDisplay/annotated.tsv
.venv/bin/python -I tools/peread.py "$G/DX7HRDisplay.dll" guid:10010678 guid:10010688 guid:10010698 u7:10016754
```
