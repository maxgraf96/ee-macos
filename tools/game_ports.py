"""One game server at a time on this machine.

A game that opens its server (a random map, the Scenario Editor, a lobby host)
binds UDP 33334 and TCP 33335/33336, fixed in TCP Protocol.dll and
WONLobby.cfg. A second instance on the same Mac gets WSAEADDRINUSE and faults
in the engine (NESynchronousServer::ProcessLocalUserInput). Test runners that
start such a game - in any checkout or worktree - hold this lock for the
game's lifetime:

    from game_ports import game_ports_lock
    with game_ports_lock("menus random-map"):
        subprocess.run([... ee_boot ...])

The lock is an flock on a file in the user's temporary directory, so it is
shared by every process of this user and released if the holder dies.
"""
import contextlib
import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path

LOCK = Path(tempfile.gettempdir()) / "ee-game-ports.lock"


@contextlib.contextmanager
def game_ports_lock(who, timeout=None):
    """Wait until no other game server runs here, then hold the ports.

    By default the wait blocks in the kernel (flock), so waiters are served as
    the lock frees rather than racing a polling loop. With timeout (seconds) it
    polls instead and raises TimeoutError when the time is up."""
    with open(LOCK, "a+") as f:
        start = time.monotonic()
        told = False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout is None:
                    f.seek(0)
                    holder = f.read().strip() or "another run"
                    print("game ports busy (%s); waiting" % holder, file=sys.stderr, flush=True)
                    fcntl.flock(f, fcntl.LOCK_EX)
                    break
                if not told:
                    f.seek(0)
                    holder = f.read().strip() or "another run"
                    print("game ports busy (%s); waiting" % holder, file=sys.stderr, flush=True)
                    told = True
                if time.monotonic() - start > timeout:
                    raise TimeoutError("game ports still busy after %d s: %s" % (timeout, LOCK))
                time.sleep(2)
        f.seek(0)
        f.truncate()
        f.write("%s, pid %d\n" % (who, os.getpid()))
        f.flush()
        try:
            yield
        finally:
            f.seek(0)
            f.truncate()
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
