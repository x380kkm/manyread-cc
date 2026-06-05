# audience: internal
# extensions.ue.tests.test_dsl_semantic_bplisp_animlang
"""bplisp / animlang 由 schema 驱动的语义校验层（scripts/dsl_validate.py）的测试。

镜像 test_dsl_semantic.py 的 matlang 套路，覆盖两条语言的：合法文件零语义错误、未知类型
warning、严格属性 form 的未知属性 warning、非严格 form（设计稿位置关键字）不误报、必需引脚
缺失 error、以及 --schema 门控（无 schema 时与两参调用逐字节一致）。schema 来自扩展自带的
bplisp.sample.json / animlang.sample.json；必需引脚路径用内联 ad-hoc 字典验证（自带 schema
有意全部 required:false）。
"""
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
    GOOD_ANIMLANG_SEMANTIC,
    GOOD_BPLISP,
    codes as _codes,
)

_SCHEMA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "schemas"))
_BPLISP_SCHEMA = os.path.join(_SCHEMA_DIR, "bplisp.sample.json")
_ANIMLANG_SCHEMA = os.path.join(_SCHEMA_DIR, "animlang.sample.json")

_SEMANTIC_CODES = {"UNKNOWN_NODE_TYPE", "UNKNOWN_PROP", "MISSING_REQUIRED_PIN"}


#### 加载内置的 bplisp / animlang 样例 schema [@380kkm 2026-06-05] ####
def _bplisp_schema():
    return V.load_schema(_BPLISP_SCHEMA)


#### 加载内置的 animlang 样例 schema [@380kkm 2026-06-05] ####
def _animlang_schema():
    return V.load_schema(_ANIMLANG_SCHEMA)


#### 收集 bplisp/animlang 的语义错误条目 [@380kkm 2026-06-05] ####
def _sem(text, lang, schema):
    return [i for i in V.dsl_validate(text, lang, schema) if i.code in _SEMANTIC_CODES]


#### 验证无 schema 路径与三参传 None 的结果完全相同（--schema 门控） [@380kkm 2026-06-05] ####
def test_bplisp_no_schema_byte_identical():
    assert V.dsl_validate(GOOD_BPLISP, "bplisp") == V.dsl_validate(GOOD_BPLISP, "bplisp", None)


#### 验证合法 bplisp 加 schema 不引入任何 error [@380kkm 2026-06-05] ####
def test_bplisp_good_inline_no_errors():
    assert _codes(GOOD_BPLISP, "bplisp", _bplisp_schema(), "error") == []


#### 真实 bplisp 示例（仓库唯一一个）对样例 schema 零语义问题 [@380kkm 2026-06-05] ####
_VILLAGER = os.path.join(
    r"W:\cc\reference", "Blueprint2DSL", "Tests", "Regression",
    "villager_select_before_print.bplisp")


#### 验证真实 bplisp 示例对样例 schema 零语义问题且无 error [@380kkm 2026-06-05] ####
def test_bplisp_real_example_zero_semantic():
    if not os.path.isfile(_VILLAGER):
        pytest.skip(f"reference fixture absent: {_VILLAGER}")
    with open(_VILLAGER, encoding="utf-8") as fh:
        text = fh.read()
    assert _sem(text, "bplisp", _bplisp_schema()) == []
    assert [i for i in V.dsl_validate(text, "bplisp", _bplisp_schema())
            if i.severity == "error"] == []


#### 验证 CALL 节点（开放 UFunction 词表）绝不被判 UNKNOWN_NODE_TYPE [@380kkm 2026-06-05] ####
def test_bplisp_call_open_vocabulary_not_flagged():
    src = ("(function None (DoSomethingObscure :x 1 :id \"a\")"
           " (AnotherRareUFunction :y 2 :id \"b\"))")
    assert "UNKNOWN_NODE_TYPE" not in _codes(src, "bplisp", _bplisp_schema())


