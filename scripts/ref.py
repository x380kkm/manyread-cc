# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L4 — ref / prune / worktree CLI (stdlib only).

Spec section 10. A *ref* is a dated, task-tagged reading workspace holding
pruned + annotated copies of selected files. It lives under
  <root>/.manyread/refs/<id>/      where  id = <YYYY-MM-DD>-<task-slug>
and carries a manifest `ref.json`, free-form `notes.md`, and a `files/`
directory of copies.

ref.json fields (NORMATIVE, spec section 10):
  id, task, date, source_project, status (active|shelved|cleared),
  worktree (null or RELATIVE path), files[{src, rev, copy}],
  annotations ("notes.md"), sub_index (null or relative db path).

All source paths stored in ref.json are RELATIVE to the project root so the
manifest is committable and dynamic-path friendly (the source root is resolved
at runtime via lib.config; §5).

Runtime: uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py <subcmd> ...

CLI (spec section 10):
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

# Fixed build date for this plugin pass (spec uses 2026-05-28 throughout).
REF_DATE = "2026-05-28"

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


# --- slug / id helpers ------------------------------------------------------
def slugify(task: str) -> str:
    """Turn a free-form task string into a filesystem-safe slug.

    Lowercased, non-alphanumerics collapsed to single hyphens, trimmed. Empty
    input falls back to "ref" so an id is always well-formed.
    """
    s = task.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "ref"


def make_ref_id(task: str, date: str = REF_DATE) -> str:
    """Compose the ref id = <date>-<task-slug>."""
    return f"{date}-{slugify(task)}"


# --- project / ref resolution ----------------------------------------------
def resolve_cfg(store: str | None, root: str | None) -> config.ProjectConfig:
    """Resolve a project config from --store / --root (or discovery from cwd, §5)."""
    return config.resolve_project(root=root, store=store)


def find_ref(ref_id: str, store: str | None, root: str | None) -> tuple[config.ProjectConfig, Path]:
    """Locate the ref dir for ref_id under the resolved store's refs_dir."""
    cfg = resolve_cfg(store, root)
    ref_dir = Path(cfg.refs_dir) / ref_id
    if ref_dir.exists():
        return cfg, ref_dir
    raise SystemError(f"ref '{ref_id}' not found under {cfg.refs_dir}")


def load_manifest(ref_dir: Path) -> dict:
    mpath = ref_dir / "ref.json"
    if not mpath.exists():
        raise SystemError(f"manifest missing: {mpath}")
    return json.loads(mpath.read_text(encoding="utf-8"))


def save_manifest(ref_dir: Path, manifest: dict) -> None:
    mpath = ref_dir / "ref.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# --- file selection ---------------------------------------------------------
