"""Tests for manyscan.lib.rollup — dir/module folding + frontier carry-up."""
from __future__ import annotations

from lib import rollup, scope, stores
from lib.graph import Budget, Edge, Graph, Node


def test_rollup_file_is_identity():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a/b.py"))
    assert rollup.rollup(g, "file") is g


def test_rollup_dir_collapses_intra_group():
    g = Graph()
    g.add_node(Node("file:1", "file", label="A/f1"))
    g.add_node(Node("file:2", "file", label="A/f2"))
    g.add_node(Node("file:3", "file", label="B/f3"))
    g.add_edge(Edge("file:1", "file:2", "imports"))  # intra A -> dropped
    g.add_edge(Edge("file:1", "file:3", "imports"))
    g.add_edge(Edge("file:2", "file:3", "imports"))
    r = rollup.rollup(g, "dir")
    assert set(r.nodes) == {"A", "B"}
    assert r.nodes["A"].attrs["members"] == 2
    a_b = [e for e in r.edges if e.src == "A" and e.dst == "B"]
    assert len(a_b) == 1 and a_b[0].weight == 2


def test_rollup_carries_frontier_to_group():
    g = Graph()
    g.add_node(Node("file:1", "file", label="A/f1"))
    g.add_node(Node("file:2", "file", label="A/f2"))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    g.truncated = True
    g.frontier_depth = 1
    g.elided = 5
    g.frontier["file:1"] = 5  # 5 deps elided beyond the budget at f1
    r = rollup.rollup(g, "dir")
    assert r.truncated and r.frontier_depth == 1 and r.elided == 5
    assert r.frontier.get("A") == 5  # re-attributed to dir A, not dropped


def test_module_of_handles_repo_root_marker():
    from lib.graph import Node
    roots = sorted({"", "modA"}, key=len, reverse=True)  # "" = a marker at repo root
    assert rollup._module_of(Node("file:1", "file", label="modA/x.py"), roots) == "modA"
    assert rollup._module_of(Node("file:2", "file", label="top.py"), roots) == "(root)"


def test_module_roots_and_module_rollup(module_store):
    with stores.Store(module_store) as st:
        assert rollup.module_roots(st) == {"modA", "modB"}
        g = scope.scan(st, "modA/x.py", Budget(max_nodes=50, max_depth=3, direction="out"))
        # file-level slice: modA/x.py -> modB/y.py
        assert {n.label for n in g.nodes.values()} == {"modA/x.py", "modB/y.py"}
        r = rollup.rollup(g, "module", store=st)
        assert set(r.nodes) == {"modA", "modB"}
        a_b = [e for e in r.edges if e.src == "modA" and e.dst == "modB"]
        assert len(a_b) == 1
