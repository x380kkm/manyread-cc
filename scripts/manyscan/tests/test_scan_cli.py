"""manyscan CLI（scripts/scan.py）经进程内 main(argv) 的测试。"""
from __future__ import annotations

import contextlib
import io
import json

import pytest

# scripts/ 已由 conftest 加入 sys.path
import scan
from lib import stores


#### 进程内运行一次 CLI，断言退出 0 并返回其 stdout [@380kkm 2026-06-05] ####
def _run(argv) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = scan.main(argv)
    assert rc == 0, f"exit {rc} for {argv}"
    return buf.getvalue()
#### /进程内运行 CLI ####


#### 测试 scan --format json 输出节点集合与有界标志 [@380kkm 2026-06-05] ####
def test_cli_scan_json(synth_store):
    out = _run(["scan", "pkg/a.py", "--store", str(synth_store.parent), "--format", "json", "--no-cache"])
    data = json.loads(out)
    assert {n["label"] for n in data["nodes"]} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
    assert data["bounded"]["truncated"] is False


#### 测试 scan --format text 输出节点计数 [@380kkm 2026-06-05] ####
def test_cli_scan_text(synth_store):
    out = _run(["scan", "pkg/a.py", "--store", str(synth_store.parent), "--format", "text"])
    assert "nodes=3" in out


#### 测试 analyze 子命令输出计数与最不稳定项 [@380kkm 2026-06-05] ####
def test_cli_analyze(synth_store):
    out = _run(["analyze", "pkg/a.py", "--store", str(synth_store.parent)])
    assert "nodes=3" in out and "most_unstable" in out


#### 测试 --level module 在 module 级聚合 [@380kkm 2026-06-05] ####
def test_cli_module_level(module_store):
    out = _run(["scan", "modA/x.py", "--store", str(module_store.parent),
                "--level", "module", "--format", "text"])
    assert "modA" in out and "modB" in out


#### 测试 export 子命令输出 DOT 图 [@380kkm 2026-06-05] ####
def test_cli_export_dot(module_store):
    out = _run(["export", "modA/x.py", "--store", str(module_store.parent)])
    assert out.startswith("digraph manyscan {")


#### 测试 list-stores 子命令可运行 [@380kkm 2026-06-05] ####
def test_cli_list_stores_runs():
    out = _run(["list-stores"])
    assert isinstance(out, str)


#### 测试 CLI 在真实引擎上遵守有界铁律 [@380kkm 2026-06-05] ####
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
    # CLI 遵守有界铁律
    assert len(json.loads(out)["nodes"]) <= 120
