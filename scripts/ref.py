# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L4 —— ref / prune / worktree 命令行（仅标准库）。

规范第 10 节。一个 *ref* 是带日期、按任务打标签的阅读工作区，存放选定文件的
经裁剪 + 标注的副本。它位于
  <root>/.manyread/refs/<id>/      其中  id = <YYYY-MM-DD>-<task-slug>
并携带清单 `ref.json`、自由格式的 `notes.md`，以及一个存放副本的 `files/` 目录。

ref.json 字段（规范第 10 节，规范性）：
  id, task, date, source_project, status (active|shelved|cleared),
  worktree（null 或相对路径）, files[{src, rev, copy}],
  annotations ("notes.md"), sub_index（null 或相对 db 路径）。

ref.json 中存储的所有源路径都相对于项目根，使清单可提交且对动态路径友好
（源根在运行时经 lib.config 解析；§5）。

运行时：uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py <subcmd> ...

命令行（规范第 10 节）：
  ref.py create --project A|--root PATH --task "..." [--from-query SQL | --files a,b] [--worktree]
  ref.py list [--project A] [--all] [--status active]
  ref.py select <ref_id>
  ref.py strip-ifdef <ref_id> --keep MACRO[,MACRO...]
  ref.py index <ref_id>
  ref.py keep|shelve|clear <ref_id>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config  # noqa: E402

# 本插件 pass 的固定构建日期（规范全程使用 2026-05-28）。
REF_DATE = "2026-05-28"

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


#### 把自由格式任务串转成文件系统安全的 slug [@380kkm 2026-06-05] ####
def slugify(task: str) -> str:
    """把自由格式任务串转成文件系统安全的 slug。

    转小写，非字母数字折叠为单个连字符，并裁剪两端。空输入回退为 "ref"，
    使 id 始终格式良好。
    """
    s = task.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "ref"


#### 组装 ref id = <date>-<task-slug> [@380kkm 2026-06-05] ####
def make_ref_id(task: str, date: str = REF_DATE) -> str:
    """组装 ref id = <date>-<task-slug>。"""
    return f"{date}-{slugify(task)}"


#### 从 --store / --root（或由 cwd 发现，§5）解析项目配置 [@380kkm 2026-06-05] ####
def resolve_cfg(store: str | None, root: str | None) -> config.ProjectConfig:
    """从 --store / --root（或由 cwd 发现，§5）解析项目配置。"""
    return config.resolve_project(root=root, store=store)


#### 在解析出的 store 的 refs_dir 下定位 ref_id 的目录 [@380kkm 2026-06-05] ####
def find_ref(ref_id: str, store: str | None, root: str | None) -> tuple[config.ProjectConfig, Path]:
    """在解析出的 store 的 refs_dir 下定位 ref_id 的目录。"""
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
    """直接对项目 db 跑 SQL；返回 'path' 列的取值。

    查询必须产出一个名为 'path' 的列（大小写不敏感）。我们直接用 sqlite3 连接
    （不经 query.py 中转），依据规范 §10。
    """
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
    """尽力取文件的 git blob/commit sha；非 git 跟踪时返回 None。"""
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
    """把选定路径归一化为相对项目根的 posix 串。

    接受已相对于根的路径，或根下的绝对路径。当路径逃逸出根（无法可移植地存储）
    时返回 None。
    """
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


