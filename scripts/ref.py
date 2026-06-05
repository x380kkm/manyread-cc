# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# ref
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

本文件是 CLI 门面：命名+定位+清单+选源/裁剪的纯辅助层在 lib.ref_store，由本文件
在顶部再导出；本文件只保留各子命令处理与 argparse 装配。

运行时：uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py <subcmd> ...

命令行（规范第 10 节）：
  ref.py create --project A|--root PATH --task "..." [--from-query SQL | --files a,b] [--worktree]
  ref.py list [--status active]
  ref.py select <ref_id>
  ref.py strip-ifdef <ref_id> --keep MACRO[,MACRO...]
  ref.py index <ref_id>
  ref.py keep|shelve|clear <ref_id>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config  # noqa: E402
from lib import ref_store  # noqa: E402
from lib.ref_store import (  # noqa: E402,F401
    REF_DATE,
    _add_worktree,
    _git_rev,
    _ifdef_spans_for,
    _scan_refs,
    _span_keeps,
    _strip_spans_from_copy,
    _to_relative,
    _unique_copy_name,
    find_ref,
    is_git_repo,
    load_manifest,
    make_ref_id,
    parse_files_arg,
    resolve_cfg,
    save_manifest,
    select_from_query,
    slugify,
)

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


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


#### 子命令 list：按状态过滤并逐行打印 ref 概览 [@380kkm 2026-06-05] ####
def cmd_list(args: argparse.Namespace) -> int:
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
    # 经 uv run 重新调用同级脚本，无 uv 时回退到当前解释器
    runner = ["uv", "run", "--python", "3.12"] if shutil.which("uv") else [sys.executable]

    print(f"indexing ref {args.ref_id} sub-index at {index_root}")
    rc = subprocess.run([*runner, ib, "--root", str(index_root)], check=False).returncode
    if rc != 0:
        print(f"error: index_build failed (rc={rc})", file=sys.stderr)
        return rc
    # 富化失败不让 ref 索引失败
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

    sp = sub.add_parser("list", help="list refs in the resolved store")
    sp.add_argument("--store", default=None)
    sp.add_argument("--root", default=None)
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
