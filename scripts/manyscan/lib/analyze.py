# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.analyze
"""manyscan.lib.analyze —— 依赖 Graph 之上的重构支持度量（纯函数）。

给定任意一个 :class:`graph.Graph`（一个有界/已 rollup 的切片），计算团队推理
模块化与重构所需的信号，且从不改动该 graph：

  * 逐节点耦合：``fan_in``/``fan_out`` 以及 Martin 的 ``Ca``/``Ce``/``instability``
    （``Ce/(Ca+Ce)``；0 = 稳定，1 = 不稳定）。
  * ``cycles``    —— 强连通分组（>1 个节点），必须打破才能解耦。
  * ``bridges``   —— 移除后会切开 graph 的边（待切的候选缝）。
  * ``cut_nodes`` —— 移除后会切开 graph 的关节节点（脆弱的枢纽）。
  * ``layers``    —— 拓扑分层（``leftover`` = 缠在 cycle 里的节点）。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from lib import graph
from lib.graph import Graph


#### 单个节点的耦合度量 [@380kkm 2026-06-05] ####
@dataclass
class NodeMetric:
    id: str
    label: str
    fan_in: int
    fan_out: int
    # afferent coupling（依赖我的）
    ca: int
    # efferent coupling（我依赖的）
    ce: int
    # Ce / (Ca + Ce)
    instability: float


#### 一个 graph 切片的完整度量集 [@380kkm 2026-06-05] ####
@dataclass
class Metrics:
    nodes: list[NodeMetric]
    cycles: list[list[str]]
    bridges: list[tuple[str, str, str]]
    cut_nodes: list[str]
    layers: list[list[str]]
    leftover: list[str]
    bounded: dict
    summary: dict


#### 计算逐节点耦合度量，按最不稳定 / 被依赖最多优先排序 [@380kkm 2026-06-05] ####
def node_metrics(g: Graph) -> list[NodeMetric]:
    out: list[NodeMetric] = []
    for nid, node in g.nodes.items():
        ca = len({p for p in g.predecessors(nid) if p != nid})
        ce = len({s for s in g.successors(nid) if s != nid})
        instab = ce / (ca + ce) if (ca + ce) > 0 else 0.0
        out.append(NodeMetric(nid, node.label or nid, ca, ce, ca, ce, round(instab, 3)))
    out.sort(key=lambda m: (-m.instability, -m.fan_in, m.id))
    return out


#### 找出真正构成 cycle 的强连通分量 [@380kkm 2026-06-05] ####
def cycles(g: Graph) -> list[list[str]]:
    """>1 个节点、或带自环的单节点，方计入。"""
    self_loops = {e.src for e in g.edges if e.src == e.dst}
    return [c for c in graph.scc(g) if len(c) > 1 or (len(c) == 1 and c[0] in self_loops)]


#### 构建忽略自环与方向的无向邻接表 [@380kkm 2026-06-05] ####
def _undirected_adj(g: Graph) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for nid in g.nodes:
        adj.setdefault(nid, set())
    for e in g.edges:
        if e.src != e.dst:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    return adj


#### 数无向图的连通分量数，可选跳过某节点 / 移除某条边 [@380kkm 2026-06-05] ####
def _count_components(nodes, adj, skip_node=None, remove_pair=None) -> int:
    rp = frozenset(remove_pair) if remove_pair else None
    seen: set[str] = set()
    comps = 0
    for start in nodes:
        if start == skip_node or start in seen:
            continue
        comps += 1
        stack = [start]
        seen.add(start)
        while stack:
            x = stack.pop()
            for y in adj.get(x, ()):
                if y == skip_node:
                    continue
                if rp is not None and frozenset((x, y)) == rp:
                    continue
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
    return comps


#### 找出移除后会增加连通分量数的边（无向桥） [@380kkm 2026-06-05] ####
def bridges(g: Graph) -> list[tuple[str, str, str]]:
    """一对节点间的平行边永远不是桥，故只测试唯一连接的边。"""
    adj = _undirected_adj(g)
    nodes = list(g.nodes)
    mult: Counter = Counter(frozenset((e.src, e.dst)) for e in g.edges if e.src != e.dst)
    base = _count_components(nodes, adj)
    out: list[tuple[str, str, str]] = []
    for e in g.edges:
        if e.src == e.dst or mult[frozenset((e.src, e.dst))] != 1:
            continue
        if _count_components(nodes, adj, remove_pair=(e.src, e.dst)) > base:
            out.append((e.src, e.dst, e.relation))
    return out


#### 找出移除后会增加连通分量数的关节节点 [@380kkm 2026-06-05] ####
def cut_nodes(g: Graph) -> list[str]:
    adj = _undirected_adj(g)
    nodes = list(g.nodes)
    base = _count_components(nodes, adj)
    return sorted(v for v in nodes if _count_components(nodes, adj, skip_node=v) > base)


#### 按拓扑序把节点分到各层，cycle 节点归为 leftover [@380kkm 2026-06-05] ####
def layers(g: Graph) -> tuple[list[list[str]], list[str]]:
    """层号 = 1 + 前驱最大层号；leftover = cycle 节点。"""
    order, leftover = graph.toposort(g)
    layer: dict[str, int] = {}
    for nid in order:
        preds = [p for p in g.predecessors(nid) if p in layer and p != nid]
        layer[nid] = max((layer[p] for p in preds), default=-1) + 1
    by_layer: dict[int, list[str]] = defaultdict(list)
    for nid in order:
        by_layer[layer[nid]].append(nid)
    return [sorted(by_layer[k]) for k in sorted(by_layer)], leftover


#### 装配某 graph 切片的完整重构支持度量集 [@380kkm 2026-06-05] ####
def metrics(g: Graph) -> Metrics:
    nms = node_metrics(g)
    cy = cycles(g)
    br = bridges(g)
    cn = cut_nodes(g)
    ly, leftover = layers(g)
    summary = {
        "nodes": len(g.nodes),
        "edges": len(g.edges),
        "cycles": len(cy),
        "bridges": len(br),
        "cut_nodes": len(cn),
        "layers": len(ly),
        "most_unstable": nms[0].label if nms else None,
        "most_depended_on": (max(nms, key=lambda m: m.fan_in).label if nms else None),
    }
    bounded = {
        "truncated": g.truncated,
        "depth_bounded": g.depth_bounded,
        "frontier_depth": g.frontier_depth,
        "elided": g.elided,
        "frontier": dict(g.frontier),
    }
    return Metrics(nms, cy, br, cn, ly, leftover, bounded, summary)
