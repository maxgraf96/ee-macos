# windows/ — reference harness for `legion`

Scripts that turn the Windows laptop `legion` (reached as `ssh legion`) into the reference
machine for the unmodified GOG build. Results and machine facts: `docs/reference/legion.md`.

Everything on legion lives under `C:\EEPort\` (game, tools, captures, these scripts), plus
Python 3.12 in user scope and the `HKCU\Software\SSSI` / GOG HKLM registry keys. No game bytes
are tracked here; `deploy.sh` copies them from the git-ignored `original/gog/`.

| File | Runs on | Purpose |
|---|---|---|
| `deploy.sh` | Mac | copy game folder (if missing) + GOG install script + these scripts, run `setup.ps1` |
| `setup.ps1` | legion | verify module hashes, write registry, install Python/Frida venv, Sysinternals, x64dbg (`-WithX64dbg`) |
| `launch.ps1` | legion (SSH) | `status` / `start` / `stop` / `modules` / `session` / `frida` / `cleanup` on the **interactive** desktop |
| `session.ps1` | legion (user session) | windows, screenshots (`grab` = Desktop Duplication), clicks, keys, `close` |
| `hidden.vbs` | legion | runs a session job without a console flash |
| `frida/run.py` | legion | attach or spawn, load scripts, snapshot to JSON |
| `frida/modules.js` | in game | module list with base / preferred base / relocation, SHA-256 (added by run.py) |
| `frida/threads.js` | in game | per-thread x87 CW + MXCSR by sampling `GetThreadContext`, start address, stack scan |
| `frida/fpu_hooks.js` | in game | `_controlfp`/`_control87`, engine `UM{Get,Set}FloatingPoint*`, thread creation, `LoadLibrary*`, D3D9 `CreateDevice` flags |
| `frida/iat_census.js` | in game | API census: IAT stubs on every external import, counts per function × thread × CW |
| `frida/sync_hooks.js` | in game | `ESChecksum*`/`ESVerify` call counts per thread × CW, `GetTotalChecksum` stream (0 calls in single player) |
| `frida/summarize.py` | Mac or legion | capture directory → Markdown tables |
| `vm/` | Mac → legion | EE-VM: unattended Windows 11 VM in VMware, the second peer for LAN captures (`vm/README.md`) |

## Typical session (from the Mac)

```sh
windows/deploy.sh                       # first time: copies ~400 MB, runs setup.ps1
L() { ssh legion "powershell -NoProfile -ExecutionPolicy Bypass -Command \"& C:\\EEPort\\scripts\\launch.ps1 $*\""; }
L start                                 # game appears on legion's screen (session 1)
L "session '-Action grab -Out C:\EEPort\captures\screen.jpg -Scale 1280'"   # see the real screen
ssh legion 'C:\EEPort\tools\venv\Scripts\python.exe C:\EEPort\frida\run.py --scripts modules,threads,fpu_hooks,iat_census --seconds 60 --out C:\EEPort\captures\menu'
scp -r legion:C:/EEPort/captures/menu /tmp/ && python3 -I windows/frida/summarize.py /tmp/menu --inventory db/win32-imports.json
L stop                                  # WM_CLOSE; the game exits cleanly (not during intro movies)
```

ESC (`session '-Action key -Keys ESC'`) skips the intro movies. The in-game route (Single Player → Play
Random Map → Start Game) works with `session '-Action move -X <dx> -Y <dy>'` + `'-Action click -Here'`,
checked with `grab` after each step (relative, speed-dependent mouse gain; see legion.md §6).

Spawn-time capture (init: CRT/engine FPU setup, D3D9 device flags, plugin loading) must run in
the user's session: `L "frida '--spawn --scripts modules,fpu_hooks,threads --seconds 150 --out C:\EEPort\captures\spawn'"`.
Do not load `iat_census` at spawn (imports are not bound yet).

## Gotchas (learned the hard way)

- SSH commands run in session 0: no desktop. Anything that needs the screen goes through
  `launch.ps1` (scheduled task, `LogonType Interactive`, normal priority, no battery stop).
- `powershell -File` treats any argument starting with `-` as a parameter name, even quoted.
  Use `-Command "& launch.ps1 session '-Action ...'"` as above.
- The GOG wrapper presents through D3D9 flip/fullscreen; GDI screenshots show a stale desktop
  or black. `session.ps1 -Action grab` uses ffmpeg's `ddagrab` (Desktop Duplication).
- Frida 17.23.3 on 32-bit Windows targets: TinyCC CModule globals are not writable and
  `__attribute__((stdcall))` externs unbalance the stack; pass buffers in and call stdcall APIs
  through inline asm (see `threads.js`). An asm `"m"` operand naming an extern symbol faults.
- Frida's `Module.enumerateImports()` skips ordinal imports (all of ws2_32/wsock32) and crashes
  the game on `Language.dll`; `iat_census.js` walks the import directory itself.
- If a sampler ever dies between `SuspendThread` and `ResumeThread`, the thread stays suspended
  (and Frida's `enumerateThreads()` stops listing it at the 127 limit). Check with
  `(Get-Process -Id <pid>).Threads | ? WaitReason -eq Suspended`.
