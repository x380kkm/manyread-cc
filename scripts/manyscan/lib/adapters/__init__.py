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


class SymbolAdapter:
    """SYMBOL-LEVEL adapter: ``extends``/``implements``/``uses_type`` edges with a
    depth-1 dependency SINK.

    Each node is a single symbol (``s<id>``) or an external dependency target
    (``dep:<name>``). ``neighbors`` yields a symbol's boundary out-edges, resolved
    to concrete targets WITH a soundness confidence (carried on the yielded
    ``Step.edge`` as a private ``_confidence`` attribute so ``boundary.build`` can
    stash it on the Graph). A dependency-zone or ``dep:`` node is a SINK — its
    neighbours are never expanded — which is what keeps the slice to the target plus
    its one-layer dependency interface.

    ``boundary`` is imported lazily inside methods to avoid an import cycle
    (``adapters`` ← ``boundary`` ← ``adapters``).
    """

    name = "symbol"

    def __init__(self, zoning: "object"):
        self.z = zoning

    def seed_nodes(self, store: "stores.Store", seed: str, alias: str | None = None,
                   max_seeds: int = 25) -> list[Node]:
        from lib import boundary
        out: dict[str, Node] = {}
        for r in store.symbols_named(seed, limit=max_seeds):
            sid = int(r["id"])
            node = boundary.symbol_node(store, sid, self.z, alias)
            if node.attrs.get("zone") == boundary.TARGET:
                out.setdefault(node.id, node)
        return [out[k] for k in sorted(out, key=lambda nid: int(nid[1:]) if nid[1:].isdigit() else 0)]

    def neighbors(self, store: "stores.Store", node_id: str, *, direction: str = "out",
                  index: "deps.PathIndex | None" = None, alias: str | None = None
                  ) -> Iterator[Step]:
        from lib import boundary
        if not node_id.startswith("s") or not node_id[1:].isdigit():
            return  # dep:/dependency nodes are SINKS (depth-1)
        sid = int(node_id[1:])
        row = store.symbol(sid)
        if row is None:
            return
        if boundary.zone_of_path(row["path"], self.z) == boundary.DEPENDENCY:
            return  # dependency symbol is a SINK
        for er in boundary.out_edges(store, sid):
            r = boundary.resolve_target(store, er, self.z, alias)
            edge = Edge(src=node_id, dst=r.target_id, relation=er["relation"],
                        evidence=Evidence(boundary._NORM(row["path"]) if row["path"] else None,
                                          row["start_line"]))
            # Carry confidence on the edge so build() can record it on the Graph.
            edge._confidence = r.confidence  # type: ignore[attr-defined]
            yield Step(edge=edge, node=r.node)


DEFAULT_ADAPTER: SourceAdapter = CodeAdapter()
