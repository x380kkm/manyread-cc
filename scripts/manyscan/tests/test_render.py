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
    # cytoscape lib inlined from the vendored asset (offline, single file)
    assert "cytoscape" in out and len(out) > 300_000
    assert "name:'cose'" in out          # force-directed layout
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


def test_srp_text_reports_modules():
    from lib.graph import Edge as E, Node as N
    g = Graph()
    for nid in ["mod/a", "mod/b", "mod/c", "mod/d"]:
        g.add_node(N(nid, "file", label=nid))
    g.add_edge(E("mod/a", "mod/b", "imports"))
    g.add_edge(E("mod/c", "mod/d", "imports"))
    reports = analyze.srp(g, lambda n: n.id.split("/")[0])
    txt = render.srp_text(reports)
    assert "multi-responsibility" in txt and "K=2" in txt
    assert "cluster#0" in txt and "cluster#1" in txt


def test_render_unknown_format_raises():
    try:
        render.render(_slice(), "yaml")
        assert False
    except ValueError:
        pass
