"""预检结构 DSL 校验器（scripts/dsl_validate.py）的测试。"""
import os
import sys

import pytest

# 把 scripts/ 加入路径
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
try:
    import dsl_validate as V
    _HAVE = True
except Exception:  # noqa: BLE001
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")


#### GOOD 夹具：真实 reference/* 的内联镜像，期望零错误 [@380kkm 2026-06-05] ####
_GOOD_MATLANG = (
    '(material "M_SimplePBR"\n'
    "  :domain surface\n"
    "  (expressions\n"
    "    (texture-sample $tex1 :uv (connect $uv1))\n"
    "    (texture-coordinate $uv1 :coordinate-index 0)\n"
    '    (vector-parameter $vparam1 :name "TintColor")\n'
    "    (multiply $mul1 :a (connect $tex1 0) :b (connect $vparam1 0))\n"
    "    (constant $const1 :value 0.0))\n"
    "  (outputs\n"
    "    :base-color (connect $mul1 0)\n"
    "    :metallic (connect $const1 0)))\n"
)

_GOOD_BPLISP = (
    "(function\n"
    "  None\n"
    '  :event-id "8abce957"\n'
    "  :param (Selected Actor)\n"
    '  (PrintString :instring "Villager Select called!" :id "5f6936c3")\n'
    '  (set Selected "K2Node_FunctionEntry" :id "226de0c6")\n'
    "  (let returnvalue\n"
    '    (SpawnSystemAttached :location "0, 0, 0" :id "60944b57")))\n'
)

_GOOD_ANIMLANG = (
    '(anim-blueprint "SimpleStateMachine"\n'
    "  :variables [(float :speed 0.0 :range [0.0 600.0])]\n"
    "  :anim-graph\n"
    "    (state-machine :locomotion :initial :idle\n"
    "      :states\n"
    '        [(state :idle (sequence-player "Idle_Rifle" :loop true))\n'
    '         (state :walk (sequence-player "Walk_Fwd" :loop true))]))\n'
)


#### 收集校验结果的错误码（可选按严重度过滤），排序返回 [@380kkm 2026-06-05] ####
def _codes(text, lang, sev=None):
    return sorted(i.code for i in V.dsl_validate(text, lang)
                  if sev is None or i.severity == sev)


#### 验证合法 matlang 产生零 error [@380kkm 2026-06-05] ####
def test_good_matlang_zero_errors():
    assert _codes(_GOOD_MATLANG, "matlang", "error") == []


#### 验证合法 bplisp 零 error，仅产生 warning [@380kkm 2026-06-05] ####
def test_good_bplisp_zero_errors_warns_only():
    issues = V.dsl_validate(_GOOD_BPLISP, "bplisp")
    assert [i for i in issues if i.severity == "error"] == []
    # 未解析的绑定（Selected/returnvalue）是 warning，绝非 error
    assert any(i.code == "UNRESOLVED_REF" and i.severity == "warning" for i in issues)


#### 验证合法 animlang 产生零 error [@380kkm 2026-06-05] ####
def test_good_animlang_zero_errors():
    assert _codes(_GOOD_ANIMLANG, "animlang", "error") == []


#### 验证悬空连线（连到不存在节点）被判为 DANGLING_WIRE 错误 [@380kkm 2026-06-05] ####
def test_dangling_wire():
    bad = ('(material "M" (expressions (multiply $m1 :a (connect $missing 0)))'
           " (outputs :base-color (connect $m1 0)))")
    assert "DANGLING_WIRE" in _codes(bad, "matlang", "error")


#### 验证重复 id 报 DUP_ID，且自连线时不误报幻象 CYCLE [@380kkm 2026-06-05] ####
def test_duplicate_id():
    bad = ('(material "M" (expressions (constant $c1 :value 1.0)'
           " (multiply $c1 :a (connect $c1 0))) (outputs :base-color (connect $c1 0)))")
    codes = _codes(bad, "matlang", "error")
    assert "DUP_ID" in codes
    assert "CYCLE" not in codes


