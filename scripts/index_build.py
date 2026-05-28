# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L1 — Phase-1 FTS5 trigram indexer (stdlib only).

Builds <root>/.manyread/source.db with a full DROP+CREATE rebuild:
  * enumerate files (git ls-files when the root is a git repo, else os.walk
    filtered by config ignore_globs + a built-in SKIP_DIRS set),
  * load each text file and insert into `files` + `files_fts`,
  * create the empty `symbols`/`edges`/`meta` tables (via db.init_schema) so L2
    can fill them later,
  * write meta(build_id=<unix ts>, built_at, langs, exts).

Config-driven: extensions and ignore globs come from the per-project config
(<root>/.manyread/config.json) resolved through lib.config. When the config
provides no extensions (e.g. a bare --root with no config), fall back to a
sensible built-in default so the index is still useful.

CLI:  index_build.py <alias|--root PATH> [--rebuild]
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db

# Directories never descended into during the os.walk fallback.
SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".manyread",
    "manyread",
}

# Fallback extensions when the project config supplies none (bare --root case).
# Union of all built-in language presets plus common doc extensions.
DEFAULT_EXTS: list[str] = sorted(
    {ext for exts in config.LANG_EXTS.values() for ext in exts} | {".md"}
)

# Cap individual file content so a stray huge/binary file can't blow up the db.
MAX_FILE_BYTES = 4 * 1024 * 1024  # 4 MiB


def is_git_repo(root: Path) -> bool:
    """Return True if `root` is inside a git work tree (and git is available)."""
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


def enumerate_git(root: Path) -> list[Path]:
    """Enumerate tracked + untracked-but-not-ignored files via `git ls-files`.

    Uses --cached --others --exclude-standard so newly added (yet unignored)
    files are seen, while .gitignore'd files are skipped.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for line in out.stdout.splitlines():
        rel = line.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        paths.append(root / rel)
    return paths


def _matches_ignore(rel_posix: str, ignore_globs: list[str]) -> bool:
    """True if a root-relative posix path matches any ignore glob."""
    for pat in ignore_globs:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, pat.rstrip("/*") + "/*"):
            return True
        # Also match when the pattern targets a path segment anywhere.
        if "/" not in pat and any(fnmatch.fnmatch(seg, pat) for seg in rel_posix.split("/")):
            return True
    return False


def enumerate_walk(root: Path, ignore_globs: list[str]) -> list[Path]:
    """Enumerate files via os.walk, pruning SKIP_DIRS and ignore_globs."""
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in place so os.walk does not descend into them.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if _matches_ignore(rel, ignore_globs):
                continue
            paths.append(full)
    return paths


def read_text(path: Path) -> str | None:
    """Read a file as UTF-8 (replacing errors). Skip oversized/unreadable files."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def build(cfg: config.ProjectConfig, rebuild: bool) -> dict:
    """Run the full DROP+CREATE rebuild. Returns a stats dict for reporting."""
    root = Path(cfg.root).resolve()
    db_path = Path(cfg.db_path)

    # Extensions are config-driven; fall back to a built-in default if empty.
    exts = [e.lower() for e in cfg.exts] if cfg.exts else list(DEFAULT_EXTS)
    ext_set = set(exts)

    # 1. Enumerate.
    git = is_git_repo(root)
    if git:
        method = "git ls-files"
        candidates = enumerate_git(root)
    else:
        method = "os.walk"
        candidates = enumerate_walk(root, cfg.ignore_globs)

    # Filter by configured extensions.
    selected = [p for p in candidates if p.suffix.lower() in ext_set]
    enumerated = len(selected)

    # 2. Full DROP+CREATE rebuild: start from a clean db file.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = db.connect(db_path)
    try:
        # init_schema creates files/files_fts AND the empty symbols/edges/meta
        # tables so L2 can fill them later.
        db.init_schema(conn)

        # 3. Insert files + files_fts.
        indexed = 0
        for full in selected:
            content = read_text(full)
            if content is None:
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            ext = full.suffix.lower()
            cur = conn.execute(
                "INSERT OR IGNORE INTO files(path, ext, size, mtime, content) "
                "VALUES(?, ?, ?, ?, ?)",
                (rel, ext, st.st_size, int(st.st_mtime), content),
            )
            if cur.rowcount:
                conn.execute(
                    "INSERT INTO files_fts(rowid, path, content) VALUES(?, ?, ?)",
                    (cur.lastrowid, rel, content),
                )
                indexed += 1
        conn.commit()

        # 4. Meta.
        build_id = int(time.time())
        db.set_meta(conn, "build_id", build_id)
        db.set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(build_id)))
        db.set_meta(conn, "langs", ",".join(cfg.languages))
        db.set_meta(conn, "exts", ",".join(exts))
        conn.commit()
    finally:
        conn.close()

    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {
        "method": method,
        "exts": exts,
        "enumerated": enumerated,
        "indexed": indexed,
        "db_path": db_path,
        "db_size": db_size,
        "build_id": build_id,
    }


