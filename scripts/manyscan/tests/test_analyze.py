"""manyscan.lib.analyze 的测试 —— 耦合 / 环 / 桥 / 割点 / 分层。"""
from __future__ import annotations

from lib import analyze
from lib.graph import Edge, Graph, Node


#### 由若干 id 构造一条链式有向图 [@380kkm 2026-06-05] ####
def _chain(*ids):
    g = Graph()
    for i in ids:
        g.add_node(Node(i, "n", label=i))
    for a, b in zip(ids, ids[1:]):
        g.add_edge(Edge(a, b, "dep"))
    return g
#### /链式有向图构造器 ####


#### 测试链上各节点的 ca/ce/不稳定度（纯源、中间、纯汇） [@380kkm 2026-06-05] ####
def test_instability_chain():
    # a -> b -> c
    g = _chain("a", "b", "c")
    by = {m.id: m for m in analyze.node_metrics(g)}
    # 纯源
    assert (by["a"].ca, by["a"].ce, by["a"].instability) == (0, 1, 1.0)
    assert (by["b"].ca, by["b"].ce, by["b"].instability) == (1, 1, 0.5)
    # 纯汇
    assert (by["c"].ca, by["c"].ce, by["c"].instability) == (1, 0, 0.0)


#### 测试环检测与分层（双向边成环、链可分层） [@380kkm 2026-06-05] ####
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


#### 测试星形图的桥与割点（每条辐为桥、中心为割点） [@380kkm 2026-06-05] ####
def test_bridges_and_cut_nodes_star():
    # 中心 h -> a, b, c：每条辐都是桥，h 是关节点
    g = Graph()
    for n in "habc":
        g.add_node(Node(n, "n"))
    for leaf in "abc":
        g.add_edge(Edge("h", leaf, "dep"))
    assert sorted(b[1] for b in analyze.bridges(g)) == ["a", "b", "c"]
    assert analyze.cut_nodes(g) == ["h"]


#### 测试三角环既无桥也无割点 [@380kkm 2026-06-05] ####
def test_triangle_has_no_bridges():
    g = Graph()
    for n in "abc":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "c", "dep"))
    # 成环 -> 无桥、无割点
    g.add_edge(Edge("c", "a", "dep"))
    assert analyze.bridges(g) == []
    assert analyze.cut_nodes(g) == []


#### 测试 metrics 汇总与有界元信息（truncated/elided/frontier） [@380kkm 2026-06-05] ####
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
