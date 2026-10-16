"""The replay of the executable's sort by angle (app/ee_sort_sim.h, used by
app/ee_sort.cpp): tests/sort/sim_test.cpp, compiled with the Xcode clang.

    .venv/bin/python -m pytest -q tests/test_sort.py
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="needs the Xcode command line tools")
def test_the_sort_replay(tmp_path):
    exe = tmp_path / "sim_test"
    subprocess.run(["xcrun", "clang++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "app"), "-o", str(exe), str(ROOT / "tests/sort/sim_test.cpp")],
                   check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "all checks passed" in r.stdout
