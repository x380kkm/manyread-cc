# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread —— UE 资产 DSL 的预检（pre-flight）结构校验器。

对 AI 或人工撰写的 DSL 文件（matlang / bplisp / animlang）做 STRUCTURAL 校验，
在昂贵且脆弱的 UE 导入之前完成：它能在毫秒级、完全 OFFLINE（无 UE、无网络、无索引
db）地捕捉那些原本会让导入器崩掉的错误（无法解析的文本、指向虚空的连线、重复的
node id、DAG 中的环、缺失的必需 root）。

有两层，都消费同一个不可变的 Context：

  * STRUCTURAL（始终开启）—— 语法/形状检查（无法解析的文本、悬空连线、重复 id、
    环、缺失 root）。注册在 `STRUCTURAL_PASSES[lang]`。
  * SEMANTIC（可选、schema 驱动）—— 一个类型字典（type-dictionary）检查：该 node
    是否是已知的表达式类、它的属性是否已知、它的必需输入 pin 是否已连接。注册在
    `SEMANTIC_PASSES[lang]`；仅当提供了 `--schema` JSON 时才运行。每个 pass 都是纯
    `(Context) -> Iterable[Issue]` 可调用对象，读取 `ctx.schema`（行携带 `kind` +
    `attrs.node_type`；已连接的 pin / 已出现的属性从 `ctx.tree` 重新遍历得到，因为
    `uses_type` 边只记录被连线的 SOURCE id 而不记录 pin 关键字）。新增 pass 无需改动
    任何 pass 列表的调用方 —— 直接 append 即可。

semantic schema 已 HARVEST-READY：它镜像未来一次性 UE 反射导出会产出的内容
（lang -> nodeType -> {classPath?, properties, pins}）。UE 增量序列化里每个
UPROPERTY 都有 CDO 默认值（缺省 == 默认，永不缺失），所以"必需性"落在输入 PIN 上，
绝不在属性上。随附的 `scripts/schemas/matlang.sample.json` 是 PARTIAL 的、从两个示例
文件推断而来；bplisp/animlang 的 semantic schema 等待 harvest。

入口：纯函数 `dsl_validate(text, lang, schema=None) -> list[Issue]` 运行该语言的
结构 pass（且当给出 `schema` 时运行其 semantic pass），返回按 (byte, code, message)
确定性排序的 issue。`schema=None` 时结果与"仅结构"校验器 BYTE-IDENTICAL。一个轻量
__main__ CLI 校验单个文件，可选加载 `--schema`，打印 issue 与摘要，当存在任意
error 级别 issue 时以非零退出。

REUSE：只有一条 parse 路径、一套 .scm captures —— 校验器 import `enrich_treesitter`
并调用其内存级 helper `_load_language`、`_load_query_specs`、`Query` 以及
`_extract_file(file_id, content, lang, parser, do_refs, query) -> (rows, edges)`。
`_extract_file` 不向 DB 写任何东西；matlang 的 `(connect $id)` 连线以 `dst_local=None`
的 `uses_type` 边到达（文件内 id 解析只在 DB 插入时发生，而校验器从不调用它），所以
校验器自己做名字集合解析。环检测复用 manyscan 的 `graph.scc` + `analyze.cycles` 自环
过滤，按显式 FILE PATH 以私有模块名加载，以避开 scripts/lib（enrich）与
scripts/manyscan/lib（analyze/graph）之间的 `lib` 包名冲突。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
# scripts/ —— 与 scripts/tests/test_enrich_query.py 相同
sys.path.insert(0, _HERE)
# 复用 _extract_file / _load_* / Query / Parser
import enrich_treesitter as E  # noqa: E402


