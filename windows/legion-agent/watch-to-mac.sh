#!/bin/sh
# Wait for new messages from the legion agent (run on the Mac, usually in the background).
# Polls C:\EEPort\workspace\mailbox\to-mac over SSH and exits as soon as a file appears
# that is not in the seen-list, printing the new names; marks them seen.
# Exits with status 2 after MAX_WAIT seconds without news.
#   SEEN=<file> INTERVAL=30 MAX_WAIT=7200 windows/legion-agent/watch-to-mac.sh
set -u
HOST=${HOST:-legion}
SEEN=${SEEN:-${TMPDIR:-/tmp}/ee-legion-to-mac.seen}
INTERVAL=${INTERVAL:-30}
MAX_WAIT=${MAX_WAIT:-7200}
touch "$SEEN"
start=$(date +%s)
while :; do
  names=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" 'dir /b C:\EEPort\workspace\mailbox\to-mac' 2>/dev/null | tr -d '\r' | grep -v '^$' || true)
  new=$(printf '%s\n' "$names" | grep -v '^$' | grep -vxF -f "$SEEN" || true)
  if [ -n "$new" ]; then
    printf '%s\n' "$new" >> "$SEEN"
    printf 'new from legion:\n%s\n' "$new"
    exit 0
  fi
  [ $(( $(date +%s) - start )) -ge "$MAX_WAIT" ] && { echo "no news after ${MAX_WAIT}s"; exit 2; }
  sleep "$INTERVAL"
done
