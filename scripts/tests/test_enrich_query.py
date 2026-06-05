"""enrich 中声明式依赖边查询层的回归测试。

须在 scripts/ 目录下、带 tree-sitter 依赖运行，例如：
    cd scripts && uv run --python 3.12 --with pytest --with "tree-sitter>=0.23" \
        --with tree-sitter-language-pack -m pytest tests/test_enrich_query.py -q
（它不放在 scripts/manyscan/tests 里，是因为 enrich 会 import manyread 核心的 `lib`
包，而那会在该套件的 sys.path 中遮蔽 manyscan 自己的 `lib`。）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))  # scripts/
try:
    import enrich_treesitter as E
    from tree_sitter import Parser, Query
    from tree_sitter_language_pack import get_language
    _HAVE = True
except Exception:  # noqa: BLE001 - tree-sitter 未安装时干净跳过
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")


#### 校验 _simplify_dep 归并联合类型/泛型/限定符到单一裸名 [@380kkm 2026-06-05] ####
def test_simplify_dep():
    # 联合类型 -> 取首个并剥离泛型
    assert E._simplify_dep("list[str] | None") == "list"
    # 限定符 -> 取末段
    assert E._simplify_dep("module.Foo") == "Foo"
    assert E._simplify_dep("Outer::Inner") == "Inner"
    assert E._simplify_dep("TArray<FString>") == "TArray"
    assert E._simplify_dep("Foo | Bar") == "Foo"


#### 校验内建 python 查询预设能加载并对 grammar 编译通过 [@380kkm 2026-06-05] ####
def test_builtin_python_query_loads_and_compiles():
    specs = E._load_query_specs(None)
    assert "python" in specs and "@dep.calls" in specs["python"]
    # 对 grammar 编译
    Query(get_language("python"), specs["python"])


#### 校验项目级 .scm 覆盖优先于内建预设 [@380kkm 2026-06-05] ####
def test_project_override_wins(tmp_path):
    d = tmp_path / ".manyread" / "queries"
    d.mkdir(parents=True)
    (d / "python.scm").write_text("(call function: (identifier) @dep.calls)\n", encoding="utf-8")
    specs = E._load_query_specs(tmp_path)
    assert specs["python"].strip() == "(call function: (identifier) @dep.calls)"


#### 用内建 python 预设跑 _extract_file，返回 (rows, edges) 的夹具 [@380kkm 2026-06-05] ####
def _edges(src: str):
    lang = get_language("python")
    q = Query(lang, E._load_query_specs(None)["python"])
    return E._extract_file(1, src, "python", Parser(lang), False, q)


#### python 依赖边端到端：calls/uses_type/imports 归属于其封闭符号 [@380kkm 2026-06-05] ####
def test_python_edges_end_to_end():
    # 注意：每条 @dep 边都归属于其封闭（ENCLOSING）符号；顶层语句没有封闭符号，故模块作用域
    # 的 import/call 被丢弃（文件级节点是未来工作）。因此这里的 import 写在方法内，使其确有归属。
    src = ("class A(Base):\n"
           "    def m(self, x: Widget) -> Out:\n"
           "        from pkg.mod import thing\n"
           "        return helper(x)\n")
    _rows, edges = _edges(src)
    pairs = {(e["relation"], e["dst_name"]) for e in edges}
    assert ("calls", "helper") in pairs
    assert ("uses_type", "Widget") in pairs and ("uses_type", "Out") in pairs
    # pkg.mod -> 取末段
    assert ("imports", "mod") in pairs
    # 继承由遍历器（WALKER）发出，而非查询 -> 恰好一条 extends，不重复
    assert sum(1 for e in edges if e["relation"] == "extends") == 1


#### 已知局限：模块作用域的边因无封闭符号而被丢弃 [@380kkm 2026-06-05] ####
def test_module_scope_edges_dropped():
    # 记录已知局限：模块级 import 没有封闭符号。
    _rows, edges = _edges("from pkg.mod import thing\nx = helper()\n")
    assert not edges


#### 查询出的边对同一输入是确定性的 [@380kkm 2026-06-05] ####
def test_query_edges_deterministic():
    src = "def f(a: T):\n    return g(a)\n"
    _r1, e1 = _edges(src)
    _r2, e2 = _edges(src)
    assert e1 == e2


# ===========================================================================
# UE 资产 DSL（无遍历器、纯查询驱动）的符号 + 连线提取。
# 这些用例走 _extract_file 的 `lang not in HAS_WALKER` 分支：符号来自 @def 捕获
# （经 _query_symbols），连线来自 @dep 捕获。
# ===========================================================================
#### 用内建 .scm 查询为无遍历器 DSL 跑 _extract_file 的夹具 [@380kkm 2026-06-05] ####
def _dsl_extract(src: str, lang: str):
    """为无遍历器的 DSL 以其内建 .scm 查询运行 _extract_file。"""
    # 三种 DSL 都共用 scheme grammar
    L = get_language("scheme")
    specs = E._load_query_specs(None)
    q = Query(L, specs[lang])
    return E._extract_file(1, src, lang, Parser(L), False, q)


# --- matlang 样例文本（对应 DSL/Examples/*.matlang） -------------------------
_SIMPLE_PBR = (
    '(material "M_SimplePBR"\n'
    "  :domain surface\n"
    "  (expressions\n"
    "    (texture-sample $tex1 :uv (connect $uv1))\n"
    "    (texture-sample $tex2 :uv (connect $uv1))\n"
    "    (texture-coordinate $uv1 :coordinate-index 0)\n"
    '    (vector-parameter $vparam1 :name "TintColor")\n'
    "    (multiply $mul1 :a (connect $tex1 0) :b (connect $vparam1 0))\n"
    '    (scalar-parameter $sparam1 :name "Roughness")\n'
    "    (constant $const1 :value 0.0))\n"
    "  (outputs\n"
    "    :base-color (connect $mul1 0)\n"
    "    :normal (connect $tex2 0)\n"
    "    :metallic (connect $const1 0)\n"
    "    :roughness (connect $sparam1 0)))\n"
)

_EMISSIVE_RIM = (
    '(material "M_EmissiveRim"\n'
    "  :domain surface\n"
    "  (expressions\n"
    "    (constant3-vector $vec1 :value (0.05 0.05 0.1))\n"
    '    (vector-parameter $vparam1 :name "EmissiveColor")\n'
    '    (scalar-parameter $sparam1 :name "EmissiveIntensity")\n'
    "    (fresnel $fresnel1 :exponent 3.0)\n"
    "    (multiply $mul1 :a (connect $fresnel1 0) :b (connect $vparam1 0))\n"
    "    (multiply $mul2 :a (connect $mul1 0) :b (connect $sparam1 0))\n"
    "    (constant $const1 :value 0.3)\n"
    "    (constant $const2 :value 0.9))\n"
    "  (outputs\n"
    "    :base-color (connect $vec1 0)\n"
    "    :emissive-color (connect $mul2 0)\n"
    "    :metallic (connect $const2 0)\n"
    "    :roughness (connect $const1 0)))\n"
)


#### matlang 符号：material + 各 node + outputs 的种类/类型/父子关系 [@380kkm 2026-06-05] ####
def test_matlang_symbols():
    rows, _edges = _dsl_extract(_SIMPLE_PBR, "matlang")
    by_name = {r["name"]: r for r in rows}
    # 1 个 material + 7 个 node + 1 个 outputs = 9 个符号
    assert len(rows) == 9
    mat = by_name["M_SimplePBR"]
    assert mat["kind"] == "material" and mat["lang"] == "matlang"
    node_names = {r["name"] for r in rows if r["kind"] == "node"}
    assert node_names == {"$tex1", "$tex2", "$uv1", "$vparam1", "$mul1", "$sparam1", "$const1"}
    node_types = {r["name"]: r["attrs"].get("node_type") for r in rows if r["kind"] == "node"}
    assert node_types["$mul1"] == "multiply"
    assert node_types["$tex1"] == "texture-sample"
    assert node_types["$uv1"] == "texture-coordinate"
    assert node_types["$vparam1"] == "vector-parameter"
    assert node_types["$sparam1"] == "scalar-parameter"
    assert node_types["$const1"] == "constant"
    assert "outputs" in by_name and by_name["outputs"]["kind"] == "outputs"
    # 每个 node 与 outputs 块的父节点都是 material 符号
    mat_local = mat["_local"]
    for r in rows:
        if r["kind"] in ("node", "outputs"):
            assert r["parent_local"] == mat_local
    # material/outputs 不携带 node_type 属性（head==name 或 kind!=node）
    assert mat["attrs"] == {}
    assert by_name["outputs"]["attrs"] == {}


#### 取 matlang `uses_type` 连线边的 (src_name, dst_name) 集合 [@380kkm 2026-06-05] ####
def _wire_pairs(rows, edges):
    """matlang `uses_type` 连线边的 (src_name, dst_name) 集合。"""
    local_to_name = {r["_local"]: r["name"] for r in rows}
    return {(local_to_name[e["src_local"]], e["dst_name"])
            for e in edges if e["relation"] == "uses_type"}


#### M_SimplePBR 的连线与 contains 边精确匹配预期 [@380kkm 2026-06-05] ####
def test_matlang_wires_simple_pbr():
    rows, edges = _dsl_extract(_SIMPLE_PBR, "matlang")
    assert _wire_pairs(rows, edges) == {
        ("$mul1", "$tex1"), ("$mul1", "$vparam1"),
        ("$tex1", "$uv1"), ("$tex2", "$uv1"),
        ("outputs", "$mul1"), ("outputs", "$tex2"),
        ("outputs", "$const1"), ("outputs", "$sparam1"),
    }
    # contains：material -> 每个 node + outputs
    local_to_name = {r["_local"]: r["name"] for r in rows}
    contains = {(local_to_name[e["src_local"]], e["dst_name"])
                for e in edges if e["relation"] == "contains"}
    assert contains == {
        ("M_SimplePBR", n) for n in
        ("$tex1", "$tex2", "$uv1", "$vparam1", "$mul1", "$sparam1", "$const1", "outputs")
    }


#### M_EmissiveRim 的 node 集合与连线精确匹配预期 [@380kkm 2026-06-05] ####
def test_matlang_wires_emissive_rim():
    rows, edges = _dsl_extract(_EMISSIVE_RIM, "matlang")
    assert {r["name"] for r in rows if r["kind"] == "node"} == {
        "$vec1", "$vparam1", "$sparam1", "$fresnel1", "$mul1", "$mul2", "$const1", "$const2"
    }
    assert _wire_pairs(rows, edges) == {
        ("$mul1", "$fresnel1"), ("$mul1", "$vparam1"),
        ("$mul2", "$mul1"), ("$mul2", "$sparam1"),
        ("outputs", "$vec1"), ("outputs", "$mul2"),
        ("outputs", "$const2"), ("outputs", "$const1"),
    }


#### matlang 提取对同一输入是确定性的 [@380kkm 2026-06-05] ####
def test_matlang_deterministic():
    r1, e1 = _dsl_extract(_SIMPLE_PBR, "matlang")
    r2, e2 = _dsl_extract(_SIMPLE_PBR, "matlang")
    assert r1 == r2 and e1 == e2


# --- bplisp -----------------------------------------------------------------
_VILLAGER = (
    "(function\n"
    "  None\n"
    '  :event-id "8abce957"\n'
    "  :param (Selected Actor)\n"
    '  (PrintString :instring "Villager Select called!" :id "5f6936c3")\n'
    '  (set Selected "K2Node_FunctionEntry" :id "226de0c6")\n'
    "  (let returnvalue\n"
    '    (SpawnSystemAttached :location "0, 0, 0" :id "60944b57"))\n'
    '  (set NS_Path "...circular..." :id "a1f38460")\n'
    "  (let returnvalue\n"
    '    (K2_SetTimer :functionname "Update Path" :id "c1d52411")))\n'
)


#### bplisp 符号与 binds：graph/node/call 分类、binds 目标未解析、嵌套归属 [@380kkm 2026-06-05] ####
def test_bplisp_symbols_and_binds():
    rows, edges = _dsl_extract(_VILLAGER, "bplisp")
    graphs = [r for r in rows if r["kind"] == "graph"]
    assert len(graphs) == 1 and graphs[0]["name"] == "function"
    g_local = graphs[0]["_local"]
    nodes = sorted(r["name"] for r in rows if r["kind"] == "node")
    assert nodes == ["let", "let", "set", "set"]
    calls = sorted(r["name"] for r in rows if r["kind"] == "call")
    # 仅真实的 UFunction 调用 —— `:param (Selected Actor)` 里的类型不再被误捕为 call
    # （call 规则现要求第二个子节点是 :pin）。
    assert calls == ["K2_SetTimer", "PrintString", "SpawnSystemAttached"]
    # 回归保护：参数类型不是 call
    assert "Selected" not in calls
    # binds 的目标名（let/set 绑定的变量）。returnvalue/NS_Path 在文件内没有同名符号，故保持
    # 未解析（提取时 dst_local 为 None）；只有 matlang 的 $id 连线会在文件内解析。
    binds = sorted(e["dst_name"] for e in edges if e["relation"] == "binds")
    assert binds == ["NS_Path", "Selected", "returnvalue", "returnvalue"]
    assert all(e["dst_local"] is None for e in edges if e["relation"] == "binds")
    # 顶层语句的 node/call 由 function graph 容纳；PrintString/set/let 直接挂在其下，而
    # SpawnSystemAttached/K2_SetTimer 在其各自的 `let`（最内层封闭 @def）下再深一层。
    contained_under_graph = {e["dst_name"] for e in edges
                             if e["relation"] == "contains" and e["src_local"] == g_local}
    assert {"PrintString", "set", "let"} <= contained_under_graph
    # `let` 下的纯 call 节点归属于该 let，而非 graph。
    let_locals = {r["_local"] for r in rows if r["name"] == "let"}
    nested_calls = {e["dst_name"] for e in edges
                    if e["relation"] == "contains" and e["src_local"] in let_locals}
    assert nested_calls == {"SpawnSystemAttached", "K2_SetTimer"}


# --- animlang ---------------------------------------------------------------
_STATE_MACHINE = (
    '(anim-blueprint "SimpleStateMachine"\n'
    "  :variables [(float :speed 0.0 :range [0.0 600.0])]\n"
    "  :anim-graph\n"
    "    (state-machine :locomotion :initial :idle\n"
    "      :states\n"
    '        [(state :idle (sequence-player "Idle_Rifle" :loop true))\n'
    '         (state :walk (sequence-player "Walk_Fwd" :loop true))\n'
    '         (state :run (sequence-player "Run_Fwd" :loop true))]\n'
    "      :transitions\n"
    "        [(transition :idle :walk :condition (and (> :speed 10.0) (< :speed 300.0)) :duration 0.2)\n"
    "         (transition :walk :idle :condition (< :speed 10.0) :duration 0.25)\n"
    "         (transition :walk :run :condition (> :speed 300.0) :duration 0.15)\n"
    "         (transition :run :walk :condition (< :speed 300.0) :duration 0.2)]))\n"
)


#### animlang 真实形态：节点计数、禁止类型标签成符号、pose 树 contains [@380kkm 2026-06-05] ####
def test_animlang_symbols_real_form():
    rows, edges = _dsl_extract(_STATE_MACHINE, "animlang")
    node_names = [r["name"] for r in rows if r["kind"] == "node"]
    assert len(node_names) == 12
    counts = {n: node_names.count(n) for n in set(node_names)}
    assert counts["anim-blueprint"] == 1
    assert counts["state-machine"] == 1
    assert counts["state"] == 3
    assert counts["sequence-player"] == 3
    assert counts["transition"] == 4
    # 变量类型标签、运算符、结构性首词都绝不应成为符号
    forbidden = {"float", "bool", ">", "<", "and", "or", "not", "if", "->", "define", "ref"}
    assert not (forbidden & set(node_names))
    # 由 `contains` 还原 pose 树：anim-blueprint -> state-machine -> state -> player
    local_to_name = {r["_local"]: r["name"] for r in rows}
    contains = {(local_to_name[e["src_local"]], e["dst_name"])
                for e in edges if e["relation"] == "contains"}
    assert ("anim-blueprint", "state-machine") in contains
    assert ("state-machine", "state") in contains
    assert ("state", "sequence-player") in contains


#### animlang 导出器形态（define/ref）的尽力绑定与 ref 连线（合成片段） [@380kkm 2026-06-05] ####
def test_animlang_exporter_form_synthetic():
    # 导出器形态 (define ...)/(ref ...) —— 仓内样例里没有；这里仅以合成片段固定其尽力绑定
    # + ref 连线行为。真正依赖前请先对真实导出器转储重新核验。
    src = (
        '(anim-blueprint "X"\n'
        "  :anim-graph\n"
        "    (define CachedLeg (two-bone-ik :a 1))\n"
        '  (blend (ref "Get Speed") (CachedLeg)))\n'
    )
    rows, edges = _dsl_extract(src, "animlang")
    bindings = [r for r in rows if r["kind"] == "binding"]
    assert len(bindings) == 1 and bindings[0]["name"] == "CachedLeg"
    # (ref "Get Speed") -> 一条 dep.ref 边。dst_name 保留引号（复用的 _query_edges 跑的是
    # _simplify_dep 而非 _dsl_name），且保持未解析。
    refs = [e for e in edges if e["relation"] == "ref"]
    assert len(refs) == 1 and refs[0]["dst_name"] == '"Get Speed"'
    # 已记录行为：`(CachedLeg)` 复用会产生一个名为 'CachedLeg' 的多余 def.node 符号
    # （@dep.use 的后置过滤被刻意省略）。
    node_named_cachedleg = [r for r in rows if r["kind"] == "node" and r["name"] == "CachedLeg"]
    assert len(node_named_cachedleg) == 1


# ===========================================================================
# 无回归：遍历器语言（cpp/python）在 @def 新增（受 HAS_WALKER 闸门控制）后仍逐字节
# 一致。golden 取自遍历器输出。
# ===========================================================================
#### 用遍历器语言的内建预设跑 _extract_file 的夹具 [@380kkm 2026-06-05] ####
def _walker_extract(src: str, lang: str):
    L = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    q = Query(L, specs[lang]) if lang in specs else None
    return E._extract_file(1, src, lang, Parser(L), False, q)


_CPP_GOLDEN_SRC = (
    "class Foo : public Base {\n"
    "  Widget w;\n"
    "  Out compute(Arg a) { return helper(a); }\n"
    "};\n"
    "void freefn(Thing t) {}\n"
)
_CPP_GOLDEN_ROWS = [
    {"_local": 0, "file_id": 1, "name": "Foo", "kind": "class", "lang": "cpp",
     "start_line": 1, "end_line": 4, "start_byte": 0, "end_byte": 82,
     "parent_local": None, "attrs": {}, "provenance": []},
    {"_local": 1, "file_id": 1, "name": "compute", "kind": "function", "lang": "cpp",
     "start_line": 3, "end_line": 3, "start_byte": 40, "end_byte": 80,
     "parent_local": 0, "attrs": {}, "provenance": []},
    {"_local": 2, "file_id": 1, "name": "freefn", "kind": "function", "lang": "cpp",
     "start_line": 5, "end_line": 5, "start_byte": 84, "end_byte": 107,
     "parent_local": None, "attrs": {}, "provenance": []},
]
_CPP_GOLDEN_EDGES = [
    {"file_id": 1, "src_local": 0, "dst_local": 1, "dst_name": "compute", "relation": "contains"},
    {"file_id": 1, "src_local": 0, "dst_local": None, "dst_name": "Base", "relation": "extends"},
    {"file_id": 1, "src_local": 0, "dst_local": None, "dst_name": "Widget", "relation": "uses_type"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Out", "relation": "uses_type"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Arg", "relation": "uses_type"},
    {"file_id": 1, "src_local": 2, "dst_local": None, "dst_name": "Thing", "relation": "uses_type"},
]

_PY_GOLDEN_SRC = (
    "class A(Base):\n"
    "    def m(self, x: Widget) -> Out:\n"
    "        from pkg.mod import thing\n"
    "        return helper(x)\n"
)
_PY_GOLDEN_ROWS = [
    {"_local": 0, "file_id": 1, "name": "A", "kind": "class", "lang": "python",
     "start_line": 1, "end_line": 4, "start_byte": 0, "end_byte": 108,
     "parent_local": None, "attrs": {}, "provenance": []},
    {"_local": 1, "file_id": 1, "name": "m", "kind": "method", "lang": "python",
     "start_line": 2, "end_line": 4, "start_byte": 19, "end_byte": 108,
     "parent_local": 0, "attrs": {}, "provenance": []},
]
_PY_GOLDEN_EDGES = [
    {"file_id": 1, "src_local": 0, "dst_local": 1, "dst_name": "m", "relation": "contains"},
    {"file_id": 1, "src_local": 0, "dst_local": None, "dst_name": "Base", "relation": "extends"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "helper", "relation": "calls"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "mod", "relation": "imports"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Out", "relation": "uses_type"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Widget", "relation": "uses_type"},
]


#### cpp 遍历器输出与 golden 逐字节一致 [@380kkm 2026-06-05] ####
def test_cpp_walker_byte_identical():
    rows, edges = _walker_extract(_CPP_GOLDEN_SRC, "cpp")
    assert rows == _CPP_GOLDEN_ROWS
    assert edges == _CPP_GOLDEN_EDGES


#### python 遍历器输出与 golden 逐字节一致 [@380kkm 2026-06-05] ####
def test_python_walker_byte_identical():
    rows, edges = _walker_extract(_PY_GOLDEN_SRC, "python")
    assert rows == _PY_GOLDEN_ROWS
    assert edges == _PY_GOLDEN_EDGES


# ===========================================================================
# javascript / typescript / tsx / csharp 的依赖边预设。
# 符号来自这些语言（已接线的）tree-sitter 遍历器；新的
# scripts/queries/{javascript,typescript,tsx,csharp}.scm 仅追加 EDGE-only 的
# @dep.calls / @dep.imports / @dep.uses_type 捕获。与 python 用例同一套内存测床：
# 走内建预设的 _extract_file，不落 DB。
# ===========================================================================
#### 用遍历器语言的内建 .scm 预设取 (rows, edges) 的夹具 [@380kkm 2026-06-05] ####
def _lang_edges(src: str, lang: str):
    """走内建 .scm 预设、为某遍历器语言返回 (rows, edges)。"""
    L = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    assert lang in specs, f"missing built-in preset for {lang}"
    return E._extract_file(1, src, lang, Parser(L), False, Query(L, specs[lang]))


#### 取限定在 `relations` 内的 (封闭符号名, 关系, dst_name) 集合 [@380kkm 2026-06-05] ####
def _rel_pairs(rows, edges, relations):
    """限定在 `relations` 内的 {(封闭符号名, 关系, dst_name)} 集合。"""
    by_local = {r["_local"]: r["name"] for r in rows}
    return {(by_local[e["src_local"]], e["relation"], e["dst_name"])
            for e in edges if e["relation"] in relations}


# --- javascript -------------------------------------------------------------
_JS_SRC = (
    'import topdep from "./top.js";\n'           # 模块作用域：被丢弃（无封闭符号）
    "function loader() {\n"
    '    const fs = require("node:fs");\n'        # require -> calls 'require' 且 imports 'node:fs'
    "    helper(loader);\n"                       # 自由调用
    "    return obj.read(fs);\n"                  # 成员调用 -> 'read'
    "}\n"
    "class Widget extends Base {\n"               # extends 来自遍历器（WALKER），而非 .scm
    "    render() { this.draw(); helper(); }\n"
    "}\n"
)


#### javascript 依赖边端到端：require 既是 call 又是 import，继承仅一条 [@380kkm 2026-06-05] ####
def test_javascript_edges_end_to_end():
    rows, edges = _lang_edges(_JS_SRC, "javascript")
    calls_imports = _rel_pairs(rows, edges, {"calls", "imports"})
    # 自由 + 成员 + require() 调用本身都归属 loader；node:fs 是其 import。
    assert ("loader", "calls", "helper") in calls_imports
    assert ("loader", "calls", "read") in calls_imports
    # require 确是一次真实调用
    assert ("loader", "calls", "require") in calls_imports
    # ……同时又是一条 import（无冲突）
    assert ("loader", "imports", "node:fs") in calls_imports
    # 方法体内的调用归属于该方法。
    assert ("render", "calls", "draw") in calls_imports
    assert ("render", "calls", "helper") in calls_imports
    # JS 无类型：该预设完全不声明 uses_type。
    assert not any(e["relation"] == "uses_type" for e in edges)
    # 继承：恰好一条 extends，由遍历器（WALKER）发出（.scm 不重复计数）。
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    # 模块作用域的 `import ... from` 无封闭符号 -> 被丢弃。
    assert all(e["dst_name"] != "top" for e in edges if e["relation"] == "imports")


#### 文件顶部无封闭符号的 import 产生零条边（与 python 一致） [@380kkm 2026-06-05] ####
def test_javascript_module_scope_import_dropped():
    # 顶部 import 无封闭符号，产生零条边（与 python 相同）。
    _rows, edges = _lang_edges('import x from "pkg";\n', "javascript")
    assert not edges


# --- typescript / tsx -------------------------------------------------------
_TS_SRC = (
    "class Circle extends Base implements Shape {\n"   # extends/implements -> 遍历器
    "    area(): Box {\n"                               # 返回类型 -> uses_type Box
    '        const legacy = require("legacy");\n'       # require -> imports 'legacy'，不是 call
    "        return helper(this.svc.doThing());\n"      # calls helper + doThing（仅末段属性）
    "    }\n"
    "}\n"
    "function compute(a: Vec3): Result {\n"             # 参数 Vec3 + 返回 Result -> uses_type
    "    const col: Color = make(a);\n"                 # 变量类型 Color -> uses_type；make -> calls
    "    return new Widget();\n"                        # new T -> uses_type Widget
    "}\n"
)
_TS_EXPECTED_CALLS_IMPORTS = {
    ("area", "calls", "helper"),
    ("area", "calls", "doThing"),
    ("area", "imports", "legacy"),
    ("compute", "calls", "make"),
}
_TS_EXPECTED_TYPES = {
    ("area", "uses_type", "Box"),
    ("compute", "uses_type", "Vec3"),
    ("compute", "uses_type", "Result"),
    ("compute", "uses_type", "Color"),
    ("compute", "uses_type", "Widget"),
}


#### typescript/tsx 依赖边端到端：.ts 与 .tsx 产出相同边，require 算 import [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("lang", ["typescript", "tsx"])
def test_typescript_edges_end_to_end(lang):
    # tsx grammar 与 typescript 共用节点/字段名，tsx.scm 预设是其镜像，故 .ts 与 .tsx 对本
    # 片段必产出相同的依赖边。
    rows, edges = _lang_edges(_TS_SRC, lang)
    assert _TS_EXPECTED_CALLS_IMPORTS <= _rel_pairs(rows, edges, {"calls", "imports"})
    assert _TS_EXPECTED_TYPES <= _rel_pairs(rows, edges, {"uses_type"})
    # require() 被捕获为 import 而非 call（#not-eq? 断言把它排除在 call 之外）。
    assert ("area", "calls", "require") not in _rel_pairs(rows, edges, {"calls"})
    # extends + implements 来自遍历器，各恰好一次（.scm 不重复计数）。
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    assert sum(1 for e in edges if e["relation"] == "implements") == 1


#### #not-eq? 断言区分 require：只有真 require 才算 import [@380kkm 2026-06-05] ####
def test_typescript_predicate_distinguishes_require():
    # 非 `require` 的单字符串调用是普通 call，不是 import。
    src = "function f() {\n  const a = notrequire('x');\n  const b = require('mod');\n}\n"
    rows, edges = _lang_edges(src, "typescript")
    pairs = _rel_pairs(rows, edges, {"calls", "imports"})
    assert ("f", "calls", "notrequire") in pairs
    assert ("f", "imports", "mod") in pairs
    # require 被排除在 call 之外
    assert ("f", "calls", "require") not in pairs
    # notrequire 不是 import
    assert ("f", "imports", "x") not in pairs


# --- csharp -----------------------------------------------------------------
_CS_SRC = (
    "using System;\n"                                   # 模块作用域 using -> 丢弃（无符号）
    "using Alias = Some.Long.Name;\n"                   # 别名 'Alias' 绝不能成为 import
    "namespace MyApp {\n"
    "  public class Widget : BaseWidget, IDisposable {\n"   # base_list -> 遍历器 extends/implements
    "    private Helper _helper;\n"                      # 字段类型 -> uses_type（封闭者：class）
    "    public Result DoWork(Config cfg) {\n"           # 参数 + 返回类型 -> uses_type
    "      var sb = new StringBuilder();\n"              # 对象创建类型 -> uses_type
    '      Console.WriteLine("hi");\n'                   # 成员调用 -> WriteLine
    "      _helper.Process(cfg);\n"                      # 成员调用 -> Process
    "      return new Result(Compute());\n"              # 自由调用 Compute + new Result
    "    }\n"
    "    private int Compute() => 2;\n"
    "  }\n"
    "}\n"
)


#### csharp 依赖边端到端：调用/类型归属正确，继承仅一条，var/基元跳过 [@380kkm 2026-06-05] ####
def test_csharp_edges_end_to_end():
    rows, edges = _lang_edges(_CS_SRC, "csharp")
    calls = _rel_pairs(rows, edges, {"calls"})
    types = _rel_pairs(rows, edges, {"uses_type"})
    assert {("DoWork", "calls", "WriteLine"),
            ("DoWork", "calls", "Process"),
            ("DoWork", "calls", "Compute")} <= calls
    # 字段类型归属于封闭的 CLASS；参数/返回/new 归属于方法。
    assert ("Widget", "uses_type", "Helper") in types
    assert {("DoWork", "uses_type", "Config"),
            ("DoWork", "uses_type", "Result"),
            ("DoWork", "uses_type", "StringBuilder")} <= types
    # extends + implements 来自遍历器（base_list），各恰好一次。
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    assert sum(1 for e in edges if e["relation"] == "implements") == 1
    # `var` 局部是隐式类型 -> 不算 uses_type 依赖；基元类型也跳过。
    assert all(e["dst_name"] not in ("var", "int") for e in edges
               if e["relation"] == "uses_type")


#### 带别名的 using 捕获源类型而非别名标识符 [@380kkm 2026-06-05] ####
def test_csharp_aliased_using_excludes_alias_name():
    # `using Alias = Some.Long.Name;` 必须捕获源类型，绝不能捕获别名 'Alias'。
    # 顶层 using 无封闭符号（成边时被丢弃），故在原始捕获层面断言 !name 锚点从不抓到别名标识符。
    L = get_language(E._PACK_NAME["csharp"])
    specs = E._load_query_specs(None)
    q = Query(L, specs["csharp"])
    from tree_sitter import QueryCursor
    tree = Parser(L).parse("using Alias = Some.Long.Name;\nusing System;\n".encode("utf-8"))
    caps = QueryCursor(q).captures(tree.root_node)
    imports = {n.text.decode("utf-8") for n in caps.get("dep.imports", [])}
    # !name 锚点把它排除
    assert "Alias" not in imports
    assert "System" in imports
    # 别名的源类型确被捕获
    assert any("Some.Long.Name" in s for s in imports)


#### js/ts/tsx/csharp 提取对同一输入是确定性的 [@380kkm 2026-06-05] ####
def test_js_ts_csharp_deterministic():
    for src, lang in ((_JS_SRC, "javascript"), (_TS_SRC, "typescript"),
                      (_TS_SRC, "tsx"), (_CS_SRC, "csharp")):
        r1, e1 = _lang_edges(src, lang)
        r2, e2 = _lang_edges(src, lang)
        assert r1 == r2 and e1 == e2


# ===========================================================================
# 回归：无 .scm 预设的遍历器语言不受此层影响 —— 仍保留仅遍历器的边
# （contains/extends），零条 @dep 边。`java` 有遍历器（符号 + extends）但不带
# java.scm，是完美的探针。
# ===========================================================================
#### 无 .scm 预设的遍历器语言只有遍历器边、无任何 dep 边 [@380kkm 2026-06-05] ####
def test_walker_lang_without_scm_has_no_dep_edges():
    # 不存在 java.scm 预设
    assert "java" not in E._load_query_specs(None)
    src = ("class A extends B {\n"
           "  void m(C c) { helper(); }\n"
           "}\n")
    # 传入 query=None（无预设）
    rows, edges = _walker_extract(src, "java")
    rels = {e["relation"] for e in edges}
    # 遍历器仍发出结构边……
    assert "contains" in rels and "extends" in rels
    # ……但声明式 dep 层未新增任何东西（无 calls/imports/uses_type）。
    assert not (rels & {"calls", "imports", "uses_type"})
    assert any(r["name"] == "A" and r["kind"] == "class" for r in rows)


# ===========================================================================
# 真实夹具冒烟测试：每种语言搭一个微型临时仓库，索引 + enrich 进一个隔离的库，断言
# 依赖边落入 DB。MANYREAD_HOME 指向临时目录，使 hub 注册表写入绝不碰用户 hub
# （~/.manyread/stores.json）；一切在 finally 中清理。这条路径走完整的
# index_build -> enrich_treesitter -> edges 表，而不止内存提取器。
# ===========================================================================
#### 隔离库内 js/csharp 全链路冒烟：依赖边落入 edges 表且 hub 隔离 [@380kkm 2026-06-05] ####
def test_real_fixture_smoke_js_csharp_isolated(tmp_path, monkeypatch):
    import sqlite3
    import index_build
    from lib import config

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    # 隔离 hub 及任何环境中的库发现，确保绝不触碰用户 hub。
    monkeypatch.setenv("MANYREAD_HOME", str(home))
    monkeypatch.delenv("MANYREAD_STORE", raising=False)

    (repo / "a.js").write_text(
        "function loader() {\n"
        '    const fs = require("node:fs");\n'
        "    helper();\n"
        "    return fs.readFileSync(loader);\n"
        "}\n"
        "class Widget extends Base { render() { this.draw(); } }\n",
        encoding="utf-8",
    )
    (repo / "b.cs").write_text(
        "using System;\n"
        "namespace N {\n"
        "  class C {\n"
        "    public Result Do(Config c) {\n"
        "      var s = new StringBuilder();\n"
        '      Console.WriteLine("x");\n'
        "      return new Result();\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    rc = index_build.main(["--init", "--store-at", str(repo), "--root", str(repo),
                           "--langs", "javascript,csharp", "--exts", ".js,.cs"])
    assert rc == 0
    store = repo / "manyread"
    rc = E.main(["--store", str(store), "--root", str(repo)])
    assert rc == 0

    cfg = config.resolve_project(root=str(repo), store=str(store))
    conn = sqlite3.connect(cfg.db_path)
    try:
        got = {(name, rel, dst) for name, rel, dst in conn.execute(
            "SELECT sy.name, e.relation, e.dst_name FROM edges e "
            "JOIN symbols sy ON sy.id = e.src_symbol_id "
            "WHERE e.relation IN ('calls','imports','uses_type')")}
    finally:
        conn.close()

    # JavaScript：函数内的 require() 同时挂为一条 call + 一条 import。
    assert ("loader", "calls", "helper") in got
    assert ("loader", "calls", "readFileSync") in got
    assert ("loader", "imports", "node:fs") in got
    assert ("render", "calls", "draw") in got
    # C#：调用 + 类型使用都落入 edges 表。
    assert ("Do", "calls", "WriteLine") in got
    assert {("Do", "uses_type", "Config"), ("Do", "uses_type", "Result"),
            ("Do", "uses_type", "StringBuilder")} <= got

    # hub 隔离：注册表写入了临时 MANYREAD_HOME，而非用户的。
    assert (home / "stores.json").exists()


# ===========================================================================
# macro_strip：对声明修饰符宏（`class|struct <ALLCAPS_MACRO> <RealName>`）做长度保持
# 的解析前剥离。纯函数、确定性、span 精确、默认开启、泛化安全（干净 cpp 与非 cpp 逐字节
# 一致）。变换见 enrich_treesitter._strip_decl_macros + config.load_macro_strip。
# ===========================================================================
_MS_DEFAULT = {"enabled": True, "extra_names": [], "extra_patterns": []}


#### 用真实 cpp grammar 取 (有错误, 类/结构体名, 主体存在) 的夹具 [@380kkm 2026-06-05] ####
def _cpp_class(src: str):
    """经真实 cpp grammar 返回 (has_error, class/struct 名, 带主体的成员是否存在)。"""
    parser = Parser(get_language("cpp"))
    tree = parser.parse(src.encode())
    cs = [None]

    #### 深度优先找首个 class/struct 节点 [@380kkm 2026-06-05] ####
    def find(n):
        if n.type in ("class_specifier", "struct_specifier") and cs[0] is None:
            cs[0] = n
        for c in n.children:
            find(c)

    find(tree.root_node)
    node = cs[0]
    nm = node.child_by_field_name("name") if node else None
    body = node.child_by_field_name("body") if node else None
    return tree.root_node.has_error, (nm.text.decode() if nm else None), body is not None


#### 正向：剥离后无解析错误、恢复真实名、保有真实主体 [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("src,real_name", [
    ("class ENGINE_API UMaterial : public UMaterialInterface { int A; void F(){} };", "UMaterial"),
    ("class BASE_EXPORT Foo {};", "Foo"),                       # Chromium
    ("struct PROTOBUF_EXPORT Bar { int x; };", "Bar"),          # protobuf
    ("class UE_DEPRECATED(5.0) UOld {};", "UOld"),              # 带实参的宏
    ("class CV_EXPORTS Mat { int rows; };", "Mat"),             # OpenCV
    ("class ENGINE_API UMaterial final : public X {};", "UMaterial"),
    ("template<typename T> class ENGINE_API TFoo {};", "TFoo"),
])
def test_macro_strip_recovers_real_name(src, real_name):
    """正向：剥离后无解析错误、得到真实名、并有真实主体。"""
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    # 长度保持
    assert len(stripped) == len(src)
    orig_err, orig_name, _ = _cpp_class(src)
    err, name, has_body = _cpp_class(stripped)
    # bug：原始解析把宏当成了类名
    assert orig_name != real_name
    assert name == real_name and not err and has_body


#### 剥离后恢复成员符号且存活 token 字节偏移与手工空白化逐字节一致 [@380kkm 2026-06-05] ####
def test_macro_strip_recovers_member_rows_and_spans():
    """类被改名且其主体方法成为子符号；存活 token 的字节偏移与手工（空格）空白化的源逐字节一致。"""
    src = "class ENGINE_API UMaterial : public UMaterialInterface { int A; void F(){} };"
    rows_on, edges_on = E._extract_file(
        1, src, "cpp", Parser(get_language("cpp")), False, None, _MS_DEFAULT)
    names = {r["name"] for r in rows_on}
    assert {"UMaterial", "F"} <= names and "ENGINE_API" not in names
    um = next(r for r in rows_on if r["name"] == "UMaterial")
    fm = next(r for r in rows_on if r["name"] == "F")
    # F 是 UMaterial 的成员
    assert fm["parent_local"] == um["_local"]
    assert ("UMaterialInterface", "extends") in {
        (e["dst_name"], e["relation"]) for e in edges_on}
    # 与手工空白化的源对比 span 精确性
    hand = src.replace("ENGINE_API ", " " * len("ENGINE_API "))
    rows_hand, _ = E._extract_file(
        1, hand, "cpp", Parser(get_language("cpp")), False, None, {"enabled": False})
    assert um["start_byte"] == next(r for r in rows_hand if r["name"] == "UMaterial")["start_byte"]


#### 负向/泛化安全：干净 cpp、非类名位置的宏、class/struct 作为子串都是逐字节 no-op [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("src", [
    ("class Baz {};"),                                          # 普通类
    ("class RGBA {};"),                                         # 全大写名，无第二个标识符
    ("struct UPPER {};"),
    ("class Foo : public Bar { int a; void f(){} };"),         # 干净的继承
    ("class Foo : public BAR_API Base {};"),                    # 宏在基类列表里，不在名位置
    ("int ENGINE_API_VERSION = 3;"),                            # 形似宏的名，在取值位置
    ("void ENGINE_API Foo();"),                                 # 函数返回修饰符（不在处理范围）
    ("enum class EColor { Red };"),
    # 词边界：作为用户标识符子串的 `class`/`struct` 绝不能命中
    # （否则其后的全大写 token 会被当成修饰符宏空白化）。
    ("subclass ENGINE_API Foo x;"),
    ("metaclass FOO_API Bar y;"),
    ("mystruct ENGINE_API_T xVar;"),
    ("superclass DLL_API Baz q;"),
])
def test_macro_strip_negative_byte_identical(src):
    """负向/泛化安全：干净 cpp + 非类名位置的宏 + class/struct 作为子串的标识符都是逐字节 no-op
    （即便默认开启）。"""
    assert E._strip_decl_macros(src, _MS_DEFAULT) == src


#### 堆叠的声明修饰符宏被完全剥离（不动点） [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("src,real_name", [
    # 堆叠的 export+可见性/属性宏：两者都必须被剥离（迭代到不动点）。
    ("class DLL_EXPORT ENGINE_API UMaterial { int A; void F(){} };", "UMaterial"),
    ("struct BASE_EXPORT PROTOBUF_EXPORT Bar {};", "Bar"),
    ("class A_API B_API C_API UFoo {};", "UFoo"),
])
def test_macro_strip_stacked_macros_recovered(src, real_name):
    """堆叠的声明修饰符宏被完全剥离 -> 真实名、无错误。"""
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    assert len(stripped) == len(src)
    err, name, has_body = _cpp_class(stripped)
    assert name == real_name and not err and has_body


#### `enum class <MACRO> <Name>` 恢复 enum 的真实名 [@380kkm 2026-06-05] ####
def test_macro_strip_enum_class_macro_recovered():
    """`enum class <MACRO> <Name>` 恢复 enum 的真实名（\\b 词边界仍在 `enum class` 内的
    `class` 词首处触发）。"""
    src = "enum class ENGINE_API EColor { Red, Green };"
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    assert len(stripped) == len(src)
    parser = Parser(get_language("cpp"))
    tree = parser.parse(stripped.encode())
    assert not tree.root_node.has_error
    # 恢复出的 enum 名为 EColor（而非 ENGINE_API）
    rows, _ = E._extract_file(1, src, "cpp", parser, False, None, _MS_DEFAULT)
    assert any(r["name"] == "EColor" and r["kind"] == "enum" for r in rows)
    assert all(r["name"] != "ENGINE_API" for r in rows)


#### 高危回归：被空白化的宏实参区内的非 ASCII 字符不移位下游字节偏移 [@380kkm 2026-06-05] ####
def test_macro_strip_utf8_byte_offsets_preserved():
    """高危回归：被空白化的宏实参区内的非 ASCII 字符（如 UE_DEPRECATED 消息里的 em-dash）
    绝不能移位下游 BYTE 偏移。空白化按每个 UTF-8 字节输出一个空格，使编码长度不变，于是后续
    符号的 start_byte 等于其在原始（未改动）内容里的偏移。
    """
    src = ('class UE_DEPRECATED(5.0, "Use NewType — deprecated") UOld {};\n'
           "class Bar {};")
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    # 尽管被空白化的实参里有多字节 em-dash，字节长度仍保持。
    assert len(stripped.encode("utf-8")) == len(src.encode("utf-8"))
    assert stripped.count("\n") == src.count("\n")
    rows, _ = E._extract_file(1, src, "cpp", Parser(get_language("cpp")), False, None, _MS_DEFAULT)
    names = {r["name"] for r in rows}
    assert {"UOld", "Bar"} <= names and "UE_DEPRECATED" not in names
    # Bar 记录的 start_byte 必须索引到原始字节里的 'class Bar' 处。
    orig_bytes = src.encode("utf-8")
    bar = next(r for r in rows if r["name"] == "Bar")
    assert orig_bytes[bar["start_byte"]:bar["start_byte"] + len(b"class Bar")] == b"class Bar"


#### 注释内的误命中无害：空白化长度保持且仅作用于解析副本 [@380kkm 2026-06-05] ####
def test_macro_strip_comment_false_positive_is_harmless():
    """已记录的开放风险：原始文本正则可能空白化出现在 // 注释里的 `class MACRO Name`。它是
    无害的，因为空白化长度保持且只作用于本地解析副本（入库内容保持原样），故提取出的符号与未
    空白化的源相同。"""
    src = ("class RealClass {\n"
           "  // class ENGINE_API Fake Inner {}; in a comment\n"
           "};\n")
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    # 仍长度保持
    assert len(stripped) == len(src)
    parser = Parser(get_language("cpp"))
    rows_blanked, _ = E._extract_file(1, src, "cpp", parser, False, None, _MS_DEFAULT)
    rows_raw, _ = E._extract_file(1, src, "cpp", parser, False, None, {"enabled": False})
    # 两种情形下唯一的符号都是 RealClass；注释文本绝不成为符号。
    assert [(r["name"], r["start_byte"], r["end_byte"]) for r in rows_blanked] \
        == [(r["name"], r["start_byte"], r["end_byte"]) for r in rows_raw]


#### 禁用（enabled=false 或 None）时是恒等变换 [@380kkm 2026-06-05] ####
def test_macro_strip_disabled_is_identity():
    src = "class ENGINE_API UMaterial : public X { int A; };"
    assert E._strip_decl_macros(src, {"enabled": False}) == src
    assert E._strip_decl_macros(src, None) == src


#### 配置的 extra_names/extra_patterns 扩展检测器，位置闸门仍约束之 [@380kkm 2026-06-05] ####
def test_macro_strip_extra_names_and_patterns():
    """配置扩展内建检测器；位置闸门仍对其加以约束。"""
    # GTEST_API_（尾下划线）是已记录的 _is_macro_type 缺口。
    g = "class GTEST_API_ MyTest : public Test {};"
    # 默认 no-op
    assert E._strip_decl_macros(g, _MS_DEFAULT) == g
    ext = {"enabled": True, "extra_names": ["GTEST_API_"], "extra_patterns": []}
    assert _cpp_class(E._strip_decl_macros(g, ext))[1] == "MyTest"
    # extra_patterns 以 OR 并入（无下划线 -> 基础正则漏掉 MYLIBEXPORT）
    m = "class MYLIBEXPORT Thing {};"
    pat = {"enabled": True, "extra_names": [], "extra_patterns": ["^MYLIB[A-Z]+$"]}
    # 默认 no-op
    assert E._strip_decl_macros(m, _MS_DEFAULT) == m
    assert _cpp_class(E._strip_decl_macros(m, pat))[1] == "Thing"
    # 即便配置了 extra_name，也无法剥离真实名（无第二个标识符）
    assert E._strip_decl_macros("class FOO {};", {"enabled": True, "extra_names": ["FOO"]}) \
        == "class FOO {};"


#### 剥离幂等且保留换行 [@380kkm 2026-06-05] ####
def test_macro_strip_idempotent_and_newline_preserving():
    src = "class ENGINE_API\n  UMaterial : public X { int A; };"
    once = E._strip_decl_macros(src, _MS_DEFAULT)
    # 幂等
    assert E._strip_decl_macros(once, _MS_DEFAULT) == once
    assert once.count("\n") == src.count("\n") and len(once) == len(src)
    assert _cpp_class(once)[1] == "UMaterial"


#### 仅 c 系语言运行该变换，非 cpp 语言不受影响 [@380kkm 2026-06-05] ####
def test_macro_strip_only_cfamily_lang():
    """非 cpp 语言绝不运行该变换（lang not in _CFAMILY_STRIP_LANGS）。"""
    assert "cpp" in E._CFAMILY_STRIP_LANGS
    for lang in ("python", "javascript", "typescript", "csharp", "glsl", "java"):
        assert lang not in E._CFAMILY_STRIP_LANGS
    pysrc = "class A(Base):\n    def m(self): return 1\n"
    on = E._extract_file(1, pysrc, "python", Parser(get_language("python")), False, None, _MS_DEFAULT)
    off = E._extract_file(1, pysrc, "python", Parser(get_language("python")), False, None, {"enabled": False})
    assert on == off


#### 已提交的 cpp golden 无修饰符宏，默认开启的剥离对其是 no-op [@380kkm 2026-06-05] ####
def test_macro_strip_golden_cpp_unchanged():
    """已提交的 cpp golden 无修饰符宏 -> 默认开启的剥离是 no-op，故即便用生产默认值，逐字节
    golden 仍保持绿色。"""
    assert E._strip_decl_macros(_CPP_GOLDEN_SRC, _MS_DEFAULT) == _CPP_GOLDEN_SRC
    rows, edges = E._extract_file(
        1, _CPP_GOLDEN_SRC, "cpp", Parser(get_language("cpp")), False, None, _MS_DEFAULT)
    assert rows == _CPP_GOLDEN_ROWS and edges == _CPP_GOLDEN_EDGES


#### config.load_macro_strip：缺省=>开启、可禁用、可扩展、畸形=>默认+告警 [@380kkm 2026-06-05] ####
def test_macro_strip_config_load(tmp_path):
    """config.load_macro_strip：缺省=>ON、禁用、扩展、畸形=>默认+告警。"""
    import json
    from lib import config
    store = tmp_path
    # 无 manyread.json
    assert config.load_macro_strip(store) == _MS_DEFAULT
    (store / "manyread.json").write_text(json.dumps({"alias": "x"}))
    # 缺该键
    assert config.load_macro_strip(store) == _MS_DEFAULT
    (store / "manyread.json").write_text(json.dumps({"macro_strip": {"enabled": False}}))
    assert config.load_macro_strip(store)["enabled"] is False
    (store / "manyread.json").write_text(json.dumps(
        {"macro_strip": {"enabled": True, "extra_names": ["GTEST_API_"], "extra_patterns": ["^X_[A-Z]+$"]}}))
    got = config.load_macro_strip(store)
    assert got["extra_names"] == ["GTEST_API_"] and got["extra_patterns"] == ["^X_[A-Z]+$"]
    (store / "manyread.json").write_text(json.dumps({"macro_strip": {"extra_patterns": ["("]}}))
    # 坏正则 -> 默认
    assert config.load_macro_strip(store) == _MS_DEFAULT
    (store / "manyread.json").write_text(json.dumps({"macro_strip": {"enabled": True, "bogus": 1}}))
    # 未知键被剔除 -> 默认
    assert config.load_macro_strip(store) == _MS_DEFAULT
    assert config.validate_macro_strip({"enabled": "no"}) == ["macro_strip.enabled must be a bool"]