#### 加载 manyscan 的 graph 模块，且不把其 `lib` 放上 sys.path [@380kkm 2026-06-05] ####
# analyze.py 硬编码 `from lib import graph`，而 enrich 用 `from lib import config, db`；
# scripts/lib 与 scripts/manyscan/lib 包名都是 `lib`，在一个进程内冲突，所以 enrich
# 加载后 `from lib import analyze` 会失败。graph.py 不从 lib import 任何东西，故按
# 文件路径加载是安全的 —— 但私有模块必须在 exec_module 之前注册进 sys.modules，否则
# @dataclass 内省会抛 NoneType.__dict__。
def _load_ms_graph():
    p = os.path.join(_HERE, "manyscan", "lib", "graph.py")
    spec = importlib.util.spec_from_file_location("_dsl_ms_graph", p)
    m = importlib.util.module_from_spec(spec)
    # 在 exec 之前注册（dataclass 内省所需）
    sys.modules["_dsl_ms_graph"] = m
    spec.loader.exec_module(m)
    return m


_G = _load_ms_graph()


#### 等价于 manyscan analyze.cycles（graph.scc + 自环过滤），内联实现 [@380kkm 2026-06-05] ####
def _cycles(g):
    self_loops = {e.src for e in g.edges if e.src == e.dst}
    return [c for c in _G.scc(g) if len(c) > 1 or (len(c) == 1 and c[0] in self_loops)]


#### 一条结构发现；frozen -> 可哈希/可比较（确定性测试所需） [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Issue:
    # 'error' | 'warning'
    severity: str
    # 稳定的语义字符串（PARSE_ERROR、DANGLING_WIRE…）
    code: str
    message: str
    # 从 1 起
    line: int
    # 从 0 起的字节偏移
    byte: int

    #### 排序键：(byte, code, message) [@380kkm 2026-06-05] ####
    def sort_key(self):
        return (self.byte, self.code, self.message)
#### /Issue ####


#### 一次构建、被每个检查 pass 共享的（约定不可变）上下文束 [@380kkm 2026-06-05] ####
@dataclass
class Context:
    """每个检查 pass 共享的（约定上不可变）上下文束，只构建一次。

    SEMANTIC 层消费同一个 Context（rows.kind/attrs + edges.relation/dst_name）外加
    可选的 `schema` 类型字典，所以新增一个 schema pass 不需要任何新管线。
    """

    lang: str
    text: str
    # tree_sitter.Tree（用于 PARSE 错误节点遍历）
    tree: object
    rows: list[dict]
    edges: list[dict]
    # _local -> row
    by_local: dict
    # 文件内所有符号名
    names: set
    # 每语言的 node-type 字典，或 None（仅结构）
    schema: dict | None = None

    #### 边不带字节偏移 -> 把边 issue 归到其源行的位置 [@380kkm 2026-06-05] ####
    def _row_loc(self, local):
        r = self.by_local.get(local)
        return (r["start_line"], r["start_byte"]) if r else (1, 0)
#### /Context ####


# 检查 PASS：每个都是纯 (Context) -> Iterable[Issue]。
# 严重度规则（单一最高风险的正确性决策）：
#   ERROR   = DSL 契约规定必须在文件内解析却没解析到的引用
#             （matlang (connect $id) 悬空，外加所有 DSL 的 DUP_ID / CYCLE /
#             required-form，以及 PARSE_ERROR）。
#   WARNING = 合法外部的未解析依赖（bplisp binds/calls/casts、animlang ref）——
#             这些将来在 SemanticPass 里对引擎/schema 解析；现在把它们标成 error
#             会让每个合法文件都误报（既有 enrich 测试断言它们按设计保持未解析）。

