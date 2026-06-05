# audience: internal
# tests.test_macro_strip
"""enrich 中 cpp 声明修饰符宏剥离变换（_strip_decl_macros）的回归测试。"""
import os
import sys

import pytest

# scripts/
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
try:
    import enrich_treesitter as E
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    _HAVE = True
except Exception:  # noqa: BLE001
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")

from cpp_golden import CPP_GOLDEN_EDGES, CPP_GOLDEN_ROWS, CPP_GOLDEN_SRC  # noqa: E402

_MS_DEFAULT = {"enabled": True, "extra_names": [], "extra_patterns": []}


#### 用真实 cpp grammar 取 (有错误, 类/结构体名, 主体存在) 的夹具 [@380kkm 2026-06-05] ####
def _cpp_class(src: str):
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
    ("class BASE_EXPORT Foo {};", "Foo"),
    ("struct PROTOBUF_EXPORT Bar { int x; };", "Bar"),
    # 带实参的宏
    ("class UE_DEPRECATED(5.0) UOld {};", "UOld"),
    ("class CV_EXPORTS Mat { int rows; };", "Mat"),
    ("class ENGINE_API UMaterial final : public X {};", "UMaterial"),
    ("template<typename T> class ENGINE_API TFoo {};", "TFoo"),
])
def test_macro_strip_recovers_real_name(src, real_name):
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
    # 普通类
    ("class Baz {};"),
    # 全大写名，无第二个标识符
    ("class RGBA {};"),
    ("struct UPPER {};"),
    # 干净的继承
    ("class Foo : public Bar { int a; void f(){} };"),
    # 宏在基类列表里，不在名位置
    ("class Foo : public BAR_API Base {};"),
    # 形似宏的名，在取值位置
    ("int ENGINE_API_VERSION = 3;"),
    # 函数返回修饰符（不在处理范围）
    ("void ENGINE_API Foo();"),
    ("enum class EColor { Red };"),
    # 作为用户标识符子串的 class/struct 不命中
    ("subclass ENGINE_API Foo x;"),
    ("metaclass FOO_API Bar y;"),
    ("mystruct ENGINE_API_T xVar;"),
    ("superclass DLL_API Baz q;"),
])
def test_macro_strip_negative_byte_identical(src):
    assert E._strip_decl_macros(src, _MS_DEFAULT) == src


#### 堆叠的声明修饰符宏被完全剥离（不动点） [@380kkm 2026-06-05] ####
@pytest.mark.parametrize("src,real_name", [
    ("class DLL_EXPORT ENGINE_API UMaterial { int A; void F(){} };", "UMaterial"),
    ("struct BASE_EXPORT PROTOBUF_EXPORT Bar {};", "Bar"),
    ("class A_API B_API C_API UFoo {};", "UFoo"),
])
def test_macro_strip_stacked_macros_recovered(src, real_name):
    stripped = E._strip_decl_macros(src, _MS_DEFAULT)
    assert len(stripped) == len(src)
    err, name, has_body = _cpp_class(stripped)
    assert name == real_name and not err and has_body


#### `enum class <MACRO> <Name>` 恢复 enum 的真实名 [@380kkm 2026-06-05] ####
def test_macro_strip_enum_class_macro_recovered():
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
    assert "cpp" in E._CFAMILY_STRIP_LANGS
    for lang in ("python", "javascript", "typescript", "csharp", "glsl", "java"):
        assert lang not in E._CFAMILY_STRIP_LANGS
    pysrc = "class A(Base):\n    def m(self): return 1\n"
    on = E._extract_file(1, pysrc, "python", Parser(get_language("python")), False, None, _MS_DEFAULT)
    off = E._extract_file(1, pysrc, "python", Parser(get_language("python")), False, None, {"enabled": False})
    assert on == off


#### 已提交的 cpp golden 无修饰符宏，默认开启的剥离对其是 no-op [@380kkm 2026-06-05] ####
def test_macro_strip_golden_cpp_unchanged():
    assert E._strip_decl_macros(CPP_GOLDEN_SRC, _MS_DEFAULT) == CPP_GOLDEN_SRC
    rows, edges = E._extract_file(
        1, CPP_GOLDEN_SRC, "cpp", Parser(get_language("cpp")), False, None, _MS_DEFAULT)
    assert rows == CPP_GOLDEN_ROWS and edges == CPP_GOLDEN_EDGES


#### config.load_macro_strip：缺省=>开启、可禁用、可扩展、畸形=>默认+告警 [@380kkm 2026-06-05] ####
def test_macro_strip_config_load(tmp_path):
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
