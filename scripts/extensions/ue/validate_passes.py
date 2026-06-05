# audience: internal
# extensions.ue.validate_passes
"""UE 资产 DSL（matlang / bplisp / animlang）的结构 + 语义校验 pass。

这些 pass 是 UE 扩展专属的；通用核心（scripts/dsl_validate.py）只提供 pass 协议、
不可变的 Context、通用的 pass_parse 与 schema 加载。本模块从 dsl_validate 原样移出
UE 特有的检查逻辑，并经 ue/__init__.register_enrich 把它们按 v0.8.16 的确定顺序注册回去。

每个 pass 是 `(Context) -> Iterable[Issue]` 的纯函数，复用 dsl_validate 的 Issue/Context。
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Iterable

# scripts/ 已在 sys.path 上（由入口脚本插入）；从通用核心取 Issue/Context
from dsl_validate import Context, Issue
# 同包的纯 stdlib 按路径加载器（不拖入 tree-sitter、不反向 import manyscan 的规范实现）
from extensions.ue._modload import load_module as _load_module

# scripts/ 目录（用于按路径加载 manyscan 的 graph 模块）
_SCRIPTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


#### 按文件路径加载 manyscan 的 graph 模块 [@380kkm 2026-06-05] ####
def _load_ms_graph():
    p = os.path.join(_SCRIPTS_DIR, "manyscan", "lib", "graph.py")
    return _load_module("_dsl_ms_graph", p)


_G = _load_ms_graph()


#### 等价于 manyscan analyze.cycles（graph.scc + 自环过滤），内联实现 [@380kkm 2026-06-05] ####
def _cycles(g):
    self_loops = {e.src for e in g.edges if e.src == e.dst}
    return [c for c in _G.scc(g) if len(c) > 1 or (len(c) == 1 and c[0] in self_loops)]


#### matlang 必需形式：一个 (material ...) root + 一个 (outputs ...) 块 [@380kkm 2026-06-05] ####
def pass_matlang_required(ctx: Context) -> Iterable[Issue]:
    if not any(r["kind"] == "material" for r in ctx.rows):
        yield Issue("error", "MATLANG_NO_MATERIAL", "no (material ...) root", 1, 0)
    if not any(r["kind"] == "outputs" for r in ctx.rows):
        yield Issue("error", "MATLANG_NO_OUTPUTS", "no (outputs ...) block", 1, 0)


#### matlang $id 唯一性：报告 node 名的每个第 2 次及以后出现 [@380kkm 2026-06-05] ####
def pass_matlang_dup_id(ctx: Context) -> Iterable[Issue]:
    counts = Counter(r["name"] for r in ctx.rows if r["kind"] == "node")
    seen: set[str] = set()
    for r in sorted((r for r in ctx.rows if r["kind"] == "node"),
                    key=lambda r: (r["start_byte"], r["end_byte"])):
        if counts[r["name"]] > 1:
            # 第 2 次及以后出现的才是重复
            if r["name"] in seen:
                yield Issue("error", "DUP_ID",
                            f"duplicate node id {r['name']}",
                            r["start_line"], r["start_byte"])
            seen.add(r["name"])


#### matlang 悬空连线：(connect $id) 的 $id 未在文件内定义 [@380kkm 2026-06-05] ####
def pass_matlang_dangling(ctx: Context) -> Iterable[Issue]:
    node_names = {r["name"] for r in ctx.rows if r["kind"] == "node"}
    for e in ctx.edges:
        if e["relation"] != "uses_type":
            continue
        dst = e["dst_name"]
        # $id 不匹配任何文件内 node 符号即为悬空
        if dst.startswith("$") and dst not in node_names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("error", "DANGLING_WIRE",
                        f"(connect {dst}) targets undefined id", ln, by)


#### matlang 是 DAG：node<->node 连线图中的任何环都是 error [@380kkm 2026-06-05] ####
def pass_matlang_cycle(ctx: Context) -> Iterable[Issue]:
    counts = Counter(r["name"] for r in ctx.rows if r["kind"] == "node")
    # 有重复 id 时连线图按名折叠不可靠，交由 DUP_ID 处理
    if any(c > 1 for c in counts.values()):
        return
    node_names = set(counts)
    g = _G.Graph()
    for n in sorted(node_names):
        g.add_node(_G.Node(id=n, kind="node", label=n))
    for e in ctx.edges:
        if e["relation"] != "uses_type":
            continue
        s = (ctx.by_local.get(e["src_local"]) or {}).get("name")
        d = e["dst_name"]
        if s in node_names and d in node_names:
            g.add_edge(_G.Edge(src=s, dst=d, relation="wire"))
    for comp in _cycles(g):
        members = sorted(comp)
        loc = min((r["start_line"], r["start_byte"]) for r in ctx.rows
                  if r["name"] in comp)
        yield Issue("error", "CYCLE",
                    "wire cycle: " + " -> ".join(members), loc[0], loc[1])


#### bplisp 必需形式：至少一个图 root（事件类/函数类/transition-cond） [@380kkm 2026-06-05] ####
def pass_bplisp_required(ctx: Context) -> Iterable[Issue]:
    if not any(r["kind"] == "graph" for r in ctx.rows):
        yield Issue("error", "BPLISP_NO_GRAPH",
                    "no graph root (event|input-action|input-key|component-bound-event"
                    "|actor-bound-event|func|function|macro|transition-cond)", 1, 0)


#### animlang 必需形式：一个顶层图 root [@380kkm 2026-06-05] ####
def pass_animlang_required(ctx: Context) -> Iterable[Issue]:
    if not any(r["kind"] == "node" and r["parent_local"] is None for r in ctx.rows):
        yield Issue("error", "ANIMLANG_NO_GRAPH", "no top-level anim graph root", 1, 0)


#### 合法外部的未解析依赖 -> WARNING，绝不 error [@380kkm 2026-06-05] ####
def pass_external_warn(ctx: Context) -> Iterable[Issue]:
    external_rels = {
        # matlang 连线在文件内解析（由 pass_matlang_dangling 处理）
        "matlang": (),
        "bplisp": ("binds", "calls", "casts"),
        "animlang": ("ref",),
    }
    rels = external_rels.get(ctx.lang, ())
    for e in ctx.edges:
        if e["relation"] not in rels:
            continue
        # 去掉引号以匹配 ctx.names 里的 unquoted 符号名
        dst = e["dst_name"].strip('"')
        if dst not in ctx.names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("warning", "UNRESOLVED_REF",
                        f"{e['relation']} target {dst} not defined in-file "
                        "(resolves against engine/schema later)", ln, by)


#### 在 ctx.tree 中定位字节跨度 == (sb, eb) 的 `list` 子树 [@380kkm 2026-06-05] ####
def _find_node_list(node, sb, eb):
    if node.type == "list" and node.start_byte == sb and node.end_byte == eb:
        return node
    for c in node.children:
        r = _find_node_list(c, sb, eb)
        if r is not None:
            return r
    return None


#### 返回某 list 行的 (connected_pins, present_props)；classify 定 pin 判据 [@380kkm 2026-06-05] ####
def _node_fields(ctx: Context, row: dict, classify) -> tuple[set[str], set[str]]:
    """走查 (head :k v :k v ...) 的 :key 配对：classify(val, src) 返回 'flag'（无值关键字，
    只消费 key）/ 'pin' / 'prop'。matlang 以「值是含 connect 的 list」判 pin、且把关键字值
    的 key 视为 flag；bplisp/animlang 以「值是 list」判 pin、关键字值按 prop 消费两位。"""
    pins: set[str] = set()
    props: set[str] = set()
    n = _find_node_list(ctx.tree.root_node, row["start_byte"], row["end_byte"])
    if n is None:
        return pins, props
    src = ctx.text.encode("utf-8", "replace")
    kids = [c for c in n.children if c.type not in ("(", ")", "comment")]

    i = 0
    while i < len(kids):
        k = kids[i]
        if _is_keyword(k, src):
            # 去掉一个 ':'
            name = src[k.start_byte:k.end_byte].decode("utf-8", "replace")[1:]
            val = kids[i + 1] if i + 1 < len(kids) else None
            cls = classify(val, src)
            if cls == "flag":
                # 无值关键字记为 property，只消费它自身
                props.add(name)
                i += 1
                continue
            (pins if cls == "pin" else props).add(name)
            i += 2
        else:
            i += 1
    return pins, props


#### 判定子节点是否为 ':'-关键字 symbol [@380kkm 2026-06-05] ####
def _is_keyword(c, src: bytes) -> bool:
    return c is not None and c.type == "symbol" \
        and src[c.start_byte:c.end_byte].startswith(b":")


#### matlang pin 判据：关键字值=flag、含 connect 的 list=pin、余=prop [@380kkm 2026-06-05] ####
def _matlang_classify(val, src: bytes) -> str:
    if val is None or _is_keyword(val, src):
        return "flag"
    is_connect = (val.type == "list" and any(
        c.type == "symbol" and src[c.start_byte:c.end_byte] == b"connect"
        for c in val.children))
    return "pin" if is_connect else "prop"


#### 返回某 node/material 行的 (connected_pins, present_props) [@380kkm 2026-06-05] ####
def _matlang_node_fields(ctx: Context, row: dict) -> tuple[set[str], set[str]]:
    return _node_fields(ctx, row, _matlang_classify)


#### 取 matlang 行的 schema 类型键：node 用 attrs.node_type、material 行用 'material' [@380kkm 2026-06-05] ####
def _matlang_key(r) -> str | None:
    if r["kind"] == "node":
        return (r.get("attrs") or {}).get("node_type")
    if r["kind"] == "material":
        return "material"
    return None


#### SEMANTIC 类型字典检查（matlang）；无 schema 时不发任何东西 [@380kkm 2026-06-05] ####
def pass_semantic_schema(ctx: Context) -> Iterable[Issue]:
    # matlang 命名属性与位置标识符可区分，恒判未知属性（strict_props=True）
    yield from _semantic_node_check(ctx, _matlang_key, _matlang_node_fields,
                                    strict_props=True)


#### bplisp/animlang pin 判据：无值=flag、list 值=pin（pose 子节点）、余=prop [@380kkm 2026-06-05] ####
def _sexpr_classify(val, src: bytes) -> str:
    # 列表末尾的无值关键字 -> flag；值是 list（pose 子节点）-> pin；
    # 字面量 / 关键字 param-ref / 表达式 -> prop（关键字值按 prop 消费两位）
    if val is None:
        return "flag"
    return "pin" if val.type == "list" else "prop"


#### 返回某 S 表达式节点行的 (connected_pose_pins, present_props) [@380kkm 2026-06-05] ####
def _sexpr_node_fields(ctx: Context, row: dict) -> tuple[set[str], set[str]]:
    return _node_fields(ctx, row, _sexpr_classify)


#### 复用 schema 字典检查走查（按 key_fn 取类型、按 fields_fn 取字段） [@380kkm 2026-06-05] ####
def _semantic_node_check(ctx: Context, key_fn, fields_fn,
                         strict_props: bool = False) -> Iterable[Issue]:
    """key_fn(row) -> 该行的 schema 类型键，None 表示跳过该行（未知类型不发 warning 时返回
    None；要发 UNKNOWN_NODE_TYPE 则返回键并由本函数处理）。fields_fn(ctx,row) ->
    (connected_pins, present_props)。strict_props=True 时对所有 form 恒判未知属性（matlang）。
    无 schema / 该语言无字典时不发任何东西（--schema 门控）。
    """
    if not ctx.schema:
        return
    lang_schema = ctx.schema.get(ctx.lang)
    if not lang_schema:
        return
    for r in sorted(ctx.rows, key=lambda r: (r["start_byte"], r["end_byte"])):
        nt = key_fn(r)
        if not nt:
            continue
        spec = lang_schema.get(nt)
        if spec is None:
            yield Issue("warning", "UNKNOWN_NODE_TYPE",
                        f"node type {nt!r} not in schema (dictionary is partial)",
                        r["start_line"], r["start_byte"])
            continue
        connected, props = fields_fn(ctx, r)
        known_props = set((spec.get("properties") or {}).keys())
        known_pins = set((spec.get("pins") or {}).keys())
        # UNKNOWN_PROP 对 strict_props 或声明 strict-props 的 form 启用：设计稿 animlang
        # 用前导位置关键字（状态名 / from-to 引用）做标识符，与命名属性形状不可区分，默认不判
        if strict_props or spec.get("strict-props"):
            for p in sorted(props):
                if p not in known_props and p not in known_pins:
                    yield Issue("warning", "UNKNOWN_PROP",
                                f"{nt}: unknown property :{p}",
                                r["start_line"], r["start_byte"])
        for pin, pspec in sorted((spec.get("pins") or {}).items()):
            if pspec.get("required") and pin not in connected:
                yield Issue("error", "MISSING_REQUIRED_PIN",
                            f"{nt} (id {r['name']}): required pin :{pin} not connected",
                            r["start_line"], r["start_byte"])


#### SEMANTIC 类型字典检查（bplisp）；类型键=name，CALL 节点开放词表不检查 [@380kkm 2026-06-05] ####
def pass_bplisp_semantic(ctx: Context) -> Iterable[Issue]:
    #### 取 bplisp 行的 schema 类型键：仅 kind=='node' 用 name，其余跳过 [@380kkm 2026-06-05] ####
    def _key(r) -> str | None:
        # CALL = 任意 UFunction（开放词表），不判未知；graph 由结构 pass 检查
        return r["name"] if r["kind"] == "node" else None

    yield from _semantic_node_check(ctx, _key, _sexpr_node_fields)


#### SEMANTIC 类型字典检查（animlang）；类型键=name [@380kkm 2026-06-05] ####
def pass_animlang_semantic(ctx: Context) -> Iterable[Issue]:
    #### 取 animlang 行的 schema 类型键：仅 kind=='node' 用 name，其余跳过 [@380kkm 2026-06-05] ####
    def _key(r) -> str | None:
        return r["name"] if r["kind"] == "node" else None

    yield from _semantic_node_check(ctx, _key, _sexpr_node_fields)