#### PARSE_ERROR：tree-sitter 拒绝该文件（任意 ERROR / MISSING 节点） [@380kkm 2026-06-05] ####
def pass_parse(ctx: Context) -> Iterable[Issue]:
    if not ctx.tree.root_node.has_error:
        return []
    out: list[Issue] = []
    stack = [ctx.tree.root_node]
    while stack:
        n = stack.pop()
        if n.is_error or n.is_missing:
            out.append(Issue(
                "error", "PARSE_ERROR",
                f"tree-sitter {'missing' if n.is_missing else 'error'} node",
                n.start_point[0] + 1, n.start_byte))
        stack.extend(n.children)
    # has_error 为真但没有 ERROR/MISSING 浮现（罕见）：给一个通用阻断项。
    return out or [Issue("error", "PARSE_ERROR", "grammar rejected file", 1, 0)]


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
    """matlang 悬空连线：(connect $id) 的 $id 未在文件内定义。

    matlang 连线以 relation 'uses_type' 发出（刻意复用，给 manyscan 边界门用），
    dst_local=None，所以这里按名解析。
    """
    node_names = {r["name"] for r in ctx.rows if r["kind"] == "node"}
    for e in ctx.edges:
        if e["relation"] != "uses_type":
            continue
        dst = e["dst_name"]
        # 只有 $id 连线在文件内解析；加守卫，使将来的非 $ 依赖不被误标。
        # 一个 $id 悬空，当且仅当它不匹配任何文件内 node 符号。
        if dst.startswith("$") and dst not in node_names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("error", "DANGLING_WIRE",
                        f"(connect {dst}) targets undefined id", ln, by)


#### matlang 是 DAG：node<->node 连线图中的任何环都是 error [@380kkm 2026-06-05] ####
def pass_matlang_cycle(ctx: Context) -> Iterable[Issue]:
    """matlang 是 DAG：node<->node 连线图中的任何环都是 error。

    该图刻意只含 node<->node 的 'uses_type' 边（排除 'outputs'/'material' 字面量），
    使它们永不成为虚假的环成员。按排序后的 node 顺序构建，使 graph.scc 输出确定。

    当任一 $id 重复时 SKIP：本 pass 按名折叠连线图，仅在 DUP_ID 所强制的文件内唯一性
    不变量下才可靠。有重复 id 时两个物理 node 融为一个图节点，把一条合法的
    `(connect $thatid)` 变成幻象自环 CYCLE，叠加在（正确的）DUP_ID 之上 —— 误报。
    DUP_ID 已标出真正的问题；在用户把 id 改唯一之前环图是有歧义的，故这里不发任何东西。
    """
    counts = Counter(r["name"] for r in ctx.rows if r["kind"] == "node")
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
    """bplisp 必需形式：至少一个 (event|func|function|macro ...) 图 root
    —— 以 kind 'graph' 的行发出。"""
    if not any(r["kind"] == "graph" for r in ctx.rows):
        yield Issue("error", "BPLISP_NO_GRAPH",
                    "no (event|func|function|macro ...) graph root", 1, 0)


#### animlang 必需形式：一个顶层图 root [@380kkm 2026-06-05] ####
def pass_animlang_required(ctx: Context) -> Iterable[Issue]:
    """animlang 必需形式：一个顶层图 root。

    animlang.scm 不发 'graph' kind —— root（样本中是 anim-blueprint）是唯一的顶层
    kind=='node'（parent_local 为 None）。一个天真的共享 kind=='graph' 检查会错误地
    让每个 animlang 文件都失败。
    """
    if not any(r["kind"] == "node" and r["parent_local"] is None for r in ctx.rows):
        yield Issue("error", "ANIMLANG_NO_GRAPH", "no top-level anim graph root", 1, 0)


#### 合法外部的未解析依赖 -> WARNING，绝不 error [@380kkm 2026-06-05] ####
def pass_external_warn(ctx: Context) -> Iterable[Issue]:
    """合法外部的未解析依赖 -> WARNING（SemanticPass 稍后对引擎/schema 解析），
    绝不 error。"""
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
        # animlang (ref "Title") 边保留引号（复用的 _query_edges 跑的是
        # _simplify_dep 而非 _dsl_name），而 ctx.names 存的是 UNQUOTED 符号名。
        # 归一化使文件内 ref 能解析，只有真正外部的名字才 warn（否则每个 ref 即便
        # 命名了文件内 node 也会误 warn —— 一个将来 SemanticPass 会继承的潜伏 bug）。
        dst = e["dst_name"].strip('"')
        if dst not in ctx.names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("warning", "UNRESOLVED_REF",
                        f"{e['relation']} target {dst} not defined in-file "
                        "(resolves against engine/schema later)", ln, by)


