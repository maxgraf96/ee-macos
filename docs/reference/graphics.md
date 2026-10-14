# Graphics, video and input: COM inventory (Phase 1)

Status: static inventory done, 2026-10-14. Dynamic confirmation is pending (`windows/frida/com_census.js`, §11).
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
   - The frame loop does release a DC on the render target after `EndScene` (§2.4), so GDI-font text directly on the back buffer is possible. The census measures it (§11).
5. **Recommended design: extend the kit's D3D2/3 recorder and GPU renderer** (`dx/ddraw.cpp`, `dx/d3d.cpp`, `host/d3d_render.cpp`) with version-7 interfaces. Do not build on the D3D9 path. (§10.3)
   - The recorder path already keeps DirectDraw surfaces coherent across Lock, Blt, GetDC and Flip, and already renders untransformed vertices with fog, alpha and z.
   - The D3D9 path has no fixed-function generator at all.
   - **Effort:** about **4.5–6 focused weeks** for DD7 + D3D7 + the renderer extensions, including screenshot diffs against `legion` (§10.4). That fits inside ADR-001's 5–7 weeks. GDI text (gap 9) and DirectInput fixes add about 1–1.5 weeks.

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
| +0x08 | dynamic hint |
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
| 5 texture | `CAPS\|HEIGHT\|WIDTH\|PIXELFORMAT` | `TEXTURE` | `TEXTUREMANAGE` (+`HINTDYNAMIC`) | textures, font atlases |
| 5 + 3D flag | same | `TEXTURE \| 3DDEVICE \| VIDEOMEMORY \| LOCALVIDMEM` | `HINTDYNAMIC` / `HINTSTATIC` | render-target textures |
| 6 mipmapped texture | `…\|MIPMAPCOUNT` | `TEXTURE \| MIPMAP \| COMPLEX` (+`3DDEVICE`) | `TEXTUREMANAGE` (+`HINTDYNAMIC`) | Sub-levels come from `GetAttachedSurface(DDSCAPS_MIPMAP)`. |

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
- **So GDI-on-surface happens mostly at font/atlas creation, on *texture* surfaces.** [inferred from static; the census counts `Surface GetDC` per second and target kind]
- **The exception: fonts without an atlas.** Text in such a font is drawn with GDI straight onto the render target during the frame. The DC is then held until `RTSGraphics::Render` calls `TextUnlock` after `EndScene` (§2.4).
- **The measured in-game GDI rate** is `GetDC` / `ReleaseDC` / `GetTextExtentPoint32A` at 92/s each and `SelectObject` at 185/s, with no `ExtTextOutA` [measured]. That is exactly one `GEText::ComputeTextExtent` (LLE+0x446f4) per string: `GetDC(NULL)` through `GEDeviceContext::GetDeviceContext` (LLE+0x49db5), `SelectObject`, `GetTextExtentPoint32A`, `SelectObject` again, `ReleaseDC`. This is text measurement on a screen DC.
- **Fonts** are created with `CreateFontA` in `GEFont::GEFont` (LLE+0x2711c), followed by `GetTextMetricsA`.
- **Shim consequences:**
  - The kit's `GetDC`-on-surface (a DIB over guest memory) fits, because the targets are CPU-side textures, so no GPU readback is needed.
  - The kit is missing `ExtTextOutA`, `GetTextExtentPoint32A`, `CreateFontA` and `GetTextMetricsA` (ADR-001 gap 9).
  - Glyph metrics must match Windows, or the atlas layout and UI text widths will differ (cosmetic only).
- **The EXE's own GDI** (`CreateDIBSection`, `BitBlt`, `Polygon`, `Ellipse`, `TextOutW`, … at EXE+0x3ba000–0x3bc000) is not on the plugin path, and nothing shows it running in the census [measured: not among the in-game hot calls]. Its purpose is left to func-db.

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
  | 5 | GetProperty | `DIPROP_GRANULARITY` for X/Y/Z by offset. It is used as an integer **divisor**, so 0 would raise #DE. |
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

