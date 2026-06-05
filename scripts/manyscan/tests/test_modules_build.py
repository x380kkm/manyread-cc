# audience: internal
# manyscan.tests.test_modules_build
"""manyscan.lib.boundary.modules_build 的测试 —— N 路有界构建流水线。

覆盖：逐 zone 前缀播种（规模随声明模块、非库大小）、N 区节点构造（zone==cluster==module）、
兜底汇点（不展开）、跨多模块歧义汇点（绝不任选）、预算截断如实上报、dep_depth 沿用。
"""
from __future__ import annotations

from conftest import _make_store as _mk_store

from lib import stores
from lib.boundary import modulespec as ms
from lib.boundary import modules_build as mb
from lib.graph import Budget


#### 四模块库：Core / Render / Game / ThirdParty 各一文件，外加一个库外大目录 [@380kkm 2026-06-05] ####
def _four_zone_store(tmp_path):
    files = [
        (1, "Engine/Source/Runtime/Core/Obj.h", ".h", "x"),
        (2, "Engine/Source/Runtime/RHI/Rhi.h", ".h", "x"),
        (3, "Engine/Source/Runtime/Engine/Actor.h", ".h", "x"),
        (4, "Engine/Source/ThirdParty/zlib.h", ".h", "x"),
        (5, "Engine/Plugins/Misc/Other.h", ".h", "x"),
    ]
    syms = [
        (1, 1, "FObject", "class", 1, 1, None),
        (2, 2, "FRHI", "class", 1, 1, None),
        (3, 3, "AActor", "class", 1, 1, None),
        (4, 4, "ZStream", "class", 1, 1, None),
        (5, 5, "Other", "class", 1, 1, None),
    ]
    # Actor extends FObject (Game->Core)；RHI uses FObject (Render->Core)；
    # FObject uses ZStream (Core->ThirdParty)；Actor uses Other (Game->External 兜底)
    edges = [
        (1, 3, 3, 1, None, "extends"),
        (2, 2, 2, 1, None, "uses_type"),
        (3, 1, 1, 4, None, "uses_type"),
        (4, 3, 3, 5, None, "uses_type"),
    ]
    return _mk_store(tmp_path, files, syms, edges)


#### 四模块规格：Core/Render/Game/ThirdParty + External 兜底 [@380kkm 2026-06-05] ####
def _four_zone_spec():
    doc = {"version": 1, "fallback": "External", "zones": [
        {"name": "Core", "include": ["Engine/Source/Runtime/Core"]},
        {"name": "Render", "include": ["Engine/Source/Runtime/RHI"]},
        {"name": "Game", "include": ["Engine/Source/Runtime/Engine"]},
        {"name": "ThirdParty", "include": ["Engine/Source/ThirdParty"]},
    ]}
    return ms.make_module_spec(doc)


#### 节点按模块上区：zone==cluster==module，跨模块边都在 [@380kkm 2026-06-05] ####
def test_build_modules_zones_nodes(tmp_path):
    db = _four_zone_store(tmp_path)
    spec = _four_zone_spec()
    with stores.Store(db) as st:
        g = mb.build_modules(st, spec, Budget(max_nodes=400, max_depth=2, direction="out"))
    assert g.nodes["s1"].attrs["module"] == "Core"
    assert g.nodes["s1"].attrs["zone"] == "Core" == g.nodes["s1"].attrs["cluster"]
    assert g.nodes["s2"].attrs["module"] == "Render"
    assert g.nodes["s3"].attrs["module"] == "Game"
    assert g.nodes["s4"].attrs["module"] == "ThirdParty"
    # Other 落兜底 External（经 Game->External 边被达），是汇点
    assert g.nodes["s5"].attrs["module"] == "External"
    assert g.out_edges("s5") == []


#### 播种规模随声明模块大小而非库大小：库外大目录的符号绝不作为种子 [@380kkm 2026-06-05] ####
def test_seed_scales_with_declared_size(tmp_path):
    # 1 个声明模块文件 + 500 个库外文件/符号（不在任何 include 下）
    files = [(1, "decl/Mod/a.h", ".h", "x")]
    syms = [(1, 1, "A", "class", 1, 1, None)]
    edges = []
    nid = 2
    for k in range(500):
        files.append((nid, f"other/big/f{k}.h", ".h", "x"))
        syms.append((nid, nid, f"S{k}", "class", 1, 1, None))
        nid += 1
    db = _mk_store(tmp_path, files, syms, edges)
    spec = ms.make_module_spec({"version": 1, "fallback": "External",
                                "zones": [{"name": "Mod", "include": ["decl/Mod"]}]})
    with stores.Store(db) as st:
        seeds = mb._seed_rows(st, spec)
    # 只播种声明模块下的 1 个符号，库外 500 个绝不入种子
    assert [s[0] for s in seeds] == [1]


