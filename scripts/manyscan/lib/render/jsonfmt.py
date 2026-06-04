# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render.jsonfmt —— Graph / Metrics 的确定性 JSON 视图。"""
from __future__ import annotations

import json
from dataclasses import asdict

from lib import analyze
from lib.graph import Graph


#### 把图序列化为可 JSON 化的 dict [@380kkm 2026-06-05] ####
def graph_to_dict(g: Graph) -> dict:
    """把 Graph 转为 dict:节点、边均按 id 排序以保证稳定,并显式带出有界信息。

    返回的 ``bounded`` 段记录是否截断/深度封顶、边界深度、省略依赖数,以及边界节点
    到越界依赖数的映射(frontier)。
    """
    return {
        "nodes": [
            {"id": n.id, "kind": n.kind, "label": n.label, "store": n.store,
             "evidence": str(n.evidence) if n.evidence else None}
            for n in sorted(g.nodes.values(), key=lambda n: n.id)
        ],
        "edges": [
            {"src": e.src, "dst": e.dst, "relation": e.relation, "weight": e.weight,
             "evidence": str(e.evidence) if e.evidence else None}
            for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation))
        ],
        "bounded": {
            "truncated": g.truncated, "depth_bounded": g.depth_bounded,
            "frontier_depth": g.frontier_depth, "elided": g.elided,
            "frontier": dict(sorted(g.frontier.items())),
        },
    }
#### /把图序列化为可 JSON 化的 dict ####


#### 把度量结果序列化为可 JSON 化的 dict [@380kkm 2026-06-05] ####
def metrics_to_dict(m: analyze.Metrics) -> dict:
    return {
        "summary": m.summary,
        "bounded": m.bounded,
        "nodes": [asdict(nm) for nm in m.nodes],
        "cycles": m.cycles,
        "bridges": [list(b) for b in m.bridges],
        "cut_nodes": m.cut_nodes,
        "layers": m.layers,
        "leftover": m.leftover,
    }
#### /把度量结果序列化为可 JSON 化的 dict ####


#### 把 Graph 或 Metrics 渲染为 JSON 字符串 [@380kkm 2026-06-05] ####
def to_json(obj: Graph | analyze.Metrics, indent: int | None = 2) -> str:
    """把 Graph 或 Metrics 渲染为 JSON 文本(``ensure_ascii=False`` 以保留中文)。

    Args:
      obj: 待渲染的 Graph 或 analyze.Metrics;按类型自动分派到对应的转换函数。
      indent: JSON 缩进;None 表示紧凑输出。
    """
    data = metrics_to_dict(obj) if isinstance(obj, analyze.Metrics) else graph_to_dict(obj)
    return json.dumps(data, ensure_ascii=False, indent=indent)
#### /把 Graph 或 Metrics 渲染为 JSON 字符串 ####
