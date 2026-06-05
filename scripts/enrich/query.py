from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from enrich.langreg import QueryCursor
from enrich.model import _text


#### 取捕获 token 的最近 `list` 祖先，作为该符号的 span [@380kkm 2026-06-05] ####
def _dsl_list_ancestor(node: Node) -> Node | None:
    n = node
    while n is not None and n.type != "list":
        n = n.parent
    return n
#### /取捕获 token 的最近 `list` 祖先 ####


#### 仅从被捕获节点自身取符号名 [@380kkm 2026-06-05] ####
def _dsl_name(node: Node, src: bytes) -> str:
    nm = _text(node, src)
    # scheme `string` 文本包含引号
    if node.type == "string":
        nm = nm.strip('"')
    return nm or "<anon>"
#### /仅从被捕获节点自身取符号名 ####


#### 从 `@def.<kind>` 捕获产出 walker-less DSL 的符号 [@380kkm 2026-06-05] ####
def _query_symbols(file_id: int, tree, src: bytes, query, lang: str) -> list[dict]:
    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001
        return []

    #### 收集每个 `@def.*` 捕获的 (start_byte, end_byte, kind, name) -> head [@380kkm 2026-06-05] ####
    raw: dict[tuple[int, int, str, str], str] = {}
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
    #### /收集每个 `@def.*` 捕获 ####

    # 按全序键排序后分配确定性的 _local 下标
    keys = sorted(raw)
    spans = [(k[0], k[1]) for k in keys]

    #### 取第 i 个符号的 parent：最小的严格包含其 span 的前序 span [@380kkm 2026-06-05] ####
    def _parent_of(i: int) -> int | None:
        si, ei = spans[i]
        # 最小严格包含 span 的 (size, local)
        best = None
        for j, (sj, ej) in enumerate(spans):
            if j == i:
                continue
            if sj <= si and ei <= ej and (ej - sj) > (ei - si):
                size = ej - sj
                if best is None or size < best[0] or (size == best[0] and j < best[1]):
                    best = (size, j)
        return best[1] if best is not None else None
    #### /取第 i 个符号的 parent ####

    #### 按 span 重新收集祖先节点，用于取行号 [@380kkm 2026-06-05] ####
    span_to_node: dict[tuple[int, int], Node] = {}
    for cap_name in sorted(caps):
        if not cap_name.startswith("def."):
            continue
        for node in caps[cap_name]:
            anc = _dsl_list_ancestor(node)
            if anc is None:
                continue
            span_to_node.setdefault((anc.start_byte, anc.end_byte), anc)
    #### /按 span 重新收集祖先节点 ####

    #### 组装共享契约的 row-dict 列表 [@380kkm 2026-06-05] ####
    rows: list[dict] = []
    for i, (sb, eb, kind, name) in enumerate(keys):
        head = raw[(sb, eb, kind, name)]
        anc = span_to_node[(sb, eb)]
        # 仅当节点类型与名字不同才把类型提升进 attrs
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
    #### /组装共享契约的 row-dict 列表 ####
#### /从 `@def.<kind>` 捕获产出 DSL 的符号 ####


# 内置声明式依赖边查询 .scm 的预设目录
_QUERY_DIR = Path(__file__).resolve().parent.parent / "queries"


#### 加载 lang -> .scm 文本：内置预设，再叠加项目覆盖（覆盖优先） [@380kkm 2026-06-05] ####
def _load_query_specs(root) -> dict[str, str]:
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
#### /加载 lang -> .scm 文本 ####


#### 把捕获的类型/名字化简为裸标识符，供按名 resolve [@380kkm 2026-06-05] ####
def _simplify_dep(name: str) -> str:
    s = name.split("|")[0].strip()
    s = s.split("[")[0].split("<")[0].strip()
    return s.split("::")[-1].split(".")[-1].strip()
#### /把捕获的类型/名字化简为裸标识符 ####


#### 从 `@dep.<relation>` 捕获产出边，归属到外围符号 [@380kkm 2026-06-05] ####
def _query_edges(file_id: int, tree, src: bytes, query, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    spans = sorted(((r["start_byte"], r["end_byte"], r["_local"]) for r in rows),
                   key=lambda s: (s[0], -s[1]))

    #### 返回包含给定字节位置的外围符号 _local [@380kkm 2026-06-05] ####
    def enclosing(byte: int):
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best
    #### /返回外围符号 _local ####

    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001
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
#### /从 `@dep.<relation>` 捕获产出边 ####
