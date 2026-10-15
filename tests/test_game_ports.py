"""tools/game_ports.py: one game server at a time on this machine."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import game_ports  # noqa: E402


def test_a_second_holder_waits_and_times_out(tmp_path, monkeypatch):
    lock = tmp_path / "ports.lock"
    monkeypatch.setattr(game_ports, "LOCK", lock)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, %r); import game_ports; game_ports.LOCK = %r\n"
         "with game_ports.game_ports_lock('holder'):\n"
         "    print('held', flush=True); time.sleep(30)" % (str(ROOT / "tools"), str(lock))],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert "holder" in lock.read_text()
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with game_ports.game_ports_lock("second", timeout=1):
                pass
        assert time.monotonic() - start >= 1
    finally:
        holder.kill()
        holder.wait()
    # A holder that died releases the lock.
    with game_ports.game_ports_lock("third", timeout=5):
        assert "third" in lock.read_text()
    assert lock.read_text() == ""
