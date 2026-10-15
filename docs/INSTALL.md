# Empire Earth for Mac: installing and first start

This app runs **your own copy of Empire Earth Gold from GOG** natively on an Apple Silicon Mac. It contains none of the game. The first time you open it, it copies your GOG files, checks them and builds the game for your Mac, which takes a few minutes. After that it starts straight into the game.

## What you need

- A Mac with Apple Silicon (M1 or later) and macOS 13 Ventura or later.
- **Empire Earth Gold from GOG** (gog.com). Other editions (the 2001 CD, Steam, NeoEE) are not supported. The Art of Conquest is not supported either: only the base game is used.
- About **1.5 GB of free disk space** during the first start, and about 0.6 GB afterwards.

## 1. Download the game from GOG

1. Sign in at gog.com and open **Account → Games → Empire Earth Gold**.
2. Under **Download offline backup game installers**, choose **Windows** and **English**.
3. Download **all parts** into the same folder (Downloads is fine):
   - `setup_empire_earth_gold_<version>.exe`;
   - and every `setup_empire_earth_gold_<version>-1.bin`, `-2.bin` … beside it.

You do not need Windows or Wine: the app unpacks the installer itself.

**Already installed it?** The app also takes:
- the installed `Empire Earth Gold` folder (copied from a Windows PC, a USB stick or a network share);
- a Wine app that has it (Wineskin, Sikarugir, Kegworks, Porting Kit), or a CrossOver or Whisky bottle.

Your files are only read, never changed.

## 2. Install the app

- **From `EmpireEarth.dmg`:** open it and drag **EmpireEarth** onto **Applications**.
- **From `EmpireEarth.zip`:** open it, then drag the **EmpireEarth** app into your Applications folder.

The app is called **EmpireEarth**, with no space. So it never replaces a Wine app called “Empire Earth” that holds your game.

## 3. Open it the first time: “Open Anyway”

The app is not notarized by Apple: that needs a paid developer account. So macOS stops it the first time. This is expected, and you only do it once:

1. Double-click **EmpireEarth**. macOS says **“EmpireEarth” Not Opened** (or “Apple could not verify…”). Click **Done**. Do not click “Move to Trash”.
2. Open **System Settings → Privacy & Security** and scroll down to **Security**.
3. Next to **“EmpireEarth” was blocked to protect your Mac**, click **Open Anyway**. Enter your password if asked.
4. In the dialog that follows, click **Open Anyway** again.

On macOS 13 and 14 you can instead **Control-click** the app in Finder, choose **Open**, then **Open** in the dialog.

**Why it is safe to allow:** the app is signed, so macOS notices if anything in it changes. It only reads the game files you choose. It writes only to its own folder (step 6) and connects to nothing on the internet.

## 4. The first start

A small window asks for your game:

- **Click a suggestion.** The app looks in the usual places: your Applications folder, CrossOver and Whisky, Downloads and Desktop. macOS may ask whether it may look in Downloads or Desktop.
- **Or click “Choose…”** and pick the `setup_empire_earth_gold_….exe`, the `Empire Earth Gold` folder, or the Wine app.
- **Or drag** any of them onto the window.

Then one progress bar shows two steps:

1. **Importing your game files.** The files are checked and copied: a few seconds from an installed game, about half a minute from the installer.
2. **Building the game for your Mac.** This takes about 2–3 minutes on a recent Mac and up to about 10 on a base M1. The Mac's fans may spin up.

You can quit at any time (**Cancel**, or ⌘Q). The next time you open the app, it carries on where it stopped. When the build is done, the game starts.

**From then on, the app starts the game directly.** After an update of the app it may build once more.

### If something is wrong

The window says what happened and what to do. The usual cases:

| Message | What to do |
|---|---|
| That is not GOG's Empire Earth Gold | Use GOG's installer or a fresh GOG installation: CD, Steam, patched and NeoEE copies are not supported. |
| That is The Art of Conquest | Choose the `Empire Earth` folder next to it, or the whole `Empire Earth Gold` folder. |
| The game folder is incomplete | Copy the whole folder again, or use the installer. |
| Some of the game files have been changed | Use an unmodified copy. A modified game goes out of sync with other players. |
| Parts of the installer are missing | Download the `.exe` **and all its `.bin` files** into the same folder, then choose the `.exe`. |
| The installer cannot be read | Download it again from GOG. |
| There is not enough free disk space | Free up the space shown, then click **Try Again**. |
| Building the game failed | Click **Try Again**. If it keeps failing, click **Show Log** and send the log to whoever gave you the app. |

## 5. Playing

