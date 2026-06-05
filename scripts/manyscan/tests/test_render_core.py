# audience: internal
# manyscan.tests.test_render_core
"""manyscan.lib.render 的渲染核心测试 —— 确定性视图 + 诚实的 frontier 渲染。

覆盖 to_json/mermaid/dot/text/metrics_text 与 to_html 的自包含、离线、确定性、
importance、hub/bridge/zone 编码、no-zone 向后兼容。交互层（band/drilldown/hide-panel/
collapse）见 test_render_interactive.py。共享建图 helper 取自 conftest。
"""
from __future__ import annotations

import json

from conftest import _slice, _zoned_hub_graph

from lib import analyze, render
from lib.graph import Edge, Graph, Node


#### to_json 输出确定性且携带 bounded 截断元数据 [@380kkm 2026-06-05] ####
def test_to_json_deterministic_and_bounded():
    data = json.loads(render.to_json(_slice()))
    assert [n["label"] for n in data["nodes"]] == ["a.py", "b.py"]
    assert data["edges"][0] == {
        "src": "file:1", "dst": "file:2", "relation": "imports", "weight": 1, "evidence": None,
    }
    assert data["bounded"]["truncated"] is True
    assert data["bounded"]["elided"] == 7
    assert data["bounded"]["frontier"] == {"file:2": 7}


#### to_json 两次渲染逐字节稳定 [@380kkm 2026-06-05] ####
def test_to_json_is_stable():
    a, b = render.to_json(_slice()), render.to_json(_slice())
    assert a == b


#### mermaid 输出标记出 frontier 节点与截断警告 [@380kkm 2026-06-05] ####
def test_mermaid_marks_frontier_and_truncation():
    out = render.to_mermaid(_slice())
    assert out.startswith("flowchart TD")
    assert "truncated at level 1: 7 deps elided" in out
    # frontier 节点被打标
    assert "+7⤳" in out
    assert "-->|imports|" in out


#### to_dot 输出 graphviz 的 digraph 头与一条带标签的边 [@380kkm 2026-06-05] ####
def test_dot_basic():
    out = render.to_dot(_slice())
    assert out.startswith("digraph manyscan {")
    assert '"file:1" -> "file:2" [label="imports"];' in out


#### to_text 打印诚实的封顶截断警告与越界标记 [@380kkm 2026-06-05] ####
def test_text_prints_honest_truncation_warning():
    out = render.to_text(_slice())
    assert "⚠ 已在第 1 层封顶,省略 7 个依赖(分布: file:2→7)" in out
    assert "b.py  (+7 越界)" in out


#### metrics_text 汇总指标并带上截断省略警告 [@380kkm 2026-06-05] ####
def test_metrics_text_summary_and_warning():
    g = _slice()
    txt = render.metrics_text(analyze.metrics(g))
    assert "cycles=0" in txt and "bridges=" in txt
    assert "省略 7 个依赖" in txt
    assert "most_unstable:" in txt


#### to_html 输出自包含的离线可交互单页（sigma/graphology 内联） [@380kkm 2026-06-05] ####
def test_to_html_self_contained_and_interactive():
    out = render.to_html(_slice())
    assert out.startswith("<!doctype html>") and out.rstrip().endswith("</html>")
    # sigma + graphology + graphology-library 以 UMD <script> 全局形式内联
    assert "new SigmaCls(" in out and len(out) > 200_000
    # graphology-library（forceAtlas2）UMD
    assert "graphologyLibrary" in out
    # graphology 核心 UMD 全局
    assert "window.graphology" in out
    # forceAtlas2 布局
    assert "FA2.assign(" in out
    assert "a.py" in out and "b.py" in out
    # frontier 节点在其 label 上被打标
    assert "+7⤳" in out
    # 诚实的截断横幅
    assert "7 deps elided" in out
    # 可交互搜索框
    assert "search node" in out


