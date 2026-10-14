# M1 replay driver: random-map generator, capture → replay

The M1 oracle (docs/phase2.md) in three steps. The full spec is in [rmg.md](../../../docs/reference/rmg.md) §6–§8.

1. **Capture** on legion: `windows/frida/rmg_capture.js` records every `EERMPIM*` call that `Default.dll` receives during a real random-map start. For each call it records:
   - the input memory, the allocation and file-I/O sequences, and the output memory;
   - everything address-exact.
2. **Pack** (Mac or legion): `rmg_pack.py` turns a capture into one `.rrp` file per call.
3. **Replay** each pack and compare:
   - on **Windows** against the real DLLs (`rmg_replay.exe`). This is the completeness self-check: a capture that misses an input does not reproduce its outputs.
   - on the **Mac** against the translated DLLs (the kit harness plus `rmg_replay_core.c`). The outputs must be byte-identical.

Captures and packs hold game-derived bytes. Keep them out of git: `analysis/m1-captures/` on the Mac, `C:\EEPort\captures\rmg\` on legion.

## Files

| File | What |
|---|---|
| `rmg_replay.h` | pack format, platform interface, shim API |
| `rmg_replay_core.c` | portable C99: pack parser, address ranges, allocator/file shims (sequence replay), restore, call, compare, report |
| `rmg_replay_win32.c` | Windows platform: suspended-child address reservation, PE mapper at the captured bases, import binding, stack switch, crash report |
| `build_win32.bat` | MSVC build |
| `rmg_pack.py` | capture directory → packs (`list`, `pack`) |
| `selftest/` | host-only test of packer + core with a mock platform: `sh selftest/run.sh` |

## How a replay works

The replay is **address-exact**: no pointer is ever rewritten.

- **Modules.** `sxlrt233.dll`, `Low-Level Engine.dll` and `Default.dll` are mapped at their captured bases. Their imports are bound as follows:
  - msvcrt `operator new`/`new[]`/`malloc`/`calloc`/`realloc`, `operator delete`/`delete[]`/`free` and `fopen`/`fgets`/`fclose` go to the shims;
  - kernel32 `OutputDebugStringA` goes to a no-op;
  - everything else goes to the real system DLLs.

  `msvcp60.dll`'s msvcrt allocator imports are patched too. Then the DllMains run in dependency order, with the shims in pass-through mode.
- **State.** Every input region is written back at its address:
  - the context (`ctx`, 0x3d0 bytes);
  - Default.dll's `.data`;
  - the captured stack window;
  - the `UString` argument of `SetRandomMapValues`;
  - every heap block reachable from these.

  The arguments go at `[ESP+4]…`.
- **Call.** The export runs with ESP equal to the captured ESP (the driver's return address goes in the captured return slot) and with x87 CW equal to the captured CW, after FNINIT.
- **Shims during the call.**
  - Each allocator call must match the next recorded event (operation and arguments). It returns the recorded address, which already holds the recorded initial contents.
  - `fopen`/`fgets`/`fclose` replay the recorded sequence. The path must match, and `fgets` copies the recorded line into the buffer.
  - Any divergence stops the replay at once and names the event.
- **Checks after the call:**
  - EAX and the x87 CW;
  - all recorded allocator and file events were consumed;
  - every output region is byte-identical. Mismatches give the region, its label (for example `heap<-ctx+0xb4`, a row of the height map), the first differing offset and the differing byte count.

  `--diff-dir` writes expected and actual bytes.

Exit status: 0 pass, 1 mismatch, 2 error (setup, address conflict, crash). A crash is reported with module+RVA.

## Windows (legion): build and run

Any 32-bit C compiler works. Options:

- **MSVC.** Install VS 2022 Build Tools with the C++ workload (free). Then, in an *x86 Native Tools* prompt: `tests\m1\replay\build_win32.bat`.
- **MinGW from the Mac.** No install on legion. `brew install mingw-w64` needs the user's OK; then:
  ```sh
  i686-w64-mingw32-gcc -std=c99 -O2 -Wall tests/m1/replay/rmg_replay_win32.c tests/m1/replay/rmg_replay_core.c \
      -o rmg_replay.exe -static -Wl,--large-address-aware -Wl,--dynamicbase
  scp rmg_replay.exe legion:C:/EEPort/tools/
  ```
- **clang-cl from the Mac**, with an SDK provisioned by `xwin`: `clang-cl --target=i686-pc-windows-msvc /O2 /MT ... /link /LARGEADDRESSAWARE /DYNAMICBASE`.

Both inline-asm variants (MSVC `__asm` and GNU `__asm__`) are checked to assemble with clang for i686.

Run, from cmd on legion:

```bat
C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\m1\replay\rmg_pack.py pack C:\EEPort\captures\rmg\c01 -o C:\EEPort\captures\rmg\c01\packs --verify
C:\EEPort\tools\rmg_replay.exe --game "C:\EEPort\game\Empire Earth" --diff-dir C:\EEPort\captures\rmg\c01\diff C:\EEPort\captures\rmg\c01\packs\*.rrp
```

`*.rrp` is expanded by the program itself: MSVC links `setargv.obj`, and the MinGW runtime globs by default.

**Why a child process per pack.** The parent starts the child suspended and checks with `VirtualQueryEx` that every needed range is free. Then it reserves the ranges before the child's loader runs, so the child's heap and the DLLs it loads later land elsewhere. If the child's initial stack, its image or ntdll occupy a needed range, the parent retries with a fresh child (ASLR moves them), up to 10 times.

## Mac platform: what the kit harness must provide

The core is host-independent. The Mac build links `rmg_replay_core.c` into a kit harness (workstream A's `tests/m1/harness/`, on B's loader) that implements `rr_platform` (`rmg_replay.h`):

| Callback | Needs from the kit |
|---|---|
| `map(addr, size)` | commit guest pages at **exactly** these 32-bit guest addresses. B's 4 GB guarded arena makes every address representable. The harness must keep the kit's own heap, stack and shim regions out of the pack's ranges: query `rr_needed_ranges()` before loading anything. |
| `write`/`read` | guest memory copy (`gm_write`/`gm_read`) |
| `load_modules(mods)` | B's multi-module loader: load the three **translated** modules at the given bases (relocated), bind imports, run the DllMains in order (sxlrt233, engine, Default). Binding must let the harness override `msvcrt.dll!??2@YAPAXI@Z`, `??_U@YAPAXI@Z`, `malloc`, `calloc`, `realloc`, `??3@YAXPAX@Z`, `??_V@YAXPAX@Z`, `free`, `fopen`, `fgets`, `fclose` and `kernel32.dll!OutputDebugStringA` with host functions. Each override calls the matching `rr_shim_*` while `rr_active()`, else the normal HLE. The cdecl args are read from the guest stack; the return goes in EAX. |
| `export_addr(module, name)` | export lookup in a loaded guest module |
| `call(fn, esp, cw)` | run guest code at `fn` with ESP = `esp`, a sentinel return address at `[esp]`, x87 reset and CW = `cw`, until it returns to the sentinel. Return EAX and the CW. Needs the strict-trap mode (gap 3) so a miss stops instead of returning 0. |
| `fatal`/`log` | report and abort the guest call |

What makes the Mac replay comparable with Windows:
- the kit's msvcrt HLE (workstream D) is *not* used for the shimmed functions during the call. It *is* used for everything else the call reaches: `sscanf` (`%d`/`%f` parsing of the scripts), `sqrt`, `strlen`, `printf`, `_ftol` if reached. Those are under test.
- The x87 model (workstream C) is under test through every stage.

## Self-test (any host)

`sh tests/m1/replay/selftest/run.sh` does the following:
1. builds the core with a mock platform;
2. writes a synthetic capture (no game data);
3. packs it;
4. expects `PASS`, and expects a `MISMATCH` for a variant with one corrupted expected byte.
