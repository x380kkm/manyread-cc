# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render.textfmt —— Graph / Metrics 的确定性纯文本视图。"""
from __future__ import annotations

from lib import analyze
from lib.graph import Graph


#### 生成有界状态提示行(截断/深度封顶) [@380kkm 2026-06-05] ####
def _bounded_lines(truncated: bool, depth_bounded: bool, frontier_depth: int,
                   elided: int, frontier: dict) -> list[str]:
    if truncated:
        dist = ", ".join(f"{k}→{v}" for k, v in sorted(frontier.items()))
        return [f"⚠ 已在第 {frontier_depth} 层封顶,省略 {elided} 个依赖(分布: {dist})"]
    if depth_bounded:
        return [f"ℹ 已按深度封顶在第 {frontier_depth} 层(边缘节点可能有更深依赖)"]
    return []
#### /生成有界状态提示行 ####


#### 把图渲染为纯文本节点清单 [@380kkm 2026-06-05] ####
def to_text(g: Graph) -> str:
    """首行统计节点/边数,有界提示行紧跟其后,随后按 id 排序逐行列出节点标签,边界节点行尾标注 ``(+N 越界)``。"""
    lines = [f"nodes={len(g.nodes)} edges={len(g.edges)}"]
    lines += _bounded_lines(g.truncated, g.depth_bounded, g.frontier_depth, g.elided, g.frontier)
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        suffix = f"  (+{g.frontier[n.id]} 越界)" if n.id in g.frontier else ""
        lines.append(f"  - {n.label or n.id}{suffix}")
    return "\n".join(lines) + "\n"
#### /把图渲染为纯文本节点清单 ####


#### 把度量结果渲染为纯文本报告 [@380kkm 2026-06-05] ####
def metrics_text(m: analyze.Metrics) -> str:
    """首行汇总节点/边/环/桥/切点/分层计数;随后有界提示、最不稳定与最被依赖节点;再列出环、桥、切点及不稳定度前 5 的节点。"""
    s = m.summary
    lines = [
        f"nodes={s['nodes']} edges={s['edges']} | cycles={s['cycles']} "
        f"bridges={s['bridges']} cut_nodes={s['cut_nodes']} layers={s['layers']}"
    ]
    lines += _bounded_lines(m.bounded.get("truncated", False), m.bounded.get("depth_bounded", False),
                            m.bounded.get("frontier_depth", 0), m.bounded.get("elided", 0),
                            m.bounded.get("frontier", {}))
    lines.append(f"most_unstable: {s.get('most_unstable')}")
    lines.append(f"most_depended_on: {s.get('most_depended_on')}")
    if m.cycles:
        lines.append("cycles(需解耦): " + "; ".join("↔".join(c) for c in m.cycles))
    if m.bridges:
        lines.append("bridges(候选切点): " + ", ".join(f"{a}->{b}" for a, b, _ in m.bridges))
    if m.cut_nodes:
        lines.append("cut_nodes(脆弱枢纽): " + ", ".join(m.cut_nodes))
    if m.nodes:
        lines.append("top instability:")
        for nm in m.nodes[:5]:
            lines.append(f"  - {nm.label} I={nm.instability} (Ca={nm.ca},Ce={nm.ce})")
    return "\n".join(lines) + "\n"
#### /把度量结果渲染为纯文本报告 ####
