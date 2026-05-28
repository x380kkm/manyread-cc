# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread DB schema DDL + sqlite helpers (stdlib only).

The project database lives at <root>/.manyread/source.db. The schema below is
NORMATIVE (spec section 6): L1 fills files+files_fts+meta; L2 fills symbols+edges.
Keep import-safe: NO side effects at import time.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Exact schema from spec section 6.
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


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a sqlite connection to the project db (creating parent dirs).

    Pass ":memory:" for an in-memory database (used by self-tests).
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes from SCHEMA_SQL (idempotent).

    Also migrates a pre-existing db: the `symbols` table gained `attrs` and
    `provenance` columns (spec section 16, override-rules layer). sqlite has no
    `ADD COLUMN IF NOT EXISTS`, so we PRAGMA table_info and add only what is
    missing — keeping older databases backward compatible.
    """
    conn.executescript(SCHEMA_SQL)
    _migrate_symbol_columns(conn)
    conn.commit()


def _migrate_symbol_columns(conn: sqlite3.Connection) -> None:
    """Add symbols.attrs / symbols.provenance to an existing db if absent."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    if "attrs" not in cols:
        conn.execute("ALTER TABLE symbols ADD COLUMN attrs TEXT")
    if "provenance" not in cols:
        conn.execute("ALTER TABLE symbols ADD COLUMN provenance TEXT")


def set_meta(conn: sqlite3.Connection, k: str, v) -> None:
    """Upsert a key/value pair into the meta table (value stored as text)."""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (k, str(v)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, k: str) -> str | None:
    """Return the meta value for key k, or None if absent."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return row[0] if row else None
