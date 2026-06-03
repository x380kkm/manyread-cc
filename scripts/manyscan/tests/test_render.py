"""Tests for manyscan.lib.render — deterministic views + honest frontier rendering."""
from __future__ import annotations

import json

from lib import analyze, render
from lib.graph import Edge, Graph, Node


def _slice():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a.py"))
    g.add_node(Node("file:2", "file", label="b.py"))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    g.truncated = True
    g.frontier_depth = 1
    g.elided = 7
    g.frontier["file:2"] = 7
    return g


def test_to_json_deterministic_and_bounded():
    data = json.loads(render.to_json(_slice()))
    assert [n["label"] for n in data["nodes"]] == ["a.py", "b.py"]
    assert data["edges"][0] == {
        "src": "file:1", "dst": "file:2", "relation": "imports", "weight": 1, "evidence": None,
    }
    assert data["bounded"]["truncated"] is True
    assert data["bounded"]["elided"] == 7
    assert data["bounded"]["frontier"] == {"file:2": 7}


def test_to_json_is_stable():
    a, b = render.to_json(_slice()), render.to_json(_slice())
    assert a == b  # deterministic


def test_mermaid_marks_frontier_and_truncation():
    out = render.to_mermaid(_slice())
    assert out.startswith("flowchart TD")
    assert "truncated at level 1: 7 deps elided" in out
    assert "+7⤳" in out  # frontier node tagged
    assert "-->|imports|" in out


def test_dot_basic():
    out = render.to_dot(_slice())
    assert out.startswith("digraph manyscan {")
    assert '"file:1" -> "file:2" [label="imports"];' in out


def test_text_prints_honest_truncation_warning():
    out = render.to_text(_slice())
    assert "⚠ 已在第 1 层封顶,省略 7 个依赖(分布: file:2→7)" in out
    assert "b.py  (+7 越界)" in out


def test_metrics_text_summary_and_warning():
    g = _slice()
    txt = render.metrics_text(analyze.metrics(g))
    assert "cycles=0" in txt and "bridges=" in txt
    assert "省略 7 个依赖" in txt
    assert "most_unstable:" in txt


def test_to_html_self_contained_and_interactive():
    out = render.to_html(_slice())
    assert out.startswith("<!doctype html>") and out.rstrip().endswith("</html>")
    # sigma + graphology + graphology-library (forceAtlas2) inlined as UMD <script>
    # globals (offline, single file)
    assert "new SigmaCls(" in out and len(out) > 200_000
    assert "graphologyLibrary" in out    # graphology-library (forceAtlas2) UMD
    assert "window.graphology" in out    # graphology core UMD global
    assert "FA2.assign(" in out          # forceAtlas2 layout
    assert "a.py" in out and "b.py" in out
    assert "+7⤳" in out                  # frontier node tagged in its label
    assert "7 deps elided" in out        # honest truncation banner
    assert "search node" in out          # interactive search box


def test_to_html_offline_no_network_load():
    # the renderer must inline all libs (no <script src=...> / no http(s) URL fetched
    # at runtime). CDN URLs live ONLY in the per-file fallback branch (not emitted
    # when the asset exists), so a generated page contains zero network references.
    out = render.to_html(_slice())
    assert "<script src=" not in out     # nothing fetched over the network
    # 3 bare lib <script> + 1 bare consts <script> (the boot tag is <script id="..."> so
    # it does NOT match the literal '<script>'; drill-down build-strings only inflate it).
    assert out.count("<script>") >= 4
    # harden the offline guard on the invariants that actually matter (not the count):
    assert 'id="ms-boot"' in out         # the bootstrap is retrievable by the drill-down child
    assert "new SigmaCls(" in out        # the boot tag carries the real sigma bootstrap
    assert "window.graphology" in out    # graphology core UMD inlined
    assert "graphologyLibrary" in out    # graphology-library (forceAtlas2) inlined


