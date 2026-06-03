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
import math
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


# --- self-contained interactive HTML (sigma.js / WebGL + graphology force layout) ---
# A single file that opens in any browser — no server/node/build. Built to scale to
# MANY nodes (dependency-level bounded slices): sigma.js renders on the GPU (WebGL),
# so pan/zoom/drag stay smooth on hundreds–thousands of nodes where the old Canvas2D
# renderer choked. Layout is graphology-forceAtlas2 (fast, refined in-browser from a
# deterministic baked seed). Features kept: degree sizing, hub halo, bridge edges,
# search, target/dependency view toggle, tap→path, drag-node≠pan-canvas, honest
# truncation banner. Zones are encoded by COLOR + spatial clustering (sigma has no
# compound parents) instead of the old faint boxes; bridges are red+thick (sigma
# edges can't dash without an extra program).
#
# Offline: sigma + graphology + graphology-library (forceAtlas2) all ship a UMD
# build, inlined IN ORDER as plain <script> globals — graphology (core,
# window.graphology) → graphology-library (layouts incl. forceAtlas2,
# window.graphologyLibrary) → sigma (window.Sigma). Per-file CDN fallback when an
# asset is missing. The sigma UMD bundles its OWN ES5-consistent events polyfill,
# so there is no ESM/node-shim class-constructor skew (the reason we don't use the
# ESM bundles). Emitted bytes (static lib text + DATA + baked seed positions) stay
# deterministic; the force layout is computed in-browser, so nothing time/random is
# baked.
_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
_SIGMA_LIBS = [
    (_ASSET_DIR / "graphology.umd.min.js",
     "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"),
    (_ASSET_DIR / "graphology-library.min.js",
     "https://cdn.jsdelivr.net/npm/graphology-library@0.8.0/dist/graphology-library.min.js"),
    (_ASSET_DIR / "sigma.umd.min.js",
     "https://unpkg.com/sigma@2.4.0/build/sigma.min.js"),
]
_PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#9d7660"]

