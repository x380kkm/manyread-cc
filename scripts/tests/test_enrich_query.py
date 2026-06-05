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

from cpp_golden import CPP_GOLDEN_EDGES, CPP_GOLDEN_ROWS, CPP_GOLDEN_SRC  # noqa: E402


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
    ts_lang = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    q = Query(ts_lang, specs[lang]) if lang in specs else None
    return E._extract_file(1, src, lang, Parser(ts_lang), False, q)


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
    rows, edges = _walker_extract(CPP_GOLDEN_SRC, "cpp")
    assert rows == CPP_GOLDEN_ROWS
    assert edges == CPP_GOLDEN_EDGES


#### python 遍历器输出与 golden 逐字节一致 [@380kkm 2026-06-05] ####
def test_python_walker_byte_identical():
    rows, edges = _walker_extract(_PY_GOLDEN_SRC, "python")
    assert rows == _PY_GOLDEN_ROWS
    assert edges == _PY_GOLDEN_EDGES


#### 用遍历器语言的内建 .scm 预设取 (rows, edges) 的夹具 [@380kkm 2026-06-05] ####
def _lang_edges(src: str, lang: str):
    ts_lang = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    assert lang in specs, f"missing built-in preset for {lang}"
    return E._extract_file(1, src, lang, Parser(ts_lang), False, Query(ts_lang, specs[lang]))


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
    ts_lang = get_language(E._PACK_NAME["csharp"])
    specs = E._load_query_specs(None)
    q = Query(ts_lang, specs["csharp"])
    from tree_sitter import QueryCursor
    tree = Parser(ts_lang).parse("using Alias = Some.Long.Name;\nusing System;\n".encode("utf-8"))
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
