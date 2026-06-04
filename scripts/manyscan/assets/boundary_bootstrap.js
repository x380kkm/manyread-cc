
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
  // `graph` (the MASTER) is the source-of-truth for reachability / drill-down / module
  // membership / hidden-ness and is NEVER mutated by the quotient. `displayed` is what
  // sigma renders. When the collapsible quotient is OFF (no MODULES baked) displayed IS
  // graph (same object) => zero behavioral/byte change vs v0.6.2.
  var graph = GG.MultiDirectedGraph ? new GG.MultiDirectedGraph()
    : new (GG.Graph || GG)({ type:'directed', multi:true });
  DATA.nodes.forEach(function(n){ try{ graph.addNode(n.key, Object.assign({}, n.attrs)); }catch(_){} });
  DATA.edges.forEach(function(e){ try{ graph.addEdgeWithKey(e.key, e.source, e.target, Object.assign({}, e.attrs)); }catch(_){} });

  // ---- collapsible MODULE<->SYMBOL quotient: gate + module index ----
  // MODS null (no `const MODULES=` baked) => the entire quotient is inert and displayed
  // === graph (the v0.6.2 path). When present, `displayed` is a fresh graph rebuilt by
  // buildQuotient from (master + ST.expanded + ST.hidden) on every collapse-state change.
  var MODS = (typeof MODULES !== 'undefined' && MODULES) ? MODULES : null;
  var MODBYID = {};
  if(MODS){ for(var mi=0; mi<MODS.length; mi++){ MODBYID[MODS[mi].id] = MODS[mi]; } }
  var displayed = MODS ? (GG.MultiDirectedGraph ? new GG.MultiDirectedGraph()
    : new (GG.Graph || GG)({ type:'directed', multi:true })) : graph;

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
    hiddenE:new Set(),         // edge-hide for the view toggle
    expanded:new Set() };      // collapsible quotient: module ids the user EXPANDED (empty => all collapsed)
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

  // ---- collapsible quotient: repOf + buildQuotient ----
  // repOf(k): the DISPLAYED representative of a MASTER node key k — itself when the
  // quotient is off / its module is expanded / it is ungrouped, else its 'mod:'+module
  // super-node. Pure over (graph attrs + ST.expanded).
  function repOf(k){
    if(!MODS) return k;
    var a = graph.getNodeAttributes(k); var mod = a && a.module;
    if(mod && ST.expanded.has(mod)) return k;
    if(mod) return 'mod:' + mod;
    return k;
  }
  // buildQuotient(): rebuild `displayed` from (master graph + ST.expanded + ST.hidden).
  // Only callable when MODS. A COLLAPSED module => ONE super-node 'mod:<id>' (sized by its
  // NON-HIDDEN member count); an EXPANDED module => its member symbol nodes verbatim. A
  // module is emitted (and view-counted) ONLY if it has >=1 non-hidden member. Edges map
  // each endpoint to its displayed rep, drop intra-collapsed self-loops, and DEDUP onto a
  // single deterministic 'q:rs>rd' key (weight + commutative cross/bridge flags).
  function buildQuotient(){
    if(!MODS) return;
    displayed.clear();                              // keeps the sigma binding + listeners
    // live per-module non-hidden member tally (for super-node size + view visibility)
    var liveMembers = {};
    graph.forEachNode(function(k,a){
      if(ST.hidden.has(k)) return;
      var mod = a.module;
      if(mod) liveMembers[mod] = (liveMembers[mod]|0) + 1;
    });
    var emitted = {};
    graph.forEachNode(function(k,a){
      if(ST.hidden.has(k)) return;                  // hidden member drops out of its super-node
      var mod = a.module;
      if(mod && !ST.expanded.has(mod)){             // COLLAPSED -> super-node (once)
        var sid = 'mod:' + mod;
        if(!emitted[sid]){
          emitted[sid] = true;
          var m = MODBYID[mod] || {label:mod, zone:'dependency', band:0, color:'#888', members:1, side:'dependency'};
          var cnt = liveMembers[mod] || 1;
          displayed.addNode(sid, { label:m.label, kind:'module', zone:m.zone,
            band:(m.band|0), color:m.color, size:8 + Math.log2(cnt + 1) * 4,
            module:mod, isSuper:true, path:'', cluster:m.zone, fan_in:cnt, deg:cnt });
        }
      } else {                                      // EXPANDED or ungrouped -> verbatim member
        try{ displayed.addNode(k, Object.assign({}, a)); }catch(_){}
      }
    });
    var seen = {};                                  // 'rsrd' -> displayed edge key
    graph.forEachEdge(function(ek,ea,s,t){
      if(ST.hidden.has(s) || ST.hidden.has(t)) return;
      var rs = repOf(s), rd = repOf(t);
      if(rs === rd && rs.indexOf('mod:') === 0) return;       // intra-collapsed self-loop
      if(!displayed.hasNode(rs) || !displayed.hasNode(rd)) return;
      var sk = rs + '' + rd;
      var qk = seen[sk];
      if(qk === undefined){
        qk = 'q:' + rs + '>' + rd; seen[sk] = qk;
        displayed.addEdgeWithKey(qk, rs, rd, { rel:ea.rel, weight:1, size:(ea.size||1),
          color:(ea.color||'#c4c9d4'), cross:(ea.cross|0), bridge:(ea.bridge|0) });
      } else {
        displayed.updateEdgeAttribute(qk, 'weight', function(w){ return (w||1) + 1; });
        if(ea.cross) displayed.setEdgeAttribute(qk, 'cross', 1);
        if(ea.bridge) displayed.setEdgeAttribute(qk, 'bridge', 1);
      }
    });
    // commutative seam tint: derive color purely from the OR-ed cross flag AFTER the loop,
    // so the tint is independent of forEachEdge order (the bridge reducer repaints red).
    displayed.forEachEdge(function(ek,ea){
      if(ea.cross){ displayed.setEdgeAttribute(ek,'color','#7f8a9c');
        if((ea.size||1) < 1.5) displayed.setEdgeAttribute(ek,'size',1.5); }
    });
    // LAYOUT the quotient from a fresh deterministic seed (RUNTIME ONLY; no baked coords).
    reseed(displayed);
    if(FA2 && displayed.order >= 2 && displayed.size > 0){
      try{ FA2.assign(displayed, { iterations: ITER, settings: fa2Settings(displayed) }); }catch(_){}
    }
    partitionBandsOn(displayed.forEachNode.bind(displayed),
      function(k){ return displayed.getNodeAttributes(k); },
      function(k,nx){ displayed.setNodeAttribute(k,'x',nx); });
    if(renderer){ renderer.refresh(); fitVisible(true); }
    updateCounts(); if(typeof renderRows === 'function') renderRows();
    if(typeof renderModuleRows === 'function') renderModuleRows();
  }

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
    const ex = displayed.extremities(key);          // displayed: quotient edge keys ('q:..') live ONLY here
    if(ST.hiddenE.has(key) || ST.hidden.has(ex[0]) || ST.hidden.has(ex[1])){ r.hidden = true; return r; }
    if(ST.preview && (ST.preview.has(ex[0]) || ST.preview.has(ex[1]))){ r.color='#eceef3'; r.zIndex=0; }  // dim preview-incident
    if(attr.bridge){ r.color = '#e15759'; r.size = Math.max(r.size||1, 3); r.zIndex = 3; }  // bridge: red+thick
    if(ST.q){ const both = ST.hits && ST.hits.has(ex[0]) && ST.hits.has(ex[1]); if(!both) r.color = '#eef0f4'; }
    return r;
  }

  // ---- BOOT layout: exclude cfg-hidden nodes from FA2 participation so the camera
  // auto-fits the VISIBLE subgraph. Empty cfg => the EXACT v0.6.0 code path (full-graph
  // FA2 + partitionBands), so unconfigured runtime layout is unchanged. ----
  // QUOTIENT GATE: when MODS, the master `graph` is NOT what sigma renders — the default
  // all-collapsed `displayed` is. Skip the boot layout of master entirely; buildQuotient()
  // (run once below, after the renderer + panel fns exist) produces the overview.
  if(!MODS){
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
  }

  const renderer = new SigmaCls(displayed, document.getElementById('cy'), {
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
    m.textContent=(d.kind||'')+(d.cluster?('  \u00b7  '+d.cluster):''); info.appendChild(m);
    const c=document.createElement('code'); c.textContent=d.path||''; info.appendChild(c);
    info.style.display='block';
  }
  renderer.on('clickNode', function(e){
    // read from `displayed` (the bound graph): a collapsed super-node 'mod:<id>' exists
    // ONLY there, never in master. locateRow on a 'mod:' key is a harmless no-op (no HP row).
    if(displayed.hasNode(e.node)) showInfo(displayed.getNodeAttributes(e.node));
    locateRow(e.node);                              // graph -> list: scroll+flash the row
  });
  renderer.on('clickStage', function(){ info.style.display='none'; });

  // ---- frame the camera on the currently-VISIBLE nodes via a custom bbox. The sigma
  // nodeExtent/normalization span the FULL graph (hidden nodes still distort it), so an
  // explicit setCustomBBox over visible-only x/y is required for a tight fit. ----
  function fitVisible(animate){
    var minx=Infinity,maxx=-Infinity,miny=Infinity,maxy=-Infinity,seen=false;
    displayed.forEachNode(function(k,a){            // displayed: frames the rendered quotient (super-nodes incl.)
      if(ST.hidden.has(k)) return;
      if(typeof a.x!=='number'||typeof a.y!=='number') return;
      if(a.x<minx)minx=a.x; if(a.x>maxx)maxx=a.x; if(a.y<miny)miny=a.y; if(a.y>maxy)maxy=a.y; seen=true;
    });
    if(!seen){ try{ renderer.getCamera().animatedReset({duration:300}); }catch(_){} return; }
    try{ renderer.setCustomBBox({ x:[minx,maxx], y:[miny,maxy] }); }catch(_){}
    renderer.refresh();
    try{ renderer.getCamera().animatedReset({duration: animate?300:0}); }catch(_){}
  }
  if(!MODS && ST.hiddenCfg.size > 0) fitVisible(false);  // tight initial frame on the visible subgraph (MODS: buildQuotient fits)

  // ---- search: dim non-matches, highlight hits (label + path) ----
  const q = document.getElementById('q');
  if(q){ q.addEventListener('input', function(){
    ST.q = q.value.trim().toLowerCase();
    ST.hits = null;
    if(ST.q){ ST.hits = new Set();
      displayed.forEachNode(function(k,a){           // displayed: search matches the RENDERED nodes (incl. super-nodes)
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
    // QUOTIENT: rebuild displayed so the view change reaches the super-nodes. A super-node
    // is emitted only if it has >=1 non-hidden member, so a module whose members are ALL
    // view-hidden (e.g. a pure-dependency module under view='internal') disappears.
    if(MODS){ recomputeHidden(); buildQuotient(); return; }
    recomputeHidden(); renderer.refresh(); updateCounts();
  }
  if(viewSel){ viewSel.addEventListener('change', function(){ applyView(viewSel.value); }); }
  if(HAS_ZONES && !MODS) applyView(ST.view);        // MODS: buildQuotient (below) applies the initial view

  // ---- drag a NODE (≠ pan): canvas drag still pans (sigma default) ----
  // Writes go to `displayed` (the bound graph) — a dragged key may be a super-node that
  // exists ONLY in displayed; off-path displayed===graph so this is identical to v0.6.2.
  let dragged=null, dragging=false;
  renderer.on('downNode', function(e){ dragging=true; dragged=e.node;
    if(displayed.hasNode(dragged)) displayed.setNodeAttribute(dragged,'highlighted',true); });
  renderer.getMouseCaptor().on('mousemovebody', function(e){
    if(!dragging || !displayed.hasNode(dragged)) return;
    const p = renderer.viewportToGraph(e);
    displayed.setNodeAttribute(dragged,'x',p.x); displayed.setNodeAttribute(dragged,'y',p.y);
    e.preventSigmaDefault(); e.original.preventDefault(); e.original.stopPropagation();
  });
  renderer.getMouseCaptor().on('mouseup', function(){
    if(dragged && displayed.hasNode(dragged)) displayed.removeNodeAttribute(dragged,'highlighted');
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
      // displayed: super-nodes carry their (min-member) band so boxes frame the quotient.
      displayed.forEachNode(function(k,a){
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
    for(var i=0;i<libs.length;i++){ libTags += '<script>' + libs[i] + '<\/script>'; }
    // child HIDDEN = parent HIDDEN intersected with the sub-slice keys (cfg-hide affects
    // child RENDERING only, never chain reachability). Rides INSIDE the same bare consts
    // <script> as DATA so the offline bare-tag count + libTexts() `const `-skip both hold.
    var subKeys = {}; for(var si=0; si<sub.nodes.length; si++){ subKeys[sub.nodes[si].key] = true; }
    var childHidden = []; for(var hj=0; hj<HIDDEN.length; hj++){ if(subKeys[HIDDEN[hj]]) childHidden.push(HIDDEN[hj]); }
    var consts = 'const DATA=' + JSON.stringify(sub) + ';\n'
      + 'const HAS_ZONES=' + (typeof HAS_ZONES!=='undefined' ? JSON.stringify(HAS_ZONES) : 'false') + ';\n'
      + 'const ITER=' + (typeof ITER!=='undefined' ? ITER : 90) + ';\n'
      + 'const INITVIEW=' + JSON.stringify(typeof INITVIEW!=='undefined'?INITVIEW:'both') + ';\n'
      + 'const BANDS=' + JSON.stringify(typeof BANDS!=='undefined'?BANDS:[]) + ';\n'
      + 'const HIDDEN=' + JSON.stringify(childHidden) + ';\n'
      // re-emit MODULES (slice-independent: module ids/meta describe ALL modules; modules
      // with zero surviving members in the child slice simply emit no super-node because
      // buildQuotient only emits one when iterating a master node carrying that module).
      // Rides INSIDE the same bare consts string as DATA (still starts 'const DATA=').
      + (typeof MODULES!=='undefined' && MODULES ? 'const MODULES=' + JSON.stringify(MODULES) + ';\n' : '');
    var styleEl = document.querySelector('style');
    var barEl = document.getElementById('bar');
    var banner = '<div style="position:fixed;top:44px;left:0;right:0;z-index:9;'
      + 'background:#3a2f12;color:#ffcf5c;padding:4px 10px;font:12px system-ui,sans-serif">'
      + '\u26a0 chain of ' + esc(rootLabel)
      + ' (this slice only \u2014 re-run manyscan for a deeper chain)</div>';
    var html = '<!doctype html><html><head><meta charset=utf-8><title>chain: '
      + esc(rootLabel) + '</title>'
      + (styleEl ? styleEl.outerHTML : '')
      + '</head><body>'
      + (barEl ? barEl.outerHTML : '')
      + banner
      + '<div id="cy"></div><div id="info"></div>'
      + PRISTINE_HP                              // fresh, pristine hide panel (no live state)
      + libTags
      + '<script>' + consts + '<\/script>'
      + '<script id="ms-boot">' + bootText() + '<\/script>'
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
    if(MODS){
      // the canvas shows the QUOTIENT (super-nodes + expanded symbols), not 1769 symbols.
      // Report the actual rendered node count + symbols + collapsed-module count, so the
      // bar never reads 'shown 0' against a populated overview.
      var collapsed = 0; for(var ci=0; ci<MODS.length; ci++){ if(!ST.expanded.has(MODS[ci].id)) collapsed++; }
      countsEl.textContent = '\u00b7 displayed ' + displayed.order + ' nodes \u00b7 '
        + x + ' / ' + total + ' symbols \u00b7 ' + collapsed + ' modules collapsed';
    } else {
      countsEl.textContent = '\u00b7 shown ' + x + ' / hidden ' + y + ' / total ' + total;
    }
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

  // ---- MODULES section (the ONLY collapse control). Lists every module grouped by side
  // with a per-module expand/collapse toggle + LIVE non-hidden member count; row click
  // toggles ST.expanded then rebuilds the quotient. No-op when the quotient is off. ----
  function refreshModDelta(){       // pending vs committed expand state -> "Apply: expand N, collapse M"
    if(!MODS) return;
    if(!ST.expandPending) ST.expandPending = new Set(ST.expanded);
    var add=0, rem=0;
    ST.expandPending.forEach(function(id){ if(!ST.expanded.has(id)) add++; });
    ST.expanded.forEach(function(id){ if(!ST.expandPending.has(id)) rem++; });
    var el = document.getElementById('hp-mdelta');
    if(el) el.textContent = (add||rem) ? ('Apply: expand '+add+', collapse '+rem) : '';
  }
  function renderModuleRows(){
    if(!MODS) return;
    if(!ST.expandPending) ST.expandPending = new Set(ST.expanded);
    var box = document.getElementById('hp-mlist'); if(!box) return;
    box.innerHTML = '';
    // LIVE non-hidden member tally per module (so a module fully hidden shows 0 + the row
    // is a no-op; chain-child slices show only modules with surviving members as non-zero).
    var live = {};
    graph.forEachNode(function(k,a){
      if(ST.hidden.has(k)) return; var m = a.module; if(m) live[m] = (live[m]|0) + 1;
    });
    var bySide = { target:[], dependency:[] };
    for(var i=0;i<MODS.length;i++){ var m=MODS[i]; (bySide[m.side]||(bySide[m.side]=[])).push(m); }
    ['target','dependency'].forEach(function(side){
      var arr = bySide[side]; if(!arr || !arr.length) return;
      var grp = document.createElement('div'); grp.className='hp-mod-grp'; grp.textContent = side;
      box.appendChild(grp);
      for(var j=0;j<arr.length;j++){ (function(m){
        var row = document.createElement('div'); row.className='hp-mrow';
        var exP = ST.expandPending.has(m.id);                 // PENDING (preview) state
        var exC = ST.expanded.has(m.id);                      // COMMITTED (rendered) state
        if(exP) row.classList.add('expanded');
        if(exP !== exC) row.classList.add('mpending');        // pending change until Apply
        var car = document.createElement('span'); car.className='car';
        car.textContent = exP ? '\u25be' : '\u25b8';
        var nm = document.createElement('div'); nm.className='mn'; nm.textContent = m.label; nm.title = m.id;
        var q = document.createElement('div'); q.className='mq';
        q.textContent = (live[m.id]|0) + '/' + m.members;
        row.appendChild(car); row.appendChild(nm); row.appendChild(q);
        row.addEventListener('click', function(){      // STAGE 1: toggle pending only (no relayout)
          if(ST.expandPending.has(m.id)) ST.expandPending.delete(m.id); else ST.expandPending.add(m.id);
          renderModuleRows(); refreshModDelta();
        });
        box.appendChild(row);
      })(arr[j]); }
    });
    var n = document.getElementById('hp-mods-n');
    if(n) n.textContent = ST.expanded.size + ' / ' + MODS.length + ' expanded';
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
    // QUOTIENT: the displayed quotient IS the relayout target — rebuild it (a committed
    // hide of a member now drops it from buildQuotient, shrinking its super-node).
    if(MODS){ buildQuotient(); return; }
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
    // QUOTIENT: a member inside a COLLAPSED module is not a displayed node — auto-expand
    // its module (rebuild the quotient) so the symbol becomes locatable IN PLACE.
    if(MODS && !displayed.hasNode(key)){
      var a = graph.getNodeAttributes(key); var mod = a && a.module;
      if(mod && !ST.expanded.has(mod)){ ST.expanded.add(mod); buildQuotient(); }
      if(!displayed.hasNode(key)) return;           // still absent (e.g. hidden) => give up
    }
    var hp = document.getElementById('hp'); if(hp && hp.classList.contains('collapsed')){ /* keep */ }
    var d = renderer.getNodeDisplayData(key);
    if(d){ try{ renderer.getCamera().animate({ x:d.x, y:d.y, ratio:0.45 }, { duration:400 }); }catch(_){} }
    try{ displayed.setNodeAttribute(key,'highlighted',true); renderer.refresh();
      setTimeout(function(){ if(displayed.hasNode(key)) displayed.removeNodeAttribute(key,'highlighted'); renderer.refresh(); }, 900);
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
        // use the UNSUFFIXED name (strip the baked frontier '  +N\u2933' marker) so the
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
    // drag the left-edge grip to RESIZE the panel width (so long symbol names aren't clipped)
    var grip = document.getElementById('hp-grip');
    if(grip){
      var rz = false;
      grip.addEventListener('mousedown', function(e){ rz = true; e.preventDefault(); document.body.style.userSelect='none'; });
      window.addEventListener('mousemove', function(e){
        if(!rz) return;
        var w = window.innerWidth - e.clientX;                       // right-anchored panel => width grows leftward
        hp.style.width = Math.max(260, Math.min(window.innerWidth - 80, w)) + 'px';
      });
      window.addEventListener('mouseup', function(){ if(rz){ rz = false; document.body.style.userSelect=''; } });
    }
    // ---- MODULES section: the ONLY collapse control (expand/collapse here, never on the
    // graph). Its markup is emitted ONLY when the quotient is on (the OFF panel is byte-
    // identical to v0.6.2 and has no hp-tabs / hp-mods / hp-hide-sec elements at all).
    if(MODS){
      if(!ST.expandPending) ST.expandPending = new Set(ST.expanded);
      var mex = document.getElementById('hp-mexpand');     // STAGE 1: set pending, no relayout
      if(mex) mex.addEventListener('click', function(){ MODS.forEach(function(m){ ST.expandPending.add(m.id); }); renderModuleRows(); refreshModDelta(); });
      var mco = document.getElementById('hp-mcollapse');
      if(mco) mco.addEventListener('click', function(){ ST.expandPending.clear(); renderModuleRows(); refreshModDelta(); });
      var map = document.getElementById('hp-mapply');      // STAGE 2: commit pending -> ONE rebuild/relayout
      if(map) map.addEventListener('click', function(){ ST.expanded = new Set(ST.expandPending); buildQuotient(); renderModuleRows(); refreshModDelta(); });
      // tab switch: show one section (Modules | Hide) at a time -> no overlap, full height
      var tabs = hp.querySelectorAll('.hp-tabb');
      tabs.forEach(function(tb){
        tb.addEventListener('click', function(){
          tabs.forEach(function(x){ x.classList.remove('active'); });
          tb.classList.add('active');
          ['hp-mods','hp-hide-sec'].forEach(function(sid){
            var s = document.getElementById(sid);
            if(s) s.classList.toggle('active', s.id === tb.getAttribute('data-sec'));
          });
        });
      });
      renderModuleRows();
    }
    renderRows(); refreshDeltaHint(); updateCounts();
  }
  setupHidePanel();
  updateCounts();

  // ---- QUOTIENT boot: produce the default ALL-COLLAPSED overview (ST.expanded empty).
  // applyView (when HAS_ZONES) seeds ST.hiddenView for the initial view THEN calls
  // buildQuotient; otherwise buildQuotient directly. The OFF path never reaches here
  // (the v0.6.2 boot FA2 + the HAS_ZONES applyView above already ran). ----
  if(MODS){ if(HAS_ZONES){ applyView(ST.view); } else { buildQuotient(); } }

  renderer.on('doubleClickNode', function(e){
    e.preventSigmaDefault();                       // MUST stay synchronous-first (suppresses sigma's zoom)
    // a collapsed module super-node has no symbol chain to drill — ignore double-click on it
    // (expand/collapse is ONLY the side-panel MODULES section). Symbol drill-down unchanged.
    if(MODS && String(e.node).indexOf('mod:') === 0) return;
    var html = buildChild(e.node);
    var url = URL.createObjectURL(new Blob([html], {type:'text/html'}));
    window.open(url, '_blank');                    // do NOT revoke (races the child load)
  });
})();
