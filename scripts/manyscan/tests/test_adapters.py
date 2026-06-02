"""Tests for manyscan.lib.adapters — SourceAdapter protocol + CodeAdapter."""
from __future__ import annotations

from lib import adapters, stores


def test_codeadapter_satisfies_protocol():
    a = adapters.CodeAdapter()
    assert isinstance(a, adapters.SourceAdapter)
    assert a.name == "code"
    assert adapters.DEFAULT_ADAPTER.name == "code"


def test_seed_nodes(synth_store):
    with stores.Store(synth_store) as st:
        nodes = adapters.CodeAdapter().seed_nodes(st, "pkg/a.py")
        assert [n.label for n in nodes] == ["pkg/a.py"]
        assert nodes[0].id == "file:1" and nodes[0].kind == "file"


def test_neighbors_forward(synth_store):
    with stores.Store(synth_store) as st:
        steps = list(adapters.CodeAdapter().neighbors(st, "file:1", direction="out"))
        assert {s.node.label for s in steps} == {"pkg/b.py", "pkg/c.py"}
        assert all(s.edge.relation == "imports" for s in steps)


def test_neighbors_reverse(synth_store):
    with stores.Store(synth_store) as st:
        steps = list(adapters.CodeAdapter().neighbors(st, "file:2", direction="in"))
        assert {s.node.label for s in steps} == {"pkg/a.py"}  # a imports b


def test_neighbors_non_file_node_is_empty(synth_store):
    with stores.Store(synth_store) as st:
        assert list(adapters.CodeAdapter().neighbors(st, "ext:foo")) == []