# SEMANTIC 层：schema（类型字典）驱动的检查 pass。
# 仅当提供了 schema 时才运行（dsl_validate(..., schema=...)）。每个都是纯
# (Context)->Iterable[Issue]；它在 ctx.schema[lang] 里查 node 行的
# attrs.node_type，检查未知类型 / 未知属性 / 缺失必需 pin。schema 已
# HARVEST-READY（镜像未来 UE 反射导出）：lang -> nodeType -> {classPath?, properties, pins}。

#### 在 ctx.tree 中定位字节跨度 == (sb, eb) 的 `list` 子树 [@380kkm 2026-06-05] ####
def _find_node_list(node, sb, eb):
    """在 ctx.tree 中定位字节跨度 == (sb, eb) 的 `list` 子树。

    semantic pass 重新遍历 node 自身的 list，以恢复它的 :keyword 属性以及哪些输入
    pin 是 CONNECTED —— 两者都不在边上（matlang 的 `uses_type` 边记录被连线的
    SOURCE id，但不记录 pin 关键字）。
    """
    if node.type == "list" and node.start_byte == sb and node.end_byte == eb:
        return node
    for c in node.children:
        r = _find_node_list(c, sb, eb)
        if r is not None:
            return r
    return None


#### 返回某 node/material 行的 (connected_pins, present_props) [@380kkm 2026-06-05] ####
def _matlang_node_fields(ctx: Context, row: dict) -> tuple[set[str], set[str]]:
    """返回某 node/material 行的 (connected_pins, present_props)。

    按顺序遍历该行的 `list` 子节点。类型为 `symbol` 且文本以 ':' 开头的子节点是关键字；
    其值是紧随其后的子节点。当且仅当值是一个第一个 `symbol` 子节点恰为 `connect` 的
    `list` 时，(:keyword, value) 对才是一次 PIN 连接；否则它是一个 PROPERTY。关键字名
    返回时去掉前导的 ':'（以匹配 schema 键）。

    关键：子节点迭代只排除结构性 token '('、')' 和 'comment'。它绝不能过滤到
    symbol/list/string 白名单 —— 数值会解析成 `number` 节点（如 ':u-tiling 2.0'、
    ':value 0.3'），白名单会丢掉它们并 MISALIGN 关键字/值的配对。纯函数 + 确定性
    （树已在 ctx.tree 上）。

    防御性配对：一个无值关键字（其紧随子节点本身又是一个 ':'-关键字，或它是最后一个
    子节点）被记为 PROPERTY 且只消费它自身 —— 不把下一个关键字吞作其值。这阻止一个
    畸形的 `(:a :b (connect ...))` 把 `:b` 误配为 `:a` 的值再把 `:b` 从 pin 集合里丢掉
    （那会为 b 虚假触发 MISSING_REQUIRED_PIN）。此类输入是畸形的、不在任何随附示例里；
    结构层也不会捕捉它，故这是叠加在结构校验之上的尽力而为加固。
    """
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
                # 无值关键字：记为 property，不把下一个关键字消费为其值
                # （前进 1，重新审视下一个子节点）。
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
    """SEMANTIC 类型字典检查（matlang）。除非 Context 上携带 schema，否则不发任何东西
    （使无 schema 路径保持字节级一致）。

    对每个 node/material 行，在 ctx.schema[lang] 里查 attrs.node_type（或 'material'）
    并发出：
      * UNKNOWN_NODE_TYPE (warning) —— 类型不在（PARTIAL）字典里。
      * UNKNOWN_PROP (warning)      —— 一个 :keyword 既非已知属性也非已知 pin 名。
      * MISSING_REQUIRED_PIN (error)—— schema 中 required:true 的 pin，其关键字不在已
                                      连接集合里。
    缺省的 OPTIONAL 属性绝不被标记（缺省 == CDO 默认）。只处理 kind=='node'（且
    node_type 非空）与 kind=='material' 行；outputs / 无 node_type 的行被跳过（否则
    它们的槽位关键字会误 warn）。遍历行 + 排序后的 props/pins 使发出确定；dsl_validate
    里唯一的最终排序为合并后的列表定序。
    """
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


