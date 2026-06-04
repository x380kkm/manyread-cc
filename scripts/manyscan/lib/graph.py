# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.graph —— 内存中的节点/边模型 + 图算法。

纯函数、无 IO：本模块从不触碰存储库。调用方（scope/deps）注入一个 ``expand``
回调，由它从真实的 manyread 存储库产出邻居；这里的算法只操作得到的
:class:`Graph`。

核心是 :func:`bfs_bounded` —— 从种子节点出发、带预算的广度优先扩展。它正是
"一个问题不会拖入整个引擎"的机制：扩展在 ``budget.max_nodes`` /
``budget.max_depth`` 处停止，返回的图保证至多持有 ``max_nodes`` 个节点。
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable


#### 节点/边的来源凭证，使每条断言可核查 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Evidence:
    path: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path or ""
#### /来源凭证 ####


#### 图节点，以 id（kind|store|key）标识，按 id 判等 [@380kkm 2026-06-05] ####
@dataclass
class Node:
    id: str
    #### symbol | file | dir | module | external | ...
    kind: str
    label: str = ""
    store: str | None = None
    evidence: Evidence | None = None
    attrs: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and other.id == self.id
#### /图节点 ####


#### 有向边 ``src -> dst``，携带关系 + 凭证 + 权重 [@380kkm 2026-06-05] ####
@dataclass
class Edge:
    #### node id
    src: str
    #### node id
    dst: str
    relation: str
    evidence: Evidence | None = None
    weight: int = 1

    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.relation)
#### /有向边 ####


#### 一次扩展步：到达的邻居 ``node`` 及指向它的 ``edge`` [@380kkm 2026-06-05] ####
@dataclass
class Step:
    edge: Edge
    node: Node
#### /扩展步 ####


#### :func:`bfs_bounded` 的硬边界；``direction`` 仅为建议（由 expand 决定）[@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Budget:
    max_nodes: int = 200
    max_depth: int = 3
    #### out | in | both
    direction: str = "both"
#### /预算 ####


#### 有向多重图：节点以 id 为键，附带邻接索引 [@380kkm 2026-06-05] ####
class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._edge_keys: set[tuple[str, str, str]] = set()
        #### node id -> 边下标列表
        self._out: dict[str, list[int]] = defaultdict(list)
        self._in: dict[str, list[int]] = defaultdict(list)

        #### 有界扩展的记账（诚实截断；详见 bfs_bounded）[@380kkm 2026-06-05] ####
        #### 因节点预算整层被拒
        self.truncated: bool = False
        #### 在 max_depth 处停止（边缘可能更深）
        self.depth_bounded: bool = False
        #### 完整纳入的最深层级
        self.frontier_depth: int = 0
        #### node id -> 因预算省略的出向依赖计数
        self.frontier: dict[str, int] = {}
        #### 边界处被拒的不重复节点总数
        self.elided: int = 0
        #### /有界扩展记账 ####

    #### 节点不存在则插入，返回该 id 对应的已存节点 [@380kkm 2026-06-05] ####
    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is not None:
            return existing
        self.nodes[node.id] = node
        return node

    #### 插入边（按 (src,dst,relation) 去重），新增返回 True [@380kkm 2026-06-05] ####
    def add_edge(self, edge: Edge) -> bool:
        """插入 ``edge``（按 (src,dst,relation) 去重）；首次新增返回 True。

        两端必须已作为节点存在。重复边将权重累加到已有边上，而不新增一条平行边。
        """
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            raise KeyError(f"edge endpoints must be added as nodes first: {edge.key()}")
        if edge.key() in self._edge_keys:
            for e in self.edges:
                if e.key() == edge.key():
                    e.weight += edge.weight
                    return False
        idx = len(self.edges)
        self.edges.append(edge)
        self._edge_keys.add(edge.key())
        self._out[edge.src].append(idx)
        self._in[edge.dst].append(idx)
        return True

    #### 取某节点的出向边 [@380kkm 2026-06-05] ####
    def out_edges(self, node_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._out.get(node_id, ())]

    #### 取某节点的入向边 [@380kkm 2026-06-05] ####
    def in_edges(self, node_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._in.get(node_id, ())]

    #### 取某节点的后继 id [@380kkm 2026-06-05] ####
    def successors(self, node_id: str) -> list[str]:
        return [self.edges[i].dst for i in self._out.get(node_id, ())]

    #### 取某节点的前驱 id [@380kkm 2026-06-05] ####
    def predecessors(self, node_id: str) -> list[str]:
        return [self.edges[i].src for i in self._in.get(node_id, ())]

    #### 由 ``node_ids`` 诱导出的子图（两端都保留的边才纳入）[@380kkm 2026-06-05] ####
    def subgraph(self, node_ids: Iterable[str]) -> "Graph":
        keep = {nid for nid in node_ids if nid in self.nodes}
        g = Graph()
        for nid in keep:
            g.add_node(self.nodes[nid])
        for e in self.edges:
            if e.src in keep and e.dst in keep:
                g.add_edge(Edge(e.src, e.dst, e.relation, e.evidence, e.weight))
        return g

    def __len__(self) -> int:
        return len(self.nodes)
