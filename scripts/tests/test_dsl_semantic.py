"""预检 DSL 校验器（scripts/dsl_validate.py）可选语义层（schema / 类型字典）的测试。

结构层由 test_dsl_validate.py 覆盖（那 20 个测试以无 schema 方式调用 dsl_validate(text, lang)，
必须保持逐字节一致 —— 也要一并重跑）。本套件仅覆盖叠加其上的、由 schema 驱动的语义趟：

  * 无 schema 路径与纯结构结果逐字节一致；
  * 内置的 matlang.sample.json 使两个真实示例零错误；
  * 精心构造的坏文件以正确的 code + 严重度暴露 MISSING_REQUIRED_PIN / UNKNOWN_PROP /
    UNKNOWN_NODE_TYPE；
  * 语义结果跨多次运行确定；
  * 畸形 schema 抛出干净的 ValueError（不崩溃），且 CLI 以 2 退出。

带 tree-sitter 依赖、从 scripts/ 运行，例如：
    cd scripts && uv run --python 3.12 --with pytest --with "tree-sitter>=0.23" \
        --with tree-sitter-language-pack -m pytest tests/test_dsl_semantic.py -q
"""
import json
import os
import sys

import pytest

# 把 scripts/ 加入路径
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
try:
    import dsl_validate as V
    _HAVE = True
except Exception:  # noqa: BLE001 - tree-sitter 未安装时干净跳过
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")

_SCHEMA_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "schemas", "matlang.sample.json"))


#### 加载内置的 matlang 样例 schema [@380kkm 2026-06-05] ####
def _schema():
    return V.load_schema(_SCHEMA_PATH)


#### 带 schema 收集校验结果的错误码（可选按严重度过滤），排序返回 [@380kkm 2026-06-05] ####
def _codes(text, lang, schema=None, sev=None):
    return sorted(i.code for i in V.dsl_validate(text, lang, schema)
                  if sev is None or i.severity == sev)


#### 合法 matlang 内联夹具（与结构套件镜像一致） [@380kkm 2026-06-05] ####
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


#### 验证无 schema 路径与三参传 None 的结果完全相同 [@380kkm 2026-06-05] ####
def test_no_schema_byte_identical_to_two_arg():
    # 第三个位置参数默认 None；两参与三参（None）的结果必须是相同对象（已有的 20 个测试依赖此点）
    two_arg = V.dsl_validate(_GOOD_MATLANG, "matlang")
    three_none = V.dsl_validate(_GOOD_MATLANG, "matlang", None)
    assert two_arg == three_none


#### 验证为合法文件加上样例 schema 不引入任何 error [@380kkm 2026-06-05] ####
def test_schema_adds_no_errors_to_good_inline():
    assert _codes(_GOOD_MATLANG, "matlang", _schema(), "error") == []


#### 真实 matlang 示例文件清单（用于零语义错误验证） [@380kkm 2026-06-05] ####
_REF = r"W:\cc\reference"
_REAL_MATLANG = [
    os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "simple_pbr.matlang"),
    os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "emissive_rim.matlang"),
]


#### 验证每个真实示例对样例 schema 校验出零语义错误 [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("path", _REAL_MATLANG)
def test_real_examples_zero_semantic_errors(path):
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    issues = V.dsl_validate(text, "matlang", _schema())
    # 样例 schema 是从这些文件推断而来 -> 零语义 error 且零语义 warning（每个出现的类型 +
    # 属性都在字典里）
    semantic_codes = {"UNKNOWN_NODE_TYPE", "UNKNOWN_PROP", "MISSING_REQUIRED_PIN"}
    sem = [i for i in issues if i.code in semantic_codes]
    assert sem == [], f"{path}: unexpected semantic issues {sem}"
    assert [i for i in issues if i.severity == "error"] == []


