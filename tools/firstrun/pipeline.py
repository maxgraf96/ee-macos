"""Translate and compile the imported game into bundles (docs/reference/firstrun.md §5).

    preflight -> inputs -> translate -> compile (+ link) -> publish -> clean up

Everything happens in <home>/Build/work-<key>/, where <key> is the cache key:
a digest of what the bundles depend on (the nine modules, game.toml,
globals.toml, the pinned inputs, the translator, the runtime headers the
generated C includes, bundle.py and the compiler's identity). An app update
that changes none of those reuses the bundles; one that changes any rebuilds.

The work directory mirrors a game repository, so the kit's tools run
unchanged on it:

    work-<key>/game/game.toml, globals.toml          copies of the app's
    work-<key>/game/original/gog/Empire Earth   ->   <home>/Game/Empire Earth
    work-<key>/game/analysis/kit/<module>/           translation inputs
    work-<key>/parts/<module key>/                   one translation each
    work-<key>/gen/  (+ aux-<key>/)                  the assembled translation
    work-<key>/bundles/                              bundle.py's objects and bundles

Each step leaves a complete marker behind (a module's funcdb.json, a
translation's renamed directory, bundle.py's per-object stamps), so a run
that is stopped resumes where it was. The published result is
<home>/Build/<key>/ (the bundles, symbols.json, build.json) and
<home>/Build/current.json naming it; the work directory is then deleted.
"""
import concurrent.futures
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import paths
import verify
from progress import Failure

SCHEMA = 1
MIN_MACOS = "13.0"
# What the generated C includes; their include closure is part of the key.
GEN_HEADERS = ("x86.h", "intrinsics.h", "seh.h", "thunks.h", "freestanding.h")
# The peak under Build/ of a fresh build: 744 MB measured (firstrun.md §5.4),
# the C, the objects and the bundles at the end of the compile.
PEAK_DISK = 800 << 20
MARGIN = 200 << 20
DEFAULT_CC = ("/opt/homebrew/opt/llvm/bin/clang", "clang")
DEFAULT_LLD = ("/opt/homebrew/opt/lld/bin/ld64.lld", "ld64.lld")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_path(p):
    return verify.sha256_file(p)


# -------------------------------------------------------------- toolchain --

