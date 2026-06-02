# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render — deterministic JSON / mermaid / dot / text views.

Every emitter sorts its output so results are stable (golden-testable). The
bounded-expansion accounting is rendered *explicitly* everywhere — a frontier node
is tagged ``+N⤳`` and a truncated/ depth-bounded slice prints a visible warning —
so a budget-capped slice can never be mistaken for a complete one.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from lib import analyze
from lib.graph import Graph


def _esc(s: str | None) -> str:
    return (s or "").replace('"', "'").replace("\n", " ")


def _mid(node_id: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in node_id)


# --- JSON --------------------------------------------------------------------
def graph_to_dict(g: Graph) -> dict:
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


def to_json(obj: Graph | analyze.Metrics, indent: int | None = 2) -> str:
    data = metrics_to_dict(obj) if isinstance(obj, analyze.Metrics) else graph_to_dict(obj)
    return json.dumps(data, ensure_ascii=False, indent=indent)


# --- mermaid -----------------------------------------------------------------
def to_mermaid(g: Graph) -> str:
    lines = ["flowchart TD"]
    if g.truncated:
        lines.append(f"  %% truncated at level {g.frontier_depth}: {g.elided} deps elided")
    if g.depth_bounded:
        lines.append(f"  %% depth-bounded at level {g.frontier_depth}")
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} +{extra}⤳"
        lines.append(f'  {_mid(n.id)}["{_esc(label)}"]')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f"  {_mid(e.src)} -->|{_esc(e.relation)}| {_mid(e.dst)}")
    return "\n".join(lines) + "\n"


# --- graphviz dot ------------------------------------------------------------
def to_dot(g: Graph) -> str:
    lines = ["digraph manyscan {", "  rankdir=LR;"]
    if g.truncated:
        lines.append(f'  label="truncated@L{g.frontier_depth}: {g.elided} elided"; labelloc=b;')
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} (+{extra})"
        lines.append(f'  "{n.id}" [label="{_esc(label)}"];')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f'  "{e.src}" -> "{e.dst}" [label="{_esc(e.relation)}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


# --- text --------------------------------------------------------------------
def _bounded_lines(truncated: bool, depth_bounded: bool, frontier_depth: int,
                   elided: int, frontier: dict) -> list[str]:
    if truncated:
        dist = ", ".join(f"{k}→{v}" for k, v in sorted(frontier.items()))
        return [f"⚠ 已在第 {frontier_depth} 层封顶,省略 {elided} 个依赖(分布: {dist})"]
    if depth_bounded:
        return [f"ℹ 已按深度封顶在第 {frontier_depth} 层(边缘节点可能有更深依赖)"]
    return []


def to_text(g: Graph) -> str:
    lines = [f"nodes={len(g.nodes)} edges={len(g.edges)}"]
    lines += _bounded_lines(g.truncated, g.depth_bounded, g.frontier_depth, g.elided, g.frontier)
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        suffix = f"  (+{g.frontier[n.id]} 越界)" if n.id in g.frontier else ""
        lines.append(f"  - {n.label or n.id}{suffix}")
    return "\n".join(lines) + "\n"


def metrics_text(m: analyze.Metrics) -> str:
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


FORMATS = {"json": to_json, "mermaid": to_mermaid, "dot": to_dot, "text": to_text}


def render(g: Graph, fmt: str) -> str:
    """Render a Graph in ``fmt`` (json|mermaid|dot|text)."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r} (use {'/'.join(FORMATS)})")
    return FORMATS[fmt](g)
