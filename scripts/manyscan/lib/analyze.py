# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.analyze — refactoring-support metrics over a dependency Graph (pure).

Given any :class:`graph.Graph` (a scoped/rolled slice) this computes the signals a
team needs to reason about modularization & refactoring — never mutating the graph:

  * per-node coupling: ``fan_in``/``fan_out`` and Martin's ``Ca``/``Ce``/``instability``
    (``Ce/(Ca+Ce)``; 0 = stable, 1 = unstable).
  * ``cycles``    — strongly-connected groups (>1 node) that must be broken to decouple.
  * ``bridges``   — edges whose removal splits the graph (candidate seams to cut).
  * ``cut_nodes`` — articulation nodes whose removal splits it (fragile hubs).
  * ``layers``    — topological layers (``leftover`` = nodes tangled in cycles).

All connectivity checks are exact and run on the *bounded* slice, so they stay cheap.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from lib import graph
from lib.graph import Graph


@dataclass
class NodeMetric:
    id: str
    label: str
    fan_in: int
    fan_out: int
    ca: int          # afferent coupling (depend on me)
    ce: int          # efferent coupling (I depend on)
    instability: float  # Ce / (Ca + Ce)


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


def node_metrics(g: Graph) -> list[NodeMetric]:
    """Per-node coupling metrics, sorted most-unstable / most-depended-on first."""
    out: list[NodeMetric] = []
    for nid, node in g.nodes.items():
        ca = len({p for p in g.predecessors(nid) if p != nid})
        ce = len({s for s in g.successors(nid) if s != nid})
        instab = ce / (ca + ce) if (ca + ce) > 0 else 0.0
        out.append(NodeMetric(nid, node.label or nid, ca, ce, ca, ce, round(instab, 3)))
    out.sort(key=lambda m: (-m.instability, -m.fan_in, m.id))
    return out


def cycles(g: Graph) -> list[list[str]]:
    """SCCs that are real cycles (>1 node, or a single node with a self-loop)."""
    self_loops = {e.src for e in g.edges if e.src == e.dst}
    return [c for c in graph.scc(g) if len(c) > 1 or (len(c) == 1 and c[0] in self_loops)]


def _undirected_adj(g: Graph) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for nid in g.nodes:
        adj.setdefault(nid, set())
    for e in g.edges:
        if e.src != e.dst:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    return adj


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


def bridges(g: Graph) -> list[tuple[str, str, str]]:
    """Edges whose removal increases connected components (undirected bridges).

    Parallel edges between a pair are never bridges (removing one leaves the link),
    so only uniquely-connecting edges are tested.
    """
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


def cut_nodes(g: Graph) -> list[str]:
    """Articulation nodes: removing one increases connected components."""
    adj = _undirected_adj(g)
    nodes = list(g.nodes)
    base = _count_components(nodes, adj)
    return sorted(v for v in nodes if _count_components(nodes, adj, skip_node=v) > base)


def layers(g: Graph) -> tuple[list[list[str]], list[str]]:
    """Topological layers (layer = 1 + max predecessor layer); leftover = cycle nodes."""
    order, leftover = graph.toposort(g)
    layer: dict[str, int] = {}
    for nid in order:
        preds = [p for p in g.predecessors(nid) if p in layer and p != nid]
        layer[nid] = max((layer[p] for p in preds), default=-1) + 1
    by_layer: dict[int, list[str]] = defaultdict(list)
    for nid in order:
        by_layer[layer[nid]].append(nid)
    return [sorted(by_layer[k]) for k in sorted(by_layer)], leftover


# --- SRP (single-responsibility) support: cohesion + cohesive sub-clusters ----
# A module satisfying SRP tends to form ONE cohesive internal dependency cluster.
# K>1 weakly-connected clusters over a module's INTERNAL edges *suggest* K
# responsibilities (candidate split boundaries), and the cross-cluster `seams`
# are exactly the edges a split would cut. This is a STRUCTURAL PROXY — a single
# connected component is necessary-but-not-sufficient for semantic SRP; it
# confirms a human-proposed split is clean and surfaces candidates, no more.
@dataclass
class Cluster:
    members: list[str]
    size: int
    internal_edges: int


@dataclass
class SrpReport:
    module: str
    n_members: int
    internal_edges: int
    cohesion: float                       # internal density 2E/(N(N-1)), 0..1
    components: int                       # K = weakly-connected comps (internal edges)
    clusters: list[Cluster]               # K candidate split groups, largest first
    seams: list[tuple[str, str, str]]     # cross-cluster (src,dst,relation) = edges to cut
    fan_in_sources: list[str]             # distinct upstream consumer modules
    multi_responsibility: bool
    note: str


