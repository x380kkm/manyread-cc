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


# --- definition-preference: forward declarations dropped when a definition exists ---
def _store_with_spans(tmp_path, syms):
    """syms: [(id, path, name, start_byte, end_byte)] — all kind=class, lang cpp."""
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    files: dict[str, int] = {}
    for _sid, path, _name, _sb, _eb in syms:
        files.setdefault(path, len(files) + 1)
    for path, fid in files.items():
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,'.h',0,0,'')",
            (fid, path),
        )
    for sid, path, name, sb, eb in syms:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id) VALUES(?,?,?, 'class','cpp',1,1,?,?,NULL)",
            (sid, files[path], name, sb, eb),
        )
    conn.commit()
    conn.close()
    return db_path


def test_resolve_prefers_definition_over_forward_declarations(tmp_path):
    # one body-bearing definition (large span) + three `class UMaterial;` fwd-decls
    db = _store_with_spans(tmp_path, [
        (1, "engine/Mat.h", "UMaterial", 0, 2000),
        (2, "a/A.h", "UMaterial", 0, 15),
        (3, "b/B.h", "UMaterial", 0, 15),
        (4, "c/C.h", "UMaterial", 0, 15),
    ])
    with stores.Store(db) as st:
        cands = deps.resolve_edge_targets(st, "UMaterial")
    assert len(cands) == 1 and cands[0]["path"] == "engine/Mat.h"


def test_resolve_keeps_all_when_only_forward_declarations(tmp_path):
    # no definition under the name -> honest, keep all (stays ambiguous)
    db = _store_with_spans(tmp_path, [
        (1, "a/A.h", "UThing", 0, 12),
        (2, "b/B.h", "UThing", 0, 12),
    ])
    with stores.Store(db) as st:
        cands = deps.resolve_edge_targets(st, "UThing")
    assert len(cands) == 2
