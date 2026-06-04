# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.views — derived VIEWS over the boundary graph.

Two derived views plus their layered/collapsed presentations:
  * INTERNAL coupling — the target-zone subgraph (target→target edges only),
    for split seams / SCC.
  * DEPENDENCY surface — the bipartite boundary (target symbols that reach a
    dependency) → their dependency targets, optionally rolled up by module.
Plus band assignment (the 4-layer refactoring view), module assignment (the
collapsible quotient view), and the target→dependency crossings list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from lib import rollup
from lib.graph import Edge, Graph, Node

from .zoning import DEPENDENCY, TARGET, Zoning

# Band indices for the 4-layer refactoring view (left->right reading order):
#   target-core   = target symbol with NO crossing edge into a dependency
#   target-iface  = target symbol WITH >=1 crossing edge (the call sites to wrap)
#   dep-iface     = dependency symbol referenced DIRECTLY by a target (the API surface)
#   dep-core      = dependency symbol behind the surface (only via --dep-depth 2)
TARGET_CORE, TARGET_IFACE, DEP_IFACE, DEP_CORE = 0, 1, 2, 3


# --- views -------------------------------------------------------------------
def _carry_confidence(src: Graph, dst: Graph) -> None:
    """Copy the relevant edge-confidence entries from ``src`` onto ``dst``."""
    base = getattr(src, "edge_confidence", {})
    dst.edge_confidence = {e.key(): base.get(e.key(), "direct") for e in dst.edges}


def internal_view(g: Graph) -> Graph:
    """The target-zone subgraph (target→target edges only) for split seams / SCC."""
    ids = sorted(nid for nid, n in g.nodes.items() if n.attrs.get("zone") == TARGET)
    sub = g.subgraph(ids)
    _carry_confidence(g, sub)
    return sub


def boundary_nodes(g: Graph) -> list[str]:
    """Sorted ids of target-zone nodes that have at least one out-edge into the dependency zone."""
    out: set[str] = set()
    for e in g.edges:
        src = g.nodes.get(e.src)
        dst = g.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if src.attrs.get("zone") == TARGET and dst.attrs.get("zone") == DEPENDENCY:
            out.add(e.src)
    return sorted(out)


def assign_bands(g: Graph, layers: str) -> tuple[dict[str, int], list[dict]]:
    """Assign each node to an ORDERED band (left->right) for the layered html view.

    PURE + sorted; NEVER mutates ``g`` (the same ``g`` is reused by
    ``internal_view`` / ``dependency_surface`` for the non-html formats). Returns
    ``(band_of, bands_meta)`` where ``band_of`` maps every node id to an integer band
    and ``bands_meta`` is the ordered list of ``{"band": i, "label": str}`` boxes.

    * ``flat`` (or a graph with no zones) -> every node in band 0, no boxes (``[]``)
      => the renderer emits ``const BANDS=[]`` and the box/partition layers are
      no-ops (exactly the historical flat behavior).
    * ``two`` -> band 0 = every ``target`` node, band 1 = every ``dependency`` node.
    * ``four`` -> ``[target-core | target-iface || dep-iface | dep-core]``:
        - target-core  (0): a target node with NO crossing edge into a dependency,
        - target-iface (1): a target node WITH >=1 crossing edge (``boundary_nodes``),
        - dep-iface    (2): a dependency node referenced DIRECTLY by a target, or any
          dependency node not marked ``dep_core``,
        - dep-core     (3): a dependency node marked ``dep_core`` (only via
          ``build(dep_depth=2)``) AND not part of the surface.
      The 4th (dep-core) band is KEPT in ``bands_meta`` even when empty (at
      ``--dep-depth 1``) so its framed/labelled box is always drawn — a documented,
      non-error state.
    """
    has_zone = any(n.attrs.get("zone") in (TARGET, DEPENDENCY) for n in g.nodes.values())
    if layers == "flat" or not has_zone:
        return ({nid: 0 for nid in sorted(g.nodes)}, [])
    if layers == "two":
        band_of = {nid: (TARGET_CORE if g.nodes[nid].attrs.get("zone") == TARGET else 1)
                   for nid in sorted(g.nodes)}
        return band_of, [{"band": 0, "label": "target"},
                         {"band": 1, "label": "dependency"}]
    # four
    iface_targets = set(boundary_nodes(g))           # target nodes with a crossing edge
    dep_surface = {e.dst for e in g.edges             # dep nodes referenced DIRECTLY by a target
                   if (g.nodes.get(e.src) is not None and g.nodes.get(e.dst) is not None
                       and g.nodes[e.src].attrs.get("zone") == TARGET
                       and g.nodes[e.dst].attrs.get("zone") == DEPENDENCY)}
    band_of = {}
    for nid in sorted(g.nodes):
        n = g.nodes[nid]
        if n.attrs.get("zone") == TARGET:
            band_of[nid] = TARGET_IFACE if nid in iface_targets else TARGET_CORE
        else:
            band_of[nid] = DEP_IFACE if (nid in dep_surface or not n.attrs.get("dep_core")) else DEP_CORE
    return band_of, [{"band": 0, "label": "target-core"}, {"band": 1, "label": "target-iface"},
                     {"band": 2, "label": "dep-iface"}, {"band": 3, "label": "dep-core"}]