def _components_of(sub: Graph) -> list[list[str]]:
    """Weakly-connected components of an (induced) subgraph, largest first."""
    adj = _undirected_adj(sub)
    seen: set[str] = set()
    comps: list[list[str]] = []
    for start in sub.nodes:
        if start in seen:
            continue
        comp: list[str] = []
        stack = [start]
        seen.add(start)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(sorted(comp))
    comps.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return comps


def _groups(g: Graph, group_of) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for nid, node in g.nodes.items():
        grouped[group_of(node)].append(nid)
    return grouped


def cluster_of(g: Graph, group_of) -> dict[str, str]:
    """Map each node id -> a cluster label ``<module>#<k>`` (its cohesive sub-cluster).

    Used to color a rendered slice by detected responsibility-cluster. Modules with
    a single cohesive component get one label; a split module gets ``mod#0/#1/...``.
    """
    out: dict[str, str] = {}
    for mod, members in _groups(g, group_of).items():
        comps = _components_of(g.subgraph(members))
        for k, comp in enumerate(comps):
            for m in comp:
                out[m] = f"{mod}#{k}" if len(comps) > 1 else mod
    return out


def srp(g: Graph, group_of, min_cohesion: float = 0.15) -> list[SrpReport]:
    """Per-module single-responsibility report (structural proxy). Deterministic.

    For each module (grouping via ``group_of``): induce its member subgraph, find
    weakly-connected components over INTERNAL edges (K), measure internal-edge
    density (cohesion), list the K candidate clusters + the cross-cluster seams,
    and flag ``multi_responsibility`` when K>1 (or low cohesion pulled by >=2
    unrelated consumer modules). Bounded: cost is O(module edges).
    """
    reports: list[SrpReport] = []
    for mod, members in _groups(g, group_of).items():
        member_set = set(members)
        sub = g.subgraph(members)
        internal_edges = sum(1 for e in sub.edges if e.src != e.dst)
        n = len(members)
        if n <= 1:
            cohesion = 1.0
        else:
            cohesion = round((2 * internal_edges) / (n * (n - 1)), 3)
        def _lbl(nid: str) -> str:
            node = g.nodes.get(nid)
            return (node.label or nid) if node is not None else nid

        comps = _components_of(sub)
        clusters = [
            Cluster(members=[_lbl(m) for m in c], size=len(c),
                    internal_edges=sum(1 for e in sub.edges
                                       if e.src in set(c) and e.dst in set(c) and e.src != e.dst))
            for c in comps
        ]
        # K>1 components are ALREADY separable (no edges between them — a clean split).
        # For a single cohesive blob the candidate cut points are its internal BRIDGES
        # (edges whose removal further separates it) — the seams to cut if you must split.
        # Member/seam ids are mapped to human paths (node labels), not internal ids.
        seams = [(_lbl(a), _lbl(b), rel) for a, b, rel in bridges(sub)]
        sources: set[str] = set()
        for e in g.edges:
            if e.dst in member_set and e.src not in member_set:
                sn = g.nodes.get(e.src)
                if sn is not None:
                    sources.add(group_of(sn))
        fan_in_sources = sorted(sources)
        k = len(comps)
        # Flag GENUINE multi-responsibility, two honest cases:
        #  - K>1 cohesive clusters with real internal coupling (already-separable groups); OR
        #  - one sprawling weakly-cohesive blob (>=3 members, density < min_cohesion, has
        #    internal bridges = natural cut points).
        # A flat bag of independent files (internal_edges==0) is NOT flagged.
        multi = (k > 1 and internal_edges > 0) or \
                (k == 1 and n >= 3 and cohesion < min_cohesion and len(seams) > 0)
        note = ("structural proxy: K>1 internally-coupled clusters suggest split candidates "
                "/ confirm a split is clean; flat independent files are not flagged; "
                "not proof of semantic SRP")
        if g.truncated or g.depth_bounded:
            note += "; slice bounded, module view may be partial"
        reports.append(SrpReport(mod, n, internal_edges, cohesion, k, clusters,
                                 seams, fan_in_sources, multi, note))
    reports.sort(key=lambda r: (not r.multi_responsibility, -r.components, r.module))
    return reports


def metrics(g: Graph) -> Metrics:
    """Assemble the full refactoring-support metric set for a graph slice."""
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
