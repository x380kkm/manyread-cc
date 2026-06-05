"""manyscan.lib.boundary 的测试 —— 符号级 目标↔依赖 边界。

覆盖：分类器（路径包含判定，含归一化 + 自动探测）、解析置信度
（0/1/N → 未解析/唯一/歧义，绝不静默任选其一）、深度 1 依赖汇点
（依赖节点出现但不展开）、边界集合 + 跨越边、两种视图、确定性，
以及 render 复合分区（向后兼容）。
"""
from __future__ import annotations

import json

from lib import boundary, render, stores
from lib.graph import Budget


#### 用默认预算在 out 方向构建边界图，返回 (zoning, graph) [@380kkm 2026-06-05] ####
def _build(st):
    z = boundary.make_zoning(st, None, None)
    budget = Budget(max_nodes=400, max_depth=2, direction="out")
    return z, boundary.build(st, z, budget, alias="t")


#### 从带标记的库自动探测出目标根，并确认存在模块标记 [@380kkm 2026-06-05] ####
def test_detect_target_root(boundary_store):
    with stores.Store(boundary_store) as st:
        assert boundary.detect_target_root(st) == "plugin"
        assert boundary.has_module_markers(st) is True


#### 无标记时自动探测不可靠，guard 须报 False 让 CLI 拒绝 [@380kkm 2026-06-05] ####
def test_no_markers_autodetect_unsound(cpp_no_marker_store):
    with stores.Store(cpp_no_marker_store) as st:
        assert boundary.has_module_markers(st) is False
        assert boundary.detect_target_root(st) == ""
        # 放任自动探测时依赖符号 AActor 会被分类为 TARGET
        z = boundary.make_zoning(st, None, None)
        assert boundary.zone_of_path("Engine/Source/Actor.h", z) == boundary.TARGET


#### 显式覆盖目标根与依赖根：归一化 + 按长度降序排序 [@380kkm 2026-06-05] ####
def test_make_zoning_override(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, "./other/", ["Engine\\Source", "engine"])
        assert z.target_root == "other"
        # 依赖根经过归一化，并按长度降序排序
        assert z.dep_roots == ("Engine/Source", "engine")


#### 按路径判定分区：归一化、目录边界、None、空目标根等情形 [@380kkm 2026-06-05] ####
def test_zone_of_path():
    z = boundary.Zoning(target_root="plugin")
    assert boundary.zone_of_path("plugin/Foo.cpp", z) == boundary.TARGET
    assert boundary.zone_of_path("plugin", z) == boundary.TARGET
    # 归一化
    assert boundary.zone_of_path(".\\plugin\\Bar.h", z) == boundary.TARGET
    assert boundary.zone_of_path("engine/Core.h", z) == boundary.DEPENDENCY
    # 前缀相同但不在目录边界上
    assert boundary.zone_of_path("pluginX/Foo.cpp", z) == boundary.DEPENDENCY
    assert boundary.zone_of_path(None, z) == boundary.DEPENDENCY
    # 目标根为 "" => 全部算目标
    z0 = boundary.Zoning(target_root="")
    assert boundary.zone_of_path("anything/here.cpp", z0) == boundary.TARGET


#### 顶层符号（无父）的限定名是裸名 [@380kkm 2026-06-05] ####
def test_qualified_name(boundary_store):
    with stores.Store(boundary_store) as st:
        # 顶层符号（无父）-> 裸名；嵌套链见下一个用例
        assert boundary.qualified_name(st, 1) == "Foo"


#### 嵌套符号的限定名用 :: 拼接父链 [@380kkm 2026-06-05] ####
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


#### 两个目标文件都定义的类型：歧义但确属内部，停在目标分区不污染依赖面 [@380kkm 2026-06-05] ####
def test_resolve_ambiguous_all_target_stays_internal(tmp_path):
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
        # 记为 amb: 而非 dep:
        assert r.target_id == "amb:PDup"
        # 停在内部，不进入依赖面
        assert r.node.attrs["zone"] == boundary.TARGET


