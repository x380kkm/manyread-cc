"""Tests for manyscan.lib.stores — read-only access to manyread stores."""
from __future__ import annotations

import sqlite3

import pytest

from lib import stores


def test_counts_relations_langs(synth_store):
    with stores.Store(synth_store) as st:
        assert st.counts() == {"files": 3, "symbols": 3, "edges": 1}
        assert st.relation_summary() == {"extends": 1}
        assert st.lang_summary() == {"python": 3}


def test_symbols_by_name(synth_store):
    with stores.Store(synth_store) as st:
        names = {r["name"] for r in st.symbols_by_name("%A%")}
        assert "A" in names


def test_meta_absent_is_none(synth_store):
    with stores.Store(synth_store) as st:
        assert st.meta("does-not-exist") is None


def test_store_is_readonly(synth_store):
    with stores.Store(synth_store) as st:
        with pytest.raises(sqlite3.OperationalError):
            st.conn.execute("INSERT INTO meta(key,value) VALUES('x','y')")


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        stores.Store(tmp_path / "nope.db")


def test_hub_list_runs_and_is_typed():
    res = stores.list_stores()
    assert isinstance(res, list)
    for si in res:
        assert isinstance(si, stores.StoreInfo)
