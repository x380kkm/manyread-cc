# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.boundary.nodes
"""manyscan.lib.boundary.nodes —— 图节点与限定名的构造。

为已索引的符号构造图 :class:`~lib.graph.Node`，为未解析/依赖的外部符号构造外部节点，
为目标内部但有歧义的符号构造歧义节点，并提供 ``Outer::Inner::name`` 的限定名遍历。
"""
from __future__ import annotations

from lib.graph import Evidence, Node

from .zoning import DEPENDENCY, TARGET, Zoning, _NORM, zone_of_path


#### 沿 parent_id 上溯拼出 Outer::Inner::name 限定名（带环路保护） [@380kkm 2026-06-05] ####
def qualified_name(store, symbol_id: int) -> str:
    cache = getattr(store, "_ms_qname_cache", None)
    if cache is None:
        cache = {}
        store._ms_qname_cache = cache
    if symbol_id in cache:
        return cache[symbol_id]
    parts: list[str] = []
    seen: set[int] = set()
    sid: int | None = symbol_id
    while sid is not None and sid not in seen:
        seen.add(sid)
        row = store.conn.execute(
            "SELECT name, parent_id FROM symbols WHERE id = ?", (sid,)
        ).fetchone()
        if row is None:
            break
        parts.append(row["name"] or str(sid))
        sid = row["parent_id"]
    qn = "::".join(reversed(parts)) if parts else str(symbol_id)
    cache[symbol_id] = qn
    return qn


#### 为已索引符号构造图节点（id 为 s<id>） [@380kkm 2026-06-05] ####
def symbol_node(store, symbol_id: int, z: Zoning, alias: str | None = None) -> Node:
    row = store.symbol(symbol_id)
    if row is None:
        # 防御：边指向已消失的符号，按外部节点处理
        return external_node(f"#{symbol_id}")
    path = row["path"]
    zone = zone_of_path(path, z)
    return Node(
        id=f"s{symbol_id}",
        kind=row["kind"] or "symbol",
        label=qualified_name(store, symbol_id),
        store=alias,
        evidence=Evidence(_NORM(path) if path else None, row["start_line"]),
        attrs={"path": _NORM(path) if path else "", "zone": zone, "cluster": zone},
    )


#### 为依赖/未解析符号构造外部节点（id 为 dep:<name>） [@380kkm 2026-06-05] ####
def external_node(name: str, ambiguity: int = 0) -> Node:
    attrs: dict = {"zone": DEPENDENCY, "cluster": DEPENDENCY, "unresolved": True}
    if ambiguity:
        attrs["ambiguity"] = ambiguity
    return Node(id=f"dep:{name}", kind="external", label=name, attrs=attrs)


#### 为目标内部但有歧义的符号构造歧义节点（id 为 amb:<name>） [@380kkm 2026-06-05] ####
def ambiguous_internal_node(name: str, ambiguity: int) -> Node:
    """目标区内已知属内部、但无法锁定到单一符号的类型（例如头文件定义 + 前向声明）。

    保留在目标区、不计入依赖边界，但标记为有歧义（绝不悄悄解析为某一个符号）。
    """
    return Node(id=f"amb:{name}", kind="ambiguous", label=name,
                attrs={"zone": TARGET, "cluster": TARGET, "ambiguity": ambiguity})
