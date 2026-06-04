# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L3 —— 查询轨迹存储库的命令行工具（仅依赖标准库）。

对应规范第 9 节。一个跨会话的查询轨迹存储库，分为两类:
  static  —— 持久、可复用的查询模式；永不自动搁置。
  dynamic —— 绑定当前代码状态的发现；携带 valid_date 与
            file_state（json [{path,mtime,size}]）；当任一记录文件的
            当前 mtime/size 发生变化、或超过年龄阈值（--stale-days，默认 30）时变为“陈旧”。

陈旧的 dynamic 轨迹永不自动删除。preflight 会以 "(stale?)" 标记把它们呈现出来，
供遍历器向用户询问；之后由用户执行 keep / shelve / clear。

存储（v2）: 轨迹数据库位于项目本地存储库内部，路径为
`<store>/short/traces/trace.db` —— 短期且被 gitignore（动态、绑定版本、提交后手动清理）。
没有 `~/.manyread` 主目录；存储库通过从 cwd 向上回溯发现
（或经由 --store / --root / MANYREAD_STORE 指定）。

运行方式: 先解析插件根目录，再执行 `uv run --python 3.12 "$MR/scripts/trace.py" <subcmd> ...`
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


#### 轨迹存储库的 schema 定义（规范第 9 节，规范性） [@380kkm 2026-06-05] ####
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

#### 动态轨迹判定陈旧的默认年龄阈值（天） [@380kkm 2026-06-05] ####
DEFAULT_STALE_DAYS = 30


#### 从命令行参数解析项目配置（即定位存储库） [@380kkm 2026-06-05] ####
def _cfg(args: argparse.Namespace) -> config.ProjectConfig:
    """从命令行参数解析项目配置（并由此确定存储库）。"""
    return config.resolve_project(root=getattr(args, "root", None),
                                  store=getattr(args, "store", None))


#### 返回本存储库的轨迹数据库路径 [@380kkm 2026-06-05] ####
def trace_path(cfg: config.ProjectConfig) -> Path:
    """本存储库的轨迹数据库: <store>/short/traces/trace.db（临时、被 gitignore）。"""
    cfg.short_traces_dir.mkdir(parents=True, exist_ok=True)
    return cfg.short_traces_dir / "trace.db"


#### 打开存储库的轨迹数据库并确保 schema 就绪 [@380kkm 2026-06-05] ####
def connect(cfg: config.ProjectConfig):
    """打开存储库的轨迹数据库（按需创建父目录）并确保 schema 已建立。"""
    conn = db.connect(trace_path(cfg))
    conn.executescript(TRACE_SCHEMA_SQL)
    conn.commit()
    return conn


#### 返回今天的 ISO 日期字符串 [@380kkm 2026-06-05] ####
def today_str() -> str:
    return date.today().isoformat()


#### 把逗号分隔的文件参数解析为路径列表 [@380kkm 2026-06-05] ####
def parse_files_arg(files: str | None) -> list[str]:
    if not files:
        return []
    return [f for f in (x.strip() for x in files.split(",")) if f]


#### 为给定路径采集 [{path, mtime, size}] 文件状态 [@380kkm 2026-06-05] ####
def capture_file_state(paths: list[str], cfg: config.ProjectConfig) -> list[dict]:
    """为给定（相对根目录或绝对）路径采集 [{path, mtime, size}] 文件状态。"""
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
#### /采集文件状态 ####


#### 把记录的路径解析为可 stat 的当前路径（绝对或相对根目录） [@380kkm 2026-06-05] ####
def _resolve_for_state(path_str: str, cfg: config.ProjectConfig) -> Path:
    """把记录的路径解析为可对当前状态 stat 的路径（绝对，或相对根目录）。"""
    p = Path(path_str)
    if p.exists():
        return p
    cand = Path(cfg.root) / path_str
    return cand if cand.exists() else p


#### 判定动态轨迹行是否陈旧并给出原因 [@380kkm 2026-06-05] ####
def is_stale(row_kind: str, valid_date: str | None, file_state: str | None,
             cfg: config.ProjectConfig, stale_days: int) -> tuple[bool, str]:
    """返回某动态轨迹行的 (是否陈旧, 原因)。static 行永不陈旧。

    参数:
        row_kind: 轨迹类别（static 或 dynamic）。
        valid_date: 该行的有效日期（ISO 字符串），用于年龄判定。
        file_state: 记录的文件状态 json 字符串。
        cfg: 项目配置，用于解析记录路径。
        stale_days: 年龄阈值（天）。

    返回:
        (stale, reason) 二元组；非陈旧时 reason 为空串。
    """
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
#### /判定是否陈旧 ####


#### 取某条日志最新一条备注的状态 [@380kkm 2026-06-05] ####
def _status_of(conn, log_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM query_notes WHERE log_id=? ORDER BY id DESC LIMIT 1",
        (log_id,),
    ).fetchone()
    return row[0] if row else None


#### 把字符串折叠为单行（压缩空白） [@380kkm 2026-06-05] ####
def _oneline(s: str | None) -> str:
    return " ".join(s.split()) if s else ""


#### 判定文本是否（不分大小写）包含全部检索词 [@380kkm 2026-06-05] ####
def _matches(text: str | None, terms: list[str]) -> bool:
    if not terms:
        return True
    hay = (text or "").lower()
    return all(t.lower() in hay for t in terms)


#### init 子命令：创建存储库的轨迹数据库 [@380kkm 2026-06-05] ####
def cmd_init(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    connect(cfg).close()
    print(f"initialized trace store: {trace_path(cfg)}")
    return 0
#### /init 子命令 ####


#### log 子命令：向轨迹存储库追加一条查询 [@380kkm 2026-06-05] ####
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
#### /log 子命令 ####


#### preflight 子命令：先列 static/带标注，再列活跃 dynamic（带陈旧标记） [@380kkm 2026-06-05] ####
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
#### /preflight 子命令 ####


#### search 子命令：检索轨迹存储库 [@380kkm 2026-06-05] ####
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
#### /search 子命令 ####


#### tag 子命令：为日志行添加备注/标签（可提升为 static） [@380kkm 2026-06-05] ####
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
#### /tag 子命令 ####


#### stale 子命令：列出文件状态已偏离的动态轨迹 [@380kkm 2026-06-05] ####
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
#### /stale 子命令 ####


#### 设置某日志行最新备注的状态（无备注则插入一条） [@380kkm 2026-06-05] ####
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
#### /设置状态 ####


#### keep 子命令：刷新动态轨迹的 valid_date/file_state 并标记活跃 [@380kkm 2026-06-05] ####
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
#### /keep 子命令 ####


#### shelve 子命令：把日志行状态置为 shelved（从 preflight 隐藏） [@380kkm 2026-06-05] ####
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
#### /shelve 子命令 ####


#### clear 子命令：把日志行状态置为 cleared [@380kkm 2026-06-05] ####
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
#### /clear 子命令 ####


#### 为子命令解析器添加 --root/--store 存储库定位参数 [@380kkm 2026-06-05] ####
def _add_store(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--root", default=None, help="source tree root (default: store's parent)")
    sp.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
#### /添加存储库定位参数 ####


#### 构建命令行参数解析器并挂载各子命令 [@380kkm 2026-06-05] ####
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
#### /构建参数解析器 ####


#### CLI 入口：解析参数并分派到对应子命令 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
#### /CLI 入口 ####


if __name__ == "__main__":
    raise SystemExit(main())
