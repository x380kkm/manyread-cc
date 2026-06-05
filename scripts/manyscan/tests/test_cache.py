# audience: internal
# manyscan.tests.test_cache
"""manyscan.lib.cache 的测试 —— 键的构造、put/get、按新鲜度失效。"""
from __future__ import annotations

from pathlib import Path

from lib import cache, stores
from lib.graph import Budget


#### 验证缓存键随输入（种子、预算）变化而变化、相同输入则稳定 [@380kkm 2026-06-05] ####
def test_cache_key_varies_with_inputs(synth_store):
    with stores.Store(synth_store) as st:
        b = Budget(max_nodes=50, max_depth=3, direction="out")
        k = cache.cache_key(st, "x", b)
        # 相同输入 -> 稳定
        assert k == cache.cache_key(st, "x", b)
        # 种子不同 -> 键不同
        assert k != cache.cache_key(st, "y", b)
        # 预算不同 -> 键不同
        assert k != cache.cache_key(st, "x", Budget(10, 3, "out"))


#### 验证 enriched_at 变化（重建索引）会使缓存键失效 [@380kkm 2026-06-05] ####
def test_cache_key_invalidates_on_enriched_at(tmp_path):
    _, mr_db = stores.manyread_lib()
    store_dir = tmp_path / "manyread"
    store_dir.mkdir(parents=True)
    db = store_dir / "source.db"
    conn = mr_db.connect(db)
    mr_db.init_schema(conn)
    mr_db.set_meta(conn, "enriched_at", "T1")
    conn.close()
    with stores.Store(db) as st:
        k1 = cache.cache_key(st, "x", Budget())
    conn = mr_db.connect(db)
    mr_db.set_meta(conn, "enriched_at", "T2")
    conn.close()
    with stores.Store(db) as st:
        k2 = cache.cache_key(st, "x", Budget())
    # 重建索引改变指纹 -> 旧条目未命中
    assert k1 != k2


#### 验证 put 写入后 get 能取回、且落盘为对应的 json 文件 [@380kkm 2026-06-05] ####
def test_put_get_roundtrip(synth_store):
    with stores.Store(synth_store) as st:
        assert cache.get(st, "k1") is None
        cache.put(st, "k1", {"hello": "world"})
        assert cache.get(st, "k1") == {"hello": "world"}
        assert (Path(synth_store).parent / "manyscan" / "cache" / "k1.json").is_file()


#### 验证带缓存的 scan 首次未命中、再次命中、use_cache=False 时再次未命中 [@380kkm 2026-06-05] ####
def test_cached_scan_hit_then_miss_on_nocache(synth_store):
    b = Budget(max_nodes=50, max_depth=3, direction="out")
    with stores.Store(synth_store) as st:
        data1, hit1 = cache.cached_scan(st, "pkg/a.py", b)
        assert hit1 is False
        assert {n["label"] for n in data1["nodes"]} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
        data2, hit2 = cache.cached_scan(st, "pkg/a.py", b)
        assert hit2 is True and data2 == data1
        _, hit3 = cache.cached_scan(st, "pkg/a.py", b, use_cache=False)
        assert hit3 is False