class Toolchain:
    """Python, clang and ld64.lld: every one injectable (P6-B1 bundles them)."""

    def __init__(self, python=None, cc=None, lld=None, kit=None):
        self.python = str(python or sys.executable)
        self.kit = paths.kit_dir(kit)
        self.cc = self._find(cc, "RECOMP_FREESTANDING_CC", DEFAULT_CC)
        self.lld = self._find(lld, "RECOMP_LD64_LLD", DEFAULT_LLD)

    @staticmethod
    def _find(explicit, env, defaults):
        for c in (explicit, os.environ.get(env)) + defaults:
            if not c:
                continue
            p = shutil.which(c) if os.sep not in c else c
            if p and Path(p).is_file():
                return p
        return None

    def version(self, tool):
        try:
            out = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return out.splitlines()[0].strip() if out else None

    def nm(self):
        """What bundle.py's undefined-symbol check needs: nothing when it reads
        the Mach-O itself (kit feat/importer), else llvm-nm beside clang or on
        PATH, else a real nm. On a Mac without the Command Line Tools
        /usr/bin/nm is a stub that pops up an installer, so it counts only
        when xcode-select names a developer directory (firstrun.md §8)."""
        try:
            if "def macho_undefined" in (self.kit / "tools/recomp/bundle.py").read_text():
                return "bundle.py (reads the Mach-O)"
        except OSError:
            pass
        beside = Path(self.cc).with_name("llvm-nm") if self.cc else None
        if beside is not None and beside.is_file():
            return str(beside)
        found = shutil.which("llvm-nm")
        if found:
            return found
        nm = shutil.which("nm")
        if nm and nm != "/usr/bin/nm":
            return nm
        try:
            dev = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return nm if nm and dev.returncode == 0 else None

    def python_modules(self):
        """capstone and pefile versions in the translator's Python, or None."""
        code = ("import capstone, pefile, importlib.metadata as m;"
                "print(m.version('capstone'), m.version('pefile'))")
        try:
            r = subprocess.run([self.python, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.split() if r.returncode == 0 else None


# -------------------------------------------------------------- cache key --

def _include_closure(kit):
    """The kit headers the generated C reads: GEN_HEADERS and what they
    include, found in runtime/ or the kit root (bundle.py's -I order)."""
    seen, todo = {}, list(GEN_HEADERS)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = next((d / name for d in (kit / "runtime", kit) if (d / name).is_file()), None)
        if path is None:
            continue
        seen[name] = path
        todo += re.findall(r'^\s*#\s*include\s+"([^"]+)"', path.read_text(errors="replace"), re.M)
    return seen


def key_inputs(tools, defines):
    """Everything the bundles depend on, as a JSON-able dict of digests."""
    root, kit = paths.ROOT, tools.kit
    modules = dict(verify.load_modules(root))
    headers = _include_closure(kit)
    return {
        "schema": SCHEMA,
        "modules": modules,
        "game.toml": sha256_path(root / "game.toml"),
        "globals.toml": sha256_path(root / "globals.toml"),
        "db/inputs.json": sha256_path(root / "db/inputs.json"),
        "translator": {p: sha256_path(kit / p) for p in ("tools/recomp/translate.py", "tools/game_config.py")},
        "headers": {n: sha256_path(p) for n, p in sorted(headers.items())},
        "bundle.py": sha256_path(kit / "tools/recomp/bundle.py"),
        "defines": defines,
        "min_macos": MIN_MACOS,
        "cc": tools.version(tools.cc) if tools.cc else None,
        "lld": tools.version(tools.lld) if tools.lld else None,
    }


def cache_key(inputs):
    return sha256_bytes(json.dumps(inputs, sort_keys=True).encode())[:16]


def guest_defines():
    """bundle.py's -D values, as CMake passes them (kit Translate.cmake,
    gen_game_config.py: c_hex)."""
    import tomllib
    cfg = tomllib.loads((paths.ROOT / "game.toml").read_text())["game"]
    heap = int(cfg.get("heap_base", 0x01000000))
    return ["GUEST_IMAGE_BASE=0x%08xu" % cfg["image_base"], "GUEST_HEAP_BASE=0x%08xu" % heap,
            "GUEST_SIZE=0x%08xu" % cfg["guest_size"]]


# -------------------------------------------------------------- resources --

def memsize():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
    except (OSError, ValueError):
        return 0


def perf_cores():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                                  capture_output=True, text=True).stdout)
    except (OSError, ValueError):
        return os.cpu_count() or 4


def plan_jobs(mem, cores, jobs=None):
    """(compile jobs, translations beside the EXE's, exporter processes).
    The EXE's translation takes 2.0 GB; clang on the largest EXE chunk about
    0.5 GB (firstrun.md §5). 8 GB Macs: 4 compile jobs, one DLL at a time."""
    small = mem and mem <= (8 << 30)
    cj = jobs or (min(cores, 4) if small else cores)
    return max(1, cj), (1 if small else 3), (2 if small else min(cores, 9))


def tree_bytes(path):
    """Disk used under path (allocated blocks: 48,000 small listings take
    three times their size), not following links."""
    total = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_blocks * 512
            except OSError:
                pass
    return total


# ---------------------------------------------------------- child processes --

class Children:
    """Child processes in their own sessions, so a stop takes their
    compilers down too."""

    def __init__(self):
        self.procs = set()
        self.lock = threading.Lock()

    def popen(self, cmd, **kw):
        p = subprocess.Popen(cmd, start_new_session=True, **kw)
        with self.lock:
            self.procs.add(p)
        return p

    def done(self, p):
        with self.lock:
            self.procs.discard(p)

    def kill_all(self):
        with self.lock:
            procs = list(self.procs)
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + 5
        for p in procs:
            try:
                p.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass


# ------------------------------------------------------------------ build --

