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

  // resolve the operators namespace (subgraph lives at graphologyLibrary.operators.subgraph
  // in the vendored bundle, NOT at the top level). Defensive so an asset swap is caught.
  var OPS = window.graphologyLibrary && (window.graphologyLibrary.operators || window.graphologyLibrary);
  // capture pristine hide-panel markup ONCE (before setupHidePanel populates it) so
  // drill-down children re-emit fresh panel markup, never the live/checked state.
  var PRISTINE_HP = (function(){ var e = document.getElementById('hp'); return e ? e.outerHTML : ''; })();

  // ---- build the graphology graph from baked DATA ----
  var graph = GG.MultiDirectedGraph ? new GG.MultiDirectedGraph()
    : new (GG.Graph || GG)({ type:'directed', multi:true });
  DATA.nodes.forEach(function(n){ try{ graph.addNode(n.key, Object.assign({}, n.attrs)); }catch(_){} });
  DATA.edges.forEach(function(e){ try{ graph.addEdgeWithKey(e.key, e.source, e.target, Object.assign({}, e.attrs)); }catch(_){} });

  // ---- config-default-hidden bake (sorted list; [] when no config) ----
  var HIDDEN = (typeof HIDDEN !== 'undefined' && HIDDEN) ? HIDDEN : [];

  // ---- N-band macro placement (refactored): constrain each node's x into its band's
  // disjoint window so bands read left->right WITHOUT overlap (forceAtlas2 still does the
  // organic layout WITHIN each band; y untouched). partitionBandsOn runs over EITHER the
  // master graph (boot) OR an induced subgraph (Apply). NBANDS<=1 => no-op == today.
  var NBANDS = (typeof BANDS !== 'undefined' && BANDS) ? BANDS.length : 1;
  function partitionBandsOn(eachNode, getAttr, setX){
    if(NBANDS <= 1) return;
    var W = 1000;                       // band-width constant (only scales the in-browser x-window)
    var GAP = 0.22 * W, P = W + GAP;    // pitch between band left-edges
    var lo = [], hi = [];
    for(var b=0; b<NBANDS; b++){ lo[b] = Infinity; hi[b] = -Infinity; }
    eachNode(function(k,a){
      var aa = a || getAttr(k);
      var b = (aa.band|0); if(b<0 || b>=NBANDS) return;
      var x = aa.x; if(typeof x !== 'number' || !isFinite(x)) return;
      if(x < lo[b]) lo[b] = x; if(x > hi[b]) hi[b] = x;
    });
    eachNode(function(k,a){
      var aa = a || getAttr(k);
      var b = (aa.band|0); if(b<0 || b>=NBANDS) return;
      var X0 = b * P;
      var span = hi[b] - lo[b];
      var nx;
      // Guard the DIVISOR (covers single-member AND zero-span multi-member bands,
      // incl. the offline-no-fa2 path where every node sits on its seed column):
      if(!(span > 1e-9)){ nx = X0 + W/2; }
      else { nx = X0 + (aa.x - lo[b]) / span * W; }
      setX(k, nx);                          // same write pattern as the drag handler
    });
  }
  function partitionBands(){               // thin wrapper over the master graph (boot)
    partitionBandsOn(graph.forEachNode.bind(graph),
      function(k){ return graph.getNodeAttributes(k); },
      function(k,nx){ graph.setNodeAttribute(k,'x',nx); });
  }

  // ---- FA2 settings factory (shared by boot + Apply relayout) ----
  function fa2Settings(gr){
    return Object.assign(FA2.inferSettings(gr),
      { barnesHutOptimize: gr.order>400, adjustSizes:true, gravity:1, scalingRatio:8, slowDown:2 });
  }
  // Re-seed a graph's x/y from a deterministic circle + band-bias formula keyed on the
  // VISIBLE set (NOT the previous positions) so each Apply is deterministic per visible-SET
  // and does not accumulate FA2 drift across repeated Applies. Also rescues re-enabled /
  // isolated nodes stuck at a stale faraway seed.
  function reseed(gr){
    var ks = []; gr.forEachNode(function(k){ ks.push(k); }); ks.sort();
    var n = ks.length || 1;
    for(var i=0;i<ks.length;i++){
      var a = gr.getNodeAttributes(ks[i]);
      var ang = 2*Math.PI*i/n;
      var x = Math.cos(ang)*120, y = Math.sin(ang)*120;
      var b = (a.band|0);
      if(NBANDS>1 && b>=0 && b<NBANDS){ x += (b-(NBANDS-1)/2)*520; }
      gr.setNodeAttribute(ks[i],'x',x); gr.setNodeAttribute(ks[i],'y',y);
    }
  }
  // Lay out + band-partition a graph from a fresh seed (used by boot subgraph + Apply).
  function layoutGraph(gr){
    if(FA2 && gr.order >= 2 && gr.size > 0){ try {
      FA2.assign(gr, { iterations: ITER, settings: fa2Settings(gr) });
    } catch(_){} }
    partitionBandsOn(gr.forEachNode.bind(gr),
      function(k){ return gr.getNodeAttributes(k); },
      function(k,nx){ gr.setNodeAttribute(k,'x',nx); });
  }

  // ---- LAYERED hidden sets + a DERIVED effective set the reducers/boxes read ----
  const ST = { q:'', view:(HAS_ZONES?INITVIEW:'both'), hits:null,
    hiddenView:new Set(),      // owned EXCLUSIVELY by applyView (the view-toggle logic)
    hiddenCfg:new Set(),       // seeded ONCE at boot from the baked HIDDEN const
    hiddenManual:new Set(),    // keys committed via the panel [Apply]
    unhidden:new Set(),        // keys the user explicitly re-enabled (lets a cfg/manual node return)
    preview:new Set(),         // PENDING, not-yet-applied checkbox toggles (translucent only)
    hidden:new Set(),          // DERIVED union — what reducers/boxes read
    hiddenE:new Set() };       // edge-hide for the view toggle
  for(var hi=0; hi<HIDDEN.length; hi++){ if(graph.hasNode(HIDDEN[hi])) ST.hiddenCfg.add(HIDDEN[hi]); }
  function recomputeHidden(){
    var nx = new Set();
    graph.forEachNode(function(k){
      if(ST.hiddenView.has(k)
        || (ST.hiddenCfg.has(k) && !ST.unhidden.has(k))
        || (ST.hiddenManual.has(k) && !ST.unhidden.has(k))) nx.add(k);
    });
    ST.hidden = nx;
  }
  recomputeHidden();

  function nodeReducer(key, attr){
    const r = Object.assign({}, attr);
    if(ST.hidden.has(key)){ r.hidden = true; return r; }          // effective-hidden: gone
    // pending HIDE of a currently-visible node => translucent grey preview (no relayout).
    // (A pending RE-ENABLE of an effective-hidden node returned above, so it is shown only
    // in the panel row + delta counter, never as a green canvas ghost.)
    if(ST.preview && ST.preview.has(key)){ r.color='rgba(120,120,140,0.28)'; r.label=''; r.zIndex=0; return r; }
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
    if(ST.preview && (ST.preview.has(ex[0]) || ST.preview.has(ex[1]))){ r.color='#eceef3'; r.zIndex=0; }  // dim preview-incident
    if(attr.bridge){ r.color = '#e15759'; r.size = Math.max(r.size||1, 3); r.zIndex = 3; }  // bridge: red+thick
    if(ST.q){ const both = ST.hits && ST.hits.has(ex[0]) && ST.hits.has(ex[1]); if(!both) r.color = '#eef0f4'; }
    return r;
  }

  // ---- BOOT layout: exclude cfg-hidden nodes from FA2 participation so the camera
  // auto-fits the VISIBLE subgraph. Empty cfg => the EXACT v0.6.0 code path (full-graph
  // FA2 + partitionBands), so unconfigured runtime layout is unchanged. ----
  if(ST.hiddenCfg.size === 0 || !OPS || !OPS.subgraph){
    if(FA2){ try { FA2.assign(graph, { iterations: ITER, settings: fa2Settings(graph) }); } catch(_){} }
    partitionBands();
  } else {
    var bootVis = [];
    graph.forEachNode(function(k){ if(!ST.hidden.has(k)) bootVis.push(k); });
    var bootSub = null; try { bootSub = OPS.subgraph(graph, bootVis); } catch(_){}
    if(bootSub){
      layoutGraph(bootSub);
      bootSub.forEachNode(function(k,a){ graph.setNodeAttribute(k,'x',a.x); graph.setNodeAttribute(k,'y',a.y); });
    } else {
      if(FA2){ try { FA2.assign(graph, { iterations: ITER, settings: fa2Settings(graph) }); } catch(_){} }
      partitionBands();
    }
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
  renderer.on('clickNode', function(e){
    showInfo(graph.getNodeAttributes(e.node));
    locateRow(e.node);                              // graph -> list: scroll+flash the row
  });
  renderer.on('clickStage', function(){ info.style.display='none'; });

  // ---- frame the camera on the currently-VISIBLE nodes via a custom bbox. The sigma
  // nodeExtent/normalization span the FULL graph (hidden nodes still distort it), so an
  // explicit setCustomBBox over visible-only x/y is required for a tight fit. ----
  function fitVisible(animate){
    var minx=Infinity,maxx=-Infinity,miny=Infinity,maxy=-Infinity,seen=false;
    graph.forEachNode(function(k,a){
      if(ST.hidden.has(k)) return;
      if(typeof a.x!=='number'||typeof a.y!=='number') return;
      if(a.x<minx)minx=a.x; if(a.x>maxx)maxx=a.x; if(a.y<miny)miny=a.y; if(a.y>maxy)maxy=a.y; seen=true;
    });
    if(!seen){ try{ renderer.getCamera().animatedReset({duration:300}); }catch(_){} return; }
    try{ renderer.setCustomBBox({ x:[minx,maxx], y:[miny,maxy] }); }catch(_){}
    renderer.refresh();
    try{ renderer.getCamera().animatedReset({duration: animate?300:0}); }catch(_){}
  }
  if(ST.hiddenCfg.size > 0) fitVisible(false);      // tight initial frame on the visible subgraph

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
    // rebuild ONLY ST.hiddenView (+ ST.hiddenE); NEVER clobber cfg/manual hides. The
    // derived ST.hidden is then recomputed as the union — that is the backward-compat seam.
    ST.view = v; ST.hiddenView = new Set(); ST.hiddenE = new Set();
    if(HAS_ZONES && v !== 'both'){
      if(v === 'internal'){
        graph.forEachNode(function(k,a){ if(a.zone==='dependency') ST.hiddenView.add(k); });
      } else if(v === 'dependency'){
        // hide pure target->target edges
        graph.forEachEdge(function(k,a,s,t,sa,ta){ if(sa.zone==='target' && ta.zone==='target') ST.hiddenE.add(k); });
        // hide target nodes with NO crossing (target->dependency) edge — off the boundary
        graph.forEachNode(function(k,a){
          if(a.zone!=='target') return;
          let cross=false;
          graph.forEachEdge(k, function(ek,ea,s,t,sa,ta){ if(sa.zone==='target' && ta.zone==='dependency') cross=true; });
          if(!cross) ST.hiddenView.add(k);
        });
      }
    }
    recomputeHidden(); renderer.refresh(); updateCounts();
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
    // child HIDDEN = parent HIDDEN intersected with the sub-slice keys (cfg-hide affects
    // child RENDERING only, never chain reachability). Rides INSIDE the same bare consts
    // <script> as DATA so the offline bare-tag count + libTexts() `const `-skip both hold.
    var subKeys = {}; for(var si=0; si<sub.nodes.length; si++){ subKeys[sub.nodes[si].key] = true; }
    var childHidden = []; for(var hj=0; hj<HIDDEN.length; hj++){ if(subKeys[HIDDEN[hj]]) childHidden.push(HIDDEN[hj]); }
    var consts = 'const DATA=' + JSON.stringify(sub) + ';\\n'
      + 'const HAS_ZONES=' + (typeof HAS_ZONES!=='undefined' ? JSON.stringify(HAS_ZONES) : 'false') + ';\\n'
      + 'const ITER=' + (typeof ITER!=='undefined' ? ITER : 90) + ';\\n'
      + 'const INITVIEW=' + JSON.stringify(typeof INITVIEW!=='undefined'?INITVIEW:'both') + ';\\n'
      + 'const BANDS=' + JSON.stringify(typeof BANDS!=='undefined'?BANDS:[]) + ';\\n'
      + 'const HIDDEN=' + JSON.stringify(childHidden) + ';\\n';
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
      + PRISTINE_HP                              // fresh, pristine hide panel (no live state)
      + libTags
      + '<script>' + consts + '<\\/script>'
      + '<script id="ms-boot">' + bootText() + '<\\/script>'
      + '</body></html>';
    return html;
  }
  function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // ============================ HIDE PANEL ============================
  // Live counts: 'shown X / hidden Y / total N' (total immutable = DATA.nodes.length).
  var countsEl = document.getElementById('ms-counts');
  function updateCounts(){
    if(!countsEl) return;
    var total = DATA.nodes.length, y = ST.hidden.size, x = total - y;
    countsEl.textContent = '\\u00b7 shown ' + x + ' / hidden ' + y + ' / total ' + total;
  }

  // bandLabel(b): the framed-box label for a band index (or '').
  function bandLabel(b){
    if(typeof b !== 'number') return '';
    return (typeof BANDS!=='undefined' && BANDS[b] && BANDS[b].label) ? BANDS[b].label : ('band '+b);
  }
  // The list-row model, built deterministically from DATA.nodes[i].attrs.
  var HP_ROWS = DATA.nodes.map(function(n){
    var a = n.attrs || {};
    return { key:n.key, label:a.label||n.key, kind:a.kind||'', zone:(a.zone||''),
             band:(typeof a.band==='number'?a.band:null), fan_in:(a.fan_in||0) };
  });
  var hpSortK = 'fan_in', hpSortAsc = false;     // default: fan_in DESC
  var hpRowEl = {};                              // key -> row DOM element

  // effective-hidden membership (mirrors recomputeHidden, for row classes WITHOUT a recompute).
  function effHidden(k){
    return ST.hiddenView.has(k)
      || (ST.hiddenCfg.has(k) && !ST.unhidden.has(k))
      || (ST.hiddenManual.has(k) && !ST.unhidden.has(k));
  }
  function hpFilter(){
    var q = (document.getElementById('hpq').value||'').toLowerCase();
    var fk = document.getElementById('hp-kind').value;
    var fzEl = document.getElementById('hp-zone'); var fz = fzEl ? fzEl.value : '';
    var fbEl = document.getElementById('hp-band'); var fb = fbEl ? fbEl.value : '';
    var fmin = parseInt(document.getElementById('hp-fmin').value||'0', 10) || 0;
    return HP_ROWS.filter(function(r){
      if(q && r.label.toLowerCase().indexOf(q) < 0) return false;
      if(fk && r.kind !== fk) return false;
      if(fz && r.zone !== fz) return false;
      if(fb !== '' && String(r.band) !== fb) return false;
      if(r.fan_in < fmin) return false;
      return true;
    });
  }
  function hpSorted(rows){
    var asc = hpSortAsc, k = hpSortK;
    return rows.slice().sort(function(a,b){
      var av=a[k], bv=b[k];
      if(k==='label'||k==='zone'){ av=String(av); bv=String(bv); }
      if(av<bv) return asc?-1:1; if(av>bv) return asc?1:-1;
      if(a.label<b.label) return -1; if(a.label>b.label) return 1;   // tie-break label ASC
      return a.key<b.key?-1:(a.key>b.key?1:0);                       // then key ASC
    });
  }
  function renderRows(){
    var list = document.getElementById('hp-list'); list.innerHTML=''; hpRowEl = {};
    var rows = hpSorted(hpFilter());
    for(var i=0;i<rows.length;i++){
      var r = rows[i];
      var row = document.createElement('div'); row.className='hp-row'; row.setAttribute('data-key', r.key);
      var eff = effHidden(r.key), prev = ST.preview.has(r.key);
      if(eff && !prev) row.classList.add('committed');
      if(prev && !eff) row.classList.add('previewed');               // pending HIDE
      if(prev && eff) row.classList.add('willreturn');               // pending RE-ENABLE
      var cb = document.createElement('input'); cb.type='checkbox'; cb.checked = prev;
      cb.addEventListener('change', (function(key,box){ return function(){ togglePreview(key, box.checked); }; })(r.key, cb));
      var lbl = document.createElement('div'); lbl.className='hp-lbl';
      lbl.textContent = r.label; lbl.title = r.label;
      var sub = document.createElement('div'); sub.className='hp-sub'; sub.textContent = r.kind;
      lbl.appendChild(sub);
      var fan = document.createElement('div'); fan.textContent = r.fan_in;
      var zb = document.createElement('div'); zb.className='hp-sub';
      zb.textContent = r.band!==null ? bandLabel(r.band) : r.zone;
      row.appendChild(cb); row.appendChild(lbl); row.appendChild(fan); row.appendChild(zb);
      row.addEventListener('click', (function(key){ return function(ev){
        if(ev.target && ev.target.type==='checkbox') return;          // checkbox handled separately
        locateNode(key);
      }; })(r.key));
      list.appendChild(row); hpRowEl[r.key] = row;
    }
    var foot = document.getElementById('hp-foot');
    if(foot) foot.textContent = rows.length + ' shown of ' + HP_ROWS.length + ' symbols';
  }

  // STAGE 1 — checkbox = INSTANT preview (no relayout).
  function togglePreview(key, on){
    if(on) ST.preview.add(key); else ST.preview.delete(key);
    refreshDeltaHint(); renderer.refresh(); renderRows();
  }
  // delta hint: pure set-diff vs effective-hidden (no layout).
  function refreshDeltaHint(){
    var hideN=0, restoreM=0;
    ST.preview.forEach(function(k){ if(ST.hidden.has(k)) restoreM++; else hideN++; });
    var el = document.getElementById('hp-delta');
    if(el) el.textContent = 'Apply: hide ' + hideN + ', restore ' + restoreM;
  }

  // STAGE 2 — [Apply] commits preview into manual/unhidden, then relayouts the visible
  // induced subgraph (FA2 + band partition, RUNTIME ONLY — zero emitted-byte impact).
  function applyPanel(){
    ST.preview.forEach(function(k){
      if(ST.hidden.has(k)){ ST.unhidden.add(k); ST.hiddenManual.delete(k); }   // RE-ENABLE
      else { ST.hiddenManual.add(k); ST.unhidden.delete(k); }                  // HIDE
    });
    ST.preview.clear(); refreshDeltaHint(); recomputeHidden();
    var visKeys=[]; graph.forEachNode(function(k){ if(!ST.hidden.has(k)) visKeys.push(k); });
    var sub = (OPS && OPS.subgraph) ? (function(){ try{ return OPS.subgraph(graph, visKeys); }catch(_){ return null; } })() : null;
    if(sub){
      reseed(sub);                                  // fresh deterministic seed (no cross-Apply drift)
      // FA2.assign has NO node-filter in this build, so the induced subgraph copy is what
      // excludes hidden nodes from the force sim; guard trivial (edgeless / <2 node) subs.
      if(FA2 && sub.order >= 2 && sub.size > 0){ try { FA2.assign(sub, { iterations: ITER, settings: fa2Settings(sub) }); } catch(_){} }
      partitionBandsOn(sub.forEachNode.bind(sub),
        function(k){ return sub.getNodeAttributes(k); },
        function(k,nx){ sub.setNodeAttribute(k,'x',nx); });
      sub.forEachNode(function(k,a){ graph.setNodeAttribute(k,'x',a.x); graph.setNodeAttribute(k,'y',a.y); });
    }
    renderer.refresh(); updateCounts(); renderRows();   // camera preserved; 'fit' reframes on demand
  }

  // list -> graph: animate the camera to the node's LIVE display coords (already in the
  // camera's normalized framed space — do NOT run through graphToViewport) + transient flash.
  function locateNode(key){
    if(!graph.hasNode(key)) return;
    var hp = document.getElementById('hp'); if(hp && hp.classList.contains('collapsed')){ /* keep */ }
    var d = renderer.getNodeDisplayData(key);
    if(d){ try{ renderer.getCamera().animate({ x:d.x, y:d.y, ratio:0.45 }, { duration:400 }); }catch(_){} }
    try{ graph.setNodeAttribute(key,'highlighted',true); renderer.refresh();
      setTimeout(function(){ if(graph.hasNode(key)) graph.removeNodeAttribute(key,'highlighted'); renderer.refresh(); }, 900);
    }catch(_){}
  }
  // graph -> list: scroll the matching row into view + flash it (~1.2s). No-op if filtered out.
  function locateRow(key){
    var row = hpRowEl[key]; if(!row) return;
    try{ row.scrollIntoView({block:'center'}); }catch(_){ row.scrollIntoView(); }
    row.classList.add('flash'); setTimeout(function(){ row.classList.remove('flash'); }, 1200);
  }

  // EXPORT (offline, 3 redundant sinks): wrapped {view_hide:{version:1,names:[...]}}.
  function exportHidden(){
    var set = {};
    DATA.nodes.forEach(function(n){
      if(ST.hidden.has(n.key)){
        // use the UNSUFFIXED name (strip the baked frontier '  +N\\u2933' marker) so the
        // exported names re-match scan._default_hidden_keys' raw-label/segment logic.
        var lbl = (n.attrs && n.attrs.label) || n.key;
        var cut = lbl.indexOf('  +'); if(cut >= 0) lbl = lbl.slice(0, cut);
        set[lbl] = true;
      }
    });
    var names = Object.keys(set).sort();
    var json = JSON.stringify({ view_hide: { version:1, names:names } }, null, 2);
    var ta = document.getElementById('hp-export-ta'); if(ta) ta.value = json;       // (a) textarea (file:// safe)
    try{ if(navigator.clipboard) navigator.clipboard.writeText(json); }catch(_){}   // (b) clipboard (best-effort)
    try{ var a=document.createElement('a');                                          // (c) Blob download
      a.href=URL.createObjectURL(new Blob([json],{type:'application/json'}));
      a.download='manyread.view_hide.json'; a.click(); }catch(_){}
  }

  function setupHidePanel(){
    var hp = document.getElementById('hp'); if(!hp) return;
    // build value-gated facets from values actually present (kind always; zone gated on
    // HAS_ZONES; band gated on BANDS.length>=2 — flat/plain graphs show only kind+fan_in).
    var kinds={}, zones={}, bands={};
    HP_ROWS.forEach(function(r){ if(r.kind) kinds[r.kind]=1; if(r.zone) zones[r.zone]=1;
      if(r.band!==null) bands[r.band]=1; });
    function fill(sel, vals, lab){ Object.keys(vals).sort().forEach(function(v){
      var o=document.createElement('option'); o.value=String(v);
      o.textContent = lab ? lab(v) : v; sel.appendChild(o); }); }
    fill(document.getElementById('hp-kind'), kinds);
    var zoneSel = document.getElementById('hp-zone');
    if(typeof HAS_ZONES!=='undefined' && HAS_ZONES){ fill(zoneSel, zones); }
    else if(zoneSel && zoneSel.parentNode){ zoneSel.parentNode.removeChild(zoneSel); }
    var bandSel = document.getElementById('hp-band');
    if(typeof BANDS!=='undefined' && BANDS.length >= 2){ fill(bandSel, bands, bandLabel); }
    else if(bandSel && bandSel.parentNode){ bandSel.parentNode.removeChild(bandSel); }

    document.getElementById('hp-tab').addEventListener('click', function(){ hp.classList.toggle('collapsed'); });
    ['hpq','hp-kind','hp-zone','hp-band','hp-fmin'].forEach(function(id){
      var el=document.getElementById(id); if(el) el.addEventListener('input', renderRows);
    });
    var cols = hp.querySelectorAll('.hp-cols .sortable');
    for(var i=0;i<cols.length;i++){ cols[i].addEventListener('click', function(){
      var k=this.getAttribute('data-k');
      if(k===hpSortK){ hpSortAsc=!hpSortAsc; } else { hpSortK=k; hpSortAsc=(k==='label'||k==='zone'); }
      for(var j=0;j<cols.length;j++) cols[j].classList.remove('active');
      this.classList.add('active'); renderRows();
    }); }
    // BULK actions — each ONE coalesced refresh, never relayout.
    document.getElementById('hp-selmatch').addEventListener('click', function(){
      hpFilter().forEach(function(r){ ST.preview.add(r.key); }); refreshDeltaHint(); renderer.refresh(); renderRows(); });
    document.getElementById('hp-selfan').addEventListener('click', function(){
      var fmin = parseInt(document.getElementById('hp-fmin').value||'0',10)||0;
      HP_ROWS.forEach(function(r){ if(r.fan_in>=fmin) ST.preview.add(r.key); });
      refreshDeltaHint(); renderer.refresh(); renderRows(); });
    document.getElementById('hp-clear').addEventListener('click', function(){
      ST.preview.clear(); refreshDeltaHint(); renderer.refresh(); renderRows(); });
    document.getElementById('hp-apply').addEventListener('click', applyPanel);
    document.getElementById('hp-fit').addEventListener('click', function(){ fitVisible(true); });
    document.getElementById('hp-export').addEventListener('click', exportHidden);
    renderRows(); refreshDeltaHint(); updateCounts();
  }
  setupHidePanel();
  updateCounts();

  renderer.on('doubleClickNode', function(e){
    e.preventSigmaDefault();                       // MUST stay synchronous-first (suppresses sigma's zoom)
    var html = buildChild(e.node);
    var url = URL.createObjectURL(new Blob([html], {type:'text/html'}));
    window.open(url, '_blank');                    // do NOT revoke (races the child load)
  });
})();
"""


# The hide panel (#hp) — STATIC markup emitted on EVERY graph (additive, byte-stable;
# rows are filled at runtime by setupHidePanel from DATA). Facets are value-gated by JS
# (zone removed when !HAS_ZONES; band removed when BANDS.length<2), so flat/plain graphs
# show only kind+fan_in. Starts collapsed. Captured once at boot as pristine markup so
# drill-down children re-emit it without carrying live preview/checkbox state.
_HIDE_PANEL_HTML = (
    "<div id='hp' class='collapsed'>"
    "<div class='hp-tab' id='hp-tab'>HIDE</div>"
    "<div class='hp-hd'>"
    "<input id='hpq' placeholder='filter symbols...'>"
    "<select id='hp-kind'><option value=''>kind: any</option></select>"
    "<select id='hp-zone'><option value=''>zone: any</option></select>"
    "<select id='hp-band'><option value=''>band: any</option></select>"
    "<span>fan_in&ge;<input id='hp-fmin' class='hp-num' type='number' min='0' value='0'></span>"
    "</div>"
    "<div class='hp-act'>"
    "<button id='hp-selmatch'>select matching</button>"
    "<button id='hp-selfan'>select fan_in&ge;X</button>"
    "<button id='hp-clear'>clear preview</button>"
    "<span id='hp-delta' class='hp-delta'></span>"
    "<button id='hp-apply' class='primary'>Apply</button>"
    "<button id='hp-fit'>fit</button>"
    "<button id='hp-export'>Export</button>"
    "</div>"
    "<div class='hp-cols'>"
    "<span></span><span class='sortable' data-k='label'>symbol</span>"
    "<span class='sortable active' data-k='fan_in'>fan_in</span>"
    "<span class='sortable' data-k='zone'>zone/band</span>"
    "</div>"
    "<div class='hp-list' id='hp-list'></div>"
    "<textarea id='hp-export-ta' readonly placeholder='exported view_hide JSON appears here'></textarea>"
    "<div class='hp-foot' id='hp-foot'></div>"
    "</div>"
)


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
            band_of: dict | None = None, bands_meta: list | None = None,
            default_hidden: list[str] | None = None) -> str:
    """Render a Graph as ONE self-contained interactive HTML file (sigma.js / WebGL).

    sigma + graphology + graphology-library (forceAtlas2) are inlined from the vendored
    UMD bundles as plain ``<script>`` globals (graphology core → graphology-library →
    sigma), with a per-file CDN ``<script src>`` fallback only when an asset is missing.
    The graph data + baked seed positions ride in a single bare consts ``<script>``, so
    the output is a single OFFLINE file that renders a GPU-accelerated, zoomable,
    searchable graph in any browser. Node positions are computed in-browser (forceAtlas2
    from a deterministic baked seed), so the emitted bytes stay byte-identical across runs.

    ``default_hidden`` (optional): a list of node ids that start APPLIED-hidden (out of
    the boot layout) but stay LISTED in the hide panel + re-enableable. The renderer
    bakes the SORTED list into a gated ``const HIDDEN=`` line; when ``None`` the line is
    OMITTED entirely, so an unconfigured render is byte-identical to v0.6.0.
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
        # --- hide panel (#hp): collapsible right-side panel, sibling of #cy ---
        "#hp{position:fixed;top:44px;right:0;bottom:0;width:340px;z-index:15;display:flex;"
        "flex-direction:column;background:#222838;color:#dfe3ea;box-shadow:-2px 0 8px rgba(0,0,0,.3);"
        "transition:transform .18s ease;font-size:12px}"
        "#hp.collapsed{transform:translateX(322px)}"
        ".hp-tab{position:absolute;left:-0px;top:0;width:18px;height:64px;background:#39415a;"
        "color:#cdd6e0;writing-mode:vertical-rl;text-align:center;font-weight:600;cursor:pointer;"
        "border-radius:4px 0 0 4px;padding:6px 1px;user-select:none}"
        "#hp .hp-hd{display:flex;flex-wrap:wrap;gap:4px;padding:8px 8px 4px 24px}"
        "#hp .hp-hd input,#hp .hp-hd select{background:#2b3142;color:#eee;border:1px solid #556;"
        "border-radius:4px;padding:2px 4px;font-size:11px}"
        "#hp #hpq{flex:1 1 100%}.hp-num{width:56px}"
        "#hp .hp-act{display:flex;flex-wrap:wrap;gap:4px;padding:2px 8px 4px 24px;align-items:center}"
        "#hp .hp-act button{background:#39415a;color:#dfe3ea;border:1px solid #556;border-radius:4px;"
        "padding:2px 6px;font-size:11px;cursor:pointer}"
        "#hp .hp-act button.primary{background:#2f7d46;border-color:#3a9457}"
        ".hp-delta{color:#ffcf5c;flex:1 1 100%;font-size:11px}"
        ".hp-cols{display:grid;grid-template-columns:20px 1fr 64px 52px;gap:2px;padding:2px 8px 2px 24px;"
        "color:#9aa6b2;border-bottom:1px solid #39415a}"
        ".hp-cols .sortable{cursor:pointer;user-select:none}.hp-cols .sortable.active{color:#ffcf5c}"
        ".hp-list{flex:1;overflow:auto;padding:0 8px 0 24px}"
        ".hp-row{display:grid;grid-template-columns:20px 1fr 64px 52px;gap:2px;align-items:center;"
        "padding:2px 0;border-bottom:1px solid #2b3142;cursor:pointer}"
        ".hp-row .hp-lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".hp-row .hp-sub{color:#9aa6b2;font-size:10px}"
        ".hp-row.previewed{opacity:.55}.hp-row.committed{text-decoration:line-through;color:#9aa6b2}"
        ".hp-row.willreturn{color:#8ad18a}.hp-row.flash{outline:2px solid #ffcf5c;outline-offset:-2px}"
        "#hp-export-ta{margin:4px 8px 4px 24px;max-height:120px;background:#1b2030;color:#a6e3a1;"
        "border:1px solid #556;border-radius:4px;font:11px ui-monospace,Consolas,monospace}"
        ".hp-foot{padding:2px 8px 6px 24px;color:#9aa6b2;font-size:10px}"
        "</style></head><body><div id='bar'>"
        f"<b>{_html_escape(title)}</b><span class='meta'>{meta} &middot; color={legend_kind} &middot; tap node → path</span>"
        + "<span id='ms-counts' class='meta'></span>"
        + (f"<span class='warn'>&#9888; {_html_escape(banner)}</span>" if banner else "")
        + "<input id='q' placeholder='search node/path...'>"
        + view_ctl
        + f"<span style='display:flex;gap:8px;flex-wrap:wrap'>{legend}</span>"
        + "</div><div id='cy'></div><div id='info'></div>"
        + _HIDE_PANEL_HTML
    )
    consts = (
        f"const DATA={data_json};\n"
        f"const HAS_ZONES={'true' if zoned else 'false'};\n"
        f"const ITER={iters};\n"
        f"const INITVIEW={json.dumps(view)};\n"
        f"const BANDS={json.dumps(bands_meta or [], ensure_ascii=False)};\n"
    )
    # GATED, SORTED default-hidden bake: omitted entirely when default_hidden is None
    # (v0.6.0 bytes preserved). Kept INSIDE the same bare consts <script> so the offline
    # bare-tag count (>=4) and the libTexts() `const `-prefix skip both still hold.
    if default_hidden is not None:
        consts += f"const HIDDEN={json.dumps(sorted(default_hidden))};\n"
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