#### 验证 .scm 捕获的每个结构 node 都在 schema 词表内（结构 node 永不报未知类型） [@380kkm 2026-06-05] ####
def test_bplisp_structural_vocab_matches_schema():
    # bplisp .scm 的结构 node 白名单与本 schema 的 form 集合一致：被捕为 kind=='node' 的
    # 头词必在字典里 -> 结构 node 的 UNKNOWN_NODE_TYPE 不会触发；未知词（如小写 while-loop）
    # 根本不被 .scm 捕获，故也无行可报。
    src = ("(function None (let X (delay 1.0)) (set Y 1 :id \"a\") (seq) (branch)"
           " (foreach I C) (cast T V) (return) (vec 1 2 3) (rot 0 0 0) (make-array)"
           " (get-array-item) (break-struct) (switch) (switch-int) (switch-enum)"
           " (switch-string) (while-loop :id \"b\"))")
    schema_forms = set(_bplisp_schema()["bplisp"])
    ctx = V._build_context(src, "bplisp")
    node_heads = {r["name"] for r in ctx.rows if r["kind"] == "node"}
    assert node_heads <= schema_forms
    # while-loop 未被捕获 -> 不在 node_heads 里
    assert "while-loop" not in node_heads
    assert "UNKNOWN_NODE_TYPE" not in _codes(src, "bplisp", _bplisp_schema())


#### 验证严格 form（set）上的未知属性被判 warning，无 schema 时不报 [@380kkm 2026-06-05] ####
def test_bplisp_unknown_prop_on_strict_set_is_warning():
    src = '(function None (set X 1 :idd "a"))'
    issues = V.dsl_validate(src, "bplisp", _bplisp_schema())
    unk = [i for i in issues if i.code == "UNKNOWN_PROP"]
    assert len(unk) == 1 and unk[0].severity == "warning"
    assert ":idd" in unk[0].message
    assert "UNKNOWN_PROP" not in _codes(src, "bplisp")


#### 验证非严格 form 不发 UNKNOWN_PROP（call-parent 的开放 :pin 参数） [@380kkm 2026-06-05] ####
def test_bplisp_nonstrict_form_no_unknown_prop():
    src = '(function None (call-parent ReceiveBeginPlay :anything 1 :other 2))'
    assert "UNKNOWN_PROP" not in _codes(src, "bplisp", _bplisp_schema())


#### 验证必需引脚缺失被判 error（内联 ad-hoc schema；自带 schema 全 required:false） [@380kkm 2026-06-05] ####
def test_bplisp_missing_required_pin_via_adhoc_schema():
    adhoc = {"bplisp": {"branch": {"pins": {"cond": {"required": True}}}}}
    src = "(function None (branch))"
    issues = V.dsl_validate(src, "bplisp", adhoc)
    miss = [i for i in issues if i.code == "MISSING_REQUIRED_PIN"]
    assert len(miss) == 1 and miss[0].severity == "error"
    assert ":cond" in miss[0].message
    assert "MISSING_REQUIRED_PIN" not in _codes(src, "bplisp")


#### 验证 ad-hoc 必需引脚已用 pose 子节点连接时不报缺失 [@380kkm 2026-06-05] ####
def test_bplisp_required_pin_connected_is_clean():
    adhoc = {"bplisp": {"branch": {"pins": {"cond": {"required": True}}}}}
    src = "(function None (branch :cond (IsValid)))"
    assert "MISSING_REQUIRED_PIN" not in _codes(src, "bplisp", adhoc)


#### 验证无 schema 路径与三参传 None 的结果完全相同（--schema 门控） [@380kkm 2026-06-05] ####
def test_animlang_no_schema_byte_identical():
    assert (V.dsl_validate(GOOD_ANIMLANG_SEMANTIC, "animlang")
            == V.dsl_validate(GOOD_ANIMLANG_SEMANTIC, "animlang", None))


#### 验证合法 animlang 加 schema 不引入任何 error [@380kkm 2026-06-05] ####
def test_animlang_good_inline_no_errors():
    assert _codes(GOOD_ANIMLANG_SEMANTIC, "animlang", _animlang_schema(), "error") == []


#### 三个真实 animlang 示例对样例 schema 零语义问题（设计稿方言） [@380kkm 2026-06-05] ####
_REF = r"W:\cc\reference"
_REAL_ANIMLANG = [
    os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "simple_blend.animlang"),
    os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "state_machine.animlang"),
    os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "third_person_char.animlang"),
]


#### 验证每个真实 animlang 示例对样例 schema 零语义问题且无 error [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("path", _REAL_ANIMLANG)
def test_animlang_real_examples_zero_semantic(path):
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert _sem(text, "animlang", _animlang_schema()) == []
    assert [i for i in V.dsl_validate(text, "animlang", _animlang_schema())
            if i.severity == "error"] == []


#### 验证未知 pose 节点类型被判 UNKNOWN_NODE_TYPE warning [@380kkm 2026-06-05] ####
def test_animlang_unknown_node_type_is_warning():
    src = '(anim-blueprint "X" :anim-graph (frobnicate "Y" :loop true))'
    issues = V.dsl_validate(src, "animlang", _animlang_schema())
    unk = [i for i in issues if i.code == "UNKNOWN_NODE_TYPE"]
    assert len(unk) == 1 and unk[0].severity == "warning"
    assert "frobnicate" in unk[0].message


