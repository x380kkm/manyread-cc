# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L3 — query-trace store CLI (stdlib only).

Spec section 9. A cross-session query-trace store split into:
  static  = durable, reusable query *pattern*; never auto-shelved.
  dynamic = a finding tied to current code state; carries valid_date +
            file_state (json [{path,mtime,size}]); becomes "stale" when any
            recorded file's current mtime/size differs, or it exceeds an age
            threshold (--stale-days, default 30).

Stale dynamic traces are NEVER auto-deleted. preflight surfaces them flagged
"(stale?)" so the agent can ask the user; the user then runs keep / shelve / clear.

Storage (v2): the trace db lives INSIDE the project-local store, under
`<store>/short/traces/trace.db` — short-term + gitignored (dynamic, version-tied,
manually cleared after commit). No `~/.manyread` home dir; the store is discovered
by walking up from cwd (or via --store / --root / MANYREAD_STORE).

Runtime: resolve the plugin root, then `uv run --python 3.12 "$MR/scripts/trace.py" <subcmd> ...`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db  # noqa: E402


# --- Trace store schema (spec section 9; NORMATIVE) -------------------------
TRACE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    project TEXT NOT NULL,
    db_path TEXT,
    sql_text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'dynamic',       -- static | dynamic
    task_tag TEXT,
    valid_date TEXT,
    file_state TEXT,
    imported_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS query_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER REFERENCES query_log(id),
    note TEXT,
    tag TEXT,
    status TEXT NOT NULL DEFAULT 'active',       -- active | shelved | cleared
    created_at INTEGER
);

CREATE VIEW IF NOT EXISTS query_trace AS
    SELECT ql.id, ql.ts, ql.project, ql.kind, ql.task_tag, ql.valid_date, ql.sql_text,
           qn.note, qn.tag, qn.status
    FROM query_log ql LEFT JOIN query_notes qn ON qn.log_id = ql.id;

CREATE INDEX IF NOT EXISTS idx_ql_project ON query_log(project);
CREATE INDEX IF NOT EXISTS idx_ql_kind ON query_log(kind);
"""

DEFAULT_STALE_DAYS = 30


# --- store / connection -----------------------------------------------------
def _cfg(args: argparse.Namespace) -> config.ProjectConfig:
    """Resolve the project config (and thus the store) from CLI args."""
    return config.resolve_project(root=getattr(args, "root", None),
                                  store=getattr(args, "store", None))


def trace_path(cfg: config.ProjectConfig) -> Path:
    """This store's trace db: <store>/short/traces/trace.db (ephemeral, gitignored)."""
    cfg.short_traces_dir.mkdir(parents=True, exist_ok=True)
    return cfg.short_traces_dir / "trace.db"


def connect(cfg: config.ProjectConfig):
    """Open (creating parent dirs) the store's trace db and ensure schema."""
    conn = db.connect(trace_path(cfg))
    conn.executescript(TRACE_SCHEMA_SQL)
    conn.commit()
    return conn


def today_str() -> str:
    return date.today().isoformat()


def parse_files_arg(files: str | None) -> list[str]:
    if not files:
        return []
    return [f for f in (x.strip() for x in files.split(",")) if f]


def capture_file_state(paths: list[str], cfg: config.ProjectConfig) -> list[dict]:
    """Capture [{path, mtime, size}] for given (root-relative or absolute) paths."""
    state: list[dict] = []
    for raw in paths:
        p = raw.strip()
        if not p:
            continue
        fs = _resolve_for_state(p, cfg)
        if fs.exists():
            st = fs.stat()
            state.append({"path": p, "mtime": int(st.st_mtime), "size": int(st.st_size)})
        else:
            state.append({"path": p, "mtime": None, "size": None})
    return state


def _resolve_for_state(path_str: str, cfg: config.ProjectConfig) -> Path:
    """Resolve a recorded path for a current-state stat (absolute, or root-relative)."""
    p = Path(path_str)
    if p.exists():
        return p
    cand = Path(cfg.root) / path_str
    return cand if cand.exists() else p


