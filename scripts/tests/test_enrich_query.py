"""Regression tests for the declarative dependency-edge query layer in enrich.

Run from the scripts/ dir WITH the tree-sitter deps, e.g.:
    cd scripts && uv run --python 3.12 --with pytest --with "tree-sitter>=0.23" \
        --with tree-sitter-language-pack -m pytest tests/test_enrich_query.py -q
(It lives outside scripts/manyscan/tests because enrich imports the manyread-core
`lib` package, which would shadow manyscan's own `lib` in that suite's sys.path.)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))  # scripts/
try:
    import enrich_treesitter as E
    from tree_sitter import Parser, Query
    from tree_sitter_language_pack import get_language
    _HAVE = True
except Exception:  # noqa: BLE001 - skip cleanly when tree-sitter isn't installed
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")


def test_simplify_dep():
    assert E._simplify_dep("list[str] | None") == "list"   # union -> first, strip generics
    assert E._simplify_dep("module.Foo") == "Foo"           # qualifier -> last
    assert E._simplify_dep("Outer::Inner") == "Inner"
    assert E._simplify_dep("TArray<FString>") == "TArray"
    assert E._simplify_dep("Foo | Bar") == "Foo"


def test_builtin_python_query_loads_and_compiles():
    specs = E._load_query_specs(None)
    assert "python" in specs and "@dep.calls" in specs["python"]
    Query(get_language("python"), specs["python"])          # compiles against the grammar


def test_project_override_wins(tmp_path):
    d = tmp_path / ".manyread" / "queries"
    d.mkdir(parents=True)
    (d / "python.scm").write_text("(call function: (identifier) @dep.calls)\n", encoding="utf-8")
    specs = E._load_query_specs(tmp_path)
    assert specs["python"].strip() == "(call function: (identifier) @dep.calls)"


def _edges(src: str):
    lang = get_language("python")
    q = Query(lang, E._load_query_specs(None)["python"])
    return E._extract_file(1, src, "python", Parser(lang), False, q)


def test_python_edges_end_to_end():
    # NB: every @dep edge is attributed to its ENCLOSING symbol; a top-level statement
    # has none, so module-scope imports/calls are dropped (a file-level node is future
    # work). So the import here is inside the method, where it IS attributed.
    src = ("class A(Base):\n"
           "    def m(self, x: Widget) -> Out:\n"
           "        from pkg.mod import thing\n"
           "        return helper(x)\n")
    _rows, edges = _edges(src)
    pairs = {(e["relation"], e["dst_name"]) for e in edges}
    assert ("calls", "helper") in pairs
    assert ("uses_type", "Widget") in pairs and ("uses_type", "Out") in pairs
    assert ("imports", "mod") in pairs                      # pkg.mod -> last segment
    # inheritance is emitted by the WALKER, not the query -> exactly one extends, no dup
    assert sum(1 for e in edges if e["relation"] == "extends") == 1


def test_module_scope_edges_dropped():
    # documents the known limitation: a module-level import has no enclosing symbol.
    _rows, edges = _edges("from pkg.mod import thing\nx = helper()\n")
    assert not edges


def test_query_edges_deterministic():
    src = "def f(a: T):\n    return g(a)\n"
    _r1, e1 = _edges(src)
    _r2, e2 = _edges(src)
    assert e1 == e2


# ===========================================================================
# UE asset DSL (walker-less, query-driven) symbol + wire extraction.
# These exercise the `lang not in HAS_WALKER` branch of _extract_file: symbols
# come from @def captures (via _query_symbols), wires from @dep captures.
# ===========================================================================
def _dsl_extract(src: str, lang: str):
    """Run _extract_file for a walker-less DSL with its built-in .scm query."""
    L = get_language("scheme")               # all three DSLs share the scheme grammar
    specs = E._load_query_specs(None)
    q = Query(L, specs[lang])
    return E._extract_file(1, src, lang, Parser(L), False, q)


# --- matlang sample text (mirrors DSL/Examples/*.matlang) -------------------
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


def test_matlang_symbols():
    rows, _edges = _dsl_extract(_SIMPLE_PBR, "matlang")
    by_name = {r["name"]: r for r in rows}
    # 1 material + 7 nodes + 1 outputs = 9 symbols.
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
    # parent of every node + the outputs block is the material symbol.
    mat_local = mat["_local"]
    for r in rows:
        if r["kind"] in ("node", "outputs"):
            assert r["parent_local"] == mat_local
    # material/outputs carry NO node_type attr (head==name or kind!=node).
    assert mat["attrs"] == {}
    assert by_name["outputs"]["attrs"] == {}


def _wire_pairs(rows, edges):
    """(src_name, dst_name) set for the matlang `uses_type` wire edges."""
    local_to_name = {r["_local"]: r["name"] for r in rows}
    return {(local_to_name[e["src_local"]], e["dst_name"])
            for e in edges if e["relation"] == "uses_type"}


def test_matlang_wires_simple_pbr():
    rows, edges = _dsl_extract(_SIMPLE_PBR, "matlang")
    assert _wire_pairs(rows, edges) == {
        ("$mul1", "$tex1"), ("$mul1", "$vparam1"),
        ("$tex1", "$uv1"), ("$tex2", "$uv1"),
        ("outputs", "$mul1"), ("outputs", "$tex2"),
        ("outputs", "$const1"), ("outputs", "$sparam1"),
    }
    # contains: material -> every node + outputs.
    local_to_name = {r["_local"]: r["name"] for r in rows}
    contains = {(local_to_name[e["src_local"]], e["dst_name"])
                for e in edges if e["relation"] == "contains"}
    assert contains == {
        ("M_SimplePBR", n) for n in
        ("$tex1", "$tex2", "$uv1", "$vparam1", "$mul1", "$sparam1", "$const1", "outputs")
    }


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


def test_matlang_deterministic():
    r1, e1 = _dsl_extract(_SIMPLE_PBR, "matlang")
    r2, e2 = _dsl_extract(_SIMPLE_PBR, "matlang")
    assert r1 == r2 and e1 == e2


# --- bplisp -----------------------------------------------------------------
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


def test_bplisp_symbols_and_binds():
    rows, edges = _dsl_extract(_VILLAGER, "bplisp")
    graphs = [r for r in rows if r["kind"] == "graph"]
    assert len(graphs) == 1 and graphs[0]["name"] == "function"
    g_local = graphs[0]["_local"]
    nodes = sorted(r["name"] for r in rows if r["kind"] == "node")
    assert nodes == ["let", "let", "set", "set"]
    calls = sorted(r["name"] for r in rows if r["kind"] == "call")
    # only real UFunction calls — the `:param (Selected Actor)` type is NO LONGER
    # mis-captured as a call (the call rule now requires a :pin as the 2nd child).
    assert calls == ["K2_SetTimer", "PrintString", "SpawnSystemAttached"]
    assert "Selected" not in calls  # regression guard: param type is not a call
    # binds dst names (the let/set bound vars). returnvalue/NS_Path have no in-file
    # symbol of that name, so they stay UNRESOLVED (dst_local None at extract time);
    # only matlang $id wires resolve in-file.
    binds = sorted(e["dst_name"] for e in edges if e["relation"] == "binds")
    assert binds == ["NS_Path", "Selected", "returnvalue", "returnvalue"]
    assert all(e["dst_local"] is None for e in edges if e["relation"] == "binds")
    # the top-level statement nodes/calls are contained by the function graph;
    # PrintString/set/let attach directly, while SpawnSystemAttached/K2_SetTimer
    # nest one level deeper under their `let` (innermost enclosing @def).
    contained_under_graph = {e["dst_name"] for e in edges
                             if e["relation"] == "contains" and e["src_local"] == g_local}
    assert {"PrintString", "set", "let"} <= contained_under_graph
    # the pure-call nodes under a `let` are parented to that let, not the graph.
    let_locals = {r["_local"] for r in rows if r["name"] == "let"}
    nested_calls = {e["dst_name"] for e in edges
                    if e["relation"] == "contains" and e["src_local"] in let_locals}
    assert nested_calls == {"SpawnSystemAttached", "K2_SetTimer"}


# --- animlang ---------------------------------------------------------------
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
    # variable type-tags, operators, structural heads are NEVER symbols.
    forbidden = {"float", "bool", ">", "<", "and", "or", "not", "if", "->", "define", "ref"}
    assert not (forbidden & set(node_names))
    # pose tree from `contains`: anim-blueprint -> state-machine -> state -> player.
    local_to_name = {r["_local"]: r["name"] for r in rows}
    contains = {(local_to_name[e["src_local"]], e["dst_name"])
                for e in edges if e["relation"] == "contains"}
    assert ("anim-blueprint", "state-machine") in contains
    assert ("state-machine", "state") in contains
    assert ("state", "sequence-player") in contains


def test_animlang_exporter_form_synthetic():
    # EXPORTER-form (define ...)/(ref ...) — NOT present in the in-repo samples;
    # this pins the best-effort binding + ref-wire behavior against a synthetic
    # snippet only. Re-verify against a real exporter dump before relying on it.
    src = (
        '(anim-blueprint "X"\n'
        "  :anim-graph\n"
        "    (define CachedLeg (two-bone-ik :a 1))\n"
        '  (blend (ref "Get Speed") (CachedLeg)))\n'
    )
    rows, edges = _dsl_extract(src, "animlang")
    bindings = [r for r in rows if r["kind"] == "binding"]
    assert len(bindings) == 1 and bindings[0]["name"] == "CachedLeg"
    # (ref "Get Speed") -> a dep.ref edge. dst_name keeps the quotes (the reused
    # _query_edges runs _simplify_dep, not _dsl_name) and stays UNRESOLVED.
    refs = [e for e in edges if e["relation"] == "ref"]
    assert len(refs) == 1 and refs[0]["dst_name"] == '"Get Speed"'
    # DOCUMENTED behavior: the `(CachedLeg)` reuse becomes a spurious def.node
    # symbol named 'CachedLeg' (the @dep.use post-filter is intentionally omitted).
    node_named_cachedleg = [r for r in rows if r["kind"] == "node" and r["name"] == "CachedLeg"]
    assert len(node_named_cachedleg) == 1


# ===========================================================================
# NO-REGRESSION: walker langs (cpp/python) are BYTE-IDENTICAL with the @def
# addition gated by HAS_WALKER. Goldens captured from the walker output.
# ===========================================================================
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


def test_cpp_walker_byte_identical():
    rows, edges = _walker_extract(_CPP_GOLDEN_SRC, "cpp")
    assert rows == _CPP_GOLDEN_ROWS
    assert edges == _CPP_GOLDEN_EDGES


def test_python_walker_byte_identical():
    rows, edges = _walker_extract(_PY_GOLDEN_SRC, "python")
    assert rows == _PY_GOLDEN_ROWS
    assert edges == _PY_GOLDEN_EDGES


# ===========================================================================
# javascript / typescript / tsx / csharp dependency-edge presets.
# Symbols come from the (already-wired) tree-sitter WALKERS for these langs;
# the new scripts/queries/{javascript,typescript,tsx,csharp}.scm add EDGE-only
# @dep.calls / @dep.imports / @dep.uses_type captures. Same in-memory harness
# as the python case: _extract_file with the built-in preset, no DB.
# ===========================================================================
def _lang_edges(src: str, lang: str):
    """(rows, edges) for a walker lang using its built-in .scm preset."""
    L = get_language(E._PACK_NAME[lang])
    specs = E._load_query_specs(None)
    assert lang in specs, f"missing built-in preset for {lang}"
    return E._extract_file(1, src, lang, Parser(L), False, Query(L, specs[lang]))


def _rel_pairs(rows, edges, relations):
    """{(enclosing_symbol_name, relation, dst_name)} restricted to `relations`."""
    by_local = {r["_local"]: r["name"] for r in rows}
    return {(by_local[e["src_local"]], e["relation"], e["dst_name"])
            for e in edges if e["relation"] in relations}


# --- javascript -------------------------------------------------------------
_JS_SRC = (
    'import topdep from "./top.js";\n'           # module-scope: dropped (no enclosing sym)
    "function loader() {\n"
    '    const fs = require("node:fs");\n'        # require -> calls 'require' AND imports 'node:fs'
    "    helper(loader);\n"                       # free call
    "    return obj.read(fs);\n"                  # member call -> 'read'
    "}\n"
    "class Widget extends Base {\n"               # extends comes from the WALKER, not the .scm
    "    render() { this.draw(); helper(); }\n"
    "}\n"
)


def test_javascript_edges_end_to_end():
    rows, edges = _lang_edges(_JS_SRC, "javascript")
    calls_imports = _rel_pairs(rows, edges, {"calls", "imports"})
    # free + member + the require() call itself attach to loader; node:fs is the import.
    assert ("loader", "calls", "helper") in calls_imports
    assert ("loader", "calls", "read") in calls_imports
    assert ("loader", "calls", "require") in calls_imports      # require IS a real call
    assert ("loader", "imports", "node:fs") in calls_imports    # ...and an import (no collision)
    # method body calls attach to the method.
    assert ("render", "calls", "draw") in calls_imports
    assert ("render", "calls", "helper") in calls_imports
    # JS is untyped: the preset declares no uses_type at all.
    assert not any(e["relation"] == "uses_type" for e in edges)
    # inheritance: exactly one extends, emitted by the WALKER (no .scm double-count).
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    # module-scope `import ... from` has no enclosing symbol -> dropped.
    assert all(e["dst_name"] != "top" for e in edges if e["relation"] == "imports")


def test_javascript_module_scope_import_dropped():
    # a top-of-file import with no enclosing symbol produces zero edges (like python).
    _rows, edges = _lang_edges('import x from "pkg";\n', "javascript")
    assert not edges


# --- typescript / tsx -------------------------------------------------------
_TS_SRC = (
    "class Circle extends Base implements Shape {\n"   # extends/implements -> WALKER
    "    area(): Box {\n"                               # return type -> uses_type Box
    '        const legacy = require("legacy");\n'       # require -> imports 'legacy', NOT a call
    "        return helper(this.svc.doThing());\n"      # calls helper + doThing (final prop only)
    "    }\n"
    "}\n"
    "function compute(a: Vec3): Result {\n"             # param Vec3 + return Result -> uses_type
    "    const col: Color = make(a);\n"                 # var type Color -> uses_type; make -> calls
    "    return new Widget();\n"                        # new T -> uses_type Widget
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


@pytest.mark.parametrize("lang", ["typescript", "tsx"])
def test_typescript_edges_end_to_end(lang):
    # the tsx grammar shares typescript's node/field names; the tsx.scm preset is a
    # mirror, so .ts and .tsx must yield the SAME dep edges for this snippet.
    rows, edges = _lang_edges(_TS_SRC, lang)
    assert _TS_EXPECTED_CALLS_IMPORTS <= _rel_pairs(rows, edges, {"calls", "imports"})
    assert _TS_EXPECTED_TYPES <= _rel_pairs(rows, edges, {"uses_type"})
    # require() is captured as an import, NOT a call (the #not-eq? predicate excludes it).
    assert ("area", "calls", "require") not in _rel_pairs(rows, edges, {"calls"})
    # extends + implements come from the WALKER, exactly once each (no .scm double-count).
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    assert sum(1 for e in edges if e["relation"] == "implements") == 1


def test_typescript_predicate_distinguishes_require():
    # a non-`require` single-string call is a normal call, not an import.
    src = "function f() {\n  const a = notrequire('x');\n  const b = require('mod');\n}\n"
    rows, edges = _lang_edges(src, "typescript")
    pairs = _rel_pairs(rows, edges, {"calls", "imports"})
    assert ("f", "calls", "notrequire") in pairs
    assert ("f", "imports", "mod") in pairs
    assert ("f", "calls", "require") not in pairs       # require excluded from calls
    assert ("f", "imports", "x") not in pairs           # notrequire is NOT an import


# --- csharp -----------------------------------------------------------------
_CS_SRC = (
    "using System;\n"                                   # module-scope using -> dropped (no sym)
    "using Alias = Some.Long.Name;\n"                   # alias name must NOT be an import
    "namespace MyApp {\n"
    "  public class Widget : BaseWidget, IDisposable {\n"   # base_list -> WALKER extends/implements
    "    private Helper _helper;\n"                      # field type -> uses_type (encloser: class)
    "    public Result DoWork(Config cfg) {\n"           # param + return type -> uses_type
    "      var sb = new StringBuilder();\n"              # object-creation type -> uses_type
    '      Console.WriteLine("hi");\n'                   # member call -> WriteLine
    "      _helper.Process(cfg);\n"                      # member call -> Process
    "      return new Result(Compute());\n"              # free call Compute + new Result
    "    }\n"
    "    private int Compute() => 2;\n"
    "  }\n"
    "}\n"
)


def test_csharp_edges_end_to_end():
    rows, edges = _lang_edges(_CS_SRC, "csharp")
    calls = _rel_pairs(rows, edges, {"calls"})
    types = _rel_pairs(rows, edges, {"uses_type"})
    assert {("DoWork", "calls", "WriteLine"),
            ("DoWork", "calls", "Process"),
            ("DoWork", "calls", "Compute")} <= calls
    # field type attaches to the enclosing CLASS; param/return/new attach to the method.
    assert ("Widget", "uses_type", "Helper") in types
    assert {("DoWork", "uses_type", "Config"),
            ("DoWork", "uses_type", "Result"),
            ("DoWork", "uses_type", "StringBuilder")} <= types
    # extends + implements from the WALKER (base_list), exactly once each.
    assert sum(1 for e in edges if e["relation"] == "extends") == 1
    assert sum(1 for e in edges if e["relation"] == "implements") == 1
    # the `var` local is implicit_type -> NOT a uses_type dep; primitives skipped too.
    assert all(e["dst_name"] not in ("var", "int") for e in edges
               if e["relation"] == "uses_type")


def test_csharp_aliased_using_excludes_alias_name():
    # `using Alias = Some.Long.Name;` must capture the SOURCE, never the alias 'Alias'.
    # Top-level usings have no enclosing symbol (dropped at edge time), so assert at the
    # raw-capture level that the !name anchor never grabs the alias identifier.
    L = get_language(E._PACK_NAME["csharp"])
    specs = E._load_query_specs(None)
    q = Query(L, specs["csharp"])
    from tree_sitter import QueryCursor
    tree = Parser(L).parse("using Alias = Some.Long.Name;\nusing System;\n".encode("utf-8"))
    caps = QueryCursor(q).captures(tree.root_node)
    imports = {n.text.decode("utf-8") for n in caps.get("dep.imports", [])}
    assert "Alias" not in imports                       # the !name anchor excludes it
    assert "System" in imports
    assert any("Some.Long.Name" in s for s in imports)  # the aliased source IS captured


def test_js_ts_csharp_deterministic():
    for src, lang in ((_JS_SRC, "javascript"), (_TS_SRC, "typescript"),
                      (_TS_SRC, "tsx"), (_CS_SRC, "csharp")):
        r1, e1 = _lang_edges(src, lang)
        r2, e2 = _lang_edges(src, lang)
        assert r1 == r2 and e1 == e2


# ===========================================================================
# REGRESSION: a walker lang with NO .scm preset is unaffected by this layer —
# it keeps walker-only edges (contains/extends), zero @dep edges. `java` has a
# walker (symbols + extends) but ships no java.scm, so it is the perfect probe.
# ===========================================================================
def test_walker_lang_without_scm_has_no_dep_edges():
    assert "java" not in E._load_query_specs(None)       # no java.scm preset exists
    src = ("class A extends B {\n"
           "  void m(C c) { helper(); }\n"
           "}\n")
    rows, edges = _walker_extract(src, "java")            # passes query=None (no preset)
    rels = {e["relation"] for e in edges}
    # walker still emits structure...
    assert "contains" in rels and "extends" in rels
    # ...but the declarative dep layer added NOTHING (no calls/imports/uses_type).
    assert not (rels & {"calls", "imports", "uses_type"})
    assert any(r["name"] == "A" and r["kind"] == "class" for r in rows)


# ===========================================================================
# REAL-FIXTURE SMOKE: build a tiny throwaway repo per lang, index + enrich into
# an ISOLATED store, and assert the dep edges land in the DB. MANYREAD_HOME is
# pointed at a temp dir so the hub registry write NEVER touches the user hub
# (~/.manyread/stores.json); everything is removed in a finally. This exercises
# the full index_build -> enrich_treesitter -> edges-table path, not just the
# in-memory extractor.
# ===========================================================================
def test_real_fixture_smoke_js_csharp_isolated(tmp_path, monkeypatch):
    import sqlite3
    import index_build
    from lib import config

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    # Isolate the hub + any ambient store discovery so we never touch the user hub.
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

    # JavaScript: require() inside a function attaches as both a call + an import.
    assert ("loader", "calls", "helper") in got
    assert ("loader", "calls", "readFileSync") in got
    assert ("loader", "imports", "node:fs") in got
    assert ("render", "calls", "draw") in got
    # C#: calls + type usages land in the edges table.
    assert ("Do", "calls", "WriteLine") in got
    assert {("Do", "uses_type", "Config"), ("Do", "uses_type", "Result"),
            ("Do", "uses_type", "StringBuilder")} <= got

    # Hub isolation: the registry write went to the temp MANYREAD_HOME, not the user's.
    assert (home / "stores.json").exists()
