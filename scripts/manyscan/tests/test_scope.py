"""Tests for manyscan.lib.scope — seed resolution + bounded real-dependency expansion."""
from __future__ import annotations

import pytest

from lib import scope, stores
from lib.graph import Budget


def _labels(g):
    return {n.label for n in g.nodes.values()}


def _rels(g):
    return {(g.nodes[e.src].label, g.nodes[e.dst].label) for e in g.edges}


def test_resolve_seed_by_file(synth_store):
    with stores.Store(synth_store) as st:
        nodes = scope.resolve_seed(st, "pkg/a.py")
        assert [n.label for n in nodes] == ["pkg/a.py"]
        assert nodes[0].kind == "file" and str(nodes[0].evidence) == "pkg/a.py"


def test_resolve_seed_by_symbol(synth_store):
    with stores.Store(synth_store) as st:
        assert _labels_set(scope.resolve_seed(st, "C")) == {"pkg/c.py"}


def _labels_set(nodes):
    return {n.label for n in nodes}


def test_resolve_seed_unresolved(synth_store):
    with stores.Store(synth_store) as st:
        assert scope.resolve_seed(st, "zzz_nope_zzz") == []


def test_expand_forward_imports(synth_store):
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=3, direction="out"))
        assert _labels(g) == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
        assert ("pkg/a.py", "pkg/b.py") in _rels(g)
        assert ("pkg/a.py", "pkg/c.py") in _rels(g)


def test_expand_reverse_importers(synth_store):
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/b.py", Budget(max_nodes=50, max_depth=2, direction="in"))
        assert {"pkg/a.py", "pkg/b.py"} <= _labels(g)
        assert ("pkg/a.py", "pkg/b.py") in _rels(g)  # a imports b -> reverse edge a->b


def test_scan_unresolved_is_empty(synth_store):
    with stores.Store(synth_store) as st:
        assert len(scope.scan(st, "zzz_nope_zzz")) == 0


def test_expand_bounded_on_engine_store():
    """The bounded 铁律 on a real 2.65M-symbol engine store: result <= budget."""
    info = next((s for s in stores.list_stores() if s.alias == "NS_UE_5_6_1"), None)
    if info is None or not info.db_path.is_file():
        pytest.skip("NS_UE_5_6_1 store not present")
    with stores.Store(info.db_path) as st:
        seed_row = next(iter(st.iter_files(exts={".cpp", ".h"})), None)
        if seed_row is None:
            pytest.skip("no cpp/h files in store")
        seeds = scope.resolve_seed(st, seed_row["path"], alias=info.alias)
        assert seeds, "a real file path must resolve to a seed"
        g = scope.expand(st, seeds, Budget(max_nodes=150, max_depth=4, direction="out"),
                         alias=info.alias)
        assert len(g) <= 150  # never drags in the whole engine
        if g.truncated:
            assert g.frontier  # truncation is recorded honestly, not silent
