# Audio and video: DirectMusic, Miles, sound data, Bink

Status: Phase 1 inventory, 2026-10-14 (agent `audio`).
Inputs:
- GOG modules (PLAN §2.4);
- spike Ghidra exports of the engine and EXE;
- a new Ghidra project for `Miles Sound System Mixer.dll` and `DX7HRDisplay.dll`;
- GothicKit/dmusic `b73b270`;
- the kit at `eb32cc4`.

Scratch work (builds, renders, scripts) is in `analysis/audio/` (git-ignored). §10 lists how to reproduce it.

Labels: **[static]** = read from code or data. **[measured]** = run on this Mac (or on `legion`, where stated). **[inferred]** = reasoning, to be confirmed.

---

## 0. Summary

**Architecture** [static]:
- Music is DirectMusic, driven entirely by the engine's `SEConductor`/`SEScore`, using the DX6/7 API (`IDirectMusicPerformance`, not `…8`).
- The EXE's `EEMusicManager` thread maps game state to an **intensity of 0–3**. This becomes DirectMusic's **master groove level**, followed by an `AutoTransition` at the next measure.
- Sound effects run through `SESoundManager` → `SEMixer` → the Miles plugin (`SPIM*`) → 34 `AIL_*` calls, all 2D.
- Bink runs in `DX7HRDisplay`.
- One `IDirectSound*` (Miles') is shared by Miles, `IDirectMusicPerformance::Init` and `BinkSetSoundSystem`.

**DirectMusic surface** [static]:
- 9 interfaces and about 40 methods, listed in §2.9.
- Nothing uses notifications, motifs, chord maps or tempo changes.
- `AutoTransition` is called only with `GROOVE` + `MEASURE`.

**dmusic test** [measured]:
- All 8 segments load and render with style, band and DLS resolved, at 550–1300× realtime.
- Upstream needs two classes of fix before EE sounds right:
  1. **Bank-select bug.** dmusic maps DirectMusic patches to TinySoundFont banks wrongly (it ignores the bank MSB and the drum flag). As a result, **89 of 103 patched band channels across the 8 tracks play the wrong instrument**, mostly the DLS's first preset. A 5-line fix resolves 102 of 103.
  2. **Missing API.** There is no master-groove level (EE's whole intensity mechanism) and no `SetRepeats`. A 30-line patch adds both. With it, intensity 1–3 switches pattern families exactly as the styles' groove ranges (1–4) intend.

**Big content dependency** [static + measured]:
- 4 of 8 tracks need instruments from **Windows' system GM/GS bank** (`gm.dls`, Roland), which the game doesn't ship:
  - Balance: drums, harp, choir;
  - Honor: drums;
  - Pawns: harp, strings, vibraphone, oboe;
  - Shadow2: drums at the top intensity.
- Upstream dmusic **fails to load Pawns at all** without it.
- macOS ships a Roland GS DLS (`gs_instruments.dls`, 8-bit). Pointing dmusic's resolver at it works today; the kit's GeneralUser GS SF2 is the alternative.

**Sound effects** [measured]:
- All 758 WAVs inside `data.ssa` are **8-bit mono 22,050 Hz PCM**.
- 3 MP3s (44.1 kHz stereo, 128 kbps) are menu/UI music played through Miles.
- No ADPCM, no streaming, no 3D.
- The kit's Miles shim has 17 of the 34 imports; the missing 17 are small (§4.2).

**Bink** [measured]:
- All three movies (Sierra, SSSI, intro) are present: Bink 1 rev `i`, 640×480, 15 or 30 fps, 44.1 kHz stereo.
- FFmpeg 9.0.1 decodes every frame with no errors.
- The kit covers all 9 `binkw32` imports.

**Effort** (§7):

| Item | Estimate | ADR-001 |
|---|---|---|
| DirectMusic | **3–4 weeks** total, of which 1.5–2 weeks reach "plays right" | gap 11: 3–4 weeks, confirmed |
| Miles | 0.5–1 week | gap 10, confirmed |
| Bink | ≤ 0.5 week, verification only | |

---

## 1. Audio architecture

```
Empire Earth.exe
 ├─ EEMusicManager (EEMusicManager.cpp; thread "MusicManager", TSThread EXE+0x12711d)
 │    intensity 0..3, track shuffle, fades ──► SEConductor::{Play,TransitionTo,SetIntensity,SetVolume,
 │                                                        SetReverbStyle,Stop,GetScore,SetRepeatCount,LoadScore,UnloadScore}
 ├─ sound init FUN_00538360 ──► SEMixer::GetMixer/UseMixer, SEConductor::{SetChannelGroupCount(8),
 │                               SetConductorState, SetReverb, SetVolume, SetIntensity(0), SetMusicFolder}
 └─ ~160 call sites ──► SESoundManager::{Play,Stop,Load,SetMasterVolume,...}
Low-Level Engine.dll
 ├─ SEConductor / SEScore ──► ole32 CoCreateInstance ──► DirectMusic (dmime/dmusic/dmloader/dmcompos/dmstyle/dmband/dmsynth)
 │     thread SEConductor::EventHandler (notification wait)
 ├─ SEMixer (SEConductor at offset 0; the SPIM* exports are wrapped 1:1)
 └─ SESoundManager (thread Scavenger: SEMixer::IsChannelBusyPlaying → AIL_sample_status ≈3/s)
Miles Sound System Mixer.dll (SPIM*) ──► Mss32.dll 6.1a (34 AIL_*) ──► DirectSound
DX7HRDisplay.dll  DX7Movie::PlayBink (own TSThread) ──► binkw32.dll 1.0x (9 calls)
                  BinkSetSoundSystem(BinkOpenDirectSound, SEMixer::sActiveMixer->GetDirectSoundInterface())
```

The shared `IDirectSound*` [static]. Its route is `AIL_get_DirectSound_info` → `SPIMGetDirectSoundInterface` → `SEMixer` vtable `+0xa8`. Three consumers use it:
- the Miles plugin: `AddRef`, `Release`, and `GetCaps` (`DSCAPS.dwFreeHwMixingAllBuffers` sets the channel count);
- `IDirectMusicPerformance::Init(&dm, pDS, NULL)`;
- `BinkSetSoundSystem`.

The HLE needs one stub `IDirectSound` (`QueryInterface`/`AddRef`/`Release`/`GetCaps`). The kit's `dx/dsound.cpp` can probably provide it.

---

## 2. DirectMusic usage

### 2.1 Objects and GUIDs [static]
`CoCreateInstance(clsid, NULL, CLSCTX_INPROC (3), iid, &p)` is called only by `SEConductor::InitializeConductor` (`engine+0x582e6`). The EXE imports only `CoInitialize`/`CoUninitialize`.

| Object | CLSID | IID requested |
|---|---|---|
| Loader | `CLSID_DirectMusicLoader` `{D2AC2892-…}` | `IID_IDirectMusicLoader` (DX7) `{2FFAACA2-5DCA-11D2-AFA6-00AA0024D8B6}` |
| Composer | `CLSID_DirectMusicComposer` `{D2AC2890-…}` | `IID_IDirectMusicComposer` `{D2AC28BF-…}` |
| Performance | `CLSID_DirectMusicPerformance` `{D2AC2881-…}` | `IID_IDirectMusicPerformance` (DX6.1) `{07D43D03-6523-11D2-871D-00600893B1BD}` |

All `{D2AC28xx-B39B-11D1-8704-00600893B1BD}`. Other GUIDs referenced:

| Kind | GUIDs |
|---|---|
| Classes and interfaces | `CLSID_DirectMusicSegment`, `IID_IDirectMusicSegment` `{F96029A2-…}`, `IID_IDirectMusicSegmentState` `{A3AFDCC7-…}`, `IID_IKsControl` `{28F54685-…}` |
| Parameters | `GUID_IDirectMusicStyle`, `GUID_Download`, `GUID_Unload`, `GUID_PerfAutoDownload`, `GUID_PerfMasterGrooveLevel`, `GUID_DirectMusicAllTypes` |
| Notifications | `GUID_NOTIFICATION_SEGMENT`, `GUID_NOTIFICATION_PERFORMANCE` |
| Synth properties | `GUID_DMUS_PROP_Volume`, `GUID_DMUS_PROP_Effects`, `GUID_DMUS_PROP_WavesReverb` |

### 2.2 Initialisation sequence (`InitializeConductor` + `InitializePorts`) [static]
1. `CoInitialize(NULL)` is called in the `SEConductor` constructor. It creates events and a critical section. Defaults: volume 1.0, reverb on, intensity 0, channel groups 1000.
2. `CoCreateInstance` ×3, in the order loader, composer, performance.
3. `perf->Init(&pDM, pDS /*Miles' DirectSound*/, NULL)`.
4. `pDM->GetDefaultPort(&guid)`. Then `pDM->CreatePort(guid, DMUS_PORTPARAMS8{dwSize=0x24, dwValidParams=CHANNELGROUPS, dwChannelGroups=1}, &port, NULL)`.
5. `port->GetCaps(DMUS_PORTCAPS{dwSize=0x134})`. The port must have `dwClass == DMUS_PC_OUTPUTCLASS` and `dwFlags & DMUS_PC_DLS`. Otherwise the engine releases it, walks `pDM->EnumPort(i, &caps)` for the first output-class DLS port, and `CreatePort`s that.
6. The group count is clamped to `caps.dwMaxChannelGroups`. The EXE asks for **8** (`SetChannelGroupCount(8)` before init). Then `port->SetNumChannelGroups(8)`, `port->Activate(TRUE)` and `perf->AddPort(port)`.
7. For each group *i* in 0..7, `perf->AssignPChannelBlock(i, port, i+1)`. That gives 128 PChannels; EE content uses PChannels 0–16 only.
8. `pDM->Activate(TRUE)`.
9. `perf->SetNotificationHandle(hEvent, 0)`.
10. `perf->AddNotificationType(GUID_NOTIFICATION_PERFORMANCE)` and `perf->AddNotificationType(GUID_NOTIFICATION_SEGMENT)`.
11. `perf->SetGlobalParam(GUID_PerfAutoDownload, &FALSE, 4)`. **Auto-download is off**, so the engine downloads bands explicitly.
12. `loader->EnableCache(GUID_DirectMusicAllTypes, TRUE)`.
13. `SetVolume`, `SetReverb` and `SetIntensity(…, true)` re-apply stored state.
14. If a music folder is set, `SetDirectMusicFolder`.
15. Every score queued while uninitialised is `LoadScore`d.
16. The `EventHandler` thread is created.

Teardown (`UninitializeConductor`, also triggered by **Music Enabled** off → `SetConductorState(0)`):
- signal and join `EventHandler`;
- `SetNotificationHandle(0)`;
- `perf->Stop(NULL, NULL, 0, 0)`;
- delete scores;
- `perf->CloseDown()`;
- `Release` everything.

### 2.3 Loader: search path, caching, objects [static]
**Search path.** `SetMusicFolder(path)` → `loader->SetSearchDirectory(GUID_DirectMusicAllTypes, wpath, FALSE)`.
- The EXE passes `<game dir>\Data\Music\`, built from `gStartupDirectory` + `"Data\Music\"`.
- If that folder is missing, it falls back to a second base directory (probably the install source from the registry) [inferred].

**Loading.** `LoadScore(name)` → `loader->GetObject(DMUS_OBJECTDESC{dwSize=0x350, dwValidData=DMUS_OBJ_CLASS|DMUS_OBJ_FILENAME, guidClass=CLSID_DirectMusicSegment, wszFileName=name}, IID_IDirectMusicSegment, &seg)`.
- `DMUS_S_PARTIALLOAD` is tolerated.
- Then `seg->SetParam(GUID_Download, 0xFFFFFFFF, 0, 0, perf)`, which downloads the bands' DLS instruments to the port.
- `SEScore`s are refcounted by file name.
- `UnloadScore` refuses to unload the playing score: it calls `GetCurrentlyPlayingScore` = `perf->GetTime` + `GetSegmentState` + `segstate->GetSegment`.

**Caching.** Cache on, for all types. Style, band and DLS references are resolved by the loader from the segment's `DMRF` references. Each reference carries the GUID, the name and a **file name**: for example `Balance.sgt` → `Balance.sty`, `Balance.dls`. The file names differ from the segment names in places (`Shadow2.sgt` → `Shadows.sty`/`Shadows.dls`, `Pawns.sgt` → `Pawn-Style.sty`/`Pawns1.dls`, `Rivers DLS.dls`, `Time DLS.dls`), so resolve by the reference's file name, case-insensitively.

**Secondary scores.** The `SEScore` constructor calls `seg->GetParam(GUID_IDirectMusicStyle, …, &style)`, then `style->EnumMotif(i, name)` / `GetMotif(name, &seg)` to wrap motifs as secondary scores (played with `DMUS_SEGF_SECONDARY`). **No EE style contains motifs** [measured: no pattern has embellishment `0x10`], so `EnumMotif` returns `S_FALSE` at once.

**Unloading.** The `SEScore` destructor calls `seg->SetParam(GUID_Unload, …, perf)`, then `Release`.

### 2.4 Port and synth configuration [static]
- **Port.** The default port, which on real Windows is the Microsoft Software Synthesizer (DLS1), with 8 channel groups. No `dwVoices` or `dwSampleRate` is set, so the defaults apply: **32 voices and 22,050 Hz** [inferred from DX7 defaults; verify on `legion`]. The DirectSound primary that Miles opens is also 22,050 Hz, 16-bit stereo.
- **Volume (`SetVolume(float v)`).**
  - The value written is `mB = v ≤ 0.01 ? −20000 : round(1000·ln v)`, in hundredths of a dB.
  - It is applied via `port->QueryInterface(IID_IKsControl)->KsProperty({GUID_DMUS_PROP_Volume, 0, KSPROPERTY_TYPE_SET}, &mB, 4)`.
  - The law is slightly steeper than amplitude: the linear gain is `v^1.151`. Registry **Music Volume** = 62 % → −478 mB (−4.8 dB).
- **Reverb on/off (`SetReverb(1|0)`).** `KsProperty GET` then `SET` of `GUID_DMUS_PROP_Effects`, toggling `DMUS_EFFECT_REVERB`. This is the registry option **Reverb Enabled** (1 by default).
- **Reverb parameters (`SetReverbStyle(a,b,c,d)`).**
  - `KsProperty SET GUID_DMUS_PROP_WavesReverb` with `DMUS_WAVES_REVERB_PARAMS{fInGain=a, fReverbMix=c, fReverbTime=d, fHighFreqRTRatio=b}`. **Note the argument order.**
  - The values come per track from `db\dbmusic.dat`. All tracks use: in-gain 0 dB, mix −7…−9 dB, time 1.6–2.8 s, HF ratio 0.15–0.45.

### 2.5 What each `SEConductor` call does [static]

| Engine API (EXE imports it) | DirectMusic calls |
|---|---|
| `Play(id)` | `perf->PlaySegment(seg, DMUS_SEGF_DEFAULT (0x4000) [|SECONDARY 0x80 for motif scores], 0, NULL)` |
| `Queue(id)` (not imported) | `PlaySegment(seg, DEFAULT|QUEUE (0x4100))` |
| `Stop()` | `perf->Stop(NULL, NULL, 0, 0)` |
| `TransitionTo(id, cmd, flags)` | `composer->AutoTransition(perf, seg|NULL, wCommand, dwFlags, NULL, NULL, NULL, NULL)`. The command maps 1→FILL, 2→BREAK, 3→INTRO, 4→END, 5→ENDANDINTRO, else GROOVE. The flags map 1→`DMUS_COMPOSEF_MODULATE`, 2→`LONG`, else `MEASURE`. **The EXE only ever calls `TransitionTo(current, 0, 0)` = GROOVE at the next measure.** |
| `SetIntensity(level, force)` | `perf->SetGlobalParam(GUID_PerfMasterGrooveLevel, &(char)level, 1)`. The master groove is **added** to every segment groove level. |
| `SetVolume` / `SetReverb` / `SetReverbStyle` | `IKsControl::KsProperty` on the port (§2.4) |
| `SEScore::Get/SetRepeatCount` | `seg->GetRepeats` / `SetRepeats`. The EXE sets 100000. |
| `LoadScore` / `UnloadScore` / `SetMusicFolder` / `SetConductorState` / `SetChannelGroupCount` | §2.2–2.3 |

### 2.6 Notifications [static]
`EventHandler` waits on the notification event, then `perf->GetNotificationPMsg` → dispatches to `SetNotificationCallback` slots → `perf->FreePMsg`. It handles:
- PERFORMANCE: MUSICSTARTED and MUSICSTOPPED;
- SEGMENT: START, END, ALMOSTEND and ABORT, via `segstate->GetSegment`.

**No code in the EXE or the engine registers a callback**, so the HLE can accept `SetNotificationHandle`/`AddNotificationType` and never signal anything. `GetNotificationPMsg` returns `S_FALSE`.

### 2.7 How the game drives music: `EEMusicManager` (EXE) [static]
- **Thread.** `EEMusicManager::MusicManagerMain` (`EXE+0x5270bf`; legion's "music intensity" thread `EXE+0x12711d` is its `UMSetFloatingPointPrecision` return site).
  - It waits on 5 events (shutdown, pause, intensity, start, stop), with a timeout equal to the current track's remaining time.
  - It is created with the sound system (`gMusicManager`), with an initial `SetIntensity(0, true)`.
- **When music plays.**
  - **Start (event 3)** fires at *"Game initialized"* / *"Game loaded"* (`FUN_0052e7c8`, which also requests intensity 1), and when Music Enabled is switched back on.
  - **Stop (event 4)** fires at game end.
  - **Pause (event 1)** fires elsewhere.
  - In-game DirectMusic only; the main menu does not use it [inferred]. The menu and other UI forms play **`EEIntro.mp3` / `Arthurian.mp3` through Miles** (`db\dbuiforms.dat`).
- **Track selection.**
  - `db\dbmusic.dat` (in `data.ssa`) has 9 records of 196 bytes: a null record plus Pawns, Shadow2, Balance, Fallen, Enmity, Time, Honor, Rivers.
  - Each record holds the segment base name at `+0x6c`, 4 reverb floats at `+0xa0`, and 14 per-epoch enable bytes at `+0xb5`. **All 8 tracks are enabled for all 14 epochs.**
  - The picker is a shuffle bag per epoch, using `URandomIntegers` at `0x8d1d80` (its own instance).
  - A moddable override is `Data\Music\New Music.txt`: one segment name + 4 reverb floats, enabled for all epochs.
- **Playing a track.**
  - `LoadScore(name)`, `SetVolume(0)`, `GetScore(id)->SetRepeatCount(100000)`, `SetIntensity(current, false)`, `SetReverbStyle(4 floats)`, `Play(id)`, then a fade in to the Music Volume setting (+0.1·k per `Sleep(n)`; *k* and *n* are runtime globals).
  - Track duration = `300000 + URandom()·120001` ms, i.e. **5–7 min**, using the manager's own `URandom` at `this+4`. Then fade out, `Stop`, unload, and the next track.
  - **Music RNGs are separate instances, so music cannot touch simulation state** [static; matters for lockstep].
- **Intensity.**
  - The world-update function `FUN_00503b83` computes a ratio and maps it to **1, 2 or 3** against thresholds 0.5 / 0.65 (the pair depends on a mode field). It does this at most every 250 ms, and only if Music Enabled.
  - `FUN_0052723c` applies **increases immediately and decreases after a ≈5 s hold**.
  - On an applied change while playing: `SetIntensity(level, false)` + `TransitionTo(current, 0, 0)`. The current segment restarts on the next measure, with a 1-bar groove transition.
  - So **in-game intensity is 1–3 (groove 2–4)**. Groove 1, the long through-composed "intro" patterns, is reached only at intensity 0, which the game sets only before playback starts [inferred].
- **Other volume calls.** Movies and pause save the music volume, set it to 0 and restore it later (`FUN_00540bed`/`cab`). The options screen re-applies volume, reverb and enable.
- **Registry** (`HKCU\Software\SSSI\Empire Earth`): `Music Enabled`, `Music Volume` (percent), `Reverb Enabled`, `Sound Volume`, `Max Number Of Sounds (percent)`, `Ambient Sounds`.

### 2.8 Content survey of `Data/Music` [measured, own RIFF reader]
Every `.sgt` has the same 6 tracks:
- chord (`cord`), command (a single `groove 1` at bar 0), tempo (constant), style (one `.sty`), time signature, band (one band, DLS references);
- `segh` repeats = 0, no loop points.

DX6-format styles (`ptnh` 10 bytes, `prfc` without PChannel, so PChannel = logical part ID). Patterns are counted with embellishment NORMAL; "emb." counts the FILL/BREAK/INTRO/END patterns, which EE never requests.

| Segment | Time sig. | BPM | Length | Patterns at groove 1 / 2 / 3 / 4 | Emb. | GM/GS instruments with notes (groove) |
|---|---|---|---|---|---|---|
| Balance | 4/4 | 130.5 | 390 s | 1 / 7 / 27 / 19 | 19 | drums (1–4); harp, choir (1–3) |
| Enmity | 4/4 | 120 | 200 s | 2 / 20 / 17 / 10 | 15 | – |
| Fallen | 4/4 | 121.3 | 493 s | 1 / 28 / 28 / 27 | 8 | – |
| Honor | 3/4 | 104 | 346 s | 2 / 18 / 32 / 10 | 11 | drums (1, 2, 4) |
| Pawns | 4/4 | 100 | 391 s | 1 / 13 / 22 / 37 | 14 | harp, oboe, vibraphone (1–4); strings (1–3); strings and vibraphone are GS bank MSB 8 |
| Rivers | 4/4 | 75 | 160 s | 1 / 20 / 16 / 19 | 17 | – |
| Shadow2 | 4/4 | 120 | 452 s | 1 / 14 / 14 / 14 | 18 | drums (4 only) |
| Time | 4/4 | 120 | 200 s | 1 / 22 / 24 / 20 | 18 | – |

- **Groove levels 1–4 exist in every style, matching base groove 1 + intensity 0–3** [static].
- **DLS banks:**
  - DLS Level 1 only (`art1`, no `art2`/`cdl`);
  - 9–13 instruments each;
  - 16-bit **mono PCM** waves at 44.1, 22.05 and 48 kHz (no ADPCM);
  - 2 banks contain a drum kit;
  - instruments use bank MSB 0, 1, 6 or 8.
- **Unknown or ignored content:**
  - `UART`/`USBJ` info chunks;
  - `tims` track data;
  - MIDI channel-pressure curves (type 5): 2,840 events in 180 s of Balance. DLS1 has no channel-pressure modulator, so they are probably inaudible on Windows too [inferred].

### 2.9 Checklist: the `IDirectMusic*` surface to emulate [static]
Everything else may return `E_NOTIMPL`.

**IDirectMusicLoader (DX7)**
- [ ] `GetObject`: segment by file name; resolve the references inside.
- [ ] `SetSearchDirectory`
- [ ] `EnableCache`

**IDirectMusicPerformance (DX6.1)**
- [ ] `Init(ppDM, pDS, hWnd)`: returns an `IDirectMusic`.
- [ ] `PlaySegment(seg, flags {DEFAULT, SECONDARY, QUEUE}, 0, NULL)`
- [ ] `Stop(NULL, NULL, 0, 0)`
- [ ] `GetSegmentState(&ss, mt)`
- [ ] `GetTime(NULL, &mt)`
- [ ] `SetNotificationHandle`, `AddNotificationType`: accept and never signal.
- [ ] `GetNotificationPMsg` → `S_FALSE`; `FreePMsg`.
- [ ] `AddPort`, `AssignPChannelBlock`
- [ ] `SetGlobalParam`: `GUID_PerfAutoDownload` (BOOL) and **`GUID_PerfMasterGrooveLevel` (char)**.
- [ ] `CloseDown`

**IDirectMusic**
- [ ] `GetDefaultPort`
- [ ] `EnumPort`: may stop at 0.
- [ ] `CreatePort(PORTPARAMS8)`
- [ ] `Activate`

**IDirectMusicPort**
- [ ] `GetCaps`: OUTPUTCLASS, `DMUS_PC_DLS`, `dwMaxChannelGroups ≥ 8`, `dwSize` 0x134.
- [ ] `SetNumChannelGroups`, `Activate`
- [ ] `QueryInterface(IID_IKsControl)`

**IKsControl**
- [ ] `KsProperty`: `GUID_DMUS_PROP_Volume` SET (LONG mB); `GUID_DMUS_PROP_Effects` GET/SET (bit 0 = reverb); `GUID_DMUS_PROP_WavesReverb` SET (4 floats).

**IDirectMusicSegment**
- [ ] `GetRepeats`, `SetRepeats`
- [ ] `GetParam(GUID_IDirectMusicStyle)` → `IDirectMusicStyle`
- [ ] `SetParam(GUID_Download | GUID_Unload, perf)`: ignore the pointer argument for Unload (the engine passes `sActiveMixer+0x58`).
- [ ] `Release`

**IDirectMusicSegmentState**
- [ ] `GetSegment`

**IDirectMusicStyle**
- [ ] `EnumMotif` → `S_FALSE`
- [ ] (`GetMotif`): not reached with EE content.

**IDirectMusicComposer**
- [ ] `AutoTransition(perf, seg|NULL, GROOVE…ENDANDINTRO, MEASURE|MODULATE|LONG, NULL…)`. EE uses only GROOVE + MEASURE.

**IUnknown and ole32**
- [ ] `AddRef`/`Release`/`QueryInterface` on all of the above.
- [ ] `CoCreateInstance` registration for the 3 CLSIDs. The kit has `com_register_class`.

Two ways to integrate:
- **(a) COM-level HLE.** The translated `SEConductor` runs unchanged against these objects, which are backed by dmusic. This is the recommended option; it is what PLAN §5.4 describes.
- **(b) Function-level override.** Replace the ~15 `SEConductor` exports the EXE imports with native code (kit `FN_<addr>`), avoiding COM plumbing altogether. Music has no sync relevance, so (b) is a cheap fallback if (a) stalls.

---

## 3. GothicKit/dmusic on EE's music

### 3.1 Setup [measured]
- **Library.** `github.com/GothicKit/dmusic` @ `b73b270` (2026-03-02, MIT-Modern-Variant; vendored TinySoundFont, MIT). Built as a static library with CMake + Apple clang on arm64. It built with no warnings and uses its own C11-threads shim.
- **Driver** (`tools/audio/ee_dmrender.c`, own code). It does what `SEConductor`/`EEMusicManager` do:
  1. load `<name>.sgt` with a case-insensitive resolver over `Data/Music` (`DmLoader_DOWNLOAD`);
  2. `SetRepeats(100000)`;
  3. master groove 0;
  4. play;
  5. at each scheduled time, master groove = intensity, then `playTransition(seg, GROOVE, MEASURE)`.

  It renders 44.1 kHz stereo s16 to WAV, logs dmusic messages by level, and an optional second resolver directory supplies `gm.dls`/`gs.dls`.
- **Local patch** (`third_party/patches/dmusic-ee-fixes.patch`, 80 lines):
  - `DmPerformance_setMasterGroove` (`effective = clamp(groove + master, 1, 100)` in pattern selection);
  - `DmSegment_setRepeats`;
  - the bank-select fix (§3.3 #1).
- **Renders.** 150 s at intensity 1 and 180 s with schedule `0:1, 30:2, 60:3, 90:2, 120:0, 150:1`, for all 8 tracks. WAVs are in `analysis/audio/renders/`.

### 3.2 Results [measured]
**Loading.**
- Upstream, no GM bank: 7 of 8 segments load, and **Pawns fails** (*"Automatic download of segment 'Pawns.sgt' failed"*: `gs.dls` not found). The other 7 silently drop their GM instruments.
- With macOS's `gs_instruments.dls` served as `gm.dls`/`gs.dls`: 8 of 8 load.
- Styles, bands and the game DLS banks resolve in all 8, by file name.
- Load time is 1–3 ms per segment.

**Warnings.** None are fatal:
- `tims`/`UART`/`USBJ`/`acid`/`strc` chunks only partly parsed: harmless.
- *"Curve type 5 not implemented (midi channel pressure)"*: harmless for DLS1 [inferred].
- 495 errors per load of Apple's bank: dmusic insists on `cbSize` after a 16-byte `fmt ` chunk (Apple writes `PCMWAVEFORMAT`). The waves still load, but this should be fixed upstream.

**Audio, fixed build with the GS bank, intensity 1, 150 s** (raw synth output, no volume applied):

| Track | RMS dBFS | Clipped samples | Silent 100 ms windows | Notes |
|---|---|---|---|---|
| Balance | −10.9 | 31,185 | 0 % | |
| Enmity | −14.0 | 3,173 | 0 % | |
| Fallen | −13.5 | 1,516 | 0 % | |
| Honor | −11.6 | 12,136 | 0 % | |
| Pawns | −14.1 | 141 | 2.6 % (max 1.0 s rest) | |
| Rivers | −13.9 | 475 | 0 % | |
| Shadow2 | −11.7 | 47,228 | 0 % | |
| Time | −11.7 | 23,449 | 0 % | |

- **Clipping.** Peaks hit full scale on every track. At EE's default music gain (62 % → 0.577 linear), clipping falls to 12–497 samples per 180 s. Gain staging plus a soft limiter is still needed, and Windows' 32-voice limit and synth headroom differ (§3.3).
- **Before the bank fix,** RMS was 0–9.5 dB lower. Balance, Enmity and Honor had periodic 2–3 s silences (10–22 % silent windows): parts were playing the DLS's first preset, often a short percussive sample.

**Intensity.** RMS by schedule phase (dB):

| Track | i1 | i2 | i3 | i2 | i0 | i1 |
|---|---|---|---|---|---|---|
| Balance | −10.6 | −9.2 | −9.4 | −10.6 | −13.7 | −11.8 |
| Enmity | −11.6 | −10.9 | −10.4 | −11.7 | −15.2 | −12.8 |
| Fallen | −14.3 | −14.5 | −14.6 | −14.1 | −17.5 | −15.5 |
| Honor | −12.0 | −13.0 | −9.3 | −10.7 | −12.8 | −12.0 |
| Pawns | −15.5 | −13.3 | −12.7 | −13.1 | −16.0 | −16.8 |
| Rivers | −14.0 | −10.0 | −10.5 | −10.8 | −15.5 | −13.7 |
| Shadow2 | −16.5 | −15.0 | −10.1 | −13.1 | −11.9 | −14.4 |
| Time | −12.7 | −9.3 | −8.6 | −9.4 | −14.5 | −12.4 |

- The pattern log shows each change switching to the pattern family of the new groove at the next bar. At intensity 0, a single 48–144-bar intro pattern plays.
- **dmusic's `playTransition(GROOVE, MEASURE)` is a direct stand-in for `AutoTransition(…, GROOVE, MEASURE)`**: a 1-bar groove pattern, then the segment restarts.

**CPU.** 550–1,300× realtime on one M4 Max core at 44.1 kHz, i.e. ≤ 0.2 % of a core.

### 3.3 Gaps found, by severity

| # | Gap | Effect on EE | Fix | Status |
|---|---|---|---|---|
| 1 | **Bank select.** `DmSynth_assignInstrumentChannels` passes TSF bank `= LSB` and ignores the MSB and the drum flag. The hydra stores bank `(MSB<<8)|LSB`, with drums in 128. | **89 of 103** patched band channels resolve to no preset and keep preset 0. GM drums map to the GM piano. | Bank `= drum ? 128 : (MSB<<8)|LSB` (5 lines). | fixed locally; upstream PR |
| 2 | No master groove level | No intensity at all | `setMasterGroove` (patch) | fixed locally; upstream PR |
| 3 | No `SetRepeats` | Segments stop after one pass (`segh` repeats = 0); EE sets 100000 | setter (patch) | fixed locally; upstream PR |
| 4 | System GM/GS bank (`gm.dls`) is not shipped | 4 tracks lose drums or melody; Pawns fails | Resolve to macOS `gs_instruments.dls`, or add an SF2 GM fallback (kit's GeneralUser GS). Also add GS variation fallback (MSB *n* → 0). | resolver works; SF2 path still to do |
| 5 | `Band.c` instrument check ignores bank MSB and drum flag; downloads fail hard when a referenced DLS is missing | Wrong log only after #1; hard failure (Pawns) | match the full bank; warn instead of failing | to do |
| 6 | No reverb (Waves reverb / `DMUS_EFFECT_REVERB`) | Dry sound. EE sets −7…−9 dB mix, 1.6–2.8 s per track. | Simple reverb in the HLE output stage (Freeverb-class, public domain), parameters mapped from `DMUS_WAVES_REVERB_PARAMS` | to do |
| 7 | Gain staging | Clips at full volume | headroom + limiter; calibrate against `legion` | to do |
| 8 | Variation choice ignores chord function (`variation_choices` = first *N* non-zero entries); `RANDOM_START`/`NO_REPEAT` approximated | Different but plausible variations | implement the DX7 rules | optional |
| 9 | Pattern choice is uniform random (DX7 also weighs pattern length and rhythm against the chord track) | Different sequencing, same style | optional | optional |
| 10 | Synth differences: unlimited voices vs 32; TSF envelopes vs the Microsoft DLS1 synth; 22.05 kHz on Windows | Timbre and level differences | A/B against `legion`; optionally render at 22.05 kHz | verify |
| 11 | `fmt ` `cbSize` strictness; `tims` not parsed; channel pressure ignored | Log noise only | small | upstream |

### 3.4 Gap to good playback, and fallbacks

**Gap to good playback.**
- **Correct notes, instruments and intensity** already work with the local patch plus the macOS GS bank. That is about 1–2 days of upstreamable changes.
- **"Sounds like Windows"** additionally needs reverb, gain staging, the GM fallback for non-macOS hosts and A/B tuning: about 1.5–2 weeks.
- The COM HLE layer (§2.9) and host-mixer integration add about 1–1.5 weeks.

**Fallbacks.**
1. **Kit TinySoundFont path.** The kit's TSF is a `midiOut` + SF2 General MIDI synth (Populous); it has no DLS, styles or segments. It is not a fallback by itself. dmusic already uses TSF, and the kit's GeneralUser GS SF2 is useful only as the **GM bank substitute** (gap #4).
2. **Offline rendering from the user's own files.** At first run, render each track at each intensity with dmusic, then crossfade by intensity at runtime. This is cheap (8 tracks × 3 grooves × 10 min ≈ 1 min CPU), but loses bar-accurate transitions and costs disk (about 2.4 GB as 44.1 kHz WAV, so FLAC or 22.05 kHz). Use it only if live rendering hits a showstopper; live dmusic is cheaper than this.
3. **No music.** Music is cosmetic and not sync-relevant, so it is acceptable for M2/M3 bring-up.

---

## 4. Miles (sound effects)

### 4.1 SPIM interface semantics [static, own Ghidra on `Miles Sound System Mixer.dll`]

| `SPIM*` export | Miles calls and semantics |
|---|---|
| `InitializeMixer(p)` | stores `HWND = *p` |
| `ActivateMixer` | 1. `AIL_set_redist_directory(gStartupDirectory+"redist\win32")`, `AIL_startup`. 2. `set_preference(15, 0)` and `(1, 64)` (MSS6 names `DIG_USE_WAVEOUT` and `DIG_MIXER_CHANNELS` [inferred]). 3. `AIL_open_digital_driver(22050, 16, 2, 1)`, `AIL_set_DirectSound_HWND`. 4. `allocate` → `get_DirectSound_info(&pDS)`, `AddRef`, `GetCaps`; **max channels = `dwFreeHwMixingAllBuffers`, or 16 if 0 or > 64**. 5. Release the probe; allocate a permanent probe sample; `CreateChannels(max)`. 6. Read prefs 34, 42 and 35 (DS fragment size and counts). |
| `CreateChannels(n)` / `DestroyChannels` | *n* ≤ max sample handles; `end_sample` + `release_sample_handle` |
| `LoadWaveData(file, sample)` | `FSMemoryFile` (from `.ssa`): copy into `AIL_mem_alloc_lock(n)`. Otherwise `AIL_file_size` / `AIL_file_read(path, 0)`. `SetLatency(500)` meanwhile. Then on the probe: `init_sample`, `set_named_sample_file(probe, name, buf, n, 0)` (**type chosen from the name: `.wav` PCM, `.mp3` via `Mp3dec.asi`**), `sample_granularity`, `sample_playback_rate`. The whole image stays in memory (refcounted). |
| `SetChannelSample(ch, s)` | `init_sample` + `set_named_sample_file`, then re-apply loop, pan, pitch and volume |
| `PlayChannel` / `StopChannel` / `StopAllChannels` | `start_sample` (unless `status == SMP_PLAYING`) / `end_sample` |
| `IsChannelBusyPlaying` | `sample_status == 4` → busy (5), else free (6) |
| `SetSampleVolume(v∈[0,1])` | `set_sample_volume(round(127·v))` |
| `SetSamplePan(p∈[−1,1])` | `set_sample_pan(clamp(round((p+1)·63.5), 0, 127))` |
| `SetSamplePitch(f)` | `set_sample_playback_rate(round(native_rate·f))` |
| `SetSampleLooping(b)` | `set_sample_loop_count(b ? 0 /*forever*/ : 1)` |
| `Get/SetSampleOffset` | `sample_position` / `set_sample_position` (bytes) |
| `GetSamplePlayBackTime(&total, &remaining)` | `sample_ms_position`; remaining = total − current |
| `SetVolume(v)` / `GetVolume` | `set_digital_master_volume(round(127·v))` |
| `GetCPUUsage` | `digital_CPU_percent · 0.01` |
| `MixAhead` | `AIL_serve` |
| `SetLatency(ms)` | `set_preference(42, ms / frag)` + `serve` |
| `GetHighestTimerDelay` | `get_timer_highest_delay` |
| `GetDirectSoundInterface` | the `IDirectSound*` (§1) |

- **Absent:** no 3D (`AIL_3D_*`), streams (`AIL_open_stream`), filters, EAX or timers. Pan and volume come from the engine (`SESoundManager`), so all audio is 2D [static].
- **Rate:** `AIL_sample_status` ≈ 3/s on `legion` [measured, legion.md §5.5].
- **Channel count:** with `Max Number Of Sounds` = 100 %, `SESoundManager` uses `max(8, round(max·1.0))` = **16 channels** on modern Windows [inferred: Vista+ reports 0 hardware buffers].

### 4.2 The 34 imports vs the kit's `dx/mss32.cpp` [static]
**Present in the kit (17):**
`startup`, `shutdown`, `set_preference`, `get_preference`, `set_digital_master_volume`, `set_named_sample_file` (RIFF PCM, or a whole MP3 decoded with minimp3), `mem_free_lock`, `file_read`, `allocate_sample_handle`, `release_sample_handle`, `init_sample`, `start_sample`, `end_sample`, `sample_status`, `set_sample_volume`, `set_sample_pan`, `set_sample_loop_count`.

**Missing (17)**, with what each needs:

| Function | Needs |
|---|---|
| `open_digital_driver@16` | like the kit's `waveOutOpen`: register a driver at 22050/16/2 and return `HDIGDRIVER` (0 = fail) |
| `close_digital_driver@4` | like `waveOutClose` |
| `get_DirectSound_info@12` | write a stub `IDirectSound*` (QI/AddRef/Release/GetCaps with `dwSize` 0x60, `dwFreeHwMixingAllBuffers` = 0 → 16 channels); optional `IDirectSoundBuffer*` = NULL. **Also feeds DirectMusic `Init` and Bink.** |
| `set_DirectSound_HWND@8`, `set_redist_directory@4` | no-op; return the previous directory pointer |
| `last_error@0` | pointer to a static guest string |
| `digital_CPU_percent@4`, `get_timer_highest_delay@0` | constants |
| `serve@0` | call `mss32_frame_pump` |
| `mem_alloc_lock@4`, `file_size@4` | `heap_alloc`; stat via the runtime path resolver (as `file_read`) |
| `sample_granularity@4` | bytes per frame for PCM (1 for EE's 8-bit mono) |
| `sample_playback_rate@4`, `set_sample_playback_rate@8` | wave rate / `host_audio_set_frequency` (pitch) |
| `sample_position@4`, `set_sample_position@8`, `sample_ms_position@12` | `host_audio_position` / `start_offset`; ms from bytes and rate |

Also:
- `set_named_sample_file` decodes a whole MP3 on every call. Each `LoadWaveData` uses the probe sample, and each play does it again. `EEIntro.mp3` is about 219 s, so cache the decoded PCM by guest buffer address.
- `riff_parse_wave` is PCM-only, which is sufficient: EE has no ADPCM.

---

## 5. Sound data: `data.ssa` [static, own reader]

### 5.1 Format
Matches the field layout in the EE-modders tools: SSA-tool (archived, no licence) and Empire-Earth-Studio-2 (GPL-3.0); only their format description was read.

- **Header (16 bytes):** `"rass"`, u32 version major = 1, u32 minor = 0, u32 *index length*. The index ends at offset 16 + length.
- **Index entry:** u32 *n*, name[*n*] (Latin-1, NUL-terminated, backslash paths such as `sounds\…`), u32 start, u32 end (**inclusive**: end = start + size − 1), u32 *stored* size.
- **After the index:** u32 count, then count × (u32 *n*, key, u32 *m*, value). `data.ssa` has one: `Timestamp`. File data starts right after it (offset 163,402).
- **File data:** either stored raw, or `"PK01"`, u32 unpacked size, u32 0, then a **PKWARE DCL implode** stream. zlib `contrib/blast` decodes it, and the unpacked lengths match the RIFF sizes [measured].
- **Contents:** 3,739 entries:

| Kind | Count |
|---|---|
| `.sst` textures | 2,095 |
| `.wav` | 758 |
| `.cem` models | 730 |
| `.tai` | 58 |
| `db\*.dat` | 39 |
| `.tga` | 31 |
| `.civ` | 21 |
| `.mp3` | 3 |
| other | 4 |

The engine's `FS*` classes read it (`FSMemoryFile` → `SPIMLoadWaveData`), so the port needs no conversion.

### 5.2 Sound census [measured: every `sounds\*` entry, headers decoded in memory]
- **758 `.wav`, all RIFF `WAVE_FORMAT_PCM`, 1 channel, 22,050 Hz, 8-bit.**
  - 736 are DCL-compressed and 25 stored.
  - Median 22 KB, 25 MB in total, largest 3.5 MB.
- **3 `.mp3`** (`EEIntro`, `Arthurian`, `Siege`): MPEG-1 Layer III, 44.1 kHz stereo, 128 kbps; about 219 s, 64 s and 59 s.
- **No IMA ADPCM, no ASI-only codecs** (voice `.asi` are shipped but unused).
- **Loose files:** 12 WAVs, all in `Data/WONLobby Resources/Sounds`, for the dead WON lobby UI (PCM 16-bit at 11/44.1 kHz, one 8-bit). `Data/Sounds/` is empty.

---

## 6. Bink [measured]
`ffprobe` / `ffmpeg` 9.0.1 (Homebrew) results:

| File | Container | Video | Audio | Full decode |
|---|---|---|---|---|
| `Sierra.bik` | `BIKi` (Bink 1, rev *i*) | 640×480, 30 fps, 9.0 s | binkaudio_rdft 44.1 kHz stereo | 270/270 frames, no errors |
| `SSSI.bik` | `BIKi` | 640×480, 30 fps, 8.0 s | same | 240/240 |
| `Empire Earth.bik` | `BIKi` | 640×480, 15 fps, 98.1 s | same | 1471/1471 |

- **All three are present** in the GOG base folder.
- **Audio:** float peaks reach +1.4 to +2.5 dBFS, so the s16 conversion must clamp. The kit's `bink.cpp` already does.
- **Calls** (`DX7Movie::PlayBink`, one TSThread per movie) [static]:
  - `BinkSetSoundSystem(BinkOpenDirectSound, pDS)` (only if the pointer changed), then `BinkOpen(path, 0)`;
  - per frame: `BinkWait` (with `Sleep(1)`), `BinkDoFrame`, a Lock of the DD surface, `BinkDDSurfaceType(surface)`, `BinkCopyToBuffer`, `BinkNextFrame`;
  - at the end: `BinkClose`.
- **Coverage:** all 9 imports are in the kit's `dx/bink.cpp` (`OpenDirectSound`/`SetSoundSystem` are `ret1`). The pixel formats from `BinkDDSurfaceType` need checking against the D3D7 shim's surfaces once DD7 exists.

---

## 7. Recommendations and effort

1. **DirectMusic: 3–4 focused weeks** (ADR-001 gap 11 confirmed).

   | Work | Estimate |
   |---|---|
   | COM HLE for §2.9 (kit `dx/dmusic.cpp`, about 40 methods) + host-mixer source (as `midi_synth.cpp`, but pulling `DmPerformance_renderPcm`) | 1–1.5 wk |
   | Upstream dmusic fixes #1–#5 + GM bank (macOS DLS, SF2 fallback) | 0.5–1 wk |
   | Reverb + gain staging | 0.5 wk |
   | A/B against `legion` recordings, tuning (#8–#10 as needed) | 1 wk |

   - **Pin a fork of dmusic** (MIT) with our patches, and send #1–#3, #5 and #11 upstream.
   - At first run, resolve `gm.dls`/`gs.dls` to `/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls`. That is the user's own system file and is never shipped; check that it exists, and fall back to the SF2.
2. **Miles: 0.5–1 week.** Add the 17 functions (§4.2), the stub `IDirectSound`, and MP3 decode caching. Keep the translated mixer plugin: it is tiny (95 functions) and its `SPIM` interface takes engine C++ objects (`FSMemoryFile`, `SESample`), so replacing it natively is not worth it.
3. **Bink: ≤ 0.5 week**, verification only.
4. **Phasing.** Miles and Bink in Phase 3–4 as planned. For DirectMusic, the stub (§3.4 fallback 3) is enough until M2; the real implementation goes in Phase 4.

---

## 8. `legion` follow-ups (not run; `ssh legion` was reserved)

Scripts, not tracked (proposed for `windows/`):
- `windows/frida/music_hooks.js`, a Frida script with three parts:
  - a timestamped log of every `SEConductor`/`SEScore` call with decoded arguments;
  - counts and argument samples for all 34 `AIL_*` and the `SPIM` channel functions;
  - `PARAMS.schedule`: forces intensity levels on the live conductor (locks `SetIntensity` and calls `TransitionTo(current, 0, 0)`).
- `windows/loopback_record.py`: WASAPI loopback to WAV (`pip install soundcard numpy`).

Steps:
1. Copy the scripts and install the dependency:
   ```sh
   scp windows/frida/music_hooks.js legion:C:/EEPort/frida/
   scp windows/loopback_record.py legion:C:/EEPort/scripts/
   ssh legion 'C:\EEPort\tools\venv\Scripts\pip.exe install soundcard numpy'
   ```
2. Trace a normal session: menu → random map → 3 min of play.
   ```sh
   L start; L "session '-Action key -Keys ESC'"    # then route into a game as in legion.md §6
   ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts music_hooks --seconds 240 --out C:\EEPort\captures\music-trace'
   ```
3. Make reference recordings, for A/B against `renders/*-fixed-sched.wav`. Both steps must run in the user's session, so add a `record` action to `launch.ps1` that runs `loopback_record.py` through `Invoke-InSession` like `frida`. Then run both together:
   ```sh
   L "frida '--scripts music_hooks --seconds 200 --param schedule=[[0,1],[30,2],[60,3],[90,2],[120,0],[150,1]] --out C:\EEPort\captures\music-sched'" &
   L "record '190 C:\EEPort\captures\music-sched\loop.wav'"
   ```
4. Check during these runs:
   - the DirectMusic port's sample rate and voices (§2.4);
   - `SPIMGetMaximumChannelCount` (16 expected);
   - whether the main menu calls `Play` on the conductor (§2.7);
   - Nahimic: disable its effects for clean loopback.

---

## 9. Open questions
- What exactly feeds the intensity ratio in `FUN_00503b83`? (Military or combat share is suspected.) What is the mode field at `[0x919788]+0x48`?
- Does the menu play DirectMusic at all, or only `EEIntro.mp3`? Settle with the `legion` trace.
- On Windows, which of `fill`/`break`/`intro`/`end` patterns ever sound? None are requested by the EXE; DirectMusic might still choose an INTRO at segment start.
- Windows synth output level, voice limit and sample rate. These set our gain staging and the decision to render at 22.05 kHz.
- Steam/retail builds: same music DB? (Out of scope per D8.)

---

## 10. Reproduce (`analysis/audio/`, git-ignored)
```sh
cd analysis/audio
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r ../../tools/requirements.txt numpy
# Ghidra (mixer + display plugins; the EXE is not re-analysed)
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home GHIDRA_MAXMEM=6G \
  /opt/homebrew/Cellar/ghidra/12.1.4/libexec/support/analyzeHeadless $PWD/ghidra audio \
  -import "bin/Miles Sound System Mixer.dll" bin/DX7HRDisplay.dll \
  -scriptPath ../../spikes/ee-recomp/kit/tools -postScript ExportProgram.java $PWD/ghidra-out
# dmusic + patch + driver
git clone https://github.com/GothicKit/dmusic third_party/dmusic && git -C third_party/dmusic checkout b73b270
git -C third_party/dmusic apply ../../patches/dmusic-ee-fixes.patch
cmake -S third_party/dmusic -B build/dmusic -DDM_ENABLE_ASAN=OFF -DDM_BUILD_STATIC=ON && cmake --build build/dmusic
clang -O2 -std=c11 -Ithird_party/dmusic/include driver/ee_dmrender.c build/dmusic/libdmusic.a \
  build/dmusic/vendor/libdmusic-tsf.a -lm -o build/ee_dmrender
mkdir -p sysdls && for n in gm gs; do ln -sf /System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls sysdls/$n.dls; done
M="../../original/gog/Empire Earth/Data/Music"
EE_GM_DIR=$PWD/sysdls ./build/ee_dmrender "$M" Balance.sgt 180 renders/Balance.wav 44100 "0:1,30:2,60:3,90:2,120:0,150:1"
.venv/bin/python -I scripts/wavstats.py renders/*.wav
# SSA (zlib contrib/blast, memory only)
clang -O2 -shared -fPIC -Ithird_party/blast third_party/blast/blast.c driver/dclwrap.c -o build/libdcl.dylib
.venv/bin/python -I scripts/ssa.py "../../original/gog/Empire Earth/Data/data.ssa"
```
Other scripts:
- `scripts/riffdump.py`: DirectMusic RIFF tree;
- `scripts/dm_channels.py`: band channels vs notes;
- `scripts/exports.py`, `xref_iat.py`, `dis.py`, `guids.py`: PE export/IAT, cross-references, disassembly, GUID decoding.
