#!/bin/sh
# Stage the EE-VM (second Windows peer for LAN captures) on legion: unattend CD, empty disk, .vmx.
# Usage: windows/vm/build.sh '<Windows path of the Windows 11 x64 ISO on legion>'
# Env: HOST (default legion), EDITION (default "Windows 11 Pro"), STATE (dir for the generated
#      password + CD image; default $TMPDIR/ee-vm, never inside the repo).
# Result: C:\EEPort\vm\EE-VM\EE-VM.vmx; open it in VMware Workstation and power on (press a key
# at "Press any key to boot from CD"). See windows/vm/README.md.
set -eu
WIN_ISO=${1:?Windows path of the Windows 11 ISO on legion}
HOST=${HOST:-legion}
EDITION=${EDITION:-Windows 11 Pro}
HERE=$(cd "$(dirname "$0")" && pwd)
STATE=${STATE:-${TMPDIR:-/tmp}/ee-vm}
PUBKEY=${PUBKEY:-$HOME/.ssh/id_rsa.pub}
VMDIR='C:\EEPort\vm\EE-VM'
VMWARE='C:\Program Files (x86)\VMware\VMware Workstation'

mkdir -p "$STATE/cd"
[ -f "$STATE/password" ] || LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20 > "$STATE/password"
PASSWORD=$(cat "$STATE/password")

sed -e "s|@PASSWORD@|$PASSWORD|g" -e "s|@EDITION@|$EDITION|g" "$HERE/autounattend.xml" > "$STATE/cd/autounattend.xml"
cp "$HERE/firstlogon.ps1" "$STATE/cd/"
cp "$PUBKEY" "$STATE/cd/authorized_keys"
rm -f "$STATE/autounattend.iso"
hdiutil makehybrid -quiet -iso -joliet -default-volume-name EEUNATTEND -o "$STATE/autounattend.iso" "$STATE/cd"

ssh "$HOST" "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force '$VMDIR' | Out-Null\""
scp -q "$STATE/autounattend.iso" "$HOST:C:/EEPort/vm/EE-VM/autounattend.iso"
sed -e "s|@WINISO@|$(printf '%s' "$WIN_ISO" | sed 's/[\\&|]/\\&/g')|" "$HERE/EE-VM.vmx.in" > "$STATE/EE-VM.vmx"
scp -q "$STATE/EE-VM.vmx" "$HOST:C:/EEPort/vm/EE-VM/EE-VM.vmx"
# 64 GB growable single-file disk; only created once.
ssh "$HOST" "powershell -NoProfile -Command \"if (-not (Test-Path '$VMDIR\\EE-VM.vmdk')) { & '$VMWARE\\vmware-vdiskmanager.exe' -c -s 64GB -a lsilogic -t 0 '$VMDIR\\EE-VM.vmdk' }; Get-ChildItem '$VMDIR' | Format-Table Name, Length -AutoSize | Out-String\""
echo "[ee-vm] user ee, password in $STATE/password"