# Bootstrap is a plain (non-f) string: literal { } braces. Injected values arrive as
# the const block (DATA, HAS_ZONES, ITER, INITVIEW) — no token rewriting, so no
# mapData/DEGMAX fragility (node size is baked per-node in DATA). The sigma /
# graphology / forceAtlas2 globals come from the inlined UMD <script> tags above.
_HTML_BOOTSTRAP = """
(function(){
  // ---- resolve UMD globals (graphology core, graphology-library fa2, sigma) ----
  var GG = window.graphology;
  var SigmaCls = (typeof window.Sigma === 'function') ? window.Sigma
    : (window.Sigma && (window.Sigma.Sigma || window.Sigma.default));
  var FA2 = window.graphologyLibrary && window.graphologyLibrary.layoutForceAtlas2;
  if(!GG || !SigmaCls){ document.getElementById('cy').innerHTML =
    '<p style="padding:20px;color:#900">graph renderer failed to load</p>'; return; }

  // ---- build the graphology graph from baked DATA ----
  var graph = GG.MultiDirectedGraph ? new GG.MultiDirectedGraph()
    : new (GG.Graph || GG)({ type:'directed', multi:true });
  DATA.nodes.forEach(function(n){ try{ graph.addNode(n.key, Object.assign({}, n.attrs)); }catch(_){} });
  DATA.edges.forEach(function(e){ try{ graph.addEdgeWithKey(e.key, e.source, e.target, Object.assign({}, e.attrs)); }catch(_){} });

  // ---- layout: forceAtlas2 from the deterministic baked seed (positions in-browser) ----
  if(FA2){ try {
    var settings = Object.assign(FA2.inferSettings(graph),
      { barnesHutOptimize: graph.order>400, adjustSizes:true, gravity:1, scalingRatio:8, slowDown:2 });
    FA2.assign(graph, { iterations: ITER, settings: settings });
  } catch(_){} }

  // ---- N-band macro placement: constrain each node's x into its band's disjoint
  // window so the bands read left->right WITHOUT overlap (forceAtlas2 still did the
  // organic layout WITHIN each band; y is untouched). Run ONCE after FA2. No
  // random/time => deterministic given FA2 (FA2 itself is in-browser, so emitted
  // bytes are unaffected). NBANDS<=1 (flat / plain / empty BANDS) => no-op == today.
  var NBANDS = (typeof BANDS !== 'undefined' && BANDS) ? BANDS.length : 1;
  function partitionBands(){
    if(NBANDS <= 1) return;
    var W = 1000;                       // band-width constant (only scales the in-browser x-window)
    var GAP = 0.22 * W, P = W + GAP;    // pitch between band left-edges
    var lo = [], hi = [];
    for(var b=0; b<NBANDS; b++){ lo[b] = Infinity; hi[b] = -Infinity; }
    graph.forEachNode(function(k,a){
      var b = (a.band|0); if(b<0 || b>=NBANDS) return;
      var x = a.x; if(typeof x !== 'number' || !isFinite(x)) return;
      if(x < lo[b]) lo[b] = x; if(x > hi[b]) hi[b] = x;
    });
    graph.forEachNode(function(k,a){
      var b = (a.band|0); if(b<0 || b>=NBANDS) return;
      var X0 = b * P;
      var span = hi[b] - lo[b];
      var nx;
      // Guard the DIVISOR (covers single-member AND zero-span multi-member bands,
      // incl. the offline-no-fa2 path where every node sits on its seed column):
      if(!(span > 1e-9)){ nx = X0 + W/2; }
      else { nx = X0 + (a.x - lo[b]) / span * W; }
      graph.setNodeAttribute(k, 'x', nx);   // same write pattern as the drag handler
    });
  }
  partitionBands();

  // ---- interaction state + reducers ----
  const ST = { q:'', view:(HAS_ZONES?INITVIEW:'both'), hits:null, hidden:new Set(), hiddenE:new Set() };
  function nodeReducer(key, attr){
    const r = Object.assign({}, attr);
    if(ST.hidden.has(key)){ r.hidden = true; return r; }
    if(attr.hub){ r.highlighted = true; r.forceLabel = true; }     // hub halo via sigma highlight
    if(ST.q){
      if(ST.hits && ST.hits.has(key)){ r.highlighted = true; r.forceLabel = true; r.zIndex = 2; }
      else { r.label = ''; r.color = '#dfe3ea'; r.zIndex = 0; }    // dim non-hits
    }
    return r;
  }
  function edgeReducer(key, attr){
    const r = Object.assign({}, attr);
    const ex = graph.extremities(key);
    if(ST.hiddenE.has(key) || ST.hidden.has(ex[0]) || ST.hidden.has(ex[1])){ r.hidden = true; return r; }
    if(attr.bridge){ r.color = '#e15759'; r.size = Math.max(r.size||1, 3); r.zIndex = 3; }  // bridge: red+thick
    if(ST.q){ const both = ST.hits && ST.hits.has(ex[0]) && ST.hits.has(ex[1]); if(!both) r.color = '#eef0f4'; }
    return r;
  }

  const renderer = new SigmaCls(graph, document.getElementById('cy'), {
    defaultEdgeType:'line', renderEdgeLabels:false, zIndex:true,
    labelRenderedSizeThreshold: 7, labelDensity: 0.7, labelGridCellSize: 80,
    nodeReducer: nodeReducer, edgeReducer: edgeReducer,
    minCameraRatio: 0.05, maxCameraRatio: 12
  });

  // ---- tap a node to GET ITS FILE PATH (+ kind/cluster); tap blank closes ----
  const info = document.getElementById('info');
  function showInfo(d){
    info.innerHTML='';
    const b=document.createElement('b'); b.textContent=d.label||d.key||''; info.appendChild(b);
    const m=document.createElement('div'); m.className='k';
    m.textContent=(d.kind||'')+(d.cluster?('  \\u00b7  '+d.cluster):''); info.appendChild(m);
    const c=document.createElement('code'); c.textContent=d.path||''; info.appendChild(c);
    info.style.display='block';
  }
  renderer.on('clickNode', function(e){ showInfo(graph.getNodeAttributes(e.node)); });
  renderer.on('clickStage', function(){ info.style.display='none'; });

  // ---- search: dim non-matches, highlight hits (label + path) ----
  const q = document.getElementById('q');
  if(q){ q.addEventListener('input', function(){
    ST.q = q.value.trim().toLowerCase();
    ST.hits = null;
    if(ST.q){ ST.hits = new Set();
      graph.forEachNode(function(k,a){
        if(((a.label||'')+' '+(a.path||'')).toLowerCase().indexOf(ST.q) >= 0) ST.hits.add(k);
      });
    }
    renderer.refresh();
  }); }

  // ---- view toggle: internal | dependency | both (show/hide only; no relayout) ----
  const viewSel = document.getElementById('view');
  if(!HAS_ZONES && viewSel){ viewSel.parentNode.style.display = 'none'; }
  function applyView(v){
    ST.view = v; ST.hidden = new Set(); ST.hiddenE = new Set();
    if(HAS_ZONES && v !== 'both'){
      if(v === 'internal'){
        graph.forEachNode(function(k,a){ if(a.zone==='dependency') ST.hidden.add(k); });
      } else if(v === 'dependency'){
        // hide pure target->target edges
        graph.forEachEdge(function(k,a,s,t,sa,ta){ if(sa.zone==='target' && ta.zone==='target') ST.hiddenE.add(k); });
        // hide target nodes with NO crossing (target->dependency) edge — off the boundary
        graph.forEachNode(function(k,a){
          if(a.zone!=='target') return;
          let cross=false;
          graph.forEachEdge(k, function(ek,ea,s,t,sa,ta){ if(sa.zone==='target' && ta.zone==='dependency') cross=true; });
          if(!cross) ST.hidden.add(k);
        });
      }
    }
    renderer.refresh();
  }
  if(viewSel){ viewSel.addEventListener('change', function(){ applyView(viewSel.value); }); }
  if(HAS_ZONES) applyView(ST.view);

  // ---- drag a NODE (≠ pan): canvas drag still pans (sigma default) ----
  let dragged=null, dragging=false;
  renderer.on('downNode', function(e){ dragging=true; dragged=e.node;
    graph.setNodeAttribute(dragged,'highlighted',true); });
  renderer.getMouseCaptor().on('mousemovebody', function(e){
    if(!dragging) return;
    const p = renderer.viewportToGraph(e);
    graph.setNodeAttribute(dragged,'x',p.x); graph.setNodeAttribute(dragged,'y',p.y);
    e.preventSigmaDefault(); e.original.preventDefault(); e.original.stopPropagation();
  });
  renderer.getMouseCaptor().on('mouseup', function(){
    if(dragged) graph.removeNodeAttribute(dragged,'highlighted');
    dragging=false; dragged=null;
  });

  // ---- N-band background BOX layer: ordered framed rounded-rects drawn BEHIND the
  // nodes/edges on a dedicated canvas, recomputed on afterRender from the LIVE per-band
  // node bounding boxes (via graphToViewport) so they track pan/zoom/drag. Guarded on
  // NBANDS>1 => flat/plain pages install nothing (byte-identical RUNTIME to today).
  if(NBANDS > 1){
    var container = renderer.getContainer();
    var bcanvas = document.createElement('canvas');
    bcanvas.style.position = 'absolute';
    bcanvas.style.left = '0'; bcanvas.style.top = '0';
    bcanvas.style.pointerEvents = 'none';
    bcanvas.style.zIndex = '0';
    container.insertBefore(bcanvas, container.firstChild);  // first child => paints behind sigma layers
    var bctx = bcanvas.getContext('2d');
    // a SEPARATE faint band palette (never collides with node zone color)
    var BAND_FILL = ['rgba(78,121,167,0.06)','rgba(89,161,79,0.06)',
                     'rgba(242,142,43,0.06)','rgba(225,87,89,0.06)'];
    var BAND_LINE = ['rgba(78,121,167,0.55)','rgba(89,161,79,0.55)',
                     'rgba(242,142,43,0.55)','rgba(225,87,89,0.55)'];
    function sizeBox(){
      var dpr = window.devicePixelRatio || 1;
      var w = container.offsetWidth, h = container.offsetHeight;
      bcanvas.width = Math.max(1, Math.round(w*dpr));
      bcanvas.height = Math.max(1, Math.round(h*dpr));
      bcanvas.style.width = w + 'px'; bcanvas.style.height = h + 'px';
      bctx.setTransform(dpr,0,0,dpr,0,0);
    }
    function roundRect(c,x,y,w,h,r){
      r = Math.min(r, w/2, h/2); if(r<0) r=0;
      c.beginPath();
      c.moveTo(x+r,y); c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r);
      c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath();
    }
    function drawBands(){
      // clear the FULL canvas every frame (CSS px, since setTransform scales by dpr)
      bctx.clearRect(0, 0, container.offsetWidth, container.offsetHeight);
      var bb = [];
      for(var b=0; b<NBANDS; b++) bb[b] = null;
      // accumulate per-band viewport bbox over VISIBLE nodes (skip ST.hidden — the Set
      // the view-toggle reducer actually reads — so boxes follow the view toggle).
      graph.forEachNode(function(k,a){
        if(ST.hidden.has(k)) return;
        var b = (a.band|0); if(b<0 || b>=NBANDS) return;
        var p = renderer.graphToViewport({x:a.x, y:a.y});
        if(!p || !isFinite(p.x) || !isFinite(p.y)) return;
        var box = bb[b];
        if(!box){ bb[b] = {x0:p.x, y0:p.y, x1:p.x, y1:p.y}; }
        else { if(p.x<box.x0)box.x0=p.x; if(p.y<box.y0)box.y0=p.y;
               if(p.x>box.x1)box.x1=p.x; if(p.y>box.y1)box.y1=p.y; }
      });
      var pad = 26;
      for(var b=0; b<NBANDS; b++){
        var box = bb[b];
        if(!box) continue;              // empty (or fully-hidden) band => skip its box
        var x = box.x0 - pad, y = box.y0 - pad - 14;
        var w = (box.x1 - box.x0) + pad*2, h = (box.y1 - box.y0) + pad*2 + 14;
        roundRect(bctx, x, y, w, h, 10);
        bctx.fillStyle = BAND_FILL[b % BAND_FILL.length]; bctx.fill();
        bctx.lineWidth = 1.5; bctx.strokeStyle = BAND_LINE[b % BAND_LINE.length]; bctx.stroke();
        var lbl = (BANDS[b] && BANDS[b].label) ? BANDS[b].label : ('band '+b);
        bctx.font = '600 12px system-ui,Segoe UI,sans-serif';
        bctx.fillStyle = BAND_LINE[b % BAND_LINE.length].replace('0.55','0.95');
        bctx.fillText(lbl, x + 8, y + 14);
      }
    }
    sizeBox();
    renderer.on('afterRender', drawBands);
    renderer.on('resize', sizeBox);
  }

  // ---- DRILL-DOWN: double-click a node -> open a NEW TAB with that node's up+downstream
  // reachable chain (client-side BFS over the LOADED slice). The child reuses the SAME
  // inlined libs + the SAME bootstrap text, so it gets the full UI (search, view toggle,
  // hub/bridge, bands, AND recursive drill-down) bounded to the narrowed slice. Offline:
  // the Blob URL is runtime-only; the emitted file fetches nothing over the network.
  // HONEST LIMITATION (banner + docs): the in-browser chain only sees the currently
  // loaded slice — a deeper/fresh chain = re-run manyscan with that node as the seed.
  function chainKeys(root){
    var fwd = {}, back = {};
    DATA.edges.forEach(function(e){
      (fwd[e.source] = fwd[e.source] || []).push(e.target);
      (back[e.target] = back[e.target] || []).push(e.source);
    });
    var keep = {}; keep[root] = true;
    function bfs(adj){
      var q = [root], seen = {}; seen[root] = true;
      while(q.length){
        var cur = q.shift(); var ns = adj[cur] || [];
        for(var i=0;i<ns.length;i++){ var nx = ns[i];
          if(!seen[nx]){ seen[nx] = true; keep[nx] = true; q.push(nx); } }
      }
    }
    bfs(fwd); bfs(back);
    return keep;
  }
  function subData(root){
    var keep = chainKeys(root);
    return {
      nodes: DATA.nodes.filter(function(n){ return keep[n.key]; }),
      edges: DATA.edges.filter(function(e){ return keep[e.source] && keep[e.target]; })
    };
  }
  function libTexts(){
    var out = [], ss = document.querySelectorAll('script');
    for(var i=0;i<ss.length;i++){ var s = ss[i];
      if(s.id === 'ms-boot') continue;
      if(s.src) continue;
      var t = s.textContent || '';
      if(t.indexOf('const ') === 0) continue;   // skip the BARE consts tag
      out.push(t);
    }
    return out;   // document order: [graphology, graphology-library, sigma]
  }
  function bootText(){ var b = document.getElementById('ms-boot'); return b ? b.textContent : ''; }
  function buildChild(root){
    var sub = subData(root);
    var rootLabel = (graph.hasNode(root) ? (graph.getNodeAttribute(root,'label')||root) : root);
    var libs = libTexts();
    var libTags = '';
    for(var i=0;i<libs.length;i++){ libTags += '<script>' + libs[i] + '<\\/script>'; }
    var consts = 'const DATA=' + JSON.stringify(sub) + ';\\n'
      + 'const HAS_ZONES=' + (typeof HAS_ZONES!=='undefined' ? JSON.stringify(HAS_ZONES) : 'false') + ';\\n'
      + 'const ITER=' + (typeof ITER!=='undefined' ? ITER : 90) + ';\\n'
      + 'const INITVIEW=' + JSON.stringify(typeof INITVIEW!=='undefined'?INITVIEW:'both') + ';\\n'
      + 'const BANDS=' + JSON.stringify(typeof BANDS!=='undefined'?BANDS:[]) + ';\\n';
    var styleEl = document.querySelector('style');
    var barEl = document.getElementById('bar');
    var banner = '<div style="position:fixed;top:44px;left:0;right:0;z-index:9;'
      + 'background:#3a2f12;color:#ffcf5c;padding:4px 10px;font:12px system-ui,sans-serif">'
      + '\\u26a0 chain of ' + esc(rootLabel)
      + ' (this slice only \\u2014 re-run manyscan for a deeper chain)</div>';
    var html = '<!doctype html><html><head><meta charset=utf-8><title>chain: '
      + esc(rootLabel) + '</title>'
      + (styleEl ? styleEl.outerHTML : '')
      + '</head><body>'
      + (barEl ? barEl.outerHTML : '')
      + banner
      + '<div id="cy"></div><div id="info"></div>'
      + libTags
      + '<script>' + consts + '<\\/script>'
      + '<script id="ms-boot">' + bootText() + '<\\/script>'
      + '</body></html>';
    return html;
  }
  function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  renderer.on('doubleClickNode', function(e){
    e.preventSigmaDefault();                       // MUST stay synchronous-first (suppresses sigma's zoom)
    var html = buildChild(e.node);
    var url = URL.createObjectURL(new Blob([html], {type:'text/html'}));
    window.open(url, '_blank');                    // do NOT revoke (races the child load)
  });
})();
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


# zone identity + tints (shared by to_html). The dependency zone may hold MANY
# distinct dependency sources; zones are encoded by node COLOR + spatial clustering.
_ZONES = ("target", "dependency")
_ZONE_COLOR = {"target": "#4e79a7", "dependency": "#f28e2b"}


def _seed_xy(i: int, n_total: int, zone: str | None = None,
             band: int | None = None, n_bands: int = 1) -> tuple[float, float]:
    """Deterministic initial layout seed (circle by sorted index; band/zone x-bias so
    the bands/zones start apart). forceAtlas2 refines from here in-browser; rounding
    keeps the emitted bytes stable.

    When a ``band`` is given (n_bands > 1) the seed x is biased by band column (520
    pitch) so the bands start in left->right order — a COARSE fallback used only if
    forceAtlas2 is skipped (offline / no GPU); the intended geometry is the in-browser
    ``partitionBands`` remap (a different pitch, hence the two constants differ). With
    no band, the legacy two-zone bias is used.
    """
    ang = 2.0 * math.pi * i / max(1, n_total)
    x = math.cos(ang) * 120.0
    y = math.sin(ang) * 120.0
    if band is not None and n_bands > 1:
        x += (band - (n_bands - 1) / 2.0) * 520.0
    elif zone == "target":
        x -= 220.0
    elif zone == "dependency":
        x += 220.0
    return round(x, 3), round(y, 3)


def to_html(g: Graph, title: str = "manyscan dependency slice", view: str = "both",
            band_of: dict | None = None, bands_meta: list | None = None) -> str:
    """Render a Graph as ONE self-contained interactive HTML file (sigma.js / WebGL).

    sigma + graphology + forceAtlas2 are inlined from the vendored ESM bundles as
    base64 and loaded in-browser via blob URLs + dynamic ``import()`` (offline;
    per-module esm.sh fallback if an asset is missing), as is the graph data — so the
    output is a single file that renders a GPU-accelerated, zoomable, searchable graph
    in any browser. WebGL keeps pan/zoom/drag smooth where the old Canvas2D renderer
    lagged. Node positions are computed in-browser (forceAtlas2 from a deterministic
    baked seed), so the emitted bytes stay byte-identical across runs.
    """
    n_sorted = sorted(g.nodes.values(), key=lambda n: n.id)
    n_total = len(n_sorted)

    # Color by ZONE when this is a boundary graph (makes target↔dependency pop), else
    # by cohesive cluster (attrs['cluster']) when present, else by kind.
    zoned = any(n.attrs.get("zone") in _ZONES for n in g.nodes.values())
    clustered = any(n.attrs.get("cluster") for n in g.nodes.values())
    if zoned:
        keys = list(_ZONES)
        kcolor = dict(_ZONE_COLOR)
        legend_kind = "zone"
    elif clustered:
        keys = sorted({(n.attrs.get("cluster") or "?") for n in g.nodes.values()})
        kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}
        legend_kind = "cluster"
    else:
        keys = sorted({(n.kind or "node") for n in g.nodes.values()})
        kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}
        legend_kind = "kind"

    def _path_of(n) -> str:
        if n.evidence is not None and getattr(n.evidence, "path", None):
            return n.evidence.path
        return n.label or n.id

    # (2)(3) Importance: degree-sizing for ALL graphs + hub/bridge highlight markers.
    imp = _importance(g)
    degmax = max([1] + [v["deg"] for v in imp.values()])

    def _size(deg: int, hub: int) -> float:
        base = 4.0 + (deg / degmax) * 11.0          # 4 .. 15 by degree
        if hub:
            base = max(base, 12.0) + 4.0            # hubs bump up + get a halo (reducer)
        return round(base, 2)

    # --- nodes: {key, attrs} with baked position / size / color (graphology model) ---
    # n_bands counts EVERY band in bands_meta (incl. a possibly-empty dep-core band),
    # so seam geometry is stable across graphs with/without dep-core.
    n_bands = len(bands_meta) if bands_meta else 1
    nodes: list[dict] = []
    for i, n in enumerate(n_sorted):
        extra = g.frontier.get(n.id, 0)
        label = n.label or n.id
        if extra:
            label = f"{label}  +{extra}⤳"
        cluster = n.attrs.get("cluster") or ""
        zone = n.attrs.get("zone")
        ckey = zone if zoned else (cluster if clustered else (n.kind or "node"))
        ni = imp.get(n.id, {"deg": 0, "fan_in": 0, "hub": 0})
        nb = band_of.get(n.id, 0) if band_of is not None else None
        x, y = _seed_xy(i, n_total, zone if zoned else None, band=nb, n_bands=n_bands)
        attrs = {
            "label": label, "x": x, "y": y, "size": _size(ni["deg"], ni["hub"]),
            "color": kcolor.get(ckey, "#888"), "kind": n.kind or "node",
            "path": _path_of(n), "cluster": cluster, "frontier": extra,
            "deg": ni["deg"], "fan_in": ni["fan_in"], "hub": ni["hub"],
        }
        if zoned and zone in _ZONES:
            attrs["zone"] = zone
        # GATE on band_of is not None: plain/flat DATA bytes are untouched (byte-compat).
        if band_of is not None:
            attrs["band"] = band_of.get(n.id, 0)
        nodes.append({"key": n.id, "attrs": attrs})

    # --- edges: {key, source, target, attrs} ---
    edge_conf = getattr(g, "edge_confidence", {})
    bridge_keys = {(a, b, r) for a, b, r in analyze.bridges(g)}
    edges: list[dict] = []
    for i, e in enumerate(sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation))):
        ea = {"rel": e.relation, "size": 1, "color": "#c4c9d4"}
        conf = edge_conf.get(e.key())
        if conf:
            ea["conf"] = conf
            if conf == "ambiguous":
                ea["color"] = "#d98a8a"
            elif conf == "unresolved":
                ea["color"] = "#c3a3bd"
        if e.key() in bridge_keys:
            ea["bridge"] = 1                       # reducer paints red + thick
        if zoned:
            s, d = g.nodes.get(e.src), g.nodes.get(e.dst)
            if (s is not None and d is not None
                    and s.attrs.get("zone") == "target" and d.attrs.get("zone") == "dependency"):
                ea["cross"] = 1                    # target->dependency crossing (the seam)
                ea["size"] = 1.5
                ea["color"] = "#7f8a9c"
        edges.append({"key": f"e{i}", "source": e.src, "target": e.dst, "attrs": ea})

    data_json = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)

    banner = ""
    if g.truncated:
        banner = f"bounded: capped at level {g.frontier_depth}, {g.elided} deps elided"
    elif g.depth_bounded:
        banner = f"bounded: depth-capped at level {g.frontier_depth}"

    legend = "".join(
        f'<span class="lg"><i style="background:{kcolor[k]}"></i>{_html_escape(k)}</span>' for k in keys
    )
    meta = f"{len(g.nodes)} nodes &middot; {len(g.edges)} edges"

    # (5) one-page view toggle (only meaningful for zoned graphs; JS hides it otherwise).
    view = view if view in ("both", "internal", "dependency") else "both"
    view_opts = "".join(
        f"<option value='{v}'{' selected' if v == view else ''}>{v}</option>"
        for v in ("both", "internal", "dependency")
    )
    view_ctl = (
        "<span class='vc'>view <select id='view'>" + view_opts + "</select></span>"
        if zoned else ""
    )

    # forceAtlas2 iteration budget — fewer iterations for big graphs (layout is the
    # only in-browser CPU cost; WebGL handles the rendering). Deterministic per size.
    iters = 200 if n_total <= 200 else (90 if n_total <= 1200 else 45)

    # Inline the vendored UMD libs IN ORDER as plain <script> globals (graphology →
    # graphology-library → sigma); per-file CDN fallback (<script src>) when an asset
    # is missing. Deterministic: static file text + DATA.
    def _script_for(asset: Path, cdn: str) -> str:
        if asset.is_file():
            return "<script>" + asset.read_text(encoding="utf-8") + "</script>"
        return f'<script src="{cdn}"></script>'

    lib = "".join(_script_for(asset, cdn) for asset, cdn in _SIGMA_LIBS)

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
        f"const HAS_ZONES={'true' if zoned else 'false'};\n"
        f"const ITER={iters};\n"
        f"const INITVIEW={json.dumps(view)};\n"
        f"const BANDS={json.dumps(bands_meta or [], ensure_ascii=False)};\n"
    )
    # STRUCTURAL: the consts go in a BARE <script> (so the offline guard still counts 4
    # bare lib+consts tags AND the drill-down child can distinguish it by a `const `
    # prefix); the bootstrap gets id="ms-boot" so the child can retrieve it verbatim.
    consts_tag = "<script>" + consts + "</script>"
    boot_tag = '<script id="ms-boot">' + _HTML_BOOTSTRAP + "</script>"
    return head + lib + consts_tag + boot_tag + "</body></html>"


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
