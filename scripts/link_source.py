# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread link-source —— 资产↔源码跨层链接器（纯函数、只读）。

给定一个 DSL **资产**存储库（如 matlang 材质）、一个**代码**存储库（引擎 C++）、
以及类型字典 **schema**（nodeType -> classPath），把每个 DSL 节点解析到实现它的
C++ 类，并报告 ``node -> {源码类符号, file:line, 置信度}``。它把资产图桥接到已索引
的源码，使读者能从一个材质节点跳到其 ``UMaterialExpression`` C++ 类。

机制（复用，而非重造）：
* 两个输入存储库都通过 ``manyscan.lib.stores.Store`` 打开，以只读方式连接 sqlite
  （``file:...?mode=ro``）。任何写入都会抛错——纯净性得到保证；两个输入存储库都
  绝不被改动。
* 每个 DSL 节点：``node_type``（取自 ``symbols.attrs.node_type``，``material`` 根则
  取行 KIND）-> ``schema[lang][node_type].classPath`` -> ReflectedName（最后一个
  ``.`` 之后的部分）-> 在代码存储库中、跨固定前缀集 ``["", "U", "A", "F"]``（UE 约定）
  对 class/struct 符号做按名查找。
* 置信度模型**镜像** ``manyscan.lib.boundary.resolve_target``：0 个候选 ->
  ``unresolved``；恰好 1 个 -> ``unique``；N>1 个 -> ``ambiguous(N)``（列出**所有**
  候选，**绝不**静默挑选）。第 4 个**仅供报告**的桶 ``no-classPath`` 覆盖 schema 中
  缺失的 nodeType。

enrich_treesitter.py / dsl_validate.py 中**没有任何东西**被改动；``load_schema`` 在
本地重新实现（仅用 stdlib），使得 import 本模块绝不会拖入 tree-sitter。输出是确定性
的：DSL 行按固定排序、前缀变体按固定顺序、候选按 ``(path, id)`` 排序——两次运行字节
一致。

CLI::

    uv run --python 3.12 scripts/link_source.py \
        --dsl-store <asset store> --code-store <c++ store> \
        --schema scripts/schemas/matlang.sample.json [--lang matlang] [--json]

成功退出 0；存储库路径错误或 schema 畸形时退出 2。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


#### 按文件路径直接加载模块到指定私有别名（绕开 lib 名冲突）[@380kkm 2026-06-05] ####
#
# 直接以文件路径、在一个唯一的模块别名下加载 manyscan 的只读存储库层
# （scripts/manyscan/lib/stores.py），而**不**经由 ``from lib import stores``。
#
# 为何不用裸 ``lib`` 名：manyread 自己的包是 scripts/lib，且
# dsl_validate -> enrich_treesitter 会做 ``from lib import config``，把
# ``sys.modules['lib']`` 绑定到 scripts/lib（其下没有 ``stores`` 子模块）。当本模块
# 与该 import 路径共享同一个 Python 会话时（如合并的核心测试套件），这里的
# ``from lib import stores`` 会落到已缓存的 scripts/lib 包上并抛 ImportError。
# stores.py 自身**不**做 ``from lib import ...``（它按文件路径、以别名加载 manyread
# 的 lib），故按路径在私有别名下直接加载它能彻底回避冲突，并保持本模块纯净
# （仅 stdlib + stores 的只读 sqlite）。
def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    #### exec 前先注册（镜像 stores._load_module）
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
#### /按路径加载模块 ####


_MANYSCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manyscan")
stores = _load_module("manyscan_stores", os.path.join(_MANYSCAN_DIR, "lib", "stores.py"))

#### UE 类以 kind='class' 产出；F 前缀的反射类型是 struct
CLASS_KINDS = {"class", "struct"}
#### ambiguous 解析至多列出这么多候选位置（确定性样本）；真实总数另作 "ambiguity" 报告
_CAND_CAP = 12
#### UE 反射名映射到带前缀的 C++ 类（U/A/F）；按优先级试这些层级，并在第一个非空处停止。
#### 裸名仅作最后手段（真正无前缀的类型）——急于试它会在常见名上爆炸：对真实引擎索引，裸 "Material" 匹配了 254 个无关符号。
PREFIXES = ("U", "A", "F", "")


