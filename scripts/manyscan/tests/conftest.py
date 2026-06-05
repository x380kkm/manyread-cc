# audience: internal
# manyscan.tests.conftest
"""manyscan 的 pytest 夹具。

用 manyread 自己的 ``db.init_schema`` 建一个极小的存储库（让测试运行真实 schema），再插入一个
三文件的 Python 包，含一条 import 边与一条 ``extends`` 边 —— 足以驱动 stores / deps / scope /
graph 各项测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 manyscan 目录加入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import stores  # noqa: E402


#### 三文件 Python 包的文件行：(id, 路径, 扩展名, 内容) [@380kkm 2026-06-05] ####
_FILES = [
    (1, "pkg/a.py", ".py", "import pkg.b\nfrom pkg.c import C\n\n\nclass A(Base):\n    pass\n"),
    (2, "pkg/b.py", ".py", "class B:\n    pass\n"),
    (3, "pkg/c.py", ".py", "class C:\n    pass\n"),
]
#### 符号行：(id, file_id, name, kind, start_line, end_line) [@380kkm 2026-06-05] ####
_SYMS = [(1, 1, "A", "class", 5, 6), (2, 2, "B", "class", 1, 2), (3, 3, "C", "class", 1, 2)]


#### 在磁盘上建一个最小真实 schema 的 manyread 存储库，返回其 source.db 路径 [@380kkm 2026-06-05] ####
@pytest.fixture
def synth_store(tmp_path) -> Path:
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,?,?)",
            (fid, path, ext, len(content), 0, content),
        )
        conn.execute(
            "INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content)
        )
    for sid, fid, name, kind, sl, el in _SYMS:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id) VALUES(?,?,?,?, 'python',?,?,0,1,NULL)",
            (sid, fid, name, kind, sl, el),
        )
    conn.execute(
        "INSERT INTO edges(id,file_id,src_symbol_id,dst_symbol_id,dst_name,relation) "
        "VALUES(1,1,1,NULL,'Base','extends')"
    )
    conn.commit()
    conn.close()
    return db_path


#### 符号级目标↔依赖边界的文件行：plugin/Foo.cpp 为目标，依赖 engine/{Core.h,Actor.h} [@380kkm 2026-06-05] ####
_B_FILES = [
    (1, "plugin/X.uplugin", ".uplugin", "{}"),
    (2, "plugin/Foo.cpp", ".cpp", "class Foo : public Actor {};\n"),
    (3, "engine/Core.h", ".h", "class Core {};\nclass Dup {};\n"),
    (4, "engine/Actor.h", ".h", "class Actor {};\nclass Dup {};\n"),
]
#### 边界符号行：(id, file_id, name, kind, start_line, end_line, parent_id) [@380kkm 2026-06-05] ####
_B_SYMS = [
    # 目标
    (1, 2, "Foo", "class", 1, 1, None),
    # 依赖
    (2, 4, "Actor", "class", 1, 1, None),
    # 依赖
    (3, 3, "Core", "class", 1, 1, None),
    # 依赖（重复 #1，Core.h）
    (4, 3, "Dup", "class", 2, 2, None),
    # 依赖（重复 #2，Actor.h）
    (5, 4, "Dup", "class", 2, 2, None),
]
#### 边界边行：(id, file_id, src_symbol_id, dst_symbol_id, dst_name, relation) [@380kkm 2026-06-05] ####
_B_EDGES = [
    # Foo -> Actor：dst_symbol_id 已设 => 直接边
    (1, 2, 1, 2, None, "extends"),
    # Foo -> Core：1 个候选 => 唯一解析
    (2, 2, 1, None, "Core", "implements"),
    # Foo -> Missing：0 个候选 => 未解析
    (3, 2, 1, None, "Missing", "uses_type"),
    # Foo -> Dup：2 个候选 => 歧义
    (4, 2, 1, None, "Dup", "uses_type"),
]


#### 建符号级目标↔依赖存储库：一个目标符号经 直接/唯一/未解析/歧义 边连向其依赖，返回 source.db 路径 [@380kkm 2026-06-05] ####
@pytest.fixture
def boundary_store(tmp_path) -> Path:
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _B_FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
            (fid, path, ext, len(content), content),
        )
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    for sid, fid, name, kind, sl, el, parent in _B_SYMS:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id) VALUES(?,?,?,?, 'cpp',?,?,0,1,?)",
            (sid, fid, name, kind, sl, el, parent),
        )
    for eid, fid, src, dst, dname, rel in _B_EDGES:
        conn.execute(
            "INSERT INTO edges(id,file_id,src_symbol_id,dst_symbol_id,dst_name,relation) "
            "VALUES(?,?,?,?,?,?)",
            (eid, fid, src, dst, dname, rel),
        )
    conn.commit()
    conn.close()
    return db_path


#### 仅含 C++、files 中无任何模块标记文件的文件行：复刻真实 L1 索引器对 cpp 项目的产物 [@380kkm 2026-06-05] ####
_NM_FILES = [
    (1, "MyPlugin/Source/Foo.h", ".h", "class Foo : public AActor {};\n"),
    (2, "Engine/Source/Actor.h", ".h", "class AActor {};\n"),
]
#### 无标记场景的符号行 [@380kkm 2026-06-05] ####
_NM_SYMS = [
    # 本应作为目标
    (1, 1, "Foo", "class", 1, 1, None),
    # 本应作为依赖
    (2, 2, "AActor", "class", 1, 1, None),
]
#### 无标记场景的边行 [@380kkm 2026-06-05] ####
_NM_EDGES = [
    # Foo -> AActor
    (1, 1, 1, 2, None, "extends"),
]


#### 建一个无 *.uplugin/*.Build.cs 标记被索引的 cpp 存储库（真实索引场景） [@380kkm 2026-06-05] ####
@pytest.fixture
def cpp_no_marker_store(tmp_path) -> Path:
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _NM_FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
            (fid, path, ext, len(content), content),
        )
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    for sid, fid, name, kind, sl, el, parent in _NM_SYMS:
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
            "start_byte,end_byte,parent_id) VALUES(?,?,?,?, 'cpp',?,?,0,1,?)",
            (sid, fid, name, kind, sl, el, parent),
        )
    for eid, fid, src, dst, dname, rel in _NM_EDGES:
        conn.execute(
            "INSERT INTO edges(id,file_id,src_symbol_id,dst_symbol_id,dst_name,relation) "
            "VALUES(?,?,?,?,?,?)",
            (eid, fid, src, dst, dname, rel),
        )
    conn.commit()
    conn.close()
    return db_path


#### 两模块（modA 引入 modB，各带一个 CMakeLists.txt 标记）的文件行 [@380kkm 2026-06-05] ####
_MOD_FILES = [
    (1, "modA/CMakeLists.txt", ".txt", ""),
    (2, "modA/x.py", ".py", "import modB.y\n"),
    (3, "modB/CMakeLists.txt", ".txt", ""),
    (4, "modB/y.py", ".py", "class Y:\n    pass\n"),
]


#### 建一个两模块存储库（modA 引入 modB），各带一个 CMakeLists.txt 标记 [@380kkm 2026-06-05] ####
@pytest.fixture
def module_store(tmp_path) -> Path:
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in _MOD_FILES:
        conn.execute(
            "INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
            (fid, path, ext, len(content), content),
        )
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    conn.commit()
    conn.close()
    return db_path