class Build:
    def __init__(self, home, reporter, tools, jobs=None, keep_work=False):
        self.home, self.r, self.tools = home, reporter, tools
        self.keep_work = keep_work
        self.jobs_arg = jobs
        self.children = Children()
        self.times = {}
        self.disk = {}
        self.runs = 1
        self.previous_wall = 0.0
        self.t_all = time.time()

    # --- setup ---
    def modules(self):
        """[(key, file name)]: "main" for the EXE, then game.toml's aux keys."""
        import tomllib
        cfg = tomllib.loads((paths.ROOT / "game.toml").read_text())
        out = [("main", cfg["game"]["executable"])]
        out += [(k, m["name"]) for k, m in sorted(cfg["modules"]["aux"].items())]
        return out

    def preflight(self):
        r, home, tools = self.r, self.home, self.tools
        steps = 5
        r.progress("preflight", 0, steps, "Checking the imported game")
        if not (home.game / "Empire Earth.exe").is_file():
            raise Failure("not-imported", "Import the game first (firstrun.py import PATH).")
        files = verify.index(home.game)
        verify.check_modules(files, verify.load_modules(), verify.load_manifest())
        r.progress("preflight", 1, steps, "Checking the toolchain")
        missing = [n for n, v in (("clang", tools.cc), ("ld64.lld", tools.lld)) if not v]
        if missing:
            raise Failure("toolchain-missing", "No %s found (set RECOMP_FREESTANDING_CC / "
                          "RECOMP_LD64_LLD, or pass --cc / --lld)." % " or ".join(missing))
        mods = tools.python_modules()
        if mods is None:
            raise Failure("toolchain-missing", "%s cannot import capstone and pefile." % tools.python)
        nm = tools.nm()
        if nm is None:
            raise Failure("toolchain-missing", "No llvm-nm for bundle.py's symbol check (and /usr/bin/nm "
                          "is only the Command Line Tools' installer stub here).")
        self.defines = guest_defines()
        self.inputs = key_inputs(tools, self.defines)
        self.key = cache_key(self.inputs)
        self.work = home.work(self.key)
        r.log("toolchain: python %s (capstone %s, pefile %s); cc %s [%s]; lld %s [%s]; nm %s"
              % (tools.python, mods[0], mods[1], tools.cc, self.inputs["cc"], tools.lld,
                 self.inputs["lld"], nm))
        r.log("cache key %s" % self.key)
        r.progress("preflight", 2, steps, "Checking memory and cores")
        mem, cores = memsize(), os.cpu_count() or 4
        self.compile_jobs, self.translate_slots, self.export_jobs = plan_jobs(mem, cores, self.jobs_arg)
        r.log("memory %.1f GB, %d cores (%d performance): %d compile jobs, %d translations beside "
              "the EXE's, %d exporters" % (mem / 2**30, cores, perf_cores(), self.compile_jobs,
                                           self.translate_slots, self.export_jobs))
        return self.key

    def preflight_disk(self):
        r, home, steps = self.r, self.home, 5
        r.progress("preflight", 3, steps, "Checking free disk space")
        home.build.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(home.build).free
        have = tree_bytes(self.work) if self.work.exists() else 0
        need = max(0, PEAK_DISK - have) + MARGIN
        if free < need:
            raise Failure("disk-space", "Building needs about %d MB of free disk space; %d MB are free "
                          "on this disk." % (need >> 20, free >> 20),
                          {"need_bytes": need, "free_bytes": free})
        r.progress("preflight", 4, steps, "Cleaning up old work")
        for old in home.build.glob("work-*"):
            if old != self.work:
                shutil.rmtree(old, ignore_errors=True)
        r.progress("preflight", steps, steps, "Ready (cache key %s)" % self.key)
        return self.key

    def current(self):
        """The published build's record, or None."""
        try:
            cur = json.loads(self.home.current.read_text())
            rec = json.loads((self.home.build / cur["dir"] / "build.json").read_text())
        except (OSError, ValueError, KeyError):
            return None
        return rec

    def mirror(self):
        """work/game: the game-repository shape the kit's tools expect."""
        game = self.work / "game"
        (game / "original/gog").mkdir(parents=True, exist_ok=True)
        for name in ("game.toml", "globals.toml"):
            shutil.copyfile(paths.ROOT / name, game / name)
        link = game / "original/gog/Empire Earth"
        if not link.is_symlink():
            link.unlink(missing_ok=True)
            link.symlink_to(self.home.game, target_is_directory=True)
        return game

    def load_cfg(self, game):
        sys.path.insert(0, str(self.tools.kit / "tools"))
        import game_config
        return game_config.load(game)

    # --- inputs ---
    def run_inputs(self, game):
        import export  # tools/inputs
        cfg = self.load_cfg(game)
        jobs = [(n, b, o, None, False) for n, b, o in export.modules(cfg)]
        todo = [j for j in jobs if not (Path(j[2]) / "funcdb.json").is_file()]
        total, done = len(jobs), len(jobs) - len(todo)
        pins = export.pinned()
        self.r.progress("inputs", done, total, "Decoding the game's code (%d modules)" % total, unit="modules")
        if not todo:
            return
        todo.sort(key=lambda j: -Path(j[1]).stat().st_size)
        ex = concurrent.futures.ProcessPoolExecutor(max_workers=min(self.export_jobs, len(todo)))
        try:
            futs = {ex.submit(export.run_one, j): j for j in todo}
            for fut in concurrent.futures.as_completed(futs):
                name, rep = fut.result()
                if rep["sha256"] != pins.get(name):
                    raise Failure("inputs-mismatch",
                                  "The translation inputs of %s are not the ones this app was verified "
                                  "with (capstone %s): the bundled Python's decoder differs."
                                  % (name, export.capstone_version()), {"module": name})
                done += 1
                self.r.progress("inputs", done, total, "Decoded %s (%d instructions, %.1f s)"
                                % (name, rep["lines"], rep["seconds"]), unit="modules")
        except BaseException:
            for p in list(getattr(ex, "_processes", {}).values()):
                p.terminate()
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        ex.shutdown()

    # --- translate ---
    def translate_one(self, game, key):
        parts = self.work / "parts"
        final, tmp = parts / key, parts / (key + ".tmp")
        if (final / "table.c").is_file():
            return key, 0.0, "kept"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        reports = self.work / "reports"
        reports.mkdir(exist_ok=True)
        cmd = [self.tools.python, "-I", str(self.tools.kit / "tools/recomp/translate.py"),
               "--out", str(tmp), "--game", str(game),
               "--report", str(reports / ("translate-%s.json" % key))]
        if key != "main":
            cmd += ["--module", key]
        t0 = time.time()
        log = open(reports / ("translate-%s.log" % key), "w")
        try:
            p = self.children.popen(cmd, cwd=str(self.tools.kit), stdout=log, stderr=subprocess.STDOUT)
            rc = p.wait()
            self.children.done(p)
        finally:
            log.close()
        if rc != 0:
            tail = (reports / ("translate-%s.log" % key)).read_text(errors="replace").strip().splitlines()[-5:]
            raise Failure("translate-failed", "Translating %s failed (exit %d): %s"
                          % (key, rc, " | ".join(tail)), {"module": key})
        tmp.rename(final)
        return key, time.time() - t0, "translated"

    def run_translate(self, game):
        mods = self.modules()
        total = len(mods)
        names = dict(mods)
        self.r.progress("translate", 0, total, "Translating to C", unit="modules")
        done, failed = 0, []
        # the EXE's translation is most of the time: start it, run the DLLs beside it.
        # A module that fails does not stop the others: what finished is kept for
        # the next run.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1 + self.translate_slots) as ex:
            futs = [ex.submit(self.translate_one, game, k) for k, _ in mods]
            try:
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        key, secs, how = fut.result()
                    except Failure as f:
                        failed.append(f)
                        continue
                    done += 1
                    self.r.progress("translate", done, total, "%s %s%s" % (
                        "Translated" if how == "translated" else "Already translated", names[key],
                        " (%.1f s)" % secs if secs else ""), unit="modules")
            except BaseException:
                for f in futs:
                    f.cancel()
                self.children.kill_all()
                raise
        if failed:
            raise failed[0]

    def assemble(self):
        """parts/* -> gen/ (main at the top, aux-<key>/ inside), as the kit's
        build.py publishes a translation (x86.h beside the sources)."""
        gen, parts = self.work / "gen", self.work / "parts"
        if not (gen / "table.c").is_file():
            (parts / "main").rename(gen)
        for key, _ in self.modules():
            if key == "main":
                continue
            src, dst = parts / key, gen / ("aux-" + key)
            if src.is_dir() and not dst.exists():
                src.rename(dst)
        shutil.copyfile(self.tools.kit / "runtime/x86.h", gen / "x86.h")
        shutil.rmtree(parts, ignore_errors=True)
        (self.work / "translated").write_text("ok\n")
        return gen

    # --- compile ---
    def run_compile(self, gen):
        out = self.work / "bundles"
        out.mkdir(exist_ok=True)
        keys = [k for k, _ in self.modules()]
        dirs = {k: (gen if k == "main" else gen / ("aux-" + k)) for k in keys}
        total = sum(len(list(d.glob("chunk_*.c"))) + 1 for d in dirs.values())
        cmd = [self.tools.python, "-I", str(self.tools.kit / "tools/recomp/bundle.py"),
               "--gen", str(gen), "--out", str(out), "--jobs", str(self.compile_jobs),
               "--cc", self.tools.cc, "--lld", self.tools.lld, "--min-macos", MIN_MACOS]
        for d in self.defines:
            cmd += ["-D", d]
        for k in keys:
            cmd += ["--module", k]
        self.r.log("bundle.py: " + " ".join(cmd))
        self.r.progress("compile", 0, total, "Compiling %d files with %d jobs" % (total, self.compile_jobs),
                        unit="objects")
        p = self.children.popen(cmd, cwd=str(self.tools.kit), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        lines = []
        stop = threading.Event()

        def poll():
            last = -1
            while not stop.wait(1.0):
                n = sum(len(list((out / ("%s.objects" % k)).glob("*.flags"))) for k in keys)
                if n != last:
                    last = n
                    self.r.progress("compile", min(n, total), total, "Compiling", unit="objects", force=False)
        t = threading.Thread(target=poll, daemon=True)
        t.start()
        try:
            for line in p.stdout:
                line = line.rstrip()
                lines.append(line)
                self.r.log("bundle.py: " + line)
                m = re.match(r"bundle (\S+): (\d+) objects", line)
                if m:
                    n = sum(len(list((out / ("%s.objects" % k)).glob("*.flags"))) for k in keys)
                    self.r.progress("compile", min(n, total), total, "Linked %s.bundle" % m.group(1),
                                    unit="objects")
            rc = p.wait()
        except BaseException:
            self.children.kill_all()
            raise
        finally:
            stop.set()
            self.children.done(p)
        if rc != 0:
            raise Failure("compile-failed", "Compiling failed (exit %d): %s"
                          % (rc, " | ".join(lines[-4:])))
        self.r.progress("compile", total, total, "Compiled and linked %d bundles" % len(keys), unit="objects")
        return out

    # --- publish ---
    def publish(self, gen, out):
        home, key = self.home, self.key
        final, tmp = home.bundles(key), home.build / (key + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        bundles = []
        order = [k for k, _ in self.modules() if k != "main"] + ["main"]
        for k in order:
            src = out / ("%s.bundle" % k)
            if not src.is_file():
                raise Failure("compile-failed", "bundle.py produced no %s.bundle" % k)
            dst = tmp / src.name
            if self.keep_work:
                shutil.copy2(src, dst)
            else:
                src.rename(dst)
            bundles.append({"key": k, "module": dict(self.modules())[k], "file": dst.name,
                            "bytes": dst.stat().st_size, "sha256": sha256_path(dst)})
        shutil.copyfile(gen / "symbols.json", tmp / "symbols.json")
        record = {
            "schema": SCHEMA, "key": key, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "load_order": [b["file"] for b in bundles], "bundles": bundles,
            "symbols": "symbols.json", "inputs": self.inputs,
            "seconds": {k: round(v, 1) for k, v in self.times.items()},
            "disk_bytes": self.disk,
        }
        (tmp / "build.json").write_text(json.dumps(record, indent=1) + "\n")
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
        cur = home.current.with_suffix(".json.tmp")
        cur.write_text(json.dumps({"key": key, "dir": key}) + "\n")
        os.replace(cur, home.current)
        return record

    def cleanup(self):
        home = self.home
        for p in home.build.iterdir():
            if p.is_dir() and p.name not in (self.key,) and not (self.keep_work and p == self.work):
                if re.fullmatch(r"[0-9a-f]{16}(\.tmp)?|work-[0-9a-f]{16}", p.name):
                    shutil.rmtree(p, ignore_errors=True)

    # --- times and disk, kept across a stop and a resume ---
    def load_state(self):
        """Times and disk of earlier, stopped runs of this build."""
        try:
            st = json.loads((self.work / "state.json").read_text())
        except (OSError, ValueError):
            return
        self.times.update(st.get("seconds", {}))
        self.disk.update(st.get("disk_bytes", {}))
        self.previous_wall = st.get("wall", 0.0)
        self.runs = st.get("runs", 1) + 1
        self.save_state()

    def save_state(self):
        tmp = self.work / "state.json.tmp"
        tmp.write_text(json.dumps({"seconds": self.times, "disk_bytes": self.disk, "runs": self.runs,
                                   "wall": self.previous_wall + time.time() - self.t_all}) + "\n")
        os.replace(tmp, self.work / "state.json")

    @contextlib.contextmanager
    def timed(self, phase):
        """A phase's time adds up over stopped runs; its disk use is measured when it ends."""
        t0 = time.time()
        try:
            yield
            self.disk["after_" + phase] = tree_bytes(self.work)
        finally:
            self.times[phase] = self.times.get(phase, 0.0) + time.time() - t0
            self.save_state()

    # --- the whole thing ---
    def run(self):
        self.t_all = time.time()
        self.preflight()
        cur = self.current()
        if cur and cur.get("key") == self.key and all((self.home.bundles(self.key) / b["file"]).is_file()
                                                      for b in cur["bundles"]):
            self.r.progress("preflight", 5, 5, "Already built (cache key %s)" % self.key)
            return cur, True
        self.preflight_disk()
        self.work.mkdir(parents=True, exist_ok=True)
        self.load_state()
        game = self.mirror()
        if not (self.work / "translated").is_file():
            with self.timed("inputs"):
                self.run_inputs(game)
            with self.timed("translate"):
                self.run_translate(game)
                self.assemble()
            if not self.keep_work:
                shutil.rmtree(game / "analysis", ignore_errors=True)   # the listings: 214 MB
        gen = self.work / "gen"
        with self.timed("compile"):
            out = self.run_compile(gen)
        rec = self.publish(gen, out)
        self.r.progress("publish", 1, 1, "Bundles ready in %s" % self.home.bundles(self.key))
        self.r.progress("cleanup", 0, 1, "Deleting the generated C and objects")
        self.cleanup()
        if not self.keep_work:
            shutil.rmtree(self.work, ignore_errors=True)
        self.times["total"] = self.previous_wall + time.time() - self.t_all
        rec["seconds"] = {k: round(v, 1) for k, v in self.times.items()}
        rec["runs"] = self.runs
        (self.home.bundles(self.key) / "build.json").write_text(json.dumps(rec, indent=1) + "\n")
        self.r.progress("cleanup", 1, 1, "Done in %.0f s" % self.times["total"])
        return rec, False


def status(home, tools):
    """The build's state for `status`: ready, needs-build or resumable."""
    out = {"current": None, "expected_key": None, "ready": False}
    try:
        cur = json.loads(home.current.read_text())
        rec = json.loads((home.build / cur["dir"] / "build.json").read_text())
        files = [home.build / cur["dir"] / b["file"] for b in rec["bundles"]]
        out["current"] = {"key": rec["key"], "dir": str(home.build / cur["dir"]),
                          "load_order": [str(f) for f in files],
                          "symbols": str(home.build / cur["dir"] / rec.get("symbols", "symbols.json")),
                          "complete": all(f.is_file() for f in files)}
    except (OSError, ValueError, KeyError):
        pass
    if tools.cc and tools.lld:
        key = cache_key(key_inputs(tools, guest_defines()))
        out["expected_key"] = key
        out["ready"] = bool(out["current"] and out["current"]["key"] == key and out["current"]["complete"])
        out["resumable"] = home.work(key).is_dir()
    return out
