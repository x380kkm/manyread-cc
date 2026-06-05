"""manyscan.lib.graph 的测试 —— 模型 + 有界 BFS + topo/scc/rollup。"""
from __future__ import annotations

from lib import graph
from lib.graph import Budget, Edge, Graph, Node, Step


#### 由邻接字典构造 expand(node_id)->Steps 回调 [@380kkm 2026-06-05] ####
def _make_expand(adj: dict[str, list[str]]):
    #### 对单个节点逐个产出指向其后继的 Step（边 + 目标节点） [@380kkm 2026-06-05] ####
    def expand(nid: str):
        for nxt in adj.get(nid, []):
            yield Step(
                edge=Edge(src=nid, dst=nxt, relation="dep"),
                node=Node(id=nxt, kind="n", label=nxt),
            )

    return expand
#### /由邻接字典构造 expand 回调 ####


#### 重复加边不新增边而累加其权重 [@380kkm 2026-06-05] ####
def test_edge_dedup_accrues_weight():
    g = Graph()
    g.add_node(Node("a", "n"))
    g.add_node(Node("b", "n"))
    assert g.add_edge(Edge("a", "b", "dep")) is True
    # 重复
    assert g.add_edge(Edge("a", "b", "dep", weight=2)) is False
    assert len(g.edges) == 1
    assert g.edges[0].weight == 3


#### 加边时两端节点都必须已存在，否则抛 KeyError [@380kkm 2026-06-05] ####
def test_edge_requires_nodes():
    g = Graph()
    g.add_node(Node("a", "n"))
    try:
        g.add_edge(Edge("a", "missing", "dep"))
        assert False, "expected KeyError"
    except KeyError:
        pass


#### 有界 BFS 在节点数上限处封顶并标记 truncated [@380kkm 2026-06-05] ####
def test_bfs_bounded_node_cap():
    # 一条 1000 节点的链 0->1->...->999
    adj = {str(i): [str(i + 1)] for i in range(999)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=50, max_depth=10_000))
    assert len(g) <= 50
    assert g.truncated is True


#### 有界 BFS 在深度上限处停止（种子 + 2 跳，不再深入） [@380kkm 2026-06-05] ####
def test_bfs_bounded_depth_cap():
    adj = {str(i): [str(i + 1)] for i in range(999)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=10_000, max_depth=2))
    # 种子 + 2 跳，不再更深
    assert set(g.nodes) == {"0", "1", "2"}


#### 封顶时仍记录已纳入节点之间的边（菱形跨越上限仍连边） [@380kkm 2026-06-05] ####
def test_bfs_records_edges_between_known_nodes_at_cap():
    # 超过上限的菱形仍在已纳入的节点间连边
    adj = {"s": ["a", "b"], "a": ["t"], "b": ["t"]}
    g = graph.bfs_bounded([Node("s", "n")], _make_expand(adj), Budget(max_nodes=4, max_depth=5))
    assert len(g) == 4
    # t 已被纳入，故 a->t 与 b->t 都应在场
    rels = {(e.src, e.dst) for e in g.edges}
    assert ("a", "t") in rels and ("b", "t") in rels


#### 整层完整或整层放弃的截断：诚实记录被省略的依赖数 [@380kkm 2026-06-05] ####
def test_bfs_level_complete_truncation():
    # 种子 -> c（深度 1）；c -> 100 个叶子（深度 2）。预算上限 10 个节点。
    adj = {"seed": ["c"], "c": [f"L{i}" for i in range(100)]}
    g = graph.bfs_bounded([Node("seed", "n")], _make_expand(adj), Budget(max_nodes=10, max_depth=5))
    # 深度 1 完整；深度 2 被整层拒绝
    assert set(g.nodes) == {"seed", "c"}
    assert g.truncated is True
    assert g.frontier_depth == 1
    # 诚实：100 个被省略的依赖被记录而非丢弃
    assert g.frontier.get("c") == 100
    assert g.elided == 100


#### 仅因深度（而非预算）停止时设 depth_bounded 而非 truncated [@380kkm 2026-06-05] ####
def test_bfs_depth_bounded_flag():
    adj = {str(i): [str(i + 1)] for i in range(10)}
    g = graph.bfs_bounded([Node("0", "n")], _make_expand(adj), Budget(max_nodes=999, max_depth=3))
    assert set(g.nodes) == {"0", "1", "2", "3"}
    # 因深度而停，非预算
    assert g.depth_bounded is True
    assert g.truncated is False
    assert g.frontier_depth == 3


