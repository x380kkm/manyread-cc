# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.boundary.resolve
"""manyscan.lib.boundary.resolve —— 带可靠性置信度的边解析。

把每条符号边（``extends`` / ``implements`` / ``uses_type``）解析到一个具体目标，并附带
可靠性置信度（绝不在多个同名候选中悄悄挑一个），另外提供只读的存储库边访问伙伴
（:func:`out_edges`）及其查询关系字母表（:data:`REL`）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from lib import deps
from lib.graph import Node

from .nodes import ambiguous_internal_node, external_node, symbol_node
from .zoning import DEPENDENCY, TARGET, Zoning, zone_of_path

#### 构成边界的符号关系字母表 [@380kkm 2026-06-05] ####
# 已排序；不含 contains / calls / references
REL: tuple[str, ...] = ("extends", "implements", "uses_type")


#### 解析单条边的结果：目标节点 id + 可靠性置信度 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Resolved:
    # 's<id>' 或 'dep:<name>'
    target_id: str
    # 'direct' | 'unique' | 'ambiguous' | 'unresolved'
    confidence: str
    # 0、1 或 N
    ambiguity: int
    node: Node
#### /解析单条边的结果 ####


#### 把一条 edges 记录解析到具体目标并记录置信度 [@380kkm 2026-06-05] ####
def resolve_target(store, row, z: Zoning, alias: str | None = None) -> Resolved:
    """* 设置了 ``dst_symbol_id`` → 该符号，``direct``。
    * 否则按精确名在全局解析 ``dst_name``：
        - 0 个候选 → ``dep:<name>``，``unresolved``。
        - 恰好 1 个 → 该符号，``unique``。
        - N > 1   → ``dep:<name>`` 且 ``ambiguity=N``，``ambiguous``。
    """
    dst_sid = row["dst_symbol_id"]
    if dst_sid is not None:
        return Resolved(f"s{dst_sid}", "direct", 0, symbol_node(store, int(dst_sid), z, alias))
    name = row["dst_name"] or ""
    cands = sorted(deps.resolve_edge_targets(store, name),
                   key=lambda r: (r["path"], r["id"]))
    if not cands:
        return Resolved(f"dep:{name}", "unresolved", 0, external_node(name))
    if len(cands) == 1:
        sid = int(cands[0]["id"])
        return Resolved(f"s{sid}", "unique", 1, symbol_node(store, sid, z, alias))

    #### N>1：有歧义 —— 全为目标区则判为内部歧义，否则判为依赖 [@380kkm 2026-06-05] ####
    n = len(cands)
    if {zone_of_path(c["path"], z) for c in cands} == {TARGET}:
        return Resolved(f"amb:{name}", "ambiguous", n, ambiguous_internal_node(name, n))
    return Resolved(f"dep:{name}", "ambiguous", n, external_node(name, n))
    #### /N>1 歧义判定 ####


#### 取一个符号的全部边界出边，按总序排列以保证确定性 [@380kkm 2026-06-05] ####
def out_edges(store, symbol_id: int) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(REL))
    return store.conn.execute(
        "SELECT id, src_symbol_id, dst_symbol_id, dst_name, relation FROM edges "
        f"WHERE src_symbol_id = ? AND relation IN ({placeholders}) "
        "ORDER BY relation, dst_name, dst_symbol_id, id",
        (symbol_id, *REL),
    ).fetchall()