#### 四种解析置信度：direct / unique / unresolved / ambiguous [@380kkm 2026-06-05] ####
def test_resolve_target(boundary_store):
    with stores.Store(boundary_store) as st:
        z = boundary.make_zoning(st, None, None)
        rows = {r["id"]: r for r in st.conn.execute(
            "SELECT id,src_symbol_id,dst_symbol_id,dst_name,relation FROM edges").fetchall()}
        # 边 1：extends，已带 dst_symbol_id -> direct
        r = boundary.resolve_target(st, rows[1], z)
        assert r.confidence == "direct" and r.target_id == "s2" and r.ambiguity == 0
        # 边 2：implements Core，1 个候选 -> unique
        r = boundary.resolve_target(st, rows[2], z)
        assert r.confidence == "unique" and r.target_id == "s3" and r.ambiguity == 1
        # 边 3：uses_type Missing，0 个候选 -> unresolved 外部依赖
        r = boundary.resolve_target(st, rows[3], z)
        assert r.confidence == "unresolved" and r.target_id == "dep:Missing" and r.ambiguity == 0
        # 边 4：uses_type Dup，2 个候选 -> ambiguous 外部依赖（绝不任选其一）
        r = boundary.resolve_target(st, rows[4], z)
        assert r.confidence == "ambiguous" and r.target_id == "dep:Dup" and r.ambiguity == 2
        assert r.node.attrs["ambiguity"] == 2
        # 绝不是某个符号 id
        assert not r.target_id.startswith("s")


#### external_node 构造未解析/歧义的依赖节点及其属性 [@380kkm 2026-06-05] ####
def test_external_node():
    n = boundary.external_node("UObject")
    assert n.id == "dep:UObject" and n.kind == "external" and n.label == "UObject"
    assert n.attrs["zone"] == boundary.DEPENDENCY and n.attrs["unresolved"] is True
    n2 = boundary.external_node("Dup", 2)
    assert n2.attrs["ambiguity"] == 2


#### 依赖节点作为汇点出现：在图中可见但绝不被展开 [@380kkm 2026-06-05] ####
def test_build_depth1_sink(boundary_store):
    with stores.Store(boundary_store) as st:
        z, g = _build(st)
        # 目标符号存在
        assert "s1" in g.nodes and g.nodes["s1"].attrs["zone"] == boundary.TARGET
        # 依赖目标都在：Actor (s2)、Core (s3)、dep:Missing、dep:Dup
        assert "s2" in g.nodes and g.nodes["s2"].attrs["zone"] == boundary.DEPENDENCY
        assert "s3" in g.nodes and g.nodes["s3"].attrs["zone"] == boundary.DEPENDENCY
        assert "dep:Missing" in g.nodes and "dep:Dup" in g.nodes
        # 依赖节点是汇点：任何依赖分区 / dep 节点都没有出边
        for nid, node in g.nodes.items():
            if node.attrs.get("zone") == boundary.DEPENDENCY:
                assert g.out_edges(nid) == [], f"dependency node {nid} was expanded"
        assert len(g) <= 400


