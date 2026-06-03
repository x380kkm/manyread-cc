# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary — SYMBOL-LEVEL target↔dependency boundary.

File-level deps are useless for C++ refactoring; this module works at the symbol
level. It classifies every symbol into a ZONE (``target`` = the code you are
analyzing / ``dependency`` = what it depends on — and the dependency side may
hold MANY distinct dependency sources), resolves each symbol edge (``extends`` /
``implements`` / ``uses_type``) to a concrete target WITH a soundness CONFIDENCE
(never silently picking one of many by-name candidates), and expands a depth-1
*dependency-sink* slice: target symbols plus their one-layer dependency interface,
regardless of how large the dependency index is.

Two derived views:
  * INTERNAL coupling — the target-zone subgraph (target→target edges only),
    for split seams / SCC.
  * DEPENDENCY surface — the bipartite boundary (target symbols that reach a
    dependency) → their dependency targets, optionally rolled up by module.

DETERMINISM is mandatory: every query is total-ordered, every set iterated
sorted, counts are integers (no floats here), and ambiguous resolution always
yields a ``dep:`` node — so the same index + same roots ⇒ byte-identical output.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from lib import deps, rollup
from lib.graph import Budget, Edge, Evidence, Graph, Node

TARGET = "target"
DEPENDENCY = "dependency"

# Band indices for the 4-layer refactoring view (left->right reading order):
#   target-core   = target symbol with NO crossing edge into a dependency
#   target-iface  = target symbol WITH >=1 crossing edge (the call sites to wrap)
#   dep-iface     = dependency symbol referenced DIRECTLY by a target (the API surface)
#   dep-core      = dependency symbol behind the surface (only via --dep-depth 2)
TARGET_CORE, TARGET_IFACE, DEP_IFACE, DEP_CORE = 0, 1, 2, 3

# UE export macros (e.g. MATBP2FP_API) parse as a leading type_identifier on a
# declaration; they are NOT real types — skip them as boundary noise.
_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*_API$")

# Symbol relations that form the boundary. Sorted; NOT ``contains`` (structural,
# not a dependency); ``calls`` is descoped. ``references`` deliberately omitted.
REL: tuple[str, ...] = ("extends", "implements", "uses_type")

_NORM = deps.PathIndex._norm


# --- zoning ------------------------------------------------------------------
@dataclass(frozen=True)
class Zoning:
    """How to split symbols into the target (analyzed) and dependency zones.

    ``target_root`` is the normalized, trailing-slash-free directory prefix that
    defines the TARGET (``""`` means the whole repo is the target). ``dep_roots``
    are LABEL/grouping hints for the dependency side only — they NEVER change the
    target bit (a symbol is a dependency iff it is not under ``target_root``). The
    dependency side may aggregate MULTIPLE distinct dependency sources, one per hint.
    """

    target_root: str
    dep_roots: tuple[str, ...] = ()  # normalized, sorted longest-first


def norm_root(p: str) -> str:
    """Normalize a root path: slash-normalize, strip leading ``./``, strip trailing ``/``."""
    return _NORM(p or "").rstrip("/")


def detect_target_root(store) -> str:
    """Autodetect the target root: the shortest module root (``*.uplugin`` / ``*.Build.cs`` / …).

    Picks ``min`` by ``(len, str)`` for determinism; ``""`` (whole repo) if no
    module markers are present. NOTE: ``""`` is AMBIGUOUS — it is also the legitimate
    repo-root marker. Callers that must NOT silently classify the whole repo (incl.
    the dependencies) as target should use :func:`has_module_markers` to tell the two
    cases apart, or supply an explicit ``target_root``.
    """
    roots = rollup.module_roots(store)
    if not roots:
        return ""
    return min(roots, key=lambda r: (len(r), r))


