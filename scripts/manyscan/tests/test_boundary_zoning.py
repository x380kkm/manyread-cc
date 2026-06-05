# audience: internal
# manyscan.tests.test_boundary_zoning
"""manyscan.lib.boundary 的核心边界测试 —— 分类器 / 解析置信度 / 构建 / 视图 / 跨越边。

覆盖：分类器（路径包含判定，含归一化 + 自动探测）、解析置信度（0/1/N → 未解析/唯一/歧义，
绝不静默任选其一）、深度 1 依赖汇点（依赖节点出现但不展开）、边界集合 + 跨越边、两种视图、
确定性，以及 render 复合分区（向后兼容）。view_hide/bands/modules 三簇分别见
test_boundary_viewhide.py / test_boundary_bands.py / test_boundary_modules.py。共享 helper
（_build/_data_payload）取自 conftest。
"""
from __future__ import annotations

import json

import scan

from conftest import _build, _data_payload

from lib import boundary, render, stores
from lib.graph import Budget


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
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store), "--format", "json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--target-root" in err


#### 显式 --target-root 时同一库正常扫描，依赖符号正确归为 DEPENDENCY [@380kkm 2026-06-05] ####
def test_cli_explicit_target_root_runs(cpp_no_marker_store, capsys):
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
    rc = scan.main(["boundary", "--store", str(cpp_no_marker_store),
                    "--target-root", "", "--view", "internal", "--format", "json"])
    assert rc == 0


#### 向后兼容：旧 plugin-boundary 子命令 + --plugin-root/--engine-root 仍可用 [@380kkm 2026-06-05] ####
def test_cli_backcompat_plugin_boundary_and_flags(cpp_no_marker_store, capsys):
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