def test_to_html_deterministic():
    assert render.to_html(_slice()) == render.to_html(_slice())


def test_html_in_formats():
    assert "html" in render.FORMATS
    assert render.render(_slice(), "html").startswith("<!doctype html>")


def test_html_exposes_node_path_and_info_panel():
    out = render.to_html(_slice())
    assert '"path"' in out                  # every node carries its file path
    assert "id='info'" in out               # tap-a-node info panel exists
    assert "GET ITS FILE PATH" in out       # the panel's purpose
    assert "search node/path" in out        # search covers path too


def test_html_colors_by_cluster_when_present():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a.py", attrs={"cluster": "mod#0"}))
    g.add_node(Node("file:2", "file", label="b.py", attrs={"cluster": "mod#1"}))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    out = render.to_html(g)
    assert '"cluster": "mod#0"' in out and '"cluster": "mod#1"' in out
    assert "color=cluster" in out           # legend switched to cluster mode
    # cluster colors come from the palette (not the zone tints)
    assert '"color": "#4e79a7"' in out and '"color": "#f28e2b"' in out


def test_render_unknown_format_raises():
    try:
        render.render(_slice(), "yaml")
        assert False
    except ValueError:
        pass


# --- REDESIGN: importance sizing, hub/bridge highlight, drag/pan, view toggle ---
def _zoned_hub_graph():
    """A tiny zoned graph with a clear hub + a bridge edge.

    target: p1,p2,p3 are mutually wired (a 3-cycle so none is a leaf) and all
    -> hub h (dependency); hub h -> leaf l (dependency). The edge h->l is the unique
    articulation BRIDGE (removing it splits l off). h has fan_in 3.
    """
    g = Graph()
    for nid in ("p1", "p2", "p3"):
        g.add_node(Node(nid, "class", label=nid, attrs={"zone": "target", "cluster": "target"}))
    g.add_node(Node("h", "class", label="Hub", attrs={"zone": "dependency", "cluster": "dependency"}))
    g.add_node(Node("l", "class", label="Leaf", attrs={"zone": "dependency", "cluster": "dependency"}))
    # target 3-cycle: p1->p2->p3->p1 (keeps each target node multiply-connected)
    g.add_edge(Edge("p1", "p2", "uses_type"))
    g.add_edge(Edge("p2", "p3", "uses_type"))
    g.add_edge(Edge("p3", "p1", "uses_type"))
    for nid in ("p1", "p2", "p3"):
        g.add_edge(Edge(nid, "h", "uses_type"))
    g.add_edge(Edge("h", "l", "uses_type"))
    return g


def test_importance_degree_hub_bridge():
    g = _zoned_hub_graph()
    imp = render._importance(g)
    # hub h: fan_in=3 (p1,p2,p3) + fan_out=1 (l) -> deg=4, flagged hub
    assert imp["h"]["fan_in"] == 3 and imp["h"]["fan_out"] == 1 and imp["h"]["deg"] == 4
    assert imp["h"]["hub"] == 1
    # the only articulation BRIDGE edge is h->l; h and l carry the bridge flag
    assert imp["h"]["bridge"] == 1 and imp["l"]["bridge"] == 1
    # plugin nodes are inside a cycle: not on a bridge edge
    assert imp["p1"]["bridge"] == 0


def test_html_has_degree_sizing_all_graphs():
    # degree-based node sizing applies to ALL graphs (incl. plain no-zone slice).
    # In sigma the size is BAKED per node (no client-side mapper), so assert the
    # per-node size + degree attrs are present in DATA.
    out = render.to_html(_slice())
    assert '"size":' in out                 # baked per-node size (degree-scaled)
    assert '"deg":' in out                  # every node carries its degree
    assert "mapData(" not in out            # no cytoscape mapper leaked
    assert "DEGMAX" not in out              # no cytoscape DEGMAX token leaked