#### 每条跨越边都把解析置信度记入 edge_confidence [@380kkm 2026-06-05] ####
def test_build_confidence_recorded(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        conf = g.edge_confidence
        assert conf[("s1", "s2", "extends")] == "direct"
        assert conf[("s1", "s3", "implements")] == "unique"
        assert conf[("s1", "dep:Missing", "uses_type")] == "unresolved"
        assert conf[("s1", "dep:Dup", "uses_type")] == "ambiguous"


#### 内部视图只保留目标符号，且本夹具下无目标→目标边 [@380kkm 2026-06-05] ####
def test_internal_view(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        iv = boundary.internal_view(g)
        # 只剩目标符号
        assert set(iv.nodes) == {"s1"}
        assert all(iv.nodes[n].attrs["zone"] == boundary.TARGET for n in iv.nodes)
        # 本夹具无目标→目标边
        assert iv.edges == []


#### 依赖面（不汇总）：二部图 目标边界 -> 各依赖目标 [@380kkm 2026-06-05] ####
def test_dependency_surface(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.dependency_surface(g, rollup_modules=False)
        # 二部图：s1（目标边界）-> 各依赖目标
        assert "s1" in es.nodes
        dep = {n for n in es.nodes if es.nodes[n].attrs["zone"] == boundary.DEPENDENCY}
        assert dep == {"s2", "s3", "dep:Missing", "dep:Dup"}
        assert all(e.src == "s1" for e in es.edges)


#### 依赖面汇总到模块：依赖符号归入 (root) 模块组 [@380kkm 2026-06-05] ####
def test_dependency_surface_rollup(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        es = boundary.dependency_surface(g, rollup_modules=True, store=st)
        # engine/ 下的依赖符号（s2、s3）汇入 "(root)" 模块组
        dep_groups = sorted(n for n in es.nodes if n.startswith("dep:"))
        # 至少一个汇总后的依赖节点
        assert dep_groups
        assert all(e.src == "s1" for e in es.edges)


#### 跨越边：按 (src,dst,relation) 排序，携带置信度与目标侧证据 [@380kkm 2026-06-05] ####
def test_crossings(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        cs = boundary.crossings(g)
        # 按 (src,dst,relation) 排序
        assert cs == sorted(cs, key=lambda c: (c.src, c.dst, c.relation))
        by_dst = {c.dst: c for c in cs}
        assert by_dst["s2"].confidence == "direct" and by_dst["s2"].relation == "extends"
        assert by_dst["s3"].confidence == "unique"
        assert by_dst["dep:Missing"].confidence == "unresolved"
        assert by_dst["dep:Dup"].confidence == "ambiguous"
        # 证据是目标侧的 path:line
        assert all(c.evidence.startswith("plugin/Foo.cpp") for c in cs)


#### 同一库两次构建的 JSON 渲染逐字节相同 [@380kkm 2026-06-05] ####
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


#### 从 html 中抽出注入的 const DATA={...} JSON 对象（不含内联库） [@380kkm 2026-06-05] ####
def _data_payload(html: str) -> str:
    marker = "const DATA="
    start = html.index(marker) + len(marker)
    end = html.index(";\n", start)
    return html[start:end]


#### 分区与置信度编码为节点/边属性 + 颜色，无 __zone_*__ 复合父 [@380kkm 2026-06-05] ####
def test_render_zone_encoding(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        html = render.to_html(g)
    payload = _data_payload(html)
    # sigma 把分区编码为节点属性 + 颜色（无 '__zone_*__' 复合父）
    assert "__zone_" not in payload
    assert '"zone": "target"' in payload
    assert '"zone": "dependency"' in payload
    # 目标色调
    assert '"color": "#4e79a7"' in payload
    # 依赖色调
    assert '"color": "#f28e2b"' in payload
    # 置信度以属性形式落到边上
    assert '"conf"' in payload


#### 无分区的普通切片渲染时不带任何分区属性 / 伪节点 [@380kkm 2026-06-05] ####
def test_render_no_zone_unchanged(synth_store):
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        html = render.to_html(g)
    payload = _data_payload(html)
    assert "__zone_" not in payload
    assert '"zone":' not in payload
    assert '"parent"' not in payload


#### 普通切片的 to_json 不受 boundary 新增项影响 [@380kkm 2026-06-05] ####
def test_render_no_zone_byte_compat(synth_store):
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        d = json.loads(render.to_json(g))
    assert "nodes" in d and "edges" in d and "bounded" in d


#### 无标记且无 --target-root 时 boundary 须拒绝（退出码 2） [@380kkm 2026-06-05] ####
def test_cli_refuses_unsound_autodetect(cpp_no_marker_store, capsys):
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store), "--format", "json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--target-root" in err


#### 显式 --target-root 时同一库正常扫描，依赖符号正确归为 DEPENDENCY [@380kkm 2026-06-05] ####
def test_cli_explicit_target_root_runs(cpp_no_marker_store, capsys):
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store),
                    "--target-root", "MyPlugin", "--dep-root", "Engine",
                    "--view", "dependency", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    # 目标 Foo
    assert "s1" in ids
    # 依赖 AActor 作为深度 1 汇点目标出现
    assert "s2" in ids


#### --target-root "" 是显式选择整仓库=目标，guard 不触发 [@380kkm 2026-06-05] ####
def test_cli_empty_target_root_opts_in(cpp_no_marker_store, capsys):
    import scan
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store),
                    "--target-root", "", "--view", "internal", "--format", "json"])
    assert rc == 0


#### 向后兼容：旧 plugin-boundary 子命令 + --plugin-root/--engine-root 仍可用 [@380kkm 2026-06-05] ####
def test_cli_backcompat_plugin_boundary_and_flags(cpp_no_marker_store, capsys):
    import scan
    rc = scan.main(["plugin-boundary", "--store", str(cpp_no_marker_store),
                    "--plugin-root", "MyPlugin", "--engine-root", "Engine",
                    "--view", "dependency", "--format", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    ids = {n["id"] for n in out["nodes"]}
    assert "s1" in ids and "s2" in ids


#### 汇总模块排序为全序 (len,str)，多次运行逐字节一致 [@380kkm 2026-06-05] ####
def test_dependency_surface_rollup_deterministic(boundary_store):
    outs = []
    for _ in range(3):
        with stores.Store(boundary_store) as st:
            _, g = _build(st)
            outs.append(render.to_json(boundary.dependency_surface(g, rollup_modules=True, store=st)))
    assert outs[0] == outs[1] == outs[2]


#### --format html 产出一张含页内视图切换的自包含页面（非投影子图） [@380kkm 2026-06-05] ####
def test_cli_html_is_one_page_with_toggle(boundary_store, capsys):
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store),
                    "--target-root", "plugin", "--view", "internal", "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!doctype html>")
    # 页内切换存在
    assert "id='view'" in out
    # --view 作为初始值传入
    assert "<option value='internal' selected>" in out
    # 完整图被输出：即使 --view internal，依赖节点仍在页面里
    assert '"zone": "dependency"' in out and '"zone": "target"' in out


#### _default_hidden_keys 返回排序、确定的列表，命中名字匹配与高扇入节点 [@380kkm 2026-06-05] ####
def test_default_hidden_keys_sorted_deterministic():
    import scan
    from lib import render
    from test_render import _zoned_hub_graph
    g = _zoned_hub_graph()
    # 'Hub' 是节点 id 'h'（fan_in=3）的 label；min_fan_in=3 也会命中它。
    keys = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    # 已排序
    assert keys == sorted(keys)
    # Hub 同时被名字与 fan_in 命中
    assert "h" in keys
    again = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    # 确定
    assert keys == again
    # 烘焙出的 HIDDEN 常量反映已排序的列表
    out = render.to_html(g, default_hidden=keys)
    assert "const HIDDEN=" + json_dumps_sorted(keys) in out


#### 把 keys 排序后转成 JSON 串（与烘焙出的 HIDDEN 常量对齐） [@380kkm 2026-06-05] ####
def json_dumps_sorted(keys):
    import json
    return json.dumps(sorted(keys))


#### label 或尾段匹配 + fnmatch 模式（并集语义） [@380kkm 2026-06-05] ####
def test_default_hidden_keys_segment_and_pattern(tmp_path):
    import scan
    from lib.graph import Edge, Graph, Node
    g = Graph()
    # 裸名外部节点
    g.add_node(Node("amb:FString", "ambiguous", label="FString"))
    # 限定名内部节点
    g.add_node(Node("s9", "class", label="Outer::Inner::FString"))
    # 模式命中目标
    g.add_node(Node("dep:TArrayView", "external", label="TArrayView"))
    # 不受影响
    g.add_node(Node("s10", "class", label="Keep"))
    g.add_edge(Edge("s10", "amb:FString", "uses_type"))
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "patterns": ["TArray*"]})
    # 裸 label 匹配
    assert "amb:FString" in keys
    # 限定 label 的尾段匹配
    assert "s9" in keys
    # fnmatch 模式
    assert "dep:TArrayView" in keys
    # 未匹配
    assert "s10" not in keys


