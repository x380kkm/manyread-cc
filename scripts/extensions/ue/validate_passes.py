"""UE 资产 DSL（matlang / bplisp / animlang）的结构 + 语义校验 pass。

这些 pass 是 UE 扩展专属的；通用核心（scripts/dsl_validate.py）只提供 pass 协议、
不可变的 Context、通用的 pass_parse 与 schema 加载。本模块从 dsl_validate 原样移出
UE 特有的检查逻辑，并经 ue/__init__.register 把它们按 v0.8.16 的确定顺序注册回去。

每个 pass 是 `(Context) -> Iterable[Issue]` 的纯函数，复用 dsl_validate 的 Issue/Context。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections import Counter
from typing import Iterable

# scripts/ 已在 sys.path 上（由入口脚本插入）；从通用核心取 Issue/Context
from dsl_validate import Context, Issue

# scripts/ 目录（用于按路径加载 manyscan 的 graph 模块）
_SCRIPTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


#### 按文件路径加载 manyscan 的 graph 模块 [@380kkm 2026-06-05] ####
def _load_ms_graph():
    p = os.path.join(_SCRIPTS_DIR, "manyscan", "lib", "graph.py")
    spec = importlib.util.spec_from_file_location("_dsl_ms_graph", p)
    m = importlib.util.module_from_spec(spec)
    # 在 exec_module 之前注册进 sys.modules
    sys.modules["_dsl_ms_graph"] = m
    spec.loader.exec_module(m)
    return m


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


#### bplisp 必需形式：至少一个 (event|func|function|macro ...) 图 root [@380kkm 2026-06-05] ####
def pass_bplisp_required(ctx: Context) -> Iterable[Issue]:
    if not any(r["kind"] == "graph" for r in ctx.rows):
        yield Issue("error", "BPLISP_NO_GRAPH",
                    "no (event|func|function|macro ...) graph root", 1, 0)


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


#### 返回某 node/material 行的 (connected_pins, present_props) [@380kkm 2026-06-05] ####
def _matlang_node_fields(ctx: Context, row: dict) -> tuple[set[str], set[str]]:
    pins: set[str] = set()
    props: set[str] = set()
    n = _find_node_list(ctx.tree.root_node, row["start_byte"], row["end_byte"])
    if n is None:
        return pins, props
    src = ctx.text.encode("utf-8", "replace")
    kids = [c for c in n.children if c.type not in ("(", ")", "comment")]

    #### 判定子节点是否为 ':'-关键字 symbol [@380kkm 2026-06-05] ####
    def _is_keyword(c) -> bool:
        return c.type == "symbol" and src[c.start_byte:c.end_byte].startswith(b":")

    i = 0
    while i < len(kids):
        k = kids[i]
        if _is_keyword(k):
            # 去掉一个 ':'
            name = src[k.start_byte:k.end_byte].decode("utf-8", "replace")[1:]
            val = kids[i + 1] if i + 1 < len(kids) else None
            if val is None or _is_keyword(val):
                # 无值关键字记为 property，只消费它自身
                props.add(name)
                i += 1
                continue
            is_connect = (val.type == "list" and any(
                c.type == "symbol" and src[c.start_byte:c.end_byte] == b"connect"
                for c in val.children))
            (pins if is_connect else props).add(name)
            i += 2
        else:
            i += 1
    return pins, props


#### SEMANTIC 类型字典检查（matlang）；无 schema 时不发任何东西 [@380kkm 2026-06-05] ####
def pass_semantic_schema(ctx: Context) -> Iterable[Issue]:
    if not ctx.schema:
        return
    lang_schema = ctx.schema.get(ctx.lang)
    # 该语言无字典 -> 不发任何东西
    if not lang_schema:
        return
    for r in sorted(ctx.rows, key=lambda r: (r["start_byte"], r["end_byte"])):
        if r["kind"] == "node":
            nt = (r.get("attrs") or {}).get("node_type")
        elif r["kind"] == "material":
            nt = "material"
        else:
            continue
        if not nt:
            continue
        spec = lang_schema.get(nt)
        if spec is None:
            yield Issue("warning", "UNKNOWN_NODE_TYPE",
                        f"node type {nt!r} not in schema (dictionary is partial)",
                        r["start_line"], r["start_byte"])
            continue
        connected, props = _matlang_node_fields(ctx, r)
        known_props = set((spec.get("properties") or {}).keys())
        known_pins = set((spec.get("pins") or {}).keys())
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