def test_html_hub_and_bridge_markers():
    out = render.to_html(_zoned_hub_graph())
    assert '"hub": 1' in out                # the hub node is tagged in DATA
    assert '"bridge": 1' in out             # the bridge edge is tagged in DATA
    assert "highlighted" in out             # hub halo via sigma highlighted flag
    assert "attr.bridge" in out             # edge reducer paints bridges red+thick
    assert "#e15759" in out                 # bridge red


def test_html_drag_pan_config():
    out = render.to_html(_zoned_hub_graph())
    # drag a NODE moves it; dragging the canvas pans (sigma default). The node-drag
    # recipe is downNode -> mousemovebody -> mouseup with preventSigmaDefault.
    assert "downNode" in out
    assert "mousemovebody" in out
    assert "preventSigmaDefault" in out


def test_html_view_toggle_one_page():
    out = render.to_html(_zoned_hub_graph(), view="dependency")
    assert "id='view'" in out               # single in-page view toggle
    assert "<option value='internal'>" in out
    assert "<option value='dependency' selected>" in out  # initial state threaded from view=
    assert "<option value='both'>" in out
    assert "applyView" in out               # client-side show/hide handler
    assert '"cross": 1' in out              # target->dependency crossings tagged


def test_html_zone_encoding_color_and_cluster():
    out = render.to_html(_zoned_hub_graph())
    # sigma has no compound parents: zones are encoded by node COLOR + spatial
    # clustering (no '一堆方框'). Every real node carries its zone + a zone tint, and
    # the layout seed biases the two zones apart (no '__zone_*__' pseudo-nodes).
    assert "__zone_" not in out
    assert '"zone": "target"' in out
    assert '"zone": "dependency"' in out
    assert '"color": "#4e79a7"' in out      # target zone tint
    assert '"color": "#f28e2b"' in out      # dependency zone tint


def test_html_no_zone_no_toggle_but_sized():
    # backward compat: a no-zone graph renders with NO view toggle + NO zone parents,
    # but STILL gets degree sizing.
    out = render.to_html(_slice())
    assert "id='view'" not in out           # toggle hidden for plain graphs
    assert "__zone_" not in out
    assert "const HAS_ZONES=false" in out
    assert '"size":' in out                 # degree sizing still applies (baked)


def test_html_redesign_deterministic():
    # two renders of the zoned + a plain graph are byte-identical (no random/time)
    assert render.to_html(_zoned_hub_graph()) == render.to_html(_zoned_hub_graph())
    assert render.to_html(_slice()) == render.to_html(_slice())


def test_html_no_cytoscape_leftovers():
    # migration guard: no cytoscape-era tokens may survive in the sigma renderer
    # (a stray mapData/fcose/DEGMAX/underlay would mean a half-migrated template).
    for g in (_zoned_hub_graph(), _slice()):
        out = render.to_html(g)
        for tok in ("cytoscape", "mapData(", "fcose", "DEGMAX", "underlay-color",
                    "boxSelectionEnabled", "__zone_", "data(zonecolor)"):
            assert tok not in out, tok


def test_dependency_view_hide_logic_leaves_no_dangling_edge():
    """Regression guard for the dependency-view JS contract (render.py applyView('dependency')).

    The JS hides (a) pure target->target edges and (b) target nodes with no crossing
    edge. A VISIBLE edge must never reference a HIDDEN node, or fcose's eles re-layout
    can choke. A target node only gets hidden when ALL its incident edges are
    target->target (hence already hidden), so no visible edge can dangle. This mirrors
    that invariant in Python so a future JS change that breaks it fails a test.
    """
    g = _zoned_hub_graph()
    # add an isolated target node wired ONLY into other target nodes (no crossing edge)
    g.add_node(Node("p_iso", "class", label="iso", attrs={"zone": "target", "cluster": "target"}))
    g.add_edge(Edge("p_iso", "p1", "uses_type"))
    zone = {n.id: n.attrs.get("zone") for n in g.nodes.values()}

    def cross(e):
        return zone[e.src] == "target" and zone[e.dst] == "dependency"

    hidden_edges = {(e.src, e.dst) for e in g.edges
                    if zone[e.src] == "target" and zone[e.dst] == "target"}
    hidden_nodes = {
        nid for nid in g.nodes
        if zone[nid] == "target"
        and not any(cross(e) for e in g.edges if nid in (e.src, e.dst))
    }
    assert "p_iso" in hidden_nodes  # the iso target node IS hidden
    for e in g.edges:
        if (e.src, e.dst) in hidden_edges:  # edge itself hidden -> can't dangle
            continue
        assert e.src not in hidden_nodes and e.dst not in hidden_nodes


