# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary — SYMBOL-LEVEL plugin↔engine dependency boundary.

File-level deps are useless for C++ refactoring; this module works at the symbol
level. It classifies every symbol into a ZONE (``plugin`` = internal, the code
you own / ``engine`` = external, the code you depend on), resolves each symbol
edge (``extends`` / ``implements`` / ``uses_type``) to a concrete target WITH a
soundness CONFIDENCE (never silently picking one of many by-name candidates),
and expands a depth-1 *engine-sink* slice: plugin symbols plus their one-layer
engine interface, regardless of how large the engine index is.

Two derived views:
  * INTERNAL coupling — the plugin-zone subgraph (plugin→plugin edges only),
    for split seams / SCC.
  * ENGINE surface — the bipartite boundary (plugin symbols that reach engine) →
    their engine targets, optionally rolled up by engine module.

DETERMINISM is mandatory: every query is total-ordered, every set iterated
sorted, counts are integers (no floats here), and ambiguous resolution always
yields an ``ext:`` node — so the same index + same roots ⇒ byte-identical output.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from lib import deps, rollup
from lib.graph import Budget, Edge, Evidence, Graph, Node

PLUGIN = "plugin"
ENGINE = "engine"

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
    """How to split symbols into the plugin (internal) and engine (external) zones.

    ``plugin_root`` is the normalized, trailing-slash-free directory prefix that
    defines INTERNAL (``""`` means the whole repo is plugin). ``engine_roots`` are
    LABEL/grouping hints for the engine side only — they NEVER change the internal
    bit (a symbol is engine iff it is not under ``plugin_root``).
    """

    plugin_root: str
    engine_roots: tuple[str, ...] = ()  # normalized, sorted longest-first


def norm_root(p: str) -> str:
    """Normalize a root path: slash-normalize, strip leading ``./``, strip trailing ``/``."""
    return _NORM(p or "").rstrip("/")


def detect_plugin_root(store) -> str:
    """Autodetect the plugin root: the shortest module root (``*.uplugin`` / ``*.Build.cs`` / …).

    Picks ``min`` by ``(len, str)`` for determinism; ``""`` (whole repo) if no
    module markers are present. NOTE: ``""`` is AMBIGUOUS — it is also the legitimate
    repo-root marker. Callers that must NOT silently classify the whole repo (incl.
    the engine) as plugin should use :func:`has_module_markers` to tell the two
    cases apart, or supply an explicit ``plugin_root``.
    """
    roots = rollup.module_roots(store)
    if not roots:
        return ""
    return min(roots, key=lambda r: (len(r), r))


def has_module_markers(store) -> bool:
    """True iff the index contains ANY module-marker file (``*.uplugin`` / ``*.Build.cs`` / …).

    When this is False, :func:`detect_plugin_root` cannot tell the plugin from the
    engine (the L1 indexer only stores configured source extensions, so ``.uplugin``
    markers are typically absent) — so autodetect must NOT be trusted and an explicit
    ``--plugin-root`` is required. This avoids the SILENT, UNSOUND classification of
    the entire repo (engine included) as plugin.
    """
    return bool(rollup.module_roots(store))


def make_zoning(store, plugin_root: str | None, engine_roots: list[str] | None) -> Zoning:
    """Build a :class:`Zoning`, autodetecting ``plugin_root`` when not given.

    ``engine_roots`` are normalized, de-duplicated, and sorted LONGEST-FIRST so
    that the most specific engine module wins in :func:`engine_label`.
    """
    pr = norm_root(plugin_root) if plugin_root is not None else detect_plugin_root(store)
    ers = sorted({norm_root(e) for e in (engine_roots or []) if norm_root(e)},
                 key=lambda r: (-len(r), r))
    return Zoning(plugin_root=pr, engine_roots=tuple(ers))


def zone_of_path(path: str | None, z: Zoning) -> str:
    """Classify a defining file path into ``plugin`` or ``engine`` (sound containment).

    A symbol is PLUGIN iff its normalized path equals ``plugin_root`` or starts
    with ``plugin_root + '/'``. ``plugin_root == ""`` ⇒ everything is plugin.
    A missing path (no file) is conservatively ENGINE.
    """
    if path is None:
        return ENGINE
    p = _NORM(path)
    pr = z.plugin_root
    if pr == "":
        return PLUGIN
    if p == pr or p.startswith(pr + "/"):
        return PLUGIN
    return ENGINE