def has_module_markers(store) -> bool:
    """True iff the index contains ANY module-marker file (``*.uplugin`` / ``*.Build.cs`` / …).

    When this is False, :func:`detect_target_root` cannot tell the target from its
    dependencies (the L1 indexer only stores configured source extensions, so
    ``.uplugin`` markers are typically absent) — so autodetect must NOT be trusted
    and an explicit ``--target-root`` is required. This avoids the SILENT, UNSOUND
    classification of the entire repo (dependencies included) as the target.
    """
    return bool(rollup.module_roots(store))


def make_zoning(store, target_root: str | None, dep_roots: list[str] | None) -> Zoning:
    """Build a :class:`Zoning`, autodetecting ``target_root`` when not given.

    ``dep_roots`` are normalized, de-duplicated, and sorted LONGEST-FIRST so that
    the most specific dependency module wins in :func:`dependency_label`. Multiple
    distinct dependency sources may be supplied.
    """
    pr = norm_root(target_root) if target_root is not None else detect_target_root(store)
    ers = sorted({norm_root(e) for e in (dep_roots or []) if norm_root(e)},
                 key=lambda r: (-len(r), r))
    return Zoning(target_root=pr, dep_roots=tuple(ers))


def zone_of_path(path: str | None, z: Zoning) -> str:
    """Classify a defining file path into ``target`` or ``dependency`` (sound containment).

    A symbol is TARGET iff its normalized path equals ``target_root`` or starts
    with ``target_root + '/'``. ``target_root == ""`` ⇒ everything is target.
    A missing path (no file) is conservatively a DEPENDENCY.
    """
    if path is None:
        return DEPENDENCY
    p = _NORM(path)
    pr = z.target_root
    if pr == "":
        return TARGET
    if p == pr or p.startswith(pr + "/"):
        return TARGET
    return DEPENDENCY


def dependency_label(path: str, z: Zoning) -> str:
    """A human label for a dependency-side symbol's file: ``<dep_root>::<basename>``.

    Uses the longest matching ``dep_root`` prefix (roots are longest-first);
    falls back to the bare basename when no dependency root matches.
    """
    p = _NORM(path or "")
    base = p.rsplit("/", 1)[-1]
    for er in z.dep_roots:  # already longest-first
        if er and (p == er or p.startswith(er + "/")):
            return f"{er}::{base}"
    return base


# --- node + name construction ------------------------------------------------
def qualified_name(store, symbol_id: int) -> str:
    """The ``Outer::Inner::name`` qualified name by walking ``parent_id`` (cycle-guarded)."""
    cache = getattr(store, "_ms_qname_cache", None)
    if cache is None:
        cache = {}
        store._ms_qname_cache = cache
    if symbol_id in cache:
        return cache[symbol_id]
    parts: list[str] = []
    seen: set[int] = set()
    sid: int | None = symbol_id
    while sid is not None and sid not in seen:
        seen.add(sid)
        row = store.conn.execute(
            "SELECT name, parent_id FROM symbols WHERE id = ?", (sid,)
        ).fetchone()
        if row is None:
            break
        parts.append(row["name"] or str(sid))
        sid = row["parent_id"]
    qn = "::".join(reversed(parts)) if parts else str(symbol_id)
    cache[symbol_id] = qn
    return qn


def symbol_node(store, symbol_id: int, z: Zoning, alias: str | None = None) -> Node:
    """Build the graph :class:`Node` for an indexed symbol (``s<id>``)."""
    row = store.symbol(symbol_id)
    if row is None:
        # Defensive: an edge pointing at a vanished symbol. Treat as external.
        return external_node(f"#{symbol_id}")
    path = row["path"]
    zone = zone_of_path(path, z)
    return Node(
        id=f"s{symbol_id}",
        kind=row["kind"] or "symbol",
        label=qualified_name(store, symbol_id),
        store=alias,
        evidence=Evidence(_NORM(path) if path else None, row["start_line"]),
        attrs={"path": _NORM(path) if path else "", "zone": zone, "cluster": zone},
    )


def external_node(name: str, ambiguity: int = 0) -> Node:
    """Build a dependency/unresolved external :class:`Node` (``dep:<name>``)."""
    attrs: dict = {"zone": DEPENDENCY, "cluster": DEPENDENCY, "unresolved": True}
    if ambiguity:
        attrs["ambiguity"] = ambiguity
    return Node(id=f"dep:{name}", kind="external", label=name, attrs=attrs)


