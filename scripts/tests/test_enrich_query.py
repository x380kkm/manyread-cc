# audience: internal
# tests.test_enrich_query
"""enrich 中声明式依赖边查询层的回归测试。"""
import os
import sys

import pytest

# scripts/
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
try:
    import enrich_treesitter as E
    from tree_sitter import Parser, Query
    from tree_sitter_language_pack import get_language
    _HAVE = True
except Exception:  # noqa: BLE001
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
    _rows, edges = _edges("from pkg.mod import thing\nx = helper()\n")
    assert not edges


#### 查询出的边对同一输入是确定性的 [@380kkm 2026-06-05] ####
def test_query_edges_deterministic():
    src = "def f(a: T):\n    return g(a)\n"
    _r1, e1 = _edges(src)
    _r2, e2 = _edges(src)
    assert e1 == e2


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


#### 用遍历器语言的内建 .scm 预设取 (rows, edges) 的夹具 [@380kkm 2026-06-05] ####
def _lang_edges(src: str, lang: str):
    L = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    assert lang in specs, f"missing built-in preset for {lang}"
    return E._extract_file(1, src, lang, Parser(L), False, Query(L, specs[lang]))


#### 取限定在 `relations` 内的 (封闭符号名, 关系, dst_name) 集合 [@380kkm 2026-06-05] ####
def _rel_pairs(rows, edges, relations):
    by_local = {r["_local"]: r["name"] for r in rows}
    return {(by_local[e["src_local"]], e["relation"], e["dst_name"])
            for e in edges if e["relation"] in relations}


_JS_SRC = (
    # 模块作用域：被丢弃（无封闭符号）
    'import topdep from "./top.js";\n'
    "function loader() {\n"
    # require -> calls 'require' 且 imports 'node:fs'
    '    const fs = require("node:fs");\n'
    # 自由调用
    "    helper(loader);\n"
    # 成员调用 -> 'read'
    "    return obj.read(fs);\n"
    "}\n"
    # extends 来自遍历器，而非 .scm
    "class Widget extends Base {\n"
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
    _rows, edges = _lang_edges('import x from "pkg";\n', "javascript")
    assert not edges


_TS_SRC = (
    # extends/implements -> 遍历器
    "class Circle extends Base implements Shape {\n"
    # 返回类型 -> uses_type Box
    "    area(): Box {\n"
    # require -> imports 'legacy'，不是 call
    '        const legacy = require("legacy");\n'
    # calls helper + doThing（仅末段属性）
    "        return helper(this.svc.doThing());\n"
    "    }\n"
    "}\n"
    # 参数 Vec3 + 返回 Result -> uses_type
    "function compute(a: Vec3): Result {\n"
    # 变量类型 Color -> uses_type；make -> calls
    "    const col: Color = make(a);\n"
    # new T -> uses_type Widget
    "    return new Widget();\n"
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


_CS_SRC = (
    # 模块作用域 using -> 丢弃（无符号）
    "using System;\n"
    # 别名 'Alias' 绝不能成为 import
    "using Alias = Some.Long.Name;\n"
    "namespace MyApp {\n"
    # base_list -> 遍历器 extends/implements
    "  public class Widget : BaseWidget, IDisposable {\n"
    # 字段类型 -> uses_type（封闭者：class）
    "    private Helper _helper;\n"
    # 参数 + 返回类型 -> uses_type
    "    public Result DoWork(Config cfg) {\n"
    # 对象创建类型 -> uses_type
    "      var sb = new StringBuilder();\n"
    # 成员调用 -> WriteLine
    '      Console.WriteLine("hi");\n'
    # 成员调用 -> Process
    "      _helper.Process(cfg);\n"
    # 自由调用 Compute + new Result
    "      return new Result(Compute());\n"
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


#### 隔离库内 js/csharp 全链路冒烟：依赖边落入 edges 表且 hub 隔离 [@380kkm 2026-06-05] ####
def test_real_fixture_smoke_js_csharp_isolated(tmp_path, monkeypatch):
    import sqlite3
    import index_build
    from lib import config

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    # 隔离 hub 及任何环境中的库发现
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
    assert E._strip_decl_macros(_CPP_GOLDEN_SRC, _MS_DEFAULT) == _CPP_GOLDEN_SRC
    rows, edges = E._extract_file(
        1, _CPP_GOLDEN_SRC, "cpp", Parser(get_language("cpp")), False, None, _MS_DEFAULT)
    assert rows == _CPP_GOLDEN_ROWS and edges == _CPP_GOLDEN_EDGES


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
