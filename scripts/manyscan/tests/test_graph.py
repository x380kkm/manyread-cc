"""Tests for manyscan.lib.graph — model + bounded BFS + topo/scc/rollup."""
from __future__ import annotations

from lib import graph
from lib.graph import Budget, Edge, Graph, Node, Step


def _make_expand(adj: dict[str, list[str]]):
    """Build an expand(node_id)->Steps callback from a plain adjacency dict."""

    def expand(nid: str):
        for nxt in adj.get(nid, []):
            yield Step(
                edge=Edge(src=nid, dst=nxt, relation="dep"),
                node=Node(id=nxt, kind="n", label=nxt),
            )

    return expand


def test_edge_dedup_accrues_weight():
    g = Graph()
    g.add_node(Node("a", "n"))
    g.add_node(Node("b", "n"))
    assert g.add_edge(Edge("a", "b", "dep")) is True
    assert g.add_edge(Edge("a", "b", "dep", weight=2)) is False  # dup
    assert len(g.edges) == 1
    assert g.edges[0].weight == 3


def test_edge_requires_nodes():
    g = Graph()
    g.add_node(Node("a", "n"))
    try:
        g.add_edge(Edge("a", "missing", "dep"))
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_bfs_bounded_node_cap():
    # a 1000-node chain 0->1->...->999
    adj = {str(i): [str(i + 1)] for i in range(999)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=50, max_depth=10_000))
    assert len(g) <= 50
    assert g.truncated is True


def test_bfs_bounded_depth_cap():
    adj = {str(i): [str(i + 1)] for i in range(999)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=10_000, max_depth=2))
    assert set(g.nodes) == {"0", "1", "2"}  # seed + 2 hops, no deeper


def test_bfs_records_edges_between_known_nodes_at_cap():
    # diamond beyond cap still wires edges among admitted nodes
    adj = {"s": ["a", "b"], "a": ["t"], "b": ["t"]}
    g = graph.bfs_bounded([Node("s", "n")], _make_expand(adj), Budget(max_nodes=4, max_depth=5))
    assert len(g) == 4
    # both a->t and b->t should be present since t was admitted
    rels = {(e.src, e.dst) for e in g.edges}
    assert ("a", "t") in rels and ("b", "t") in rels


def test_bfs_level_complete_truncation():
    # seed -> c (depth1); c -> 100 leaves (depth2). budget caps at 10 nodes.
    adj = {"seed": ["c"], "c": [f"L{i}" for i in range(100)]}
    g = graph.bfs_bounded([Node("seed", "n")], _make_expand(adj), Budget(max_nodes=10, max_depth=5))
    assert set(g.nodes) == {"seed", "c"}  # depth-1 complete; depth-2 declined WHOLE
    assert g.truncated is True
    assert g.frontier_depth == 1
    assert g.frontier.get("c") == 100  # honest: the 100 elided deps are recorded, not dropped
    assert g.elided == 100


def test_bfs_depth_bounded_flag():
    adj = {str(i): [str(i + 1)] for i in range(10)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=999, max_depth=3))
    assert set(g.nodes) == {"0", "1", "2", "3"}
    assert g.depth_bounded is True  # stopped by depth, not budget
    assert g.truncated is False
    assert g.frontier_depth == 3


def test_bfs_seed_cap():
    # more seeds than the node budget -> capped, honestly truncated
    seeds = [Node(str(i), "n") for i in range(5)]
    g = graph.bfs_bounded(seeds, _make_expand({}), Budget(max_nodes=3, max_depth=2))
    assert len(g) == 3 and g.truncated is True


def test_bfs_early_stop_on_wide_level_stays_bounded():
    # level-1 fits (10 children); level-2 would overflow -> declined whole, and the
    # frontier enumeration stops early (bounds memory) without breaking the cap.
    adj = {"s": [f"a{i}" for i in range(10)]}
    for i in range(10):
        adj[f"a{i}"] = [f"b{i}"]
    g = graph.bfs_bounded([Node("s", "n")], _make_expand(adj), Budget(max_nodes=12, max_depth=5))
    assert len(g) <= 12 and g.truncated is True
    assert set(g.nodes) == {"s"} | {f"a{i}" for i in range(10)}  # level-2 not partially added


def test_toposort_dag():
    g = Graph()
    for n in "abc":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "c", "dep"))
    g.add_edge(Edge("a", "c", "dep"))
    order, leftover = graph.toposort(g)
    assert leftover == []
    assert order.index("a") < order.index("b") < order.index("c")


def test_toposort_detects_cycle():
    g = Graph()
    g.add_node(Node("a", "n"))
    g.add_node(Node("b", "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "a", "dep"))
    order, leftover = graph.toposort(g)
    assert set(leftover) == {"a", "b"}


def test_scc_finds_cycle():
    g = Graph()
    for n in "abc":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "a", "dep"))  # a<->b cycle
    comps = graph.scc(g)
    cyclic = [c for c in comps if len(c) > 1]
    assert cyclic == [["a", "b"]]
    assert {"c"} in [set(c) for c in comps]


def test_scc_large_chain_no_recursion_error():
    # iterative Tarjan must handle a deep chain without hitting recursion limits
    g = Graph()
    for i in range(5000):
        g.add_node(Node(str(i), "n"))
    for i in range(4999):
        g.add_edge(Edge(str(i), str(i + 1), "dep"))
    comps = graph.scc(g)
    assert len(comps) == 5000  # all singletons, no cycle


def test_rollup_collapses_and_aggregates():
    g = Graph()
    g.add_node(Node("A/f1", "file", attrs={"dir": "A"}))
    g.add_node(Node("A/f2", "file", attrs={"dir": "A"}))
    g.add_node(Node("B/f3", "file", attrs={"dir": "B"}))
    g.add_edge(Edge("A/f1", "A/f2", "dep"))  # intra-group -> dropped
    g.add_edge(Edge("A/f1", "B/f3", "dep"))
    g.add_edge(Edge("A/f2", "B/f3", "dep"))
    rolled = graph.rollup(g, group_of=lambda n: n.attrs["dir"])
    assert set(rolled.nodes) == {"A", "B"}
    assert rolled.nodes["A"].attrs["members"] == 2
    a_to_b = [e for e in rolled.edges if e.src == "A" and e.dst == "B"]
    assert len(a_to_b) == 1 and a_to_b[0].weight == 2  # two cross edges aggregated
