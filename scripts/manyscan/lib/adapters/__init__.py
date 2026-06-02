# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.adapters — pluggable dependency-source adapters.

A :class:`SourceAdapter` turns a manyread store into manyscan graph primitives:
``seed_nodes`` (resolve a seed to starting nodes) and ``neighbors`` (yield the
real dependency :class:`Step`\\s out of / into a node). ``scope`` is adapter-driven,
so the bounded-expansion engine never needs to know HOW deps are derived.

v1 ships :class:`CodeAdapter` (imports/includes + edge resolution over indexed
code). When manyread later exposes UE blueprints/materials or Unity metadata as
new rows, an ``AssetAdapter`` / ``MetaAdapter`` implements the same Protocol and
slots straight in — this is the forward-compatibility seam.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from lib import deps, stores
from lib.graph import Edge, Evidence, Node, Step


@runtime_checkable
class SourceAdapter(Protocol):
    """A source of manyscan graph primitives over a manyread store."""

    name: str

    def seed_nodes(self, store: "stores.Store", seed: str, alias: str | None = None,
                   max_seeds: int = 25) -> list[Node]:
        ...

    def neighbors(self, store: "stores.Store", node_id: str, *, direction: str = "out",
                  index: "deps.PathIndex | None" = None, alias: str | None = None
                  ) -> Iterator[Step]:
        ...


def _file_node(file_id: int, path: str, alias: str | None = None) -> Node:
    return Node(id=f"file:{file_id}", kind="file", label=path, store=alias,
                evidence=Evidence(path=path))


def _import_keys(path: str) -> list[str]:
    p = PurePosixPath(path.replace("\\", "/"))
    stem = p.with_suffix("").as_posix()
    return list(dict.fromkeys([stem.replace("/", "."), p.name, stem]))


class CodeAdapter:
    """v1 adapter: file→file dependency edges from code imports/includes."""

    name = "code"

    def seed_nodes(self, store: "stores.Store", seed: str, alias: str | None = None,
                   max_seeds: int = 25) -> list[Node]:
        seed = seed.strip()
        out: dict[str, Node] = {}
        norm = "replace(path, char(92), '/')"
        s = seed.replace("\\", "/").lstrip("./")
        rows = store.conn.execute(
            f"SELECT id, path FROM files WHERE {norm} = ? OR {norm} LIKE ? "
            "ORDER BY length(path) LIMIT ?",
            (s, "%/" + s, max_seeds),
        ).fetchall()
        if rows:
            for r in rows:
                out.setdefault(f"file:{r['id']}", _file_node(r["id"], r["path"], alias))
            return list(out.values())
        for r in store.symbols_named(seed, limit=max_seeds):
            out.setdefault(f"file:{r['file_id']}", _file_node(r["file_id"], r["path"], alias))
        if out:
            return list(out.values())
        if not any(c in seed for c in "%_"):  # skip fuzzy if seed has LIKE wildcards
            for r in store.symbols_by_name(f"%{seed}%", limit=max_seeds):
                out.setdefault(f"file:{r['file_id']}", _file_node(r["file_id"], r["path"], alias))
            if out:
                return list(out.values())
        try:
            fts_term = '"' + seed.replace('"', '""') + '"'  # escape quotes for FTS phrase
            rows = store.conn.execute(
                "SELECT f.id, f.path FROM files_fts JOIN files f ON f.rowid = files_fts.rowid "
                "WHERE files_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_term, max_seeds),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            out.setdefault(f"file:{r['id']}", _file_node(r["id"], r["path"], alias))
        return list(out.values())

    def neighbors(self, store: "stores.Store", node_id: str, *, direction: str = "out",
                  index: "deps.PathIndex | None" = None, alias: str | None = None,
                  reverse_limit: int = 40) -> Iterator[Step]:
        if not node_id.startswith("file:"):
            return
        file_id = int(node_id.split(":", 1)[1])
        row = store.file(file_id)
        if row is None:
            return
        index = index or deps.PathIndex.for_store(store)
        if direction in ("out", "both"):
            for ref in deps.file_imports(store, file_id):
                tgt = deps.resolve_import(store, ref, from_path=row["path"], index=index)
                if tgt is None or tgt == file_id:
                    continue
                yield Step(
                    edge=Edge(src=node_id, dst=f"file:{tgt}", relation="imports",
                              evidence=Evidence(row["path"], ref.line)),
                    node=_file_node(tgt, index.path_of.get(tgt, str(tgt)), alias),
                )
        if direction in ("in", "both"):
            yield from self._reverse(store, file_id, row["path"], index, alias, reverse_limit)

    def _reverse(self, store: "stores.Store", file_id: int, path: str,
                 index: "deps.PathIndex", alias: str | None, limit: int) -> Iterator[Step]:
        seen: set[int] = set()
        for key in _import_keys(path):
            if len(seen) >= limit:  # total importers per file is capped, not per-key
                break
            try:
                rows = store.conn.execute(
                    "SELECT f.id, f.path FROM files_fts JOIN files f ON f.rowid = files_fts.rowid "
                    "WHERE files_fts MATCH ? LIMIT ?",
                    (f'"{key.replace(chr(34), chr(34) * 2)}"', limit),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                if len(seen) >= limit:
                    break
                if r["id"] == file_id or r["id"] in seen:
                    continue
                for ref in deps.file_imports(store, r["id"]):
                    if deps.resolve_import(store, ref, from_path=r["path"], index=index) == file_id:
                        seen.add(r["id"])
                        yield Step(
                            edge=Edge(src=f"file:{r['id']}", dst=f"file:{file_id}",
                                      relation="imports", evidence=Evidence(r["path"], ref.line)),
                            node=_file_node(r["id"], r["path"], alias),
                        )
                        break


DEFAULT_ADAPTER: SourceAdapter = CodeAdapter()