#### 在分区图中 view_hide 仅作用于依赖侧，绝不默认隐藏目标符号 [@380kkm 2026-06-05] ####
def test_default_hidden_keys_engine_side_only():
    import scan
    from lib.graph import Edge, Graph, Node
    g = Graph()
    g.add_node(Node("dep:FString", "external", label="FString", attrs={"zone": "dependency"}))
    # 同名，但在目标侧
    g.add_node(Node("s1", "class", label="FString", attrs={"zone": "target"}))
    # 高扇入的目标枢纽
    g.add_node(Node("hub", "class", label="Hub", attrs={"zone": "target"}))
    for s in ("a", "b", "c"):
        g.add_node(Node(s, "class", label=s, attrs={"zone": "target"}))
        # hub 的 fan_in = 3
        g.add_edge(Edge(s, "hub", "uses_type"))
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "min_fan_in": 2})
    # 依赖侧名字匹配 -> 隐藏
    assert "dep:FString" in keys
    # 同名但在目标侧 -> 受保护
    assert "s1" not in keys
    # 高扇入的目标枢纽 -> 受保护（min_fan_in 只作用于依赖侧）
    assert "hub" not in keys


#### boundary --ignore <裸文件> 把命中的 id 烘焙进排序后的 const HIDDEN [@380kkm 2026-06-05] ####
def test_cli_boundary_ignore_bakes_hidden(boundary_store, capsys, tmp_path):
    import scan
    ig = tmp_path / "ignore.json"
    # 目标符号 Foo（id s1）
    ig.write_text('{"names": ["Foo"]}', encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    # Foo -> s1 默认隐藏
    assert "const HIDDEN=" in out and '"s1"' in out


#### 无 --ignore 且无提交的 view_hide 时无 HIDDEN 行，两次渲染逐字节相同 [@380kkm 2026-06-05] ####
def test_cli_boundary_no_config_byte_identical(boundary_store, capsys):
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
    # 受门控的 HIDDEN 行不出现在常量块里
    consts = a[a.index("const DATA="):a.index('<script id="ms-boot">')]
    assert "const HIDDEN=" not in consts


#### 提交在 manyread.json['view_hide'] 的键在渲染时自动发现（无需标志） [@380kkm 2026-06-05] ####
def test_cli_boundary_committed_view_hide_autodiscovered(boundary_store, capsys):
    import json as _json

    import scan
    # <tmp>/manyread/
    store_dir = boundary_store.parent
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "languages": [], "view_hide": {"names": ["Foo"]}}),
        encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    # Foo 经提交的键被自动隐藏
    assert "const HIDDEN=" in out and '"s1"' in out