#### 验证两节点互连构成的环被判为 CYCLE 错误 [@380kkm 2026-06-05] ####
def test_cycle():
    bad = ('(material "M" (expressions (multiply $a :x (connect $b 0))'
           " (multiply $b :x (connect $a 0))) (outputs :base-color (connect $a 0)))")
    codes = _codes(bad, "matlang", "error")
    assert "CYCLE" in codes


#### 验证三节点环恰报一个 CYCLE，自环亦被捕获 [@380kkm 2026-06-05] ####
def test_cycle_three_node_and_self_loop():
    three = ('(material "M" (expressions (multiply $a :x (connect $b 0))'
             " (multiply $b :x (connect $c 0)) (multiply $c :x (connect $a 0)))"
             " (outputs :base-color (connect $a 0)))")
    issues = V.dsl_validate(three, "matlang")
    cycles = [i for i in issues if i.code == "CYCLE"]
    assert len(cycles) == 1
    loop = ('(material "M" (expressions (multiply $a :x (connect $a 0)))'
            " (outputs :base-color (connect $a 0)))")
    assert "CYCLE" in _codes(loop, "matlang", "error")


#### 验证缺少 material 根被判为 MATLANG_NO_MATERIAL 错误 [@380kkm 2026-06-05] ####
def test_no_material_root():
    assert "MATLANG_NO_MATERIAL" in _codes("(expressions (constant $c1 :value 1.0))",
                                           "matlang", "error")


#### 验证缺少 (outputs ...) 块被判为 MATLANG_NO_OUTPUTS 错误 [@380kkm 2026-06-05] ####
def test_no_outputs_block():
    assert "MATLANG_NO_OUTPUTS" in _codes('(material "M" (expressions (constant $c1 :value 1.0)))',
                                          "matlang", "error")


#### 验证括号不配对（语法被拒）报 PARSE_ERROR [@380kkm 2026-06-05] ####
def test_parse_error():
    assert "PARSE_ERROR" in _codes('(material "M" (expressions (multiply $m1', "matlang", "error")


#### 验证 bplisp 缺少图根（无 event|func|function|macro 头）报错 [@380kkm 2026-06-05] ####
def test_bplisp_no_graph_root():
    assert "BPLISP_NO_GRAPH" in _codes('(PrintString :instring "x" :id "1")', "bplisp", "error")


#### 验证未知语言仅报 UNKNOWN_LANG 且严重度为 error [@380kkm 2026-06-05] ####
def test_unknown_lang():
    issues = V.dsl_validate("(material \"M\")", "klingon")
    assert [i.code for i in issues] == ["UNKNOWN_LANG"]
    assert issues[0].severity == "error"


#### 验证同一输入两次校验结果一致（确定性） [@380kkm 2026-06-05] ####
def test_deterministic():
    a = V.dsl_validate(_GOOD_MATLANG, "matlang")
    b = V.dsl_validate(_GOOD_MATLANG, "matlang")
    assert a == b


#### 验证存在 PARSE_ERROR 时不短路、其余校验趟仍运行且结果有序 [@380kkm 2026-06-05] ####
def test_parse_error_runs_other_passes():
    bad = "(expressions (multiply $m1 :a (connect $x"
    issues = V.dsl_validate(bad, "matlang")
    codes = {i.code for i in issues}
    assert "PARSE_ERROR" in codes
    assert issues == sorted(issues, key=lambda i: i.sort_key())


#### 可选趟的真实参考文件清单（缺失则跳过） [@380kkm 2026-06-05] ####
_REF = r"W:\cc\reference"
_REAL = [
    (os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "simple_pbr.matlang"), "matlang"),
    (os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "emissive_rim.matlang"), "matlang"),
    (os.path.join(_REF, "Blueprint2DSL", "Tests", "Regression",
                  "villager_select_before_print.bplisp"), "bplisp"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "state_machine.animlang"), "animlang"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "third_person_char.animlang"), "animlang"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "simple_blend.animlang"), "animlang"),
]


#### 验证真实参考文件（存在时）校验出零 error [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("path,lang", _REAL)
def test_real_reference_files_zero_errors(path, lang):
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    errors = [i for i in V.dsl_validate(text, lang) if i.severity == "error"]
    assert errors == [], f"{path}: {errors}"
