"""pytest fixtures for manyscan.

Builds a tiny store using manyread's OWN ``db.init_schema`` (so tests exercise the
real schema), then inserts a 3-file Python package with one import edge and one
``extends`` edge — enough to drive stores / deps / scope / graph tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Put manyscan's dir on the path so ``from lib import ...`` resolves.
# (merged layout: scripts/manyscan/tests/conftest.py -> parents[1] == scripts/manyscan/)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import stores  # noqa: E402


_FILES = [
    (1, "pkg/a.py", ".py", "import pkg.b\nfrom pkg.c import C\n\n\nclass A(Base):\n    pass\n"),
    (2, "pkg/b.py", ".py", "class B:\n    pass\n"),
    (3, "pkg/c.py", ".py", "class C:\n    pass\n"),
]
# (id, file_id, name, kind, start_line, end_line)
_SYMS = [(1, 1, "A", "class", 5, 6), (2, 2, "B", "class", 1, 2), (3, 3, "C", "class", 1, 2)]


@pytest.fixture
def synth_store(tmp_path) -> Path:
    """A minimal real-schema manyread store on disk; returns its source.db path."""
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


# --- symbol-level target↔dependency boundary fixture ------------------------
# plugin/Foo.cpp (TARGET) depends on engine/{Core.h,Actor.h} (DEPENDENCY); a
# *.uplugin marker drives target-root autodetect. `Dup` exists in TWO dependency
# files to exercise ambiguous (>1) resolution.
_B_FILES = [
    (1, "plugin/X.uplugin", ".uplugin", "{}"),
    (2, "plugin/Foo.cpp", ".cpp", "class Foo : public Actor {};\n"),
    (3, "engine/Core.h", ".h", "class Core {};\nclass Dup {};\n"),
    (4, "engine/Actor.h", ".h", "class Actor {};\nclass Dup {};\n"),
]
# (id, file_id, name, kind, start_line, end_line, parent_id)
_B_SYMS = [
    (1, 2, "Foo", "class", 1, 1, None),       # target
    (2, 4, "Actor", "class", 1, 1, None),      # dependency
    (3, 3, "Core", "class", 1, 1, None),       # dependency
    (4, 3, "Dup", "class", 2, 2, None),        # dependency (dup #1, Core.h)
    (5, 4, "Dup", "class", 2, 2, None),        # dependency (dup #2, Actor.h)
]
# (id, file_id, src_symbol_id, dst_symbol_id, dst_name, relation)
_B_EDGES = [
    (1, 2, 1, 2, None, "extends"),             # Foo -> Actor: dst_symbol_id set => direct
    (2, 2, 1, None, "Core", "implements"),     # Foo -> Core: 1 candidate => unique
    (3, 2, 1, None, "Missing", "uses_type"),   # Foo -> Missing: 0 candidates => unresolved
    (4, 2, 1, None, "Dup", "uses_type"),       # Foo -> Dup: 2 candidates => ambiguous
]


@pytest.fixture
def boundary_store(tmp_path) -> Path:
    """A symbol-level target↔dependency store: one target symbol with direct/unique/
    unresolved/ambiguous edges into its dependencies; returns its source.db path."""
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


# A C++-only index with NO module-marker file in `files` — exactly what the real
# L1 indexer produces for a cpp project (.uplugin/.Build.cs are not indexed). Used
# to lock the plugin-root autodetect SOUNDNESS guard.
_NM_FILES = [
    (1, "MyPlugin/Source/Foo.h", ".h", "class Foo : public AActor {};\n"),
    (2, "Engine/Source/Actor.h", ".h", "class AActor {};\n"),
]
_NM_SYMS = [
    (1, 1, "Foo", "class", 1, 1, None),     # would-be target
    (2, 2, "AActor", "class", 1, 1, None),  # would-be dependency
]
_NM_EDGES = [
    (1, 1, 1, 2, None, "extends"),          # Foo -> AActor
]


@pytest.fixture
def cpp_no_marker_store(tmp_path) -> Path:
    """A cpp index with NO *.uplugin/*.Build.cs marker indexed (the real-index case).

    ``boundary.has_module_markers`` must be False here, so target-root autodetect is
    untrustworthy and the CLI must refuse without an explicit --target-root.
    """
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


_MOD_FILES = [
    (1, "modA/CMakeLists.txt", ".txt", ""),
    (2, "modA/x.py", ".py", "import modB.y\n"),
    (3, "modB/CMakeLists.txt", ".txt", ""),
    (4, "modB/y.py", ".py", "class Y:\n    pass\n"),
]


@pytest.fixture
def module_store(tmp_path) -> Path:
    """A 2-module store (modA imports modB), each with a CMakeLists.txt marker."""
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