def engine_label(path: str, z: Zoning) -> str:
    """A human label for an engine-side symbol's file: ``<engine_root>::<basename>``.

    Uses the longest matching ``engine_root`` prefix (roots are longest-first);
    falls back to the bare basename when no engine root matches.
    """
    p = _NORM(path or "")
    base = p.rsplit("/", 1)[-1]
    for er in z.engine_roots:  # already longest-first
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
    """Build an engine/unresolved external :class:`Node` (``ext:<name>``)."""
    attrs: dict = {"zone": ENGINE, "cluster": ENGINE, "unresolved": True}
    if ambiguity:
        attrs["ambiguity"] = ambiguity
    return Node(id=f"ext:{name}", kind="external", label=name, attrs=attrs)


def ambiguous_internal_node(name: str, ambiguity: int) -> Node:
    """A plugin-zone type known to be internal but not pinned to one symbol
    (e.g. header definition + forward declaration). Kept INTERNAL, off the engine
    boundary, but marked ambiguous (never silently resolved to one symbol)."""
    return Node(id=f"amb:{name}", kind="ambiguous", label=name,
                attrs={"zone": PLUGIN, "cluster": PLUGIN, "ambiguity": ambiguity})


# --- resolution with confidence ----------------------------------------------
@dataclass(frozen=True)
class Resolved:
    """The outcome of resolving one edge: target node id + soundness confidence."""

    target_id: str           # 's<id>' or 'ext:<name>'
    confidence: str          # 'direct' | 'unique' | 'ambiguous' | 'unresolved'
    ambiguity: int           # 0, 1, or N
    node: Node


def resolve_target(store, row, z: Zoning, alias: str | None = None) -> Resolved:
    """Resolve an edges row to a concrete target, recording confidence.

    * ``dst_symbol_id`` set → that symbol, ``direct``.
    * else resolve ``dst_name`` globally by exact name:
        - 0 candidates → ``ext:<name>``, ``unresolved`` (engine / absent).
        - exactly 1    → that symbol, ``unique``.
        - N > 1        → ``ext:<name>`` with ``ambiguity=N``, ``ambiguous``
                         (NEVER silently picks one — C++ by-name is unsound).
    """
    dst_sid = row["dst_symbol_id"]
    if dst_sid is not None:
        return Resolved(f"s{dst_sid}", "direct", 0, symbol_node(store, int(dst_sid), z, alias))
    name = row["dst_name"] or ""
    cands = sorted(deps.resolve_edge_targets(store, name),
                   key=lambda r: (r["path"], r["id"]))
    if not cands:
        return Resolved(f"ext:{name}", "unresolved", 0, external_node(name))
    if len(cands) == 1:
        sid = int(cands[0]["id"])
        return Resolved(f"s{sid}", "unique", 1, symbol_node(store, sid, z, alias))
    # N>1: ambiguous — never pick one. But if EVERY candidate is plugin-zone (e.g. a
    # header definition + a forward declaration of the plugin's own type), it is
    # definitely INTERNAL, just not pinned to one symbol → keep it in the plugin zone
    # (off the engine boundary). Only when a candidate is engine/mixed is it engine.
    n = len(cands)
    if {zone_of_path(c["path"], z) for c in cands} == {PLUGIN}:
        return Resolved(f"amb:{name}", "ambiguous", n, ambiguous_internal_node(name, n))
    return Resolved(f"ext:{name}", "ambiguous", n, external_node(name, n))


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


# --- the depth-1 engine-sink symbol graph ------------------------------------
def _plugin_seed_rows(store, z: Zoning) -> list[sqlite3.Row]:
    """Every plugin-zone symbol, ordered by ``(path, id)``."""
    rows = store.conn.execute(
        "SELECT s.id AS id, f.path AS path FROM symbols s "
        "JOIN files f ON f.id = s.file_id ORDER BY f.path, s.id"
    ).fetchall()
    return [r for r in rows if zone_of_path(r["path"], z) == PLUGIN]


