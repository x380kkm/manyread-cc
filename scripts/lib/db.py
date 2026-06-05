# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# lib.db
"""manyread 数据库 schema DDL 与 sqlite 辅助函数（仅依赖标准库）。

项目数据库位于 <root>/.manyread/source.db。下面的 schema 是规范性的
（spec 第 6 节）：L1 填充 files+files_fts+meta；L2 填充 symbols+edges。
保持导入安全：导入时无任何副作用。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#### spec 第 6 节给出的确切 schema [@380kkm 2026-06-05] ####
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    ext TEXT,
    size INTEGER,
    mtime INTEGER,
    content TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    path,
    content,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_id INTEGER REFERENCES files(id),
    name TEXT,
    kind TEXT,
    lang TEXT,
    start_line INTEGER,
    end_line INTEGER,
    start_byte INTEGER,
    end_byte INTEGER,
    parent_id INTEGER,
    attrs TEXT,
    provenance TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    file_id INTEGER REFERENCES files(id),
    src_symbol_id INTEGER,
    dst_symbol_id INTEGER,
    dst_name TEXT,
    relation TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- indexes
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_symbol_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation);
"""


#### 打开项目数据库的 sqlite 连接（并创建父目录） [@380kkm 2026-06-05] ####
def connect(path: str | Path) -> sqlite3.Connection:
    """传入 ":memory:" 可得到内存数据库。"""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


#### 从 SCHEMA_SQL 建立所有表与索引（幂等） [@380kkm 2026-06-05] ####
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _migrate_symbol_columns(conn)
    conn.commit()


#### 为旧库补齐缺失的 symbols.attrs / symbols.provenance 列 [@380kkm 2026-06-05] ####
def _migrate_symbol_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    if "attrs" not in cols:
        conn.execute("ALTER TABLE symbols ADD COLUMN attrs TEXT")
    if "provenance" not in cols:
        conn.execute("ALTER TABLE symbols ADD COLUMN provenance TEXT")


#### 向 meta 表 upsert 一个键值对 [@380kkm 2026-06-05] ####
def set_meta(conn: sqlite3.Connection, k: str, v) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (k, str(v)),
    )
    conn.commit()


#### 读取 meta 表中某键的值，缺失返回 None [@380kkm 2026-06-05] ####
def get_meta(conn: sqlite3.Connection, k: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return row[0] if row else None
