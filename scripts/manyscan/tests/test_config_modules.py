"""已提交的 `modules` N 路分区配置加载器（manyread 自带 lib/config.py）的测试。

覆盖 validate_modules 的结构性检查、load_modules 的优先级
（--modules 覆盖 > manyread.json['modules'] > None）、包裹式或裸式的 --modules 文件，
以及缺失/畸形的显式 --modules 告警。镜像 test_config_view_hide.py。
"""
from __future__ import annotations

import json

from lib import stores


#### 取 manyread 自带的 config 模块 [@380kkm 2026-06-05] ####
def _cfg():
    return stores.manyread_lib()[0]
#### /取 config 模块 ####


#### 一份最小合法的 modules 文档 [@380kkm 2026-06-05] ####
def _doc(zones=None, fallback="External"):
    if zones is None:
        zones = [{"name": "Core", "include": ["a/Core"]},
                 {"name": "Render", "include": ["a/RHI", "a/Renderer"]}]
    return {"version": 1, "fallback": fallback, "zones": zones}


#### 构造带 manyread.json（可选携带 modules 键）的裸存储库目录 [@380kkm 2026-06-05] ####
def _store(tmp_path, modules=None, *, write_json=True, raw=None):
    store = tmp_path / "manyread"
    store.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (store / "manyread.json").write_text(raw, encoding="utf-8")
    elif write_json:
        payload = {"alias": "t", "languages": [], "exts": []}
        if modules is not None:
            payload["modules"] = modules
        (store / "manyread.json").write_text(json.dumps(payload), encoding="utf-8")
    return store
#### /裸存储库目录构造器 ####


#### 测试 validate_modules 接受合法配置（含 exclude/glob） [@380kkm 2026-06-05] ####
def test_validate_modules_accepts_valid():
    cfg = _cfg()
    assert cfg.validate_modules(_doc()) == []
    zones = [{"name": "Game", "include": ["a/Engine"], "exclude": ["a/Engine/Tests"],
              "glob": ["**/x"]}]
    assert cfg.validate_modules(_doc(zones=zones)) == []


#### 测试 validate_modules 拒绝各类畸形字段 [@380kkm 2026-06-05] ####
def test_validate_modules_rejects_bad():
    cfg = _cfg()
    # 版本错
    assert cfg.validate_modules({"version": 2, "zones": [{"name": "A", "include": ["x"]}]})
    # zones 为空
    assert cfg.validate_modules({"version": 1, "zones": []})
    # zones 缺失
    assert cfg.validate_modules({"version": 1})
    # name 重复
    assert cfg.validate_modules(_doc(zones=[{"name": "A", "include": ["x"]},
                                            {"name": "A", "include": ["y"]}]))
    # name 空
    assert cfg.validate_modules(_doc(zones=[{"name": "", "include": ["x"]}]))
    # include 非字符串列表
    assert cfg.validate_modules(_doc(zones=[{"name": "A", "include": [1]}]))
    # include 缺失
    assert cfg.validate_modules(_doc(zones=[{"name": "A"}]))
    # exclude 非列表
    assert cfg.validate_modules(_doc(zones=[{"name": "A", "include": ["x"], "exclude": "y"}]))
    # fallback 非字符串
    d = _doc(); d["fallback"] = 5
    assert cfg.validate_modules(d)


#### 测试缺失 modules 键时 load 返回 None [@380kkm 2026-06-05] ####
def test_load_modules_absent_is_none(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)
    assert cfg.load_modules(store) is None


#### 测试加载已提交的 modules 键 [@380kkm 2026-06-05] ####
def test_load_modules_committed_key(tmp_path):
    cfg = _cfg()
    md = _doc()
    store = _store(tmp_path, modules=md)
    got = cfg.load_modules(store)
    assert got == md


#### 测试 --modules 覆盖文件优先于已提交键 [@380kkm 2026-06-05] ####
def test_load_modules_override_wins(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path, modules=_doc(zones=[{"name": "Committed", "include": ["x"]}]))
    ov = tmp_path / "mods.json"
    ov.write_text(json.dumps({"modules": _doc(zones=[{"name": "FromFile", "include": ["y"]}])}),
                  encoding="utf-8")
    got = cfg.load_modules(store, ov)
    assert [z["name"] for z in got["zones"]] == ["FromFile"]


#### 测试 --modules 接受裸式（无 modules 包裹）文件 [@380kkm 2026-06-05] ####
def test_load_modules_accepts_bare_file(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)
    ov = tmp_path / "bare.json"
    ov.write_text(json.dumps(_doc()), encoding="utf-8")
    got = cfg.load_modules(store, ov)
    assert got == _doc()


#### 测试畸形已提交键返回 None 并告警 [@380kkm 2026-06-05] ####
def test_load_modules_malformed_returns_none_and_warns(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path, modules={"version": 2, "zones": [{"name": "A", "include": ["x"]}]})
    assert cfg.load_modules(store) is None
    assert "malformed modules" in capsys.readouterr().err


#### 测试缺失的显式 --modules 文件响亮告警 [@380kkm 2026-06-05] ####
def test_load_modules_missing_file_warns_loud(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path)
    bogus = tmp_path / "nope.json"
    assert cfg.load_modules(store, bogus) is None
    assert "--modules file not found" in capsys.readouterr().err


#### 测试未知顶层键告警但仍按其余有效键继续 [@380kkm 2026-06-05] ####
def test_load_modules_unknown_key_warns_but_proceeds(tmp_path, capsys):
    cfg = _cfg()
    md = _doc(); md["extra"] = 1
    store = _store(tmp_path, modules=md)
    got = cfg.load_modules(store)
    assert got is not None and [z["name"] for z in got["zones"]] == ["Core", "Render"]
    assert "unknown key" in capsys.readouterr().err


#### 测试 manyread.json 不可读/为空时告警并返回 None [@380kkm 2026-06-05] ####
def test_load_modules_broken_manyread_json_warns(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path, raw='{"alias": "t", "modules": {trailing,}')
    assert cfg.load_modules(store) is None
    assert "unreadable/empty" in capsys.readouterr().err


#### 测试 utf-8-sig BOM 文件可被裸式 --modules 读取 [@380kkm 2026-06-05] ####
def test_load_modules_utf8_sig_bom(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)
    ov = tmp_path / "bom.json"
    ov.write_text(json.dumps(_doc()), encoding="utf-8-sig")
    got = cfg.load_modules(store, ov)
    assert got == _doc()