def is_stale(row_kind: str, valid_date: str | None, file_state: str | None,
             cfg: config.ProjectConfig, stale_days: int) -> tuple[bool, str]:
    """Return (stale, reason) for a dynamic row. static rows are never stale."""
    if row_kind != "dynamic":
        return (False, "")
    if valid_date:
        try:
            vd = datetime.strptime(valid_date, "%Y-%m-%d").date()
            age = (date.today() - vd).days
            if age > stale_days:
                return (True, f"age {age}d > {stale_days}d")
        except ValueError:
            pass
    if file_state:
        try:
            entries = json.loads(file_state)
        except (json.JSONDecodeError, TypeError):
            entries = []
        for ent in entries:
            path_str = ent.get("path")
            if not path_str:
                continue
            fs = _resolve_for_state(path_str, cfg)
            if not fs.exists():
                return (True, f"missing: {path_str}")
            st = fs.stat()
            rec_m = ent.get("mtime")
            rec_s = ent.get("size")
            if rec_m is None or int(st.st_mtime) != int(rec_m):
                return (True, f"mtime changed: {path_str}")
            if rec_s is None or int(st.st_size) != int(rec_s):
                return (True, f"size changed: {path_str}")
    return (False, "")


def _status_of(conn, log_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM query_notes WHERE log_id=? ORDER BY id DESC LIMIT 1",
        (log_id,),
    ).fetchone()
    return row[0] if row else None


def _oneline(s: str | None) -> str:
    return " ".join(s.split()) if s else ""


def _matches(text: str | None, terms: list[str]) -> bool:
    if not terms:
        return True
    hay = (text or "").lower()
    return all(t.lower() in hay for t in terms)


