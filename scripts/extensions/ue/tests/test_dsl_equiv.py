# audience: internal
# extensions.ue.tests.test_dsl_equiv
"""规范 S 表达式等价校验器（scripts/extensions/ue/dsl_equiv.py）的测试。

conftest 已在整会话 ``run_discovery(['ue'])``，故 .matlang/.bplisp/.animlang 的文法
路由就位。这些测试既验核心 ``compare``（纯函数）的容差与判异，也经 subprocess 验 CLI
的 0/1/2 退出码。能引用真实 reference/* 时即引用（缺失则 skipif）。
"""
import os
import subprocess
import sys

import pytest

# 把 scripts/ 加入路径（本文件在 scripts/extensions/ue/tests/ 下，上溯三级）
_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SCRIPTS)
try:
    from extensions.ue import dsl_equiv as EQ
    _HAVE = True
except Exception:  # noqa: BLE001
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")

_DSL_EQUIV = os.path.join(_SCRIPTS, "extensions", "ue", "dsl_equiv.py")
_REF = r"W:\cc\reference"


#### 比较两段文本并返回是否等价（默认严格） [@380kkm 2026-06-05] ####
def _equiv(a, b, lang, ignore=()):
    _diffs, ok = EQ.compare(a, b, lang, ignore)
    return ok
#### /比较助手 ####


#### 读取真实 reference 文件文本；缺失则 skip [@380kkm 2026-06-05] ####
def _read_ref(*parts):
    path = os.path.join(_REF, *parts)
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        return fh.read()
#### /读取 reference ####


#### 真实 matlang：重排版（空白 + 注释 + 跨 key 重排）后仍等价 [@380kkm 2026-06-05] ####
def test_matlang_reformatted_equivalent():
    ref = _read_ref("MaterialBP2DSL", "DSL", "Examples", "simple_pbr.matlang")
    reformatted = (
        '(material  "M_SimplePBR"  ;; 重排了 keyword 顺序并加注释\n'
        "  :blend-mode opaque :domain surface :shading-model default-lit :two-sided false\n"
        "  (expressions\n"
        '    (texture-sample $tex1 :uv (connect $uv1) :texture (asset "/Game/Textures/T_Brick_BaseColor"))\n'
        '    (texture-sample $tex2 :texture (asset "/Game/Textures/T_Brick_Normal") :uv (connect $uv1))\n'
        "    (texture-coordinate $uv1 :coordinate-index 0 :u-tiling 2.0 :v-tiling 2.0)\n"
        '    (vector-parameter $vparam1 :group "Base" :name "TintColor" :default (1.0 0.8 0.6 1.0))\n'
        "    (multiply $mul1 :a (connect $tex1 0) :b (connect $vparam1 0))\n"
        '    (scalar-parameter $sparam1 :default 0.65 :name "Roughness" :group "Surface")\n'
        "    (constant $const1 :value 0.0))\n"
        "  (outputs :metallic (connect $const1 0) :base-color (connect $mul1 0)\n"
        "    :normal (connect $tex2 0) :roughness (connect $sparam1 0)))\n"
    )
    assert _equiv(ref, reformatted, "matlang")


#### 真实 bplisp：重排版 + 跨 key kwarg 重排后仍等价 [@380kkm 2026-06-05] ####
def test_bplisp_reformatted_equivalent():
    ref = _read_ref("Blueprint2DSL", "Tests", "Regression",
                    "villager_select_before_print.bplisp")
    # 仅重排 PrintString 内的 kwarg 顺序并加注释——语句顺序与 :param 顺序均不动
    reformatted = ref.replace(
        ':instring "Villager Select called!"\n    :bprinttoscreen true',
        ';; 重排了 kwarg\n    :bprinttoscreen true\n    :instring "Villager Select called!"')
    assert _equiv(ref, reformatted, "bplisp")


