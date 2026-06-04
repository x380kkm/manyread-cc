"""Regression tests for the ASSET->SOURCE cross-layer linker (scripts/link_source.py).

Self-contained and FAST: builds BOTH input stores by DIRECT SQL INSERT via
manyread's own ``db.init_schema`` (the conftest ``boundary_store`` idiom) — NO
tree-sitter, NO index/enrich. It tests the LINKER's confidence logic (the parser
is already covered by test_enrich_query). Uses the REAL sample schema
(scripts/schemas/matlang.sample.json). Fully isolated with MANYREAD_HOME=tmp.

Run from the scripts/ dir (no tree-sitter needed)::

    cd scripts && uv run --python 3.12 --with pytest -m pytest tests/test_link_source.py -q
"""
import json
import os
import sys
from pathlib import Path

# scripts/ on path so ``import link_source`` resolves; link_source itself adds
# scripts/manyscan for the stores layer.
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import link_source as L  # noqa: E402

SCHEMA = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "schemas", "matlang.sample.json")
)


# --- MOCK CODE store: stub C++ class symbols ---------------------------------
# UMaterialExpressionMultiply (unique for 'multiply'),
# UMaterialExpressionConstant (unique for 'constant'),
# UMaterial (unique for the 'material' root via the U-prefix),
# UMaterialExpressionScalarParameter DUPLICATED across two files (ambiguous(2)),
# and DELIBERATELY OMIT UMaterialExpressionFresnel (unresolved for 'fresnel').
_CODE_FILES = [
    (1, "engine/Mul.h", ".h", "class UMaterialExpressionMultiply {};\n"),
    (2, "engine/Const.h", ".h", "class UMaterialExpressionConstant {};\n"),
    (3, "engine/Material.h", ".h", "class UMaterial {};\n"),
    (4, "engine/Scalar.h", ".h", "class UMaterialExpressionScalarParameter {};\n"),
    (5, "engine/Scalar2.h", ".h", "class UMaterialExpressionScalarParameter {};\n"),
]
# (id, file_id, name, kind)
_CODE_SYMS = [
    (1, 1, "UMaterialExpressionMultiply", "class"),
    (2, 2, "UMaterialExpressionConstant", "class"),
    (3, 3, "UMaterial", "class"),
    (4, 4, "UMaterialExpressionScalarParameter", "class"),  # dup #1
    (5, 5, "UMaterialExpressionScalarParameter", "class"),  # dup #2
]

# --- DSL store: matlang node rows + a material root --------------------------
# Each node carries attrs {"node_type": ...}. 'panner' is NOT in the sample schema
# (no-classPath). The material root has kind='material', attrs={} (no node_type).
_DSL_FILES = [
    (1, "M_Test.matlang", ".matlang", "(material M_Test ...)\n"),
]
# (id, file_id, name, kind, attrs_json)
_DSL_SYMS = [
    (1, 1, "M_Test", "material", "{}"),
    (2, 1, "$mul1", "node", '{"node_type": "multiply"}'),
    (3, 1, "$const1", "node", '{"node_type": "constant"}'),
    (4, 1, "$sparam1", "node", '{"node_type": "scalar-parameter"}'),
    (5, 1, "$fresnel1", "node", '{"node_type": "fresnel"}'),
    (6, 1, "$pan1", "node", '{"node_type": "panner"}'),  # not in schema -> no-classPath
    (7, 1, "$out", "outputs", "{}"),  # excluded by the WHERE clause
]


def _build_code_store(tmp: Path) -> Path:
    _, mr_db = L.stores.manyread_lib()
    store = tmp / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _CODE_FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
            (fid, path, ext, len(content), content),
        )
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    for sid, fid, name, kind in _CODE_SYMS:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id) VALUES(?,?,?,?, 'cpp',1,1,0,1,NULL)",
            (sid, fid, name, kind),
        )
    conn.commit()
    conn.close()
    return db_path


def _build_dsl_store(tmp: Path) -> Path:
    _, mr_db = L.stores.manyread_lib()
    store = tmp / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _DSL_FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
            (fid, path, ext, len(content), content),
        )
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    for sid, fid, name, kind, attrs in _DSL_SYMS:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id,attrs) VALUES(?,?,?,?, 'matlang',?,?,?,?,NULL,?)",
            (sid, fid, name, kind, sid, sid, sid, sid + 1, attrs),
        )
    conn.commit()
    conn.close()
    return db_path


import pytest  # noqa: E402


@pytest.fixture
def stores_pair(tmp_path, monkeypatch):
    """Isolated MANYREAD_HOME + a code store and a DSL store under their own dirs."""
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code_db = _build_code_store(tmp_path / "code")
    dsl_db = _build_dsl_store(tmp_path / "dsl")
    return dsl_db, code_db


