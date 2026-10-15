# Phase 4: audio and video → part of M3 (single-player parity)

Started 2026-10-15, in parallel with Phase 3. Context:
- [PLAN.md](PLAN.md) §5.4;
- [audio.md](reference/audio.md), the spec, including the measured traces from legion;
- [graphics.md](reference/graphics.md) for Bink presentation;
- the protocol in [phase2.md](phase2.md).

## Done when
- **Music:** EE's 8 DirectMusic tracks play through the HLE (`SEConductor` → `IDirectMusic*`) with the right track rotation, intensity (constant 1 in the GOG build, but implemented properly), fades, volume and reverb.
- **Sound effects:** Miles plays all game SFX (8-bit mono 22 kHz samples, 3 MP3s) with volume, pan and looping.
- **Movies:** the Bink intro movies play with sound.
- **Checks:** levels and timing are compared with Windows loopback recordings (audio.md §11–§12).

## Workstreams
| ID | Owns | Where |
|---|---|---|
| P4-A dmusic | `ole32` `CoCreateInstance` for the DirectMusic CLSIDs plus the ~40-method `IDirectMusic*` surface (audio.md §2.9). A **pinned GothicKit/dmusic fork** with our patches: bank select, master groove, segment repeats, reverb. GM bank via macOS `gs_instruments.dls` at runtime, falling back to the kit's soundfont. Mixer output through the kit's audio host. | kit `feat/dmusic` |
| P4-B miles-dsound | `mss32` (the 17 AIL functions missing of EE's 34), the shared stub DirectSound, Bink audio, MP3 decode caching | kit `feat/miles` |
| P4-C sim-parity | The single-player checksum-stream oracle for M3: the game's OOS checksum (EXE 0x545ebf) after every physics tick of a fixed-seed AI skirmish, on the Mac (a mod plugin) and on Windows (Frida, legion 0027/0033/0034), and the first comparison ([simparity.md](reference/simparity.md)) | `phase4/simparity`, kit `feat/simparity` |

## Status
| ID | State | Result |
|---|---|---|
| P4-A dmusic | **merged** (`ee` e93e875) | GothicKit/dmusic pinned (`b73b270`) with 10 upstreamable patches: bank select, master groove, SetRepeats, render-thread locking, segment end, partial downloads, 16-byte fmt, SF2 GM, unnamed DLS waves, **a sample-exact clock** (upstream ran 2.5% fast at 100 BPM). All §2.9 methods in DX6.1/7 vtable order; Waves reverb approximation; −3 dB trim; GM bank from macOS `gs_instruments.dls`. All 8 EE tracks play at 540–1160× realtime; Pawns vs the legion loopback is +0.75 dB mean. The strict boot passes with real DirectMusic. To do: reverb calibration against the clean recordings, a limiter (4 tracks clip at 100% music volume). |
| P4-B miles-dsound | **merged** (`ee` 40bb320) | **Miles:** all 34 EE AIL imports, the rules derived from Miles 6.1a behaviour, MP3 decode cache, end-of-sample callbacks, linear balance pan law (fixes the kit's −51 dB bug). **DirectSound:** one shared device across Miles, DirectMusic and Bink. **Bink audio** in sync within 11–23 ms. **Data:** 758 WAVs + 3 MP3s from `data.ssa` decode with 0 failures. **In a test build:** strict boot to the menu with the intro audio, the menu music (`EEIntro.mp3` at 62%) and click sounds. To do: an SFX level/pan comparison with a Windows recording. |
| P4-C sim-parity | **branch** `phase4/simparity` (kit `feat/simparity` 8cf74a7) | **Mac = Windows for 8022 PTs** (5 game minutes, 129 units): checksum, world RNG, every object, the game's own 10,643-line OOS dump. Each platform reproduces itself (Mac 15,787 PTs through a pause and a click; Windows 24,183). Needed: the PT rate pinned (Min = Max PTs per Second; otherwise the step size follows measured tick time), the seed forced at `EEMStartLoading` (it also seeds the world RNG), the wall-clock statistics report skipped, and lockstep or no civilization timer, because a message or pause lands in whichever CT the server's thread sends next. Fixed on the way: the translator's hook flags were the runtime's weak one-byte fallback, which corrupted host memory (kit); `ee_boot --user` used the developer's registry. Open: where the civilization screen's pause lands (UI timing) (simparity.md §7.4, §8). |