#### 生成的页面零网络引用：所有库内联、运行时不发起任何 http 加载 [@380kkm 2026-06-05] ####
def test_to_html_offline_no_network_load():
    out = render.to_html(_slice())
    # 不经网络抓取任何东西
    assert "<script src=" not in out
    # 3 个裸库 <script> + 1 个裸 consts <script>
    assert out.count("<script>") >= 4
    # 引导块可被 drill-down 子页检索到
    assert 'id="ms-boot"' in out
    # boot 标签承载真正的 sigma 引导
    assert "new SigmaCls(" in out
    # graphology 核心 UMD 内联
    assert "window.graphology" in out
    # graphology-library（forceAtlas2）内联
    assert "graphologyLibrary" in out


#### to_html 两次渲染逐字节确定性 [@380kkm 2026-06-05] ####
def test_to_html_deterministic():
    assert render.to_html(_slice()) == render.to_html(_slice())


#### html 在 FORMATS 中注册且 render 分派能产出页面 [@380kkm 2026-06-05] ####
def test_html_in_formats():
    assert "html" in render.FORMATS
    assert render.render(_slice(), "html").startswith("<!doctype html>")


#### html 暴露每个节点的文件路径与点选信息面板 [@380kkm 2026-06-05] ####
def test_html_exposes_node_path_and_info_panel():
    out = render.to_html(_slice())
    # 每个节点都携带其文件路径
    assert '"path"' in out
    # 点选节点的信息面板存在
    assert "id='info'" in out
    # 该面板的用途
    assert "GET ITS FILE PATH" in out
    # 搜索也覆盖路径
    assert "search node/path" in out


#### 节点带 cluster 属性时按调色板上色并切到 cluster 图例 [@380kkm 2026-06-05] ####
def test_html_colors_by_cluster_when_present():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a.py", attrs={"cluster": "mod#0"}))
    g.add_node(Node("file:2", "file", label="b.py", attrs={"cluster": "mod#1"}))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    out = render.to_html(g)
    assert '"cluster": "mod#0"' in out and '"cluster": "mod#1"' in out
    # 图例切到 cluster 模式
    assert "color=cluster" in out
    # cluster 颜色取自调色板（而非 zone 色调）
    assert '"color": "#4e79a7"' in out and '"color": "#f28e2b"' in out


#### 未知输出格式时 render 抛 ValueError [@380kkm 2026-06-05] ####
def test_render_unknown_format_raises():
    try:
        render.render(_slice(), "yaml")
        assert False
    except ValueError:
        pass


#### _importance 按度数标出 hub 与关节 bridge 边 [@380kkm 2026-06-05] ####
def test_importance_degree_hub_bridge():
    g = _zoned_hub_graph()
    imp = render._importance(g)
    # hub h：fan_in=3（p1,p2,p3）+ fan_out=1（l）-> deg=4，标记为 hub
    assert imp["h"]["fan_in"] == 3 and imp["h"]["fan_out"] == 1 and imp["h"]["deg"] == 4
    assert imp["h"]["hub"] == 1
    # 唯一的关节 BRIDGE 边是 h->l；h 与 l 带 bridge 标志
    assert imp["h"]["bridge"] == 1 and imp["l"]["bridge"] == 1
    # target 节点处于环内：不在 bridge 边上
    assert imp["p1"]["bridge"] == 0


#### 所有图（含无 zone 切片）都按度数烘焙每节点尺寸 [@380kkm 2026-06-05] ####
def test_html_has_degree_sizing_all_graphs():
    out = render.to_html(_slice())
    # 烘焙的每节点尺寸（按度数缩放）
    assert '"size":' in out
    # 每个节点都携带其度数
    assert '"deg":' in out
    # 无 cytoscape mapper 泄漏
    assert "mapData(" not in out
    # 无 cytoscape DEGMAX token 泄漏
    assert "DEGMAX" not in out


#### DATA 中标出 hub 节点与 bridge 边并以红色粗边/光晕呈现 [@380kkm 2026-06-05] ####
def test_html_hub_and_bridge_markers():
    out = render.to_html(_zoned_hub_graph())
    # hub 节点在 DATA 中被打标
    assert '"hub": 1' in out
    # bridge 边在 DATA 中被打标
    assert '"bridge": 1' in out
    # 经 sigma highlighted 标志加 hub 光晕
    assert "highlighted" in out
    # 边 reducer 把 bridge 画成红+粗
    assert "attr.bridge" in out
    # bridge 红
    assert "#e15759" in out