#### 验证严格 form（sequence-player）上的属性拼写错误被判 warning [@380kkm 2026-06-05] ####
def test_animlang_unknown_prop_on_strict_leaf_is_warning():
    src = '(anim-blueprint "X" :anim-graph (sequence-player "Idle" :looop true))'
    issues = V.dsl_validate(src, "animlang", _animlang_schema())
    unk = [i for i in issues if i.code == "UNKNOWN_PROP"]
    assert len(unk) == 1 and unk[0].severity == "warning"
    assert ":looop" in unk[0].message
    assert "UNKNOWN_PROP" not in _codes(src, "animlang")


#### 验证非严格 form（state/transition 的位置关键字状态名）不误报 UNKNOWN_PROP [@380kkm 2026-06-05] ####
def test_animlang_positional_state_refs_not_flagged():
    # state-machine / state / transition 用前导位置关键字做标识符（任意状态名），
    # 它们与命名属性形状不可区分 -> 这些 form 不开 strict-props，绝不应误报
    assert "UNKNOWN_PROP" not in _codes(GOOD_ANIMLANG_SEMANTIC, "animlang", _animlang_schema())


#### 验证导出器方言 (B) 节点被识别（external 但 known，不报未知类型） [@380kkm 2026-06-05] ####
def test_animlang_exporter_dialect_node_known():
    src = '(anim-blueprint "X" :anim-graph (blendspace-player :name "BS" :loop true))'
    assert "UNKNOWN_NODE_TYPE" not in _codes(src, "animlang", _animlang_schema())


#### 验证必需引脚缺失被判 error（内联 ad-hoc schema；自带 schema 全 required:false） [@380kkm 2026-06-05] ####
def test_animlang_missing_required_pin_via_adhoc_schema():
    adhoc = {"animlang": {"blend": {"pins": {"a": {"required": True}}}}}
    src = '(anim-blueprint "X" :anim-graph (blend :alpha 0.5))'
    issues = V.dsl_validate(src, "animlang", adhoc)
    miss = [i for i in issues if i.code == "MISSING_REQUIRED_PIN"]
    assert len(miss) == 1 and miss[0].severity == "error"
    assert ":a" in miss[0].message
    assert "MISSING_REQUIRED_PIN" not in _codes(src, "animlang")


#### 验证 ad-hoc 必需 pose 引脚已用子节点连接时不报缺失 [@380kkm 2026-06-05] ####
def test_animlang_required_pose_pin_connected_is_clean():
    adhoc = {"animlang": {"blend": {"pins": {"a": {"required": True}}}}}
    src = '(anim-blueprint "X" :anim-graph (blend :alpha 0.5 :a (sequence-player "I" :loop true)))'
    assert "MISSING_REQUIRED_PIN" not in _codes(src, "animlang", adhoc)


#### 验证 bplisp/animlang 语义校验跨多次运行确定且结果有序 [@380kkm 2026-06-05] ####
def test_deterministic_and_sorted():
    bsrc = '(function None (while-loop) (set X 1 :idd "a"))'
    bsch = _bplisp_schema()
    a = V.dsl_validate(bsrc, "bplisp", bsch)
    b = V.dsl_validate(bsrc, "bplisp", bsch)
    assert a == b and a == sorted(a, key=lambda i: i.sort_key())

    asrc = '(anim-blueprint "X" :anim-graph (frobnicate "Y" :looop 1))'
    asch = _animlang_schema()
    c = V.dsl_validate(asrc, "animlang", asch)
    d = V.dsl_validate(asrc, "animlang", asch)
    assert c == d and c == sorted(c, key=lambda i: i.sort_key())


#### 验证两条新 schema 形状合法可加载（load_schema 不因 strict-props/external 报错） [@380kkm 2026-06-05] ####
def test_new_schemas_load():
    bsch = _bplisp_schema()
    asch = _animlang_schema()
    assert "bplisp" in bsch and "set" in bsch["bplisp"]
    assert bsch["bplisp"]["set"]["strict-props"] is True
    assert "animlang" in asch and "sequence-player" in asch["animlang"]
    assert asch["animlang"]["sequence-player"]["strict-props"] is True
    # external 标记的 form 仍是合法 spec（多余键被 load_schema 忽略）
    assert asch["animlang"]["transition"]["external"] is True