# zone-side tints for the module super-nodes (mirror render._ZONE_COLOR; kept local so
# boundary.py has no render import). target=blue, dependency=orange.
_MODULE_ZONE_COLOR = {"target": "#4e79a7", "dependency": "#f28e2b"}


def assign_modules(g: Graph, z: "Zoning", level: str = "file", store=None,
                   band_of: dict | None = None) -> tuple[dict[str, str], list[dict]]:
    """Deterministic MODULE assignment for the collapsible quotient view.

    Returns ``(module_of, modules_meta)`` where ``module_of`` maps every node id to a
    side-prefixed module id and ``modules_meta`` is the sorted-by-id list of module
    super-node descriptors.

    * TARGET side (zone == ``target``): module = the file STEM of ``attrs['path']``
      (``level='file'``, so a ``.cpp`` + ``.h`` pair coalesces) or its parent DIR
      (``level='dir'``). A path-less target (``amb:<name>``) -> ``(external)``.
    * DEPENDENCY side: a symbol dep with a path -> ``rollup._module_of`` via
      ``rollup.roots_by_len`` (the SAME resolver ``dependency_surface`` uses, so the ids
      match ``--rollup-dep``); a by-name dep (``dep:`` / ``amb:`` with no path) ->
      ``(external)``.

    ``module_id = f'{side}:{raw}'`` — the ``side`` prefix prevents a target file-stem
    from colliding with a dependency module of the same name, and keeps the synthetic
    ``mod:`` super-node id distinct from the ``s<id>``/``dep:``/``amb:`` member keys.

    PURE + sorted (``sorted(g.nodes)``) so two runs are byte-identical. The super-node
    band is the MIN member band (a file split across target-core/target-iface collapses
    to the lower band) — a documented semantic compromise.
    """
    roots = rollup.roots_by_len(store)
    module_of: dict[str, str] = {}
    members: dict[str, int] = {}
    band_min: dict[str, int] = {}
    for nid in sorted(g.nodes):                       # sorted => stable
        n = g.nodes[nid]
        side = "target" if n.attrs.get("zone") == TARGET else "dependency"
        path = n.attrs.get("path") or ""
        if side == "target":
            if path:
                pp = PurePosixPath(path)
                raw = pp.stem if level == "file" else (pp.parent.as_posix() or "(root)")
            else:
                raw = "(external)"
        else:
            raw = rollup._module_of(_FakeNode(path), roots) if path else "(external)"
        mid = side + ":" + raw
        module_of[nid] = mid
        members[mid] = members.get(mid, 0) + 1
        b = band_of.get(nid, 0) if band_of is not None else 0
        band_min[mid] = b if mid not in band_min else min(band_min[mid], b)
    meta = [{"id": mid, "label": mid.split(":", 1)[1], "side": mid.split(":", 1)[0],
             "members": members[mid], "band": band_min[mid],
             "zone": mid.split(":", 1)[0], "color": _MODULE_ZONE_COLOR[mid.split(":", 1)[0]]}
            for mid in sorted(members)]
    return module_of, meta


