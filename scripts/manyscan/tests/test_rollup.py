"""manyscan.lib.rollup 的测试 —— 目录/模块折叠 + frontier 上卷。"""
from __future__ import annotations

from lib import rollup, scope, stores
from lib.graph import Budget, Edge, Graph, Node


#### 测试 file 级折叠为恒等（原图返回） [@380kkm 2026-06-05] ####
def test_rollup_file_is_identity():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a/b.py"))
    assert rollup.rollup(g, "file") is g


#### 测试 dir 级折叠丢弃组内边、合并组间边 [@380kkm 2026-06-05] ####
def test_rollup_dir_collapses_intra_group():
    g = Graph()
    g.add_node(Node("file:1", "file", label="A/f1"))
    g.add_node(Node("file:2", "file", label="A/f2"))
    g.add_node(Node("file:3", "file", label="B/f3"))
    # A 组内 -> 丢弃
    g.add_edge(Edge("file:1", "file:2", "imports"))
    g.add_edge(Edge("file:1", "file:3", "imports"))
    g.add_edge(Edge("file:2", "file:3", "imports"))
    r = rollup.rollup(g, "dir")
    assert set(r.nodes) == {"A", "B"}
    assert r.nodes["A"].attrs["members"] == 2
    a_b = [e for e in r.edges if e.src == "A" and e.dst == "B"]
    assert len(a_b) == 1 and a_b[0].weight == 2


#### 测试折叠时把 frontier 上卷归并到组 [@380kkm 2026-06-05] ####
def test_rollup_carries_frontier_to_group():
    g = Graph()
    g.add_node(Node("file:1", "file", label="A/f1"))
    g.add_node(Node("file:2", "file", label="A/f2"))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    g.truncated = True
    g.frontier_depth = 1
    g.elided = 5
    # f1 处在预算之外省略了 5 个依赖
    g.frontier["file:1"] = 5
    r = rollup.rollup(g, "dir")
    assert r.truncated and r.frontier_depth == 1 and r.elided == 5
    # 重新归属到目录 A，而非丢弃
    assert r.frontier.get("A") == 5


#### 测试 _module_of 处理仓库根标记 [@380kkm 2026-06-05] ####
def test_module_of_handles_repo_root_marker():
    from lib.graph import Node
    # "" = 仓库根处的标记
    roots = sorted({"", "modA"}, key=len, reverse=True)
    assert rollup._module_of(Node("file:1", "file", label="modA/x.py"), roots) == "modA"
    assert rollup._module_of(Node("file:2", "file", label="top.py"), roots) == "(root)"


#### 测试 module_roots 探测与 module 级折叠 [@380kkm 2026-06-05] ####
def test_module_roots_and_module_rollup(module_store):
    with stores.Store(module_store) as st:
        assert rollup.module_roots(st) == {"modA", "modB"}
        g = scope.scan(st, "modA/x.py", Budget(max_nodes=50, max_depth=3, direction="out"))
        # 文件级切片：modA/x.py -> modB/y.py
        assert {n.label for n in g.nodes.values()} == {"modA/x.py", "modB/y.py"}
        r = rollup.rollup(g, "module", store=st)
        assert set(r.nodes) == {"modA", "modB"}
        a_b = [e for e in r.edges if e.src == "modA" and e.dst == "modB"]
        assert len(a_b) == 1