def select_from_query(db_path: Path, sql: str) -> list[str]:
    """Run SQL directly against the project db; return values of the 'path' col.

    The query must yield a column named 'path' (case-insensitive). We connect
    with sqlite3 directly (no query.py round-trip) per spec §10.
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


def parse_files_arg(files: str | None) -> list[str]:
    if not files:
        return []
    return [f for f in (x.strip() for x in files.split(",")) if f]


def _git_rev(root: Path, rel_path: str) -> str | None:
    """Best-effort git blob/commit sha for a file; None when not git-tracked."""
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


def _to_relative(root: Path, raw: str) -> str | None:
    """Normalize a selected path to a project-root-relative posix string.

    Accepts paths already relative to root, or absolute paths under root.
    Returns None when the path escapes the root (can't be stored portably).
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


# --- subcommand: create -----------------------------------------------------
def cmd_create(args: argparse.Namespace) -> int:
    cfg = resolve_cfg(args.store, args.root)
    root = Path(cfg.root).resolve()

    # 1. Gather selected source paths (root-relative posix).
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

    # 2. Make refs/<id>/ + files/.
    ref_id = make_ref_id(args.task)
    ref_dir = Path(cfg.refs_dir) / ref_id
    files_dir = ref_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # 3. Copy each selected file into files/, dedup basename collisions.
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

    # 4. Optional git worktree (only if project root is a git repo).
    worktree_rel = None
    if args.worktree:
        worktree_rel = _add_worktree(root, ref_dir, ref_id)

    # 5. notes.md scaffold + ref.json manifest.
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


def _unique_copy_name(rel: str, used: set[str]) -> str:
    """Pick a copy filename under files/, disambiguating basename collisions."""
    base = Path(rel).name
    if base not in used:
        return base
    stem = Path(base).stem
    suffix = Path(base).suffix
    # Prefix with a slugged parent dir to disambiguate.
    parent = slugify(str(Path(rel).parent))
    candidate = f"{parent}-{base}" if parent and parent != "ref" else base
    n = 1
    name = candidate
    while name in used:
        name = f"{stem}-{n}{suffix}"
        n += 1
    return name


def _add_worktree(root: Path, ref_dir: Path, ref_id: str) -> str | None:
    """Create a git worktree to host an isolated branch dir; return REL path.

    The worktree lives at refs/<id>/worktree on a new branch manyread/<id>.
    Returns the path relative to the project root, or None on failure / when
    the root is not a git repo.
    """
    if not is_git_repo(root):
        print("warning: --worktree ignored: project root is not a git repo", file=sys.stderr)
        return None
    wt_abs = ref_dir / "worktree"
    branch = f"manyread/{ref_id}"
    # Remove a stale dir so `git worktree add` does not error on a non-empty path.
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
        # Branch may already exist (re-create); retry without -b.
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


# --- subcommand: list -------------------------------------------------------
def _scan_refs(refs_dir: Path) -> list[dict]:
    """Load all ref manifests under a refs_dir (skipping unreadable ones)."""
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


# --- subcommand: select -----------------------------------------------------
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


# --- subcommand: strip-ifdef ------------------------------------------------
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


def _ifdef_spans_for(conn: sqlite3.Connection, src_rel: str) -> list[dict]:
    """Return ifdef_branch symbol spans for a source path (root-relative).

    Spans come from L2 enrichment: symbols of kind 'ifdef_branch' with a name
    that records the controlling macro/condition and precise line spans.
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


def _span_keeps(name: str, keep: set[str]) -> bool:
    """Decide whether a preproc span's controlling condition mentions a kept macro.

    The 'name' is the recorded condition text (e.g. "WIN32", "defined(WIN64)",
    "FOO && BAR"). We keep the span when ANY kept macro token appears in it.
    """
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))
    return bool(tokens & keep)


def _strip_spans_from_copy(copy_abs: Path, spans: list[dict], keep: set[str]) -> int:
    """Remove non-matching preproc spans (1-based inclusive lines) from a copy.

    Returns the number of spans removed. Lines covered by a dropped span are
    deleted; overlapping/nested dropped spans are unioned so we never double-cut.
    """
    text = copy_abs.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    n = len(lines)

    drop_line: list[bool] = [False] * (n + 2)  # 1-based, +slack
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


# --- subcommand: index ------------------------------------------------------
def cmd_index(args: argparse.Namespace) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)

    # The ref's reading-optimized sub-index covers the copies under files/.
    index_root = ref_dir / "files"
    if not index_root.exists():
        raise SystemError(f"ref files dir missing: {index_root}")

    ib = str(SCRIPT_DIR / "index_build.py")
    en = str(SCRIPT_DIR / "enrich_treesitter.py")
    # Re-invoke the sibling scripts through `uv run` so each gets its own PEP 723
    # dependency set installed (the project-wide runtime convention, §4). Falls
    # back to the current interpreter if `uv` is somehow unavailable.
    runner = ["uv", "run", "--python", "3.12"] if shutil.which("uv") else [sys.executable]

    print(f"indexing ref {args.ref_id} sub-index at {index_root}")
    rc = subprocess.run([*runner, ib, "--root", str(index_root)], check=False).returncode
    if rc != 0:
        print(f"error: index_build failed (rc={rc})", file=sys.stderr)
        return rc
    # Enrich is best-effort (needs tree-sitter deps); don't fail the ref index on it.
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


# --- subcommands: keep / shelve / clear -------------------------------------
def _set_status(args: argparse.Namespace, status: str) -> int:
    cfg, ref_dir = find_ref(args.ref_id, args.store, args.root)
    manifest = load_manifest(ref_dir)
    manifest["status"] = status
    save_manifest(ref_dir, manifest)
    print(f"{status} ref: {args.ref_id}  ({ref_dir})")
    return 0


def cmd_keep(args: argparse.Namespace) -> int:
    return _set_status(args, "active")


def cmd_shelve(args: argparse.Namespace) -> int:
    return _set_status(args, "shelved")


def cmd_clear(args: argparse.Namespace) -> int:
    return _set_status(args, "cleared")


# --- argparse wiring --------------------------------------------------------
def _add_locator(sp: argparse.ArgumentParser) -> None:
    """Add the optional project-locator args shared by ref-id subcommands."""
    sp.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
    sp.add_argument("--root", default=None, help="source tree root (default: store's parent)")


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