#### 种子数超过节点预算时被封顶且诚实标记 truncated [@380kkm 2026-06-05] ####
def test_bfs_seed_cap():
    # 种子多于节点预算 -> 封顶，诚实截断
    seeds = [Node(str(i), "n") for i in range(5)]
    g = graph.bfs_bounded(seeds, _make_expand({}), Budget(max_nodes=3, max_depth=2))
    assert len(g) == 3 and g.truncated is True


#### 宽层时提前停止枚举仍保持有界（下一层不被部分纳入） [@380kkm 2026-06-05] ####
def test_bfs_early_stop_on_wide_level_stays_bounded():
    # 第 1 层放得下，第 2 层会溢出 -> 整层放弃，frontier 枚举提前停止
    adj = {"s": [f"a{i}" for i in range(10)]}
    for i in range(10):
        adj[f"a{i}"] = [f"b{i}"]
    g = graph.bfs_bounded([Node("s", "n")], _make_expand(adj), Budget(max_nodes=12, max_depth=5))
    assert len(g) <= 12 and g.truncated is True
    # 第 2 层未被部分加入
    assert set(g.nodes) == {"s"} | {f"a{i}" for i in range(10)}


#### DAG 的拓扑排序：无残留，且边方向被排序尊重 [@380kkm 2026-06-05] ####
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


#### 拓扑排序检测出环并把环上节点列为残留 [@380kkm 2026-06-05] ####
def test_toposort_detects_cycle():
    g = Graph()
    g.add_node(Node("a", "n"))
    g.add_node(Node("b", "n"))
    g.add_edge(Edge("a", "b", "dep"))
    g.add_edge(Edge("b", "a", "dep"))
    order, leftover = graph.toposort(g)
    assert set(leftover) == {"a", "b"}


#### SCC 找出强连通环并把无环节点列为单元素分量 [@380kkm 2026-06-05] ####
def test_scc_finds_cycle():
    g = Graph()
    for n in "abc":
        g.add_node(Node(n, "n"))
    g.add_edge(Edge("a", "b", "dep"))
    # a<->b 环
    g.add_edge(Edge("b", "a", "dep"))
    comps = graph.scc(g)
    cyclic = [c for c in comps if len(c) > 1]
    assert cyclic == [["a", "b"]]
    assert {"c"} in [set(c) for c in comps]


#### 深链上的迭代式 Tarjan 不触发递归深度上限 [@380kkm 2026-06-05] ####
def test_scc_large_chain_no_recursion_error():
    # 迭代式 Tarjan 必须能处理深链而不触及递归上限
    g = Graph()
    for i in range(5000):
        g.add_node(Node(str(i), "n"))
    for i in range(4999):
        g.add_edge(Edge(str(i), str(i + 1), "dep"))
    comps = graph.scc(g)
    # 全为单元素，无环
    assert len(comps) == 5000


#### rollup 按分组折叠节点：丢弃组内边、聚合跨组边权重 [@380kkm 2026-06-05] ####
def test_rollup_collapses_and_aggregates():
    g = Graph()
    g.add_node(Node("A/f1", "file", attrs={"dir": "A"}))
    g.add_node(Node("A/f2", "file", attrs={"dir": "A"}))
    g.add_node(Node("B/f3", "file", attrs={"dir": "B"}))
    # 组内 -> 丢弃
    g.add_edge(Edge("A/f1", "A/f2", "dep"))
    g.add_edge(Edge("A/f1", "B/f3", "dep"))
    g.add_edge(Edge("A/f2", "B/f3", "dep"))
    rolled = graph.rollup(g, group_of=lambda n: n.attrs["dir"])
    assert set(rolled.nodes) == {"A", "B"}
    assert rolled.nodes["A"].attrs["members"] == 2
    a_to_b = [e for e in rolled.edges if e.src == "A" and e.dst == "B"]
    # 两条跨组边被聚合
    assert len(a_to_b) == 1 and a_to_b[0].weight == 2
