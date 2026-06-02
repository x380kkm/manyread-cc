# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.graph — the in-memory node/edge model + graph algorithms.

Pure and IO-free: this module never touches a store. Callers (scope/deps) inject
an ``expand`` callback that yields neighbours from a real manyread store; the
algorithms here only manipulate the resulting :class:`Graph`.

The keystone is :func:`bfs_bounded` — a budgeted breadth-first expansion from
seed nodes. It is the mechanism that keeps "one question from dragging in the
whole engine": expansion stops at ``budget.max_nodes`` / ``budget.max_depth`` and
the returned graph is guaranteed to hold at most ``max_nodes`` nodes.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable


@dataclass(frozen=True)
class Evidence:
    """Where a node/edge came from, so every claim is checkable."""

    path: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path or ""


@dataclass
class Node:
    """A graph node, identified by ``id`` (kind|store|key). Equality is by id."""

    id: str
    kind: str  # symbol | file | dir | module | external | ...
    label: str = ""
    store: str | None = None
    evidence: Evidence | None = None
    attrs: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and other.id == self.id


@dataclass
class Edge:
    """A directed edge ``src -> dst`` carrying its relation + evidence + weight."""

    src: str  # node id
    dst: str  # node id
    relation: str
    evidence: Evidence | None = None
    weight: int = 1

    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.relation)


@dataclass
class Step:
    """One expansion step: a reached neighbour ``node`` and the ``edge`` to it."""

    edge: Edge
    node: Node


@dataclass(frozen=True)
class Budget:
    """Hard bounds for :func:`bfs_bounded`. ``direction`` is advisory (expand decides)."""

    max_nodes: int = 200
    max_depth: int = 3
    direction: str = "both"  # out | in | both


class Graph:
    """A directed multigraph with id-keyed nodes and adjacency indexes."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._edge_keys: set[tuple[str, str, str]] = set()
        self._out: dict[str, list[int]] = defaultdict(list)  # node id -> edge indexes
        self._in: dict[str, list[int]] = defaultdict(list)
        # --- bounded-expansion accounting (honest truncation; see bfs_bounded) ---
        self.truncated: bool = False        # a whole level was declined for budget
        self.depth_bounded: bool = False    # stopped at max_depth (rim may go deeper)
        self.frontier_depth: int = 0        # deepest fully-included level
        self.frontier: dict[str, int] = {}  # node id -> count of budget-elided out-deps
        self.elided: int = 0                # total distinct nodes declined at the boundary

    # -- construction --
    def add_node(self, node: Node) -> Node:
        """Insert ``node`` if absent; return the stored node for its id."""
        existing = self.nodes.get(node.id)
        if existing is not None:
            return existing
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> bool:
        """Insert ``edge`` (dedup on (src,dst,relation)); return True if newly added.

        Endpoints must already be present as nodes. A duplicate accrues weight onto
        the existing edge instead of adding a parallel one.
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

    # -- queries --
    def out_edges(self, node_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._out.get(node_id, ())]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [self.edges[i] for i in self._in.get(node_id, ())]

    def successors(self, node_id: str) -> list[str]:
        return [self.edges[i].dst for i in self._out.get(node_id, ())]

    def predecessors(self, node_id: str) -> list[str]:
        return [self.edges[i].src for i in self._in.get(node_id, ())]

    def subgraph(self, node_ids: Iterable[str]) -> "Graph":
        """A new Graph induced by ``node_ids`` (edges with both endpoints kept)."""
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


