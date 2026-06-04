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
