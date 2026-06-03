# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.rollup — fold a file-level slice into dir / module levels.

Wraps :func:`graph.rollup` with manyscan grouping (directory, or *module* = the
nearest ancestor dir carrying a build marker like ``CMakeLists.txt`` /
``*.Build.cs`` / ``pyproject.toml`` / ``*.uplugin``), and — crucially — carries the
bounded-expansion accounting (``truncated`` / ``frontier`` / ``elided`` /
``depth_bounded``) up to the rolled graph, attributing each elided-frontier count
to the GROUP it belongs to. So "return at level X" never silently loses the
over-budget tail.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

from lib import graph, stores
from lib.graph import Graph, Node

_MARKER_NAMES = {
    "cmakelists.txt", "pyproject.toml", "package.json", "cargo.toml",
    "go.mod", "setup.py", "build.gradle",
}


def _is_marker(basename: str) -> bool:
    b = basename.lower()
    return b in _MARKER_NAMES or b.endswith(".build.cs") or b.endswith(".uplugin")


def module_roots(store: "stores.Store") -> set[str]:
    """Directories that look like module roots (contain a build marker file).

    Cached per Store: the O(files) marker scan runs once, not per rollup call.
    A repo-root marker is stored as ``""`` (empty prefix).
    """
    cached = getattr(store, "_ms_module_roots", None)
    if cached is not None:
        return cached
    roots: set[str] = set()
    for row in store.conn.execute("SELECT path FROM files"):
        p = (row["path"] or "").replace("\\", "/")
        if _is_marker(p.rsplit("/", 1)[-1]):
            roots.add(p.rsplit("/", 1)[0] if "/" in p else "")
    store._ms_module_roots = roots
    return roots


def roots_by_len(store: "stores.Store | None") -> list[str]:
    """Module roots, TOTAL-ORDERED longest-first then lexicographically.

    The longest-first order is what makes ``_module_of`` pick the most specific
    ancestor; the secondary ``str`` key removes the hash-seed nondeterminism of
    iterating the underlying ``set`` when two roots share a length.
    """
    if store is None:
        return []
    return sorted(module_roots(store), key=lambda r: (-len(r), r))


def _path_of(node: Node) -> str:
    return (node.label or node.id).replace("\\", "/")


def _dir_of(node: Node) -> str:
    parent = PurePosixPath(_path_of(node)).parent.as_posix()
    return parent if parent not in ("", ".") else "(root)"


def _module_of(node: Node, roots_by_len: list[str]) -> str:
    """Nearest module root that is an ancestor of the node's path, else top segment."""
    path = _path_of(node)
    for root in roots_by_len:  # longest-first
        if root == "":  # repo-root marker: catch-all for files not under a deeper root
            return "(root)"
        if path == root or path.startswith(root + "/"):
            return root
    seg = path.split("/", 1)[0]
    return seg or "(root)"


def _group_fn(level: str, store: "stores.Store | None") -> Callable[[Node], str]:
    if level == "dir":
        return _dir_of
    if level == "module":
        # Total-ordered (len desc, then str) so the rolled output is byte-identical
        # across runs: module_roots() is a set, and sorting by length ALONE leaves
        # equal-length roots in set-iteration (hash-seed) order — non-deterministic.
        roots = roots_by_len(store) if store else []
        return lambda n: _module_of(n, roots)
    raise ValueError(f"unknown rollup level: {level!r} (use file|dir|module)")


def rollup(g: Graph, level: str, store: "stores.Store | None" = None) -> Graph:
    """Collapse `g` to ``level`` (``file`` = identity, ``dir``, or ``module``).

    The returned graph carries `g`'s bounded-expansion accounting, with each
    elided-frontier count re-attributed to the group its source node rolled into.
    """
    if level == "file":
        return g
    group_of = _group_fn(level, store)
    rolled = graph.rollup(g, group_of=group_of)
    rolled.truncated = g.truncated
    rolled.depth_bounded = g.depth_bounded
    rolled.frontier_depth = g.frontier_depth
    rolled.elided = g.elided
    for node_id, count in g.frontier.items():
        node = g.nodes.get(node_id)
        grp = group_of(node) if node is not None else node_id
        rolled.frontier[grp] = rolled.frontier.get(grp, 0) + count
    return rolled