def ambiguous_internal_node(name: str, ambiguity: int) -> Node:
    """A target-zone type known to be internal but not pinned to one symbol
    (e.g. header definition + forward declaration). Kept in the target zone, off the
    dependency boundary, but marked ambiguous (never silently resolved to one symbol)."""
    return Node(id=f"amb:{name}", kind="ambiguous", label=name,
                attrs={"zone": TARGET, "cluster": TARGET, "ambiguity": ambiguity})


# --- resolution with confidence ----------------------------------------------
@dataclass(frozen=True)
class Resolved:
    """The outcome of resolving one edge: target node id + soundness confidence."""

    target_id: str           # 's<id>' or 'dep:<name>'
    confidence: str          # 'direct' | 'unique' | 'ambiguous' | 'unresolved'
    ambiguity: int           # 0, 1, or N
    node: Node


def resolve_target(store, row, z: Zoning, alias: str | None = None) -> Resolved:
    """Resolve an edges row to a concrete target, recording confidence.

    * ``dst_symbol_id`` set → that symbol, ``direct``.
    * else resolve ``dst_name`` globally by exact name:
        - 0 candidates → ``dep:<name>``, ``unresolved`` (dependency / absent).
        - exactly 1    → that symbol, ``unique``.
        - N > 1        → ``dep:<name>`` with ``ambiguity=N``, ``ambiguous``
                         (NEVER silently picks one — C++ by-name is unsound).
    """
    dst_sid = row["dst_symbol_id"]
    if dst_sid is not None:
        return Resolved(f"s{dst_sid}", "direct", 0, symbol_node(store, int(dst_sid), z, alias))
    name = row["dst_name"] or ""
    cands = sorted(deps.resolve_edge_targets(store, name),
                   key=lambda r: (r["path"], r["id"]))
    if not cands:
        return Resolved(f"dep:{name}", "unresolved", 0, external_node(name))
    if len(cands) == 1:
        sid = int(cands[0]["id"])
        return Resolved(f"s{sid}", "unique", 1, symbol_node(store, sid, z, alias))
    # N>1: ambiguous — never pick one. But if EVERY candidate is target-zone (e.g. a
    # header definition + a forward declaration of the target's own type), it is
    # definitely INTERNAL, just not pinned to one symbol → keep it in the target zone
    # (off the dependency boundary). Only when a candidate is a dependency/mixed is
    # it a dependency.
    n = len(cands)
    if {zone_of_path(c["path"], z) for c in cands} == {TARGET}:
        return Resolved(f"amb:{name}", "ambiguous", n, ambiguous_internal_node(name, n))
    return Resolved(f"dep:{name}", "ambiguous", n, external_node(name, n))


# --- store edge access (no by-src accessor on Store; query conn read-only) ---
def out_edges(store, symbol_id: int) -> list[sqlite3.Row]:
    """All boundary out-edges of a symbol, total-ordered for determinism."""
    placeholders = ",".join("?" * len(REL))
    return store.conn.execute(
        "SELECT id, src_symbol_id, dst_symbol_id, dst_name, relation FROM edges "
        f"WHERE src_symbol_id = ? AND relation IN ({placeholders}) "
        "ORDER BY relation, dst_name, dst_symbol_id, id",
        (symbol_id, *REL),
    ).fetchall()


# --- the depth-1 dependency-sink symbol graph --------------------------------
def _target_seed_rows(store, z: Zoning) -> list[sqlite3.Row]:
    """Every target-zone symbol, ordered by ``(path, id)``."""
    rows = store.conn.execute(
        "SELECT s.id AS id, f.path AS path FROM symbols s "
        "JOIN files f ON f.id = s.file_id ORDER BY f.path, s.id"
    ).fetchall()
    return [r for r in rows if zone_of_path(r["path"], z) == TARGET]


