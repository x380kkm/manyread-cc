"""Tests for manyscan.lib.boundary — symbol-level plugin↔engine boundary.

Covers: classifier (path containment incl. normalization + autodetect), resolution
confidence (0/1/N → unresolved/unique/ambiguous, never silently picks one),
depth-1 engine sink (engine nodes present but not expanded), boundary set +
crossings, the two views, determinism, and render compound zones (backward compat).
"""
from __future__ import annotations

import json

from lib import boundary, render, stores
from lib.graph import Budget


def _build(st):
    z = boundary.make_zoning(st, None, None)
    budget = Budget(max_nodes=400, max_depth=2, direction="out")
    return z, boundary.build(st, z, budget, alias="t")


# --- classifier --------------------------------------------------------------
def test_detect_plugin_root(boundary_store):
    with stores.Store(boundary_store) as st:
        assert boundary.detect_plugin_root(st) == "plugin"
        assert boundary.has_module_markers(st) is True


def test_no_markers_autodetect_unsound(cpp_no_marker_store):
    """Real-index case: a cpp index has NO *.uplugin/*.Build.cs in `files`, so
    autodetect yields "" (whole repo = plugin) — which is UNSOUND. has_module_markers
    must report False so the CLI can refuse rather than misclassify the engine."""
    with stores.Store(cpp_no_marker_store) as st:
        assert boundary.has_module_markers(st) is False
        assert boundary.detect_plugin_root(st) == ""
        # The unsound consequence the guard prevents: with autodetect, the engine
        # symbol AActor would be classified PLUGIN (whole-repo zone).
        z = boundary.make_zoning(st, None, None)
        assert boundary.zone_of_path("Engine/Source/Actor.h", z) == boundary.PLUGIN


def test_make_zoning_override(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, "./other/", ["Engine\\Source", "engine"])
        assert z.plugin_root == "other"
        # engine roots normalized + sorted longest-first
        assert z.engine_roots == ("Engine/Source", "engine")


def test_zone_of_path():
    z = boundary.Zoning(plugin_root="plugin")
    assert boundary.zone_of_path("plugin/Foo.cpp", z) == boundary.PLUGIN
    assert boundary.zone_of_path("plugin", z) == boundary.PLUGIN
    assert boundary.zone_of_path(".\\plugin\\Bar.h", z) == boundary.PLUGIN  # normalization
    assert boundary.zone_of_path("engine/Core.h", z) == boundary.ENGINE
    assert boundary.zone_of_path("pluginX/Foo.cpp", z) == boundary.ENGINE  # prefix not a dir boundary
    assert boundary.zone_of_path(None, z) == boundary.ENGINE
    # pr=="" => all plugin
    z0 = boundary.Zoning(plugin_root="")
    assert boundary.zone_of_path("anything/here.cpp", z0) == boundary.PLUGIN


def test_qualified_name(boundary_store):
    with stores.Store(boundary_store) as st:
        # top-level symbol (no parent) -> bare name; nested chain covered below
        assert boundary.qualified_name(st, 1) == "Foo"


def test_qualified_name_nested(tmp_path):
    _, mr_db = stores.manyread_lib()
    db = tmp_path / "m" / "source.db"
    db.parent.mkdir(parents=True)
    conn = mr_db.connect(db)
    mr_db.init_schema(conn)
    conn.execute("INSERT INTO files(id,path,ext,size,mtime,content) VALUES(1,'p/F.cpp','.cpp',0,0,'')")
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,start_byte,end_byte,parent_id) "
                 "VALUES(1,1,'Outer','class','cpp',1,1,0,1,NULL)")
    conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,start_byte,end_byte,parent_id) "
                 "VALUES(2,1,'Inner','class','cpp',2,2,0,1,1)")
    conn.commit()
    conn.close()
    with stores.Store(db) as st:
        assert boundary.qualified_name(st, 2) == "Outer::Inner"


# --- resolution confidence ---------------------------------------------------
def test_resolve_ambiguous_all_plugin_stays_internal(tmp_path):
    """A type defined in TWO plugin files (header def + fwd-decl) is ambiguous but
    DEFINITELY internal -> amb:<name> in the plugin zone, NOT ext: engine (so it
    never pollutes the engine API surface)."""
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, content in [(1, "plugin/a.h", "class Widget {};\nclass PDup {};\n"),
                               (2, "plugin/b.h", "class PDup {};\n")]:
        conn.execute("INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
                     (fid, path, ".h", len(content), content))
    for sid, fid, name, sl in [(1, 1, "Widget", 1), (2, 1, "PDup", 2), (3, 2, "PDup", 1)]:
        conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                     "start_byte,end_byte,parent_id) VALUES(?,?,?,'class','cpp',?,?,0,1,NULL)",
                     (sid, fid, name, sl, sl))
    conn.execute("INSERT INTO edges(id,file_id,src_symbol_id,dst_symbol_id,dst_name,relation) "
                 "VALUES(1,1,1,NULL,'PDup','uses_type')")
    conn.commit()
    conn.close()
    with stores.Store(db_path) as st:
        z = boundary.make_zoning(st, "plugin", [])
        row = boundary.out_edges(st, 1)[0]
        r = boundary.resolve_target(st, row, z)
        assert r.confidence == "ambiguous" and r.ambiguity == 2
        assert r.target_id == "amb:PDup"                  # NOT ext:
        assert r.node.attrs["zone"] == boundary.PLUGIN    # stays internal, off engine surface