#### 真实 animlang：重排版（跨 key 重排 + 注释）后仍等价 [@380kkm 2026-06-05] ####
def test_animlang_reformatted_equivalent():
    ref = _read_ref("AnimationBP2FP-N", "DSL", "Examples", "simple_blend.animlang")
    reformatted = (
        '(anim-blueprint "SimpleBlend" ;; 重排版\n'
        "  :anim-graph\n"
        "    (blend :blend-alpha\n"
        '      (sequence-player "Idle" :loop true)\n'
        '      (sequence-player "Walk" :loop true))\n'
        "  :variables\n"
        "    [(float :range [0.0 1.0] :blend-alpha 0.5)])\n"
    )
    assert _equiv(ref, reformatted, "animlang")


#### matlang multiply 的 :a/:b 连线互换 -> 不等价（图层 diff 看不见此例） [@380kkm 2026-06-05] ####
def test_matlang_ab_swap_not_equivalent():
    # 互换 :a/:b 后边的多重集逐字节不变，故 enrich 图层 diff 判其相同——而它们不同
    a = ('(material "M" (expressions (multiply $m :a (connect $x 0) :b (connect $y 0)))'
         " (outputs :c (connect $m 0)))")
    b = ('(material "M" (expressions (multiply $m :a (connect $y 0) :b (connect $x 0)))'
         " (outputs :c (connect $m 0)))")
    assert not _equiv(a, b, "matlang")
    # 同时确认 diff 精确落到 :a/:b 的连线 atom 上
    diffs, _ = EQ.compare(a, b, "matlang")
    assert any(":a" in d.path for d in diffs)
    assert any(":b" in d.path for d in diffs)


#### bplisp 语句体内重排 -> 不等价（位置项顺序严格保留） [@380kkm 2026-06-05] ####
def test_bplisp_statement_reorder_not_equivalent():
    a = '(function None (set A 1 :id "1") (set B 2 :id "2"))'
    b = '(function None (set B 2 :id "2") (set A 1 :id "1"))'
    assert not _equiv(a, b, "bplisp")


#### bplisp 重复 :param 互换 -> 不等价（同名 key 保相对序，即签名顺序） [@380kkm 2026-06-05] ####
def test_bplisp_repeated_param_swap_not_equivalent():
    a = '(function None :param (Sel Actor) :param (Tgt Pawn) (exit))'
    b = '(function None :param (Tgt Pawn) :param (Sel Actor) (exit))'
    assert not _equiv(a, b, "bplisp")


#### bplisp 跨 key kwarg 重排 -> 等价（不同 key 的顺序归一化） [@380kkm 2026-06-05] ####
def test_bplisp_cross_key_kwarg_reorder_equivalent():
    a = '(function None (PrintString :instring "x" :duration 5 :bprinttoscreen true :id "1"))'
    b = '(function None (PrintString :bprinttoscreen true :id "1" :instring "x" :duration 5))'
    assert _equiv(a, b, "bplisp")


#### 字面值改变 0.0 -> 0.5 不等价；0.50 vs 0.5 等价（数值比较） [@380kkm 2026-06-05] ####
def test_literal_value_change_and_numeric_compare():
    base = '(material "M" (expressions (constant $c :value {v})) (outputs :x (connect $c 0)))'
    assert not _equiv(base.format(v="0.0"), base.format(v="0.5"), "matlang")
    assert _equiv(base.format(v="0.50"), base.format(v="0.5"), "matlang")


#### --ignore-keys：仅 :id GUID 不同的两段 bplisp，带标志等价、不带不等价 [@380kkm 2026-06-05] ####
def test_ignore_keys_drops_guids():
    a = '(function None (set A 1 :id "aaaa") (PrintString :instring "x" :id "bbbb"))'
    b = '(function None (set A 1 :id "zzzz") (PrintString :instring "x" :id "wwww"))'
    assert not _equiv(a, b, "bplisp")
    assert _equiv(a, b, "bplisp", ["id"])


#### 结尾独立 keyword flag（无值）被稳定处理、不崩溃 [@380kkm 2026-06-05] ####
def test_standalone_trailing_flag_stable():
    # 两侧都带 standalone flag -> 等价
    assert _equiv("(node :a 1 :flag)", "(node :flag :a 1)", "matlang")
    # 一侧 flag 有值、另一侧无 -> 不等价（不会因 standalone 而误判相等）
    assert not _equiv("(node :flag 1)", "(node :flag)", "matlang")


