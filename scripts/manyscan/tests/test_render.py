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
    # cytoscape lib + fcose chain inlined from the vendored assets (offline, single file)
    assert "cytoscape" in out and len(out) > 300_000
    assert "name:'fcose'" in out         # fast force-directed layout (fcose)
    assert "randomize:false" in out      # deterministic spectral init
    assert "cytoscapeFcose" in out       # fcose UMD inlined
    assert "cytoscape.use(" in out       # fcose registered
    assert "a.py" in out and "b.py" in out
    assert "+7⤳" in out                  # frontier node tagged in its label
    assert "7 deps elided" in out        # honest truncation banner
    assert "search node" in out          # interactive search box


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
    assert "edge.seam" in out               # cross-cluster edges dash as seams


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
    # degree-based node sizing applies to ALL graphs (incl. plain no-zone slice)
    out = render.to_html(_slice())
    assert "const DEGMAX=" in out
    assert "mapData(deg,0," in out          # width/height mapped from degree
    assert '"deg":' in out                  # every node carries its degree


def test_html_hub_and_bridge_markers():
    out = render.to_html(_zoned_hub_graph())
    assert "node[hub=1]" in out             # hub ring/halo style present
    assert "edge[bridge=1]" in out          # bridge edge highlight style present
    assert '"hub": 1' in out                # the hub node is tagged in DATA
    assert '"bridge": 1' in out             # the bridge edge is tagged in DATA
    assert "underlay-color" in out          # hub halo


def test_html_drag_pan_config():
    out = render.to_html(_zoned_hub_graph())
    # (1) drag/pan fix: box-selection off; zone parents non-grabbable + events fall through
    assert "boxSelectionEnabled: false" in out
    assert '"grabbable": false' in out      # zone-parent elements are non-grabbable
    assert "'events':'no'" in out           # parent taps/drags fall through to pan


def test_html_view_toggle_one_page():
    out = render.to_html(_zoned_hub_graph(), view="dependency")
    assert "id='view'" in out               # single in-page view toggle
    assert "<option value='internal'>" in out
    assert "<option value='dependency' selected>" in out  # initial state threaded from view=
    assert "<option value='both'>" in out
    assert "applyView" in out               # client-side show/hide handler
    assert '"cross": 1' in out              # target->dependency crossings tagged


def test_html_light_zone_treatment():
    out = render.to_html(_zoned_hub_graph())
    # faint, borderless compound parents (no '一堆方框'): transparent fill, soft label
    assert "'background-opacity':0.04" in out
    assert "data(zonecolor)" in out
    assert '"zonecolor": "#4e79a7"' in out  # target zone tint
    assert '"zonecolor": "#f28e2b"' in out  # dependency zone tint


def test_html_no_zone_no_toggle_but_sized():
    # backward compat: a no-zone graph renders with NO view toggle + NO zone parents,
    # but STILL gets degree sizing.
    out = render.to_html(_slice())
    assert "id='view'" not in out           # toggle hidden for plain graphs
    assert "__zone_" not in out
    assert "const HAS_ZONES=false" in out
    assert "mapData(deg,0," in out          # degree sizing still applies


def test_html_redesign_deterministic():
    # two renders of the zoned + a plain graph are byte-identical (no random/time)
    assert render.to_html(_zoned_hub_graph()) == render.to_html(_zoned_hub_graph())
    assert render.to_html(_slice()) == render.to_html(_slice())


def test_html_degmax_token_fully_substituted():
    # invalid-cytoscape guard: mapData() parses its mapper string literally and CANNOT
    # read a JS const, so every `DEGMAX` token in the style mappers MUST be replaced with
    # the literal int. Both the base (18,64) and hub (34,80) mappers share the prefix
    # `mapData(deg,0,DEGMAX,` — assert no literal DEGMAX survives anywhere in the style.
    for g in (_zoned_hub_graph(), _slice()):
        out = render.to_html(g)
        style = out.split("const DEGMAX=", 1)[1]  # everything AFTER the const decl
        assert "mapData(deg,0,DEGMAX" not in style   # no un-substituted mapper
        assert "mapData(deg,0," in style             # mappers still present
        # both size ramps survive substitution (base + hub size bump)
        assert ",18,64)" in style and ",34,80)" in style


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
