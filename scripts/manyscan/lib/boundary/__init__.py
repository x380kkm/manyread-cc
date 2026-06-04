# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary —— 符号级的目标↔依赖边界。

文件级依赖对 C++ 重构无用；本模块工作在符号级。它把每个符号归类到一个分区
（``target`` = 你正在分析的代码 / ``dependency`` = 它所依赖的代码 —— 依赖侧可能
包含许多不同的依赖来源），把每条符号边（``extends`` / ``implements`` / ``uses_type``）
解析到一个具体目标并附带可靠性置信度（绝不在多个同名候选中悄悄挑一个），并展开一个
深度为 1 的“依赖汇”切片：目标符号加上它们一层的依赖接口，无论依赖索引有多大。

两个派生视图：
  * 内部耦合 —— 目标区子图（仅 target→target 的边），用于切分缝/强连通分量（SCC）。
  * 依赖表面 —— 二部边界（触达依赖的目标符号）→ 它们的依赖目标，可选按模块汇总。

确定性是强制要求：每个查询都全序，每个集合都按序迭代，计数都是整数（此处无浮点），
有歧义的解析总是产出一个 ``dep:`` 节点 —— 因此相同索引 + 相同根 ⇒ 字节级一致的输出。

这是一个门面（FACADE）包：分析各阶段的关注点分散在同级模块中
（``zoning`` / ``nodes`` / ``resolve`` / ``build`` / ``views``）；此处重新导出全部公开 +
私有接口，使 ``from lib import boundary; boundary.<name>`` 与
``from lib.boundary import <name>`` 保持不变。
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
#### /重新导出门面接口 ####