def build(store, z: Zoning, budget: Budget, alias: str | None = None) -> Graph:
    """Whole plugin + its depth-1 engine interface, by DIRECT construction.

    Every plugin-zone symbol is included (the plugin is finite and fully wanted —
    NOT bounded-BFS'd from seeds, which would let the budget die on the seeds
    themselves). Each plugin symbol's boundary edges (``extends``/``implements``/
    ``uses_type``) are resolved; targets are added — engine/``ext:`` targets as
    depth-1 SINKS (their own edges are never followed, since only plugin symbols
    are iterated). ``budget.max_nodes`` is a safety cap with honest truncation.
    Per-edge confidence on ``g.edge_confidence``; UE ``*_API`` macros are skipped.
    """
    g = Graph()
    cap = budget.max_nodes
    confidence: dict[tuple[str, str, str], str] = {}
    truncated = False
    elided = 0

    plugin_ids: list[str] = []
    for r in _plugin_seed_rows(store, z):
        if len(g.nodes) >= cap:
            truncated = True
            elided += 1
            continue
        node = symbol_node(store, int(r["id"]), z, alias)
        g.add_node(node)
        plugin_ids.append(node.id)

    for nid in plugin_ids:
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
            edge = Edge(nid, res.node.id, er["relation"], Evidence(src_path, None), 1)
            g.add_edge(edge)
            confidence[edge.key()] = res.confidence

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
    """The plugin-zone subgraph (plugin→plugin edges only) for split seams / SCC."""
    ids = sorted(nid for nid, n in g.nodes.items() if n.attrs.get("zone") == PLUGIN)
    sub = g.subgraph(ids)
    _carry_confidence(g, sub)
    return sub


def boundary_nodes(g: Graph) -> list[str]:
    """Sorted ids of plugin-zone nodes that have at least one out-edge into the engine zone."""
    out: set[str] = set()
    for e in g.edges:
        src = g.nodes.get(e.src)
        dst = g.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if src.attrs.get("zone") == PLUGIN and dst.attrs.get("zone") == ENGINE:
            out.add(e.src)
    return sorted(out)


def engine_surface(g: Graph, rollup_modules: bool = False, store=None) -> Graph:
    """The bipartite boundary surface: plugin boundary symbols → their engine targets.

    Keeps only crossing (plugin→engine) edges + their endpoints. When
    ``rollup_modules`` is set, engine targets are grouped by their module root
    (via :func:`rollup.module_roots` / ``_module_of``); plugin nodes are kept as-is
    and crossing edges re-aggregated onto the engine groups.
    """
    bset = set(boundary_nodes(g))
    keep_plugin = sorted(bset)
    engine_targets: set[str] = set()
    crossing: list[Edge] = []
    base_conf = getattr(g, "edge_confidence", {})
    for e in g.edges:
        if e.src in bset:
            dst = g.nodes.get(e.dst)
            if dst is not None and dst.attrs.get("zone") == ENGINE:
                engine_targets.add(e.dst)
                crossing.append(e)

    out = Graph()
    for nid in keep_plugin:
        out.add_node(g.nodes[nid])

    if not rollup_modules:
        for nid in sorted(engine_targets):
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

    # Rollup engine targets by module root. Reuse rollup.roots_by_len so the
    # ordering (and thus the chosen module) is the SAME total order rollup uses.
    roots = rollup.roots_by_len(store)
    group_of: dict[str, str] = {}
    for nid in sorted(engine_targets):
        node = g.nodes[nid]
        path = node.attrs.get("path") or (node.evidence.path if node.evidence else "") or node.label
        gid = "engine:" + rollup._module_of(_FakeNode(path), roots)
        group_of[nid] = gid
    members: dict[str, int] = {}
    for gid in group_of.values():
        members[gid] = members.get(gid, 0) + 1
    for gid in sorted(members):
        out.add_node(Node(id=gid, kind="external", label=gid,
                          attrs={"zone": ENGINE, "cluster": ENGINE,
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
    """One plugin→engine boundary crossing: the seam an engine dependency creates."""

    src: str
    dst: str
    relation: str
    confidence: str
    evidence: str


def crossings(g: Graph) -> list[Crossing]:
    """All plugin→engine crossings, sorted by ``(src, dst, relation)``."""
    bset = set(boundary_nodes(g))
    conf = getattr(g, "edge_confidence", {})
    out: list[Crossing] = []
    for e in g.edges:
        if e.src not in bset:
            continue
        dst = g.nodes.get(e.dst)
        if dst is None or dst.attrs.get("zone") != ENGINE:
            continue
        out.append(Crossing(
            src=e.src, dst=e.dst, relation=e.relation,
            confidence=conf.get(e.key(), "direct"),
            evidence=str(e.evidence) if e.evidence else "",
        ))
    out.sort(key=lambda c: (c.src, c.dst, c.relation))
    return out