- **The mouse:** click in the window to capture it. Hold **Escape** to release it.
- **The intro movies:** press **Escape** to skip them.
- **Quitting:** ⌘Q, or Exit Game in the menu.
- **Settings:** F10 is the game's own menu, as on Windows. The app's settings (window, performance overlay) open with **⌘,** (Command-comma); **F11** shows or hides the frame rate.
- **Multiplayer:** the game's own LAN play, with other Macs and with Windows players who have the GOG game (§5.1). For games over the internet, use a VPN that passes the game's LAN broadcasts, such as ZeroTier (Tailscale passes none): the game list is the only way to join with GOG copies (§5.1 step 5).

### 5.1 Playing with Windows players on a LAN

The Mac plays the unmodified Windows GOG game: either side can host. Tested on a home Wi-Fi with one Mac and one Windows 11 PC: a game each way with the developer build, and in this app a game joined from the PC and a game hosted for it, played to the end.

1. **Same network.** Both computers on the same home network (Wi-Fi or cable). Everyone needs the **GOG** Empire Earth Gold: the game compares its files and refuses a different version.
2. **Local network access (Mac).** The first time the game uses the network, macOS 15 and later asks whether **EmpireEarth** may find devices on your local network. Click **Allow**.
   - Without it, nobody sees the games you host, you see only some of theirs, and **Join Game does nothing**.
   - If you clicked *Don't Allow*: **System Settings → Privacy & Security → Local Network**, turn on **EmpireEarth**, then quit and reopen the game.
   - macOS may ask again after you install a new version of the app.
3. **Firewalls.**
   - **Mac:** the first time you host or join, macOS may ask whether EmpireEarth may accept incoming connections. Click **Allow**.
   - **Windows:** Windows asks the same the first time; tick **Private networks** and click **Allow**. The Wi-Fi must be a *Private* network in Windows' settings.
4. **Windows players: leave Network Adapter Options at its default** (or the first entry). Choosing another adapter crashes the original game when the match starts. If the Windows PC has VMware, VirtualBox, Hyper-V or VPN adapters, the game may announce itself on one of those, and nobody sees it: disable the adapters you don't need while playing.
5. **Host:** Multiplayer → **Other Multiplayer** → **Host Game**, type a name, **Create Game**. Leave the options as they are or change them; only the host can. Computer players: set a free slot to **Computer**. The game refuses to start when every player is on the same team.
6. **Join:** Multiplayer → **Other Multiplayer**. The game appears in the list within a few seconds (click **Refresh List** if not). Select it, **Join Game**.
   - **Direct Connect does not work with GOG copies.** Adding the host's address there lists the game, but **Join Game** answers "Invalid CDKey" at once, on Windows and on the Mac alike: the joining game refuses before it contacts the host. Use the list.
   - If the game never appears in the list: check step 2 on the Mac, and step 4 on Windows.
7. **In the room:** chat in the line under the history. Every player who joined clicks **Ready**; then the host's **Start Game** becomes clickable.
8. **In the game:** **F10** opens the game menu (Resign, Quit this Game). About three minutes in, the game asks each player to choose a civilization. The game goes on meanwhile, so choose one and click **OK**; if you don't, it chooses for you when the time is up. After the game, the statistics screen leads back to the lobby.
9. **Speed:** the game runs at the speed of the slowest computer. A snail icon at the top right means someone is lagging.

Everyone may use the same GOG CD key when joining from the list: the game does not refuse it on a LAN.

## 6. Where your files are

Everything is in **`~/Library/Application Support/EmpireEarth/`**. To open it, choose **Go → Go to Folder…** in Finder and paste the path.

| | |
|---|---|
| `Game/` | Your checked copy of the game files. Copies from a Wine app on the same disk share its space and take almost none. |
| `Build/` | The game built for your Mac, about 180 MB. |
| `registry.json` | The game's settings and your CD key, read from your own GOG files. It never leaves your Mac. |
| `Data/`, `Users/` | Saved games, scenarios and player profiles. |
| `Logs/` | The app's and the game's logs, for when something goes wrong. |

Nothing is written into the app itself, and nothing outside this folder.

## 7. Removing everything

1. Quit the game.
2. Drag **EmpireEarth** from Applications to the Bin.
3. Drag the folder `~/Library/Application Support/EmpireEarth` to the Bin. **This deletes your saved games**, so keep `Data/Saved Games` if you want them.

macOS may also have kept `~/Library/Saved Application State/dev.neoee.empireearth.savedState`, which you can delete too. Nothing else is installed: no login items, no system files, no helpers.
