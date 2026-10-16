#!/usr/bin/env python3
"""Summarize a macOS `sample` profile of the game (P7-B, docs/reference/performance.md).

    .venv/bin/python tools/perf/sample_report.py SAMPLE.txt [--top N] [--thread SUBSTR]
        [--names DB.json ...]

`sample` writes a call tree per thread. This reads it back and says, per thread,
how much of the time it waited (the leaf is a kernel wait) and where the rest
went, three ways:
  - by leaf: the host function the CPU was in (a runtime HLE function, a
    renderer routine, or a translated function with its inlined helpers);
  - by guest function: the innermost translated function on the stack
    (fn_XXXXXXXX / body_XXXXXXXX), which is the guest code the time belongs to,
    whether it ran in the translation itself or in a runtime call it made;
  - by area: guest code, runtime dispatch (recomp_call, the RET check), HLE
    imports, the scheduler, the renderer, the presenter, other.
--names maps guest addresses to function-DB names (db/functions/*.json), for
reading only: reports that carry game names stay in analysis/, out of git.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LINE = re.compile(r"^(\s*[+!:| ]*)(\d+) (.*)$")
THREAD = re.compile(r"^\s{4}(\d+) (Thread_\d+.*)$")
GUEST = re.compile(r"^(?:fn|body)_([0-9a-f]{8})\b")
WAITS = ("__psynch_cvwait", "__semwait_signal", "__psynch_mutexwait", "poll", "nanosleep",
         "kevent", "mach_msg", "__select", "__workq_kernreturn", "semaphore_wait", "__ulock_wait",
         "__psynch_rw", "read", "__recvfrom", "__accept", "waitpid", "__wait4", "usleep",
         "_pthread_cond_wait", "__sigsuspend", "mach_msg2_trap", "start_wqthread")
DISPATCH = ("recomp_call", "recomp_jump", "recomp_module_call", "recomp_is_call_return",
            "(anonymous namespace)::target_info(unsigned int)", "recomp_call_return_any",
            "recomp_module_is_call_return", "recomp_module_lookup", "recomp_module_containing",
            "recomp_lookup", "recomp_index_of", "recomp_return")


def symbol(text):
    """'name  (in module) + 12 [0x...] file:line' -> ('name', 'module')."""
    m = re.match(r"(.*?)\s+\(in ([^)]+)\)", text)
    if not m:
        return text.strip(), ""
    return m.group(1).strip(), m.group(2)


def parse(path):
    """{thread: root} where a node is [count, name, module, children]."""
    threads = {}
    stack = []
    in_graph = False
    for raw in Path(path).read_text(errors="replace").splitlines():
        if raw.startswith("Call graph:"):
            in_graph = True
            continue
        if not in_graph:
            continue
        if not raw.strip() or raw.startswith("Total number in stack"):
            if threads:
                break
            continue
        t = THREAD.match(raw)
        if t:
            root = [int(t.group(1)), t.group(2), "", []]
            threads[t.group(2)] = root
            stack = [(4, root)]
            continue
        m = LINE.match(raw)
        if not m or not stack:
            continue
        depth = len(m.group(1))
        name, module = symbol(m.group(3))
        node = [int(m.group(2)), name, module, []]
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            continue
        stack[-1][1][3].append(node)
        stack.append((depth, node))
    return threads


def area(leaf, frames):
    """The area a busy sample belongs to, from its leaf and the frames above it."""
    names = [f[0] for f in frames]
    if any(n.startswith(("d7soft::", "D3D7Renderer", "host_d7_", "gpu::", "MetalDevice",
                         "-[MTL", "MTL", "AGX", "IOGPU")) for n in names):
        return "renderer"
    if any(n.startswith(("present", "Presenter", "compositor", "host_display")) for n in names):
        return "presenter"
    if any("sched_" in n or n.startswith(("guest_block", "(anonymous namespace)::sched_",
                                           "(anonymous namespace)::guest_block")) for n in names):
        return "scheduler"
    if leaf in DISPATCH:
        return "dispatch"
    if GUEST.match(leaf):
        return "guest code"
    if any(n.startswith(("imports_dispatch", "recomp_shim_call")) for n in names):
        return "HLE imports"
    if any(GUEST.match(n) for n in names):
        return "runtime (from guest)"
    return "other"


def walk(node, frames, out):
    """Every leaf of node's subtree with its self count and the frames to it."""
    count, name, module, children = node
    frames = frames + [(name, module)]
    self_count = count - sum(c[0] for c in children)
    if self_count > 0:
        out.append((self_count, frames))
    for c in children:
        walk(c, frames, out)


def load_names(paths):
    names = {}
    for p in paths:
        try:
            db = json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
        base = int(str(db.get("rebase", db.get("base", "0"))), 0) if isinstance(db, dict) else 0
        for f in (db.get("functions") if isinstance(db, dict) else db) or []:
            va, nm = f.get("va"), f.get("name")
            if va is None or not nm:
                continue
            va = int(va, 16) if isinstance(va, str) else int(va)
            names[va + base] = nm
    return names


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sample", type=Path)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--thread", default=None, help="only threads whose line contains this")
    p.add_argument("--names", action="append", default=[], metavar="DB.json")
    p.add_argument("--json", type=Path, default=None, help="also write the tables as JSON")
    a = p.parse_args()
    names = load_names(a.names)
    threads = parse(a.sample)
    report = {}
    for tname, root in threads.items():
        if a.thread and a.thread not in tname:
            continue
        leaves = []
        walk(root, [], leaves)
        total = root[0]
        wait = sum(c for c, fr in leaves if fr[-1][0] in WAITS or fr[-1][0].startswith("__psynch"))
        busy = total - wait
        if busy < max(1, total // 100):
            continue
        by_leaf, by_guest, by_area = Counter(), Counter(), Counter()
        for c, fr in leaves:
            leaf = fr[-1][0]
            if leaf in WAITS or leaf.startswith("__psynch"):
                continue
            by_leaf[leaf] += c
            guest = next((GUEST.match(n).group(1) for n, _ in reversed(fr) if GUEST.match(n)), None)
            by_guest[guest or "(none)"] += c
            by_area[area(leaf, fr)] += c
        report[tname] = {"samples": total, "wait": wait, "busy": busy,
                         "area": by_area.most_common(), "leaf": by_leaf.most_common(a.top),
                         "guest": by_guest.most_common(a.top)}
        print("== %s: %d samples, busy %d (%.0f%%), waiting %d" %
              (tname, total, busy, 100.0 * busy / total, wait))
        print("   areas: " + ", ".join("%s %.1f%%" % (k, 100.0 * v / total) for k, v in by_area.most_common()))
        print("   by leaf:")
        for k, v in by_leaf.most_common(a.top):
            print("     %6.2f%%  %s" % (100.0 * v / total, k[:110]))
        print("   by guest function (innermost on the stack):")
        for k, v in by_guest.most_common(a.top):
            nm = names.get(int(k, 16), "") if k != "(none)" else ""
            print("     %6.2f%%  %s %s" % (100.0 * v / total, k, nm))
    if a.json:
        a.json.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
