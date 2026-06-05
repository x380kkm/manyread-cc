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
    """节点、边按 id 排序;``bounded`` 段记录截断/深度封顶、边界深度、省略依赖数及 frontier 映射。"""
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


#### 把 N 路模块解耦视图序列化为可 JSON 化的 dict [@380kkm 2026-06-05] ####
def modules_to_dict(g: Graph, spec) -> dict:
    """确定性输出：``matrix``（NxN 每对统计）、``modules``（含扇入/扇出/不稳定度）、
    ``cycles``（模块级 SCC）、``needed``（按有序模块对的按需符号 + 证据 + 引用计数）、
    ``cut_costs``（按对的最便宜切割，升序）。键全排序，便于逐字节比对。
    """
    from lib.boundary import modules_views as mv

    mat = mv.zone_matrix(g)
    needed = mv.needed_symbols(g, spec)
    fans = mv.fan_stats(mat)
    cycles = mv.module_cycles(g)
    cuts = mv.cut_costs(needed, mat)

    matrix = [
        {"src": a, "dst": b, "edge_count": s.edge_count, "weight": s.weight,
         "kind": "intra" if a == b else "cross",
         "by_relation": dict(sorted(s.by_relation.items()))}
        for (a, b), s in sorted(mat.items())
    ]
    modules = [
        {"module": f.module, "fan_in": f.fan_in, "fan_out": f.fan_out,
         "instability": f.instability}
        for f in fans
    ]
    needed_out = [
        {"src": a, "dst": b, "symbols": [
            {"dst": nd.dst, "label": nd.dst_label, "path": nd.dst_path,
             "relations": list(nd.relations), "ref_count": nd.ref_count,
             "winning_prefix": nd.winning_prefix}
            for nd in needs]}
        for (a, b), needs in sorted(needed.items())
    ]
    cut_out = [
        {"src": c.src_module, "dst": c.dst_module, "cost": c.cost, "edge_count": c.edge_count}
        for c in cuts
    ]
    return {
        "fallback": spec.fallback,
        "zones": [z.name for z in spec.zones],
        "matrix": matrix,
        "modules": modules,
        "cycles": [sorted(c) for c in cycles],
        "needed": needed_out,
        "cut_costs": cut_out,
        "bounded": {"truncated": g.truncated, "elided": g.elided,
                    "node_count": len(g.nodes), "edge_count": len(g.edges)},
    }
#### /把 N 路模块解耦视图序列化 ####


#### 把 Graph 或 Metrics 渲染为 JSON 字符串 [@380kkm 2026-06-05] ####
def to_json(obj: Graph | analyze.Metrics, indent: int | None = 2) -> str:
    """按类型把 Graph 或 Metrics 分派到对应转换函数;``indent`` 为 None 时紧凑输出。"""
    data = metrics_to_dict(obj) if isinstance(obj, analyze.Metrics) else graph_to_dict(obj)
    return json.dumps(data, ensure_ascii=False, indent=indent)
#### /把 Graph 或 Metrics 渲染为 JSON 字符串 ####