def test_resolve_target(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, None, None)
        rows = {r["id"]: r for r in st.conn.execute(
            "SELECT id,src_symbol_id,dst_symbol_id,dst_name,relation FROM edges").fetchall()}
        # edge 1: extends, dst_symbol_id set -> direct
        r = boundary.resolve_target(st, rows[1], z)
        assert r.confidence == "direct" and r.target_id == "s2" and r.ambiguity == 0
        # edge 2: implements Core, 1 candidate -> unique
        r = boundary.resolve_target(st, rows[2], z)
        assert r.confidence == "unique" and r.target_id == "s3" and r.ambiguity == 1
        # edge 3: uses_type Missing, 0 candidates -> unresolved external
        r = boundary.resolve_target(st, rows[3], z)
        assert r.confidence == "unresolved" and r.target_id == "ext:Missing" and r.ambiguity == 0
        # edge 4: uses_type Dup, 2 candidates -> ambiguous external (NEVER picks one)
        r = boundary.resolve_target(st, rows[4], z)
        assert r.confidence == "ambiguous" and r.target_id == "ext:Dup" and r.ambiguity == 2
        assert r.node.attrs["ambiguity"] == 2
        assert not r.target_id.startswith("s")  # never a symbol id


def test_external_node():
    n = boundary.external_node("UObject")
    assert n.id == "ext:UObject" and n.kind == "external" and n.label == "UObject"
    assert n.attrs["zone"] == boundary.ENGINE and n.attrs["unresolved"] is True
    n2 = boundary.external_node("Dup", 2)
    assert n2.attrs["ambiguity"] == 2


# --- depth-1 engine sink -----------------------------------------------------
def test_build_depth1_sink(boundary_store):
    with stores.Store(boundary_store) as st:
        z, g = _build(st)
        # plugin symbol present
        assert "s1" in g.nodes and g.nodes["s1"].attrs["zone"] == boundary.PLUGIN
        # engine targets present: Actor (s2), Core (s3), ext:Missing, ext:Dup
        assert "s2" in g.nodes and g.nodes["s2"].attrs["zone"] == boundary.ENGINE
        assert "s3" in g.nodes and g.nodes["s3"].attrs["zone"] == boundary.ENGINE
        assert "ext:Missing" in g.nodes and "ext:Dup" in g.nodes
        # ENGINE nodes are SINKS: no out-edges from any engine-zone / ext node
        for nid, node in g.nodes.items():
            if node.attrs.get("zone") == boundary.ENGINE:
                assert g.out_edges(nid) == [], f"engine node {nid} was expanded"
        assert len(g) <= 400