#### 本地、仅 stdlib 的 schema 加载器（dsl_validate.load_schema 的副本）[@380kkm 2026-06-05] ####
def load_schema(path: str) -> dict:
    """纯函数 schema 加载器：json.load + 形状校验。形状畸形时抛 ValueError，使 CLI
    报出干净的错误。以 '$' 开头的顶层元数据键被允许并忽略。

    在此重新实现（而非 import dsl_validate.load_schema），是因为 dsl_validate 在模块
    加载时 import enrich_treesitter，后者又 import tree-sitter——这是一个只读链接器
    绝不应要求的硬依赖。

    形状：根是对象；每个非 '$' 键（一个 lang）映射到 nodeType -> spec 对象；可选的
    'properties'/'pins' 为对象。
    """
    with open(path, encoding="utf-8") as fh:
        #### JSONDecodeError 会上抛给 CLI
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("schema root must be a JSON object (lang -> nodeType -> spec)")
    for lang, types in data.items():
        if lang.startswith("$"):
            #### 元数据键 -> 忽略
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
    return data
#### /schema 加载器 ####


#### 从 classPath 提取 ReflectedName（末尾 '.' 之后的部分）[@380kkm 2026-06-05] ####
def reflected_name(class_path: str) -> str | None:
    """``/Script/Engine.MaterialExpressionMultiply`` -> ``MaterialExpressionMultiply``。"""
    if not class_path or "." not in class_path:
        return None
    return class_path.rsplit(".", 1)[-1]
#### /提取 ReflectedName ####


#### 归一化存储的文件路径用于输出（反斜杠 -> '/'）[@380kkm 2026-06-05] ####
def _norm(path: str) -> str:
    """归一化存储的文件路径用于输出（反斜杠 -> '/'），使报告跨 OS 一致。
    files.path 在索引时按 OS 分隔符存储。"""
    return (path or "").replace("\\", "/")
#### /归一化路径 ####


#### 把 ReflectedName 解析到代码库的 class/struct 候选 [@380kkm 2026-06-05] ####
def resolve_class(code: "stores.Store", reflected: str, code_lang: str = "cpp") -> dict:
    """把一个 ReflectedName 解析到代码存储库的 class/struct 候选。

    前缀优先：一个反射名恰好映射到唯一 C++ 前缀（UMaterial 是 U，AActor 是 A，
    FLinearColor 是 F），故按 U/A/F/"" 顺序逐层尝试，返回**第一个**产出候选的层级。
    裸名仅作最后手段——急于把它并入会在常见名上爆炸（对真实引擎索引，裸 "Material"
    匹配了 254 个无关符号）。在胜出层级内，**前向声明**（声明大小的字节跨度）被丢弃
    以偏向**定义**，随后候选按 ``(path, id)`` 排序——确定性。

    候选被限制在 ``code_lang``（默认 'cpp'）。C++ 类是唯一有意义的解析目标；没有此
    过滤时，代码库中来自**不同** lang、恰好共享某 ReflectedName 的 class/struct 符号
    （如两库被合并时的某个 DSL 'material' 根）会被算作候选，并可能把 'unique' 解析翻
    成 'ambiguous'。``symbols_named`` 没有 lang 过滤（它是共享的 boundary 基础设施），
    故 lang 切割在此施加。传 ``code_lang=None`` 可跨**所有** lang 解析。

    返回 {"confidence": "unique"|"ambiguous"|"unresolved", "cands": [Row, ...]}。
    """
    placeholders = ",".join("?" * len(CLASS_KINDS))
    for prefix in PREFIXES:
        #### 优先级顺序；在第一个非空层级停止
        name = prefix + reflected
        rows = code.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, "
            "       s.start_line, s.start_byte, s.end_byte "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE s.name = ? AND s.kind IN ({placeholders}) "
            "ORDER BY f.path, s.id LIMIT 500",
            (name, *sorted(CLASS_KINDS)),
        ).fetchall()
        cands = [r for r in rows if code_lang is None or r["lang"] == code_lang]
        if not cands:
            continue

        #### 偏向定义：丢弃前向声明 [@380kkm 2026-06-05] ####
        # UE 头文件在数百个文件里前向声明一个类（`class UMaterial;` -> 一个约
        # len("class <Name>") 字节、无函数体的符号）；只有真正的定义有函数体（UE 反射
        # 类总是很大——UCLASS + GENERATED_BODY + 成员）。一个跨度仅略超其声明的符号
        # 即前向声明。仅保留定义；若**只**索引到前向声明（定义被漏掉，如这里 UMaterial
        # 这种被宏搞混的名字），则全部保留（诚实——仍被呈现，标 ambiguous）。
        defs = [r for r in cands if (r["end_byte"] - r["start_byte"]) > len(name) + 16]
        chosen = sorted(defs or cands, key=lambda r: (_norm(r["path"]), r["id"]))
        conf = "unique" if len(chosen) == 1 else "ambiguous"
        #### ambiguous 绝不挑选其一
        return {"confidence": conf, "cands": chosen}
        #### /偏向定义 ####
    return {"confidence": "unresolved", "cands": []}
