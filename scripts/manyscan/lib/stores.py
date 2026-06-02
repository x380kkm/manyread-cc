# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.stores — read-only access to manyread stores.

manyscan is built ON TOP of manyread. It never re-declares manyread's schema or
store layout; instead it loads manyread's own ``lib/config.py`` and ``lib/db.py``
(by file path, under aliased module names) so that any change manyread makes to
its storage model propagates here automatically. Every access to a store's
``source.db`` is READ-ONLY (``file:...?mode=ro``).

Run standalone as a smoke test::

    uv run --python 3.12 scripts/lib/stores.py --list
    uv run --python 3.12 scripts/lib/stores.py --root W:/3dgs/references/DrivingForward
"""
from __future__ import annotations

import glob
import importlib.util
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


# --- locate + load manyread's own lib (the compat backbone) ------------------
def manyread_scripts_dir() -> Path:
    """Resolve manyread's plugin ``scripts/`` dir (holding ``lib/config.py``).

    Honors the ``MANYSCAN_MANYREAD`` override (plugin root or its ``scripts/``);
    otherwise picks the highest-versioned installed plugin via the documented
    cache glob ``~/.claude/plugins/cache/*/manyread/*/scripts``.
    """
    # In-plugin (merged into manyread): manyread's lib is a SIBLING at scripts/lib.
    # This file is scripts/manyscan/lib/stores.py, so parents[2] == scripts/.
    # This same-repo branch is the normal path; env + cache-glob below remain as
    # fallbacks for running manyscan from a standalone checkout.
    in_plugin = Path(__file__).resolve().parents[2]
    if not os.environ.get("MANYSCAN_MANYREAD") and (in_plugin / "lib" / "config.py").is_file():
        return in_plugin

    env = os.environ.get("MANYSCAN_MANYREAD")
    if env:
        p = Path(env)
        cand = p / "scripts" if (p / "scripts").is_dir() else p
        if (cand / "lib" / "config.py").is_file():
            return cand
        raise FileNotFoundError(f"MANYSCAN_MANYREAD={env} has no lib/config.py")
    pattern = str(Path.home() / ".claude" / "plugins" / "cache" / "*" / "manyread" / "*" / "scripts")
    for cand in reversed(sorted(glob.glob(pattern))):
        if (Path(cand) / "lib" / "config.py").is_file():
            return Path(cand)
    raise FileNotFoundError("could not locate manyread plugin scripts/ (set MANYSCAN_MANYREAD)")


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: dataclasses + `from __future__ import annotations`
    # resolve string annotations via sys.modules[cls.__module__], so the module
    # must be present in sys.modules while its class bodies execute.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_MR: dict[str, ModuleType] = {}


def manyread_lib() -> tuple[ModuleType, ModuleType]:
    """Return manyread's ``(config, db)`` modules, loaded once and cached.

    Loaded by FILE PATH under aliased names so manyscan's own ``lib`` package is
    never shadowed. ``config.py`` / ``db.py`` are stdlib-only and self-contained.
    """
    if "config" not in _MR:
        libdir = manyread_scripts_dir() / "lib"
        _MR["config"] = _load_module("manyread_config", libdir / "config.py")
        _MR["db"] = _load_module("manyread_db", libdir / "db.py")
    return _MR["config"], _MR["db"]


# --- store discovery ---------------------------------------------------------
@dataclass(frozen=True)
class StoreInfo:
    """A manyread store: its ``manyread/`` dir, db, alias, and source root."""

    store: Path
    db_path: Path
    alias: str
    root: Path


def _info_from_store_dir(store: Path, alias: str | None = None, root: Path | None = None) -> StoreInfo:
    store = Path(store)
    return StoreInfo(
        store=store,
        db_path=store / "source.db",
        alias=alias or store.parent.name,
        root=Path(root) if root else store.parent,
    )


def list_stores() -> list[StoreInfo]:
    """All stores registered in manyread's hub (``~/.manyread/stores.json``)."""
    mr_config, _ = manyread_lib()
    out = [
        _info_from_store_dir(Path(s), info.get("alias"), info.get("root"))
        for s, info in mr_config.list_stores().items()
    ]
    out.sort(key=lambda s: s.alias.lower())
    return out


