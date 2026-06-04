from __future__ import annotations

from tree_sitter import Parser

from enrich.langs import HAS_WALKER, WALKERS
from enrich.macro_strip import _CFAMILY_STRIP_LANGS, _strip_decl_macros
from enrich.model import Pending, _text
from enrich.query import _query_edges, _query_symbols


#### 解析单个文件为共享契约的 rows + edges 字典结构 [@380kkm 2026-06-05] ####
def _extract_file(file_id: int, content: str, lang: str, parser: Parser,
                  do_refs: bool, query=None, macro_strip: dict | None = None):
    """把单个文件解析成共享契约的字典结构（符号 rows + 边 edges）。

    返回 (rows, edges)：rows 是以单文件内 `_local` 下标为键的符号字典列表，
    edges 通过 `src_local`/`dst_local` 引用 rows。这正是 apply_rules() 消费的形态；
    此处不向数据库写入任何内容。

    对 c 系语言（cpp；HLSL 扩展名路由到 cpp），可选地对 `content` 的本地副本做一次
    保长度的预解析声明修饰宏剥离，该副本仅喂给 parser.parse()（见 `_strip_decl_macros`）。
    macro_strip 为 None 时不做任何变换（已提交的 golden 测试以 6 个位置参数调用本函数，
    默认值因此保持字节一致）。对调用方而言原始 `content` 保持不变。

    参数:
        file_id: 文件在库中的 id。
        content: 文件源码文本。
        lang: 语言标识。
        parser: 已加载对应语言的 tree-sitter 解析器。
        do_refs: 是否额外计算尽力而为的 references 边。
        query: 该语言的 .scm 查询对象，可为 None。
        macro_strip: c 系宏剥离配置，可为 None。

    返回:
        (rows, edges) 二元组。
    """
    if lang in _CFAMILY_STRIP_LANGS:
        content = _strip_decl_macros(content, macro_strip)
    src = content.encode("utf-8", "replace")
    tree = parser.parse(src)

    if lang in HAS_WALKER:
        # 由遍历器拥有的语言（cpp/python/...）：与引入 DSL 之前字节一致
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
        # 由基类子句生成的 extends/implements 边。dst_local 留空：
        # 在入库时（任何规则改名之后）解析为同文件内的符号 id
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

        # 可选的尽力而为 references 边（默认关闭）。基于原始 span 计算，
        # 按 _local 下标归属到外围符号。该计算需要 pend，故仅在遍历器分支可用（DSL 分支无 pend）
        if do_refs:
            edges.extend(_reference_edges(file_id, tree, src, pend))
    else:
        # 无遍历器的 DSL（matlang/bplisp/animlang）：由查询拥有符号
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
    """尽力而为地把标识符引用整理成 references 边（契约字典 src_local/dst_local）。

    参数:
        file_id: 文件在库中的 id。
        tree: 已解析的 tree-sitter 语法树。
        src: 文件的 utf-8 字节内容。
        pend: 该文件的符号累加器，提供按名查找与 span 归属。

    返回:
        references 边的契约字典列表；无可归属的函数/方法/类符号时返回空列表。
    """
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
