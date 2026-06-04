from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from enrich.langreg import QueryCursor
from enrich.model import _text


#### 取捕获 token 的最近 `list` 祖先，作为该符号的 span [@380kkm 2026-06-05] ####
# S 表达式资产 DSL 没有 walker；其节点图（"连连看"接线）完全来自 .scm 查询：
# `@def.<kind>` -> 一个符号，`@dep.<relation>` -> 一条从外围 @def 符号出发的边
# （复用 _query_edges）。scheme grammar 把每个 (...) 形式都解析为 `list`，所以一个被捕获
# token 的容纳 span 是它最近的外围 `list` 祖先（token 自身 —— $id / 引号字符串 / 头符号 ——
# 并不覆盖该节点内部嵌套的接线）
def _dsl_list_ancestor(node: Node) -> Node | None:
    """返回被捕获 token 的最近外围 `list` 祖先（即该符号的 span）。"""
    n = node
    while n is not None and n.type != "list":
        n = n.parent
    # 可能为 None（防御性）-> 调用方跳过该捕获
    return n
#### /取捕获 token 的最近 `list` 祖先 ####


#### 仅从被捕获节点自身取符号名 [@380kkm 2026-06-05] ####
def _dsl_name(node: Node, src: bytes) -> str:
    """仅从本捕获节点取符号名（绝不与兄弟捕获 zip 配对）。

    scheme `string` 节点的文本包含两侧引号，故引号名需剥除引号
    （material "M_X" -> M_X）。保留 matlang $id 前导的 '$'：_simplify_dep 原样保留
    '$mul1'，所以 (connect $mul1) 这条边的 dst_name '$mul1' 必须等于节点符号名
    '$mul1'，按名 resolve 才能命中。
    """
    nm = _text(node, src)
    # scheme `string` 文本包含引号
    if node.type == "string":
        nm = nm.strip('"')
    return nm or "<anon>"
#### /仅从被捕获节点自身取符号名 ####


#### 从 `@def.<kind>` 捕获产出 walker-less DSL 的符号 [@380kkm 2026-06-05] ####
def _query_symbols(file_id: int, tree, src: bytes, query, lang: str) -> list[dict]:
    """从 `@def.<kind>` 捕获产出符号，用于没有 walker 的 DSL（查询拥有符号）。

    每个捕获落在一个 token 上；符号的 span 是该 token 外围的 `list` 祖先。
    parent = 最内层严格包含它的 @def span。

    返回共享契约的 row-dict 形状（与 walker 产出的键相同）。
    确定性：captures() 的成员稳定但顺序不稳，故在分配 `_local` 下标前
    按全序键 (start_byte, end_byte, kind, name) 排序。
    """
    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a bad query must never abort enrichment
        return []

    #### 收集每个 `@def.*` 捕获的 (start_byte, end_byte, kind, name) -> head [@380kkm 2026-06-05] ####
    # 以被捕获 token 外围的 `list` 祖先为 span。head 是该 list 的首个子 `symbol`
    # （节点类型），随后提升进 attrs
    # 四元键 -> head（去重被多个 pattern 命中的同一 list；Node 不在键内 —— span 可恢复）
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

    # 对存活的 row 取全序，分配确定性的 _local 下标
    # (start_byte, end_byte, kind, name) 构成全序
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
    # 每个 `list` 的 span 唯一；以首次见到的捕获建立 span -> node 映射
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
        # （matlang 情形：head=类型如 'multiply'，name=$id 如 '$mul1'）。
        # material/outputs/graph/... 等 head==name（或冗余）-> 无 attr
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


# 声明式依赖边查询（项目可定制）：符号来自上面的 walker；依赖边可在每种语言的
# tree-sitter 查询（.scm）中声明：每个 `@dep.<relation>` 捕获成为一条从外围符号到
# 被捕获名字的边（relation = 后缀）。内置预设在 scripts/queries/<lang>.scm；
# 项目可在 <root>/.manyread/queries/<lang>.scm 整体替换覆盖。没有 .scm 的语言保留
# 仅 walker 的边（如 C++），故此机制纯增量且向后兼容
_QUERY_DIR = Path(__file__).resolve().parent.parent / "queries"


#### 加载 lang -> .scm 文本：内置预设，再叠加项目覆盖（覆盖优先） [@380kkm 2026-06-05] ####
def _load_query_specs(root) -> dict[str, str]:
    """lang -> .scm 文本：先内置预设，后项目覆盖（覆盖胜出）。"""
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
    """把捕获的类型/名字化简为裸标识符以便按名 resolve（与 inherit 化简一致）：
    联合取首项、剥除泛型、取最后一段。"""
    s = name.split("|")[0].strip()
    s = s.split("[")[0].split("<")[0].strip()
    return s.split("::")[-1].split(".")[-1].strip()
#### /把捕获的类型/名字化简为裸标识符 ####


#### 从 `@dep.<relation>` 捕获产出边，归属到外围符号 [@380kkm 2026-06-05] ####
def _query_edges(file_id: int, tree, src: bytes, query, rows: list[dict]) -> list[dict]:
    """从 `@dep.<relation>` 捕获产出边，每条归属到外围符号
    （包含该捕获的最小 row span）。排序 + 去重 => 确定性。"""
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
#### /从 `@dep.<relation>` 捕获产出边 ####