#### /有向多重图 ####


#### 逐层完整的有预算 BFS（基石）[@380kkm 2026-06-05] ####
def bfs_bounded(
    seed_nodes: Iterable[Node],
    expand: Callable[[str], Iterable[Step]],
    budget: Budget,
) -> Graph:
    """从 ``seed_nodes`` 出发、经 ``expand(id)->Steps`` 的逐层完整有预算 BFS。

    返回的切片始终**完整到某个整层**——绝不会是会歪曲依赖全貌的、停在前沿中段的
    任意片段。扩展逐层推进；若纳入整个下一层会超过 ``budget.max_nodes``，则该层
    **整体被拒**，且边界被诚实记录而非悄悄丢弃：

      * ``truncated``      —— 因节点预算拒掉某一层时为 True。
      * ``frontier[id]``   —— 各源在边界处被省略的出向依赖计数。
      * ``elided``         —— 边界处被拒的不重复节点总数。
      * ``depth_bounded``  —— 在 ``max_depth`` 处停止扩展时为 True（边缘可能更深；
                             这是刻意的 N 层邻域）。
      * ``frontier_depth`` —— 完整纳入的最深层级。

    保证 ``len(result) <= budget.max_nodes``。在被拒边界处，已纳入节点之间的闭合
    边仍会被记录，使内部结构保持完整。
    """
    g = Graph()
    frontier: list[str] = []
    for n in seed_nodes:
        if len(g.nodes) >= budget.max_nodes:
            #### 种子数超过节点预算——诚实地截断
            g.truncated = True
            break
        g.add_node(n)
        frontier.append(n.id)
    frontier = list(dict.fromkeys(frontier))

    depth = 0
    while frontier and depth < budget.max_depth:
        depth += 1
        steps: list[tuple[str, Step]] = []
        new_ids: set[str] = set()
        overflow = False
        for src in frontier:

            #### 本层一旦超预算就会整体被拒，故在此停止枚举余下前沿——把扫描+内存界定在约 max_nodes 量级的步数内，而非 O(前沿 * 邻居) [@380kkm 2026-06-05] ####
            if len(g.nodes) + len(new_ids) > budget.max_nodes:
                overflow = True
                break
            #### /提前止枚举 ####

            for step in expand(src):
                steps.append((src, step))
                if step.node.id not in g.nodes:
                    new_ids.add(step.node.id)

        if overflow or len(g.nodes) + len(new_ids) > budget.max_nodes:

            #### 整层被拒——部分层会歪曲切片；记录诚实的边界 [@380kkm 2026-06-05] ####
            #### （提前 break 时，省略计数为下界——仍然诚实）
            g.truncated = True
            g.frontier_depth = depth - 1
            per_src: dict[str, set[str]] = {}
            for src, step in steps:
                if step.node.id not in g.nodes:
                    per_src.setdefault(src, set()).add(step.node.id)
                elif step.edge.src in g.nodes and step.edge.dst in g.nodes:
                    #### 已纳入节点之间的闭合边
                    g.add_edge(step.edge)
            for src, ids in per_src.items():
                g.frontier[src] = g.frontier.get(src, 0) + len(ids)
            g.elided = len(new_ids)
            return g
            #### /整层被拒 ####

        #### 该层放得下——整体纳入 [@380kkm 2026-06-05] ####
        nxt: list[str] = []
        for src, step in steps:
            newly = step.node.id not in g.nodes
            g.add_node(step.node)
            if step.edge.src in g.nodes and step.edge.dst in g.nodes:
                g.add_edge(step.edge)
            if newly:
                nxt.append(step.node.id)
        frontier = list(dict.fromkeys(nxt))
        #### /整体纳入 ####

    g.frontier_depth = depth
    if frontier and depth >= budget.max_depth:
        #### 边缘节点可能有更深依赖（受深度所限，非预算）
        g.depth_bounded = True
    return g