# --- bounded expansion (the keystone) ----------------------------------------
def bfs_bounded(
    seed_nodes: Iterable[Node],
    expand: Callable[[str], Iterable[Step]],
    budget: Budget,
) -> Graph:
    """Level-complete budgeted BFS from ``seed_nodes`` via ``expand(id)->Steps``.

    The returned slice is always **complete up to a whole level** — never an
    arbitrary mid-frontier fragment that would misrepresent the dependency
    picture. Expansion proceeds level by level; if admitting an entire next level
    would exceed ``budget.max_nodes``, that level is **declined as a whole** and
    the boundary is recorded honestly instead of silently dropped:

      * ``truncated``      — True when a level was declined for the node budget.
      * ``frontier[id]``   — per-source count of out-deps elided at the boundary.
      * ``elided``         — total distinct nodes declined at the boundary.
      * ``depth_bounded``  — True when expansion stopped at ``max_depth`` (the rim
                             may go deeper; this is a deliberate N-level neighbourhood).
      * ``frontier_depth`` — the deepest fully-included level.

    Guarantees ``len(result) <= budget.max_nodes``. Closing edges among already
    included nodes are still recorded at a declined boundary so internal structure
    stays complete.
    """
    g = Graph()
    frontier: list[str] = []
    for n in seed_nodes:
        if len(g.nodes) >= budget.max_nodes:
            g.truncated = True  # more seeds than the node budget — cap honestly
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
            # Once this level is already over budget it WILL be declined whole, so
            # stop enumerating the rest of the frontier here — this bounds scan +
            # memory to ~max_nodes worth of steps, not O(frontier * neighbours).
            if len(g.nodes) + len(new_ids) > budget.max_nodes:
                overflow = True
                break
            for step in expand(src):
                steps.append((src, step))
                if step.node.id not in g.nodes:
                    new_ids.add(step.node.id)

        if overflow or len(g.nodes) + len(new_ids) > budget.max_nodes:
            # Decline the whole level — a partial level would misrepresent the slice.
            # (When we broke early, elided counts are a lower bound — still honest.)
            g.truncated = True
            g.frontier_depth = depth - 1
            per_src: dict[str, set[str]] = {}
            for src, step in steps:
                if step.node.id not in g.nodes:
                    per_src.setdefault(src, set()).add(step.node.id)
                elif step.edge.src in g.nodes and step.edge.dst in g.nodes:
                    g.add_edge(step.edge)  # closing edge between included nodes
            for src, ids in per_src.items():
                g.frontier[src] = g.frontier.get(src, 0) + len(ids)
            g.elided = len(new_ids)
            return g

        # Level fits — admit it whole.
        nxt: list[str] = []
        for src, step in steps:
            newly = step.node.id not in g.nodes
            g.add_node(step.node)
            if step.edge.src in g.nodes and step.edge.dst in g.nodes:
                g.add_edge(step.edge)
            if newly:
                nxt.append(step.node.id)
        frontier = list(dict.fromkeys(nxt))

    g.frontier_depth = depth
    if frontier and depth >= budget.max_depth:
        g.depth_bounded = True  # rim nodes may have deeper deps (depth, not budget)
    return g


# --- classic algorithms (pure) -----------------------------------------------
def toposort(graph: Graph) -> tuple[list[str], list[str]]:
    """Kahn topological sort. Returns ``(order, leftover)``.

    ``leftover`` is the node ids still carrying in-edges when no zero-indegree
    node remains — i.e. the ids participating in (or downstream of) a cycle.
    """
    indeg: dict[str, int] = {nid: 0 for nid in graph.nodes}
    for e in graph.edges:
        if e.src != e.dst:  # ignore self-loops for ordering
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


def scc(graph: Graph) -> list[list[str]]:
    """Strongly-connected components via iterative Tarjan (stack-safe for big graphs).

    Returns a list of components (each a sorted list of node ids). Components with
    >1 node — or a single node with a self-loop — are cycles.
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
        # iterative DFS: frames are (node, successor-iterator)
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


def rollup(graph: Graph, group_of: Callable[[Node], str],
           group_label: Callable[[str], str] | None = None) -> Graph:
    """Collapse nodes into groups via ``group_of(node) -> group_id``.

    Intra-group edges are dropped; cross-group edges are aggregated (weights
    summed). Group nodes get kind ``group`` and an ``attrs['members']`` count.
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
