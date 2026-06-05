from __future__ import annotations

from tree_sitter import Parser

from enrich.langs import HAS_WALKER, WALKERS
from enrich.macro_strip import _CFAMILY_STRIP_LANGS, _strip_decl_macros
from enrich.model import Pending, _text
from enrich.query import _query_edges, _query_symbols


#### 解析单个文件为共享契约的 rows + edges 字典结构 [@380kkm 2026-06-05] ####
def _extract_file(file_id: int, content: str, lang: str, parser: Parser,
                  do_refs: bool, query=None, macro_strip: dict | None = None):
    if lang in _CFAMILY_STRIP_LANGS:
        content = _strip_decl_macros(content, macro_strip)
    src = content.encode("utf-8", "replace")
    tree = parser.parse(src)

    if lang in HAS_WALKER:
        # 由遍历器拥有的语言（cpp/python/...）
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
        # 由词法包含关系生成的 contains 边（父 -> 子）
        for local_idx, r in enumerate(pend.rows):
            if r.parent_local is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r.parent_local,
                    "dst_local": local_idx,
                    "dst_name": r.name,
                    "relation": "contains",
                })
        # 由基类子句生成的 extends/implements 边（dst_local 留空，入库时按名解析）
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

        # 由该语言 .scm 查询生成的声明式依赖边（若有）
        if query is not None:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

        # 可选的尽力而为 references 边（默认关闭）
        if do_refs:
            edges.extend(_reference_edges(file_id, tree, src, pend))
    else:
        # 无遍历器的语言（如扩展提供的 DSL）：符号完全来自 .scm 查询
        rows = _query_symbols(file_id, tree, src, query, lang) if query is not None else []
        edges = []
        # 从 parent_local 合成 contains 边（与遍历器产出的形态一致）
        for r in rows:
            if r["parent_local"] is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r["parent_local"],
                    "dst_local": r["_local"],
                    "dst_name": r["name"],
                    "relation": "contains",
                })
        # @dep -> wire 边，归属到最内层外围的 @def 符号
        if query is not None and rows:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

    return rows, edges
#### /解析单个文件 ####


#### 尽力而为地生成 references 边（契约字典形态） [@380kkm 2026-06-05] ####
def _reference_edges(file_id: int, tree, src: bytes, pend: Pending) -> list[dict]:
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

    #### 返回覆盖给定字节位置的最内层符号下标 [@380kkm 2026-06-05] ####
    def enclosing(byte: int) -> int | None:
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best
    #### /返回外围符号 ####

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
#### /生成 references 边 ####
