"""Tests for manyscan.lib.boundary — symbol-level target↔dependency boundary.

Covers: classifier (path containment incl. normalization + autodetect), resolution
confidence (0/1/N → unresolved/unique/ambiguous, never silently picks one),
depth-1 dependency sink (dependency nodes present but not expanded), boundary set +
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
def test_detect_target_root(boundary_store):
    with stores.Store(boundary_store) as st:
        assert boundary.detect_target_root(st) == "plugin"
        assert boundary.has_module_markers(st) is True


def test_no_markers_autodetect_unsound(cpp_no_marker_store):
    """Real-index case: a cpp index has NO *.uplugin/*.Build.cs in `files`, so
    autodetect yields "" (whole repo = target) — which is UNSOUND. has_module_markers
    must report False so the CLI can refuse rather than misclassify the dependencies."""
    with stores.Store(cpp_no_marker_store) as st:
        assert boundary.has_module_markers(st) is False
        assert boundary.detect_target_root(st) == ""
        # The unsound consequence the guard prevents: with autodetect, the dependency
        # symbol AActor would be classified TARGET (whole-repo zone).
        z = boundary.make_zoning(st, None, None)
        assert boundary.zone_of_path("Engine/Source/Actor.h", z) == boundary.TARGET


def test_make_zoning_override(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, "./other/", ["Engine\\Source", "engine"])
        assert z.target_root == "other"
        # dependency roots normalized + sorted longest-first
        assert z.dep_roots == ("Engine/Source", "engine")


def test_zone_of_path():
    z = boundary.Zoning(target_root="plugin")
    assert boundary.zone_of_path("plugin/Foo.cpp", z) == boundary.TARGET
    assert boundary.zone_of_path("plugin", z) == boundary.TARGET
    assert boundary.zone_of_path(".\\plugin\\Bar.h", z) == boundary.TARGET  # normalization
    assert boundary.zone_of_path("engine/Core.h", z) == boundary.DEPENDENCY
    assert boundary.zone_of_path("pluginX/Foo.cpp", z) == boundary.DEPENDENCY  # prefix not a dir boundary
    assert boundary.zone_of_path(None, z) == boundary.DEPENDENCY
    # pr=="" => all target
    z0 = boundary.Zoning(target_root="")
    assert boundary.zone_of_path("anything/here.cpp", z0) == boundary.TARGET


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
def test_resolve_ambiguous_all_target_stays_internal(tmp_path):
    """A type defined in TWO target files (header def + fwd-decl) is ambiguous but
    DEFINITELY internal -> amb:<name> in the target zone, NOT dep: dependency (so it
    never pollutes the dependency API surface)."""
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
        assert r.target_id == "amb:PDup"                  # NOT dep:
        assert r.node.attrs["zone"] == boundary.TARGET    # stays internal, off dependency surface


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
        # edge 3: uses_type Missing, 0 candidates -> unresolved external (dependency)
        r = boundary.resolve_target(st, rows[3], z)
        assert r.confidence == "unresolved" and r.target_id == "dep:Missing" and r.ambiguity == 0
        # edge 4: uses_type Dup, 2 candidates -> ambiguous external (NEVER picks one)
        r = boundary.resolve_target(st, rows[4], z)
        assert r.confidence == "ambiguous" and r.target_id == "dep:Dup" and r.ambiguity == 2
        assert r.node.attrs["ambiguity"] == 2
        assert not r.target_id.startswith("s")  # never a symbol id


def test_external_node():
    n = boundary.external_node("UObject")
    assert n.id == "dep:UObject" and n.kind == "external" and n.label == "UObject"
    assert n.attrs["zone"] == boundary.DEPENDENCY and n.attrs["unresolved"] is True
    n2 = boundary.external_node("Dup", 2)
    assert n2.attrs["ambiguity"] == 2


# --- depth-1 dependency sink -------------------------------------------------
def test_build_depth1_sink(boundary_store):
    with stores.Store(boundary_store) as st:
        z, g = _build(st)
        # target symbol present
        assert "s1" in g.nodes and g.nodes["s1"].attrs["zone"] == boundary.TARGET
        # dependency targets present: Actor (s2), Core (s3), dep:Missing, dep:Dup
        assert "s2" in g.nodes and g.nodes["s2"].attrs["zone"] == boundary.DEPENDENCY
        assert "s3" in g.nodes and g.nodes["s3"].attrs["zone"] == boundary.DEPENDENCY
        assert "dep:Missing" in g.nodes and "dep:Dup" in g.nodes
        # DEPENDENCY nodes are SINKS: no out-edges from any dependency-zone / dep node
        for nid, node in g.nodes.items():
            if node.attrs.get("zone") == boundary.DEPENDENCY:
                assert g.out_edges(nid) == [], f"dependency node {nid} was expanded"
        assert len(g) <= 400


def test_build_confidence_recorded(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        conf = g.edge_confidence
        assert conf[("s1", "s2", "extends")] == "direct"
        assert conf[("s1", "s3", "implements")] == "unique"
        assert conf[("s1", "dep:Missing", "uses_type")] == "unresolved"
        assert conf[("s1", "dep:Dup", "uses_type")] == "ambiguous"


# --- views -------------------------------------------------------------------
def test_internal_view(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        iv = boundary.internal_view(g)
        assert set(iv.nodes) == {"s1"}  # only the target symbol
        assert all(iv.nodes[n].attrs["zone"] == boundary.TARGET for n in iv.nodes)
        assert iv.edges == []  # no target->target edges in this fixture


def test_dependency_surface(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.dependency_surface(g, rollup_modules=False)
        # bipartite: s1 (target boundary) -> dependency targets
        assert "s1" in es.nodes
        dep = {n for n in es.nodes if es.nodes[n].attrs["zone"] == boundary.DEPENDENCY}
        assert dep == {"s2", "s3", "dep:Missing", "dep:Dup"}
        assert all(e.src == "s1" for e in es.edges)


def test_dependency_surface_rollup(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.dependency_surface(g, rollup_modules=True, store=st)
        # the *.uplugin under plugin/ is the only module marker, so dependency symbols
        # (s2,s3) under engine/ roll into the "(root)" module group.
        dep_groups = sorted(n for n in es.nodes if n.startswith("dep:"))
        assert dep_groups  # at least one grouped dependency node
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
        assert by_dst["dep:Missing"].confidence == "unresolved"
        assert by_dst["dep:Dup"].confidence == "ambiguous"
        # evidence is target-side path:line
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


# --- render zone encoding (sigma: color + spatial, no compound parents) -------
def _data_payload(html: str) -> str:
    """Extract just the injected ``const DATA={...};`` JSON object (not the inlined lib)."""
    marker = "const DATA="
    start = html.index(marker) + len(marker)
    end = html.index(";\n", start)
    return html[start:end]


def test_render_zone_encoding(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        html = render.to_html(g)
    payload = _data_payload(html)
    # sigma encodes zones as node attrs + color (no '__zone_*__' compound parents)
    assert "__zone_" not in payload
    assert '"zone": "target"' in payload
    assert '"zone": "dependency"' in payload
    assert '"color": "#4e79a7"' in payload      # target tint
    assert '"color": "#f28e2b"' in payload      # dependency tint
    # confidence reaches the edges as attrs
    assert '"conf"' in payload


def test_render_no_zone_unchanged(synth_store):
    """A plain (no-zone) graph must render with NO zone attrs / pseudo-nodes."""
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        html = render.to_html(g)
    payload = _data_payload(html)
    assert "__zone_" not in payload
    assert '"zone":' not in payload
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
    """boundary must REFUSE (exit 2) when no markers are indexed and no
    --target-root is given, instead of silently classifying a dependency as target."""
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store), "--format", "json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--target-root" in err


def test_cli_explicit_target_root_runs(cpp_no_marker_store, capsys):
    """With an explicit --target-root the same store scans fine (guard not tripped),
    and the dependency symbol is correctly classified DEPENDENCY (not target)."""
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store),
                    "--target-root", "MyPlugin", "--dep-root", "Engine",
                    "--view", "dependency", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    assert "s1" in ids       # target Foo
    assert "s2" in ids       # dependency AActor present as a depth-1 sink target


def test_cli_empty_target_root_opts_in(cpp_no_marker_store, capsys):
    """--target-root \"\" is an explicit opt-in to whole-repo=target (guard not tripped)."""
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store),
                    "--target-root", "", "--view", "internal", "--format", "json"])
    assert rc == 0


def test_cli_backcompat_plugin_boundary_and_flags(cpp_no_marker_store, capsys):
    """Back-compat: the old `plugin-boundary` subcommand + `--plugin-root`/`--engine-root`
    flags still work, mapping onto the new target/dependency dests."""
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(cpp_no_marker_store),
                    "--plugin-root", "MyPlugin", "--engine-root", "Engine",
                    "--view", "dependency", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    assert "s1" in ids and "s2" in ids


# --- dependency-surface rollup determinism (set→sorted by (len,str)) ---------
def test_dependency_surface_rollup_deterministic(boundary_store):
    """The rollup module ordering must be total-ordered (len,str), so the grouped
    surface is byte-identical run to run regardless of set/hash-seed iteration."""
    outs = []
    for _ in range(3):
        with stores.Store(boundary_store) as st:
            _, g = _build(st)
            outs.append(render.to_json(boundary.dependency_surface(g, rollup_modules=True, store=st)))
    assert outs[0] == outs[1] == outs[2]


def test_cli_html_is_one_page_with_toggle(boundary_store, capsys):
    """boundary --format html emits ONE self-contained page with the in-page
    view toggle (regardless of --view), not the projected internal/dependency subgraph."""
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store),
                    "--target-root", "plugin", "--view", "internal", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!doctype html>")
    assert "id='view'" in out                  # in-page toggle present
    assert "<option value='internal' selected>" in out  # --view threaded as initial
    # full graph emitted (dependency nodes present even though --view internal): the
    # projection is now client-side, so dependency symbols are still in the page.
    assert '"zone": "dependency"' in out and '"zone": "target"' in out


# --- view-hide config: default-hidden bake + --ignore CLI wiring -----------------
def test_default_hidden_keys_sorted_deterministic():
    """scan._default_hidden_keys returns a SORTED, deterministic list, hitting both
    name matches and high-fan_in nodes via the reused render._importance metric."""
    import scan
    from lib import render
    from test_render import _zoned_hub_graph
    g = _zoned_hub_graph()
    # 'Hub' is the label of node id 'h' (fan_in=3); min_fan_in=3 also catches it.
    keys = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    assert keys == sorted(keys)                     # SORTED
    assert "h" in keys                              # Hub matched by name AND fan_in
    again = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    assert keys == again                            # deterministic
    # the baked HIDDEN const reflects the SORTED list
    out = render.to_html(g, default_hidden=keys)
    assert "const HIDDEN=" + json_dumps_sorted(keys) in out


def json_dumps_sorted(keys):
    import json
    return json.dumps(sorted(keys))


def test_default_hidden_keys_segment_and_pattern(tmp_path):
    """label-OR-trailing-segment matching + fnmatch patterns (union semantics)."""
    import scan
    from lib.graph import Edge, Graph, Node
    g = Graph()
    g.add_node(Node("amb:FString", "ambiguous", label="FString"))         # bare-name external
    g.add_node(Node("s9", "class", label="Outer::Inner::FString"))        # qualified internal
    g.add_node(Node("dep:TArrayView", "external", label="TArrayView"))    # pattern target
    g.add_node(Node("s10", "class", label="Keep"))                        # untouched
    g.add_edge(Edge("s10", "amb:FString", "uses_type"))
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "patterns": ["TArray*"]})
    assert "amb:FString" in keys          # bare label match
    assert "s9" in keys                   # trailing-segment match on qualified label
    assert "dep:TArrayView" in keys       # fnmatch pattern
    assert "s10" not in keys              # not matched


def test_default_hidden_keys_engine_side_only():
    """In a boundary (zoned) graph view_hide is ENGINE-SIDE ONLY: it never default-hides
    a target/internal symbol — not by a name it shares with a dependency, nor by fan_in."""
    import scan
    from lib.graph import Edge, Graph, Node
    g = Graph()
    g.add_node(Node("dep:FString", "external", label="FString", attrs={"zone": "dependency"}))
    g.add_node(Node("s1", "class", label="FString", attrs={"zone": "target"}))   # SAME name, target side
    g.add_node(Node("hub", "class", label="Hub", attrs={"zone": "target"}))      # high-fan_in TARGET hub
    for s in ("a", "b", "c"):
        g.add_node(Node(s, "class", label=s, attrs={"zone": "target"}))
        g.add_edge(Edge(s, "hub", "uses_type"))                                  # hub fan_in = 3
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "min_fan_in": 2})
    assert "dep:FString" in keys      # dependency-side name match -> hidden
    assert "s1" not in keys           # same name on TARGET side -> protected
    assert "hub" not in keys          # high-fan_in TARGET hub -> protected (min_fan_in is dep-only)


def test_cli_boundary_ignore_bakes_hidden(boundary_store, capsys, tmp_path):
    """boundary --ignore <bare file> bakes the matched ids into a SORTED const HIDDEN."""
    import scan
    ig = tmp_path / "ignore.json"
    ig.write_text('{"names": ["Foo"]}', encoding="utf-8")    # target symbol Foo (id s1)
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "const HIDDEN=" in out and '"s1"' in out          # Foo -> s1 default-hidden


def test_cli_boundary_no_config_byte_identical(boundary_store, capsys):
    """No --ignore + no committed view_hide => identical to v0.6.0 (no HIDDEN line),
    and two renders are byte-identical."""
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    a = capsys.readouterr().out
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    b = capsys.readouterr().out
    assert a == b
    # the gated HIDDEN line is absent from the consts block (byte-compat to v0.6.0)
    consts = a[a.index("const DATA="):a.index('<script id="ms-boot">')]
    assert "const HIDDEN=" not in consts


def test_cli_boundary_committed_view_hide_autodiscovered(boundary_store, capsys):
    """A committed manyread.json['view_hide'] is auto-discovered at render time (no flag)."""
    import json as _json

    import scan
    store_dir = boundary_store.parent                      # <tmp>/manyread/
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "languages": [], "view_hide": {"names": ["Foo"]}}),
        encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "const HIDDEN=" in out and '"s1"' in out        # Foo auto-hidden via committed key


def test_cli_boundary_ignore_overrides_committed(boundary_store, capsys, tmp_path):
    """--ignore precedence over a committed view_hide key (different match)."""
    import json as _json

    import scan
    store_dir = boundary_store.parent
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "view_hide": {"names": ["Foo"]}}), encoding="utf-8")
    ig = tmp_path / "ig.json"
    ig.write_text('{"view_hide": {"names": ["Core"]}}', encoding="utf-8")   # Core -> s3
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    consts = out[out.index("const HIDDEN="):out.index('<script id="ms-boot">')]
    assert '"s3"' in consts          # Core hidden (from --ignore)
    assert '"s1"' not in consts      # Foo NOT hidden (committed key overridden)


def test_roots_by_len_total_order(module_store):
    """rollup.roots_by_len ties on length break lexicographically (deterministic)."""
    from lib import rollup
    with stores.Store(module_store) as st:
        roots = rollup.roots_by_len(st)
        # modA and modB are equal length -> must be in (-len, str) order
        assert roots == sorted(roots, key=lambda r: (-len(r), r))


# --- N-band layering: assign_bands correctness + dep-depth-2 population ----------
def _mk_store(tmp_path, files, syms, edges):
    """Build a tiny real-schema store from (files, syms, edges) literals."""
    _, mr_db = stores.manyread_lib()
    store = tmp_path / "manyread"
    store.mkdir(parents=True)
    db_path = store / "source.db"
    conn = mr_db.connect(db_path)
    mr_db.init_schema(conn)
    for fid, path, ext, content in files:
        conn.execute("INSERT INTO files(id,path,ext,size,mtime,content) VALUES(?,?,?,?,0,?)",
                     (fid, path, ext, len(content), content))
        conn.execute("INSERT INTO files_fts(rowid,path,content) VALUES(?,?,?)", (fid, path, content))
    for sid, fid, name, kind, sl, el, parent in syms:
        conn.execute("INSERT INTO symbols(id,file_id,name,kind,lang,start_line,end_line,"
                     "start_byte,end_byte,parent_id) VALUES(?,?,?,?, 'cpp',?,?,0,1,?)",
                     (sid, fid, name, kind, sl, el, parent))
    for eid, fid, src, dst, dname, rel in edges:
        conn.execute("INSERT INTO edges(id,file_id,src_symbol_id,dst_symbol_id,dst_name,relation) "
                     "VALUES(?,?,?,?,?,?)", (eid, fid, src, dst, dname, rel))
    conn.commit()
    conn.close()
    return db_path


def test_assign_bands_flat_two_four(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        # flat -> all band 0, no boxes
        bf, mf = boundary.assign_bands(g, "flat")
        assert set(bf.values()) == {0} and mf == []
        # two -> target ids band 0, dependency ids band 1, 2-entry meta
        bt, mt = boundary.assign_bands(g, "two")
        assert bt["s1"] == 0                       # target Foo
        for nid in ("s2", "s3", "dep:Missing", "dep:Dup"):
            assert bt[nid] == 1                    # dependencies
        assert mt == [{"band": 0, "label": "target"}, {"band": 1, "label": "dependency"}]
        # four -> s1 is target-iface (1) since it has crossing edges; deps are dep-iface (2)
        bq, mq = boundary.assign_bands(g, "four")
        assert bq["s1"] == boundary.TARGET_IFACE   # in boundary_nodes(g)
        for nid in ("s2", "s3", "dep:Missing", "dep:Dup"):
            assert bq[nid] == boundary.DEP_IFACE   # surface at depth-1
        assert [m["label"] for m in mq] == ["target-core", "target-iface", "dep-iface", "dep-core"]


def test_assign_bands_no_zone_falls_back_to_flat(synth_store):
    """A plain (no-zone) slice falls back to flat without raising."""
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        for layers in ("flat", "two", "four"):
            bo, bm = boundary.assign_bands(g, layers)
            assert set(bo.values()) == {0}
            assert bm == []                        # no boxes for a no-zone graph


def test_assign_bands_deterministic(boundary_store):
    """assign_bands('four') band_of is byte-stable across calls (sorted consumer loop)."""
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        a = boundary.assign_bands(g, "four")
        b = boundary.assign_bands(g, "four")
        c = boundary.assign_bands(g, "four")
        assert a == b == c
        assert list(a[0].keys()) == sorted(a[0].keys())  # insertion order is sorted


def test_target_core_vs_iface_split(tmp_path):
    """A target with a crossing edge -> target-iface (1); a target with only
    target->target edges -> target-core (0)."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/A.cpp", ".cpp", "x"),
             (3, "plugin/B.cpp", ".cpp", "x"),
             (4, "engine/Dep.h", ".h", "x")]
    # A (s1) uses B (s2, target) AND Dep (s3, dependency) -> A is iface.
    # B (s2) only used by A; B has no outgoing edge -> B is target-core.
    syms = [(1, 2, "A", "class", 1, 1, None),
            (2, 3, "B", "class", 1, 1, None),
            (3, 4, "Dep", "class", 1, 1, None)]
    edges = [(1, 2, 1, 2, None, "uses_type"),     # A -> B (target->target)
             (2, 2, 1, 3, None, "uses_type")]      # A -> Dep (target->dependency, crossing)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        bo, _ = boundary.assign_bands(g, "four")
        assert bo["s1"] == boundary.TARGET_IFACE   # A has a crossing edge
        assert bo["s2"] == boundary.TARGET_CORE    # B is insulated
        assert bo["s3"] == boundary.DEP_IFACE      # Dep is the surface


def test_dep_depth_2_populates_dep_core(tmp_path):
    """A surface dep symbol that ITSELF references another dep symbol: at depth-1 the
    second dep is a sink (absent); at depth-2 it appears, carries dep_core, lands band 3."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Surface.h", ".h", "x"),
             (4, "engine/Behind.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None),     # target
            (2, 3, "Surface", "class", 1, 1, None),  # dependency surface (s-id)
            (3, 4, "Behind", "class", 1, 1, None)]   # dependency behind the surface
    edges = [(1, 2, 1, 2, None, "uses_type"),        # Foo -> Surface  (depth-1)
             (2, 3, 2, 3, None, "uses_type")]         # Surface -> Behind (depth-2)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        b = Budget(max_nodes=400, max_depth=2, direction="out")
        # depth-1: Behind (s3) is a sink -> not present
        g1 = boundary.build(st, z, b, dep_depth=1)
        assert "s3" not in g1.nodes
        bo1, _ = boundary.assign_bands(g1, "four")
        assert 3 not in bo1.values()                 # dep-core band empty at depth-1
        # depth-2: Behind present, marked dep_core, lands dep-core band; Surface stays dep-iface
        g2 = boundary.build(st, z, b, dep_depth=2)
        assert "s3" in g2.nodes
        assert g2.nodes["s3"].attrs.get("dep_core") == 1
        assert g2.nodes["s3"].attrs.get("dep_depth") == 2
        bo2, _ = boundary.assign_bands(g2, "four")
        assert bo2["s2"] == boundary.DEP_IFACE       # Surface stays the API surface
        assert bo2["s3"] == boundary.DEP_CORE        # Behind is behind it


def test_dep_core_mislabel_guard(tmp_path):
    """A dep symbol referenced by BOTH a target (depth-1) AND another dep must stay
    dep-iface (band 2), NOT dep-core — it was first added at depth-1, so the depth-2
    pass never re-adds nor marks it."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Shared.h", ".h", "x"),
             (4, "engine/Surface.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None),       # target
            (2, 4, "Surface", "class", 1, 1, None),    # dependency surface
            (3, 3, "Shared", "class", 1, 1, None)]      # referenced by BOTH Foo and Surface
    edges = [(1, 2, 1, 3, None, "uses_type"),          # Foo -> Shared    (depth-1)
             (2, 2, 1, 2, None, "uses_type"),          # Foo -> Surface   (depth-1)
             (3, 4, 2, 3, None, "uses_type")]           # Surface -> Shared (depth-2)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"),
                           dep_depth=2)
        # Shared (s3) was added at depth-1 (Foo references it) -> NOT re-added/marked at depth-2
        assert g.nodes["s3"].attrs.get("dep_core") is None
        bo, _ = boundary.assign_bands(g, "four")
        assert bo["s3"] == boundary.DEP_IFACE


def test_dep_depth_2_truncation_composes(tmp_path):
    """A low max-nodes that overflows DURING the depth-2 pass must set g.truncated +
    g.elided (depth-2 overflow is reported honestly, not silently dropped)."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Surface.h", ".h", "x"),
             (4, "engine/Behind.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None),
            (2, 3, "Surface", "class", 1, 1, None),
            (3, 4, "Behind", "class", 1, 1, None)]
    edges = [(1, 2, 1, 2, None, "uses_type"),        # Foo -> Surface
             (2, 3, 2, 3, None, "uses_type")]         # Surface -> Behind (depth-2)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        # cap=2 admits Foo + Surface; the depth-2 Behind node overflows.
        g = boundary.build(st, z, Budget(max_nodes=2, max_depth=2, direction="out"),
                           dep_depth=2)
        assert g.truncated is True and g.elided > 0
        assert "s3" not in g.nodes


def test_cli_layers_dep_depth_wiring(boundary_store, capsys):
    """boundary --layers four --dep-depth 2 --format html returns 0 and the html
    carries baked band attrs + the BANDS const; --layers flat emits const BANDS=[];
    and --format json is byte-identical with vs without --layers (bands inert)."""
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--layers", "four", "--dep-depth", "2", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"band":' in out and "const BANDS=[{" in out

    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--layers", "flat", "--format", "html"])
    assert rc == 0
    assert "const BANDS=[];" in capsys.readouterr().out

    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--layers", "four", "--format", "json"])
    assert rc == 0
    with_layers = capsys.readouterr().out
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "json"])
    assert rc == 0
    without_layers = capsys.readouterr().out
    assert with_layers == without_layers     # bands inert for non-html formats


# --- collapsible MODULE<->SYMBOL quotient: assign_modules + CLI --collapse --------
def test_assign_modules_determinism_and_ids(boundary_store):
    """assign_modules('file') is deterministic, side-prefixes ids, and routes path-less
    by-name deps to a per-side '(external)' bucket; modules_meta is sorted-by-id with
    integer members + a band/zone/color per entry."""
    with stores.Store(boundary_store) as st:
        z, g = _build(st)
        band_of, _ = boundary.assign_bands(g, "four")
        a = boundary.assign_modules(g, z, "file", st, band_of)
        b = boundary.assign_modules(g, z, "file", st, band_of)
        assert a == b                                  # deterministic
        module_of, meta = a
        # target Foo (s1, plugin/Foo.cpp) -> 'target:Foo' (side-prefixed file stem)
        assert module_of["s1"] == "target:Foo"
        # dependency symbols (s2 engine/Actor.h, s3 engine/Core.h) -> 'dependency:<module>'
        assert module_of["s2"].startswith("dependency:")
        assert module_of["s3"].startswith("dependency:")
        # by-name deps with no path -> '(external)'. In boundary_store both Dup candidates
        # are DEPENDENCY-zone, so Foo->Dup resolves to dep:Dup (not amb:), side dependency.
        assert module_of["dep:Missing"] == "dependency:(external)"
        assert module_of["dep:Dup"] == "dependency:(external)"
        # modules_meta sorted by id; each entry well-formed
        assert [m["id"] for m in meta] == sorted(m["id"] for m in meta)
        for m in meta:
            assert isinstance(m["members"], int) and m["members"] >= 1
            assert "band" in m and m["zone"] in ("target", "dependency")
            assert m["color"] in ("#4e79a7", "#f28e2b")
            assert m["side"] == m["id"].split(":", 1)[0]


def test_assign_modules_amb_external_target_side(tmp_path):
    """An amb:<name> node (all-target candidates, no path) maps to 'target:(external)'."""
    files = [(1, "plugin/a.h", ".h", "class W{};\nclass PDup{};\n"),
             (2, "plugin/b.h", ".h", "class PDup{};\n")]
    syms = [(1, 1, "W", "class", 1, 1, None), (2, 1, "PDup", "class", 2, 2, None),
            (3, 2, "PDup", "class", 1, 1, None)]
    edges = [(1, 1, 1, None, "PDup", "uses_type")]     # W -> PDup (2 target candidates => amb:)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", [])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        assert "amb:PDup" in g.nodes                   # the all-target ambiguous node
        module_of, _ = boundary.assign_modules(g, z, "file", st, None)
        assert module_of["amb:PDup"] == "target:(external)"


def test_assign_modules_dir_level(tmp_path):
    """level='dir' groups a target symbol by its parent directory."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Dep.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None), (2, 3, "Dep", "class", 1, 1, None)]
    edges = [(1, 2, 1, 2, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        module_of, _ = boundary.assign_modules(g, z, "dir", st, None)
        assert module_of["s1"] == "target:plugin"      # parent dir of plugin/Foo.cpp


def test_assign_modules_band_is_min_member(tmp_path):
    """A file split across target-core (band 0) and target-iface (band 1) collapses to
    the LOWER band (min member band)."""
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Same.cpp", ".cpp", "x"),       # both A,B live in ONE file => one module
             (3, "engine/Dep.h", ".h", "x")]
    # A (s1) has a crossing edge -> target-iface (band 1); B (s2) has none -> target-core (band 0).
    syms = [(1, 2, "A", "class", 1, 1, None),
            (2, 2, "B", "class", 2, 2, None),
            (3, 3, "Dep", "class", 1, 1, None)]
    edges = [(1, 2, 1, 3, None, "uses_type"),           # A -> Dep (crossing => A iface)
             (2, 2, 1, 2, None, "uses_type")]           # A -> B (target->target)
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        band_of, _ = boundary.assign_bands(g, "four")
        assert band_of["s1"] == boundary.TARGET_IFACE and band_of["s2"] == boundary.TARGET_CORE
        _, meta = boundary.assign_modules(g, z, "file", st, band_of)
        same = next(m for m in meta if m["id"] == "target:Same")
        assert same["band"] == 0                        # MIN of {0, 1}
        assert same["members"] == 2


def test_cli_collapse_off_equals_pre_flag(boundary_store, capsys):
    """boundary --format html with NO --collapse vs --collapse off vs the flag absent ->
    byte-identical (the gate); two renders also byte-identical."""
    import scan
    runs = []
    for extra in ([], ["--collapse", "off"]):
        rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                        "--format", "html", *extra])
        assert rc == 0
        runs.append(capsys.readouterr().out)
    assert runs[0] == runs[1]                            # off == flag-absent (byte-identical)


def test_collapse_md5_stable_each_level(boundary_store, capsys):
    """For each of off/file/dir, two html renders are byte-identical + md5 equal
    (positions are in-browser, so the emitted file carries no quotient coords)."""
    import hashlib

    import scan
    for lvl in ("off", "file", "dir"):
        outs = []
        for _ in range(2):
            rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                            "--format", "html", "--collapse", lvl])
            assert rc == 0
            outs.append(capsys.readouterr().out)
        assert outs[0] == outs[1]
        assert hashlib.md5(outs[0].encode()).hexdigest() == hashlib.md5(outs[1].encode()).hexdigest()


def test_collapse_per_node_attrs_baked(boundary_store, capsys):
    """--collapse file bakes the per-node "module" attr (side-prefixed id); the target Foo
    node carries 'target:Foo'. Only "module" is baked — no dead "modside" attr."""
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--collapse", "file"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"module":' in out
    assert '"modside":' not in out                       # dead attr dropped
    assert '"module": "target:Foo"' in out               # target Foo -> its file-stem module
    assert "const MODULES=" in out
