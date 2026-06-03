"""Tests for manyscan.lib.analyze — coupling / cycles / bridges / cut-nodes / layers."""
from __future__ import annotations

from lib import analyze
from lib.graph import Edge, Graph, Node


def _chain(*ids):
    g = Graph()
    for i in ids:
        g.add_node(Node(i, "n", label=i))
    for a, b in zip(ids, ids[1:]):
        g.add_edge(Edge(a, b, "dep"))
    return g


def test_instability_chain():
    g = _chain("a", "b", "c")  # a -> b -> c
    by = {m.id: m for m in analyze.node_metrics(g)}
    assert (by["a"].ca, by["a"].ce, by["a"].instability) == (0, 1, 1.0)   # pure source
    assert (by["b"].ca, by["b"].ce, by["b"].instability) == (1, 1, 0.5)
    assert (by["c"].ca, by["c"].ce, by["c"].instability) == (1, 0, 0.0)   # pure sink


def test_cycles_and_layers():
    g = Graph()
    for n in "ab":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "a", "dep"))
    assert analyze.cycles(g) == [["a", "b"]]
    _, leftover = analyze.layers(g)
    assert set(leftover) == {"a", "b"}

    dag = _chain("x", "y", "z")
    lys, left = analyze.layers(dag)
    assert lys == [["x"], ["y"], ["z"]] and left == []
    assert analyze.cycles(dag) == []


def test_bridges_and_cut_nodes_star():
    # hub h -> a, b, c : every spoke is a bridge, h is the articulation node
    g = Graph()
    for n in "habc":
        g.add_node(Node(n, "n"))
    for leaf in "abc":
        g.add_edge(Edge("h", leaf, "dep"))
    assert sorted(b[1] for b in analyze.bridges(g)) == ["a", "b", "c"]
    assert analyze.cut_nodes(g) == ["h"]


def test_triangle_has_no_bridges():
    g = Graph()
    for n in "abc":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "c", "dep"))
    g.add_edge(Edge("c", "a", "dep"))  # cycle -> no bridges, no cut nodes
    assert analyze.bridges(g) == []
    assert analyze.cut_nodes(g) == []


def test_metrics_summary_and_bounded():
    g = _chain("a", "b", "c")
    g.truncated = True
    g.elided = 9
    g.frontier["c"] = 9
    m = analyze.metrics(g)
    assert m.summary["nodes"] == 3 and m.summary["edges"] == 2
    assert m.summary["most_unstable"] == "a" and m.summary["most_depended_on"] in {"b", "c"}
    assert m.bounded["truncated"] is True and m.bounded["elided"] == 9
    assert m.bounded["frontier"] == {"c": 9}
