# audience: internal
# manyscan.tests.test_boundary_bands
"""manyscan boundary 分层（bands）测试 —— assign_bands 的 flat/two/four + dep_depth 展开。

覆盖：三种分层各自的 band 与 meta、无分区图回退 flat、band_of 确定性、target-core/target-iface
拆分、深度 2 依赖填充 dep-core、dep-core 误标守卫、深度 2 截断如实上报，以及 boundary
--layers/--dep-depth CLI 接线。共享 helper（_build/_make_store）取自 conftest。
"""
from __future__ import annotations

import scan

from conftest import _build
from conftest import _make_store as _mk_store

from lib import boundary, stores
from lib.graph import Budget


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
