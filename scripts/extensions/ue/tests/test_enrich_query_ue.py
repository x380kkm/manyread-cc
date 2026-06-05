# audience: internal
# extensions.ue.tests.test_enrich_query_ue
"""UE 资产 DSL（matlang/bplisp/animlang）声明式查询层的回归测试。

从通用的 `test_enrich_query.py` 析出，因为三种 DSL 的 .scm 与符号契约属于 UE 扩展；
本套件经 conftest 主动开启 UE 扩展发现，使 ``E._load_query_specs(None)`` 能返回 UE 的
.scm 规格。
"""
import os
import sys

import pytest

# scripts/（本文件在 scripts/extensions/ue/tests/ 下，上溯三级）
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
try:
    import enrich_treesitter as E
    from tree_sitter import Parser, Query
    from tree_sitter_language_pack import get_language
    _HAVE = True
except Exception:  # noqa: BLE001
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")


#### 用内建 .scm 查询为无遍历器 DSL 跑 _extract_file 的夹具 [@380kkm 2026-06-05] ####
def _dsl_extract(src: str, lang: str):
    # 三种 DSL 都共用 scheme grammar
    ts_lang = get_language("scheme")
    specs = E._load_query_specs(None)
    q = Query(ts_lang, specs[lang])
    return E._extract_file(1, src, lang, Parser(ts_lang), False, q)


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
    # 仅真实的 UFunction 调用，参数类型不被误捕为 call
    assert calls == ["K2_SetTimer", "PrintString", "SpawnSystemAttached"]
    # 参数类型不是 call
    assert "Selected" not in calls
    # binds 的目标名（let/set 绑定的变量），均未解析
    binds = sorted(e["dst_name"] for e in edges if e["relation"] == "binds")
    assert binds == ["NS_Path", "Selected", "returnvalue", "returnvalue"]
    assert all(e["dst_local"] is None for e in edges if e["relation"] == "binds")
    # 顶层 node/call 由 function graph 容纳
    contained_under_graph = {e["dst_name"] for e in edges
                             if e["relation"] == "contains" and e["src_local"] == g_local}
    assert {"PrintString", "set", "let"} <= contained_under_graph
    # `let` 下的纯 call 节点归属于该 let，而非 graph。
    let_locals = {r["_local"] for r in rows if r["name"] == "let"}
    nested_calls = {e["dst_name"] for e in edges
                    if e["relation"] == "contains" and e["src_local"] in let_locals}
    assert nested_calls == {"SpawnSystemAttached", "K2_SetTimer"}


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
    # 导出器形态 (define ...)/(ref ...) 的合成片段
    src = (
        '(anim-blueprint "X"\n'
        "  :anim-graph\n"
        "    (define CachedLeg (two-bone-ik :a 1))\n"
        '  (blend (ref "Get Speed") (CachedLeg)))\n'
    )
    rows, edges = _dsl_extract(src, "animlang")
    bindings = [r for r in rows if r["kind"] == "binding"]
    assert len(bindings) == 1 and bindings[0]["name"] == "CachedLeg"
    # (ref "Get Speed") 产生一条 dep.ref 边，dst_name 保留引号且未解析
    refs = [e for e in edges if e["relation"] == "ref"]
    assert len(refs) == 1 and refs[0]["dst_name"] == '"Get Speed"'
    # `(CachedLeg)` 复用额外产生一个名为 'CachedLeg' 的 def.node 符号
    node_named_cachedleg = [r for r in rows if r["kind"] == "node" and r["name"] == "CachedLeg"]
    assert len(node_named_cachedleg) == 1
