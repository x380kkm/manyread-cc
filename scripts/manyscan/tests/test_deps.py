"""Tests for manyscan.lib.deps — import extraction + cross-file resolution."""
from __future__ import annotations

from lib import deps, stores


# --- pure extraction (no store) ---
def test_extract_python():
    src = "import os\nimport pkg.a as a, pkg.b  # c\nfrom pkg.c import C\n"
    refs = deps.extract_imports(src, ".py")
    targets = [(r.target, r.line, r.kind) for r in refs]
    assert ("os", 1, "python") in targets
    assert ("pkg.a", 2, "python") in targets
    assert ("pkg.b", 2, "python") in targets
    assert ("pkg.c", 3, "python") in targets


def test_extract_cpp_include():
    src = '#include "foo/bar.h"\n#include <vector>\nint x;\n'
    refs = deps.extract_imports(src, ".cpp")
    assert [(r.target, r.kind) for r in refs] == [
        ("foo/bar.h", "cpp_include"),
        ("vector", "cpp_include"),
    ]


def test_extract_csharp_using_skips_resource_stmt():
    src = "using System;\nusing Foo.Bar;\nusing (var s = Open()) { }\n"
    refs = deps.extract_imports(src, ".cs")
    assert [r.target for r in refs] == ["System", "Foo.Bar"]


def test_extract_js_specifiers():
    src = "import x from './a'\nimport './b'\nconst y = require('pkg-c')\n"
    refs = deps.extract_imports(src, ".ts")
    assert {r.target for r in refs} == {"./a", "./b", "pkg-c"}
    assert all(r.kind == "js_import" for r in refs)


def test_family_unknown_ext():
    assert deps.family(".txt") is None
    assert deps.extract_imports("whatever", ".txt") == []


# --- over a real-schema store (synth fixture: pkg/a imports pkg.b + pkg.c) ---
def test_file_imports_over_store(synth_store):
    with stores.Store(synth_store) as st:
        refs = deps.file_imports(st, 1)  # pkg/a.py
        assert {r.target for r in refs} == {"pkg.b", "pkg.c"}


def test_resolve_python_imports_to_files(synth_store):
    with stores.Store(synth_store) as st:
        refs = {r.target: r for r in deps.file_imports(st, 1)}
        assert deps.resolve_import(st, refs["pkg.b"], from_path="pkg/a.py") == 2
        assert deps.resolve_import(st, refs["pkg.c"], from_path="pkg/a.py") == 3


def test_resolve_edge_targets_global(synth_store):
    with stores.Store(synth_store) as st:
        # 'C' is defined in pkg/c.py -> exactly one candidate
        hits = deps.resolve_edge_targets(st, "C")
        assert len(hits) == 1 and hits[0]["path"].endswith("c.py")
        # 'Base' is referenced (extends) but never defined -> external, zero candidates
        assert deps.resolve_edge_targets(st, "Base") == []
