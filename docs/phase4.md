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
