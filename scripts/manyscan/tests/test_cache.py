"""Tests for manyscan.lib.cache — keying, put/get, freshness invalidation."""
from __future__ import annotations

from pathlib import Path

from lib import cache, stores
from lib.graph import Budget


def test_cache_key_varies_with_inputs(synth_store):
    with stores.Store(synth_store) as st:
        b = Budget(max_nodes=50, max_depth=3, direction="out")
        k = cache.cache_key(st, "x", b)
        assert k == cache.cache_key(st, "x", b)  # stable
        assert k != cache.cache_key(st, "y", b)  # seed matters
        assert k != cache.cache_key(st, "x", Budget(10, 3, "out"))  # budget matters


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
    assert k1 != k2  # re-index changes fingerprint -> stale entry misses


def test_put_get_roundtrip(synth_store):
    with stores.Store(synth_store) as st:
        assert cache.get(st, "k1") is None
        cache.put(st, "k1", {"hello": "world"})
        assert cache.get(st, "k1") == {"hello": "world"}
        assert (Path(synth_store).parent / "manyscan" / "cache" / "k1.json").is_file()


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
