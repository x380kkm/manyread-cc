# audience: internal
# extensions.ue.tests.test_link_source
"""ASSET->SOURCE 跨层链接器（scripts/extensions/ue/link_source.py）的回归测试。"""
import json
import os
import sys
from pathlib import Path

# 把扩展目录 scripts/extensions/ue/ 加入路径（link_source 已随扩展迁至此处）
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import link_source as L  # noqa: E402

# schema 在同级扩展目录 scripts/extensions/ue/schemas/ 下
SCHEMA = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "schemas", "matlang.sample.json")
)


#### 模拟 CODE 存储库：桩 C++ 类符号 [@380kkm 2026-06-05] ####
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
    # 重复 #1
    (4, 4, "UMaterialExpressionScalarParameter", "class"),
    # 重复 #2
    (5, 5, "UMaterialExpressionScalarParameter", "class"),
]
#### /模拟 CODE 存储库 ####

#### DSL 存储库：matlang 节点行 + 一个 material 根 [@380kkm 2026-06-05] ####
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
    # 不在 schema 中 -> 无 classPath
    (6, 1, "$pan1", "node", '{"node_type": "panner"}'),
    # 被 WHERE 子句排除
    (7, 1, "$out", "outputs", "{}"),
]
#### /DSL 存储库 ####


#### 构建桩 CODE 存储库并返回其 source.db 路径 [@380kkm 2026-06-05] ####
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
#### /构建桩 CODE 存储库 ####


#### 构建桩 DSL 存储库并返回其 source.db 路径 [@380kkm 2026-06-05] ####
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
#### /构建桩 DSL 存储库 ####


import pytest  # noqa: E402


#### 夹具：隔离的 MANYREAD_HOME + 各自目录下的 code 与 DSL 存储库 [@380kkm 2026-06-05] ####
@pytest.fixture
def stores_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code_db = _build_code_store(tmp_path / "code")
    dsl_db = _build_dsl_store(tmp_path / "dsl")
    return dsl_db, code_db
#### /隔离 MANYREAD_HOME + code/DSL 存储库夹具 ####


#### 按节点名从报告里取出对应的节点条目 [@380kkm 2026-06-05] ####
def _by_name(rep, name):
    for e in rep["nodes"]:
        if e["node_name"] == name:
            return e
    raise AssertionError(f"node {name!r} not in report")
#### /按名取节点条目 ####


#### 测试 reflected_name 从 classPath 反推反射名 [@380kkm 2026-06-05] ####
def test_reflected_name():
    assert L.reflected_name("/Script/Engine.MaterialExpressionMultiply") == "MaterialExpressionMultiply"
    assert L.reflected_name("/Script/Engine.Material") == "Material"
    assert L.reflected_name("") is None
    assert L.reflected_name("NoDot") is None


