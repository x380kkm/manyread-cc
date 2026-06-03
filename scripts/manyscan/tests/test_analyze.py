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


def _modular_graph():
    # module 'mod' = two disconnected internal clusters (a-b) and (c-d);
    # module 'ok' = one connected component (x-y-z).
    g = Graph()
    for nid in ["mod/a", "mod/b", "mod/c", "mod/d", "ok/x", "ok/y", "ok/z"]:
        g.add_node(Node(nid, "file", label=nid))
    g.add_edge(Edge("mod/a", "mod/b", "imports"))
    g.add_edge(Edge("mod/c", "mod/d", "imports"))
    g.add_edge(Edge("ok/x", "ok/y", "imports"))
    g.add_edge(Edge("ok/y", "ok/z", "imports"))
    return g


def _group(n):
    return n.id.split("/")[0]


def test_srp_flags_multi_responsibility_and_clusters():
    reports = {r.module: r for r in analyze.srp(_modular_graph(), _group)}
    mod, ok = reports["mod"], reports["ok"]
    assert mod.components == 2 and mod.multi_responsibility is True
    assert sorted(c.size for c in mod.clusters) == [2, 2]      # two responsibilities
    assert ok.components == 1 and ok.multi_responsibility is False  # cohesive


def test_srp_seams_are_internal_bridges():
    # one module, one connected chain a-b-c -> bridges (a,b) and (b,c) are the seams
    g = Graph()
    for nid in ["m/a", "m/b", "m/c"]:
        g.add_node(Node(nid, "file", label=nid))
    g.add_edge(Edge("m/a", "m/b", "imports"))
    g.add_edge(Edge("m/b", "m/c", "imports"))
    rep = analyze.srp(g, _group)[0]
    assert rep.components == 1
    assert {frozenset((a, b)) for a, b, _ in rep.seams} == {frozenset(("m/a", "m/b")),
                                                            frozenset(("m/b", "m/c"))}


def test_cluster_of_labels_split_module():
    cl = analyze.cluster_of(_modular_graph(), _group)
    # split module -> mod#0 / mod#1 (two labels); cohesive module -> single label 'ok'
    assert {cl["mod/a"], cl["mod/c"]} == {"mod#0", "mod#1"}
    assert cl["mod/a"] == cl["mod/b"]                 # same responsibility, same label
    assert cl["ok/x"] == cl["ok/y"] == cl["ok/z"] == "ok"


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