def test_build_confidence_recorded(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        conf = g.edge_confidence
        assert conf[("s1", "s2", "extends")] == "direct"
        assert conf[("s1", "s3", "implements")] == "unique"
        assert conf[("s1", "ext:Missing", "uses_type")] == "unresolved"
        assert conf[("s1", "ext:Dup", "uses_type")] == "ambiguous"


# --- views -------------------------------------------------------------------
def test_internal_view(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        iv = boundary.internal_view(g)
        assert set(iv.nodes) == {"s1"}  # only the plugin symbol
        assert all(iv.nodes[n].attrs["zone"] == boundary.PLUGIN for n in iv.nodes)
        assert iv.edges == []  # no plugin->plugin edges in this fixture


def test_engine_surface(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.engine_surface(g, rollup_modules=False)
        # bipartite: s1 (plugin boundary) -> engine targets
        assert "s1" in es.nodes
        engine = {n for n in es.nodes if es.nodes[n].attrs["zone"] == boundary.ENGINE}
        assert engine == {"s2", "s3", "ext:Missing", "ext:Dup"}
        assert all(e.src == "s1" for e in es.edges)


def test_engine_surface_rollup(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.engine_surface(g, rollup_modules=True, store=st)
        # the *.uplugin under plugin/ is the only module marker, so engine symbols
        # (s2,s3) under engine/ roll into the "(root)" module group.
        engine_groups = sorted(n for n in es.nodes if n.startswith("engine:"))
        assert engine_groups  # at least one grouped engine node
        assert all(e.src == "s1" for e in es.edges)


def test_crossings(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        cs = boundary.crossings(g)
        # sorted by (src,dst,relation)
        assert cs == sorted(cs, key=lambda c: (c.src, c.dst, c.relation))
        by_dst = {c.dst: c for c in cs}
        assert by_dst["s2"].confidence == "direct" and by_dst["s2"].relation == "extends"
        assert by_dst["s3"].confidence == "unique"
        assert by_dst["ext:Missing"].confidence == "unresolved"
        assert by_dst["ext:Dup"].confidence == "ambiguous"
        # evidence is plugin-side path:line
        assert all(c.evidence.startswith("plugin/Foo.cpp") for c in cs)


# --- determinism -------------------------------------------------------------
def test_determinism(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, None, None)
        b = Budget(max_nodes=400, max_depth=2, direction="out")
        a = render.to_json(boundary.build(st, z, b, alias="t"))
    with stores.Store(boundary_store) as st2:
        z2 = boundary.make_zoning(st2, None, None)
        b2 = Budget(max_nodes=400, max_depth=2, direction="out")
        c = render.to_json(boundary.build(st2, z2, b2, alias="t"))
    assert a == c


# --- render compound zones (backward compatible) -----------------------------
def _data_payload(html: str) -> str:
    """Extract just the injected ``const DATA=[...];`` JSON array (not the inlined lib)."""
    marker = "const DATA="
    start = html.index(marker) + len(marker)
    end = html.index(";\n", start)
    return html[start:end]


def test_render_compound(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        html = render.to_html(g)
    payload = _data_payload(html)
    assert "__zone_plugin__" in payload
    assert "__zone_engine__" in payload
    assert '"parent"' in payload  # real nodes carry a cytoscape parent
    # confidence reaches the elements as edge data
    assert '"conf"' in payload
    # plugin box injected before engine box (deterministic order)
    assert payload.index("__zone_plugin__") < payload.index("__zone_engine__")


def test_render_no_zone_unchanged(synth_store):
    """A plain (no-zone) graph must render with NO compound parent injection."""
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        html = render.to_html(g)
    payload = _data_payload(html)
    assert "__zone_" not in payload
    assert '"parent"' not in payload


def test_render_no_zone_byte_compat(synth_store):
    """to_json of a plain slice is unaffected by the boundary additions."""
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        d = json.loads(render.to_json(g))
    assert "nodes" in d and "edges" in d and "bounded" in d


# --- CLI soundness guard (autodetect refusal) --------------------------------
def test_cli_refuses_unsound_autodetect(cpp_no_marker_store, capsys):
    """plugin-boundary must REFUSE (exit 2) when no markers are indexed and no
    --plugin-root is given, instead of silently classifying the engine as plugin."""
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(cpp_no_marker_store), "--format", "json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--plugin-root" in err


def test_cli_explicit_plugin_root_runs(cpp_no_marker_store, capsys):
    """With an explicit --plugin-root the same store scans fine (guard not tripped),
    and the engine symbol is correctly classified ENGINE (not plugin)."""
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(cpp_no_marker_store),
                    "--plugin-root", "MyPlugin", "--engine-root", "Engine",
                    "--view", "engine", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    assert "s1" in ids       # plugin Foo
    assert "s2" in ids       # engine AActor present as a depth-1 sink target


def test_cli_empty_plugin_root_opts_in(cpp_no_marker_store, capsys):
    """--plugin-root \"\" is an explicit opt-in to whole-repo=plugin (guard not tripped)."""
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(cpp_no_marker_store),
                    "--plugin-root", "", "--view", "internal", "--format", "json"])
    assert rc == 0


# --- engine-surface rollup determinism (set→sorted by (len,str)) -------------
def test_engine_surface_rollup_deterministic(boundary_store):
    """The rollup module ordering must be total-ordered (len,str), so the grouped
    surface is byte-identical run to run regardless of set/hash-seed iteration."""
    outs = []
    for _ in range(3):
        with stores.Store(boundary_store) as st:
            _, g = _build(st)
            outs.append(render.to_json(boundary.engine_surface(g, rollup_modules=True, store=st)))
    assert outs[0] == outs[1] == outs[2]


def test_cli_html_is_one_page_with_toggle(boundary_store, capsys):
    """plugin-boundary --format html emits ONE self-contained page with the in-page
    view toggle (regardless of --view), not the projected internal/engine subgraph."""
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(boundary_store),
                    "--plugin-root", "plugin", "--view", "internal", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!doctype html>")
    assert "id='view'" in out                  # in-page toggle present
    assert "<option value='internal' selected>" in out  # --view threaded as initial
    # full graph emitted (engine nodes present even though --view internal): the
    # projection is now client-side, so engine symbols are still in the page.
    assert "__zone_engine__" in out and "__zone_plugin__" in out


def test_roots_by_len_total_order(module_store):
    """rollup.roots_by_len ties on length break lexicographically (deterministic)."""
    from lib import rollup
    with stores.Store(module_store) as st:
        roots = rollup.roots_by_len(st)
        # modA and modB are equal length -> must be in (-len, str) order
        assert roots == sorted(roots, key=lambda r: (-len(r), r))