#### --ignore 优先于提交的 view_hide 键（不同的匹配） [@380kkm 2026-06-05] ####
def test_cli_boundary_ignore_overrides_committed(boundary_store, capsys, tmp_path):
    import json as _json

    import scan
    store_dir = boundary_store.parent
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "view_hide": {"names": ["Foo"]}}), encoding="utf-8")
    ig = tmp_path / "ig.json"
    # Core -> s3
    ig.write_text('{"view_hide": {"names": ["Core"]}}', encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    consts = out[out.index("const HIDDEN="):out.index('<script id="ms-boot">')]
    # Core 被隐藏（来自 --ignore）
    assert '"s3"' in consts
    # Foo 未被隐藏（提交的键被覆盖）
    assert '"s1"' not in consts


#### rollup.roots_by_len 在长度相等时按字典序断绑（确定性） [@380kkm 2026-06-05] ####
def test_roots_by_len_total_order(module_store):
    from lib import rollup
    with stores.Store(module_store) as st:
        roots = rollup.roots_by_len(st)
        # modA 与 modB 等长 -> 必须按 (-len, str) 顺序
        assert roots == sorted(roots, key=lambda r: (-len(r), r))


#### 用 (files, syms, edges) 字面量构建一个微型真实 schema 库 [@380kkm 2026-06-05] ####
def _mk_store(tmp_path, files, syms, edges):
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


#### assign_bands 的 flat/two/four 三种分层各自的 band 与 meta [@380kkm 2026-06-05] ####
def test_assign_bands_flat_two_four(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        # flat -> 全在 band 0，无盒子
        bf, mf = boundary.assign_bands(g, "flat")
        assert set(bf.values()) == {0} and mf == []
        # two -> 目标 id 在 band 0，依赖 id 在 band 1，meta 两项
        bt, mt = boundary.assign_bands(g, "two")
        # 目标 Foo
        assert bt["s1"] == 0
        for nid in ("s2", "s3", "dep:Missing", "dep:Dup"):
            # 依赖
            assert bt[nid] == 1
        assert mt == [{"band": 0, "label": "target"}, {"band": 1, "label": "dependency"}]
        # four -> s1 因有跨越边而是目标接口层 (1)；依赖是依赖接口层 (2)
        bq, mq = boundary.assign_bands(g, "four")
        # 在 boundary_nodes(g) 中
        assert bq["s1"] == boundary.TARGET_IFACE
        for nid in ("s2", "s3", "dep:Missing", "dep:Dup"):
            # 深度 1 的依赖面
            assert bq[nid] == boundary.DEP_IFACE
        assert [m["label"] for m in mq] == ["target-core", "target-iface", "dep-iface", "dep-core"]


#### 无分区的普通切片回退到 flat 而不抛错 [@380kkm 2026-06-05] ####
def test_assign_bands_no_zone_falls_back_to_flat(synth_store):
    from lib import scope
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=2, direction="out"))
        for layers in ("flat", "two", "four"):
            bo, bm = boundary.assign_bands(g, layers)
            assert set(bo.values()) == {0}
            # 无分区图没有盒子
            assert bm == []


