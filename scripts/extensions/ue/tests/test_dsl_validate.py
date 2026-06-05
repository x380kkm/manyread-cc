# audience: internal
# extensions.ue.tests.test_dsl_validate
"""预检结构 DSL 校验器（scripts/dsl_validate.py）的测试。"""
import os
import sys

import pytest

# 把 scripts/ 加入路径（本文件在 scripts/extensions/ue/tests/ 下，上溯三级）
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
try:
    import dsl_validate as V
    _HAVE = True
except Exception:  # noqa: BLE001
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")

from dsl_fixtures import (  # noqa: E402
    GOOD_ANIMLANG_STRUCT,
    GOOD_BPLISP,
    GOOD_MATLANG,
    codes as _codes,
)


#### 验证合法 matlang 产生零 error [@380kkm 2026-06-05] ####
def test_good_matlang_zero_errors():
    assert _codes(GOOD_MATLANG, "matlang", sev="error") == []


#### 验证合法 bplisp 零 error，仅产生 warning [@380kkm 2026-06-05] ####
def test_good_bplisp_zero_errors_warns_only():
    issues = V.dsl_validate(GOOD_BPLISP, "bplisp")
    assert [i for i in issues if i.severity == "error"] == []
    # 未解析的绑定（Selected/returnvalue）是 warning，绝非 error
    assert any(i.code == "UNRESOLVED_REF" and i.severity == "warning" for i in issues)


#### 验证合法 animlang 产生零 error [@380kkm 2026-06-05] ####
def test_good_animlang_zero_errors():
    assert _codes(GOOD_ANIMLANG_STRUCT, "animlang", sev="error") == []


#### 验证悬空连线（连到不存在节点）被判为 DANGLING_WIRE 错误 [@380kkm 2026-06-05] ####
def test_dangling_wire():
    bad = ('(material "M" (expressions (multiply $m1 :a (connect $missing 0)))'
           " (outputs :base-color (connect $m1 0)))")
    assert "DANGLING_WIRE" in _codes(bad, "matlang", sev="error")


#### 验证重复 id 报 DUP_ID，且自连线时不误报幻象 CYCLE [@380kkm 2026-06-05] ####
def test_duplicate_id():
    bad = ('(material "M" (expressions (constant $c1 :value 1.0)'
           " (multiply $c1 :a (connect $c1 0))) (outputs :base-color (connect $c1 0)))")
    codes = _codes(bad, "matlang", sev="error")
    assert "DUP_ID" in codes
    assert "CYCLE" not in codes


#### 验证两节点互连构成的环被判为 CYCLE 错误 [@380kkm 2026-06-05] ####
def test_cycle():
    bad = ('(material "M" (expressions (multiply $a :x (connect $b 0))'
           " (multiply $b :x (connect $a 0))) (outputs :base-color (connect $a 0)))")
    codes = _codes(bad, "matlang", sev="error")
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
    assert "CYCLE" in _codes(loop, "matlang", sev="error")


#### 验证缺少 material 根被判为 MATLANG_NO_MATERIAL 错误 [@380kkm 2026-06-05] ####
def test_no_material_root():
    assert "MATLANG_NO_MATERIAL" in _codes("(expressions (constant $c1 :value 1.0))",
                                           "matlang", sev="error")


#### 验证缺少 (outputs ...) 块被判为 MATLANG_NO_OUTPUTS 错误 [@380kkm 2026-06-05] ####
def test_no_outputs_block():
    assert "MATLANG_NO_OUTPUTS" in _codes('(material "M" (expressions (constant $c1 :value 1.0)))',
                                          "matlang", sev="error")


#### 验证括号不配对（语法被拒）报 PARSE_ERROR [@380kkm 2026-06-05] ####
def test_parse_error():
    assert "PARSE_ERROR" in _codes('(material "M" (expressions (multiply $m1', "matlang", sev="error")


#### 验证 bplisp 缺少图根（顶层只有非图 form）报错 [@380kkm 2026-06-05] ####
def test_bplisp_no_graph_root():
    assert "BPLISP_NO_GRAPH" in _codes('(PrintString :instring "x" :id "1")', "bplisp", sev="error")
    # var 是合法顶层 form 但不是图（导入器白名单含 var，图根 pass 仍应报缺图）
    assert "BPLISP_NO_GRAPH" in _codes('(var Speed float :default 0.0)', "bplisp", sev="error")


#### 验证导入器白名单中的事件类/转移条件图根不再误报缺图 [@380kkm 2026-06-05] ####
def test_bplisp_event_like_graph_roots_accepted():
    # BlueprintLispConverter.cpp:7933-7939 的图根子集，逐一应零 error
    roots = ('(event BeginPlay (PrintString :instring "x" :id "1"))',
             '(input-action Jump (PrintString :instring "x" :id "1"))',
             '(input-key SpaceBar (PrintString :instring "x" :id "1"))',
             '(component-bound-event Box OnComponentBeginOverlap (exit))',
             '(actor-bound-event Door OnDestroyed (exit))',
             '(transition-cond (> Speed 100.0))')
    for text in roots:
        assert _codes(text, "bplisp", sev="error") == [], text


#### 验证未知语言仅报 UNKNOWN_LANG 且严重度为 error [@380kkm 2026-06-05] ####
def test_unknown_lang():
    issues = V.dsl_validate("(material \"M\")", "klingon")
    assert [i.code for i in issues] == ["UNKNOWN_LANG"]
    assert issues[0].severity == "error"


#### 验证同一输入两次校验结果一致（确定性） [@380kkm 2026-06-05] ####
def test_deterministic():
    a = V.dsl_validate(GOOD_MATLANG, "matlang")
    b = V.dsl_validate(GOOD_MATLANG, "matlang")
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
