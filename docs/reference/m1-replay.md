# M1 replay on the Mac

Status: implemented and self-tested, 2026-10-14. No real capture replayed yet: legion has none so far. Owner: workstream H (m1-replay).
Code:
- [`tests/m1/replay/rmg_replay_mac.cpp`](../../tests/m1/replay/rmg_replay_mac.cpp) (replay), [`rmg_record_mac.cpp`](../../tests/m1/replay/rmg_record_mac.cpp) (Mac-side recorder), [`rmg_mac_common.cpp`](../../tests/m1/replay/rmg_mac_common.cpp);
- [`tests/m1/replay/run_replay.py`](../../tests/m1/replay/run_replay.py) (`tools/test.py --m1-replay`);
- [`tools/m1/run_matrix.py`](../../tools/m1/run_matrix.py) (the matrix).

Labels: **[measured]** = output of the commands here on an M4 Max (macOS 15.5); **[static]** = read from the code or the binaries; **[inferred]** = reasoning.

Context: [phase2.md](../phase2.md) defines M1; [rmg.md](rmg.md) §6–§8 specifies the capture, the packs and the matrix. This file is the Mac half of rmg.md §7.4.

---

## 1. What it does

The M1 oracle replays each captured `EERMPIM*` call twice:
- on Windows against the real DLLs, which proves the capture complete (`rmg_replay.exe`, workstream F);
- on the Mac against the translated DLLs, which is M1 itself.

Both run the same portable core ([`rmg_replay_core.c`](../../tests/m1/replay/rmg_replay_core.c)): it parses the pack, restores memory, drives the allocator and file shims, calls the export and compares every output byte. The Mac half is an `rr_platform` over the kit. Two programs come from one source:

| Program | Translations |
|---|---|
| `build/recomp/ee_m1_replay` | the freestanding bundles, `dlopen`ed (`--bundles build/recomp/bundles`, or `EE_M1_BUNDLES`) |
| `build/recomp/ee_m1_replay_static` | the generated archives, linked in |

A pack must replay identically on both builds.

## 2. Running it

**Prerequisites:**
- a kit with `mem_open` and `heap_exclude` (kit branch `feat/m1-replay`, to be merged into `ee`);
- the translation inputs and a translation (`tools/setup.py`, then `tools/build.py --regenerate`; [build.md](build.md) §2–§4).

`tools/test.py --m1-replay` builds both programs first. `--no-build` skips that, and `--kit DIR` builds against another kit checkout.

```sh
.venv/bin/python tools/test.py --m1-replay                      # the self-tests (§5)
.venv/bin/python tools/test.py --m1-replay analysis/m1-captures/S00/packs   # replay packs, both builds
.venv/bin/python tools/m1/run_matrix.py                         # fetch from legion, replay every case, report
```

Directly:

```sh
build/recomp/ee_m1_replay --bundles build/recomp/bundles [--diff-dir DIR] [--results FILE.tsv] \
    [--log-dir DIR] [-v] [--lenient] PACK.rrp...
build/recomp/ee_m1_replay_static [the same options] PACK.rrp...
```

