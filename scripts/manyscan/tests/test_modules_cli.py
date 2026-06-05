# audience: internal
# manyscan.tests.test_modules_cli
"""manyscan modules 子命令的 CLI 测试 —— 规格解析、退出码、json/text 接线、html 烘焙。

覆盖：无规格退出 2、--module 内联规格、--modules 文件、manyread.json 自动发现、
json 矩阵/环/按需/切割形状、html 烘焙 MODULE_MODE/ZONE_MATRIX。boundary 子命令不受影响
（其 --help 与黄金输出在别处校验）。
"""
from __future__ import annotations

import json

import scan

from conftest import _make_store as _mk_store


#### 四模块端到端库：含一条注入环、按需符号、跨模块边 [@380kkm 2026-06-05] ####
def _e2e_store(tmp_path):
    files = [
        (1, "Core/Obj.h", ".h", "x"),
        (2, "Render/Rhi.h", ".h", "x"),
        (3, "Game/Actor.h", ".h", "x"),
        (4, "ThirdParty/z.h", ".h", "x"),
    ]
    syms = [
        (1, 1, "FObject", "class", 1, 1, None),
        (2, 2, "FRHI", "class", 1, 1, None),
        (3, 3, "AActor", "class", 1, 1, None),
        (4, 4, "ZStream", "class", 1, 1, None),
    ]
    # Game->Core, Render->Core, Core->ThirdParty, ThirdParty->Game（注入环 Core->ThirdParty->Game->Core）
    edges = [
        (1, 3, 3, 1, None, "extends"),
        (2, 2, 2, 1, None, "uses_type"),
        (3, 1, 1, 4, None, "uses_type"),
        (4, 4, 4, 3, None, "uses_type"),
    ]
    return _mk_store(tmp_path, files, syms, edges)


_E2E_DOC = {"version": 1, "fallback": "External", "zones": [
    {"name": "Core", "include": ["Core"]},
    {"name": "Render", "include": ["Render"]},
    {"name": "Game", "include": ["Game"]},
    {"name": "ThirdParty", "include": ["ThirdParty"]},
]}


#### 无任何规格来源时 modules 退出 2 并指向 --modules/--module/manyread.json [@380kkm 2026-06-05] ####
def test_modules_no_spec_exits_2(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    rc = scan.main(["modules", "--store", str(db), "--format", "json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--modules" in err and "--module" in err


#### 内联 --module 规格驱动 N 路扫描，json 输出含矩阵/区列表 [@380kkm 2026-06-05] ####
def test_modules_inline_module_json(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    rc = scan.main(["modules", "--store", str(db), "--format", "json",
                    "--module", "Core=Core", "--module", "Game=Game"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "Core" in out["zones"] and "Game" in out["zones"]
    # Game->Core 跨模块边在矩阵里
    pairs = {(m["src"], m["dst"]) for m in out["matrix"]}
    assert ("Game", "Core") in pairs


#### --modules 文件端到端：4 模块矩阵 + 注入环 + 按需符号(证据) + 切割代价排序 [@380kkm 2026-06-05] ####
def test_modules_file_end_to_end(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    spec_file = tmp_path / "mods.json"
    spec_file.write_text(json.dumps(_E2E_DOC), encoding="utf-8")
    rc = scan.main(["modules", "--store", str(db), "--format", "json",
                    "--modules", str(spec_file)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # 矩阵：含跨模块对
    pairs = {(m["src"], m["dst"]) for m in out["matrix"]}
    assert ("Game", "Core") in pairs and ("Core", "ThirdParty") in pairs
    # 注入环 Core->ThirdParty->Game->Core 被检出
    assert any(set(c) == {"Core", "ThirdParty", "Game"} for c in out["cycles"])
    # 按需符号带证据：Game 从 Core 需要 FObject
    gc = next(x for x in out["needed"] if x["src"] == "Game" and x["dst"] == "Core")
    syms = {s["label"] for s in gc["symbols"]}
    assert "FObject" in syms
    assert gc["symbols"][0]["winning_prefix"] == "Core"
    # 切割代价升序
    costs = [c["cost"] for c in out["cut_costs"]]
    assert costs == sorted(costs)


#### manyread.json['modules'] 自动发现（无 --modules/--module 标志） [@380kkm 2026-06-05] ####
def test_modules_committed_autodiscovered(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    store_dir = db.parent
    (store_dir / "manyread.json").write_text(
        json.dumps({"alias": "t", "modules": _E2E_DOC}), encoding="utf-8")
    rc = scan.main(["modules", "--store", str(db), "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["zones"]) == {"Core", "Render", "Game", "ThirdParty"}


#### --fallback 覆盖兜底名 [@380kkm 2026-06-05] ####
def test_modules_fallback_override(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    # 只声明 Core；其余落兜底
    rc = scan.main(["modules", "--store", str(db), "--format", "json",
                    "--module", "Core=Core", "--fallback", "Outside"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fallback"] == "Outside"


#### html 输出烘焙 MODULE_MODE / ZONE_MATRIX / MODULE_LIST，且是自包含页面 [@380kkm 2026-06-05] ####
def test_modules_html_bakes_consts(tmp_path, capsys):
    db = _e2e_store(tmp_path)
    spec_file = tmp_path / "mods.json"
    spec_file.write_text(json.dumps(_E2E_DOC), encoding="utf-8")
    rc = scan.main(["modules", "--store", str(db), "--format", "html",
                    "--modules", str(spec_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!doctype html>")
    assert "const MODULE_MODE=" in out
    assert "const ZONE_MATRIX=" in out
    # 节点按模块上区（cluster 驱动调色板）
    assert '"module":' in out


#### boundary 子命令在引入 modules 后仍可用且与既往一致（冒烟） [@380kkm 2026-06-05] ####
def test_boundary_still_works(tmp_path, capsys):
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"), (3, "engine/Dep.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None), (2, 3, "Dep", "class", 1, 1, None)]
    edges = [(1, 2, 1, 2, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    rc = scan.main(["boundary", "--store", str(db), "--target-root", "plugin", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    assert "s1" in ids and "s2" in ids
