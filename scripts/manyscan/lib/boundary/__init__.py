# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.boundary
"""manyscan.lib.boundary —— 符号级的目标↔依赖边界。

把每个符号归类到一个分区（``target`` = 被分析的代码 / ``dependency`` = 其依赖），把每条符号边
（``extends`` / ``implements`` / ``uses_type``）解析到一个具体目标并附带可靠性置信度，
展开一个深度为 1 的“依赖汇”切片：目标符号加上它们一层的依赖接口。

两个派生视图：
  * 内部耦合 —— 目标区子图（仅 target→target 的边），用于切分缝/强连通分量（SCC）。
  * 依赖表面 —— 二部边界（触达依赖的目标符号）→ 它们的依赖目标，可选按模块汇总。

门面包：各阶段分散在同级模块（``zoning`` / ``nodes`` / ``resolve`` / ``build`` / ``views``），
此处重新导出全部公开 + 私有接口。
"""
from __future__ import annotations

#### 从各同级阶段模块重新导出全部公开 + 私有接口 [@380kkm 2026-06-05] ####
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
from .modules import (
    DEFAULT_FALLBACK,
    ModuleSpec,
    ModuleZone,
    ambiguous_module_node,
    build_modules,
    classify_edge,
    cut_costs,
    fan_stats,
    like_prefix,
    make_module_spec,
    module_cycles,
    module_of_path,
    module_symbol_node,
    needed_symbols,
    parse_inline_module,
    resolve_module_target,
    winning_prefix,
    zone_matrix,
    CrossStat,
    CutCost,
    ModuleFanStat,
    Need,
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
    "DEFAULT_FALLBACK",
    "ModuleSpec",
    "ModuleZone",
    "make_module_spec",
    "module_of_path",
    "parse_inline_module",
    "like_prefix",
    "build_modules",
    "module_symbol_node",
    "resolve_module_target",
    "ambiguous_module_node",
    "classify_edge",
    "zone_matrix",
    "needed_symbols",
    "module_cycles",
    "fan_stats",
    "cut_costs",
    "winning_prefix",
    "CrossStat",
    "CutCost",
    "ModuleFanStat",
    "Need",
]
#### /重新导出门面接口 ####