#### LIKE 前缀仅粗筛：兄弟同名前缀目录的符号被 module_of_path 剔除 [@380kkm 2026-06-05] ####
def test_like_prefix_sibling_not_seeded(tmp_path):
    # 'a/Mod' 声明；'a/ModExtra' 是兄弟前缀（LIKE 'a/Mod%' 会命中，须被权威剔除）
    files = [(1, "a/Mod/x.h", ".h", "x"), (2, "a/ModExtra/y.h", ".h", "x")]
    syms = [(1, 1, "X", "class", 1, 1, None), (2, 2, "Y", "class", 1, 1, None)]
    db = _mk_store(tmp_path, files, syms, [])
    spec = ms.make_module_spec({"version": 1, "fallback": "External",
                                "zones": [{"name": "Mod", "include": ["a/Mod"]}]})
    with stores.Store(db) as st:
        seeds = mb._seed_rows(st, spec)
    assert [s[0] for s in seeds] == [1]


#### 跨多模块歧义：dst_name 命中分属不同模块的候选 -> amb: 多模块汇点，绝不任选 [@380kkm 2026-06-05] ####
def test_cross_module_ambiguous_sink(tmp_path):
    # 两个文件各定义同名 Dup，分属 Core 与 Render；种子 Seed uses_type Dup
    files = [(1, "Core/c.h", ".h", "class Dup{};\nclass Seed{};\n"),
             (2, "Render/r.h", ".h", "class Dup{};\n")]
    syms = [(1, 1, "Dup", "class", 1, 1, None), (2, 1, "Seed", "class", 2, 2, None),
            (3, 2, "Dup", "class", 1, 1, None)]
    edges = [(1, 1, 2, None, "Dup", "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    spec = ms.make_module_spec({"version": 1, "fallback": "External", "zones": [
        {"name": "Core", "include": ["Core"]}, {"name": "Render", "include": ["Render"]}]})
    with stores.Store(db) as st:
        g = mb.build_modules(st, spec, Budget(max_nodes=400, max_depth=2, direction="out"))
    assert "amb:Dup" in g.nodes
    n = g.nodes["amb:Dup"]
    assert sorted(n.attrs["modules"]) == ["Core", "Render"]
    assert n.attrs["ambiguity"] == 2
    # 汇点：绝不解析为某个 s<id>
    assert g.out_edges("amb:Dup") == []
    assert g.edge_confidence[("s2", "amb:Dup", "uses_type")] == "ambiguous"


#### 同模块多候选不算歧义：全部候选落同一声明模块 -> unique [@380kkm 2026-06-05] ####
def test_same_module_multi_candidate_unique(tmp_path):
    # 两个 Dup 都在 Core
    files = [(1, "Core/a.h", ".h", "class Dup{};\nclass Seed{};\n"),
             (2, "Core/b.h", ".h", "class Dup{};\n")]
    syms = [(1, 1, "Dup", "class", 1, 1, None), (2, 1, "Seed", "class", 2, 2, None),
            (3, 2, "Dup", "class", 1, 1, None)]
    edges = [(1, 1, 2, None, "Dup", "uses_type")]
    db = _mk_store(tmp_path, files, syms, edges)
    spec = ms.make_module_spec({"version": 1, "fallback": "External",
                                "zones": [{"name": "Core", "include": ["Core"]}]})
    with stores.Store(db) as st:
        g = mb.build_modules(st, spec, Budget(max_nodes=400, max_depth=2, direction="out"))
    # 无歧义汇点
    assert "amb:Dup" not in g.nodes
    assert g.edge_confidence[("s2", "s1", "uses_type")] == "unique"


#### 预算溢出如实置 truncated + elided（不静默丢弃） [@380kkm 2026-06-05] ####
def test_budget_truncation_reported(tmp_path):
    db = _four_zone_store(tmp_path)
    spec = _four_zone_spec()
    with stores.Store(db) as st:
        # cap=2 只容纳两个种子，其余溢出
        g = mb.build_modules(st, spec, Budget(max_nodes=2, max_depth=2, direction="out"))
    assert g.truncated is True and g.elided > 0
    assert len(g.nodes) <= 2


#### 声明模块符号是一等可展开节点：链式 Game->Core->ThirdParty 全部纳入 [@380kkm 2026-06-05] ####
def test_declared_symbols_expandable(tmp_path):
    db = _four_zone_store(tmp_path)
    spec = _four_zone_spec()
    with stores.Store(db) as st:
        g = mb.build_modules(st, spec, Budget(max_nodes=400, max_depth=2, direction="out"))
    # ThirdParty 的 ZStream 经 Core->ThirdParty 边被达，且是声明模块（非兜底）
    assert "s4" in g.nodes and g.nodes["s4"].attrs["module"] == "ThirdParty"
    # 跨模块边都在
    keys = {e.key() for e in g.edges}
    assert ("s3", "s1", "extends") in keys
    assert ("s1", "s4", "uses_type") in keys


#### 同库两次构建的 JSON 渲染逐字节相同（确定性） [@380kkm 2026-06-05] ####
def test_build_modules_deterministic(tmp_path):
    from lib import render
    db = _four_zone_store(tmp_path)
    spec = _four_zone_spec()
    outs = []
    for _ in range(3):
        with stores.Store(db) as st:
            g = mb.build_modules(st, spec, Budget(max_nodes=400, max_depth=2, direction="out"))
            outs.append(render.to_json(g))
    assert outs[0] == outs[1] == outs[2]
