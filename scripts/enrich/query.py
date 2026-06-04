from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from enrich.langreg import QueryCursor
from enrich.model import _text


# --- walker-less DSL symbol extraction (UE asset graphs) ---------------------
# S-expression asset DSLs have no walker; their NODE GRAPH (the "连连看" wiring)
# comes entirely from the .scm query: `@def.<kind>` -> a SYMBOL, `@dep.<relation>`
# -> an edge from the enclosing @def-symbol (via the reused _query_edges). The
# scheme grammar parses every (...) form as a `list`, so a captured token's
# CONTAINMENT span is its nearest enclosing `list` ancestor (the token itself —
# the $id / quoted-string / head symbol — does NOT cover the node's nested wires).
def _dsl_list_ancestor(node: Node) -> Node | None:
    """Nearest enclosing `list` ancestor of a captured token (its symbol span)."""
    n = node
    while n is not None and n.type != "list":
        n = n.parent
    return n  # may be None (defensive) -> caller skips the capture


def _dsl_name(node: Node, src: bytes) -> str:
    """Symbol name from THIS captured node only (never zip a sibling capture).

    A scheme `string` node's text INCLUDES the surrounding quotes, so strip them
    for a quoted name (material "M_X" -> M_X). KEEP the leading '$' on matlang
    $ids: _simplify_dep leaves '$mul1' intact, so the (connect $mul1) edge's
    dst_name '$mul1' must equal the node symbol name '$mul1' for by-name resolution.
    """
    nm = _text(node, src)
    if node.type == "string":          # scheme `string` text includes the quotes
        nm = nm.strip('"')
    return nm or "<anon>"


def _query_symbols(file_id: int, tree, src: bytes, query, lang: str) -> list[dict]:
    """Symbols from `@def.<kind>` captures, for a walker-less DSL (the query OWNS
    symbols). Each capture lands on a token; the symbol's span is the token's
    enclosing `list` ancestor. parent = innermost STRICTLY-enclosing @def span.

    Returns the SHARED-CONTRACT row-dict shape (same keys walkers produce).
    Deterministic: captures() membership is stable but ORDER is not, so we sort
    the tuples by a TOTAL key (start_byte, end_byte, kind, name) before assigning
    `_local` indices.
    """
    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a bad query must never abort enrichment
        return []

    # Collect (start_byte, end_byte, kind, name, head) for every `@def.*` capture,
    # using the captured token's enclosing `list` ancestor as the span. `head` is
    # the first child `symbol` of that list (the node TYPE), promoted into attrs.
    raw: dict[tuple[int, int, str, str], str] = {}  # 4-key -> head (de-dupes lists
    #   matched by >1 pattern; Node is NOT part of the key — span is recoverable).
    for cap_name in sorted(caps):
        if not cap_name.startswith("def."):
            continue
        kind = cap_name[4:]
        for node in caps[cap_name]:
            anc = _dsl_list_ancestor(node)
            if anc is None:
                continue
            name = _dsl_name(node, src)
            head = ""
            for ch in anc.children:
                if ch.type == "symbol":
                    head = _text(ch, src)
                    break
            key = (anc.start_byte, anc.end_byte, kind, name)
            raw.setdefault(key, head)

    # Total-order the surviving rows; assign deterministic _local indices.
    keys = sorted(raw)            # (start_byte, end_byte, kind, name) is a total order
    spans = [(k[0], k[1]) for k in keys]

    def _parent_of(i: int) -> int | None:
        si, ei = spans[i]
        best = None  # (size, local) of the smallest STRICTLY-enclosing prior span
        for j, (sj, ej) in enumerate(spans):
            if j == i:
                continue
            if sj <= si and ei <= ej and (ej - sj) > (ei - si):
                size = ej - sj
                if best is None or size < best[0] or (size == best[0] and j < best[1]):
                    best = (size, j)
        return best[1] if best is not None else None

    # Need line numbers from the ancestor node; re-collect ancestor nodes by span.
    # (A span is unique per `list`; map span -> node from the first capture seen.)
    span_to_node: dict[tuple[int, int], Node] = {}
    for cap_name in sorted(caps):
        if not cap_name.startswith("def."):
            continue
        for node in caps[cap_name]:
            anc = _dsl_list_ancestor(node)
            if anc is None:
                continue
            span_to_node.setdefault((anc.start_byte, anc.end_byte), anc)

    rows: list[dict] = []
    for i, (sb, eb, kind, name) in enumerate(keys):
        head = raw[(sb, eb, kind, name)]
        anc = span_to_node[(sb, eb)]
        # Promote the node TYPE into attrs only when it differs from the name (the
        # matlang case: head=type e.g. 'multiply', name=$id e.g. '$mul1'). For
        # material/outputs/graph/... head==name (or is redundant) -> no attr.
        attrs = {"node_type": head} if (head and head != name and kind == "node") else {}
        rows.append({
            "_local": i,
            "file_id": file_id,
            "name": name,
            "kind": kind,
            "lang": lang,
            "start_line": anc.start_point[0] + 1,
            "end_line": anc.end_point[0] + 1,
            "start_byte": sb,
            "end_byte": eb,
            "parent_local": _parent_of(i),
            "attrs": attrs,
            "provenance": [],
        })
    return rows