def resolve(store: str | None = None, root: str | None = None) -> StoreInfo:
    """Resolve a single store from an explicit ``--store``/``--root``, or a hub alias.

    Delegates to manyread's ``resolve_project`` so discovery semantics stay
    identical; a bare alias (not an existing path) is matched against the hub.
    """
    if store:
        sp = Path(store)
        if not sp.exists():  # treat as a hub alias
            for si in list_stores():
                if si.alias == store:
                    return si
        elif sp.is_file() and sp.name == "source.db":  # direct db path
            return _info_from_store_dir(sp.parent)
        elif (sp / "source.db").is_file():  # store dir containing source.db
            return _info_from_store_dir(sp)
    mr_config, _ = manyread_lib()
    cfg = mr_config.resolve_project(root=root, store=store)
    return _info_from_store_dir(Path(cfg.store), cfg.alias, Path(cfg.root))


# --- read-only store handle --------------------------------------------------
class Store:
    """Read-only handle over a manyread ``source.db`` (files/symbols/edges/meta)."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise FileNotFoundError(f"no manyread index db at {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def from_info(cls, info: StoreInfo) -> "Store":
        return cls(info.db_path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- bounded queries (callers add LIMITs for engine-scale stores) --
    def meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def counts(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM files)   AS files, "
            "       (SELECT COUNT(*) FROM symbols) AS symbols, "
            "       (SELECT COUNT(*) FROM edges)   AS edges"
        ).fetchone()
        return {"files": cur["files"], "symbols": cur["symbols"], "edges": cur["edges"]}

    def relation_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT relation, COUNT(*) AS n FROM edges GROUP BY relation ORDER BY n DESC"
        ).fetchall()
        return {r["relation"]: r["n"] for r in rows}

    def lang_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT lang, COUNT(*) AS n FROM symbols GROUP BY lang ORDER BY n DESC"
        ).fetchall()
        return {r["lang"]: r["n"] for r in rows}

    def symbols_by_name(self, like: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name LIKE ? ORDER BY f.path LIMIT ?",
            (like, limit),
        ).fetchall()

    # -- file + symbol accessors (used by deps/scope) --
    def file(self, file_id: int) -> sqlite3.Row | None:
        """Return (id, path, ext, size, content) for ``file_id`` or None."""
        return self.conn.execute(
            "SELECT id, path, ext, size, content FROM files WHERE id = ?", (file_id,)
        ).fetchone()

    def iter_files(self, exts: set[str] | None = None) -> Iterator[sqlite3.Row]:
        """Yield (id, path, ext, size) rows, optionally filtered to ``exts`` (with dot)."""
        for row in self.conn.execute("SELECT id, path, ext, size FROM files ORDER BY path"):
            if exts is None or (row["ext"] or "").lower() in exts:
                yield row

    def symbol(self, symbol_id: int) -> sqlite3.Row | None:
        """Return a symbol joined to its file path, or None."""
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id = ?",
            (symbol_id,),
        ).fetchone()

    def symbols_named(self, name: str, kinds: set[str] | None = None,
                      limit: int = 500) -> list[sqlite3.Row]:
        """Exact-name symbol lookup across all files (for cross-file edge resolution)."""
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            return self.conn.execute(
                "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                f"WHERE s.name = ? AND s.kind IN ({placeholders}) ORDER BY f.path LIMIT ?",
                (name, *sorted(kinds), limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? ORDER BY f.path LIMIT ?",
            (name, limit),
        ).fetchall()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="stores.py", description="manyscan store access smoke test")
    ap.add_argument("--list", action="store_true", help="list all hub-registered stores")
    ap.add_argument("--store", default=None, help="store dir / alias")
    ap.add_argument("--root", default=None, help="source root to discover the store from")
    args = ap.parse_args(argv)

    try:
        print(f"# manyread scripts: {manyread_scripts_dir()}", file=sys.stderr)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list or (not args.store and not args.root):
        stores = list_stores()
        print(f"# {len(stores)} store(s) in hub")
        for si in stores:
            print(f"  {si.alias:<30} {si.db_path}")
        return 0

    si = resolve(store=args.store, root=args.root)
    with Store.from_info(si) as st:
        print(json.dumps({
            "alias": si.alias,
            "root": str(si.root),
            "db": str(si.db_path),
            "enriched_at": st.meta("enriched_at"),
            "counts": st.counts(),
            "langs": st.lang_summary(),
            "relations": st.relation_summary(),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