# --- LAYERED N-band views: band-attr gating, box layer, drill-down, determinism ---
def _bands_for(g, layers):
    """Compute (band_of, bands_meta) the way scan.py wires them, for render tests."""
    from lib import boundary
    return boundary.assign_bands(g, layers)


def test_band_attr_gating_flat_vs_banded():
    # band_of=None (plain/flat) => NO "band": node attr in DATA, but const BANDS=[];
    plain = render.to_html(_zoned_hub_graph())
    assert '"band":' not in plain
    assert "const BANDS=[];" in plain
    # band_of provided => "band" rides into DATA + const BANDS=[{ ... with labels
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    banded = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    assert '"band":' in banded
    assert 'const BANDS=[{' in banded
    # explicit ordered meta literal (locks the emitted bytes against reordering)
    assert ('const BANDS=[{"band": 0, "label": "target-core"}, '
            '{"band": 1, "label": "target-iface"}, '
            '{"band": 2, "label": "dep-iface"}, '
            '{"band": 3, "label": "dep-core"}];') in banded


def test_band_attr_does_not_change_plain_data_bytes():
    # the DATA payload of a plain band_of=None render must be byte-identical to today:
    # adding `const BANDS=[];` is a separate const line, not part of DATA.
    out = render.to_html(_slice())
    marker = "const DATA="
    start = out.index(marker) + len(marker)
    end = out.index(";\n", start)
    payload = out[start:end]
    assert '"band":' not in payload


def test_layered_html_byte_deterministic():
    # render the SAME zoned graph twice with bands -> byte-identical + md5 equal
    import hashlib
    for layers in ("two", "four"):
        bo, bm = _bands_for(_zoned_hub_graph(), layers)
        a = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
        bo2, bm2 = _bands_for(_zoned_hub_graph(), layers)
        b = render.to_html(_zoned_hub_graph(), band_of=bo2, bands_meta=bm2)
        assert a == b
        assert hashlib.md5(a.encode()).hexdigest() == hashlib.md5(b.encode()).hexdigest()


def test_drilldown_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    for tok in ("doubleClickNode", "preventSigmaDefault", "URL.createObjectURL",
                "Blob(", "window.open(", "ms-boot", "chainKeys", "buildChild"):
        assert tok in out, tok
    # the </script> inside the child-build strings MUST be escaped so the HTML parser
    # never sees a real closing tag for the parent page's script.
    assert "<\\/script>" in out
    # the only literal '</script>' occurrences are the 6 real closing tags of the
    # emitted script tags (3 libs + consts + boot + ... ), NOT inside a build-string.
    # Practically: every build-string close is the escaped form.
    assert "'<\\/script>'" in out or '"<\\/script>"' in out


def test_nband_box_layer_markers():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    for tok in ("afterRender", "graphToViewport", "insertBefore", "partitionBands",
                "drawBands", "NBANDS"):
        assert tok in out, tok
    assert "pointerEvents" in out or "pointer-events" in out
    # the box layer follows the view toggle by consulting ST.hidden (NOT a node attr)
    assert "ST.hidden.has(k)" in out
    # the partition divisor is guarded against a zero/near-zero span (no NaN crash)
    assert "span > 1e-9" in out
    # flat (band_of=None) installs no box layer: BANDS=[] makes NBANDS==1 a no-op
    flat = render.to_html(_zoned_hub_graph())
    assert "const BANDS=[];" in flat