#### /有预算 BFS ####


#### Kahn 拓扑排序，返回 (order, leftover) [@380kkm 2026-06-05] ####
def toposort(graph: Graph) -> tuple[list[str], list[str]]:
    """Kahn 拓扑排序。返回 ``(order, leftover)``。

    当不再有入度为 0 的节点时，``leftover`` 是仍带有入向边的节点 id——即参与环
    （或处于环下游）的那些 id。
    """
    indeg: dict[str, int] = {nid: 0 for nid in graph.nodes}
    for e in graph.edges:
        if e.src != e.dst:
            #### 排序时忽略自环
            indeg[e.dst] += 1
    ready = deque(sorted(nid for nid, d in indeg.items() if d == 0))
    order: list[str] = []
    while ready:
        nid = ready.popleft()
        order.append(nid)
        for nxt in sorted(set(graph.successors(nid))):
            if nxt == nid:
                continue
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    leftover = sorted(nid for nid, d in indeg.items() if d > 0)
    return order, leftover
#### /拓扑排序 ####


#### 迭代式 Tarjan 求强连通分量（大图栈安全）[@380kkm 2026-06-05] ####
def scc(graph: Graph) -> list[list[str]]:
    """用迭代式 Tarjan 求强连通分量（对大图栈安全）。

    返回分量列表（每个为排序后的节点 id 列表）。节点数 >1 的分量——或带自环的
    单节点——即为环。
    """
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in graph.nodes:
        if root in index_of:
            continue

        #### 迭代式 DFS：栈帧为 (节点, 后继迭代器)
        work: list[tuple[str, list[str]]] = [(root, sorted(set(graph.successors(root))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, succs = work[-1]
            advanced = False
            while succs:
                nxt = succs.pop(0)
                if nxt == node:
                    continue
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(set(graph.successors(nxt)))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
            if advanced:
                continue
            if low[node] == index_of[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                result.append(sorted(comp))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result
#### /强连通分量 ####


#### 按 ``group_of`` 把节点折叠为分组 [@380kkm 2026-06-05] ####
def rollup(graph: Graph, group_of: Callable[[Node], str],
           group_label: Callable[[str], str] | None = None) -> Graph:
    """经 ``group_of(node) -> group_id`` 把节点折叠成分组。

    组内边被丢弃；跨组边被聚合（权重相加）。分组节点 kind 为 ``group``，并带
    ``attrs['members']`` 计数。
    """
    g = Graph()
    members: dict[str, int] = defaultdict(int)
    node_group: dict[str, str] = {}
    for node in graph.nodes.values():
        gid = group_of(node)
        node_group[node.id] = gid
        members[gid] += 1
    for gid, count in members.items():
        label = group_label(gid) if group_label else gid
        g.add_node(Node(id=gid, kind="group", label=label, attrs={"members": count}))
    for e in graph.edges:
        gs, gd = node_group.get(e.src), node_group.get(e.dst)
        if gs is None or gd is None or gs == gd:
            continue
        g.add_edge(Edge(src=gs, dst=gd, relation=e.relation, weight=e.weight))
    return g
#### /折叠分组 ####
