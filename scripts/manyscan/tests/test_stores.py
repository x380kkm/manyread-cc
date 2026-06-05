# audience: internal
# manyscan.tests.test_stores
"""manyscan.lib.stores 的测试 —— 对 manyread 存储库的只读访问。"""
from __future__ import annotations

import sqlite3

import pytest

from lib import stores


#### 验证 counts / relation_summary / lang_summary 三个汇总返回准确计数 [@380kkm 2026-06-05] ####
def test_counts_relations_langs(synth_store):
    with stores.Store(synth_store) as st:
        assert st.counts() == {"files": 3, "symbols": 3, "edges": 1}
        assert st.relation_summary() == {"extends": 1}
        assert st.lang_summary() == {"python": 3}


#### 验证按名字（LIKE 模式）查询符号能命中目标 [@380kkm 2026-06-05] ####
def test_symbols_by_name(synth_store):
    with stores.Store(synth_store) as st:
        names = {r["name"] for r in st.symbols_by_name("%A%")}
        assert "A" in names


#### 验证查询不存在的 meta 键返回 None [@380kkm 2026-06-05] ####
def test_meta_absent_is_none(synth_store):
    with stores.Store(synth_store) as st:
        assert st.meta("does-not-exist") is None


#### 验证存储库以只读方式打开，写入操作被拒绝 [@380kkm 2026-06-05] ####
def test_store_is_readonly(synth_store):
    with stores.Store(synth_store) as st:
        with pytest.raises(sqlite3.OperationalError):
            st.conn.execute("INSERT INTO meta(key,value) VALUES('x','y')")


#### 验证打开不存在的库文件抛出 FileNotFoundError [@380kkm 2026-06-05] ####
def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        stores.Store(tmp_path / "nope.db")


#### 验证 hub 的 list_stores 能运行且返回带类型的结果 [@380kkm 2026-06-05] ####
def test_hub_list_runs_and_is_typed():
    res = stores.list_stores()
    assert isinstance(res, list)
    for si in res:
        assert isinstance(si, stores.StoreInfo)