def _copy_store_data(src_store: Path, dst_store: Path) -> list[str]:
    """Reuse-by-copy: copy an existing store's index + refs + traces into dst.

    Literal copies (never links) so deleting the source can't affect the copy —
    e.g. multiple game projects sharing one already-indexed engine.
    """
    copied: list[str] = []
    src_db = src_store / "source.db"
    if src_db.exists():
        shutil.copy2(src_db, dst_store / "source.db")
        copied.append("source.db")
    for sub in ("refs", "traces"):
        s = src_store / sub
        if s.exists():
            shutil.copytree(s, dst_store / sub, dirs_exist_ok=True)
            copied.append(sub + "/")
    return copied


def _resolve_src_store(spec: str) -> Path | None:
    """Resolve a --copy-from spec to a store dir (a store, or a repo containing one)."""
    found = config.find_store(Path(spec))
    if found is not None:
        return Path(found).resolve()
    cand = Path(spec)
    if (cand / "manyread.json").exists():
        return cand.resolve()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="index_build.py",
        description="manyread L1 FTS5 trigram indexer (full DROP+CREATE rebuild).",
    )
    parser.add_argument("--root", help="source tree root (default: the store's parent / cwd)",
                        default=None)
    parser.add_argument("--store", help="explicit manyread store dir (default: discover from cwd)",
                        default=None)
    parser.add_argument("--init", action="store_true",
                        help="create a manyread store (default ./manyread) before building")
    parser.add_argument("--store-at", dest="store_at", default=None,
                        help="with --init: dir to create the store in (default: --root or cwd)")
    parser.add_argument("--langs", default=None,
                        help="with --init: comma-separated languages for the new store config")
    parser.add_argument("--exts", default=None,
                        help="with --init: comma-separated extensions (default: from --langs)")
    parser.add_argument("--copy-from", dest="copy_from", default=None,
                        help="reuse-by-copy: copy an existing store's index+refs+traces into "
                             "this one (e.g. a shared engine), instead of re-indexing")
    parser.add_argument("--list-stores", action="store_true", dest="list_stores",
                        help="print the per-user hub registry of activated stores and exit")
    parser.add_argument("--forget", default=None,
                        help="remove a store from the hub registry (does NOT delete it) and exit")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="force a full rebuild (default behaviour is always a full rebuild)",
    )
    args = parser.parse_args(argv)

    # Hub browse / manage — no store resolution needed.
    if args.list_stores:
        reg = config.list_stores()
        if not reg:
            print("(no stores registered in the hub)")
        for path, info in sorted(reg.items()):
            print(f"{path}  alias={info.get('alias', '')}  root={info.get('root', '')}")
        return 0
    if args.forget:
        ok = config.unregister_store(Path(args.forget))
        print(f"{'forgot' if ok else 'not in registry'}: {Path(args.forget).resolve()}")
        return 0

    store_arg = args.store
    if args.init:
        location = Path(args.store_at) if args.store_at else (Path(args.root) if args.root else Path.cwd())
        if config.is_system_location(location):
            print(f"warning: {Path(location).resolve()} looks like a system folder (drive root / "
                  "home / Desktop). Prefer a dedicated project subfolder for the store.",
                  file=sys.stderr)
        langs = [s.strip().lower() for s in args.langs.split(",")] if args.langs else []
        exts = None
        if args.exts:
            exts = [(e if e.startswith(".") else "." + e) for e in
                    (s.strip() for s in args.exts.split(",")) if e.strip()]
        store = config.init_store(location, languages=langs, exts=exts)
        print(f"initialized store: {store}")
        store_arg = str(store)

    try:
        cfg = config.resolve_project(root=args.root, store=store_arg)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Reuse-by-copy: pull an existing store's index/refs/traces in, then stop.
    if args.copy_from:
        src = _resolve_src_store(args.copy_from)
        if src is None:
            print(f"error: no manyread store found at/under {args.copy_from}", file=sys.stderr)
            return 2
        if src == Path(cfg.store).resolve():
            print("error: --copy-from points at this same store", file=sys.stderr)
            return 2
        copied = _copy_store_data(src, Path(cfg.store))
        config.register_store(cfg.store, cfg.alias, cfg.root)
        print(f"reused (copied) from {src}: {', '.join(copied) if copied else '(nothing to copy)'}")
        print(f"store     : {cfg.store}  (not rebuilt — pass --rebuild to re-index from source)")
        return 0

    stats = build(cfg, rebuild=args.rebuild)
    config.register_store(cfg.store, cfg.alias, cfg.root)

    print(f"project   : {cfg.alias}")
    print(f"root      : {Path(cfg.root).resolve()}")
    print(f"method    : {stats['method']}")
    print(f"exts      : {' '.join(stats['exts'])}")
    print(f"enumerated: {stats['enumerated']}")
    print(f"indexed   : {stats['indexed']}")
    print(f"db        : {stats['db_path']}")
    print(f"db size   : {stats['db_size']} bytes")
    print(f"build_id  : {stats['build_id']}")
    print(f"hub       : registered in {config.hub_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
