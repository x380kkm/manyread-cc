"""manyscan.lib.scope 的测试 —— 种子解析 + 有界的真实依赖展开。"""
from __future__ import annotations

import pytest

from lib import scope, stores
from lib.graph import Budget


#### 取图中全部节点的 label 集合 [@380kkm 2026-06-05] ####
def _labels(g):
    return {n.label for n in g.nodes.values()}
#### /取 label 集合 ####


#### 取图中全部边的 (源 label, 目标 label) 关系集合 [@380kkm 2026-06-05] ####
def _rels(g):
    return {(g.nodes[e.src].label, g.nodes[e.dst].label) for e in g.edges}
#### /取边关系集合 ####


#### 测试按文件路径解析种子 [@380kkm 2026-06-05] ####
def test_resolve_seed_by_file(synth_store):
    with stores.Store(synth_store) as st:
        nodes = scope.resolve_seed(st, "pkg/a.py")
        assert [n.label for n in nodes] == ["pkg/a.py"]
        assert nodes[0].kind == "file" and str(nodes[0].evidence) == "pkg/a.py"


#### 测试按符号名解析种子 [@380kkm 2026-06-05] ####
def test_resolve_seed_by_symbol(synth_store):
    with stores.Store(synth_store) as st:
        assert _labels_set(scope.resolve_seed(st, "C")) == {"pkg/c.py"}


#### 取节点列表的 label 集合 [@380kkm 2026-06-05] ####
def _labels_set(nodes):
    return {n.label for n in nodes}
#### /取节点列表 label 集合 ####


#### 测试无法解析的种子返回空 [@380kkm 2026-06-05] ####
def test_resolve_seed_unresolved(synth_store):
    with stores.Store(synth_store) as st:
        assert scope.resolve_seed(st, "zzz_nope_zzz") == []


#### 测试向前（out）展开沿 import 边 [@380kkm 2026-06-05] ####
def test_expand_forward_imports(synth_store):
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/a.py", Budget(max_nodes=50, max_depth=3, direction="out"))
        assert _labels(g) == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
        assert ("pkg/a.py", "pkg/b.py") in _rels(g)
        assert ("pkg/a.py", "pkg/c.py") in _rels(g)


#### 测试反向（in）展开沿 importer 反查 [@380kkm 2026-06-05] ####
def test_expand_reverse_importers(synth_store):
    with stores.Store(synth_store) as st:
        g = scope.scan(st, "pkg/b.py", Budget(max_nodes=50, max_depth=2, direction="in"))
        assert {"pkg/a.py", "pkg/b.py"} <= _labels(g)
        # a 导入 b -> 反向边 a->b
        assert ("pkg/a.py", "pkg/b.py") in _rels(g)


#### 测试无法解析的种子扫描结果为空 [@380kkm 2026-06-05] ####
def test_scan_unresolved_is_empty(synth_store):
    with stores.Store(synth_store) as st:
        assert len(scope.scan(st, "zzz_nope_zzz")) == 0


#### 测试真实引擎存储库上的有界铁律（结果 <= 预算） [@380kkm 2026-06-05] ####
def test_expand_bounded_on_engine_store():
    info = next((s for s in stores.list_stores() if s.alias == "NS_UE_5_6_1"), None)
    if info is None or not info.db_path.is_file():
        pytest.skip("NS_UE_5_6_1 store not present")
    with stores.Store(info.db_path) as st:
        seed_row = next(iter(st.iter_files(exts={".cpp", ".h"})), None)
        if seed_row is None:
            pytest.skip("no cpp/h files in store")
        seeds = scope.resolve_seed(st, seed_row["path"], alias=info.alias)
        assert seeds, "a real file path must resolve to a seed"
        g = scope.expand(st, seeds, Budget(max_nodes=150, max_depth=4, direction="out"),
                         alias=info.alias)
        # 绝不把整个引擎拖进来
        assert len(g) <= 150
        if g.truncated:
            # 截断被诚实记录，而非静默
            assert g.frontier
