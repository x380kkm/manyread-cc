# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render —— 确定性的 JSON / mermaid / dot / text / HTML 视图门面。

每个 emitter 对输出排序;有界账本显式呈现:边界节点标注 ``+N⤳``,截断/深度封顶的切片打印可见警告。
本包是门面(FACADE):调用方 ``from lib import render`` 后直接用 ``render.<name>``。各 emitter 分散在
按格式划分的子模块(jsonfmt / graphfmt / textfmt / html)中;本模块重导出其公开接口并持有格式注册表 ``FORMATS``。
"""
from __future__ import annotations

from lib.graph import Graph

from .graphfmt import to_dot, to_mermaid
from .html import _importance, to_html
from .jsonfmt import graph_to_dict, metrics_to_dict, modules_to_dict, to_json
from .textfmt import metrics_text, to_text

#### 格式名到 emitter 的注册表 [@380kkm 2026-06-05] ####
FORMATS = {"json": to_json, "mermaid": to_mermaid, "dot": to_dot, "text": to_text, "html": to_html}


#### 按格式名渲染一张图 [@380kkm 2026-06-05] ####
def render(g: Graph, fmt: str) -> str:
    """``fmt`` 取 json|mermaid|dot|text|html;格式未知时抛 ValueError。"""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r} (use {'/'.join(FORMATS)})")
    return FORMATS[fmt](g)


__all__ = [
    "render", "FORMATS",
    "to_json", "to_html", "to_mermaid", "to_dot", "to_text",
    "graph_to_dict", "metrics_to_dict", "modules_to_dict", "metrics_text", "_importance",
]
