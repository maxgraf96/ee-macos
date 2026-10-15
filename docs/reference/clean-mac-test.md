# Testing a release on a clean Mac

Status: Phase 6, P6-D, 2026-10-15, for **release 0.7** (`EmpireEarth-0.7.dmg`, build 234.2610151640, ee-macos ebb8302, kit 614be22). This is the first test on a Mac that has never had Xcode, the Command Line Tools, Homebrew or this repository. Until now every run used the packager's Mac, where the developer tools exist even when a sandbox hides them ([packaging.md](packaging.md) §8). The player's guide is [INSTALL.md](../INSTALL.md); this page says what to do on that Mac and what to report.

## 0. What to take to the other Mac

| | Where | Size |
|---|---|---|
| `EmpireEarth-0.7.dmg` | `/Users/max/Code/ee-build-cache/release/0.7/` | 74.5 MB |
| `SHA256SUMS` | the same folder | |
| GOG's installer | `~/Downloads/setup_empire_earth_gold_2.0.0.2974_gog_v3_(78415).exe`, or download it on that Mac from gog.com → Account → Games → Empire Earth Gold → *Download offline backup game installers* → Windows | 667 MB |

- **Copy them by AirDrop or download them, not with a USB stick.** A file that arrives that way is marked as downloaded (`com.apple.quarantine`), as it would be for a friend. That mark is what this test is about.
- Optional check, in Terminal on the other Mac: `shasum -a 256 ~/Downloads/EmpireEarth-0.7.dmg` should print `264c9c3ea35129b29b313b960267b69cc24e241a62f5e63a7d2e71a8aafb396a`.
- Note the Mac's model, chip, memory and macOS version (Apple menu → *About This Mac*).

## 1. Install

1. Double-click `EmpireEarth-0.7.dmg`. **Report** any dialog that appears when it opens.
2. The window shows **EmpireEarth**, an **Applications** folder and **Read Me First.txt**. Open the Read Me: does it read well?
3. Drag **EmpireEarth** onto **Applications**. Eject the disk image.

## 2. First open: Gatekeeper

1. Open **EmpireEarth** from Applications. Expected: **"EmpireEarth" Not Opened**. Click **Done**, not *Move to Trash*.
2. System Settings → **Privacy & Security** → scroll down → **Open Anyway** next to "EmpireEarth" was blocked. Enter the password, then **Open Anyway** again.
3. Expected: the first-run window, "Welcome to Empire Earth for Mac". **No further Gatekeeper dialog.**

**Report every dialog:** a screenshot (⇧⌘4, then Space, then click the dialog), and above all **the name it shows**.
- *EmpireEarth* is the expected one.
- Anything else, such as *python3*, *clang*, *ld64.lld*, *llvm*, *innoextract* or *EmpireEarthHost*, means macOS assessed a program inside the app. The launcher clears the download mark from its own files before it starts any of them (packaging.md §5.1), so that should not happen. If it does: click **Done**, then do §6's fix and write down where it happened.

## 3. The first run: import and build

1. Click **Choose…** and pick the GOG installer. Alternatively, click the suggestion for it if one is shown (the app looks in Downloads; macOS may first ask whether EmpireEarth may access the Downloads folder: **Allow**).
2. **Write down the clock** in the window, for example "Step 1 of 2: … — 0:41", at the end of each step:
   - **Step 1, importing:** unpacking the installer, checking and copying 294 files. It took 3 s here, with the installer already in the disk cache; allow up to a minute.
   - **Step 2, building:** translating and compiling the game for this Mac. It takes about 2–3 min on an M4 Max; the guess for a base M1 is 7–10 min.
3. Note anything odd: errors, a long stall in one phase, fans, the Mac becoming sluggish.
4. When the build is done, the window closes and the game starts by itself.

## 4. The first game

1. **Local Network.** macOS 15 and later ask whether EmpireEarth may "find and connect to devices on your local network". **Report whether it appeared, and when:** at the first start, at the main menu, or only in Multiplayer. Click **Allow**.
2. **Firewall.** macOS may ask whether EmpireEarth may accept incoming connections, the first time you host or join. Report it; click **Allow**.
3. **Single player:** Single Player → **Play Random Map**. Play for about five minutes: build, train units, scroll around. Save the game, then quit (⌘Q).
4. **Second start:** open the app again. Expected: straight into the game, with no window and no dialog. Load the saved game.

