"""Tests for the manyscan CLI (scripts/scan.py) via in-process main(argv)."""
from __future__ import annotations

import contextlib
import io
import json

import pytest

import scan  # scripts/ is on sys.path via conftest
from lib import stores


def _run(argv) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = scan.main(argv)
    assert rc == 0, f"exit {rc} for {argv}"
    return buf.getvalue()


def test_cli_scan_json(synth_store):
    out = _run(["scan", "pkg/a.py", "--store", str(synth_store.parent), "--format", "json", "--no-cache"])
    data = json.loads(out)
    assert {n["label"] for n in data["nodes"]} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
    assert data["bounded"]["truncated"] is False


def test_cli_scan_text(synth_store):
    out = _run(["scan", "pkg/a.py", "--store", str(synth_store.parent), "--format", "text"])
    assert "nodes=3" in out


def test_cli_analyze(synth_store):
    out = _run(["analyze", "pkg/a.py", "--store", str(synth_store.parent)])
    assert "nodes=3" in out and "most_unstable" in out


def test_cli_module_level(module_store):
    out = _run(["scan", "modA/x.py", "--store", str(module_store.parent),
                "--level", "module", "--format", "text"])
    assert "modA" in out and "modB" in out


def test_cli_export_dot(module_store):
    out = _run(["export", "modA/x.py", "--store", str(module_store.parent)])
    assert out.startswith("digraph manyscan {")


def test_cli_list_stores_runs():
    out = _run(["list-stores"])
    assert isinstance(out, str)


def test_cli_scan_bounded_on_engine():
    info = next((s for s in stores.list_stores() if s.alias == "NS_UE_5_6_1"), None)
    if info is None or not info.db_path.is_file():
        pytest.skip("NS_UE_5_6_1 store not present")
    with stores.Store(info.db_path) as st:
        row = next(iter(st.iter_files(exts={".cpp", ".h"})), None)
        if row is None:
            pytest.skip("no cpp/h files")
        seed = row["path"]
    out = _run(["scan", seed, "--store", "NS_UE_5_6_1", "--max-nodes", "120",
                "--format", "json", "--no-cache"])
    assert len(json.loads(out)["nodes"]) <= 120  # CLI honors the bounded 铁律