#### 子命令 create：选源、拷贝、可选 worktree、写清单 [@380kkm 2026-06-05] ####
def cmd_create(args: argparse.Namespace) -> int:
    cfg = resolve_cfg(args.store, args.root)
    root = Path(cfg.root).resolve()

    # 1. 收集选定的源路径（相对根的 posix）。
    raw_paths: list[str] = []
    if args.from_query:
        raw_paths = select_from_query(Path(cfg.db_path), args.from_query)
    elif args.files:
        raw_paths = parse_files_arg(args.files)
    else:
        raise SystemError("provide --from-query SQL or --files a,b")

    rel_paths: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        rel = _to_relative(root, raw)
        if rel is None:
            print(f"warning: skipping path outside project root: {raw}", file=sys.stderr)
            continue
        if rel not in seen:
            seen.add(rel)
            rel_paths.append(rel)

    if not rel_paths:
        raise SystemError("no files selected for the ref")

    # 2. 建立 refs/<id>/ 与 files/。
    ref_id = make_ref_id(args.task)
    ref_dir = Path(cfg.refs_dir) / ref_id
    files_dir = ref_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # 3. 把每个选定文件拷进 files/，对 basename 冲突去重。
    file_entries: list[dict] = []
    used_copy_names: set[str] = set()
    for rel in rel_paths:
        src_abs = root / rel
        if not src_abs.exists():
            print(f"warning: source missing, recording anyway: {rel}", file=sys.stderr)
        copy_name = _unique_copy_name(rel, used_copy_names)
        used_copy_names.add(copy_name)
        dst = files_dir / copy_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src_abs.exists():
            shutil.copy2(src_abs, dst)
            rev = _git_rev(root, rel) or f"mtime:{int(src_abs.stat().st_mtime)}"
        else:
            rev = "missing"
        file_entries.append({
            "src": rel,
            "rev": rev,
            "copy": (Path("files") / copy_name).as_posix(),
        })

    # 4. 可选 git worktree（仅当项目根是 git 仓库）。
    worktree_rel = None
    if args.worktree:
        worktree_rel = _add_worktree(root, ref_dir, ref_id)

    # 5. notes.md 脚手架 + ref.json 清单。
    notes = ref_dir / "notes.md"
    if not notes.exists():
        notes.write_text(
            f"# {args.task}\n\n"
            f"Ref `{ref_id}` — semantic behavior notes.\n\n"
            f"_Prune the copies under `files/` and describe behavior here._\n",
            encoding="utf-8",
        )

    manifest = {
        "id": ref_id,
        "task": args.task,
        "date": REF_DATE,
        "source_project": cfg.alias,
        "status": "active",
        "worktree": worktree_rel,
        "files": file_entries,
        "annotations": "notes.md",
        "sub_index": None,
    }
    save_manifest(ref_dir, manifest)

    print(f"created ref: {ref_id}")
    print(f"  dir      : {ref_dir}")
    print(f"  project  : {cfg.alias}")
    print(f"  files    : {len(file_entries)} copied into files/")
    for fe in file_entries:
        print(f"    {fe['src']}  ->  {fe['copy']}  (rev {fe['rev']})")
    print(f"  worktree : {worktree_rel if worktree_rel else '(none)'}")
    print(f"  manifest : {ref_dir / 'ref.json'}")
    print(f"  notes    : {notes}")
    return 0
#### /子命令 create ####


#### 在 files/ 下挑选副本文件名，消解 basename 冲突 [@380kkm 2026-06-05] ####
def _unique_copy_name(rel: str, used: set[str]) -> str:
    """在 files/ 下挑选一个副本文件名，消解 basename 冲突。"""
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
    """建一个 git worktree 托管隔离分支目录；返回相对路径。

    worktree 位于 refs/<id>/worktree，在新分支 manyread/<id> 上。返回相对项目根的
    路径；失败或根非 git 仓库时返回 None。
    """
    if not is_git_repo(root):
        print("warning: --worktree ignored: project root is not a git repo", file=sys.stderr)
        return None
    wt_abs = ref_dir / "worktree"
    branch = f"manyread/{ref_id}"
    # 删除陈旧目录，使 `git worktree add` 不会在非空路径上报错。
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
        # 分支可能已存在（重新创建）；去掉 -b 重试。
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
    """加载某 refs_dir 下的全部 ref 清单（跳过不可读的）。"""
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


#### 子命令 list：按状态过滤并逐行打印 ref 概览 [@380kkm 2026-06-05] ####
def cmd_list(args: argparse.Namespace) -> int:
    manifests: list[dict] = []

    cfg = resolve_cfg(args.store, args.root)
    manifests = _scan_refs(Path(cfg.refs_dir))

    status_filter = args.status
    if status_filter:
        manifests = [m for m in manifests if m.get("status") == status_filter]

    if not manifests:
        print("(no refs found)")
        return 0

    for m in manifests:
        nfiles = len(m.get("files", []))
        wt = m.get("worktree") or "-"
        print(
            f"{m.get('id'):<40} {m.get('status', '?'):<8} "
            f"proj={m.get('source_project', '?'):<14} files={nfiles:<3} "
            f"worktree={wt}  task={m.get('task', '')}"
        )
    return 0