**IDirectDraw7** (29-slot vtable, 12 implemented, the rest `DDERR_UNSUPPORTED`):
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
- [ ] Flip(NULL, 0x20 / 0x28).
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
- [ ] BeginScene, EndScene.
- [ ] SetRenderTarget(surface, 0) to the back buffer *or a 3D texture*.
- [ ] Clear(1, rect, TARGET or ZBUFFER, …).
- [ ] SetTransform(1, 2, 3), SetViewport(`D3DVIEWPORT7`).
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
   - **If the census shows per-frame GDI on the back buffer:** give `GetDC` on the *active render target* a transparent CPU overlay rather than a GPU readback, and composite it at `ReleaseDC` / before present. The game releases that DC after `EndScene` and expects the text on top.

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

### 11.2 How to run it (later, on `legion`)
From the Mac:

```sh
scp windows/frida/com_census.js legion:C:/EEPort/frida/          # or windows/deploy.sh
L() { ssh legion "powershell -NoProfile -ExecutionPolicy Bypass -Command \"& C:\\EEPort\\scripts\\launch.ps1 $*\""; }
# 1. Creation-time calls (enumeration, surfaces, device, DirectInput init): spawn in the user session.
L "frida '--spawn --scripts com_census --seconds 150 --out C:\EEPort\captures\com-spawn'"
# 2. Menu, attached, finding the live objects; start a random map during the run to catch the re-creation.
L start
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts com_census --seconds 120 --out C:\EEPort\captures\com-menu'
# 3. In game (after Single Player -> Play Random Map -> Start Game), together with the import census.
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts com_census,iat_census --seconds 60 --out C:\EEPort\captures\com-ingame'
scp -r legion:C:/EEPort/captures/com-ingame /tmp/
```

**Optional parameters:**
- `--param all_callers=true` also decodes the wrapper's internal calls.
- `--param discover=false` disables attach-time discovery.
- `--param sample_limit=N` changes the sampler size (default 2000).

`summarize.py` does not know `com_census.json` yet. The JSON is self-describing: `rows`, `aggregates`, `events`, `vtables` and `slots`.

### 11.3 What to check
1. **No surprises in coverage.** The `rows` with a game caller should be a subset of §4.7, and the states of §10.2.
2. **Runtime values:**
   - `LIGHTING` and `FOGENABLE` values in game;
   - the `SetLight` types;
   - the projection classes.
3. **`CreateSurface` aggregates:** the texture formats and sizes actually requested, whether DXT1 and 5551 are ever used, mip counts, and render-target textures.
4. **`Surface GetDC` rate and target kinds.** This confirms or refutes "GDI onto surfaces mostly at atlas build" and sizes the back-buffer overlay case (§10.3, item 7). Compare with `ExtTextOutA` in the `iat_census` rows.
5. **Frame cost:** draw calls and primitives per `EndScene` (rows `DrawIndexedPrimitiveVB` / `DrawPrimitiveVB`, `sum`), and SetRenderState / TSS calls per frame.
6. **DirectInput:**
   - `DI GetDeviceData` out-count buckets;
   - `GetDeviceState` hr while focused and unfocused;
   - the `GetProperty(GRANULARITY)` values the real dinput returns (expected 1, 1, 120).

---

## 12. Open questions
1. **Which texture formats and sizes does the engine request?** In particular DXT1 and R5G5B5A1/X1 (format indices 4, 6, 8) and how many mip levels. → census `CreateSurface`.
2. **Is `LIGHTING` ever TRUE in game,** and for which draws (models only)? This sizes the lighting work. → census.
3. **Does the windowed path (Blt to primary, no clipper) ever run in practice?** The GOG wrapper presents windowed but receives fullscreen calls (`0x513`) [measured]. The shim can treat the game as fullscreen-exclusive and present the back buffer itself.
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