#### 验证缺失必需引脚被判为 MISSING_REQUIRED_PIN 错误，无 schema 时则不报 [@380kkm 2026-06-05] ####
def test_missing_required_pin_is_error():
    # component-mask 无 :input -> 那个真正必需（无常量回退）的引脚未连 -> MISSING_REQUIRED_PIN 错误
    bad = ('(material "M" (expressions'
           ' (component-mask $cm1 :mask "rg"))'
           " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    miss = [i for i in issues if i.code == "MISSING_REQUIRED_PIN"]
    assert len(miss) == 1
    assert miss[0].severity == "error"
    assert ":input" in miss[0].message
    # 无 schema 时同一文件没有 MISSING_REQUIRED_PIN（纯结构）
    assert "MISSING_REQUIRED_PIN" not in _codes(bad, "matlang")


#### 验证必需引脚已连接时不报 MISSING_REQUIRED_PIN [@380kkm 2026-06-05] ####
def test_required_pin_connected_is_clean():
    # component-mask 的 :input 已连接 -> 无 MISSING_REQUIRED_PIN
    ok = ('(material "M" (expressions'
          " (constant3-vector $v1 :value (1.0 0.0 0.0))"
          ' (component-mask $cm1 :mask "rg" :input (connect $v1 0)))'
          " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


#### 验证 multiply 的可选引脚未连接不算错误（回归守卫） [@380kkm 2026-06-05] ####
def test_multiply_optional_pin_unconnected_is_not_error():
    # 针对审计标记的误报的回归守卫：multiply.{a,b} 为 required:false（UE ConstA/ConstB 回退）。
    # 真实回环的材质把 :b 留在常量默认值时会导出 `(multiply $m :a (connect ...))` 而无 :b 连线
    # -> 这是合法的，绝不可触发 MISSING_REQUIRED_PIN
    ok = ('(material "M" (expressions'
          " (constant $c1 :value 1.0)"
          " (multiply $m1 :a (connect $c1 0)))"
          " (outputs :base-color (connect $m1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}
    assert [i for i in issues if i.severity == "error"] == []
    # 即便 multiply 既无 :a 也无 :b 也不是语义错误（两者皆可选）
    ok2 = ('(material "M" (expressions (multiply $m1))'
           " (outputs :base-color (connect $m1 0)))")
    issues2 = V.dsl_validate(ok2, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues2}


#### 验证未知属性被判为 warning（非 error），且必需引脚已连时不报缺失 [@380kkm 2026-06-05] ####
def test_unknown_prop_is_warning():
    # :clamp-result 既非 multiply 的已知属性也非其引脚 -> warning，而非 error
    bad = ('(material "M" (expressions'
           " (constant $c1 :value 1.0) (constant $c2 :value 2.0)"
           " (multiply $m1 :a (connect $c1 0) :b (connect $c2 0) :clamp-result true))"
           " (outputs :base-color (connect $m1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    unk = [i for i in issues if i.code == "UNKNOWN_PROP"]
    assert len(unk) == 1
    assert unk[0].severity == "warning"
    assert ":clamp-result" in unk[0].message
    # 必需引脚均已连接 -> 此处无 MISSING_REQUIRED_PIN
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


#### 验证未知节点类型被判为 warning（部分字典中绝不报 error） [@380kkm 2026-06-05] ####
def test_unknown_node_type_is_warning():
    # 'panner' 是真实 UE 类型，但不在这份部分样例 schema 里 -> warning
    bad = ('(material "M" (expressions'
           " (panner $p1 :speed-x 0.1)"
           " (constant $c1 :value 1.0))"
           " (outputs :base-color (connect $c1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    unk = [i for i in issues if i.code == "UNKNOWN_NODE_TYPE"]
    assert len(unk) == 1
    assert unk[0].severity == "warning"
    assert "panner" in unk[0].message
    # 部分字典中未知类型绝不是 error
    assert all(i.severity != "error" for i in issues if i.code == "UNKNOWN_NODE_TYPE")


#### 验证可选引脚未连接不被标记 [@380kkm 2026-06-05] ####
def test_optional_pin_unconnected_is_not_flagged():
    # texture-sample.uv 为 required:false -> 不连它没问题（UE 回退到默认网格 UV），无 MISSING_REQUIRED_PIN
    ok = ('(material "M" (expressions'
          ' (texture-sample $t1 :texture (asset "/Game/T")))'
          " (outputs :base-color (connect $t1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


#### 验证缺省的可选属性不被标记（增量序列化不变量） [@380kkm 2026-06-05] ####
def test_absent_optional_property_not_flagged():
    # fresnel 无 :exponent / :base-reflect-fraction -> 缺省即等于 CDO 默认值，绝不是 error 或 warning
    ok = ('(material "M" (expressions'
          " (fresnel $f1)"
          " (constant $c1 :value 1.0))"
          " (outputs :base-color (connect $c1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    sem = [i for i in issues
           if i.code in ("UNKNOWN_PROP", "MISSING_REQUIRED_PIN")]
    assert sem == []


#### 验证 material 根属性与 outputs 块槽位关键字不被误报 [@380kkm 2026-06-05] ####
def test_material_root_props_not_flagged():
    # 'material' 根经其自身 schema 条目校验；其头部关键字（:domain 等）是已知属性而非 UNKNOWN_PROP，
    # 且 outputs 块的槽位关键字（:base-color 等）位于一个被跳过的非节点行上
    issues = V.dsl_validate(_GOOD_MATLANG, "matlang", _schema())
    assert "UNKNOWN_PROP" not in {i.code for i in issues}
    assert "UNKNOWN_NODE_TYPE" not in {i.code for i in issues}


#### 验证无值关键字不会误吞下一个引脚（防御性配对守卫） [@380kkm 2026-06-05] ####
def test_value_less_keyword_does_not_misclassify_next_pin():
    # 防御性配对守卫：一个畸形的无值关键字（:mask 无值，紧跟 :input）不得把 :input 当作其值吞掉。
    # :input 仍是被识别（已连接）的引脚 -> 无伪 MISSING_REQUIRED_PIN，且 :mask 记为（已知）属性
    src = ('(material "M" (expressions'
           " (constant3-vector $v1 :value (1.0 0.0 0.0))"
           " (component-mask $cm1 :mask :input (connect $v1 0)))"
           " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(src, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


#### 验证语义校验跨多次运行确定且结果有序 [@380kkm 2026-06-05] ####
def test_semantic_deterministic():
    bad = ('(material "M" (expressions'
           " (multiply $m1 :extra1 1 :extra2 2)"
           " (panner $p1)"
           " (constant $c1 :value 1.0))"
           " (outputs :base-color (connect $c1 0)))")
    sch = _schema()
    a = V.dsl_validate(bad, "matlang", sch)
    b = V.dsl_validate(bad, "matlang", sch)
    assert a == b
    assert a == sorted(a, key=lambda i: i.sort_key())


#### 验证加载合法 schema 后字段形状符合预期 [@380kkm 2026-06-05] ####
def test_load_schema_good():
    sch = _schema()
    assert "matlang" in sch
    # multiply.{a,b} 为可选（UE ConstA/ConstB 回退）；component-mask.input 是那个真正必需
    # （无常量回退）的引脚
    assert sch["matlang"]["multiply"]["pins"]["a"]["required"] is False
    assert sch["matlang"]["multiply"]["pins"]["b"]["required"] is False
    assert sch["matlang"]["component-mask"]["pins"]["input"]["required"] is True


#### 验证根不是对象的 schema 抛 ValueError [@380kkm 2026-06-05] ####
def test_load_schema_root_not_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


#### 验证语言值类型非法的 schema 抛 ValueError [@380kkm 2026-06-05] ####
def test_load_schema_bad_lang_value(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"matlang": 42}', encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


#### 验证 pins 形状非法的 schema 抛 ValueError [@380kkm 2026-06-05] ####
def test_load_schema_bad_pins_shape(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"matlang": {"multiply": {"pins": {"a": {"required": "yes"}}}}}',
                 encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


#### 验证非法 JSON 的 schema 抛 JSONDecodeError [@380kkm 2026-06-05] ####
def test_load_schema_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        V.load_schema(str(p))


#### 验证顶层 '$' 前缀的元数据键不触发形状校验 [@380kkm 2026-06-05] ####
def test_load_schema_allows_metadata_keys(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text('{"$schema_note": "hi", "matlang": {"multiply": {"pins": {}}}}',
                 encoding="utf-8")
    sch = V.load_schema(str(p))
    assert "matlang" in sch


#### 验证 CLI 遇畸形 schema 打印干净错误并以 2 退出（非回溯） [@380kkm 2026-06-05] ####
def test_cli_malformed_schema_exits_2(tmp_path, capsys):
    src = tmp_path / "m.matlang"
    src.write_text('(material "M" (expressions (constant $c :value 1.0))'
                   " (outputs :base-color (connect $c 0)))", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = V.main([str(src), "--schema", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "malformed schema" in err


#### 验证 CLI 端到端：合法文件 + 样例 schema -> 退出 0 [@380kkm 2026-06-05] ####
def test_cli_with_schema_runs_semantic(tmp_path):
    src = tmp_path / "m.matlang"
    src.write_text(_GOOD_MATLANG, encoding="utf-8")
    rc = V.main([str(src), "--schema", _SCHEMA_PATH])
    assert rc == 0