#### 子命令 select：打印某 ref 的清单 + notes + 文件列表 [@380kkm 2026-06-05] ####
def cmd_select(args: argparse.Namespace) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)

    print("=== ref manifest ===")
    print(json.dumps(manifest, indent=2))

    notes_name = manifest.get("annotations") or "notes.md"
    notes_path = ref_dir / notes_name
    print("\n=== notes ===")
    if notes_path.exists():
        print(notes_path.read_text(encoding="utf-8").rstrip())
    else:
        print("(no notes)")

    print("\n=== files (src  ->  copy @ rev) ===")
    for fe in manifest.get("files", []):
        copy_abs = ref_dir / fe.get("copy", "")
        present = "ok" if copy_abs.exists() else "MISSING"
        print(f"  {fe.get('src')}  ->  {fe.get('copy')}  [{present}]  (rev {fe.get('rev')})")

    print(f"\nref dir       : {ref_dir}")
    print(f"source project: {manifest.get('source_project')}  (root: {Path(cfg.root).resolve()})")
    return 0


#### 子命令 strip-ifdef：从副本中丢弃不匹配的预处理 span [@380kkm 2026-06-05] ####
def cmd_strip_ifdef(args: argparse.Namespace) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)
    keep = {k.strip() for k in args.keep.split(",") if k.strip()}
    if not keep:
        raise SystemError("--keep requires at least one MACRO")

    db_path = Path(cfg.db_path)
    if not db_path.exists():
        raise SystemError(
            f"project db not found: {db_path} (run enrich_treesitter.py first so "
            f"'ifdef_branch' symbols exist)"
        )

    conn = sqlite3.connect(str(db_path))
    total_dropped = 0
    touched_files = 0
    try:
        for fe in manifest.get("files", []):
            src = fe.get("src")
            copy_rel = fe.get("copy")
            if not src or not copy_rel:
                continue
            copy_abs = ref_dir / copy_rel
            if not copy_abs.exists():
                continue
            spans = _ifdef_spans_for(conn, src)
            if not spans:
                continue
            dropped = _strip_spans_from_copy(copy_abs, spans, keep)
            if dropped:
                total_dropped += dropped
                touched_files += 1
    finally:
        conn.close()

    print(f"strip-ifdef on ref {args.ref_id}: kept {sorted(keep)}")
    print(f"  files modified : {touched_files}")
    print(f"  spans dropped  : {total_dropped}")
    print("  (review the copies under files/ and update notes.md)")
    return 0


#### 取某源路径（相对根）的 ifdef_branch 符号 span [@380kkm 2026-06-05] ####
def _ifdef_spans_for(conn: sqlite3.Connection, src_rel: str) -> list[dict]:
    """返回某源路径（相对根）的 ifdef_branch 符号 span。

    span 来自 L2 富化：kind 为 'ifdef_branch' 的符号，其 name 记录控制宏/条件，
    并带精确的行跨度。
    """
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
    """判断某预处理 span 的控制条件是否提及被保留的宏。

    'name' 是记录下的条件文本（如 "WIN32"、"defined(WIN64)"、"FOO && BAR"）。
    只要任一被保留的宏 token 出现其中，就保留该 span。
    """
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))
    return bool(tokens & keep)


#### 从副本删除不匹配的预处理 span（1 起含端点的行） [@380kkm 2026-06-05] ####
def _strip_spans_from_copy(copy_abs: Path, spans: list[dict], keep: set[str]) -> int:
    """从副本删除不匹配的预处理 span（1 起、含端点的行）。

    返回被删除的 span 数。被丢弃 span 覆盖的行被删去；重叠/嵌套的被丢弃 span
    会取并集，以免重复裁剪。
    """
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