#### html 配置节点拖拽与画布平移（sigma 默认） [@380kkm 2026-06-05] ####
def test_html_drag_pan_config():
    out = render.to_html(_zoned_hub_graph())
    # 节点拖拽配方 downNode -> mousemovebody -> mouseup 并 preventSigmaDefault
    assert "downNode" in out
    assert "mousemovebody" in out
    assert "preventSigmaDefault" in out


#### 单页内视图切换器：内部/依赖/两者，初始态由 view= 透传 [@380kkm 2026-06-05] ####
def test_html_view_toggle_one_page():
    out = render.to_html(_zoned_hub_graph(), view="dependency")
    # 单一页内视图切换器
    assert "id='view'" in out
    assert "<option value='internal'>" in out
    # 初始态由 view= 透传
    assert "<option value='dependency' selected>" in out
    assert "<option value='both'>" in out
    # 客户端显隐处理器
    assert "applyView" in out
    # target->dependency 的跨界被打标
    assert '"cross": 1' in out


#### zone 由节点颜色 + 空间聚类编码，无伪节点方框 [@380kkm 2026-06-05] ####
def test_html_zone_encoding_color_and_cluster():
    out = render.to_html(_zoned_hub_graph())
    # zone 由节点 COLOR + 空间聚类编码，无 '__zone_*__' 伪节点
    assert "__zone_" not in out
    assert '"zone": "target"' in out
    assert '"zone": "dependency"' in out
    # target zone 色调
    assert '"color": "#4e79a7"' in out
    # dependency zone 色调
    assert '"color": "#f28e2b"' in out


#### 无 zone 图不显示视图切换器但仍按度数定尺寸（向后兼容） [@380kkm 2026-06-05] ####
def test_html_no_zone_no_toggle_but_sized():
    out = render.to_html(_slice())
    # 普通图隐藏切换器
    assert "id='view'" not in out
    assert "__zone_" not in out
    assert "const HAS_ZONES=false" in out
    # 度数定尺寸仍生效（烘焙）
    assert '"size":' in out


#### 重设计渲染对分区图与普通图均逐字节确定 [@380kkm 2026-06-05] ####
def test_html_redesign_deterministic():
    assert render.to_html(_zoned_hub_graph()) == render.to_html(_zoned_hub_graph())
    assert render.to_html(_slice()) == render.to_html(_slice())


#### 迁移守卫：sigma 渲染器中不残留任何 cytoscape 时代 token [@380kkm 2026-06-05] ####
def test_html_no_cytoscape_leftovers():
    for g in (_zoned_hub_graph(), _slice()):
        out = render.to_html(g)
        for tok in ("cytoscape", "mapData(", "fcose", "DEGMAX", "underlay-color",
                    "boxSelectionEnabled", "__zone_", "data(zonecolor)"):
            assert tok not in out, tok


#### 依赖视图的隐藏逻辑：可见边绝不指向被隐藏的节点（无悬挂边） [@380kkm 2026-06-05] ####
def test_dependency_view_hide_logic_leaves_no_dangling_edge():
    g = _zoned_hub_graph()
    # 加一个仅连入其他 target 节点（无跨界边）的孤立 target 节点
    g.add_node(Node("p_iso", "class", label="iso", attrs={"zone": "target", "cluster": "target"}))
    g.add_edge(Edge("p_iso", "p1", "uses_type"))
    zone = {n.id: n.attrs.get("zone") for n in g.nodes.values()}

    #### 判断一条边是否为 target->dependency 的跨界边 [@380kkm 2026-06-05] ####
    def cross(e):
        return zone[e.src] == "target" and zone[e.dst] == "dependency"

    hidden_edges = {(e.src, e.dst) for e in g.edges
                    if zone[e.src] == "target" and zone[e.dst] == "target"}
    hidden_nodes = {
        nid for nid in g.nodes
        if zone[nid] == "target"
        and not any(cross(e) for e in g.edges if nid in (e.src, e.dst))
    }
    # 孤立 target 节点确被隐藏
    assert "p_iso" in hidden_nodes
    for e in g.edges:
        # 边自身已隐藏 -> 不会悬挂
        if (e.src, e.dst) in hidden_edges:
            continue
        assert e.src not in hidden_nodes and e.dst not in hidden_nodes
