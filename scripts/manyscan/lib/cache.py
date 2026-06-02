# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.cache — incremental cache of scoped slices, keyed on index freshness.

A slice is cached under ``<store>/manyscan/cache/<key>.json`` where ``key`` hashes
the index fingerprint (manyread ``meta.enriched_at``, else db mtime) + seed +
budget. When manyread re-indexes, ``enriched_at`` changes → the key changes →
stale entries simply miss (no explicit invalidation needed). The manyread store
itself is never written (read-only); only the sibling cache dir is.
"""
from __future__ import annotations

import hashlib
import json

from lib import stores
from lib.graph import Budget


def _fingerprint(store: "stores.Store") -> str:
    val = store.meta("enriched_at")
    if val:
        return val
    try:
        return str(int(store.db_path.stat().st_mtime))
    except OSError:
        return "0"


def cache_key(store: "stores.Store", seed: str, budget: Budget) -> str:
    """Stable 16-hex key over (index fingerprint, seed, budget)."""
    payload = {
        "fp": _fingerprint(store),
        "seed": seed,
        "max_nodes": budget.max_nodes,
        "max_depth": budget.max_depth,
        "direction": budget.direction,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_dir(store: "stores.Store"):
    return store.db_path.parent / "manyscan" / "cache"


def get(store: "stores.Store", key: str) -> dict | None:
    path = _cache_dir(store) / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def put(store: "stores.Store", key: str, data: dict) -> None:
    d = _cache_dir(store)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def cached_scan(store: "stores.Store", seed: str, budget: Budget | None = None,
                alias: str | None = None, use_cache: bool = True) -> tuple[dict, bool]:
    """Return ``(graph_dict, hit)`` — the cached slice if fresh, else compute + store it."""
    from lib import render, scope  # local import keeps the module graph acyclic

    budget = budget or Budget()
    key = cache_key(store, seed, budget)
    if use_cache:
        hit = get(store, key)
        if hit is not None:
            return hit, True
    data = render.graph_to_dict(scope.scan(store, seed, budget, alias=alias))
    if use_cache:
        put(store, key, data)
    return data, False
