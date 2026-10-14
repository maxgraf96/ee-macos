#!/bin/sh
# Create or refresh the Windows operator workspace on legion (run on the Mac).
#   C:\EEPort\workspace\CLAUDE.md          role instructions (from windows/legion-agent/CLAUDE.md)
#   C:\EEPort\workspace\mailbox\{to-legion,to-mac,done}\
#   C:\EEPort\workspace\ee-macos\          clone of this repo, branch legion/work
# Re-running refreshes CLAUDE.md and fast-forwards the clone's main; it never touches legion/work.
set -eu
HOST=${HOST:-legion}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ssh "$HOST" "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force C:\\EEPort\\workspace\\mailbox\\to-legion, C:\\EEPort\\workspace\\mailbox\\to-mac, C:\\EEPort\\workspace\\mailbox\\done | Out-Null\""
scp -q "$REPO/windows/legion-agent/CLAUDE.md" "$HOST:C:/EEPort/workspace/CLAUDE.md"

git -C "$REPO" bundle create "$TMP/ee-macos.bundle" main >/dev/null 2>&1
scp -q "$TMP/ee-macos.bundle" "$HOST:C:/EEPort/workspace/ee-macos.bundle"
ssh "$HOST" "powershell -NoProfile -Command \"
  \$ErrorActionPreference = 'Stop'
  \$r = 'C:\\EEPort\\workspace\\ee-macos'
  if (-not (Test-Path \$r)) {
    git -c core.autocrlf=false clone -q -b main C:\\EEPort\\workspace\\ee-macos.bundle \$r
    git -C \$r config core.autocrlf false
    git -C \$r remote rename origin bundle
    git -C \$r checkout -q -b legion/work
  } else {
    git -C \$r fetch -q bundle main:refs/remotes/bundle/main
  }
  Remove-Item C:\\EEPort\\workspace\\ee-macos.bundle
  git -C \$r branch --show-current
  git -C \$r log --oneline -1 bundle/main
\""
