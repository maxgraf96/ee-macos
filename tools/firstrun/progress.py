"""The first run's two outputs: JSON lines on stdout for the app, and a human
log (docs/reference/firstrun.md §5).

Every stdout line is one JSON object with at least

    {"phase": str, "done": int, "total": int, "message": str}

plus "event": "progress" | "warning" | "error" | "result", and
- "unit" ("files", "bytes", "modules", "objects", "steps") with progress;
- "code" (a stable machine code, ERROR_CODES) with an error;
- "ok" (bool) and "data" (the command's result) with the result, which is
  always the last line.
The human log (<home>/Build/firstrun.log) gets the same events as text, with
times. Nothing here ever receives the CD key: callers pass only messages.
"""
import json
import sys
import time

EVENTS = ("progress", "warning", "error", "result")
UNITS = ("files", "bytes", "modules", "objects", "steps")
# Stable error codes the app can map to its own wording (firstrun.md §5).
ERROR_CODES = {
    "usage": "bad command line",
    "busy": "another first run holds the lock",
    "not-found": "no Empire Earth game files at the path",
    "aoc-only": "the folder is The Art of Conquest, not the base game",
    "wrong-version": "the game files are not GOG's Empire Earth Gold",
    "partial-copy": "files are missing or short",
    "modified-files": "files differ from GOG's",
    "installer-unreadable": "the installer cannot be read",
    "installer-parts-missing": "the installer's .bin parts are missing",
    "installer-tool-missing": "no innoextract binary",
    "not-imported": "build before import",
    "disk-space": "not enough free disk space",
    "toolchain-missing": "no clang, ld64.lld or Python modules",
    "inputs-mismatch": "the translation inputs differ from the pinned ones",
    "translate-failed": "the translator failed",
    "compile-failed": "compiling or linking failed",
    "interrupted": "stopped by a signal; run the same command again to resume",
    "internal": "a bug in the first run",
}


class Failure(Exception):
    """An error the player can act on: a code from ERROR_CODES and a sentence."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        assert code in ERROR_CODES, code
        self.code = code
        self.message = message
        self.data = data or {}


class Reporter:
    def __init__(self, stream=None, log_path=None, human=None):
        self.stream = stream if stream is not None else sys.stdout
        self.human = human  # a text stream for people (stderr), or None
        self.log_path = None
        self._log = None
        self._last = {}
        self.t0 = time.time()
        if log_path is not None:
            self.open_log(log_path)

    def open_log(self, path):
        if self._log is not None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = path
        self._log = open(path, "a", encoding="utf-8")
        self._log.write("\n==== %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        self._log.flush()

    def close(self):
        if self._log is not None:
            self._log.close()
            self._log = None

    def log(self, text):
        """A line for the human log only."""
        if self._log is not None:
            self._log.write("%7.1f  %s\n" % (time.time() - self.t0, text))
            self._log.flush()

    def _emit(self, obj, text):
        self.stream.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
        self.stream.flush()
        self.log(text)
        if self.human is not None:
            self.human.write(text + "\n")
            self.human.flush()

    def progress(self, phase, done, total, message, unit="steps", force=True, **extra):
        """A progress line; with force=False, at most one per phase per 0.5 s
        unless the phase completes."""
        now = time.time()
        if not force and done < total and now - self._last.get(phase, 0) < 0.5:
            return
        self._last[phase] = now
        obj = {"event": "progress", "phase": phase, "done": int(done), "total": int(total),
               "message": message, "unit": unit}
        obj.update(extra)
        self._emit(obj, "[%s] %s (%d/%d %s)" % (phase, message, done, total, unit))

    def warning(self, phase, message, **extra):
        obj = {"event": "warning", "phase": phase, "done": 0, "total": 0, "message": message}
        obj.update(extra)
        self._emit(obj, "[%s] WARNING: %s" % (phase, message))

    def error(self, phase, code, message, data=None):
        obj = {"event": "error", "phase": phase, "done": 0, "total": 0, "message": message,
               "code": code}
        if data:
            obj["data"] = data
        self._emit(obj, "[%s] ERROR (%s): %s" % (phase, code, message))

    def result(self, phase, ok, message, data=None):
        obj = {"event": "result", "phase": phase, "done": 1 if ok else 0, "total": 1,
               "message": message, "ok": bool(ok), "data": data or {}}
        self._emit(obj, "[%s] %s: %s" % (phase, "OK" if ok else "FAILED", message))
