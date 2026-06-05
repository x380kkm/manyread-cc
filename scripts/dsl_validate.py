# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
# audience: internal
# dsl_validate
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

import os
import sys
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入 sys.path
sys.path.insert(0, _HERE)
# 复用 _extract_file / _load_* / Query / Parser
import enrich_treesitter as E  # noqa: E402
from lib import config  # noqa: E402


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


#### 持有按语言注册的有序结构 pass 表（通用核心为空；由扩展经 register_passes 填充） [@380kkm 2026-06-05] ####
STRUCTURAL_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {}

#### 持有按语言注册的有序 semantic pass 表（通用核心为空；由扩展经 register_passes 填充） [@380kkm 2026-06-05] ####
SEMANTIC_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {}


#### 扩展注册某语言的结构/语义 pass（就地写入两张全局表） [@380kkm 2026-06-05] ####
def register_passes(lang: str, structural=None, semantic=None) -> None:
    STRUCTURAL_PASSES[lang] = list(structural or [])
    SEMANTIC_PASSES[lang] = list(semantic or [])


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


#### 为单文件 CLI 触发扩展发现：本 CLI 即 UE DSL 校验器，始终启用 'ue'（再并上项目扩展） [@380kkm 2026-06-05] ####
def _discover_for_cli(file_path: str) -> None:
    """本 CLI 的唯一职责就是校验 UE 资产 DSL 文件，故始终启用 'ue'，使移出到扩展的 pass
    与 .scm 可用；若文件所在目录能解析到项目，则把该项目额外启用的扩展一并并入（绝不因项目
    的空 extensions 而停用本 CLI 赖以工作的 ue 扩展）。
    """
    # 延迟 import 以避免 extensions <-> dsl_validate 的环
    from extensions import run_discovery
    run_discovery(["ue"])
    start = os.path.dirname(os.path.abspath(file_path))
    try:
        cfg = config.resolve_project(root=start, store=None)
    except SystemError:
        return
    extra = [e for e in config.active_extensions(cfg) if e != "ue"]
    if extra:
        run_discovery(extra)


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

    # 读取 LANG_FOR_EXT / pass 注册表之前先跑扩展发现
    _discover_for_cli(a.file)

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
    # 经规范模块名调用 main，使扩展发现写入的注册表与此处读取的是同一对象
    # （否则 __main__ 与被 import 的 dsl_validate 是两份模块，注册表彼此割裂）。
    import dsl_validate as _canonical

    raise SystemExit(_canonical.main())
