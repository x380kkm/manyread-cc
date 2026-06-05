# audience: internal
# manyscan.tests.test_adapters
"""manyscan.lib.adapters 的测试 —— SourceAdapter 协议 + CodeAdapter。"""
from __future__ import annotations

from lib import adapters, stores


#### CodeAdapter 满足 SourceAdapter 协议且暴露 code 名与默认适配器 [@380kkm 2026-06-05] ####
def test_codeadapter_satisfies_protocol():
    a = adapters.CodeAdapter()
    assert isinstance(a, adapters.SourceAdapter)
    assert a.name == "code"
    assert adapters.DEFAULT_ADAPTER.name == "code"


#### 由文件路径生成种子节点（id/kind 取自库中文件行） [@380kkm 2026-06-05] ####
def test_seed_nodes(synth_store):
    with stores.Store(synth_store) as st:
        nodes = adapters.CodeAdapter().seed_nodes(st, "pkg/a.py")
        assert [n.label for n in nodes] == ["pkg/a.py"]
        assert nodes[0].id == "file:1" and nodes[0].kind == "file"


#### 正向（out）邻居遍历：返回该文件 import 的依赖文件 [@380kkm 2026-06-05] ####
def test_neighbors_forward(synth_store):
    with stores.Store(synth_store) as st:
        steps = list(adapters.CodeAdapter().neighbors(st, "file:1", direction="out"))
        assert {s.node.label for s in steps} == {"pkg/b.py", "pkg/c.py"}
        assert all(s.edge.relation == "imports" for s in steps)


#### 反向（in）邻居遍历：返回 import 了该文件的来源文件 [@380kkm 2026-06-05] ####
def test_neighbors_reverse(synth_store):
    with stores.Store(synth_store) as st:
        steps = list(adapters.CodeAdapter().neighbors(st, "file:2", direction="in"))
        # a 导入了 b
        assert {s.node.label for s in steps} == {"pkg/a.py"}


#### 非文件节点（如 ext:foo）的邻居为空 [@380kkm 2026-06-05] ####
def test_neighbors_non_file_node_is_empty(synth_store):
    with stores.Store(synth_store) as st:
        assert list(adapters.CodeAdapter().neighbors(st, "ext:foo")) == []
