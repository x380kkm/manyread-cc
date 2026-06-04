# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.nodes — graph NODE + qualified-name construction.

Builds the graph :class:`~lib.graph.Node` for an indexed symbol, an unresolved /
dependency external node, and the target-internal-but-ambiguous node — plus the
``Outer::Inner::name`` qualified-name walk.
"""
from __future__ import annotations

from lib.graph import Evidence, Node

from .zoning import DEPENDENCY, TARGET, Zoning, _NORM, zone_of_path


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
