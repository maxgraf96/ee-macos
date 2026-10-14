#!/bin/sh
# Create or refresh the Windows operator workspace on legion (run on the Mac).
#   C:\EEPort\workspace\CLAUDE.md          role instructions (from windows/legion-agent/CLAUDE.md)
#   C:\EEPort\workspace\mailbox\{to-legion,to-mac,done}\
#   C:\EEPort\workspace\ee-macos\          clone of this repo, branch legion/work
# Re-running refreshes CLAUDE.md and the clone's bundle/main ref; it never touches legion/work.
# It also configures a `legion` remote in this repo to fetch the agent's commits over SSH.
# Legion's OpenSSH shell is cmd, which keeps git's single quotes around the path, so the
# upload-pack command carries the path itself and `& rem` turns git's quoted argument into a comment.
# All remote commands are single-line cmd commands for the same reason.
set -eu
HOST=${HOST:-legion}
WS='C:\EEPort\workspace'
REPO=$(cd "$(dirname "$0")/../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ssh "$HOST" "if not exist $WS\\mailbox\\to-legion mkdir $WS\\mailbox\\to-legion & if not exist $WS\\mailbox\\to-mac mkdir $WS\\mailbox\\to-mac & if not exist $WS\\mailbox\\done mkdir $WS\\mailbox\\done"
scp -q "$REPO/windows/legion-agent/CLAUDE.md" "$HOST:C:/EEPort/workspace/CLAUDE.md"

git -C "$REPO" bundle create "$TMP/ee-macos.bundle" main >/dev/null 2>&1
scp -q "$TMP/ee-macos.bundle" "$HOST:C:/EEPort/workspace/ee-macos.bundle"
R="$WS\\ee-macos"
ssh "$HOST" "if exist $R\\.git (git -C $R fetch -q $WS\\ee-macos.bundle main:refs/remotes/bundle/main) else (git -c core.autocrlf=false clone -q -b main $WS\\ee-macos.bundle $R && git -C $R config core.autocrlf false && git -C $R remote rename origin bundle && git -C $R checkout -q -b legion/work)"
ssh "$HOST" "del $WS\\ee-macos.bundle & git -C $R branch --show-current & git -C $R log --oneline -1 bundle/main"

git -C "$REPO" remote get-url legion >/dev/null 2>&1 || git -C "$REPO" remote add legion "$HOST:C:/EEPort/workspace/ee-macos"
git -C "$REPO" config remote.legion.uploadpack "git upload-pack C:/EEPort/workspace/ee-macos & rem"
git -C "$REPO" fetch -q legion && git -C "$REPO" branch -r | grep legion/