def _by_name(rep, name):
    for e in rep["nodes"]:
        if e["node_name"] == name:
            return e
    raise AssertionError(f"node {name!r} not in report")


def test_reflected_name():
    assert L.reflected_name("/Script/Engine.MaterialExpressionMultiply") == "MaterialExpressionMultiply"
    assert L.reflected_name("/Script/Engine.Material") == "Material"
    assert L.reflected_name("") is None
    assert L.reflected_name("NoDot") is None


def test_outputs_row_excluded(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    # the 'outputs' container is not a linkable node
    assert all(e["node_name"] != "$out" for e in rep["nodes"])


def test_multiply_resolved_unique(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$mul1")
    assert e["node_type"] == "multiply"
    assert e["classPath"] == "/Script/Engine.MaterialExpressionMultiply"
    assert e["status"] == "resolved-unique"
    assert e["resolved"]["symbol_name"] == "UMaterialExpressionMultiply"
    assert e["resolved"]["loc"] == "engine/Mul.h:1"
    assert e["resolved"]["confidence"] == "unique"


def test_fresnel_unresolved(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$fresnel1")
    assert e["classPath"] == "/Script/Engine.MaterialExpressionFresnel"
    assert e["status"] == "unresolved"
    assert e["resolved"] is None


def test_scalar_parameter_ambiguous(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$sparam1")
    assert e["status"] == "resolved-ambiguous"
    assert e["resolved"]["confidence"] == "ambiguous"
    assert e["resolved"]["ambiguity"] == 2
    assert e["resolved"]["candidates"] == ["engine/Scalar.h:1", "engine/Scalar2.h:1"]


def test_panner_no_classpath(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$pan1")
    assert e["node_type"] == "panner"
    assert e["status"] == "no-classPath"
    assert e["classPath"] is None
    assert e["resolved"] is None


def test_material_root_resolved_unique(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "M_Test")
    # the material ROOT has attrs={} -> node_type synthesized from kind=='material'
    assert e["node_type"] == "material"
    assert e["classPath"] == "/Script/Engine.Material"
    assert e["status"] == "resolved-unique"
    assert e["resolved"]["symbol_name"] == "UMaterial"  # via the U-prefix variant


def test_code_lang_filter_keeps_unique(tmp_path, monkeypatch):
    """A cross-lang class-kind symbol sharing a ReflectedName must NOT flip a
    'cpp' resolution to ambiguous (audit fix: resolve_class filters by code_lang).
    With code_lang=None (resolve across ALL langs) it DOES become ambiguous, proving
    the lang cut is what protects the default 'cpp' resolution."""
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    _, mr_db = L.stores.manyread_lib()
    store = tmp_path / "code2" / "manyread"
    store.mkdir(parents=True)
    code_db = store / "source.db"
    conn = mr_db.connect(code_db)
    mr_db.init_schema(conn)
    files = [
        (1, "engine/Material.h", ".h", "class UMaterial {};\n"),
        (2, "weird/material.matlang", ".matlang", "(material UMaterial ...)\n"),
    ]
    for fid, path, ext, content in files:
        conn.execute("INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
                     (fid, path, ext, len(content), content))
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    # A cpp class AND a (contrived) matlang class-kind symbol, both named UMaterial.
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                 "start_byte,end_byte,parent_id) VALUES(1,1,'UMaterial','class','cpp',1,1,0,1,NULL)")
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                 "start_byte,end_byte,parent_id) VALUES(2,2,'UMaterial','class','matlang',1,1,0,1,NULL)")
    conn.commit()
    conn.close()

    dsl_db = _build_dsl_store(tmp_path / "dsl")
    # default code_lang='cpp' -> only the cpp UMaterial counts -> unique
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "M_Test")
    assert e["status"] == "resolved-unique"
    assert e["resolved"]["symbol_name"] == "UMaterial"
    assert e["resolved"]["loc"] == "engine/Material.h:1"
    # code_lang=None -> the matlang UMaterial is also counted -> ambiguous(2)
    rep_any = L.link(str(dsl_db), str(code_db), SCHEMA, code_lang=None)
    e_any = _by_name(rep_any, "M_Test")
    assert e_any["status"] == "resolved-ambiguous"
    assert e_any["resolved"]["ambiguity"] == 2


def test_summary_counts(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    s = rep["summary"]
    assert s["resolved_unique"] >= 3   # multiply, constant, material (root)
    assert s["resolved_ambiguous"] == 1  # scalar-parameter
    assert s["unresolved"] == 1          # fresnel
    assert s["no_class_path"] == 1       # panner
    assert s["total"] == 6               # 6 node/material rows ('outputs' excluded)


def test_toplevel_paths_normalized(stores_pair):
    """Top-level provenance paths are backslash-normalized -> whole report is
    byte-portable across OSes, not just the resolution locs (audit fix #2)."""
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    for k in ("dsl_store", "code_store", "schema"):
        assert "\\" not in rep[k], f"{k} retains a backslash: {rep[k]!r}"


def test_determinism_byte_identical(stores_pair):
    dsl_db, code_db = stores_pair
    a = json.dumps(L.link(str(dsl_db), str(code_db), SCHEMA), ensure_ascii=False, indent=2)
    b = json.dumps(L.link(str(dsl_db), str(code_db), SCHEMA), ensure_ascii=False, indent=2)
    assert a == b


def test_purity_input_stores_unchanged(stores_pair):
    dsl_db, code_db = stores_pair
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (dsl_db, code_db)}
    L.link(str(dsl_db), str(code_db), SCHEMA)
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (dsl_db, code_db)}
    assert before == after  # mode=ro: neither input store is mutated


def test_text_render_runs(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    text = L.render_text(rep)
    assert "resolved-unique=" in text
    assert "UMaterialExpressionMultiply @ engine/Mul.h:1" in text
    assert "AMBIGUOUS(2)" in text


def test_bad_schema_raises():
    with pytest.raises(ValueError):
        L.load_schema(os.devnull)  # empty -> json.load fails before shape check


def test_cli_exit_codes(stores_pair, capsys):
    dsl_db, code_db = stores_pair
    rc = L.main(["--dsl-store", str(dsl_db), "--code-store", str(code_db),
                 "--schema", SCHEMA, "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    assert rep["summary"]["resolved_ambiguous"] == 1
    # bad store path -> exit 2
    rc2 = L.main(["--dsl-store", "W:/nope/nope", "--code-store", str(code_db),
                  "--schema", SCHEMA])
    assert rc2 == 2


# --- definition-preference (v0.8.6) ------------------------------------------
# A custom store builder that takes EXPLICIT spans, so we can plant a real
# definition (large span) among forward-declarations (tiny span) — the stub
# stores above use span 1 and never exercise this path.
def _build_store(tmp: Path, files, syms, lang: str) -> Path:
    """files: [(fid, path)]; syms: [(sid, fid, name, kind, start_byte, end_byte, attrs)]."""
    _, mr_db = L.stores.manyread_lib()
    store = tmp / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path in files:
        conn.execute("INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,'.x',0,0,'')", (fid, path))
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,'')", (fid, path))
    for sid, fid, name, kind, sb, eb, attrs in syms:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id,attrs) VALUES(?,?,?,?,?,1,1,?,?,NULL,?)",
            (sid, fid, name, kind, lang, sb, eb, attrs),
        )
    conn.commit()
    conn.close()
    return db_path


def test_definition_preferred_over_forward_declarations(tmp_path, monkeypatch):
    """1 definition (large span) + N forward-declarations (tiny span) -> the
    definition wins, UNIQUE. This is the real-engine pattern: UMaterialExpressionX
    is forward-declared in many headers; only the definition has a body."""
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code = _build_store(tmp_path / "code",
        [(1, "engine/Mul.h"), (2, "a/A.h"), (3, "b/B.h"), (4, "c/C.h")],
        [(1, 1, "UMaterialExpressionMultiply", "class", 0, 1070, None),  # DEFINITION
         (2, 2, "UMaterialExpressionMultiply", "class", 0, 33, None),    # fwd-decl
         (3, 3, "UMaterialExpressionMultiply", "class", 0, 33, None),    # fwd-decl
         (4, 4, "UMaterialExpressionMultiply", "class", 0, 33, None)],   # fwd-decl
        "cpp")
    dsl = _build_store(tmp_path / "dsl", [(1, "M.matlang")],
        [(1, 1, "$m", "node", 0, 1, '{"node_type": "multiply"}')], "matlang")
    e = _by_name(L.link(str(dsl), str(code), SCHEMA), "$m")
    assert e["status"] == "resolved-unique"
    assert e["resolved"]["loc"] == "engine/Mul.h:1"  # the definition, never a fwd-decl


def test_only_forward_declarations_stay_ambiguous(tmp_path, monkeypatch):
    """When ONLY forward-declarations are indexed (no definition under that name —
    the real UMaterial anomaly), they are kept and surfaced as ambiguous, not hidden."""
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code = _build_store(tmp_path / "code", [(1, "a/A.h"), (2, "b/B.h")],
        [(1, 1, "UMaterial", "class", 0, 15, None),
         (2, 2, "UMaterial", "class", 0, 15, None)], "cpp")
    dsl = _build_store(tmp_path / "dsl", [(1, "M.matlang")],
        [(1, 1, "M_Root", "material", 0, 1, "{}")], "matlang")
    e = _by_name(L.link(str(dsl), str(code), SCHEMA), "M_Root")
    assert e["status"] == "resolved-ambiguous"
    assert e["resolved"]["ambiguity"] == 2