#### /解析 class ####


#### 逐个产出 (row, lookup_key) 覆盖每个 DSL 节点/材质符号，已排序 [@380kkm 2026-06-05] ####
def dsl_nodes(dsl: "stores.Store", lang: str):
    """为每个 DSL 节点/材质符号产出 ``(row, lookup_key)``，已排序。

    lookup_key = 存在时取 attrs.node_type，否则在 kind=='material' 时取行 KIND
    （material 根的 attrs=={}，但 schema 携带一个 'material' 条目）。
    kind=='outputs'（纯容器）由 WHERE 子句排除。
    """
    rows = dsl.conn.execute(
        "SELECT s.id, s.name, s.kind, f.path, s.start_line, s.attrs "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.lang = ? AND s.kind IN ('node', 'material') "
        "ORDER BY f.path, s.start_line, s.start_byte, s.id",
        (lang,),
    ).fetchall()
    for r in rows:
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        key = attrs.get("node_type") or (r["kind"] if r["kind"] == "material" else None)
        yield r, key
#### /产出 DSL 节点 ####


#### 构建确定性的链接报告（纯函数、只读）[@380kkm 2026-06-05] ####
def link(dsl_store: str, code_store: str, schema_path: str, lang: str = "matlang",
         code_lang: str | None = "cpp") -> dict:
    """构建确定性的链接报告（纯函数、只读）。可能抛 ValueError / FileNotFoundError
    （schema/存储库错误），由 CLI 映射为退出码 2。

    ``code_lang`` 把代码库候选限制在该 lang（默认 'cpp'）；传 None 可跨代码库中每个
    lang 解析 class/struct 符号。
    """
    schema = load_schema(schema_path)
    types = schema.get(lang, {})
    nodes: list[dict] = []

    dsl_info = stores.resolve(store=dsl_store)
    code_info = stores.resolve(store=code_store)
    with stores.Store(dsl_info.db_path) as dsl, stores.Store(code_info.db_path) as code:
        for r, key in dsl_nodes(dsl, lang):
            class_path = (types.get(key) or {}).get("classPath") if key else None
            entry: dict = {
                "node_id": r["id"],
                "node_name": r["name"],
                "node_type": key,
                "node_loc": f'{_norm(r["path"])}:{r["start_line"]}',
                "classPath": class_path,
                "status": "no-classPath",
                "resolved": None,
            }
            if class_path:
                rn = reflected_name(class_path)
                res = (resolve_class(code, rn, code_lang) if rn
                       else {"confidence": "unresolved", "cands": []})
                cands = res["cands"]
                if res["confidence"] == "unique":
                    c = cands[0]
                    entry["status"] = "resolved-unique"
                    entry["resolved"] = {
                        "symbol_name": c["name"],
                        "loc": f'{_norm(c["path"])}:{c["start_line"]}',
                        "confidence": "unique",
                    }
                elif res["confidence"] == "ambiguous":
                    entry["status"] = "resolved-ambiguous"
                    entry["resolved"] = {
                        "confidence": "ambiguous",
                        "ambiguity": len(cands),
                        #### 确定性样本（排序列表的前 _CAND_CAP 个）；"ambiguity" 携带真实总数
                        "candidates": [
                            f'{_norm(c["path"])}:{c["start_line"]}'
                            for c in cands[:_CAND_CAP]
                        ],
                    }
                else:
                    entry["status"] = "unresolved"
            nodes.append(entry)

    summary = {
        "resolved_unique": 0,
        "resolved_ambiguous": 0,
        "unresolved": 0,
        "no_class_path": 0,
        "total": len(nodes),
    }
    bucket = {
        "resolved-unique": "resolved_unique",
        "resolved-ambiguous": "resolved_ambiguous",
        "unresolved": "unresolved",
        "no-classPath": "no_class_path",
    }
    for e in nodes:
        summary[bucket[e["status"]]] += 1
    return {
        "lang": lang,
        #### 来源路径被归一化（反斜杠 -> '/'），使整份报告——不仅是承重的解析位置——跨 OS 字节一致
        "dsl_store": _norm(str(dsl_info.db_path)),
        "code_store": _norm(str(code_info.db_path)),
        "schema": _norm(str(schema_path)),
        "nodes": nodes,
        "summary": summary,
    }