## 5. A LAN game

Both Macs (or the other Mac and legion) on the same Wi-Fi. The steps are in [INSTALL.md](../INSTALL.md) §5.1.
- **Against this Mac:** quit any agent games first; this Mac runs the installed app.
- **Against legion:** Windows, GOG game; leave *Network Adapter Options* at its default.

Host on one, join **from the list** (Multiplayer → Other Multiplayer) on the other. Direct Connect does not work with GOG keys. Play until the civilization choice (about 3 min in) and a little beyond. **Report:**
- who hosted;
- how long it took for the game to appear in the list;
- whether both sides reached the game;
- any out-of-sync message, lag (the snail icon) or crash.

Then swap host and joiner if there is time.

## 6. If something goes wrong

| What you see | What to do |
|---|---|
| A Gatekeeper dialog naming something other than EmpireEarth | **Done**, then in Terminal: `xattr -cr /Applications/EmpireEarth.app`, and open the app again |
| The window says **"macOS is blocking part of this app."** | Run the Terminal line it shows (the same `xattr -cr`), then **Try Again** |
| Terminal answers *Operation not permitted* | System Settings → Privacy & Security → **App Management** (or Full Disk Access): allow Terminal, then run it again. Report this: the app could not clear its own files either |
| "EmpireEarth is damaged and can't be opened" | `xattr -cr /Applications/EmpireEarth.app`, then open it again; report |
| The window says what failed (an import or build error) | **Show Log**; send the log |
| The game stops | send `game.log` (below) |

## 7. What to send back

The files, from `~/Library/Application Support/EmpireEarth/` (in Finder: Go → Go to Folder…, paste the path):
- `Logs/launcher.log`: the launcher's lines. The first ones should read like this:
  ```
  launcher 0.7 (234.2610151640) at /Applications/EmpireEarth.app
  quarantine: cleared from 1319 files (of 1319 in the app, 0.05 s)
  ```
  A `keep it (first …: Operation not permitted)` on that line is important to report.
- `Logs/game.log` and `Logs/game.previous.log`: the game's output, last run and the one before;
- `Build/firstrun.log`: the import and the build, with each phase's time.

And, in Terminal, after the first run:

```sh
/Applications/EmpireEarth.app/Contents/MacOS/EmpireEarth --check-install
xattr -lr /Applications/EmpireEarth.app | grep -c com.apple.quarantine
```

The first should say `"quarantined":0,"translocated":false`, the second `0`.

**The report:**

| | |
|---|---|
| Mac (model, chip, memory), macOS version | |
| Opening the DMG: any dialog? | |
| Gatekeeper dialogs: how many, wording, the name each showed | |
| Downloads access prompt? | |
| Step 1 (import) time / Step 2 (build) time | |
| Local Network prompt: appeared? when? | |
| Firewall prompt? | |
| Single-player game, save, second start straight into the game, load | |
| LAN game: against whom, who hosted, result | |
| `launcher.log`'s quarantine line, `--check-install` | |
| Anything else odd | |

## 8. If there is time: the other ways in

- **Opened from the disk image without dragging it** (first move the copy in Applications to the Bin):
  - Expected: Gatekeeper as in §2, then **"Move Empire Earth to Applications"** with *Move to Applications* and *Quit*.
  - *Move to Applications* copies it there and opens it from there: the first-run window, or the game.
  - Report the alert, and whether the copy in Applications then opens with no Gatekeeper dialog.
- **The same with a copy already in Applications:** the alert's move replaces it. Report if it says it *cannot be replaced* (macOS's App Management may refuse it).
- **The zip** instead of the DMG: double-click it in Downloads; open the app from Downloads; expect the same alert.

Afterwards keep the app, or remove everything as in [INSTALL.md](../INSTALL.md) §7. The data folder holds a copy of the game and the CD key.
