# audience: internal
# manyscan.tests.test_modules_spec
"""manyscan.lib.boundary.modulespec 的测试 —— N 路分区原语。

核心：退化等价 —— 一个 2-zone ModuleSpec 在一组路径语料上与二进制 zone_of_path 逐路径一致
（含 target_root=="" 整仓库特例、None 路径、反斜杠路径）。外加最长匹配、exclude、内联解析。
"""
from __future__ import annotations

from lib import boundary
from lib.boundary import modulespec as ms


#### 用一个 target_root 造出与 zone_of_path 等价的 2-zone 规格 [@380kkm 2026-06-05] ####
def _degenerate(target_root: str) -> ms.ModuleSpec:
    # target_root=="" => include 为空串（匹配一切），复刻 zone_of_path 整仓库特例
    doc = {"version": 1, "fallback": boundary.DEPENDENCY,
           "zones": [{"name": boundary.TARGET, "include": [target_root]}]}
    return ms.make_module_spec(doc)


#### 一组覆盖归一化/边界/None/反斜杠/前缀近邻的路径语料 [@380kkm 2026-06-05] ####
_CORPUS = [
    "plugin/Foo.cpp", "plugin", "plugin/", ".\\plugin\\Bar.h", "engine/Core.h",
    "pluginX/Foo.cpp", None, "Plugin/Case.h", "a/b/c.cpp", "plugin/sub/deep.h",
    "./plugin/x.cpp", "engine\\Sub\\y.h", "",
]


#### 退化等价：2-zone 规格在语料上逐路径等于 zone_of_path（多个 target_root） [@380kkm 2026-06-05] ####
def test_module_of_path_equals_zone_of_path():
    for tr in ("plugin", "", "engine/Sub", "a/b"):
        z = boundary.Zoning(target_root=boundary.norm_root(tr))
        spec = _degenerate(tr)
        for p in _CORPUS:
            zone = boundary.zone_of_path(p, z)
            mod = ms.module_of_path(p, spec)
            # zone_of_path 返回 TARGET/DEPENDENCY；spec 返回 zone 名（恰为同一对常量）
            assert mod == zone, f"tr={tr!r} path={p!r}: spec={mod!r} != binary={zone!r}"


#### target_root=="" 整仓库特例：所有非 None 路径都归目标 [@380kkm 2026-06-05] ####
def test_empty_include_matches_whole_repo():
    spec = _degenerate("")
    assert ms.module_of_path("anything/here.cpp", spec) == boundary.TARGET
    assert ms.module_of_path("x", spec) == boundary.TARGET
    # None 仍归兜底
    assert ms.module_of_path(None, spec) == boundary.DEPENDENCY


#### 最长匹配：重叠 include 由更深前缀的 zone 胜出 [@380kkm 2026-06-05] ####
def test_longest_match_wins_on_overlap():
    doc = {"version": 1, "fallback": "Ext", "zones": [
        {"name": "Runtime", "include": ["Engine/Source/Runtime"]},
        {"name": "Core", "include": ["Engine/Source/Runtime/Core"]},
    ]}
    spec = ms.make_module_spec(doc)
    assert ms.module_of_path("Engine/Source/Runtime/Core/Obj.h", spec) == "Core"
    assert ms.module_of_path("Engine/Source/Runtime/RHI/Rhi.h", spec) == "Runtime"
    assert ms.module_of_path("Engine/Source/Other.h", spec) == "Ext"


#### 等长 include 平局由声明序确定（先声明者胜） [@380kkm 2026-06-05] ####
def test_equal_length_tiebreak_by_decl_order():
    # 两个等长前缀分属不同 zone，但路径只可能落入一个目录，故造真正重叠：同前缀
    doc = {"version": 1, "fallback": "Ext", "zones": [
        {"name": "First", "include": ["a/b"]},
        {"name": "Second", "include": ["a/b"]},
    ]}
    spec = ms.make_module_spec(doc)
    # (-len, prefix, decl_order) 全序下 decl_order=0 的 First 胜
    assert ms.module_of_path("a/b/x.cpp", spec) == "First"


#### exclude 把命中 include 的路径踢回兜底 [@380kkm 2026-06-05] ####
def test_exclude_subtracts_from_zone():
    doc = {"version": 1, "fallback": "Ext", "zones": [
        {"name": "Game", "include": ["a/Engine"], "exclude": ["a/Engine/Tests"]},
    ]}
    spec = ms.make_module_spec(doc)
    assert ms.module_of_path("a/Engine/Actor.h", spec) == "Game"
    # 被 exclude 的子树落回兜底（不会再被更短 include 接住，因同 zone）
    assert ms.module_of_path("a/Engine/Tests/T.h", spec) == "Ext"


#### glob exclude 排除匹配 fnmatch 的路径 [@380kkm 2026-06-05] ####
def test_glob_exclude():
    doc = {"version": 1, "fallback": "Ext", "zones": [
        {"name": "Src", "include": ["a"], "glob": ["*/gen/*"]},
    ]}
    spec = ms.make_module_spec(doc)
    assert ms.module_of_path("a/x.cpp", spec) == "Src"
    assert ms.module_of_path("a/gen/y.cpp", spec) == "Ext"


#### 内联 --module 解析为 (name, prefixes) [@380kkm 2026-06-05] ####
def test_parse_inline_module():
    assert ms.parse_inline_module("Core=Engine/Core,Engine/CoreUObject") == (
        "Core", ["Engine/Core", "Engine/CoreUObject"])
    assert ms.parse_inline_module("A=x") == ("A", ["x"])


#### 内联解析在缺 = / 空名 / 无前缀时抛 ValueError [@380kkm 2026-06-05] ####
def test_parse_inline_module_bad():
    import pytest
    for bad in ("noeq", "=x", "A=", "A=,,"):
        with pytest.raises(ValueError):
            ms.parse_inline_module(bad)


#### 内联 zone 合并到文件规格：同名追加 include，异名追加 zone [@380kkm 2026-06-05] ####
def test_make_spec_merges_inline():
    doc = {"version": 1, "fallback": "Ext", "zones": [{"name": "Core", "include": ["a/Core"]}]}
    spec = ms.make_module_spec(doc, inline=[("Core", ["a/Core2"]), ("New", ["a/New"])])
    names = [z.name for z in spec.zones]
    assert names == ["Core", "New"]
    assert ms.module_of_path("a/Core2/x.cpp", spec) == "Core"
    assert ms.module_of_path("a/New/y.cpp", spec) == "New"


#### fallback 显式覆盖优先于 doc.fallback [@380kkm 2026-06-05] ####
def test_fallback_override():
    doc = {"version": 1, "fallback": "DocFb", "zones": [{"name": "A", "include": ["x"]}]}
    spec = ms.make_module_spec(doc, fallback="CliFb")
    assert spec.fallback == "CliFb"
    assert ms.module_of_path("z/y.cpp", spec) == "CliFb"


#### like_prefix 转义 % _ \ 供 ESCAPE '\\' 的 LIKE 前缀使用 [@380kkm 2026-06-05] ####
def test_like_prefix_escapes():
    assert ms.like_prefix("a/b") == "a/b"
    assert ms.like_prefix("a_b") == r"a\_b"
    assert ms.like_prefix("a%b") == r"a\%b"
    assert ms.like_prefix(r"a\b") == "a\\\\b"