# 检查 pass 注册表（插件接缝）。
# 有序、按语言。STRUCTURAL 始终运行；SEMANTIC 仅当提供 schema 时运行。每个 pass 纯且
# 独立、消费同一个 Context，所以新增 pass 只是在这里 APPEND —— 无需其他改动。现有结构
# pass；matlang schema 字典就绪；bplisp/animlang schema 待 UE harvest。
STRUCTURAL_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_parse, pass_matlang_required, pass_matlang_dup_id,
                pass_matlang_dangling, pass_matlang_cycle],
    "bplisp": [pass_parse, pass_bplisp_required, pass_external_warn],
    "animlang": [pass_parse, pass_animlang_required, pass_external_warn],
}

# 镜像 STRUCTURAL_PASSES。如今只有 matlang 有样本 schema；bplisp / animlang 等待 UE
# 反射 harvest（UFunction / anim-node 签名）。
SEMANTIC_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_semantic_schema],
    "bplisp": [],
    "animlang": [],
}


#### 纯 semantic-schema 加载器：json.load + 形状校验 [@380kkm 2026-06-05] ####
def load_schema(path: str) -> dict:
    """纯 semantic-schema 加载器：json.load + 形状校验。形状畸形时抛 ValueError
    （带清晰消息），让 CLI 能报告干净的错误而非 traceback。以 '$' 开头的顶层元数据键
    （如 '$schema_note'）被允许并忽略。

    形状：root 是对象；每个非 '$' 键（一个语言）映射到对象；每个 nodeType 映射到对象；
    可选的 'properties' 是对象；可选的 'pins' 是对象，其条目为带可选 bool 'required'
    的对象。
    """
    import json

    with open(path, encoding="utf-8") as fh:
        # JSONDecodeError 浮现给 CLI
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("schema root must be a JSON object (lang -> nodeType -> spec)")
    for lang, types in data.items():
        # 元数据键 -> 忽略
        if lang.startswith("$"):
            continue
        if not isinstance(types, dict):
            raise ValueError(f"schema[{lang!r}] must be an object of nodeType -> spec")
        for nt, spec in types.items():
            if not isinstance(spec, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}] must be an object")
            props = spec.get("properties", {})
            pins = spec.get("pins", {})
            if not isinstance(props, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}].properties must be an object")
            if not isinstance(pins, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}].pins must be an object")
            for pn, pv in pins.items():
                if not isinstance(pv, dict):
                    raise ValueError(
                        f"schema[{lang!r}][{nt!r}].pins[{pn!r}] must be an object")
                if "required" in pv and not isinstance(pv["required"], bool):
                    raise ValueError(
                        f"schema[{lang!r}][{nt!r}].pins[{pn!r}].required must be a bool")
    return data


#### 构建 Context（唯一 parse 路径；内存级；无 DB） [@380kkm 2026-06-05] ####
def _build_context(text: str, lang: str) -> Context:
    # 三个 DSL 都用 'scheme' 文法
    L = E._load_language(lang)
    parser = E.Parser(L)
    tree = parser.parse(text.encode("utf-8", "replace"))
    # 仅内置的 scripts/queries/<lang>.scm（纯）
    specs = E._load_query_specs(None)
    query = E.Query(L, specs[lang]) if lang in specs else None
    # _extract_file 内部会重新 parse（接受 content 而非 tree）；这两次亚毫秒级
    # parse 为避免改动 enrich 的签名而被接受。
    rows, edges = E._extract_file(0, text, lang, parser, False, query)
    return Context(lang, text, tree, rows, edges,
                   {r["_local"]: r for r in rows}, {r["name"] for r in rows})


