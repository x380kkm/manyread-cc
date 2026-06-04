# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.build — the depth-1 dependency-sink BUILD pipeline.

Expands a depth-1 *dependency-sink* slice: every target symbol plus its one-layer
dependency interface (resolved with confidence), regardless of how large the
dependency index is, by DIRECT graph construction.
"""
from __future__ import annotations

import re
import sqlite3

from lib.graph import Budget, Edge, Evidence, Graph

from .nodes import symbol_node
from .resolve import out_edges, resolve_target
from .zoning import DEPENDENCY, TARGET, Zoning, zone_of_path

# UE export macros (e.g. MATBP2FP_API) parse as a leading type_identifier on a
# declaration; they are NOT real types — skip them as boundary noise.
_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*_API$")


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
