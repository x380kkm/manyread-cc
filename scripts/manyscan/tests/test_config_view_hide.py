"""已提交的 `view_hide` 配置加载器（manyread 自带 lib/config.py）的测试。

覆盖 validate_view_hide 的结构性检查、load_view_hide 的优先级
（--ignore 覆盖 > manyread.json['view_hide'] > None）、包裹式或裸式的 --ignore 文件，
以及“显式失败响亮、隐式缺省静默”的契约（缺失/畸形的显式 --ignore 会告警；缺失已提交的键
是 v0.6.0 的静默行为）。
"""
from __future__ import annotations

import json

from lib import stores


#### 取 manyread 自带的 config 模块 [@380kkm 2026-06-05] ####
def _cfg():
    return stores.manyread_lib()[0]
#### /取 config 模块 ####


#### 构造带 manyread.json（可选携带 view_hide 键）的裸存储库目录 [@380kkm 2026-06-05] ####
def _store(tmp_path, view_hide=None, *, write_json=True, raw=None):
    """一个裸存储库目录，含一份 manyread.json（可选携带 view_hide 键）。"""
    store = tmp_path / "manyread"
    store.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (store / "manyread.json").write_text(raw, encoding="utf-8")
    elif write_json:
        payload = {"alias": "t", "languages": [], "exts": []}
        if view_hide is not None:
            payload["view_hide"] = view_hide
        (store / "manyread.json").write_text(json.dumps(payload), encoding="utf-8")
    return store
#### /裸存储库目录构造器 ####


#### 测试 validate_view_hide 接受合法配置（含全可选键） [@380kkm 2026-06-05] ####
def test_validate_view_hide_accepts_valid():
    cfg = _cfg()
    assert cfg.validate_view_hide(
        {"version": 1, "names": ["int32", "FString"], "patterns": ["TArray*"], "min_fan_in": 5}
    ) == []
    # 所有键皆可选
    assert cfg.validate_view_hide({}) == []


#### 测试 validate_view_hide 拒绝各类畸形字段 [@380kkm 2026-06-05] ####
def test_validate_view_hide_rejects_bad():
    cfg = _cfg()
    assert cfg.validate_view_hide({"version": 2})
    assert cfg.validate_view_hide({"names": [1, 2]})
    assert cfg.validate_view_hide({"patterns": "TArray*"})
    assert cfg.validate_view_hide({"min_fan_in": -1})
    # 此处 bool 不算整数
    assert cfg.validate_view_hide({"min_fan_in": True})
    assert cfg.validate_view_hide({"min_fan_in": "5"})


#### 测试缺失 view_hide 键时 load 返回 None [@380kkm 2026-06-05] ####
def test_load_view_hide_absent_is_none(tmp_path):
    cfg = _cfg()
    # manyread.json 不含 view_hide 键
    store = _store(tmp_path)
    assert cfg.load_view_hide(store) is None


#### 测试加载已提交的 view_hide 键 [@380kkm 2026-06-05] ####
def test_load_view_hide_committed_key(tmp_path):
    cfg = _cfg()
    vh = {"version": 1, "names": ["FString"], "min_fan_in": 20}
    store = _store(tmp_path, view_hide=vh)
    got = cfg.load_view_hide(store)
    assert got == vh


#### 测试 --ignore 覆盖文件优先于已提交键 [@380kkm 2026-06-05] ####
def test_load_view_hide_override_wins(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path, view_hide={"names": ["Committed"]})
    ov = tmp_path / "ignore.json"
    ov.write_text(json.dumps({"view_hide": {"names": ["FromFile"]}}), encoding="utf-8")
    got = cfg.load_view_hide(store, ov)
    # --ignore 胜过已提交键
    assert got == {"names": ["FromFile"]}


#### 测试 --ignore 接受裸式（无 view_hide 包裹）文件 [@380kkm 2026-06-05] ####
def test_load_view_hide_accepts_bare_ignore_file(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)
    ov = tmp_path / "bare.json"
    ov.write_text(json.dumps({"names": ["int32"], "min_fan_in": 30}), encoding="utf-8")
    got = cfg.load_view_hide(store, ov)
    assert got == {"names": ["int32"], "min_fan_in": 30}


#### 测试畸形已提交键返回 None 并告警 [@380kkm 2026-06-05] ####
def test_load_view_hide_malformed_returns_none_and_warns(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path, view_hide={"version": 2, "names": ["x"]})
    assert cfg.load_view_hide(store) is None
    assert "malformed view_hide" in capsys.readouterr().err


#### 测试缺失的显式 --ignore 文件响亮告警 [@380kkm 2026-06-05] ####
def test_load_view_hide_missing_ignore_file_warns_loud(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path)
    bogus = tmp_path / "does-not-exist.json"
    assert cfg.load_view_hide(store, bogus) is None
    assert "--ignore file not found" in capsys.readouterr().err


#### 测试未知键告警但仍按其余有效键继续 [@380kkm 2026-06-05] ####
def test_load_view_hide_unknown_key_warns_but_proceeds(tmp_path, capsys):
    cfg = _cfg()
    # 'name' 拼写错误（应为 'names'）：校验通过却会静默地什么都不隐藏；我们告警以使
    # 持久化循环的失败可见。min_fan_in 仍然生效。
    store = _store(tmp_path, view_hide={"name": ["FString"], "min_fan_in": 10})
    got = cfg.load_view_hide(store)
    assert got is not None and got.get("min_fan_in") == 10
    assert "unknown key" in capsys.readouterr().err


#### 测试 manyread.json 不可读/为空时告警并返回 None [@380kkm 2026-06-05] ####
def test_load_view_hide_broken_manyread_json_warns(tmp_path, capsys):
    cfg = _cfg()
    # 非法 JSON
    store = _store(tmp_path, raw='{"alias": "t", "view_hide": {trailing,}')
    assert cfg.load_view_hide(store) is None
    assert "unreadable/empty" in capsys.readouterr().err