def dependency_surface(g: Graph, rollup_modules: bool = False, store=None) -> Graph:
    """The bipartite boundary surface: target boundary symbols → their dependency targets.

    Keeps only crossing (target→dependency) edges + their endpoints. When
    ``rollup_modules`` is set, dependency targets are grouped by their module root
    (via :func:`rollup.module_roots` / ``_module_of``); target nodes are kept as-is
    and crossing edges re-aggregated onto the dependency groups. The dependency side
    may span MULTIPLE dependency sources.
    """
    bset = set(boundary_nodes(g))
    keep_target = sorted(bset)
    dep_targets: set[str] = set()
    crossing: list[Edge] = []
    base_conf = getattr(g, "edge_confidence", {})
    for e in g.edges:
        if e.src in bset:
            dst = g.nodes.get(e.dst)
            if dst is not None and dst.attrs.get("zone") == DEPENDENCY:
                dep_targets.add(e.dst)
                crossing.append(e)

    out = Graph()
    for nid in keep_target:
        out.add_node(g.nodes[nid])

    if not rollup_modules:
        for nid in sorted(dep_targets):
            out.add_node(g.nodes[nid])
        conf: dict[tuple[str, str, str], str] = {}
        for e in sorted(crossing, key=lambda e: (e.src, e.dst, e.relation)):
            edge = Edge(e.src, e.dst, e.relation, e.evidence, e.weight)
            if out.add_edge(edge):
                conf[edge.key()] = base_conf.get(e.key(), "direct")
            else:
                conf.setdefault(edge.key(), base_conf.get(e.key(), "direct"))
        out.edge_confidence = conf
        return out

    # Rollup dependency targets by module root. Reuse rollup.roots_by_len so the
    # ordering (and thus the chosen module) is the SAME total order rollup uses.
    roots = rollup.roots_by_len(store)
    group_of: dict[str, str] = {}
    for nid in sorted(dep_targets):
        node = g.nodes[nid]
        path = node.attrs.get("path") or (node.evidence.path if node.evidence else "") or node.label
        gid = "dep:" + rollup._module_of(_FakeNode(path), roots)
        group_of[nid] = gid
    members: dict[str, int] = {}
    for gid in group_of.values():
        members[gid] = members.get(gid, 0) + 1
    for gid in sorted(members):
        out.add_node(Node(id=gid, kind="external", label=gid,
                          attrs={"zone": DEPENDENCY, "cluster": DEPENDENCY,
                                 "members": members[gid], "unresolved": True}))
    conf = {}
    for e in sorted(crossing, key=lambda e: (e.src, e.dst, e.relation)):
        gid = group_of.get(e.dst)
        if gid is None:
            continue
        edge = Edge(e.src, gid, e.relation, None, e.weight)
        if out.add_edge(edge):
            conf[edge.key()] = base_conf.get(e.key(), "direct")
    out.edge_confidence = conf
    return out


class _FakeNode:
    """Minimal node-like shim so ``rollup._module_of`` (which reads ``.label``) works on a raw path."""

    def __init__(self, path: str):
        self.label = path or ""
        self.id = self.label


# --- crossings (for text/json) -----------------------------------------------
@dataclass(frozen=True)
class Crossing:
    """One target→dependency boundary crossing: the seam a dependency creates."""

    src: str
    dst: str
    relation: str
    confidence: str
    evidence: str


def crossings(g: Graph) -> list[Crossing]:
    """All target→dependency crossings, sorted by ``(src, dst, relation)``."""
    bset = set(boundary_nodes(g))
    conf = getattr(g, "edge_confidence", {})
    out: list[Crossing] = []
    for e in g.edges:
        if e.src not in bset:
            continue
        dst = g.nodes.get(e.dst)
        if dst is None or dst.attrs.get("zone") != DEPENDENCY:
            continue
        out.append(Crossing(
            src=e.src, dst=e.dst, relation=e.relation,
            confidence=conf.get(e.key(), "direct"),
            evidence=str(e.evidence) if e.evidence else "",
        ))
    out.sort(key=lambda c: (c.src, c.dst, c.relation))
    return out
