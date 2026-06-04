# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread query.py —— 对 project db 执行 SQL，并自动记入 trace 库。

对应 spec 第 7 节。对一个 project 的 <store>/source.db 执行任意 SQL，把结果行以
TSV 打印，并（除非 --no-log）按 trace.py 的 log 语义向该 store 的 trace 库
（<store>/short/traces/trace.db）追加一行：

  * kind 默认 dynamic，或用 --static 记为 static
  * valid_date = 今天（dynamic 行；由 trace.py 处理）
  * 对 SQL 中引用、且确实存在于 project ``files`` 表里的文件路径捕获 file_state
    （尽力而为）。

本脚本替代了旧的 bash sqlite3 PATH 拦截 wrapper —— 效果相同（query 被记录），但跨
平台且不玩 PATH 把戏。skill 指示 agent 通过 query.py 查询，使记录自动发生。

CLI：  query.py "<SQL>" [--root PATH | --store PATH] [--static] [--task TAG] [--no-log]

运行时：解析出 plugin root，然后 ``uv run --python 3.12 "$MR/scripts/query.py" ...``
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
# 同级脚本：复用其 log 语义
import trace  # noqa: E402


#### 对 project db 执行 SQL，返回列名与结果行 [@380kkm 2026-06-05] ####
def execute_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple]]:
    """对 project db 执行 SQL。返回 (column_names, rows)。

    db 以读写方式打开（某些探查可能用到临时表）；常见情形下调用方传入纯 SELECT。
    列名取自 cursor.description。
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


#### 把一个单元格渲染为 TSV：转字符串并中和 tab/换行 [@380kkm 2026-06-05] ####
def _tsv_cell(value) -> str:
    """把一个单元格渲染为 TSV 输出：转字符串，中和 tab/换行。"""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    # 保持一行 == 一条记录：折叠内嵌的 tab/换行，使列对齐
    return s.replace("\t", "    ").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


#### 把结果行以带表头的 TSV 打印 [@380kkm 2026-06-05] ####
def print_tsv(cols: list[str], rows: list[tuple]) -> None:
    """把结果行以 TSV 打印，并带一行表头（列名）。"""
    if cols:
        print("\t".join(cols))
    for row in rows:
        print("\t".join(_tsv_cell(v) for v in row))


#### 匹配 SQL 里像文件路径的字符串字面量 [@380kkm 2026-06-05] ####
_LITERAL_RE = re.compile(r"""['"]([^'"]+)['"]""")


#### 找出 SQL 中引用、且存在于 files 表里的文件路径 [@380kkm 2026-06-05] ####
def referenced_paths(db_path: Path, sql: str) -> list[str]:
    """尽力而为：SQL 中引用、且存在于 ``files`` 里的文件路径。

    返回与 ``files`` 表存储形式完全一致的相对 root 路径，以便 trace.py 在后续 stale
    检查时能相对 project root 重新解析。同时匹配精确相等（``path = 'a/b.cpp'``）与
    LIKE/子串模式（``path LIKE '%RHI.cpp%'``）对实际已索引路径的匹配。
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
        # 剥掉常见的 SQL LIKE 通配符 / glob 字符，使模式能匹配路径
        core = lit.strip().strip("%").strip("*")
        if not core:
            continue
        # 1. 与已索引路径精确匹配
        if lit in indexed_set and lit not in seen:
            found.append(lit)
            seen.add(lit)
            continue
        if core in indexed_set and core not in seen:
            found.append(core)
            seen.add(core)
            continue
        # 2. 子串 / 后缀匹配（覆盖 LIKE '%foo.cpp%' 与裸 basename）
        for p in indexed:
            if p in seen:
                continue
            if core == p or core in p or p.endswith(core):
                found.append(p)
                seen.add(p)
    return found


#### 按 trace.py 的 log 语义追加一行 trace，返回行 id [@380kkm 2026-06-05] ####
def log_trace(cfg: config.ProjectConfig, sql: str, static: bool,
              task: str | None, rel_paths: list[str]) -> int:
    """按 trace.py 的 log 语义追加一行 trace（以模块方式 import）。

    对 ``rel_paths`` 解析为绝对文件系统路径以正确读取 mtime/size，从而捕获
    file_state；但记录时仍存为相对 project 的路径（与 ``files`` 表一致），使后续
    stale 检查能相对 root 重新解析。
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


#### CLI 入口：执行 SQL、打印 TSV、自动记 trace [@380kkm 2026-06-05] ####
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

    #### 执行 SQL，把结果行以 TSV 打印 [@380kkm 2026-06-05] ####
    try:
        cols, rows = execute_sql(db_path, args.sql)
    except Exception as exc:
        print(f"error: SQL failed: {exc}", file=sys.stderr)
        return 1
    print_tsv(cols, rows)
    #### /执行并打印 ####

    #### 自动记入 trace 库（除非被抑制） [@380kkm 2026-06-05] ####
    if not args.no_log:
        rel_paths = referenced_paths(db_path, args.sql)
        log_id = log_trace(cfg, args.sql, args.static, args.task, rel_paths)
        kind = "static" if args.static else "dynamic"
        print(f"# logged trace id={log_id} kind={kind} project={cfg.alias}",
              file=sys.stderr)
    #### /自动记 trace ####

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