def build(store, z: Zoning, budget: Budget, alias: str | None = None,
          dep_depth: int = 1) -> Graph:
    """Whole target + its depth-1 dependency interface, by DIRECT construction.

    Every target-zone symbol is included (the target is finite and fully wanted —
    NOT bounded-BFS'd from seeds, which would let the budget die on the seeds
    themselves). Each target symbol's boundary edges (``extends``/``implements``/
    ``uses_type``) are resolved; targets are added — dependency/``dep:`` targets as
    depth-1 SINKS (their own edges are never followed, since only target symbols
    are iterated). ``budget.max_nodes`` is a safety cap with honest truncation.
    Per-edge confidence on ``g.edge_confidence``; UE ``*_API`` macros are skipped.

    ``dep_depth`` controls how many bounded out-edge layers are expanded past the
    target. ``dep_depth <= 1`` (the default) is the historical behavior: ONE layer
    (the dependency *surface*) is expanded and every dependency node is a SINK —
    byte-for-byte identical build() output to before. ``dep_depth >= 2`` runs ONE
    extra bounded layer behind the surface; dependency SYMBOL nodes FIRST added in
    that pass are marked ``dep_core`` (this id-tracking is exact: a dep symbol
    referenced by BOTH a target and another dep was already added at depth-1, so the
    depth-2 pass never re-adds it and it stays a surface/``dep-iface`` node). The
    shared ``truncated``/``elided`` counters compose across both passes, so a
    depth-2 overflow is reported honestly, not silently dropped.
    """
    g = Graph()
    cap = budget.max_nodes
    confidence: dict[tuple[str, str, str], str] = {}
    truncated = False
    elided = 0

    target_ids: list[str] = []
    for r in _target_seed_rows(store, z):
        if len(g.nodes) >= cap:
            truncated = True
            elided += 1
            continue
        node = symbol_node(store, int(r["id"]), z, alias)
        g.add_node(node)
        target_ids.append(node.id)

    def _expand(src_ids: list[str]) -> list[str]:
        """One bounded out-edge layer from a SORTED list of source ids.

        Resolves each source's boundary edges (same _MACRO_RE skip + resolve_target +
        confidence as the historical loop), de-dups new nodes via ``id not in g.nodes``,
        shares the nonlocal ``truncated``/``elided`` cap accounting, and RETURNS the
        sorted set of NEWLY-ADDED dependency SYMBOL ids only (``s``-prefixed, zone ==
        DEPENDENCY) — the deterministic seed for the next layer.
        """
        nonlocal truncated, elided
        new_dep_syms: set[str] = set()
        for nid in src_ids:
            sid = int(nid[1:])
            src_path = g.nodes[nid].attrs.get("path")
            for er in out_edges(store, sid):
                dn = er["dst_name"]
                if dn and _MACRO_RE.match(dn):
                    continue  # drop UE export-macro pseudo-types
                res = resolve_target(store, er, z, alias)
                if res.node.id not in g.nodes:
                    if len(g.nodes) >= cap:
                        truncated = True
                        elided += 1
                        continue
                    g.add_node(res.node)
                    if (res.node.attrs.get("zone") == DEPENDENCY
                            and res.node.id.startswith("s")):
                        new_dep_syms.add(res.node.id)
                edge = Edge(nid, res.node.id, er["relation"], Evidence(src_path, None), 1)
                g.add_edge(edge)
                confidence[edge.key()] = res.confidence
        return sorted(new_dep_syms)

    surface_dep = _expand(target_ids)            # depth-1 (the historical behavior)
    if dep_depth >= 2:                            # one extra bounded layer behind the surface
        core_ids = _expand(surface_dep)
        for nid in core_ids:                      # id-tracking marking (first-added-in-depth-2)
            g.nodes[nid].attrs["dep_core"] = 1
            g.nodes[nid].attrs["dep_depth"] = 2

    g.edge_confidence = {e.key(): confidence.get(e.key(), "direct") for e in g.edges}
    if truncated:
        g.truncated = True
        g.elided = elided
    return g


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
