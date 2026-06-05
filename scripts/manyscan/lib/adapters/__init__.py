# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.adapters
"""manyscan.lib.adapters —— 可插拔的依赖来源适配器。

一个 :class:`SourceAdapter` 把 manyread 存储库转换成 manyscan 的图原语：
``seed_nodes``（把种子 resolve 成起始节点）与 ``neighbors``（产出进出某节点的
真实依赖 :class:`Step`）。``scope`` 由适配器驱动。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from lib import deps, stores
from lib.graph import Edge, Evidence, Node, Step


#### 在 manyread 存储库上产出 manyscan 图原语的来源协议 [@380kkm 2026-06-05] ####
@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def seed_nodes(self, store: "stores.Store", seed: str, alias: str | None = None,
                   max_seeds: int = 25) -> list[Node]:
        ...

    def neighbors(self, store: "stores.Store", node_id: str, *, direction: str = "out",
                  index: "deps.PathIndex | None" = None, alias: str | None = None
                  ) -> Iterator[Step]:
        ...
#### /来源协议 ####


#### 由 file_id/path 构造 file 节点 [@380kkm 2026-06-05] ####
def _file_node(file_id: int, path: str, alias: str | None = None) -> Node:
    return Node(id=f"file:{file_id}", kind="file", label=path, store=alias,
                evidence=Evidence(path=path))


#### 由路径派生反向查找用的若干 import 键 [@380kkm 2026-06-05] ####
def _import_keys(path: str) -> list[str]:
    p = PurePosixPath(path.replace("\\", "/"))
    stem = p.with_suffix("").as_posix()
    return list(dict.fromkeys([stem.replace("/", "."), p.name, stem]))


#### v1 适配器：由代码 imports/includes 派生 file→file 依赖边 [@380kkm 2026-06-05] ####
class CodeAdapter:
    name = "code"

    #### 把种子 resolve 成起始 file 节点（精确路径 -> 符号名 -> 模糊名 -> 全文检索） [@380kkm 2026-06-05] ####
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
        # 种子已含 LIKE 通配符时跳过模糊匹配
        if not any(c in seed for c in "%_"):
            for r in store.symbols_by_name(f"%{seed}%", limit=max_seeds):
                out.setdefault(f"file:{r['file_id']}", _file_node(r["file_id"], r["path"], alias))
            if out:
                return list(out.values())
        try:
            # 为 FTS 短语转义引号
            fts_term = '"' + seed.replace('"', '""') + '"'
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
    #### /把种子 resolve 成起始 file 节点 ####

    #### 产出某 file 节点进出的依赖步（out=imports，in=被谁 import） [@380kkm 2026-06-05] ####
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
    #### /产出某 file 节点进出的依赖步 ####

    #### 反向查找：产出 import 了本文件的诸文件作为入边 [@380kkm 2026-06-05] ####
    def _reverse(self, store: "stores.Store", file_id: int, path: str,
                 index: "deps.PathIndex", alias: str | None, limit: int) -> Iterator[Step]:
        seen: set[int] = set()
        for key in _import_keys(path):
            # importer 总数达上限即停
            if len(seen) >= limit:
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
    #### /反向查找 ####
#### /v1 适配器 CodeAdapter ####


#### 符号级适配器：extends/implements/uses_type 边 + 深度 1 依赖汇点 [@380kkm 2026-06-05] ####
class SymbolAdapter:
    """每个节点是单个符号（``s<id>``）或一个外部依赖目标（``dep:<name>``）。

    ``neighbors`` 产出某符号的边界出边，置信度作为私有 ``_confidence`` 属性挂在产出的
    ``Step.edge`` 上。依赖区或 ``dep:`` 节点是汇点，其邻居不展开。
    """

    name = "symbol"

    def __init__(self, zoning: "object"):
        self.z = zoning

    #### 把种子 resolve 成处于 TARGET 区的符号节点 [@380kkm 2026-06-05] ####
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
    #### /把种子 resolve 成 TARGET 区符号节点 ####

    #### 产出某符号的边界出边，附带 resolve 置信度 [@380kkm 2026-06-05] ####
    def neighbors(self, store: "stores.Store", node_id: str, *, direction: str = "out",
                  index: "deps.PathIndex | None" = None, alias: str | None = None
                  ) -> Iterator[Step]:
        from lib import boundary
        # dep:/依赖节点是汇点（深度 1）
        if not node_id.startswith("s") or not node_id[1:].isdigit():
            return
        sid = int(node_id[1:])
        row = store.symbol(sid)
        if row is None:
            return
        # 依赖符号是汇点
        if boundary.zone_of_path(row["path"], self.z) == boundary.DEPENDENCY:
            return
        for er in boundary.out_edges(store, sid):
            r = boundary.resolve_target(store, er, self.z, alias)
            edge = Edge(src=node_id, dst=r.target_id, relation=er["relation"],
                        evidence=Evidence(boundary._NORM(row["path"]) if row["path"] else None,
                                          row["start_line"]))
            # 把置信度挂在边上
            edge._confidence = r.confidence  # type: ignore[attr-defined]
            yield Step(edge=edge, node=r.node)
    #### /产出某符号的边界出边 ####
#### /符号级适配器 SymbolAdapter ####


# 默认适配器：代码 imports/includes
DEFAULT_ADAPTER: SourceAdapter = CodeAdapter()

# 扩展贡献的附加来源适配器（ADD，绝不替换 DEFAULT_ADAPTER）
ADAPTERS: list[SourceAdapter] = []


#### 把一个来源适配器加入扩展适配器注册表（去重） [@380kkm 2026-06-05] ####
def register_adapter(adapter: SourceAdapter) -> None:
    if adapter not in ADAPTERS:
        ADAPTERS.append(adapter)
