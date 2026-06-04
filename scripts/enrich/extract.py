from __future__ import annotations

from tree_sitter import Parser

from enrich.langs import HAS_WALKER, WALKERS
from enrich.macro_strip import _CFAMILY_STRIP_LANGS, _strip_decl_macros
from enrich.model import Pending, _text
from enrich.query import _query_edges, _query_symbols


def _extract_file(file_id: int, content: str, lang: str, parser: Parser,
                  do_refs: bool, query=None, macro_strip: dict | None = None):
    """Parse one file into the SHARED-CONTRACT dict shape (rows + edges).

    Returns (rows, edges) where rows is a list of symbol dicts keyed by a per-file
    `_local` index, and edges reference rows by `src_local`/`dst_local`. This is
    the form apply_rules() consumes; nothing is written to the DB here.

    For c-family langs (cpp; HLSL exts route to cpp) an optional LENGTH-PRESERVING
    pre-parse strip of declaration-modifier macros runs on a LOCAL copy of `content`
    fed only to parser.parse() (see `_strip_decl_macros`). macro_strip is None => no
    transform (the committed golden harness calls this with 6 positional args, so the
    default keeps it byte-identical). The ORIGINAL `content` is unchanged for callers.
    """
    if lang in _CFAMILY_STRIP_LANGS:
        content = _strip_decl_macros(content, macro_strip)
    src = content.encode("utf-8", "replace")
    tree = parser.parse(src)

    if lang in HAS_WALKER:
        # WALKER-OWNED langs (cpp/python/...): byte-identical to pre-DSL behavior.
        pend = Pending()
        WALKERS[lang](tree.root_node, src, pend, None)

        rows: list[dict] = []
        for local_idx, r in enumerate(pend.rows):
            rows.append({
                "_local": local_idx,
                "file_id": file_id,
                "name": r.name,
                "kind": r.kind,
                "lang": lang,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "start_byte": r.start_byte,
                "end_byte": r.end_byte,
                "parent_local": r.parent_local,
                "attrs": {},
                "provenance": [],
            })

        edges: list[dict] = []
        # contains edges (parent -> child) from lexical containment.
        for local_idx, r in enumerate(pend.rows):
            if r.parent_local is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r.parent_local,
                    "dst_local": local_idx,
                    "dst_name": r.name,
                    "relation": "contains",
                })
        # extends/implements edges from base clauses. dst_local stays None: these are
        # resolved to a same-file symbol id at insert time (after any rule renames).
        for src_local, dst_name, relation in pend.inherit:
            simple = dst_name.split("<")[0].strip()
            simple = simple.split("::")[-1].split(".")[-1].strip()
            edges.append({
                "file_id": file_id,
                "src_local": src_local,
                "dst_local": None,
                "dst_name": simple or dst_name,
                "relation": relation,
            })

        # declarative dependency edges from the language's .scm query (if any).
        if query is not None:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

        # optional best-effort references (off by default). Computed on raw spans;
        # attributed to the enclosing symbol by _local index. refs needs `pend`,
        # so it stays WALKER-ONLY (the DSL branch has no pend).
        if do_refs:
            edges.extend(_reference_edges(file_id, tree, src, pend))
    else:
        # WALKER-LESS DSL (matlang/bplisp/animlang): the query OWNS the symbols.
        rows = _query_symbols(file_id, tree, src, query, lang) if query is not None else []
        edges = []
        # Synthesize `contains` from parent_local (same shape the walkers use).
        for r in rows:
            if r["parent_local"] is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r["parent_local"],
                    "dst_local": r["_local"],
                    "dst_name": r["name"],
                    "relation": "contains",
                })
        # @dep -> wire edges, attributed to the innermost enclosing @def-symbol.
        if query is not None and rows:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

    return rows, edges


def _reference_edges(file_id: int, tree, src: bytes, pend: Pending) -> list[dict]:
    """Best-effort `references` edges as contract dicts (src_local/dst_local)."""
    by_name: dict[str, int] = {}
    for local_idx, r in enumerate(pend.rows):
        if r.kind in ("function", "method", "class", "struct"):
            by_name.setdefault(r.name, local_idx)
    if not by_name:
        return []

    spans = sorted(
        ((r.start_byte, r.end_byte, i) for i, r in enumerate(pend.rows)),
        key=lambda s: (s[0], -(s[1])),
    )

    def enclosing(byte: int) -> int | None:
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best

    out: list[dict] = []
    seen: set[tuple[int, int]] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "field_identifier", "call_expression"):
            ident = node
            if node.type == "call_expression":
                fn = node.child_by_field_name("function")
                ident = fn if fn is not None else node
            if ident is not None and ident.type in ("identifier", "field_identifier"):
                nm = _text(ident, src)
                dst = by_name.get(nm)
                if dst is not None:
                    src_sym = enclosing(ident.start_byte)
                    if src_sym is not None and src_sym != dst:
                        key = (src_sym, dst)
                        if key not in seen:
                            seen.add(key)
                            out.append({
                                "file_id": file_id,
                                "src_local": src_sym,
                                "dst_local": dst,
                                "dst_name": nm,
                                "relation": "references",
                            })
        stack.extend(node.children)
    return out
