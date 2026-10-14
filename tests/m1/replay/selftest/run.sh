#!/bin/sh
# Replay-core self-test on the host: synthetic capture -> rmg_pack.py -> pack -> core + mock platform.
# Expects PASS for the faithful capture and a MISMATCH for one with a corrupted expected byte.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/rmg-selftest.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
${CC:-cc} -std=c99 -Wall -Wextra -O1 -I"$here/.." "$here/selftest.c" "$here/../rmg_replay_core.c" -o "$tmp/selftest"
python3 -I "$here/make_capture.py" "$tmp/good"
python3 -I "$here/make_capture.py" "$tmp/bad" --break
python3 -I "$here/../rmg_pack.py" pack "$tmp/good" -o "$tmp/good-packs" --verify
python3 -I "$here/../rmg_pack.py" pack "$tmp/bad" -o "$tmp/bad-packs" --verify
"$tmp/selftest" "$tmp/good-packs/000000-CreateFlatMap.rrp"
if "$tmp/selftest" "$tmp/bad-packs/000000-CreateFlatMap.rrp"; then
    echo "selftest: the corrupted capture was not detected" >&2
    exit 1
fi
echo "selftest: ok"
