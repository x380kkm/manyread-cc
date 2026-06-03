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
  boxSelectionEnabled: false,
  userPanningEnabled: true,
  userZoomingEnabled: true,
  autoungrabify: false,
  selectionType: 'single',
  style: [
    {selector:'node', style:{'label':'data(label)','font-size':'8px',
      'width':'mapData(deg,0,DEGMAX,18,64)','height':'mapData(deg,0,DEGMAX,18,64)',
      'background-color':'data(color)','color':'#223','text-valign':'bottom','text-halign':'center',
      'text-wrap':'wrap','text-max-width':'140px'}},
    {selector:'node[frontier > 0]', style:{'border-width':3,'border-color':'#e15759','border-style':'dashed'}},
    {selector:'edge', style:{'width':1,'line-color':'#c4c9d4','target-arrow-color':'#c4c9d4',
      'target-arrow-shape':'triangle','arrow-scale':0.7,'curve-style':'bezier'}},
    {selector:'edge[conf="unique"]', style:{'line-style':'solid'}},
    {selector:'edge[conf="direct"]', style:{'line-style':'solid'}},
    {selector:'edge[conf="ambiguous"]', style:{'line-style':'dashed','line-color':'#e15759','target-arrow-color':'#e15759'}},
    {selector:'edge[conf="unresolved"]', style:{'line-style':'dotted','line-color':'#b07aa1','target-arrow-color':'#b07aa1'}},
    {selector:'edge.seam', style:{'line-color':'#e15759','target-arrow-color':'#e15759','line-style':'dashed','width':2}},
    // (3) HUB highlight: heavily-depended-on / articulation nodes — ring + halo + size bump.
    {selector:'node[hub=1]', style:{'border-width':4,'border-color':'#f5a623','border-opacity':1,
      'background-blacken':-0.1,'width':'mapData(deg,0,DEGMAX,34,80)','height':'mapData(deg,0,DEGMAX,34,80)',
      'z-index':50,'font-weight':'bold','underlay-color':'#f5a623','underlay-opacity':0.25,'underlay-padding':6}},
    // (3) BRIDGE highlight: articulation edges linking two modules — thick solid red, on top.
    {selector:'edge[bridge=1]', style:{'width':3,'line-color':'#e15759','target-arrow-color':'#e15759',
      'line-style':'solid','z-index':40}},
    {selector:'.dim', style:{'opacity':0.1}},
    {selector:'.vhide', style:{'display':'none'}},
    {selector:'.hit', style:{'background-color':'#ffec3d','border-width':3,'border-color':'#f5a623','z-index':99}},
    // (4) ZONE treatment: faint, borderless compound parents with just a soft top-left label
    // (no '一堆方框'); non-grabbable + events:no so drags/taps fall through to canvas pan.
    {selector:'node:parent', style:{'background-opacity':0.04,'background-color':'data(zonecolor)',
      'border-width':0,'shape':'round-rectangle','label':'data(label)','text-valign':'top','text-halign':'left',
      'font-size':'16px','font-weight':'600','color':'#9aa6b2','text-opacity':0.6,'padding':'30px','events':'no'}}
  ],
  layout: {name:'cose', animate:false, padding:30, nodeRepulsion:9000, idealEdgeLength:90, nodeOverlap:8},
  wheelSensitivity: 0.2
});
// seams: edges crossing a cluster boundary (the cuts an SRP split would make)
cy.edges().forEach(function(ed){
  const a=ed.source().data('cluster'), b=ed.target().data('cluster');
  if(a && b && a!==b) ed.addClass('seam');
});
// info panel: tap a node to GET ITS FILE PATH (+ kind/cluster); tap blank to close
const info = document.getElementById('info');
function showInfo(d){
  info.innerHTML='';
  const b=document.createElement('b'); b.textContent=d.label||d.id; info.appendChild(b);
  const m=document.createElement('div'); m.className='k';
  m.textContent=(d.kind||'')+(d.cluster?('  ·  '+d.cluster):''); info.appendChild(m);
  const c=document.createElement('code'); c.textContent=d.path||''; c.title='click to select'; info.appendChild(c);
  info.style.display='block';
}
cy.on('tap','node',function(e){ showInfo(e.target.data()); });
cy.on('tap',function(e){ if(e.target===cy) info.style.display='none'; });
const q = document.getElementById('q');
q.addEventListener('input', function(){
  const s = q.value.trim().toLowerCase();
  cy.batch(function(){
    cy.elements().removeClass('dim hit');
    if(!s) return;
    const hits = cy.nodes().filter(function(n){return ((n.data('label')||'')+' '+(n.data('path')||'')).toLowerCase().indexOf(s)>=0;});
    if(hits.length===0) return;
    cy.elements().addClass('dim');
    hits.removeClass('dim').addClass('hit');
    hits.closedNeighborhood().removeClass('dim');
    cy.animate({fit:{eles:hits, padding:80}},{duration:300});
  });
});
// (5) ONE-PAGE VIEW TOGGLE: internal | engine | both — show/hide only (deterministic,
// single file). 'internal' = plugin-only; 'engine' = boundary plugin + engine + cross
// edges; 'both' = everything. Re-layouts the visible eles + fits.
const viewSel = document.getElementById('view');
const HASZONES = (typeof HAS_ZONES !== 'undefined') && HAS_ZONES;
if(!HASZONES && viewSel){ viewSel.parentNode.style.display = 'none'; }
function realNodes(){ return cy.nodes().filter(function(n){ return !n.isParent(); }); }
function applyView(v){
  if(!HASZONES) return;
  cy.batch(function(){
    cy.elements().removeClass('vhide');
    if(v==='internal'){
      const eng = realNodes().filter(function(n){ return n.data('zone')==='engine'; });
      eng.addClass('vhide');
      eng.connectedEdges().addClass('vhide');
    } else if(v==='engine'){
      // hide pure plugin->plugin edges; keep cross edges + their plugin endpoints + engine nodes
      const ppEdges = cy.edges().filter(function(ed){
        return ed.source().data('zone')==='plugin' && ed.target().data('zone')==='plugin';
      });
      ppEdges.addClass('vhide');
      // plugin nodes with no crossing edge are not on the boundary -> hide
      realNodes().filter(function(n){ return n.data('zone')==='plugin'; }).forEach(function(n){
        const hasCross = n.connectedEdges().some(function(ed){ return ed.data('cross')===1; });
        if(!hasCross){ n.addClass('vhide'); }
      });
    }
  });
  const vis = cy.elements().not('.vhide');
  cy.layout({name:'cose', animate:false, padding:30, nodeRepulsion:9000,
             idealEdgeLength:90, nodeOverlap:8, eles:vis}).run();
  cy.fit(cy.elements().not('.vhide'), 30);
}
if(HASZONES && viewSel){
  viewSel.addEventListener('change', function(){ applyView(viewSel.value); });
  applyView(viewSel.value);
}
"""


def _html_escape(s: str | None) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _importance(g: Graph) -> dict[str, dict]:
    """Per-node importance signals (pure, deterministic):

    ``{deg, fan_in, fan_out, hub, bridge}`` for every node id. ``fan_in``/``fan_out``
    mirror :func:`analyze.node_metrics` (distinct neighbours, self-loops excluded);
    ``deg = fan_in + fan_out`` drives node sizing for ALL graphs. ``hub`` flags the
    heavily-depended-on / fragile centres — the union of ``analyze.cut_nodes`` and the
    top-fan_in nodes (``fan_in >= max(2, p90)``). ``bridge`` flags a node touched by an
    ``analyze.bridges`` articulation edge (the per-edge bridge set is returned separately).
    Everything is integer-only and sorted, so two runs are byte-identical.
    """
    info: dict[str, dict] = {}
    fan_in: dict[str, int] = {}
    for nid in sorted(g.nodes):
        ce = len({s for s in g.successors(nid) if s != nid})
        ca = len({p for p in g.predecessors(nid) if p != nid})
        fan_in[nid] = ca
        info[nid] = {"deg": ca + ce, "fan_in": ca, "fan_out": ce, "hub": 0, "bridge": 0}

    hubs: set[str] = set(analyze.cut_nodes(g))
    fins = sorted(fan_in.values())
    if fins:
        # p90 (nearest-rank) over fan_in; gate at >= max(2, p90) so only the truly
        # depended-on centres light up (and tiny graphs need >=2 incoming).
        p90 = fins[min(len(fins) - 1, (90 * len(fins)) // 100)]
        gate = max(2, p90)
        hubs |= {nid for nid in g.nodes if fan_in[nid] >= gate}
    for nid in sorted(hubs):
        if nid in info:
            info[nid]["hub"] = 1

    for a, b, _rel in analyze.bridges(g):
        for nid in (a, b):
            if nid in info:
                info[nid]["bridge"] = 1
    return info


def to_html(g: Graph, title: str = "manyscan dependency slice", view: str = "both") -> str:
    """Render a Graph as ONE self-contained interactive HTML file (cytoscape.js).

    The cytoscape lib is inlined from the vendored asset (offline; CDN fallback if
    the asset is missing), as is the graph data — so the output is a single file
    that renders a force-directed, zoomable, searchable graph in any browser.
    """
    # Color by cohesive cluster (attrs['cluster']) when present (SRP view), else by kind.
    clustered = any(n.attrs.get("cluster") for n in g.nodes.values())
    if clustered:
        keys = sorted({(n.attrs.get("cluster") or "?") for n in g.nodes.values()})
    else:
        keys = sorted({(n.kind or "node") for n in g.nodes.values()})
    kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}

    def _path_of(n) -> str:
        if n.evidence is not None and getattr(n.evidence, "path", None):
            return n.evidence.path
        return n.label or n.id

    # Light ZONE grouping (symbol-boundary view): when any node carries a
    # plugin/engine zone, place every real node inside one of two cytoscape
    # COMPOUND PARENT boxes — but the parents are rendered FAINT (transparent fill,
    # no border, just a soft top-left label) and are non-grabbable / events:no, so
    # they group the layout without the heavy nested-box look or hijacking pan/drag.
    # Backward compatible: a graph with no 'zone' emits no parent nodes.
    _ZONES = ("plugin", "engine")
    _ZONE_COLOR = {"plugin": "#4e79a7", "engine": "#f28e2b"}
    zoned = any(n.attrs.get("zone") in _ZONES for n in g.nodes.values())

    # (2)(3) Importance: degree-sizing for ALL graphs + hub/bridge highlight markers.
    imp = _importance(g)
    degmax = max([1] + [v["deg"] for v in imp.values()])

    elements: list[dict] = []
    if zoned:  # plugin before engine, deterministic; faint + non-grabbable parents
        for z in _ZONES:
            elements.append({
                "data": {"id": f"__zone_{z}__", "label": z, "zone": z,
                         "zonecolor": _ZONE_COLOR[z]},
                "grabbable": False, "selectable": False, "pannable": True,
            })
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        extra = g.frontier.get(n.id, 0)
        label = n.label or n.id
        if extra:
            label = f"{label}  +{extra}⤳"
        cluster = n.attrs.get("cluster") or ""
        ckey = cluster if clustered else (n.kind or "node")
        ni = imp.get(n.id, {"deg": 0, "fan_in": 0, "hub": 0})
        data = {
            "id": n.id, "label": label, "kind": n.kind or "node",
            "color": kcolor.get(ckey, "#888"), "frontier": extra,
            "path": _path_of(n), "cluster": cluster,
            "evidence": str(n.evidence) if n.evidence else "",
            "deg": ni["deg"], "fan_in": ni["fan_in"], "hub": ni["hub"],
        }
        zone = n.attrs.get("zone")
        if zoned and zone in _ZONES:
            data["parent"] = f"__zone_{zone}__"
            data["zone"] = zone
        elements.append({"data": data})
    edge_conf = getattr(g, "edge_confidence", {})
    bridge_keys = {(a, b, r) for a, b, r in analyze.bridges(g)}
    for i, e in enumerate(sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation))):
        ed = {"id": f"e{i}", "source": e.src, "target": e.dst, "rel": e.relation}
        conf = edge_conf.get(e.key())
        if conf:
            ed["conf"] = conf
        if e.key() in bridge_keys:
            ed["bridge"] = 1
        # (5) tag plugin->engine crossings so the engine view can keep just them.
        if zoned:
            s, d = g.nodes.get(e.src), g.nodes.get(e.dst)
            if (s is not None and d is not None
                    and s.attrs.get("zone") == "plugin" and d.attrs.get("zone") == "engine"):
                ed["cross"] = 1
        elements.append({"data": ed})
    data_json = json.dumps(elements, ensure_ascii=False)

    banner = ""
    if g.truncated:
        banner = f"bounded: capped at level {g.frontier_depth}, {g.elided} deps elided"
    elif g.depth_bounded:
        banner = f"bounded: depth-capped at level {g.frontier_depth}"

    legend = "".join(
        f'<span class="lg"><i style="background:{kcolor[k]}"></i>{_html_escape(k)}</span>' for k in keys
    )
    legend_kind = "cluster" if clustered else "kind"
    meta = f"{len(g.nodes)} nodes &middot; {len(g.edges)} edges"

    # (5) one-page view toggle (only meaningful for zoned graphs; JS hides it otherwise).
    view = view if view in ("both", "internal", "engine") else "both"
    view_opts = "".join(
        f"<option value='{v}'{' selected' if v == view else ''}>{v}</option>"
        for v in ("both", "internal", "engine")
    )
    view_ctl = (
        "<span class='vc'>view <select id='view'>" + view_opts + "</select></span>"
        if zoned else ""
    )

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
        ".vc{color:#cdd6e0;display:inline-flex;align-items:center;gap:4px}"
        ".vc select{padding:2px 4px;border-radius:4px;border:1px solid #556;background:#2b3142;color:#eee}"
        "#cy{position:fixed;top:44px;left:0;right:0;bottom:0;background:#fbfbfd}"
        "#info{display:none;position:fixed;left:10px;bottom:10px;max-width:60%;z-index:20;"
        "background:#1f2430;color:#eee;padding:8px 10px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.3)}"
        "#info b{font-weight:600}#info .k{color:#9aa6b2;font-size:11px;margin:2px 0}"
        "#info code{display:block;color:#a6e3a1;word-break:break-all;-webkit-user-select:all;user-select:all}"
        "</style></head><body><div id='bar'>"
        f"<b>{_html_escape(title)}</b><span class='meta'>{meta} &middot; color={legend_kind} &middot; tap node → path</span>"
        + (f"<span class='warn'>&#9888; {_html_escape(banner)}</span>" if banner else "")
        + "<input id='q' placeholder='search node/path...'>"
        + view_ctl
        + f"<span style='display:flex;gap:8px;flex-wrap:wrap'>{legend}</span>"
        + "</div><div id='cy'></div><div id='info'></div>"
    )
    consts = (
        f"const DATA={data_json};\n"
        f"const DEGMAX={degmax};\n"
        f"const HAS_ZONES={'true' if zoned else 'false'};\n"
    )
    # mapData() parses its mapper string literally (it can't read a JS const), so the
    # DEGMAX token inside the style mappers is substituted with the actual integer.
    bootstrap = _HTML_BOOTSTRAP.replace("mapData(deg,0,DEGMAX,", f"mapData(deg,0,{degmax},")
    script = "<script>" + consts + bootstrap + "</script>"
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