#### 注释/空白不敏感：纯加注释与改空白后等价 [@380kkm 2026-06-05] ####
def test_comment_and_whitespace_insensitive():
    a = '(material "M" (expressions (constant $c :value 1.0)) (outputs :x (connect $c 0)))'
    b = ('(material "M" ;; 顶层注释\n'
         "  (expressions\n"
         "    (constant $c :value 1.0)) ; 行尾注释\n"
         "  (outputs :x (connect $c 0)))\n")
    assert _equiv(a, b, "matlang")


#### 同一对输入两次比较结果一致（确定性） [@380kkm 2026-06-05] ####
def test_deterministic():
    a = '(material "M" (expressions (multiply $m :a (connect $x 0) :b (connect $y 0))) (outputs :c (connect $m 0)))'
    b = '(material "M" (expressions (multiply $m :b (connect $y 0) :a (connect $x 0))) (outputs :c (connect $m 0)))'
    assert EQ.compare(a, b, "matlang") == EQ.compare(a, b, "matlang")


#### 解析失败 -> compare 抛 ValueError（不可解析文件的等价性未定义） [@380kkm 2026-06-05] ####
def test_parse_failure_raises():
    with pytest.raises(ValueError):
        EQ.compare("(material \"M\" (expressions", "(material \"M\")", "matlang")


#### 不同文法（matlang vs bplisp）的扩展名 -> infer_lang 抛 ValueError [@380kkm 2026-06-05] ####
def test_infer_lang_grammar_mismatch():
    with pytest.raises(ValueError):
        EQ.infer_lang("a.matlang", "b.bplisp")


#### 经 subprocess 在 CLI 上验 0/1/2 三条退出码路径 [@380kkm 2026-06-05] ####
def test_cli_exit_codes(tmp_path):
    same = '(material "M" (expressions (constant $c :value 0.0)) (outputs :x (connect $c 0)))\n'
    reformatted = ('(material "M" ;; 注释\n  (expressions (constant $c :value 0.00))\n'
                   "  (outputs :x (connect $c 0)))\n")
    diff = '(material "M" (expressions (constant $c :value 0.5)) (outputs :x (connect $c 0)))\n'
    a = tmp_path / "a.matlang"
    b = tmp_path / "b.matlang"
    d = tmp_path / "d.matlang"
    bad = tmp_path / "bad.matlang"
    a.write_text(same, encoding="utf-8")
    b.write_text(reformatted, encoding="utf-8")
    d.write_text(diff, encoding="utf-8")
    bad.write_text('(material "M" (expressions (constant $c', encoding="utf-8")

    def run(*args):
        return subprocess.run([sys.executable, _DSL_EQUIV, *args],
                              capture_output=True, text=True)

    # 退出 0：等价（数值 0.00 == 0.0、注释不敏感）
    r0 = run(str(a), str(b))
    assert r0.returncode == 0, r0.stderr
    assert "EQUIVALENT" in r0.stdout

    # 退出 1：字面值不同
    r1 = run(str(a), str(d))
    assert r1.returncode == 1, r1.stdout
    # 退出 1 + --json：发出 diff 列表
    r1j = run(str(a), str(d), "--json")
    assert r1j.returncode == 1
    import json
    payload = json.loads(r1j.stdout)
    assert payload and payload[0]["kind"] == "atom"

    # 退出 2：解析失败
    r2 = run(str(bad), str(a))
    assert r2.returncode == 2
    assert "error" in r2.stderr.lower()


#### #|..|# 块注释与 #;datum 一样整体跳过、不产生伪差异 [@380kkm 2026-06-05] ####
def test_block_comment_skipped():
    a = '(material "M" (expressions (constant $c :value 0.0)))'
    b = '(material "M" #| 块注释 |# (expressions #| inline |# (constant $c :value 0.0)))'
    assert _equiv(a, b, "matlang")
