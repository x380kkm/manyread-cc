# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.resolve — edge RESOLUTION with soundness confidence.

Resolves each symbol edge (``extends`` / ``implements`` / ``uses_type``) to a
concrete target WITH a soundness CONFIDENCE (never silently picking one of many
by-name candidates), plus the read-only store edge-access partner (:func:`out_edges`)
and its query alphabet (:data:`REL`).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from lib import deps
from lib.graph import Node

from .nodes import ambiguous_internal_node, external_node, symbol_node
from .zoning import DEPENDENCY, TARGET, Zoning, zone_of_path

# Symbol relations that form the boundary. Sorted; NOT ``contains`` (structural,
# not a dependency); ``calls`` is descoped. ``references`` deliberately omitted.
REL: tuple[str, ...] = ("extends", "implements", "uses_type")


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
