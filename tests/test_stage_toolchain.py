"""tools/stage_toolchain.py: the wrapper around the kit's toolchain staging.

    .venv/bin/python -m pytest -q tests/test_stage_toolchain.py

Only the part that is this repository's: the innoextract build's components,
handed to the kit's notices as extra components. Staging itself is the kit's
(tools/tests/test_toolchain.py there).
"""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kitpath  # noqa: E402
import stage_toolchain  # noqa: E402

KIT, _ = kitpath.take_kit([])


def fake_innoextract(directory):
    (directory / "licenses").mkdir(parents=True)
    names = [name for _, _, _, _, files in stage_toolchain.INNOEXTRACT for name in files]
    for name in names:
        (directory / "licenses" / name).write_text("text of %s\n" % name)
    (directory / "build.json").write_text(json.dumps({
        "id": "innoextract-test", "sources": {"innoextract": {"commit": "6e9e34ed08760"},
                                               "boost": {"version": "1.87.0"}, "xz": {"version": "5.8.4"},
                                               "bzip2": {"version": "1.0.8"}}}))
    return names


def test_innoextract_and_what_it_links_join_the_notices(tmp_path):
    notices_py = KIT / "tools/toolchain/notices.py"
    if not notices_py.is_file():
        pytest.skip("this kit has no tools/toolchain (kit feat/toolchain)")
    names = fake_innoextract(tmp_path / "inno")
    spec = stage_toolchain.innoextract_notices(tmp_path / "inno", tmp_path / "out" / "innoextract.json")
    sys.path.insert(0, str(notices_py.parent))
    import notices
    components = notices.load_extra(spec)
    assert [c["title"] for c in components] == [
        "innoextract 6e9e34e", "Boost 1.87.0 (iostreams, filesystem, date_time, system, program_options)",
        "XZ Utils 5.8.4 (liblzma)", "bzip2 1.0.8"]
    text = notices.render(components, notices.Sources(tmp_path, None), "NOTICES")
    for name in names:
        assert "text of %s" % name in text
    assert str(tmp_path) not in text
    assert all(os.path.isabs(t[1]) for c in components for t in c["texts"])
