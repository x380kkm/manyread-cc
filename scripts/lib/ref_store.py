# audience: internal
# lib.ref_store
"""ref 命名 + 定位 + 清单 + 选源/裁剪的纯辅助层（仅依赖标准库）。

承载 ref CLI（``ref.py``）的无命令编排逻辑：slug 与 ref id 生成、项目配置解析与 ref 目录
定位、``ref.json`` 清单读写、从项目 db 选源、副本命名去重、git worktree 装配、refs_dir 扫描，
以及 ``strip-ifdef`` 所需的预处理 span 取算与删除。

``ref.py`` 在末尾把本模块全部公共名再导出，外部一律经 ``ref.py`` 的 CLI 入口运行；本模块
保持导入安全（导入时无任何副作用）。源路径在清单中一律相对项目根存储（§5）。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import config

#### 固定构建日期 [@380kkm 2026-06-05] ####
REF_DATE = "2026-05-28"


#### 把自由格式任务串转成文件系统安全的 slug [@380kkm 2026-06-05] ####
def slugify(task: str) -> str:
    """空输入回退为 "ref"。"""
    s = task.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "ref"


#### 组装 ref id = <date>-<task-slug> [@380kkm 2026-06-05] ####
def make_ref_id(task: str, date: str = REF_DATE) -> str:
    return f"{date}-{slugify(task)}"


#### 从 --store / --root（或由 cwd 发现，§5）解析项目配置 [@380kkm 2026-06-05] ####
def resolve_cfg(store: str | None, root: str | None) -> config.ProjectConfig:
    return config.resolve_project(root=root, store=store)


#### 在解析出的 store 的 refs_dir 下定位 ref_id 的目录 [@380kkm 2026-06-05] ####
def find_ref(ref_id: str, store: str | None, root: str | None) -> tuple[config.ProjectConfig, Path]:
    cfg = resolve_cfg(store, root)
    ref_dir = Path(cfg.refs_dir) / ref_id
    if ref_dir.exists():
        return cfg, ref_dir
    raise SystemError(f"ref '{ref_id}' not found under {cfg.refs_dir}")


#### 读取并解析 ref 目录下的 ref.json 清单 [@380kkm 2026-06-05] ####
def load_manifest(ref_dir: Path) -> dict:
    mpath = ref_dir / "ref.json"
    if not mpath.exists():
        raise SystemError(f"manifest missing: {mpath}")
    return json.loads(mpath.read_text(encoding="utf-8"))


#### 把清单写回 ref 目录下的 ref.json [@380kkm 2026-06-05] ####
def save_manifest(ref_dir: Path, manifest: dict) -> None:
    mpath = ref_dir / "ref.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


#### 直接对项目 db 跑 SQL，返回 'path' 列的取值 [@380kkm 2026-06-05] ####
def select_from_query(db_path: Path, sql: str) -> list[str]:
    """查询必须产出一个名为 'path' 的列（大小写不敏感）。"""
    if not db_path.exists():
        raise SystemError(
            f"project db not found: {db_path} (build it with index_build.py first)"
        )
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(sql)
        cols = [d[0].lower() for d in cur.description] if cur.description else []
        if "path" not in cols:
            raise SystemError(
                f"--from-query must return a 'path' column; got columns {cols}"
            )
        idx = cols.index("path")
        rows = cur.fetchall()
    finally:
        conn.close()
    paths: list[str] = []
    seen: set[str] = set()
    for r in rows:
        val = r[idx]
        if val is None:
            continue
        p = str(val).strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


#### 把逗号分隔的 --files 参数解析为路径列表 [@380kkm 2026-06-05] ####
def parse_files_arg(files: str | None) -> list[str]:
    if not files:
        return []
    return [f for f in (x.strip() for x in files.split(",")) if f]


#### 尽力取文件的 git blob/commit sha；非 git 跟踪时返回 None [@380kkm 2026-06-05] ####
def _git_rev(root: Path, rel_path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", f"HEAD:{rel_path}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            sha = out.stdout.strip()
            if sha:
                return sha
    except (OSError, FileNotFoundError):
        pass
    return None


#### 把选定路径归一化为相对项目根的 posix 串 [@380kkm 2026-06-05] ####
def _to_relative(root: Path, raw: str) -> str | None:
    root = Path(root).resolve()
    p = Path(raw)
    if not p.is_absolute():
        candidate = (root / raw).resolve()
    else:
        candidate = p.resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None
    return rel.as_posix()


#### 判断 root 是否为 git 仓库 [@380kkm 2026-06-05] ####
def is_git_repo(root: Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        return out.returncode == 0 and out.stdout.strip() == "true"
    except (OSError, FileNotFoundError):
        return False


#### 在 files/ 下挑选副本文件名，消解 basename 冲突 [@380kkm 2026-06-05] ####
def _unique_copy_name(rel: str, used: set[str]) -> str:
    base = Path(rel).name
    if base not in used:
        return base
    stem = Path(base).stem
    suffix = Path(base).suffix
    # 以父目录的 slug 作前缀来消歧。
    parent = slugify(str(Path(rel).parent))
    candidate = f"{parent}-{base}" if parent and parent != "ref" else base
    n = 1
    name = candidate
    while name in used:
        name = f"{stem}-{n}{suffix}"
        n += 1
    return name


#### 建一个 git worktree 托管隔离分支目录，返回相对路径 [@380kkm 2026-06-05] ####
def _add_worktree(root: Path, ref_dir: Path, ref_id: str) -> str | None:
    if not is_git_repo(root):
        print("warning: --worktree ignored: project root is not a git repo", file=sys.stderr)
        return None
    wt_abs = ref_dir / "worktree"
    branch = f"manyread/{ref_id}"
    # 删除陈旧目录
    if wt_abs.exists():
        shutil.rmtree(wt_abs, ignore_errors=True)
    out = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(wt_abs)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        # 去掉 -b 重试
        out2 = subprocess.run(
            ["git", "worktree", "add", str(wt_abs), branch],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if out2.returncode != 0:
            print(
                f"warning: git worktree add failed: {out.stderr.strip()} / {out2.stderr.strip()}",
                file=sys.stderr,
            )
            return None
    try:
        return wt_abs.resolve().relative_to(root).as_posix()
    except ValueError:
        return wt_abs.as_posix()


#### 加载 refs_dir 下全部 ref 清单（跳过不可读的） [@380kkm 2026-06-05] ####
def _scan_refs(refs_dir: Path) -> list[dict]:
    out: list[dict] = []
    if not refs_dir.exists():
        return out
    for child in sorted(refs_dir.iterdir()):
        if not child.is_dir():
            continue
        mpath = child / "ref.json"
        if not mpath.exists():
            continue
        try:
            m = json.loads(mpath.read_text(encoding="utf-8"))
            m["_dir"] = str(child)
            out.append(m)
        except (json.JSONDecodeError, OSError):
            continue
    return out


#### 取某源路径（相对根）的 ifdef_branch 符号 span [@380kkm 2026-06-05] ####
def _ifdef_spans_for(conn: sqlite3.Connection, src_rel: str) -> list[dict]:
    row = conn.execute("SELECT id FROM files WHERE path=?", (src_rel,)).fetchone()
    if not row:
        return []
    file_id = row[0]
    cur = conn.execute(
        "SELECT name, start_line, end_line FROM symbols "
        "WHERE file_id=? AND kind='ifdef_branch' ORDER BY start_line",
        (file_id,),
    )
    spans: list[dict] = []
    for name, start_line, end_line in cur.fetchall():
        if start_line is None or end_line is None:
            continue
        spans.append({"name": name or "", "start_line": int(start_line), "end_line": int(end_line)})
    return spans


#### 判断某预处理 span 的控制条件是否提及被保留的宏 [@380kkm 2026-06-05] ####
def _span_keeps(name: str, keep: set[str]) -> bool:
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))
    return bool(tokens & keep)


#### 从副本删除不匹配的预处理 span（1 起含端点的行） [@380kkm 2026-06-05] ####
def _strip_spans_from_copy(copy_abs: Path, spans: list[dict], keep: set[str]) -> int:
    text = copy_abs.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    n = len(lines)

    # 1 起下标，留余量
    drop_line: list[bool] = [False] * (n + 2)
    dropped_count = 0
    for sp in spans:
        if _span_keeps(sp["name"], keep):
            continue
        s = max(1, sp["start_line"])
        e = min(n, sp["end_line"])
        if e < s:
            continue
        dropped_count += 1
        for ln in range(s, e + 1):
            drop_line[ln] = True

    if dropped_count == 0:
        return 0

    kept_lines = [lines[i - 1] for i in range(1, n + 1) if not drop_line[i]]
    copy_abs.write_text("".join(kept_lines), encoding="utf-8")
    return dropped_count
