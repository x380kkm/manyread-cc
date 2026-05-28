# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread query.py — execute SQL against a project db + auto-log to the trace store.

Spec section 7. Runs arbitrary SQL against a project's <store>/source.db, prints
the result rows as TSV, and (unless --no-log) appends a row to the store's trace db
(<store>/short/traces/trace.db) using trace.py's log semantics:

  * kind = dynamic by default, or static with --static
  * valid_date = today (for dynamic rows; handled by trace.py)
  * file_state captured for any file paths referenced in the SQL that actually
    exist in the project's `files` table (best-effort).

This REPLACES the old bash sqlite3 PATH-intercept wrapper — same effect (queries
are logged), but cross-platform and with no PATH games. The skill instructs the
agent to query through query.py so logging happens automatically.

CLI:  query.py "<SQL>" [--root PATH | --store PATH] [--static] [--task TAG] [--no-log]

Runtime: resolve the plugin root, then `uv run --python 3.12 "$MR/scripts/query.py" ...`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db  # noqa: E402
import trace  # noqa: E402  (sibling script; reuse its log semantics)


def execute_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple]]:
    """Execute SQL against the project db. Returns (column_names, rows).

    The db is opened read/write (some probes may use temp tables); callers pass
    plain SELECTs in the common case. Column names come from cursor.description.
    """
    conn = db.connect(db_path)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        conn.commit()
        return cols, rows
    finally:
        conn.close()


def _tsv_cell(value) -> str:
    """Render one cell for TSV output: stringify, neutralize tabs/newlines."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    # Keep one row == one line: collapse embedded tab/newline so columns align.
    return s.replace("\t", "    ").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def print_tsv(cols: list[str], rows: list[tuple]) -> None:
    """Print rows as TSV with a header line (column names)."""
    if cols:
        print("\t".join(cols))
    for row in rows:
        print("\t".join(_tsv_cell(v) for v in row))


# Match plausible file-path-ish string literals inside the SQL. We then keep
# only those that actually exist as a `files.path` so file_state is grounded.
_LITERAL_RE = re.compile(r"""['"]([^'"]+)['"]""")


def referenced_paths(db_path: Path, sql: str) -> list[str]:
    """Best-effort: file paths referenced in the SQL that exist in `files`.

    Returns root-relative paths exactly as stored in the `files` table so that
    trace.py can re-resolve them against the project root on later stale checks.
    Matches both exact equality (`path = 'a/b.cpp'`) and LIKE/substring patterns
    (`path LIKE '%RHI.cpp%'`) against the actual indexed paths.
    """
    literals = _LITERAL_RE.findall(sql)
    if not literals:
        return []
    if not db_path.exists():
        return []

    conn = db.connect(db_path)
    try:
        try:
            indexed = [r[0] for r in conn.execute("SELECT path FROM files").fetchall()]
        except Exception:
            return []
    finally:
        conn.close()

    indexed_set = set(indexed)
    found: list[str] = []
    seen: set[str] = set()
    for lit in literals:
        # Strip common SQL LIKE wildcards/glob chars so a pattern can match a path.
        core = lit.strip().strip("%").strip("*")
        if not core:
            continue
        # 1. exact match against an indexed path
        if lit in indexed_set and lit not in seen:
            found.append(lit)
            seen.add(lit)
            continue
        if core in indexed_set and core not in seen:
            found.append(core)
            seen.add(core)
            continue
        # 2. substring/suffix match (covers LIKE '%foo.cpp%' and bare basenames)
        for p in indexed:
            if p in seen:
                continue
            if core == p or core in p or p.endswith(core):
                found.append(p)
                seen.add(p)
    return found


def log_trace(cfg: config.ProjectConfig, sql: str, static: bool,
              task: str | None, rel_paths: list[str]) -> int:
    """Append a trace row via trace.py's log semantics (imported as a module).

    file_state is captured for `rel_paths` resolved to absolute filesystem paths
    so mtime/size are read correctly; the paths are recorded as project-relative
    (matching the `files` table) so later stale checks re-resolve via the root.
    """
    kind = "static" if static else "dynamic"
    db_path_str = str(Path(cfg.db_path))

    conn = trace.connect(cfg)
    try:
        valid_date = None
        file_state_json = None
        if kind == "dynamic":
            valid_date = trace.today_str()
            fstate = []
            root = Path(cfg.root)
            for rel in rel_paths:
                abs_p = (root / rel)
                if abs_p.exists():
                    st = abs_p.stat()
                    fstate.append({"path": rel, "mtime": int(st.st_mtime),
                                   "size": int(st.st_size)})
                else:
                    fstate.append({"path": rel, "mtime": None, "size": None})
            file_state_json = json.dumps(fstate)
        cur = conn.execute(
            "INSERT INTO query_log(ts, project, db_path, sql_text, kind, task_tag, "
            "valid_date, file_state) VALUES(?,?,?,?,?,?,?,?)",
            (int(time.time()), cfg.alias, db_path_str, sql, kind,
             task, valid_date, file_state_json),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="query.py",
        description="manyread: execute SQL against a project db + auto-log to trace.",
    )
    parser.add_argument("sql", help="the SQL statement to execute")
    parser.add_argument("--root", help="source tree root (default: store's parent)", default=None)
    parser.add_argument("--store", help="explicit manyread store dir (default: discover from cwd)",
                        default=None)
    parser.add_argument("--static", action="store_true",
                        help="log this query as a durable static pattern (default: dynamic)")
    parser.add_argument("--task", help="task tag recorded with the trace row", default=None)
    parser.add_argument("--no-log", action="store_true", dest="no_log",
                        help="do not append a row to the trace store")
    args = parser.parse_args(argv)

    try:
        cfg = config.resolve_project(root=args.root, store=args.store)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    db_path = Path(cfg.db_path)
    if not db_path.exists():
        print(f"error: no index at {db_path}; run index_build.py first",
              file=sys.stderr)
        return 2

    # 1. Execute + print rows as TSV.
    try:
        cols, rows = execute_sql(db_path, args.sql)
    except Exception as exc:
        print(f"error: SQL failed: {exc}", file=sys.stderr)
        return 1
    print_tsv(cols, rows)

    # 2. Auto-log to the trace store (unless suppressed).
    if not args.no_log:
        rel_paths = referenced_paths(db_path, args.sql)
        log_id = log_trace(cfg, args.sql, args.static, args.task, rel_paths)
        kind = "static" if args.static else "dynamic"
        print(f"# logged trace id={log_id} kind={kind} project={cfg.alias}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
