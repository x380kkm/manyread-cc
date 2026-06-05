# audience: internal
# manyscan.tests.test_modules_views
"""manyscan.lib.boundary.modules_views 的测试 —— N 路解耦派生视图（手搭图，纯变换）。

覆盖：边内/跨分类、区矩阵、按需符号（证据 + 引用计数 + winning_prefix）、模块级环检测、
扇入/扇出 + 不稳定度、切割代价升序。全部在手搭 Graph 上，不查库。
"""
from __future__ import annotations

from lib.boundary import modules_views as mv
from lib.boundary import modulespec as ms
from lib.graph import Edge, Graph, Node


#### 造一个带 module 属性的符号节点 [@380kkm 2026-06-05] ####
def _n(nid: str, module: str, path: str = "", label: str | None = None) -> Node:
    return Node(id=nid, kind="class", label=label or nid,
                attrs={"module": module, "zone": module, "cluster": module, "path": path})


#### 一个三模块小图：A->B（两边）、B->C、C->A（造环），含一条 A 内部边 [@380kkm 2026-06-05] ####
def _three_module_graph() -> Graph:
    g = Graph()
    g.add_node(_n("s1", "A", "A/a1.h"))
    g.add_node(_n("s2", "A", "A/a2.h"))
    g.add_node(_n("s3", "B", "B/b.h", label="BClass"))
    g.add_node(_n("s4", "C", "C/c.h", label="CClass"))
    # A 内部边
    g.add_edge(Edge("s1", "s2", "uses_type"))
    # A->B 两条不同源（ref_count=2）
    g.add_edge(Edge("s1", "s3", "uses_type"))
    g.add_edge(Edge("s2", "s3", "extends"))
    # B->C
    g.add_edge(Edge("s3", "s4", "uses_type"))
    # C->A（与 A->B->C 形成 A,B,C 环）
    g.add_edge(Edge("s4", "s1", "uses_type"))
    g.edge_confidence = {e.key(): "direct" for e in g.edges}
    return g


#### 测试边内/跨分类 [@380kkm 2026-06-05] ####
def test_classify_edge():
    g = _three_module_graph()
    by = {(e.src, e.dst): mv.classify_edge(g, e) for e in g.edges}
    assert by[("s1", "s2")][0] == "intra"
    assert by[("s1", "s3")] == ("cross", "A", "B")
    assert by[("s4", "s1")] == ("cross", "C", "A")


#### 测试区矩阵：对角线 intra，跨对的 edge_count 与 by_relation [@380kkm 2026-06-05] ####
def test_zone_matrix():
    g = _three_module_graph()
    mat = mv.zone_matrix(g)
    # A 内部一条
    assert mat[("A", "A")].edge_count == 1
    # A->B 两条，关系分别 uses_type/extends
    assert mat[("A", "B")].edge_count == 2
    assert dict(mat[("A", "B")].by_relation) == {"uses_type": 1, "extends": 1}
    assert mat[("B", "C")].edge_count == 1
    assert mat[("C", "A")].edge_count == 1


#### 测试按需符号：A 从 B 需要 BClass，ref_count=2，带 winning_prefix [@380kkm 2026-06-05] ####
def test_needed_symbols_with_evidence():
    g = _three_module_graph()
    spec = ms.make_module_spec({"version": 1, "fallback": "Ext", "zones": [
        {"name": "A", "include": ["A"]}, {"name": "B", "include": ["B"]},
        {"name": "C", "include": ["C"]}]})
    needed = mv.needed_symbols(g, spec)
    ab = needed[("A", "B")]
    assert len(ab) == 1
    nd = ab[0]
    assert nd.dst == "s3" and nd.dst_label == "BClass"
    # 两个 A 侧符号引用它
    assert nd.ref_count == 2
    # 关系合并去重
    assert nd.relations == ("extends", "uses_type")
    # winning include 前缀（调试重叠规格）
    assert nd.winning_prefix == "B"
    # 内部边不计入按需
    assert ("A", "A") not in needed


#### 测试模块级环检测：A,B,C 构成一个 SCC [@380kkm 2026-06-05] ####
def test_module_cycles():
    g = _three_module_graph()
    cycles = mv.module_cycles(g)
    assert len(cycles) == 1
    assert cycles[0] == ["A", "B", "C"]


#### 测试无环图返回空环列表 [@380kkm 2026-06-05] ####
def test_no_cycle_when_dag():
    g = Graph()
    g.add_node(_n("s1", "A", "A/a.h"))
    g.add_node(_n("s2", "B", "B/b.h"))
    g.add_edge(Edge("s1", "s2", "uses_type"))
    g.edge_confidence = {("s1", "s2", "uses_type"): "direct"}
    assert mv.module_cycles(g) == []


#### 测试扇入/扇出 + 不稳定度（与 analyze 文件口径一致） [@380kkm 2026-06-05] ####
def test_fan_stats_instability():
    g = _three_module_graph()
    mat = mv.zone_matrix(g)
    fans = {f.module: f for f in mv.fan_stats(mat)}
    # A 出边 2（->B），入边 1（C->A） => I = 2/3
    assert fans["A"].fan_out == 2 and fans["A"].fan_in == 1
    assert fans["A"].instability == round(2 / 3, 4)
    # C 出 1 入 1 => 0.5
    assert fans["C"].instability == 0.5


#### 测试切割代价：按 cost 升序，cost=去重 dst 符号数 [@380kkm 2026-06-05] ####
def test_cut_costs_ranked():
    g = _three_module_graph()
    spec = ms.make_module_spec({"version": 1, "fallback": "Ext", "zones": [
        {"name": "A", "include": ["A"]}, {"name": "B", "include": ["B"]},
        {"name": "C", "include": ["C"]}]})
    needed = mv.needed_symbols(g, spec)
    mat = mv.zone_matrix(g)
    cuts = mv.cut_costs(needed, mat)
    # 每对都只需切 1 个去重符号
    assert all(c.cost == 1 for c in cuts)
    # 升序（cost, src, dst）
    assert cuts == sorted(cuts, key=lambda c: (c.cost, c.src_module, c.dst_module))
    # A->B 的 edge_count=2 被带出
    ab = next(c for c in cuts if c.src_module == "A" and c.dst_module == "B")
    assert ab.edge_count == 2


#### 测试 modules_to_dict 端到端形状 + 确定性 [@380kkm 2026-06-05] ####
def test_modules_to_dict_shape():
    from lib import render
    g = _three_module_graph()
    spec = ms.make_module_spec({"version": 1, "fallback": "Ext", "zones": [
        {"name": "A", "include": ["A"]}, {"name": "B", "include": ["B"]},
        {"name": "C", "include": ["C"]}]})
    d = render.modules_to_dict(g, spec)
    assert d["zones"] == ["A", "B", "C"] and d["fallback"] == "Ext"
    assert any(m["kind"] == "intra" for m in d["matrix"])
    assert d["cycles"] == [["A", "B", "C"]]
    # needed 带证据
    ab = next(x for x in d["needed"] if x["src"] == "A" and x["dst"] == "B")
    assert ab["symbols"][0]["ref_count"] == 2
    assert ab["symbols"][0]["winning_prefix"] == "B"
    # cut_costs 升序
    costs = [c["cost"] for c in d["cut_costs"]]
    assert costs == sorted(costs)
    # 确定：两次序列化逐字节相同
    import json
    a = json.dumps(render.modules_to_dict(g, spec), ensure_ascii=False, sort_keys=False)
    b = json.dumps(render.modules_to_dict(g, spec), ensure_ascii=False, sort_keys=False)
    assert a == b
