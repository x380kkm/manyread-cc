# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.scope
"""manyscan.lib.scope — 种子解析 + 有界、按需展开。

给定一个种子（符号 / 文件 / 目录 / 关键字），围绕它构建真实的依赖切片，由
:func:`graph.bfs_bounded` 层级完备地展开。推导依赖的方式存放在可插拔的
:class:`adapters.SourceAdapter`（默认 :class:`adapters.CodeAdapter`）里；本模块
只负责驱动有界展开。
"""
from __future__ import annotations

from lib import adapters, deps, graph, stores
from lib.graph import Budget, Graph, Node


#### 经适配器（默认 code）把种子字符串解析为起始节点 [@380kkm 2026-06-05] ####
def resolve_seed(store: "stores.Store", seed: str, alias: str | None = None,
                 max_seeds: int = 25, adapter: "adapters.SourceAdapter | None" = None) -> list[Node]:
    return (adapter or adapters.DEFAULT_ADAPTER).seed_nodes(store, seed, alias=alias, max_seeds=max_seeds)


#### 构造 bfs_bounded 所需的 expand(node_id) -> Iterable[Step] 回调 [@380kkm 2026-06-05] ####
def make_expand(store: "stores.Store", budget: Budget, alias: str | None = None,
                index: "deps.PathIndex | None" = None,
                adapter: "adapters.SourceAdapter | None" = None):
    adapter = adapter or adapters.DEFAULT_ADAPTER
    index = index or deps.PathIndex.for_store(store)

    #### 按预算方向返回某节点的邻居 [@380kkm 2026-06-05] ####
    def expand(node_id: str):
        return adapter.neighbors(store, node_id, direction=budget.direction,
                                 index=index, alias=alias)
    #### /按预算方向返回邻居 ####

    return expand


#### 围绕 seeds 做有界、层级完备的真实依赖切片展开 [@380kkm 2026-06-05] ####
def expand(store: "stores.Store", seeds: list[Node], budget: Budget | None = None,
           alias: str | None = None, adapter: "adapters.SourceAdapter | None" = None) -> Graph:
    budget = budget or Budget()
    seeds = list(seeds)
    if not seeds:
        return Graph()
    return graph.bfs_bounded(seeds, make_expand(store, budget, alias=alias, adapter=adapter), budget)


#### 解析 seed 再展开其有界依赖切片 [@380kkm 2026-06-05] ####
def scan(store: "stores.Store", seed: str, budget: Budget | None = None,
         alias: str | None = None, adapter: "adapters.SourceAdapter | None" = None) -> Graph:
    """解析不出则返回空图。"""
    budget = budget or Budget()
    nodes = resolve_seed(store, seed, alias=alias, adapter=adapter)
    return expand(store, nodes, budget, alias=alias, adapter=adapter)
