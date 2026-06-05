# audience: internal
# manyscan.tests.test_boundary_modules
"""manyscan boundary 模块折叠（assign_modules + --collapse）测试。

覆盖：assign_modules('file') 确定性 + 带侧前缀 id + meta 完备、amb 全目标节点归 target:(external)、
level='dir' 按父目录分组、跨 band 文件折叠取成员最小 band，以及 boundary --collapse 的 off/file/dir
逐字节稳定与每节点 module 属性烘焙。共享 helper（_build/_make_store）取自 conftest。
"""
from __future__ import annotations

import scan

from conftest import _build
from conftest import _make_store as _mk_store

from lib import boundary, stores
from lib.graph import Budget


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