#### assign_bands('four') 的 band_of 在多次调用间逐字节稳定（消费循环已排序） [@380kkm 2026-06-05] ####
def test_assign_bands_deterministic(boundary_store):
    with stores.Store(boundary_store) as st:
        _, g = _build(st)
        a = boundary.assign_bands(g, "four")
        b = boundary.assign_bands(g, "four")
        c = boundary.assign_bands(g, "four")
        assert a == b == c
        # 插入顺序即排序顺序
        assert list(a[0].keys()) == sorted(a[0].keys())


#### 有跨越边的目标归 target-iface，仅有目标→目标边的目标归 target-core [@380kkm 2026-06-05] ####
def test_target_core_vs_iface_split(tmp_path):
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/A.cpp", ".cpp", "x"),
             (3, "plugin/B.cpp", ".cpp", "x"),
             (4, "engine/Dep.h", ".h", "x")]
    # A 既用 B（目标）又用 Dep（依赖）-> A 是接口层；B 无出边 -> B 是核心层
    syms = [(1, 2, "A", "class", 1, 1, None),
            (2, 3, "B", "class", 1, 1, None),
            (3, 4, "Dep", "class", 1, 1, None)]
    # A -> B（目标→目标）；A -> Dep（目标→依赖，跨越）
    edges = [(1, 2, 1, 2, None, "uses_type"),
             (2, 2, 1, 3, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        bo, _ = boundary.assign_bands(g, "four")
        # A 有跨越边
        assert bo["s1"] == boundary.TARGET_IFACE
        # B 被隔离
        assert bo["s2"] == boundary.TARGET_CORE
        # Dep 是依赖面
        assert bo["s3"] == boundary.DEP_IFACE


#### 深度 2 的依赖填充 dep-core：表层依赖再引用的第二依赖在深度 2 才出现并落 band 3 [@380kkm 2026-06-05] ####
def test_dep_depth_2_populates_dep_core(tmp_path):
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Surface.h", ".h", "x"),
             (4, "engine/Behind.h", ".h", "x")]
    # Foo 目标；Surface 依赖表层；Behind 表层背后的依赖
    syms = [(1, 2, "Foo", "class", 1, 1, None),
            (2, 3, "Surface", "class", 1, 1, None),
            (3, 4, "Behind", "class", 1, 1, None)]
    # Foo -> Surface（深度 1）；Surface -> Behind（深度 2）
    edges = [(1, 2, 1, 2, None, "uses_type"),
             (2, 3, 2, 3, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        b = Budget(max_nodes=400, max_depth=2, direction="out")
        # 深度 1：Behind (s3) 是汇点 -> 不出现
        g1 = boundary.build(st, z, b, dep_depth=1)
        assert "s3" not in g1.nodes
        bo1, _ = boundary.assign_bands(g1, "four")
        # 深度 1 时 dep-core band 为空
        assert 3 not in bo1.values()
        # 深度 2：Behind 出现，标记 dep_core，落到 dep-core band；Surface 仍是 dep-iface
        g2 = boundary.build(st, z, b, dep_depth=2)
        assert "s3" in g2.nodes
        assert g2.nodes["s3"].attrs.get("dep_core") == 1
        assert g2.nodes["s3"].attrs.get("dep_depth") == 2
        bo2, _ = boundary.assign_bands(g2, "four")
        # Surface 仍是 API 表层
        assert bo2["s2"] == boundary.DEP_IFACE
        # Behind 在它背后
        assert bo2["s3"] == boundary.DEP_CORE


#### 被目标(深度1)与依赖同时引用的依赖须保持 dep-iface，绝不误标 dep-core [@380kkm 2026-06-05] ####
def test_dep_core_mislabel_guard(tmp_path):
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Shared.h", ".h", "x"),
             (4, "engine/Surface.h", ".h", "x")]
    # Foo 目标；Surface 依赖表层；Shared 被 Foo 与 Surface 同时引用
    syms = [(1, 2, "Foo", "class", 1, 1, None),
            (2, 4, "Surface", "class", 1, 1, None),
            (3, 3, "Shared", "class", 1, 1, None)]
    # Foo -> Shared（深度 1）；Foo -> Surface（深度 1）；Surface -> Shared（深度 2）
    edges = [(1, 2, 1, 3, None, "uses_type"),
             (2, 2, 1, 2, None, "uses_type"),
             (3, 4, 2, 3, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"),
                           dep_depth=2)
        # Shared (s3) 在深度 1 已加入（Foo 引用它）-> 深度 2 不再重加/标记
        assert g.nodes["s3"].attrs.get("dep_core") is None
        bo, _ = boundary.assign_bands(g, "four")
        assert bo["s3"] == boundary.DEP_IFACE


#### 深度 2 过程中溢出须置 g.truncated + g.elided（如实上报，不静默丢弃） [@380kkm 2026-06-05] ####
def test_dep_depth_2_truncation_composes(tmp_path):
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Foo.cpp", ".cpp", "x"),
             (3, "engine/Surface.h", ".h", "x"),
             (4, "engine/Behind.h", ".h", "x")]
    syms = [(1, 2, "Foo", "class", 1, 1, None),
            (2, 3, "Surface", "class", 1, 1, None),
            (3, 4, "Behind", "class", 1, 1, None)]
    # Foo -> Surface；Surface -> Behind（深度 2）
    edges = [(1, 2, 1, 2, None, "uses_type"),
             (2, 3, 2, 3, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        # cap=2 容纳 Foo + Surface；深度 2 的 Behind 节点溢出。
        g = boundary.build(st, z, Budget(max_nodes=2, max_depth=2, direction="out"),
                           dep_depth=2)
        assert g.truncated is True and g.elided > 0
        assert "s3" not in g.nodes


#### boundary --layers/--dep-depth 接线：html 烘焙 band + BANDS，json 与无 --layers 逐字节一致 [@380kkm 2026-06-05] ####
def test_cli_layers_dep_depth_wiring(boundary_store, capsys):
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
    # 非 html 格式下 band 无效
    assert with_layers == without_layers


#### assign_modules('file') 确定、id 带侧前缀、无路径依赖归 (external)，meta 排序且每项完备 [@380kkm 2026-06-05] ####
def test_assign_modules_determinism_and_ids(boundary_store):
    with stores.Store(boundary_store) as st:
        z, g = _build(st)
        band_of, _ = boundary.assign_bands(g, "four")
        a = boundary.assign_modules(g, z, "file", st, band_of)
        b = boundary.assign_modules(g, z, "file", st, band_of)
        # 确定
        assert a == b
        module_of, meta = a
        # 目标 Foo（s1, plugin/Foo.cpp）-> 'target:Foo'（带侧前缀的文件名干）
        assert module_of["s1"] == "target:Foo"
        # 依赖符号（s2 engine/Actor.h, s3 engine/Core.h）-> 'dependency:<module>'
        assert module_of["s2"].startswith("dependency:")
        assert module_of["s3"].startswith("dependency:")
        # 无路径的按名依赖 -> '(external)'，dep:Dup 在依赖侧
        assert module_of["dep:Missing"] == "dependency:(external)"
        assert module_of["dep:Dup"] == "dependency:(external)"
        # modules_meta 按 id 排序；每项格式完备
        assert [m["id"] for m in meta] == sorted(m["id"] for m in meta)
        for m in meta:
            assert isinstance(m["members"], int) and m["members"] >= 1
            assert "band" in m and m["zone"] in ("target", "dependency")
            assert m["color"] in ("#4e79a7", "#f28e2b")
            assert m["side"] == m["id"].split(":", 1)[0]


#### amb:<name> 节点（全目标候选、无路径）映射到 'target:(external)' [@380kkm 2026-06-05] ####
def test_assign_modules_amb_external_target_side(tmp_path):
    files = [(1, "plugin/a.h", ".h", "class W{};\nclass PDup{};\n"),
             (2, "plugin/b.h", ".h", "class PDup{};\n")]
    syms = [(1, 1, "W", "class", 1, 1, None), (2, 1, "PDup", "class", 2, 2, None),
            (3, 2, "PDup", "class", 1, 1, None)]
    # W -> PDup（2 个目标候选 => amb:）
    edges = [(1, 1, 1, None, "PDup", "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", [])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        # 全目标的歧义节点
        assert "amb:PDup" in g.nodes
        module_of, _ = boundary.assign_modules(g, z, "file", st, None)
        assert module_of["amb:PDup"] == "target:(external)"


#### level='dir' 按目标符号的父目录分组 [@380kkm 2026-06-05] ####
def test_assign_modules_dir_level(tmp_path):
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
        # plugin/Foo.cpp 的父目录
        assert module_of["s1"] == "target:plugin"


#### 跨 target-core 与 target-iface 的文件折叠到较低 band（成员最小 band） [@380kkm 2026-06-05] ####
def test_assign_modules_band_is_min_member(tmp_path):
    # A、B 同处 plugin/Same.cpp => 一个模块
    files = [(1, "plugin/X.uplugin", ".uplugin", "{}"),
             (2, "plugin/Same.cpp", ".cpp", "x"),
             (3, "engine/Dep.h", ".h", "x")]
    # A 有跨越边 -> target-iface（band 1）；B 无 -> target-core（band 0）
    syms = [(1, 2, "A", "class", 1, 1, None),
            (2, 2, "B", "class", 2, 2, None),
            (3, 3, "Dep", "class", 1, 1, None)]
    # A -> Dep（跨越 => A 接口层）；A -> B（目标→目标）
    edges = [(1, 2, 1, 3, None, "uses_type"),
             (2, 2, 1, 2, None, "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    with stores.Store(db) as st:
        z = boundary.make_zoning(st, "plugin", ["engine"])
        g = boundary.build(st, z, Budget(max_nodes=400, max_depth=2, direction="out"))
        band_of, _ = boundary.assign_bands(g, "four")
        assert band_of["s1"] == boundary.TARGET_IFACE and band_of["s2"] == boundary.TARGET_CORE
        _, meta = boundary.assign_modules(g, z, "file", st, band_of)
        same = next(m for m in meta if m["id"] == "target:Same")
        # {0, 1} 的最小值
        assert same["band"] == 0
        assert same["members"] == 2


#### 无 --collapse、--collapse off、标志缺席三者逐字节相同；两次渲染也逐字节相同 [@380kkm 2026-06-05] ####
def test_cli_collapse_off_equals_pre_flag(boundary_store, capsys):
    import scan
    runs = []
    for extra in ([], ["--collapse", "off"]):
        rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                        "--format", "html", *extra])
        assert rc == 0
        runs.append(capsys.readouterr().out)
    # off == 标志缺席（逐字节相同）
    assert runs[0] == runs[1]


#### off/file/dir 每种折叠级别两次 html 渲染逐字节相同且 md5 一致 [@380kkm 2026-06-05] ####
def test_collapse_md5_stable_each_level(boundary_store, capsys):
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


#### --collapse file 烘焙每节点 module 属性（带侧前缀），且无死属性 modside [@380kkm 2026-06-05] ####
def test_collapse_per_node_attrs_baked(boundary_store, capsys):
    import scan
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--collapse", "file"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"module":' in out
    # 死属性被丢弃
    assert '"modside":' not in out
    # 目标 Foo -> 其文件名干模块
    assert '"module": "target:Foo"' in out
    assert "const MODULES=" in out
