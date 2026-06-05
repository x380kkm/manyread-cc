# audience: internal
# manyscan.lib.boundary.confidence
"""manyscan.lib.boundary.confidence —— 逐边置信度（edge_confidence）的默认值与重建惯用法。

boundary 各构建/视图把 ``edge.key() -> confidence`` 字串记在图上；缺记的边按 ``direct``
（直接解析、无歧义）回填。``DEFAULT_CONFIDENCE`` 是这个默认串的唯一字面量来源，
``bake_confidence`` 是「以默认值重建当前图全部边的置信度映射」的唯一实现。
"""
from __future__ import annotations

#### 缺记置信度时的默认值（直接解析、无歧义） [@380kkm 2026-06-05] ####
DEFAULT_CONFIDENCE = "direct"


#### 以 base 为底、缺则 DEFAULT_CONFIDENCE，重建 edges 的逐边置信度映射 [@380kkm 2026-06-05] ####
def bake_confidence(edges, base: dict) -> dict:
    """``base`` 是 ``edge.key() -> confidence`` 的来源（可空）；返回仅含当前 ``edges`` 之 key
    的新映射，每条边取 ``base`` 中的记录，缺则 ``DEFAULT_CONFIDENCE``。
    """
    return {e.key(): base.get(e.key(), DEFAULT_CONFIDENCE) for e in edges}