| Option | Effect |
|---|---|
| `--diff-dir DIR` | writes `<seq>-<region>-expected.bin` and `-replay.bin` for every region that differs (the core's format, as on Windows) |
| `--results FILE` | appends one TSV row per pack (§4) |
| `--log-dir DIR` | keeps each child's stderr as `<n>-<pack>.log`. By default, logs go to a temporary directory and are deleted for packs that pass. |
| `-v` | the core's per-call line, the modules and their DllMains, `OutputDebugStringA` counts. `-v -v` also shows the call's `printf` text and debug lines. |
| `--lenient` | without strict mode (`RECOMP_STRICT=0`) |

### 2.1 The matrix (`tools/m1/run_matrix.py`)

1. **Fetch.** It lists `C:\EEPort\captures\rmg` on legion with `ssh legion "dir /b ..."`. Nothing else runs there. For every case whose `packs\` holds `index.tsv` (`rmg_pack.py` writes that file last), it `scp`s `packs\` into `analysis/m1-captures/<case>/packs/`. It also fetches `replay-win.txt` when the operator saved `rmg_replay.exe`'s output there. A case already present is left alone unless `--refetch`. `--no-fetch` replays what is already here.
2. **Replay.** Every case runs on both builds (`--build bundles|linked|both`), in strict mode, 4 cases at a time (`--jobs`). Per case and build, it writes:
   - `replay-<build>.tsv`;
   - `replay-<build>.log`, the printed report;
   - `diff-<build>/`.
3. **Report.** It writes `analysis/m1-captures/report.md` (`--report`), described in §4.

Exit 0 only when every pack passed on every build. With no captures it exits 1, and the report says that nothing was replayed.

`--cases S00,A01` restricts both fetching and replay. Captures, packs, diffs and the report all stay under the ignored `analysis/`.

## 3. How a replay works

**One child process per pack.** The parent is this program without `--child`. Each pack needs a fresh guest arena whose heap keeps out of that pack's addresses. A child also contains a guest fault or a strict-mode stop. The child does the following [static: `rmg_replay_mac.cpp`]:

1. **Ranges.** It reads the pack and the address ranges it needs (`rr_needed_ranges`):
   - the module images;
   - the captured stack window plus 1 MB below ESP;
   - every input and output region;
   - every block the call allocates.
2. **Heap exclusion.** A `mem_init` reset hook calls `heap_exclude` on each range as the arena is created. The DllMains, the kit's CRT and the loader then allocate around them, whether a range lies in the base heap or in its growth area.
3. **Modules.** It calls `loader_load` and checks the pack's `MODS` against the three mapped modules:
   - **base and `SizeOfImage` must match.** Captures must come from DLLs rebased to the [game.toml map](build.md#3-the-base-map); a mismatch is an error naming both.
   - a `TimeDateStamp` difference only prints a note, because a rebasing tool may restamp the copy.
4. **Address conflicts.** A needed range that overlaps any of these is an error:
   - the null page;
   - the kit's PEB/TEB/TLS pages (`0x0fdff000`–`0x0fe02000`);
   - the import trampolines (`0x0ff00000`–`0x10000000`);
   - the executable's image (`0x00400000`–`0x00a82000`; on Windows the game's own image is there).

   The kit's stack (`0x0e800000`–`0x0f000000`) may be overwritten: nothing lives on it while the call runs on the captured stack.
5. **Import overrides.** These go to the replay shims, which pass through to the kit's own implementations until the call starts:
   - msvcrt `operator new`/`new[]`/`malloc`/`calloc`/`realloc` and `operator delete`/`delete[]`/`free`;
   - `fopen`/`fgets`/`fclose`;
   - `printf`, whose text is kept out of the report;
   - kernel32 `OutputDebugStringA`, counted during the call.

   During the call, msvcrt's other stream functions (`fread`, `fscanf`, `fseek`, `fwrite`, …, 24 in all) are errors: captures record only `fopen`/`fgets`/`fclose` and flag the others as warnings.
6. **DllMains.** They run in Windows' order: sxlrt233 and the engine (the executable's static imports), then Default.dll (`LoadLibrary`).
7. **Restore.** The core writes every input region at its own address. `mem_open` makes ranges above `guest_size` (`0x11000000`) exist at exactly those addresses, and the kit's string functions read them. This matters on legion, where the game's heap reaches past `0x30000000` (the census put DLLs at `0x2ffa0000`/`0x30f80000` [measured]).
8. **Call.** The export runs at the captured ESP:
   - `[esp]` holds the kit's return sentinel;
   - the general registers are zero;
   - the x87 gets `FNINIT`, then the captured CW;
   - the TEB's stack bounds follow ESP for the duration.
9. **Compare.** The core checks EAX, the CW after the call, that every recorded allocator and file event was consumed, and every output byte.

**Strict mode** is on unless `--lenient`. In strict mode, any of the following stops the replay with the kit's diagnostic:
- a call or jump the translation does not cover;
- an import of unknown argument count;
- a divide error;
- an access to the null page.

A guest memory fault that no guest handler takes ends the child with exit code 5. The parent reports it as an error and prints the tail of the child's log.

**What is under test** on the Mac:
- the three translated DLLs;
- the x87 model;
- the kit's msvcrt for everything the shims do not replace (`sscanf`, `sqrt`, `strlen`, the `_mbs*`/`str*` family, `sprintf`);
- the kit's msvcp60.

## 4. Report format

**Per pack**, on stdout, with the same lines as the Windows replay (the core prints them on both platforms):

```
PASS seq=12 gen=0 attempt=0 EERMPIMCreateHeightMap: 1043 regions (1192000 bytes), 85 allocator calls, 0 file calls, EAX 0
MISMATCH seq=12 EERMPIMCreateHeightMap: region 7 heap<-ctx+0xb4 0x2f41c000+0x804: 3 bytes differ, first at +0x1f0 (dword expected 3e4ccccd, replay 3e4cccce)
FAIL seq=12 gen=0 attempt=0 EERMPIMCreateHeightMap: 1 of 1043 regions differ (3 bytes)
MISMATCH seq=3 EERMPIMSetRandomMapValues: allocation 1420 differs: recorded new(0x1e, 0), replay new(0x3, 0)
ERROR the capture's sxlrt233.dll is at 10000000 (SizeOfImage 0xe4000); the translated sxlrt233.dll is at 10200000 (0xe4000). ...
```

- **A mismatch line** gives the region index and label, its address and size, how many bytes differ, the first differing offset, and the dword expected vs replayed there. The label is `ctx`, `Default.dll .data`, `arg UString`, or `heap<-ctx+0xb4` (a block found through ctx+0xb4, here a row of the height map).
- **The first divergence in a shim** stops the call with the event index and both values. Divergences: an allocator call with other arguments, a file call out of order, a different path.
- **`n/m packs passed`** ends the run. Exit status: 0 all passed, 1 a mismatch, 2 an error. An error is a setup problem, an address conflict, a strict stop, a guest fault or a crash.
- **For a pack that did not pass**, the parent prints the last 12 lines of the child's log under the pack's lines, prefixed `  | `.

**`--results` TSV**, one row per pack: `pack`, `status` (`PASS`/`MISMATCH`/`ERROR`), `seq`, `gen`, `attempt`, `export`, `exit`, `detail`. The detail is the most telling line: the PASS line, the FAIL line followed by the first MISMATCH line, the first shim divergence, or the platform's own error.

**`report.md`** (`run_matrix.py`) has these parts:
- a header: date, ee-macos and kit commits, builds, and the totals;
- **Cases**: per case, its packs, pass, mismatch and error counts, plus the Windows pass count when `replay-win.txt` came along;
- **Calls (case x export)**: one column per export in pipeline order. Each cell is `passed/replayed`, counted over a case's attempts, with `M` or `E` when any call failed;
- **Calls that did not pass**: one row each, with the per-build status, the Windows status if known, and the detail. A call that passed on the Mac but failed the Windows replay is listed too, because a capture that does not replay on Windows proves nothing on the Mac.

## 5. Self-tests (`tools/test.py --m1-replay`)

1. **The core's self-test** (`selftest/run.sh`): G's synthetic capture through `rmg_pack.py` and the core, over a mock platform. It expects PASS, and MISMATCH for one corrupted byte.
2. **The round trip, with no Windows involved:**
   - `ee_m1_replay --record` runs `EERMPIMSetRandomMapValues` through the translated modules on a synthetic context, with synthetic scripts, and records it in `rmg_capture.js`'s format (§6). `rmg_pack.py --verify` packs it, and both builds replay it.
   - It runs twice: once with the modules' blocks in the kit's heap, once with them in a heap at `0x2f000000`, above `guest_size` (`--heap-at`). The second makes the replay open memory there and the kit's string functions read it. The two recordings must leave the same values in ctx+0x220..0x3d0.
3. **Two deliberate corruptions**, which both builds must report as MISMATCH:
   - one byte of the expected context;
   - one recorded script line (`kWaterBorder` 4 → 5), so that the replayed call computes something else.

[measured] All pass on both builds, in about 5 s:
- the recorded call returns 0;
- 97 input regions (393 KB), 7,396 allocator calls, 88 file calls, 457 output regions (69 KB);
- recordings from the bundle build and the linked build pack to byte-identical `.rrp` files;
- the two corruptions are reported at ctx+0x25e and at ctx+0x304 (`00000004` expected, `00000005` replayed).

A replay takes about 0.4 s per pack, mostly process start, translation load and `loader_load`. The 60-case matrix (about 600 packs) is therefore a few minutes per build [measured on the round-trip pack; inferred for the matrix].

**Error paths checked by hand** [measured]:
- G's synthetic pack (module bases 0x10000000/0x01410000/0x0f200000) is refused with the module error above;
- a context pointer corrupted in the input diverges at the first affected allocator call;
- a TSV row and the log tail come out for each.

## 6. The Mac recorder (`--record`)

```sh
build/recomp/ee_m1_replay --bundles build/recomp/bundles --record OUT [--rmv NAME] [--scripts DIR] \
    [--heap-at ADDR] [--seed N] [--size-class K] [--players N] [--epoch E] [--case NAME]
```

It writes `OUT/calls/000000.json`, `OUT/blobs/` and `OUT/rmg_capture.json` in `ee-rmg-capture/1` (rmg.md §6), following `rmg_capture.js`'s rules:
- **Heap index:** every block the modules get from msvcrt's allocators, from load on.
- **At entry:**
  - the roots (ctx, Default.dll `.data`, the `UString` argument), plus every live block a dword of a root or of a taken block points into, recursively;
  - the stack window `[ESP-0x8000, ESP+0x200)`, not scanned;
  - the engine's `.data` as `module_data_diag`;
  - the x87 words.
- **During the call:** allocator calls with each new block's first contents; `fopen`/`fgets`/`fclose` with their data; `printf` formats and debug lines.
- **At exit:** EAX, the x87 words, and the same closure without the stack.

**The context** gets what the EXE's `FUN_0066dc35` fills first (rmg.md §4.2):
- the two generators (engine constructors, seed written over the state);
- the scripts' `FSFileSpec`;
- `$MapSize`, `$StartEpoch`, `$PlayerCount`;
- 2 × width and height;
- the seed at ctx+0x3b0.

Every list stays empty.

**Scripts.** `--scripts DIR` serves the scripts from a host directory as `C:\Empire Earth\MacRoundTrip\`, through the kit's file seam, and never writes to the game. `run_replay.py` writes its synthetic `MacRoundTrip.rmv` and `MacRoundTrip Values.rmv` there: the script language and its parameter keywords, with values of their own, and no game data.

## 7. Known limitations

- **The round trip uses synthetic scripts** [static, measured]. On the synthetic context, the full `EERMPIMSetRandomMapValues` with a real script (Continental) reads the script and then faults in its post-processing, at Default.dll+0xc3ef. That code indexes the named-entry list ctx+0x100 with a placement rule's unit index. The EXE fills that list, and the lists at 0x140/0x150, with the game's object and terrain names, and the scripts' `PlaceUnit`/`DefineHerd`/`DefineForest` lines need them. Reproducing that list is exactly what the captures are for. So the round trip proves the pipeline (recorder → packer → replay → compare) on real translated code, and the real scripts are covered by the real captures.
- **Address conflicts are errors, not workarounds.** A Windows heap block in the kit's PEB/TEB/TLS pages or trampoline range (about 1 MB of the 4 GB, at fixed addresses below `0x10000000`) cannot be replayed. It would need the kit to move those regions per run [inferred: rare; none seen yet, since there are no captures].
- **Engine and sxlrt233 globals are not restored.** They come from fresh DllMains, as on Windows (rmg.md §4.4, §6.5). The Windows replay proves per call that the plugin does not depend on them.
- **The registers at entry** other than ESP are zero, and FS:[0] is the kit's chain, not the captured one. The exports are cdecl and read only their stack arguments [static: rmg.md §3]. Callee-saved pushes land in the stack window, which is not compared.
- **printf.** The kit's `printf` runs, so its return value is the kit's, but its text is suppressed. On Windows the game has no console.
- **The recorder's `--heap-at` is a bump allocator**: freed blocks are not reused. It starts after the DllMains, because the kit's CRT bookkeeping (the `__dllonexit` tables) must be able to reallocate its own blocks.
- **Timing.** Every pack loads the translations and maps the modules anew (about 0.4 s). That is fine for the matrix, but not for thousands of packs.
