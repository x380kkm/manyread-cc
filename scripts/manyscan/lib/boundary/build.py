# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.build — 深度 1 依赖汇（dependency-sink）构建流水线。

展开一个深度 1 的*依赖汇*切片：每个目标符号加上其单层依赖边界（带置信度解析），
无论依赖索引多大，都通过直接构图完成。
"""
from __future__ import annotations

import re
import sqlite3

from lib.graph import Budget, Edge, Evidence, Graph

from .nodes import symbol_node
from .resolve import out_edges, resolve_target
from .zoning import DEPENDENCY, TARGET, Zoning, zone_of_path

#### UE 导出宏（如 MATBP2FP_API）匹配模式：作为边界噪声跳过 [@380kkm 2026-06-05] ####
# 此类宏在声明上被解析为前导 type_identifier，但并非真实类型
_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*_API$")


#### 取所有目标区符号行，按 (path, id) 排序 [@380kkm 2026-06-05] ####
def _target_seed_rows(store, z: Zoning) -> list[sqlite3.Row]:
    rows = store.conn.execute(
        "SELECT s.id AS id, f.path AS path FROM symbols s "
        "JOIN files f ON f.id = s.file_id ORDER BY f.path, s.id"
    ).fetchall()
    return [r for r in rows if zone_of_path(r["path"], z) == TARGET]


#### 直接构造整个目标及其深度 1 依赖边界图 [@380kkm 2026-06-05] ####
def build(store, z: Zoning, budget: Budget, alias: str | None = None,
          dep_depth: int = 1) -> Graph:
    """通过直接构造，得到整个目标及其深度 1 的依赖边界。

    每个目标区符号都被纳入（目标是有限且完全需要的——不从种子做有界 BFS，
    否则预算可能在种子自身上耗尽）。对每个目标符号的边界边
    （``extends``/``implements``/``uses_type``）进行解析；目标被加入——
    依赖/``dep:`` 目标作为深度 1 汇点加入（其自身的边永不被跟随，因为只迭代目标符号）。
    ``budget.max_nodes`` 是带诚实截断的安全上限。逐边置信度记于 ``g.edge_confidence``；
    UE ``*_API`` 宏被跳过。

    ``dep_depth`` 控制在目标之外展开多少层有界出边。``dep_depth <= 1``（默认）
    是历史行为：只展开一层（依赖*表层*），且每个依赖节点都是汇点——build() 输出
    与之前逐字节一致。``dep_depth >= 2`` 在表层之后多跑一层有界展开；在该层中
    首次被加入的依赖 SYMBOL 节点被标记为 ``dep_core``（这种 id 跟踪是精确的：
    被某目标与另一依赖同时引用的依赖符号已在深度 1 加入，故深度 2 不会再加它，
    它保持为表层/``dep-iface`` 节点）。共享的 ``truncated``/``elided`` 计数器在两趟之间
    叠加，故深度 2 的溢出会被诚实报告，而非静默丢弃。
    """
    g = Graph()
    cap = budget.max_nodes
    confidence: dict[tuple[str, str, str], str] = {}
    truncated = False
    elided = 0

    target_ids: list[str] = []
    for r in _target_seed_rows(store, z):
        if len(g.nodes) >= cap:
            truncated = True
            elided += 1
            continue
        node = symbol_node(store, int(r["id"]), z, alias)
        g.add_node(node)
        target_ids.append(node.id)

    #### 从一组有序源 id 展开一层有界出边，返回新增依赖符号 id [@380kkm 2026-06-05] ####
    def _expand(src_ids: list[str]) -> list[str]:
        """从一个已排序的源 id 列表展开一层有界出边。

        解析每个源的边界边（与历史循环相同的 _MACRO_RE 跳过 + resolve_target +
        置信度），通过 ``id not in g.nodes`` 去重新节点，共享外层（nonlocal）的
        ``truncated``/``elided`` 上限计数，并仅返回新增的依赖 SYMBOL id 的有序集合
        （``s`` 前缀、zone == DEPENDENCY）——即下一层的确定性种子。
        """
        nonlocal truncated, elided
        new_dep_syms: set[str] = set()
        for nid in src_ids:
            sid = int(nid[1:])
            src_path = g.nodes[nid].attrs.get("path")
            for er in out_edges(store, sid):
                dn = er["dst_name"]
                if dn and _MACRO_RE.match(dn):
                    # 丢弃 UE 导出宏伪类型
                    continue
                res = resolve_target(store, er, z, alias)
                if res.node.id not in g.nodes:
                    if len(g.nodes) >= cap:
                        truncated = True
                        elided += 1
                        continue
                    g.add_node(res.node)
                    if (res.node.attrs.get("zone") == DEPENDENCY
                            and res.node.id.startswith("s")):
                        new_dep_syms.add(res.node.id)
                edge = Edge(nid, res.node.id, er["relation"], Evidence(src_path, None), 1)
                g.add_edge(edge)
                confidence[edge.key()] = res.confidence
        return sorted(new_dep_syms)
    #### /从有序源展开一层有界出边 ####

    # 深度 1（历史行为）
    surface_dep = _expand(target_ids)
    if dep_depth >= 2:
        # 表层之后多展开一层有界出边
        core_ids = _expand(surface_dep)
        for nid in core_ids:
            # 标记深度 2 首次加入的节点
            g.nodes[nid].attrs["dep_core"] = 1
            g.nodes[nid].attrs["dep_depth"] = 2

    g.edge_confidence = {e.key(): confidence.get(e.key(), "direct") for e in g.edges}
    if truncated:
        g.truncated = True
        g.elided = elided
    return g
#### /直接构造整个目标及其深度 1 依赖边界图 ####