# --- raw extraction -> SHARED CONTRACT dicts ---------------------------------
# --- declarative dependency-edge queries (project-customizable) --------------
# Symbols come from the walkers above; dependency EDGES can be declared per language
# in a tree-sitter query (.scm): every `@dep.<relation>` capture becomes an edge from
# the enclosing symbol to the captured name (relation = the suffix). Built-in presets
# live in scripts/queries/<lang>.scm; a project overrides one at
# <root>/.manyread/queries/<lang>.scm (full replace). A language with no .scm keeps
# walker-only edges (e.g. C++), so this is purely additive + backward compatible.
_QUERY_DIR = Path(__file__).resolve().parent.parent / "queries"


def _load_query_specs(root) -> dict[str, str]:
    """lang -> .scm text: built-in presets, then project overrides (which win)."""
    specs: dict[str, str] = {}
    if _QUERY_DIR.is_dir():
        for p in sorted(_QUERY_DIR.glob("*.scm")):
            try:
                specs[p.stem] = p.read_text(encoding="utf-8")
            except OSError:
                pass
    if root is not None:
        odir = Path(root) / ".manyread" / "queries"
        if odir.is_dir():
            for p in sorted(odir.glob("*.scm")):
                try:
                    specs[p.stem] = p.read_text(encoding="utf-8")
                except OSError:
                    pass
    return specs


def _simplify_dep(name: str) -> str:
    """Reduce a captured type/name to a bare identifier for by-name resolution
    (mirrors the inherit simplification): union -> first, strip generics, last segment."""
    s = name.split("|")[0].strip()
    s = s.split("[")[0].split("<")[0].strip()
    return s.split("::")[-1].split(".")[-1].strip()


def _query_edges(file_id: int, tree, src: bytes, query, rows: list[dict]) -> list[dict]:
    """Edges from `@dep.<relation>` captures, each attributed to the enclosing symbol
    (smallest row span containing the capture). Sorted + deduped => deterministic."""
    if not rows:
        return []
    spans = sorted(((r["start_byte"], r["end_byte"], r["_local"]) for r in rows),
                   key=lambda s: (s[0], -s[1]))

    def enclosing(byte: int):
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best

    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a bad query must never abort enrichment
        return []
    out: list[dict] = []
    seen: set[tuple] = set()
    for cap_name in sorted(caps):
        if not cap_name.startswith("dep."):
            continue
        relation = cap_name[4:]
        for node in caps[cap_name]:
            src_local = enclosing(node.start_byte)
            if src_local is None:
                continue
            dst = _simplify_dep(_text(node, src))
            if not dst:
                continue
            key = (src_local, relation, dst)
            if key in seen:
                continue
            seen.add(key)
            out.append({"file_id": file_id, "src_local": src_local,
                        "dst_local": None, "dst_name": dst, "relation": relation})
    out.sort(key=lambda e: (e["src_local"], e["relation"], e["dst_name"]))
    return out