#### 子命令 index：对副本构建一份阅读优化的子索引 [@380kkm 2026-06-05] ####
def cmd_index(args: argparse.Namespace) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)

    # ref 的阅读优化子索引覆盖 files/ 下的副本。
    index_root = ref_dir / "files"
    if not index_root.exists():
        raise SystemError(f"ref files dir missing: {index_root}")

    ib = str(SCRIPT_DIR / "index_build.py")
    en = str(SCRIPT_DIR / "enrich_treesitter.py")
    # 经 `uv run` 重新调用同级脚本，使各自装上自己的 PEP 723 依赖集
    # （项目级运行时约定，§4）。若 `uv` 不可用则回退到当前解释器。
    runner = ["uv", "run", "--python", "3.12"] if shutil.which("uv") else [sys.executable]

    print(f"indexing ref {args.ref_id} sub-index at {index_root}")
    rc = subprocess.run([*runner, ib, "--root", str(index_root)], check=False).returncode
    if rc != 0:
        print(f"error: index_build failed (rc={rc})", file=sys.stderr)
        return rc
    # 富化是尽力而为（需 tree-sitter 依赖）；不因它失败而让 ref 索引失败。
    rc2 = subprocess.run([*runner, en, "--root", str(index_root)], check=False).returncode
    if rc2 != 0:
        print(f"warning: enrich_treesitter failed (rc={rc2}); sub-index built without symbols",
              file=sys.stderr)

    sub_db = (index_root / "manyread" / "source.db")
    if sub_db.exists():
        manifest["sub_index"] = (Path("files") / "manyread" / "source.db").as_posix()
        save_manifest(ref_dir, manifest)
        print(f"sub_index recorded: {manifest['sub_index']}")
    return 0


#### 设置 ref 状态并写回清单 [@380kkm 2026-06-05] ####
def _set_status(args: argparse.Namespace, status: str) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)
    manifest["status"] = status
    save_manifest(ref_dir, manifest)
    print(f"{status} ref: {args.ref_id}  ({ref_dir})")
    return 0


#### 子命令 keep：把 ref 标为 active [@380kkm 2026-06-05] ####
def cmd_keep(args: argparse.Namespace) -> int:
    return _set_status(args, "active")


#### 子命令 shelve：把 ref 标为 shelved [@380kkm 2026-06-05] ####
def cmd_shelve(args: argparse.Namespace) -> int:
    return _set_status(args, "shelved")


#### 子命令 clear：把 ref 标为 cleared [@380kkm 2026-06-05] ####
def cmd_clear(args: argparse.Namespace) -> int:
    return _set_status(args, "cleared")


#### 添加 ref-id 子命令共用的项目定位参数 [@380kkm 2026-06-05] ####
def _add_locator(sp: argparse.ArgumentParser) -> None:
    """添加 ref-id 子命令共用的可选项目定位参数。"""
    sp.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
    sp.add_argument("--root", default=None, help="source tree root (default: store's parent)")


#### 构建 argparse 解析器并接好各子命令 [@380kkm 2026-06-05] ####
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ref.py", description="manyread L4 ref/prune CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create", help="create a dated, task-tagged ref workspace")
    sp.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
    sp.add_argument("--root", default=None, help="source tree root (default: store's parent)")
    sp.add_argument("--task", required=True, help="task description (drives the ref slug)")
    sp.add_argument("--from-query", dest="from_query", default=None,
                    help="SQL returning a 'path' column (run against the project db)")
    sp.add_argument("--files", default=None, help="comma-separated file paths (root-relative or absolute)")
    sp.add_argument("--worktree", action="store_true", help="host an isolated git worktree")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("list", help="list refs for a project or across all (--all)")
    sp.add_argument("--store", default=None)
    sp.add_argument("--root", default=None)
    sp.add_argument("--all", action="store_true", help="(reserved) currently scans the resolved store")
    sp.add_argument("--status", default=None, choices=["active", "shelved", "cleared"])
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("select", help="print a ref's manifest + notes + file list")
    sp.add_argument("ref_id")
    _add_locator(sp)
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("strip-ifdef", help="drop non-matching preproc spans from the copies")
    sp.add_argument("ref_id")
    sp.add_argument("--keep", required=True, help="MACRO[,MACRO...] to keep")
    _add_locator(sp)
    sp.set_defaults(func=cmd_strip_ifdef)

    sp = sub.add_parser("index", help="build a reading-optimized sub-index over the copies")
    sp.add_argument("ref_id")
    _add_locator(sp)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("keep", help="mark a ref active")
    sp.add_argument("ref_id")
    _add_locator(sp)
    sp.set_defaults(func=cmd_keep)

    sp = sub.add_parser("shelve", help="mark a ref shelved")
    sp.add_argument("ref_id")
    _add_locator(sp)
    sp.set_defaults(func=cmd_shelve)

    sp = sub.add_parser("clear", help="mark a ref cleared")
    sp.add_argument("ref_id")
    _add_locator(sp)
    sp.set_defaults(func=cmd_clear)

    return p


#### 入口：解析参数并分派到子命令处理函数 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
