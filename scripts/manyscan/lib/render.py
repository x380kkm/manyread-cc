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
from pathlib import Path

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


# --- self-contained interactive HTML (cytoscape.js, force-directed cose) -----
# A single file that opens in any browser — no server/node/build. Built to scale
# to MANY nodes (engine-level bounded slices): force layout + pan/zoom + search +
# color-by-kind, with the bounded-truncation banner kept visible. For very large
# slices, roll up to dir/module first (manyscan's real scale lever).
_ASSET_LIB = Path(__file__).resolve().parent.parent / "assets" / "cytoscape.min.js"
_CDN_LIB = "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"
_PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#9d7660"]

_HTML_BOOTSTRAP = """
const cy = cytoscape({
  container: document.getElementById('cy'),
  elements: DATA,
  style: [
    {selector:'node', style:{'label':'data(label)','font-size':'8px','width':16,'height':16,
      'background-color':'data(color)','color':'#223','text-valign':'bottom','text-halign':'center',
      'text-wrap':'wrap','text-max-width':'140px'}},
    {selector:'node[frontier > 0]', style:{'border-width':3,'border-color':'#e15759','border-style':'dashed'}},
    {selector:'edge', style:{'width':1,'line-color':'#c4c9d4','target-arrow-color':'#c4c9d4',
      'target-arrow-shape':'triangle','arrow-scale':0.7,'curve-style':'bezier'}},
    {selector:'.dim', style:{'opacity':0.1}},
    {selector:'.hit', style:{'background-color':'#ffec3d','border-width':3,'border-color':'#f5a623','z-index':99}}
  ],
  layout: {name:'cose', animate:false, padding:30, nodeRepulsion:9000, idealEdgeLength:90, nodeOverlap:8},
  wheelSensitivity: 0.2
});
const q = document.getElementById('q');
q.addEventListener('input', function(){
  const s = q.value.trim().toLowerCase();
  cy.batch(function(){
    cy.elements().removeClass('dim hit');
    if(!s) return;
    const hits = cy.nodes().filter(function(n){return (n.data('label')||'').toLowerCase().indexOf(s)>=0;});
    if(hits.length===0) return;
    cy.elements().addClass('dim');
    hits.removeClass('dim').addClass('hit');
    hits.closedNeighborhood().removeClass('dim');
    cy.animate({fit:{eles:hits, padding:80}},{duration:300});
  });
});
"""


def _html_escape(s: str | None) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_html(g: Graph, title: str = "manyscan dependency slice") -> str:
    """Render a Graph as ONE self-contained interactive HTML file (cytoscape.js).

    The cytoscape lib is inlined from the vendored asset (offline; CDN fallback if
    the asset is missing), as is the graph data — so the output is a single file
    that renders a force-directed, zoomable, searchable graph in any browser.
    """
    kinds = sorted({(n.kind or "node") for n in g.nodes.values()})
    kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(kinds)}

    elements: list[dict] = []
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        extra = g.frontier.get(n.id, 0)
        label = n.label or n.id
        if extra:
            label = f"{label}  +{extra}⤳"
        elements.append({"data": {
            "id": n.id, "label": label, "kind": n.kind or "node",
            "color": kcolor.get(n.kind or "node", "#888"), "frontier": extra,
            "evidence": str(n.evidence) if n.evidence else "",
        }})
    for i, e in enumerate(sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation))):
        elements.append({"data": {"id": f"e{i}", "source": e.src, "target": e.dst, "rel": e.relation}})
    data_json = json.dumps(elements, ensure_ascii=False)

    banner = ""
    if g.truncated:
        banner = f"bounded: capped at level {g.frontier_depth}, {g.elided} deps elided"
    elif g.depth_bounded:
        banner = f"bounded: depth-capped at level {g.frontier_depth}"

    legend = "".join(
        f'<span class="lg"><i style="background:{kcolor[k]}"></i>{_html_escape(k)}</span>' for k in kinds
    )
    meta = f"{len(g.nodes)} nodes &middot; {len(g.edges)} edges"

    if _ASSET_LIB.is_file():
        lib = "<script>" + _ASSET_LIB.read_text(encoding="utf-8") + "</script>"
    else:
        lib = f'<script src="{_CDN_LIB}"></script>'

    head = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_html_escape(title)}</title><style>"
        "html,body{margin:0;height:100%;font:13px system-ui,Segoe UI,sans-serif}"
        "#bar{position:fixed;top:0;left:0;right:0;padding:6px 10px;background:#1f2430;color:#eee;"
        "z-index:10;display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
        "#bar b{font-weight:600}.meta{color:#9aa6b2}.warn{color:#ffcf5c}"
        "#q{width:200px;padding:3px 6px;border-radius:4px;border:1px solid #556;background:#2b3142;color:#eee}"
        ".lg{display:inline-flex;align-items:center;gap:4px;color:#cdd6e0}"
        ".lg i{width:10px;height:10px;border-radius:50%;display:inline-block}"
        "#cy{position:fixed;top:44px;left:0;right:0;bottom:0;background:#fbfbfd}"
        "</style></head><body><div id='bar'>"
        f"<b>{_html_escape(title)}</b><span class='meta'>{meta}</span>"
        + (f"<span class='warn'>&#9888; {_html_escape(banner)}</span>" if banner else "")
        + "<input id='q' placeholder='search node...'>"
        f"<span style='display:flex;gap:8px;flex-wrap:wrap'>{legend}</span>"
        "</div><div id='cy'></div>"
    )
    script = "<script>const DATA=" + data_json + ";\n" + _HTML_BOOTSTRAP + "</script>"
    return head + lib + script + "</body></html>"


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


FORMATS = {"json": to_json, "mermaid": to_mermaid, "dot": to_dot, "text": to_text, "html": to_html}


def render(g: Graph, fmt: str) -> str:
    """Render a Graph in ``fmt`` (json|mermaid|dot|text|html)."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r} (use {'/'.join(FORMATS)})")
    return FORMATS[fmt](g)