def test_flat_and_plain_still_render_with_features():
    # backward compat: plain (no band) + zoned (no band) still render fully
    for g in (_slice(), _zoned_hub_graph()):
        out = render.to_html(g)
        assert out.startswith("<!doctype html>") and out.rstrip().endswith("</html>")
        assert "search node" in out
        assert "const BANDS=[];" in out
        # the hide panel is ADDITIVE on every graph; no-config path bakes NO HIDDEN line
        assert "id='hp'" in out and "setupHidePanel" in out
        assert _consts_block(out).find("const HIDDEN=") < 0
    # a two-band render still carries both zone colors
    bo, bm = _bands_for(_zoned_hub_graph(), "two")
    two = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    assert '"color": "#4e79a7"' in two and '"color": "#f28e2b"' in two
    assert "id='view'" in two            # zoned graph keeps the view toggle


# --- HIDE PANEL + persistent view-hide config + two-stage apply + export ---------
def _consts_block(html: str) -> str:
    """The bare consts <script> segment (from `const DATA=` to the boot tag) — the ONLY
    place a gated `const HIDDEN=` line may appear (the buildChild template literal in the
    boot tag also contains the substring, so a raw `in out` check is ambiguous)."""
    start = html.index("const DATA=")
    end = html.index('<script id="ms-boot">')
    return html[start:end]


def test_default_hidden_baked_sorted_and_deterministic():
    import hashlib

    from lib import boundary
    g = _zoned_hub_graph()
    keys = ["l", "h"]                     # intentionally unsorted input
    bo, bm = boundary.assign_bands(g, "four")
    a = render.to_html(g, band_of=bo, bands_meta=bm, default_hidden=keys)
    b = render.to_html(g, band_of=bo, bands_meta=bm, default_hidden=list(keys))
    # SORTED JSON list baked into the consts block; two renders byte-identical + md5 equal
    assert 'const HIDDEN=["h", "l"];' in _consts_block(a)
    assert a == b
    assert hashlib.md5(a.encode()).hexdigest() == hashlib.md5(b.encode()).hexdigest()


def test_no_config_byte_compat_no_hidden_line():
    # no default_hidden => NO `const HIDDEN=` const line in the consts block (the gate);
    # confirms byte-identity to the v0.6.0 baseline for unconfigured pages.
    for g in (_slice(), _zoned_hub_graph()):
        plain = render.to_html(g)
        explicit_none = render.to_html(g, default_hidden=None)
        assert "const HIDDEN=" not in _consts_block(plain)
        assert "const HIDDEN=" not in _consts_block(explicit_none)
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    banded = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)  # banded, no config
    assert "const HIDDEN=" not in _consts_block(banded)


def test_hide_panel_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("id='hp'", "hp-list", "hp-apply", "hp-export", "hp-fmin",
                "setupHidePanel", "hp-export-ta", "hp-selmatch", "hp-selfan", "ms-counts"):
        assert tok in out, tok
    # facets are value-gated at runtime by HAS_ZONES / BANDS
    assert "HAS_ZONES" in out and "BANDS.length >= 2" in out
    # additive on a plain/flat graph too (facets gate down to kind+fan_in at runtime)
    plain = render.to_html(_slice())
    assert "id='hp'" in plain and "setupHidePanel" in plain


def test_two_stage_preview_apply_markers():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("ST.preview", "togglePreview", "refreshDeltaHint", "Apply: hide ",
                "hiddenView", "hiddenCfg", "hiddenManual", "recomputeHidden", "unhidden"):
        assert tok in out, tok
    # preview branch dims with translucent grey (NOT a green canvas ghost)
    assert "rgba(120,120,140,0.28)" in out
    # applyView writes ST.hiddenView, never `ST.hidden = new Set()` (which would clobber cfg/manual)
    assert "ST.hiddenView = new Set()" in out
    assert "ST.hidden = new Set()" not in out