#### /构建链接报告 ####


#### 把链接报告渲染为文本 [@380kkm 2026-06-05] ####
def render_text(rep: dict) -> str:
    lines: list[str] = []
    lines.append(f'# link-source  lang={rep["lang"]}')
    lines.append(f'#   dsl  : {rep["dsl_store"]}')
    lines.append(f'#   code : {rep["code_store"]}')
    lines.append(f'#   schema: {rep["schema"]}')
    lines.append("")
    for e in rep["nodes"]:
        name = e["node_name"] or "-"
        nt = e["node_type"] or "-"
        line = f'{name:<16} {nt:<20} {e["status"]:<20} {e["classPath"] or ""}'
        res = e["resolved"]
        if e["status"] == "resolved-unique" and res:
            line += f'  -> {res["symbol_name"]} @ {res["loc"]}'
        elif e["status"] == "resolved-ambiguous" and res:
            line += f'  -> AMBIGUOUS({res["ambiguity"]}): ' + ", ".join(res["candidates"])
        lines.append(line)
    s = rep["summary"]
    lines.append("")
    lines.append(
        f'resolved-unique={s["resolved_unique"]} '
        f'resolved-ambiguous={s["resolved_ambiguous"]} '
        f'unresolved={s["unresolved"]} '
        f'no-classPath={s["no_class_path"]} '
        f'total={s["total"]}'
    )
    return "\n".join(lines)
#### /渲染文本 ####


#### CLI 入口：解析参数、运行链接、按 --json 或文本输出 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="link_source.py",
        description="ASSET->SOURCE cross-layer linker: resolve DSL nodes to C++ classes.",
    )
    ap.add_argument("--dsl-store", required=True, help="DSL asset store dir / source.db / hub alias")
    ap.add_argument("--code-store", required=True, help="C++ code store dir / source.db / hub alias")
    ap.add_argument("--schema", required=True, help="type-dictionary JSON (nodeType -> classPath)")
    ap.add_argument("--lang", default="matlang", help="DSL lang to link (default: matlang)")
    ap.add_argument(
        "--code-lang", default="cpp",
        help="restrict code-store candidates to this lang (default: cpp; "
        "pass 'any' to resolve across all langs)",
    )
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit machine JSON")
    args = ap.parse_args(argv)

    code_lang = None if args.code_lang == "any" else args.code_lang
    try:
        rep = link(args.dsl_store, args.code_store, args.schema, args.lang, code_lang)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        #### 归一化诊断信息中嵌入的任何路径，保持跨 OS 一致
        print(f"error: {_norm(str(exc))}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))
    return 0
#### /CLI 入口 ####


if __name__ == "__main__":
    raise SystemExit(main())