# --- subcommands ------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    connect(cfg).close()
    print(f"initialized trace store: {trace_path(cfg)}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    project = args.project or cfg.alias
    kind = args.kind or "dynamic"
    valid_date = None
    file_state_json = None
    if kind == "dynamic":
        valid_date = today_str()
        file_state_json = json.dumps(capture_file_state(parse_files_arg(args.files), cfg))
    cur = conn.execute(
        "INSERT INTO query_log(ts, project, db_path, sql_text, kind, task_tag, valid_date, file_state) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (int(time.time()), project, args.db, args.sql, kind, args.task, valid_date, file_state_json),
    )
    conn.commit()
    log_id = cur.lastrowid
    conn.close()
    print(f"logged id={log_id} kind={kind} project={project}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    project = cfg.alias
    terms = list(args.terms or [])
    stale_days = args.stale_days
    limit = args.limit

    rows = conn.execute(
        "SELECT id, ts, project, db_path, sql_text, kind, task_tag, valid_date, file_state "
        "FROM query_log WHERE project=? ORDER BY ts DESC",
        (project,),
    ).fetchall()

    static_rows: list[tuple] = []
    dynamic_rows: list[tuple] = []
    for r in rows:
        log_id, ts, proj, dbp, sql_text, kind, task_tag, valid_date, file_state = r
        if _status_of(conn, log_id) in ("shelved", "cleared"):
            continue
        nrow = conn.execute(
            "SELECT tag, note FROM query_notes WHERE log_id=? ORDER BY id DESC LIMIT 1",
            (log_id,),
        ).fetchone()
        tag, note = (nrow[0], nrow[1]) if nrow else (None, None)
        blob = " ".join(str(x) for x in (sql_text, task_tag, tag, note) if x)
        if not _matches(blob, terms):
            continue
        if kind == "static" or note is not None or tag is not None:
            static_rows.append((log_id, kind, task_tag, sql_text, tag, note))
        else:
            stale, reason = is_stale(kind, valid_date, file_state, cfg, stale_days)
            dynamic_rows.append((log_id, kind, task_tag, valid_date, sql_text, stale, reason))
    conn.close()

    out: list[str] = [f"# preflight: project={project}" + (f" terms={terms}" if terms else "")]
    out.append("## static / tagged (durable)")
    if not static_rows:
        out.append("  (none)")
    for log_id, kind, task_tag, sql_text, tag, note in static_rows[:limit]:
        label = f"[{log_id}] {kind}"
        if task_tag:
            label += f" task={task_tag}"
        if tag:
            label += f" tag={tag}"
        out.append(f"  {label}")
        out.append(f"      sql: {_oneline(sql_text)}")
        if note:
            out.append(f"      note: {note}")
    out.append("## dynamic (most recent active; (stale?) = ask user keep/shelve/clear)")
    if not dynamic_rows:
        out.append("  (none)")
    for log_id, kind, task_tag, valid_date, sql_text, stale, reason in dynamic_rows[:limit]:
        flag = f"  (stale? {reason})" if stale else ""
        label = f"[{log_id}] dynamic valid={valid_date}"
        if task_tag:
            label += f" task={task_tag}"
        out.append(f"  {label}{flag}")
        out.append(f"      sql: {_oneline(sql_text)}")
    print("\n".join(out))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    project = cfg.alias
    terms = list(args.terms or [])
    rows = conn.execute(
        "SELECT id, ts, project, kind, task_tag, valid_date, sql_text "
        "FROM query_log WHERE project=? ORDER BY ts DESC",
        (project,),
    ).fetchall()
    printed = 0
    for r in rows:
        log_id, ts, proj, kind, task_tag, valid_date, sql_text = r
        nrow = conn.execute(
            "SELECT tag, note, status FROM query_notes WHERE log_id=? ORDER BY id DESC LIMIT 1",
            (log_id,),
        ).fetchone()
        tag = note = status = None
        if nrow:
            tag, note, status = nrow
        blob = " ".join(str(x) for x in (sql_text, task_tag, tag, note) if x)
        if not _matches(blob, terms):
            continue
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        line = f"[{log_id}] {kind} {when} project={proj}"
        if status:
            line += f" status={status}"
        if task_tag:
            line += f" task={task_tag}"
        if tag:
            line += f" tag={tag}"
        print(line)
        print(f"      sql: {_oneline(sql_text)}")
        if note:
            print(f"      note: {note}")
        printed += 1
    conn.close()
    if printed == 0:
        print("(no matches)")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    if not conn.execute("SELECT id FROM query_log WHERE id=?", (args.log_id,)).fetchone():
        conn.close()
        print(f"error: no query_log row with id={args.log_id}", file=sys.stderr)
        return 1
    conn.execute(
        "INSERT INTO query_notes(log_id, note, tag, status, created_at) VALUES(?,?,?,?,?)",
        (args.log_id, args.note, args.tag, "active", int(time.time())),
    )
    if args.kind == "static":
        conn.execute("UPDATE query_log SET kind='static' WHERE id=?", (args.log_id,))
    conn.commit()
    conn.close()
    promo = " (promoted to static)" if args.kind == "static" else ""
    print(f"tagged id={args.log_id} tag={args.tag}{promo}")
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    rows = conn.execute(
        "SELECT id, project, valid_date, file_state, sql_text, kind "
        "FROM query_log WHERE project=? AND kind='dynamic' ORDER BY ts DESC",
        (cfg.alias,),
    ).fetchall()
    found = 0
    for log_id, proj, valid_date, file_state, sql_text, kind in rows:
        if _status_of(conn, log_id) in ("shelved", "cleared"):
            continue
        stale, reason = is_stale(kind, valid_date, file_state, cfg, args.stale_days)
        if stale:
            print(f"[{log_id}] dynamic valid={valid_date} STALE: {reason}")
            print(f"      sql: {_oneline(sql_text)}")
            found += 1
    conn.close()
    if found == 0:
        print("(no stale dynamic traces)")
    return 0


def _set_status(conn, log_id: int, status: str) -> bool:
    if not conn.execute("SELECT id FROM query_log WHERE id=?", (log_id,)).fetchone():
        return False
    existing = conn.execute(
        "SELECT id FROM query_notes WHERE log_id=? ORDER BY id DESC LIMIT 1", (log_id,)
    ).fetchone()
    if existing:
        conn.execute("UPDATE query_notes SET status=? WHERE id=?", (status, existing[0]))
    else:
        conn.execute(
            "INSERT INTO query_notes(log_id, note, tag, status, created_at) VALUES(?,?,?,?,?)",
            (log_id, None, None, status, int(time.time())),
        )
    return True


def cmd_keep(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    row = conn.execute(
        "SELECT id, project, file_state, kind FROM query_log WHERE id=?", (args.log_id,)
    ).fetchone()
    if not row:
        conn.close()
        print(f"error: no query_log row with id={args.log_id}", file=sys.stderr)
        return 1
    log_id, project, file_state, kind = row
    new_valid = today_str()
    new_state_json = file_state
    if file_state:
        try:
            entries = json.loads(file_state)
        except (json.JSONDecodeError, TypeError):
            entries = []
        refreshed = []
        for ent in entries:
            path_str = ent.get("path")
            if not path_str:
                continue
            fs = _resolve_for_state(path_str, cfg)
            if fs.exists():
                st = fs.stat()
                refreshed.append({"path": path_str, "mtime": int(st.st_mtime), "size": int(st.st_size)})
            else:
                refreshed.append({"path": path_str, "mtime": None, "size": None})
        new_state_json = json.dumps(refreshed)
    conn.execute("UPDATE query_log SET valid_date=?, file_state=? WHERE id=?",
                 (new_valid, new_state_json, log_id))
    _set_status(conn, log_id, "active")
    conn.commit()
    conn.close()
    print(f"kept id={log_id} (valid_date refreshed to {new_valid}, file_state recaptured)")
    return 0


def cmd_shelve(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    ok = _set_status(conn, args.log_id, "shelved")
    conn.commit()
    conn.close()
    if not ok:
        print(f"error: no query_log row with id={args.log_id}", file=sys.stderr)
        return 1
    print(f"shelved id={args.log_id}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    conn = connect(cfg)
    ok = _set_status(conn, args.log_id, "cleared")
    conn.commit()
    conn.close()
    if not ok:
        print(f"error: no query_log row with id={args.log_id}", file=sys.stderr)
        return 1
    print(f"cleared id={args.log_id}")
    return 0


# --- argparse wiring --------------------------------------------------------
def _add_store(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--root", default=None, help="source tree root (default: store's parent)")
    sp.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trace.py", description="manyread L3 trace store")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create the store's trace db")
    _add_store(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("log", help="append a query to the trace store")
    sp.add_argument("--sql", required=True)
    sp.add_argument("--project", default=None, help="project label (default: store alias)")
    sp.add_argument("--kind", choices=["static", "dynamic"], default="dynamic")
    sp.add_argument("--task", default=None)
    sp.add_argument("--db", default=None)
    sp.add_argument("--files", help="comma-separated file paths to capture state for")
    _add_store(sp)
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("preflight", help="static first, then active dynamic (+stale flag)")
    sp.add_argument("terms", nargs="*")
    sp.add_argument("--limit", type=int, default=12)
    sp.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS, dest="stale_days")
    _add_store(sp)
    sp.set_defaults(func=cmd_preflight)

    sp = sub.add_parser("search", help="search the trace store")
    sp.add_argument("terms", nargs="*")
    _add_store(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("tag", help="add a note/tag to a log row (optionally promote to static)")
    sp.add_argument("log_id", type=int)
    sp.add_argument("tag")
    sp.add_argument("note")
    sp.add_argument("--kind", choices=["static", "dynamic"], default=None)
    _add_store(sp)
    sp.set_defaults(func=cmd_tag)

    sp = sub.add_parser("stale", help="list dynamic traces whose file_state diverged")
    sp.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS, dest="stale_days")
    _add_store(sp)
    sp.set_defaults(func=cmd_stale)

    for name, fn, helptext in [("keep", cmd_keep, "refresh valid_date/file_state; mark active"),
                               ("shelve", cmd_shelve, "status=shelved (hidden from preflight)"),
                               ("clear", cmd_clear, "status=cleared")]:
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("log_id", type=int)
        _add_store(sp)
        sp.set_defaults(func=fn)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
