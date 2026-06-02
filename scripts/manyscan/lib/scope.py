# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.scope — seed resolution + bounded, demand-driven expansion.

This is manyscan's core promise: given a *seed* (symbol / file / dir / keyword)
build the REAL dependency slice around it, expanded by the level-complete
:func:`graph.bfs_bounded` so one question never drags in the whole engine. The HOW
of deriving deps lives in a pluggable :class:`adapters.SourceAdapter` (default
:class:`adapters.CodeAdapter`); this module only drives the bounded expansion.
"""
from __future__ import annotations

from lib import adapters, deps, graph, stores
from lib.graph import Budget, Graph, Node


def resolve_seed(store: "stores.Store", seed: str, alias: str | None = None,
                 max_seeds: int = 25, adapter: "adapters.SourceAdapter | None" = None) -> list[Node]:
    """Resolve a seed string to starting Nodes via the adapter (default: code)."""
    return (adapter or adapters.DEFAULT_ADAPTER).seed_nodes(store, seed, alias=alias, max_seeds=max_seeds)


def make_expand(store: "stores.Store", budget: Budget, alias: str | None = None,
                index: "deps.PathIndex | None" = None,
                adapter: "adapters.SourceAdapter | None" = None):
    """Build the ``expand(node_id) -> Iterable[Step]`` callback for ``bfs_bounded``."""
    adapter = adapter or adapters.DEFAULT_ADAPTER
    index = index or deps.PathIndex.for_store(store)

    def expand(node_id: str):
        return adapter.neighbors(store, node_id, direction=budget.direction,
                                 index=index, alias=alias)

    return expand


def expand(store: "stores.Store", seeds: list[Node], budget: Budget | None = None,
           alias: str | None = None, adapter: "adapters.SourceAdapter | None" = None) -> Graph:
    """Bounded, level-complete expansion of the real dependency slice around `seeds`."""
    budget = budget or Budget()
    seeds = list(seeds)
    if not seeds:
        return Graph()
    return graph.bfs_bounded(seeds, make_expand(store, budget, alias=alias, adapter=adapter), budget)


def scan(store: "stores.Store", seed: str, budget: Budget | None = None,
         alias: str | None = None, adapter: "adapters.SourceAdapter | None" = None) -> Graph:
    """Resolve `seed` then expand its bounded dependency slice. Empty graph if unresolved."""
    budget = budget or Budget()
    nodes = resolve_seed(store, seed, alias=alias, adapter=adapter)
    return expand(store, nodes, budget, alias=alias, adapter=adapter)
