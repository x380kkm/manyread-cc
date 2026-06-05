# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.modules —— N 路模块分区的门面（与二进制 boundary 并行）。

把分散在 ``modulespec`` / ``modules_build`` / ``modules_views`` 的 N 路原语、构建与视图重新
导出，使调用方 ``from lib import boundary`` 后直接用 ``boundary.<name>``。二进制 ``zoning`` /
``build`` / ``views`` 不受影响，N 路路径完全并行。
"""
from __future__ import annotations

#### 重新导出 N 路模块分区的原语/构建/视图接口 [@380kkm 2026-06-05] ####
from .modulespec import (
    DEFAULT_FALLBACK,
    ModuleSpec,
    ModuleZone,
    like_prefix,
    make_module_spec,
    module_of_path,
    parse_inline_module,
)
from .modules_build import (
    ambiguous_module_node,
    build_modules,
    module_symbol_node,
    resolve_module_target,
)
from .modules_views import (
    CrossStat,
    CutCost,
    ModuleFanStat,
    Need,
    classify_edge,
    cut_costs,
    fan_stats,
    module_cycles,
    needed_symbols,
    winning_prefix,
    zone_matrix,
)

__all__ = [
    "DEFAULT_FALLBACK", "ModuleSpec", "ModuleZone", "make_module_spec", "module_of_path",
    "parse_inline_module", "like_prefix",
    "build_modules", "module_symbol_node", "resolve_module_target", "ambiguous_module_node",
    "classify_edge", "zone_matrix", "needed_symbols", "module_cycles", "fan_stats",
    "cut_costs", "winning_prefix", "CrossStat", "CutCost", "ModuleFanStat", "Need",
]
#### /重新导出 N 路门面接口 ####
