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
