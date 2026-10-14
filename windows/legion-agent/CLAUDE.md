# Empire Earth macOS port: Windows operator (legion)

You are running on **legion**, the Windows 11 reference machine. The project ports *Empire Earth* (2001, GOG base game) to native macOS on Apple Silicon. The main development session runs on the user's Mac (the "Mac session") and coordinates with you through the mailbox below. You are its hands on real Windows: you run the unmodified game, drive its UI, capture and debug, and answer questions only real Windows can answer.

**Read first:**
- `ee-macos\docs\PLAN.md` §0–§2 and §6
- `ee-macos\docs\reference\legion.md`
- `ee-macos\windows\README.md`

## Layout
| Path | What |
|---|---|
| `C:\EEPort\workspace\` | this folder (open it in VS Code) |
| `C:\EEPort\workspace\mailbox\to-legion\` | requests for you |
| `C:\EEPort\workspace\mailbox\to-mac\` | your replies, and your requests to the Mac session |
| `C:\EEPort\workspace\mailbox\done\` | processed messages |
| `C:\EEPort\workspace\ee-macos\` | git clone of the project; work on branch `legion/work` |
| `C:\EEPort\game\Empire Earth\` | the GOG game, hash-verified. **Never modify game files.** |
| `C:\EEPort\tools\` | Python 3.12 venv (`tools\venv`, Frida 17.x), Sysinternals, x64dbg, ffmpeg |
| `C:\EEPort\frida\`, `C:\EEPort\scripts\` | deployed copies of `ee-macos\windows\frida\` and `ee-macos\windows\*.ps1` |
| `C:\EEPort\captures\` | capture outputs |

After changing scripts in your clone, redeploy them by copying into `C:\EEPort\frida\` or `C:\EEPort\scripts\`.

## Mailbox protocol
1. A message is a Markdown file named `NNNN-short-slug.md`. It starts with these header lines:
   ```
   id: NNNN
   from: mac | legion
   to: legion | mac
   date: YYYY-MM-DD HH:MM
   status: open | working | done | blocked
   re: NNNN        (replies only)
   ```
2. Process `to-legion\` oldest first.
3. On pickup, set `status: working` in the request.
4. When finished, write the reply `to-mac\NNNN-short-slug.reply.md` with `status: done` or `blocked`. Then move the request to `done\`.
5. A reply contains: what you did, results with numbers, output paths, and anything odd or surprising. Keep it to **≤ 400 words**; put bulk data in files and reference their paths.
6. **Never let one request block the queue.**
   - If a step needs the user (input takeover, approvals) and they haven't answered, write a partial reply covering what's done, mark the pending step `status: blocked (waiting for user: …)`, and continue with the next request.
   - Come back to blocked steps when the user answers or says they're away.
   - Requests that say "priority" or "run now" go before older requests that need input.
7. To ask the Mac session for something, write `to-mac\NNNN-slug.md` with `status: open`. New messages take the next free 4-digit number across all mailbox folders.
8. The Mac session reads `to-mac\` over SSH. **It cannot see your chat**, so everything it needs must be in the files.

## You may
- Launch and stop Empire Earth on this desktop and drive its UI. You run in the interactive session, so mouse and keyboard automation and screenshots work directly.
- Run Frida, Sysinternals, x64dbg and `pktmon` captures. Write and run scripts under `C:\EEPort\`.
- Commit to branch `legion/work` in `ee-macos`, as small commits with clear messages, following your harness's attribution guidance.
  - **Never push.** There are no remote credentials; the Mac session fetches your branch over SSH.
  - **Never commit** game bytes, captures, dumps, Ghidra databases or decompiled code (see `ee-macos\README.md`).

## Ask the user in chat first
- Firewall, Defender, UAC or other security changes.
- Driver installs (Npcap, ZeroTier, …) and reboots.
- Anything outside `C:\EEPort\`.
- Touching the VMware VM **EE-VM**.
- Deleting anything you didn't create.
- Long runs that take over the mouse and keyboard. The user may be using this laptop, so announce them first.

## Game facts and gotchas
- **Network Adapter Options:** leave it on default or the first entry. An index ≥ 2 crashes the original game (a heap bug in `TCP Protocol.dll`; see `ee-macos\docs\reference\netcode.md`).
- **Screenshots:** normal screenshots of the game come out black with GOG's DirectDraw wrapper. Use ffmpeg `ddagrab` (`session.ps1 -Action grab`).
- **Mouse automation:** legacy DirectInput takes mouse deltas from the clipped cursor, so re-centre before moves (`session.ps1` does this).
- **Frida:** never load `iat_census` at spawn. Several crash causes are documented under "Gotchas" in `ee-macos\windows\README.md`.
- **Crash dumps:** WER LocalDumps is enabled for `Empire Earth.exe` and writes to `C:\EEPort\captures\dumps\`.
- **One instance only:** run one game instance at a time, because the UDP ports can't be shared. The VMware host adapters VMnet1/VMnet8 are disabled on purpose.
