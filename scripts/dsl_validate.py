# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread —— UE 资产 DSL 的预检（pre-flight）结构校验器。

对 DSL 文件（matlang / bplisp / animlang）做 OFFLINE 的结构校验：捕捉无法解析的
文本、悬空连线、重复 node id、DAG 中的环、缺失的必需 root。

两层，都消费同一个不可变的 Context：

  * STRUCTURAL（始终开启）—— 语法/形状检查，注册在 `STRUCTURAL_PASSES[lang]`。
  * SEMANTIC（可选、schema 驱动）—— 类型字典检查（已知 node 类、已知属性、已连接的
    必需 pin），注册在 `SEMANTIC_PASSES[lang]`，仅当给出 `--schema` JSON 时运行。

入口：纯函数 `dsl_validate(text, lang, schema=None) -> list[Issue]` 运行该语言的结构
pass（给出 `schema` 时加运行其 semantic pass），返回按 (byte, code, message) 确定性
排序的 issue。`__main__` CLI 校验单个文件，可选加载 `--schema`，打印 issue 与摘要，
存在 error 级 issue 时以非零退出。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入 sys.path
sys.path.insert(0, _HERE)
# 复用 _extract_file / _load_* / Query / Parser
import enrich_treesitter as E  # noqa: E402


#### 按文件路径加载 manyscan 的 graph 模块 [@380kkm 2026-06-05] ####
def _load_ms_graph():
    p = os.path.join(_HERE, "manyscan", "lib", "graph.py")
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


#### 一条结构发现；frozen -> 可哈希/可比较 [@380kkm 2026-06-05] ####
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
    # has_error 为真但无 ERROR/MISSING 节点时给一个通用阻断项
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


# 按语言注册的有序结构 pass 表
STRUCTURAL_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_parse, pass_matlang_required, pass_matlang_dup_id,
                pass_matlang_dangling, pass_matlang_cycle],
    "bplisp": [pass_parse, pass_bplisp_required, pass_external_warn],
    "animlang": [pass_parse, pass_animlang_required, pass_external_warn],
}

# 按语言注册的有序 semantic pass 表
SEMANTIC_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_semantic_schema],
    "bplisp": [],
    "animlang": [],
}


#### 纯 semantic-schema 加载器：json.load + 形状校验 [@380kkm 2026-06-05] ####
def load_schema(path: str) -> dict:
    """形状：root 是对象；每个非 '$' 键（一个语言）映射到对象；每个 nodeType 映射到对象；
    可选的 'properties' 是对象；可选的 'pins' 是对象，其条目为带可选 bool 'required'
    的对象。形状畸形时抛 ValueError。以 '$' 开头的顶层元数据键被忽略。
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
    # _extract_file 内部会重新 parse（接受 content 而非 tree）
    rows, edges = E._extract_file(0, text, lang, parser, False, query)
    return Context(lang, text, tree, rows, edges,
                   {r["_local"]: r for r in rows}, {r["name"] for r in rows})


#### 纯预检校验器：跑结构 pass（有 schema 则加 semantic），确定性排序返回 issue [@380kkm 2026-06-05] ####
def dsl_validate(text: str, lang: str, schema: dict | None = None) -> list[Issue]:
    if lang not in STRUCTURAL_PASSES:
        return [Issue("error", "UNKNOWN_LANG", f"no validator for language {lang!r}", 1, 0)]
    ctx = _build_context(text, lang)
    issues = [i for p in STRUCTURAL_PASSES[lang] for i in p(ctx)]
    if schema is not None:
        # 把字典挂到 ctx 上供 semantic pass 读取
        ctx.schema = schema
        issues += [i for p in SEMANTIC_PASSES.get(lang, []) for i in p(ctx)]
    # 合并后唯一的最终排序
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
