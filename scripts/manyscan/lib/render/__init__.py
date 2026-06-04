# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render —— 确定性的 JSON / mermaid / dot / text / HTML 视图门面。

每个 emitter 都对输出排序,使结果稳定(可做 golden 测试)。有界扩展账本在各处都*显式*
呈现:边界节点标注 ``+N⤳``,被截断/深度封顶的切片打印可见警告——这样预算封顶的切片
永远不会被误当成完整切片。

本包是门面(FACADE):调用方 ``from lib import render`` 后直接用 ``render.<name>``。
各 emitter 分散在按格式划分的子模块(jsonfmt / graphfmt / textfmt / html)中;本模块
重导出它们的公开接口,并持有格式注册表(单选 ``FORMATS`` 工厂)。
"""
from __future__ import annotations

from lib.graph import Graph

from .graphfmt import to_dot, to_mermaid
from .html import _importance, to_html
from .jsonfmt import graph_to_dict, metrics_to_dict, to_json
from .textfmt import metrics_text, to_text

#### 格式名到 emitter 的注册表 [@380kkm 2026-06-05] ####
FORMATS = {"json": to_json, "mermaid": to_mermaid, "dot": to_dot, "text": to_text, "html": to_html}


#### 按格式名渲染一张图 [@380kkm 2026-06-05] ####
def render(g: Graph, fmt: str) -> str:
    """按 ``fmt``(json|mermaid|dot|text|html)渲染一张 Graph;格式未知时抛 ValueError。"""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r} (use {'/'.join(FORMATS)})")
    return FORMATS[fmt](g)


__all__ = [
    "render", "FORMATS",
    "to_json", "to_html", "to_mermaid", "to_dot", "to_text",
    "graph_to_dict", "metrics_to_dict", "metrics_text", "_importance",
]