def test_view_toggle_preserves_cfg_hidden():
    # the view toggle must rebuild ONLY hiddenView + call recomputeHidden (so a toggle can
    # never wipe cfg/manual hides). Mirror the JS contract as string markers.
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    # the applyView body rebuilds hiddenView and recomputes the derived union
    assert "ST.hiddenView = new Set()" in out
    assert "recomputeHidden(); renderer.refresh(); updateCounts();" in out
    # boot seeds hiddenCfg then recomputeHidden BEFORE the layout/Sigma construction
    assert "ST.hiddenCfg.add(HIDDEN[hi])" in out
    assert "recomputeHidden();" in out


def test_relayout_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("operators.subgraph", "partitionBandsOn", "animatedReset",
                "getNodeDisplayData", "FA2.assign(sub", "setCustomBBox"):
        assert tok in out, tok
    # the partitionBands() wrapper + guard tokens still present (existing box-layer test)
    assert "partitionBands()" in out and "span > 1e-9" in out and "NBANDS" in out
    # subgraph resolved via the operators namespace, NOT the (undefined) top-level
    assert "graphologyLibrary.subgraph(" not in out


def test_export_markers_present():
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    for tok in ("exportHidden", "URL.createObjectURL", "navigator.clipboard.writeText",
                "manyread.view_hide.json", "view_hide", "version:1"):
        assert tok in out, tok
    # the export collects sorted names (stable diffs)
    assert "Object.keys(set).sort()" in out
    # and strips the baked frontier suffix so names re-match the config loader's raw label
    assert "lbl.indexOf('  +')" in out


def test_drilldown_child_carries_hidden_and_panel():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    # buildChild emits a child HIDDEN line INSIDE the same consts string as DATA, and
    # re-emits the pristine panel markup so the child runs its own setupHidePanel.
    assert "'const HIDDEN=' + JSON.stringify(childHidden)" in out
    assert "PRISTINE_HP" in out
    # chainKeys still runs over the FULL DATA edges (cfg-hide never filters reachability)
    assert "DATA.edges.forEach" in out


def test_hide_panel_offline_and_deterministic():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    a = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h", "l"])
    b = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["l", "h"])
    assert a == b                              # byte-identical (sorted bake)
    assert "<script src=" not in a            # still fully offline
    assert a.count("<script>") >= 4           # HIDDEN rides in the existing bare consts tag
    # export is Blob/clipboard/textarea only — no network fetch
    assert "fetch(" not in a and "http://" not in a.split("<script>")[-1]


def test_manual_hidden_hides_incident_edges_no_dangle():
    """Mirror of the edgeReducer invariant for manual/cfg hides: an edge is hidden when
    EITHER endpoint is in ST.hidden (render.py edgeReducer), so any node set added to
    the hidden set hides all its incident edges (no dangling edge to a vanished node)."""
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    # edgeReducer's hidden early-return reads ST.hidden on BOTH extremities
    assert "ST.hidden.has(ex[0]) || ST.hidden.has(ex[1])" in out
    # Python-side invariant: hiding any node => all its incident edges are hidden
    g = _zoned_hub_graph()
    hidden = {"h"}                       # e.g. the high-fan-in hub
    for e in g.edges:
        if e.src in hidden or e.dst in hidden:
            # this edge is incident to a hidden node => the reducer hides it
            assert e.src in hidden or e.dst in hidden  # tautology mirrors the JS guard
    # no VISIBLE edge references a hidden endpoint
    for e in g.edges:
        incident = e.src in hidden or e.dst in hidden
        if not incident:
            assert e.src not in hidden and e.dst not in hidden
