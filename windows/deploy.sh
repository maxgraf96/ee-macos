#!/bin/sh
# Push the GOG game folder (if missing) and these scripts to legion, then run setup.ps1.
# Usage: windows/deploy.sh [--game] [-- <extra setup.ps1 args, e.g. -ResetRegistry -WithX64dbg>]
#   --game   (re)copy the game folder even if it already exists on legion
# Env: HOST (default legion), GOG_DIR (default original/gog), GOG_SCRIPT (GOG install script).
set -eu
HOST=${HOST:-legion}
REPO=$(cd "$(dirname "$0")/.." && pwd)
GOG_DIR=${GOG_DIR:-$REPO/original/gog}
GOG_SCRIPT=${GOG_SCRIPT:-"/Applications/Empire Earth.app/Contents/drive_c/GOG Games/Empire Earth Gold/goggame-1207658777.script"}

COPY_GAME=0
while [ $# -gt 0 ]; do
  case $1 in
    --game) COPY_GAME=1; shift ;;
    --) shift; break ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

ssh "$HOST" 'powershell -NoProfile -Command "foreach($d in \"game\",\"tools\",\"captures\",\"scripts\",\"frida\"){New-Item -ItemType Directory -Force C:\EEPort\$d | Out-Null}"'

if [ $COPY_GAME = 1 ] || ! ssh "$HOST" 'if exist "C:\EEPort\game\Empire Earth\Empire Earth.exe" (exit 0) else (exit 1)'; then
  echo "[deploy] copying game folder (~400 MB)"
  # tar over ssh; no AppleDouble/xattr files; Windows' bsdtar unpacks it.
  (cd "$GOG_DIR" && COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -cf - "Empire Earth") \
    | ssh "$HOST" "tar -xf - -C C:/EEPort/game"
fi
[ -f "$GOG_SCRIPT" ] && scp -q "$GOG_SCRIPT" "$HOST:C:/EEPort/game/goggame-1207658777.script"

scp -q "$REPO"/windows/*.ps1 "$REPO"/windows/*.vbs "$HOST:C:/EEPort/scripts/"
scp -q "$REPO"/windows/frida/*.js "$REPO"/windows/frida/*.py "$HOST:C:/EEPort/frida/"

ssh "$HOST" "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\EEPort\\scripts\\setup.ps1 $*"