#### 测试 'outputs' 容器行被排除、不可链接 [@380kkm 2026-06-05] ####
def test_outputs_row_excluded(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    # 'outputs' 容器不是可链接节点
    assert all(e["node_name"] != "$out" for e in rep["nodes"])


#### 测试 multiply 唯一命中并解析到正确符号 [@380kkm 2026-06-05] ####
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


#### 测试 fresnel 因符号缺失而解析失败 [@380kkm 2026-06-05] ####
def test_fresnel_unresolved(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$fresnel1")
    assert e["classPath"] == "/Script/Engine.MaterialExpressionFresnel"
    assert e["status"] == "unresolved"
    assert e["resolved"] is None


#### 测试 scalar-parameter 因符号重复而歧义(2) [@380kkm 2026-06-05] ####
def test_scalar_parameter_ambiguous(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$sparam1")
    assert e["status"] == "resolved-ambiguous"
    assert e["resolved"]["confidence"] == "ambiguous"
    assert e["resolved"]["ambiguity"] == 2
    assert e["resolved"]["candidates"] == ["engine/Scalar.h:1", "engine/Scalar2.h:1"]


#### 测试 panner 不在 schema 中而无 classPath [@380kkm 2026-06-05] ####
def test_panner_no_classpath(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "$pan1")
    assert e["node_type"] == "panner"
    assert e["status"] == "no-classPath"
    assert e["classPath"] is None
    assert e["resolved"] is None


#### 测试 material 根经 kind 合成 node_type 并唯一命中 [@380kkm 2026-06-05] ####
def test_material_root_resolved_unique(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "M_Test")
    # material 根 attrs={} -> node_type 由 kind=='material' 合成
    assert e["node_type"] == "material"
    assert e["classPath"] == "/Script/Engine.Material"
    assert e["status"] == "resolved-unique"
    # 经 U- 前缀变体命中
    assert e["resolved"]["symbol_name"] == "UMaterial"


#### 测试 code_lang 过滤使跨语言同名符号不破坏唯一解析 [@380kkm 2026-06-05] ####
def test_code_lang_filter_keeps_unique(tmp_path, monkeypatch):
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
    # 一个 cpp class，以及一个（人为构造的）matlang class-kind 符号，二者都名为 UMaterial。
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                 "start_byte,end_byte,parent_id) VALUES(1,1,'UMaterial','class','cpp',1,1,0,1,NULL)")
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                 "start_byte,end_byte,parent_id) VALUES(2,2,'UMaterial','class','matlang',1,1,0,1,NULL)")
    conn.commit()
    conn.close()

    dsl_db = _build_dsl_store(tmp_path / "dsl")
    # 默认 code_lang='cpp' -> 只有 cpp 的 UMaterial 计入 -> 唯一
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    e = _by_name(rep, "M_Test")
    assert e["status"] == "resolved-unique"
    assert e["resolved"]["symbol_name"] == "UMaterial"
    assert e["resolved"]["loc"] == "engine/Material.h:1"
    # code_lang=None -> matlang 的 UMaterial 也被计入 -> 歧义(2)
    rep_any = L.link(str(dsl_db), str(code_db), SCHEMA, code_lang=None)
    e_any = _by_name(rep_any, "M_Test")
    assert e_any["status"] == "resolved-ambiguous"
    assert e_any["resolved"]["ambiguity"] == 2


#### 测试 summary 各状态计数正确 [@380kkm 2026-06-05] ####
def test_summary_counts(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    s = rep["summary"]
    # multiply、constant、material（根）
    assert s["resolved_unique"] >= 3
    # scalar-parameter
    assert s["resolved_ambiguous"] == 1
    # fresnel
    assert s["unresolved"] == 1
    # panner
    assert s["no_class_path"] == 1
    # 6 个 node/material 行（'outputs' 已排除）
    assert s["total"] == 6


#### 测试顶层溯源路径做反斜杠归一化、报告跨平台逐字节可移植 [@380kkm 2026-06-05] ####
def test_toplevel_paths_normalized(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    for k in ("dsl_store", "code_store", "schema"):
        assert "\\" not in rep[k], f"{k} retains a backslash: {rep[k]!r}"


#### 测试两次链接的 JSON 序列化逐字节一致（确定性） [@380kkm 2026-06-05] ####
def test_determinism_byte_identical(stores_pair):
    dsl_db, code_db = stores_pair
    a = json.dumps(L.link(str(dsl_db), str(code_db), SCHEMA), ensure_ascii=False, indent=2)
    b = json.dumps(L.link(str(dsl_db), str(code_db), SCHEMA), ensure_ascii=False, indent=2)
    assert a == b


#### 测试链接为只读、输入存储库不被改动（纯函数） [@380kkm 2026-06-05] ####
def test_purity_input_stores_unchanged(stores_pair):
    dsl_db, code_db = stores_pair
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (dsl_db, code_db)}
    L.link(str(dsl_db), str(code_db), SCHEMA)
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (dsl_db, code_db)}
    # mode=ro：两个输入存储库都不被改动
    assert before == after


#### 测试文本渲染可运行且含关键行 [@380kkm 2026-06-05] ####
def test_text_render_runs(stores_pair):
    dsl_db, code_db = stores_pair
    rep = L.link(str(dsl_db), str(code_db), SCHEMA)
    text = L.render_text(rep)
    assert "resolved-unique=" in text
    assert "UMaterialExpressionMultiply @ engine/Mul.h:1" in text
    assert "AMBIGUOUS(2)" in text


#### 测试加载非法 schema 抛 ValueError [@380kkm 2026-06-05] ####
def test_bad_schema_raises():
    # 空文件 -> json.load 在形状检查前即失败
    with pytest.raises(ValueError):
        L.load_schema(os.devnull)


#### 测试 CLI 成功退出 0、坏存储库路径退出 2 [@380kkm 2026-06-05] ####
def test_cli_exit_codes(stores_pair, capsys):
    dsl_db, code_db = stores_pair
    rc = L.main(["--dsl-store", str(dsl_db), "--code-store", str(code_db),
                 "--schema", SCHEMA, "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    rep = json.loads(out)
    assert rep["summary"]["resolved_ambiguous"] == 1
    # 坏存储库路径 -> 退出 2
    rc2 = L.main(["--dsl-store", "W:/nope/nope", "--code-store", str(code_db),
                  "--schema", SCHEMA])
    assert rc2 == 2


#### 显式 span 的存储库构造器：用于定义优先 [@380kkm 2026-06-05] ####
def _build_store(tmp: Path, files, syms, lang: str) -> Path:
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
#### /显式 span 存储库构造器 ####


#### 测试定义（大 span）优先于多个前向声明并唯一命中 [@380kkm 2026-06-05] ####
def test_definition_preferred_over_forward_declarations(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code = _build_store(tmp_path / "code",
        [(1, "engine/Mul.h"), (2, "a/A.h"), (3, "b/B.h"), (4, "c/C.h")],
        # 定义
        [(1, 1, "UMaterialExpressionMultiply", "class", 0, 1070, None),
         # 前向声明
         (2, 2, "UMaterialExpressionMultiply", "class", 0, 33, None),
         (3, 3, "UMaterialExpressionMultiply", "class", 0, 33, None),
         (4, 4, "UMaterialExpressionMultiply", "class", 0, 33, None)],
        "cpp")
    dsl = _build_store(tmp_path / "dsl", [(1, "M.matlang")],
        [(1, 1, "$m", "node", 0, 1, '{"node_type": "multiply"}')], "matlang")
    e = _by_name(L.link(str(dsl), str(code), SCHEMA), "$m")
    assert e["status"] == "resolved-unique"
    # 是定义，绝不会是前向声明
    assert e["resolved"]["loc"] == "engine/Mul.h:1"


#### 测试仅有前向声明时保留全部并维持歧义 [@380kkm 2026-06-05] ####
def test_only_forward_declarations_stay_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "hub"))
    code = _build_store(tmp_path / "code", [(1, "a/A.h"), (2, "b/B.h")],
        [(1, 1, "UMaterial", "class", 0, 15, None),
         (2, 2, "UMaterial", "class", 0, 15, None)], "cpp")
    dsl = _build_store(tmp_path / "dsl", [(1, "M.matlang")],
        [(1, 1, "M_Root", "material", 0, 1, "{}")], "matlang")
    e = _by_name(L.link(str(dsl), str(code), SCHEMA), "M_Root")
    assert e["status"] == "resolved-ambiguous"
    assert e["resolved"]["ambiguity"] == 2
