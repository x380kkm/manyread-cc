# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary — SYMBOL-LEVEL target↔dependency boundary.

File-level deps are useless for C++ refactoring; this module works at the symbol
level. It classifies every symbol into a ZONE (``target`` = the code you are
analyzing / ``dependency`` = what it depends on — and the dependency side may
hold MANY distinct dependency sources), resolves each symbol edge (``extends`` /
``implements`` / ``uses_type``) to a concrete target WITH a soundness CONFIDENCE
(never silently picking one of many by-name candidates), and expands a depth-1
*dependency-sink* slice: target symbols plus their one-layer dependency interface,
regardless of how large the dependency index is.

Two derived views:
  * INTERNAL coupling — the target-zone subgraph (target→target edges only),
    for split seams / SCC.
  * DEPENDENCY surface — the bipartite boundary (target symbols that reach a
    dependency) → their dependency targets, optionally rolled up by module.

DETERMINISM is mandatory: every query is total-ordered, every set iterated
sorted, counts are integers (no floats here), and ambiguous resolution always
yields a ``dep:`` node — so the same index + same roots ⇒ byte-identical output.

This is a FACADE package: the analysis-stage concerns live in sibling modules
(``zoning`` / ``nodes`` / ``resolve`` / ``build`` / ``views``); the full public +
private surface is re-exported here so ``from lib import boundary; boundary.<name>``
and ``from lib.boundary import <name>`` are unchanged.
"""
from __future__ import annotations

from .zoning import (
    DEPENDENCY,
    TARGET,
    Zoning,
    _NORM,
    dependency_label,
    detect_target_root,
    has_module_markers,
    make_zoning,
    norm_root,
    zone_of_path,
)
from .nodes import (
    ambiguous_internal_node,
    external_node,
    qualified_name,
    symbol_node,
)
from .resolve import (
    REL,
    Resolved,
    out_edges,
    resolve_target,
)
from .build import (
    _MACRO_RE,
    _target_seed_rows,
    build,
)
from .views import (
    DEP_CORE,
    DEP_IFACE,
    TARGET_CORE,
    TARGET_IFACE,
    _FakeNode,
    _MODULE_ZONE_COLOR,
    _carry_confidence,
    assign_bands,
    assign_modules,
    boundary_nodes,
    Crossing,
    crossings,
    dependency_surface,
    internal_view,
)

__all__ = [
    "TARGET",
    "DEPENDENCY",
    "TARGET_CORE",
    "TARGET_IFACE",
    "DEP_IFACE",
    "DEP_CORE",
    "REL",
    "Zoning",
    "norm_root",
    "detect_target_root",
    "has_module_markers",
    "make_zoning",
    "zone_of_path",
    "dependency_label",
    "qualified_name",
    "symbol_node",
    "external_node",
    "ambiguous_internal_node",
    "Resolved",
    "resolve_target",
    "out_edges",
    "build",
    "internal_view",
    "boundary_nodes",
    "assign_bands",
    "assign_modules",
    "dependency_surface",
    "Crossing",
    "crossings",
]
