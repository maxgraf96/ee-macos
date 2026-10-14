# Research notes: porting strategies and tooling (2026-10-14)

Gathered by a research agent. recomp-kit, xi-on-anything and 00xJS were then cloned and read directly. **[F]** = fact with source, **[I]** = inference.

## Platform changes, 2025–2026
- **Rosetta:**
  - Apple says Rosetta stays a general tool "through macOS 27". After that only "a subset … aimed at supporting older unmaintained gaming titles, that rely on Intel-based frameworks" remains. [F] https://developer.apple.com/documentation/apple-silicon/about-the-rosetta-translation-environment
  - macOS 27 release notes: Intel software is incompatible with macOS 28 "excluding legacy games". [F] https://developer.apple.com/documentation/macos-release-notes/macos-27-release-notes
  - Asked about Wine/CrossOver, an Apple engineer would only say x86_64 *macOS* games keep working. [F] https://developer.apple.com/forums/thread/830692
  - [I] Treat Wine-on-Rosetta as unsupported from macOS 28 (~autumn 2027).
- **Native arm64 Wine + FEX (CodeWeavers, Aug 2026):**
  - Needs macOS 26.5+ and the **restricted** entitlement `com.apple.developer.cross-architecture-support`.
  - The merge requests are not yet upstream. [F] wine-devel thread CKG5CEN2…
- **Wrapper landscape:** CrossOver 27 runs 32-bit apps in WoW64 bottles; Whisky is archived (May 2025); Kegworks → Sikarugir (active); Wineskin archived. [F]
- **AI decompilation at scale:** an Oct 2026 write-up decompiled a Windows FPS in ~3 months [F] https://momo5502.com/posts/2026-10-09-game-decompilation/:
  - 15+ agents with ida-mcp, 600–700B tokens;
  - 99% of functions present, 83% byte-exact;
  - **it had a PDB**, and agents cheated until verification was mechanical.

## Tools
- **REA** (https://github.com/morluto/rea, MIT, TypeScript):
  - An MCP server + CLI so an agent can drive Hopper, Ghidra 12.1 or IDA (pseudocode, xrefs, strings, types). It is not a decompiler or porter itself.
  - Ghidra on Apple Silicon needs an arm64 JDK 21. Its Ghidra DBs are ephemeral, and only names/comments are editable.
  - The GitHub API showed ~225k stars, which is implausibly high; weigh it cautiously.
  - Showcase: DX-Ball (Win32) functions rebuilt and byte-matched with VC4.0.
- **Ghidra 12.1.4:** native arm64; RTTI analyzer + `RecoverClassesFromRTTIScript`. OOAnalyzer has moved to CERT Kaiju.
- **IDA:** IDA Free (arm64, cloud x86 decompiler, non-commercial); IDA 9 + official ida-mcp.
- **Matching decompilation:**
  - decomp.me has MSVC 6.0 SP0–SP5 (via wibo).
  - objdiff (arm64).
  - reccmp (AGPL; isledecomp).
  - ghidra-delinker-extension (Apache-2).
  - wibo's macOS build is x86_64, so it is Rosetta-bound.

## Strategy precedents
**Static recompilation of 32-bit Windows x86 (all Sept–Oct 2026, young):**
- **recomp-kit** (https://github.com/veritr1x/recomp-kit, MIT, ~170k LOC, 1 maintainer)
  - Pipeline: Python translator from Ghidra listings → C, plus a C runtime with x86 semantics and an explicit x87 precision model (never host `long double`).
  - Runtime pieces: interpreter (`runtime/interp.cpp`), PE loader, scheduler, kernel32/user32/gdi32 (TrueType text), comctl32.
  - Indirect calls: a "trap" mode records unresolved indirect targets; "report" mode collects them in bulk.
  - `dx/`: ddraw (some DD7), D3D2/3, a D3D9 shader generator on Metal/Vulkan/WebGPU, dinput, dsound, qmixer, waveout, Bink/AVI via FFmpeg 7.1.1 dylibs (LGPL notices handled).
  - `mss32` is only a stub (2 AIL functions). No D3D7 device, no DirectMusic.
  - Targets macOS, iOS, Android, Linux, Windows and web.
  - Game-repo pattern: generated code is never tracked; `check_repo.py` and `check_game_literals.py` enforce it.
  - First game: Populous: The Beginning (https://github.com/veritr1x/populous-recomp).
- **xi-on-anything** (https://github.com/rubymatrix/xi-on-anything, MIT)
  - Translates Final Fantasy XI's 32-bit MSVC DLLs to C *on the user's machine*; runs natively on arm64 macOS.
  - Ghidra discovery + manual verdicts; D3D8 on Metal.
  - Patched code becomes "translated variants".
  - Optional NEON kernels replace hot x87 code.
  - Python translator (`recomp/x86c.py`).
- **xboxrecomp** (Xbox x86 → C): the author calls indirect-call dispatch tables the hardest part of bring-up.
- **Console analogues:** N64Recomp, XenonRecomp.
- **retrowin32** (x86 emulator + Win32 HLE) was abandoned in favour of a static translator ("Theseus"). https://neugierig.org/software/blog/2026/04/theseus.html

**Decompilation precedents:**
- **re3/reVC:** in-process hook DLL (2018), standalone 2020; DMCA'd by Take-Two.
- **OpenRCT2:** imported into `rct2.exe`; EXE-free by ~2017–18.
- **OpenJKDF2:** Hex-Rays + hooks; now native macOS arm64.
- **isledecomp:** LEGO Island, byte-matching MSVC 4.2 since Apr 2023.
- **isle-portable:**
  - **miniwin**: a `windows.h`/`ddraw.h`/`d3d.h`/`d3drm.h`/`dinput.h`-compatible shim on SDL3.
  - D3DRM reimplemented with SDL_GPU/GL/GLES/D3D9/software backends.
  - miniaudio, libsmacker; LGPL-3; macOS arm64.

## Emulation options rejected
- BoxedWine (GPL-2, macOS ARM, Mac OpenGL framebuffer TODO).
- box64/box32 and FEX: Linux-only (FEX-darwin is a 1-star community port).
- [I] A custom Unicorn + Win32 layer is dominated by static recompilation.

## Wine-path graphics notes
- D3DMetal (GPTK 4) is DX11/12 only.
- D3D7 routes on Mac:
  - wined3d (GL 4.1 / Vulkan);
  - **D7VK** (D3D7 → DXVK D3D9 backend; zlib; feature-frozen) https://github.com/WinterSnowfall/d7vk;
  - dgVoodoo2 → DXMT (D3D11 → Metal; unverified);
  - KosmicKrisp (Vulkan 1.3 on Metal, in Mesa).

## Replacement libraries
- **Platform:** SDL3 (zlib) + SDL_GPU/Metal.
- **Video:** FFmpeg `libavcodec/bink.c` (LGPL-2.1+).
- **Miles:** no open reimplementation exists; build a facade over our own mixer.
- **DirectMusic:** GothicKit/dmusic (MIT, C11, active Mar 2026, incomplete).
- **DirectPlay:** only Wine's dplayx (EE uses its own TCP plugin + WON, so it is not needed).

## Cross-cutting risks
- **Determinism:**
  - Native C&C Generals ports report multiplayer broken by float determinism.
  - EE2 under Wine had LAN desyncs (Wine bug 18840).
  - Faithful x87 modelling is required for cross-play.
- **Legal:** re3/reVC DMCA and lawsuit; mirrors blocked again Apr 2025. Mitigation: generate on the user's machine and require the user's own copy.