#### 纯预检校验器：跑结构 pass（有 schema 则加 semantic），确定性排序返回 issue [@380kkm 2026-06-05] ####
def dsl_validate(text: str, lang: str, schema: dict | None = None) -> list[Issue]:
    """纯预检校验器：运行该语言的 STRUCTURAL pass，且当提供 `schema` 时运行其
    SEMANTIC pass，然后按 (byte, code, message) 确定性排序返回 issue。

    schema=None 时结果与"仅结构"校验器 BYTE-IDENTICAL（不构建也不运行 semantic pass，
    同一个最终单次排序生效）—— 故每个现有的 2 参调用方都不受影响。

    当 parse 失败时，其余 pass 仍在部分 rows/edges 上运行（PARSE_ERROR 主导摘要）；
    这让流水线保持简单，一个文件可同时浮现 parse error 与结构 issue。
    """
    if lang not in STRUCTURAL_PASSES:
        return [Issue("error", "UNKNOWN_LANG", f"no validator for language {lang!r}", 1, 0)]
    ctx = _build_context(text, lang)
    issues = [i for p in STRUCTURAL_PASSES[lang] for i in p(ctx)]
    if schema is not None:
        # 携带字典；结构 pass 忽略它
        ctx.schema = schema
        issues += [i for p in SEMANTIC_PASSES.get(lang, []) for i in p(ctx)]
    # 唯一的最终排序 -> 确定性得以保持
    issues.sort(key=lambda i: i.sort_key())
    return issues


#### 轻量 CLI：校验单文件，可选 --schema，打印 issue + 摘要，有 error 则非零退出 [@380kkm 2026-06-05] ####
def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        prog="dsl_validate.py",
        description="Pre-flight STRUCTURAL validator for UE asset DSLs "
                    "(matlang/bplisp/animlang). Pure + offline; nonzero exit on error.")
    ap.add_argument("file", help="the DSL file to validate")
    ap.add_argument("--lang", default=None,
                    help="matlang|bplisp|animlang (default: auto-detect by extension)")
    ap.add_argument("--schema", default=None,
                    help="optional semantic schema JSON (a node-type dictionary); "
                         "enables the SEMANTIC layer (unknown type/prop, missing required pin). "
                         "No --schema -> structural-only.")
    ap.add_argument("--json", action="store_true", help="emit issues as a JSON list")
    a = ap.parse_args(argv)

    lang = a.lang or E.LANG_FOR_EXT.get(os.path.splitext(a.file)[1].lower())
    if lang not in STRUCTURAL_PASSES:
        print(f"error: unknown DSL for {a.file!r} (use --lang matlang|bplisp|animlang)",
              file=sys.stderr)
        return 2
    try:
        with open(a.file, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: cannot read {a.file!r}: {exc}", file=sys.stderr)
        return 2

    schema = None
    if a.schema:
        try:
            schema = load_schema(a.schema)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: malformed schema {a.schema!r}: {exc}", file=sys.stderr)
            return 2

    issues = dsl_validate(text, lang, schema)
    if a.json:
        print(json.dumps([asdict(i) for i in issues], indent=2))
    else:
        for i in issues:
            print(f"{i.severity.upper():7} {i.code:18} L{i.line} b{i.byte}: {i.message}")
        n_err = sum(i.severity == "error" for i in issues)
        n_warn = sum(i.severity == "warning" for i in issues)
        if not issues:
            print(f"OK      {a.file} ({lang}): no structural issues")
        else:
            print(f"-- {n_err} error(s), {n_warn} warning(s)")
    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
