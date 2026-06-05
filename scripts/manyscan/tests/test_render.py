"""manyscan.lib.render 的测试 —— 确定性视图 + 诚实的 frontier 渲染。"""
from __future__ import annotations

import json

from lib import analyze, render
from lib.graph import Edge, Graph, Node


#### 构造一个被截断的微型切片图（两文件 + 一条 import 边 + frontier 元数据） [@380kkm 2026-06-05] ####
def _slice():
    g = Graph()
    g.add_node(Node("file:1", "file", label="a.py"))
    g.add_node(Node("file:2", "file", label="b.py"))
    g.add_edge(Edge("file:1", "file:2", "imports"))
    g.truncated = True
    g.frontier_depth = 1
    g.elided = 7
    g.frontier["file:2"] = 7
    return g


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


#### 构造一个带清晰 hub + bridge 边的微型分区图 [@380kkm 2026-06-05] ####
def _zoned_hub_graph():
    g = Graph()
    for nid in ("p1", "p2", "p3"):
        g.add_node(Node(nid, "class", label=nid, attrs={"zone": "target", "cluster": "target"}))
    g.add_node(Node("h", "class", label="Hub", attrs={"zone": "dependency", "cluster": "dependency"}))
    g.add_node(Node("l", "class", label="Leaf", attrs={"zone": "dependency", "cluster": "dependency"}))
    # target 3-环：p1->p2->p3->p1
    g.add_edge(Edge("p1", "p2", "uses_type"))
    g.add_edge(Edge("p2", "p3", "uses_type"))
    g.add_edge(Edge("p3", "p1", "uses_type"))
    for nid in ("p1", "p2", "p3"):
        g.add_edge(Edge(nid, "h", "uses_type"))
    g.add_edge(Edge("h", "l", "uses_type"))
    return g


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


#### 按 scan.py 的接线方式算出 (band_of, bands_meta) 供渲染测试用 [@380kkm 2026-06-05] ####
def _bands_for(g, layers):
    from lib import boundary
    return boundary.assign_bands(g, layers)


#### band 属性门控：扁平图无 band 属性、分层图带 band 与 BANDS 元数据 [@380kkm 2026-06-05] ####
def test_band_attr_gating_flat_vs_banded():
    # band_of=None（普通/扁平）=> DATA 中无 "band": 节点属性，但有 const BANDS=[];
    plain = render.to_html(_zoned_hub_graph())
    assert '"band":' not in plain
    assert "const BANDS=[];" in plain
    # 提供 band_of => "band" 进入 DATA + const BANDS=[{ ... 带 label
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    banded = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    assert '"band":' in banded
    assert 'const BANDS=[{' in banded
    # 显式有序的 meta 字面量
    assert ('const BANDS=[{"band": 0, "label": "target-core"}, '
            '{"band": 1, "label": "target-iface"}, '
            '{"band": 2, "label": "dep-iface"}, '
            '{"band": 3, "label": "dep-core"}];') in banded


#### 加 band 不改变 band_of=None 普通渲染的 DATA 载荷字节 [@380kkm 2026-06-05] ####
def test_band_attr_does_not_change_plain_data_bytes():
    out = render.to_html(_slice())
    marker = "const DATA="
    start = out.index(marker) + len(marker)
    end = out.index(";\n", start)
    payload = out[start:end]
    assert '"band":' not in payload


#### 带 band 的同图两次渲染逐字节一致且 md5 相等 [@380kkm 2026-06-05] ####
def test_layered_html_byte_deterministic():
    import hashlib
    for layers in ("two", "four"):
        bo, bm = _bands_for(_zoned_hub_graph(), layers)
        a = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
        bo2, bm2 = _bands_for(_zoned_hub_graph(), layers)
        b = render.to_html(_zoned_hub_graph(), band_of=bo2, bands_meta=bm2)
        assert a == b
        assert hashlib.md5(a.encode()).hexdigest() == hashlib.md5(b.encode()).hexdigest()


#### drill-down 标记齐备，且子页构建串里的 </script> 被转义 [@380kkm 2026-06-05] ####
def test_drilldown_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    for tok in ("doubleClickNode", "preventSigmaDefault", "URL.createObjectURL",
                "Blob(", "window.open(", "ms-boot", "chainKeys", "buildChild"):
        assert tok in out, tok
    # 子页构建串里的 </script> 被转义
    assert "<\\/script>" in out
    # 每个构建串的闭合都是转义形式
    assert "'<\\/script>'" in out or '"<\\/script>"' in out


#### N-band 方框层标记齐备且分区除数对零跨度有守卫 [@380kkm 2026-06-05] ####
def test_nband_box_layer_markers():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    for tok in ("afterRender", "graphToViewport", "insertBefore", "partitionBands",
                "drawBands", "NBANDS"):
        assert tok in out, tok
    assert "pointerEvents" in out or "pointer-events" in out
    # 方框层跟随视图切换：靠查询 ST.hidden（而非节点属性）
    assert "ST.hidden.has(k)" in out
    # 分区除数对零/近零跨度有守卫
    assert "span > 1e-9" in out
    # 扁平（band_of=None）不安装方框层：BANDS=[] 使 NBANDS==1 成为 no-op
    flat = render.to_html(_zoned_hub_graph())
    assert "const BANDS=[];" in flat


#### 扁平图与普通图（无 band）仍带全部特性正常渲染 [@380kkm 2026-06-05] ####
def test_flat_and_plain_still_render_with_features():
    for g in (_slice(), _zoned_hub_graph()):
        out = render.to_html(g)
        assert out.startswith("<!doctype html>") and out.rstrip().endswith("</html>")
        assert "search node" in out
        assert "const BANDS=[];" in out
        # 隐藏面板对每张图都是增量的；无配置路径不烘焙 HIDDEN 行
        assert "id='hp'" in out and "setupHidePanel" in out
        assert _consts_block(out).find("const HIDDEN=") < 0
    # 两层渲染仍带上两个 zone 颜色
    bo, bm = _bands_for(_zoned_hub_graph(), "two")
    two = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    assert '"color": "#4e79a7"' in two and '"color": "#f28e2b"' in two
    # 分区图保留视图切换器
    assert "id='view'" in two


#### 截取裸 consts <script> 段（从 const DATA= 到 boot 标签），HIDDEN 行的唯一栖身处 [@380kkm 2026-06-05] ####
def _consts_block(html: str) -> str:
    start = html.index("const DATA=")
    end = html.index('<script id="ms-boot">')
    return html[start:end]


#### 默认隐藏集被排序烘焙进 consts 块且确定性 [@380kkm 2026-06-05] ####
def test_default_hidden_baked_sorted_and_deterministic():
    import hashlib

    from lib import boundary
    g = _zoned_hub_graph()
    # 乱序的输入
    keys = ["l", "h"]
    bo, bm = boundary.assign_bands(g, "four")
    a = render.to_html(g, band_of=bo, bands_meta=bm, default_hidden=keys)
    b = render.to_html(g, band_of=bo, bands_meta=bm, default_hidden=list(keys))
    # 排序后的 JSON 列表烘焙进 consts 块；两次渲染逐字节一致 + md5 相等
    assert 'const HIDDEN=["h", "l"];' in _consts_block(a)
    assert a == b
    assert hashlib.md5(a.encode()).hexdigest() == hashlib.md5(b.encode()).hexdigest()


#### 无配置时 consts 块不烘焙 const HIDDEN= 行（字节兼容基线） [@380kkm 2026-06-05] ####
def test_no_config_byte_compat_no_hidden_line():
    # 无 default_hidden => consts 块中无 const HIDDEN= 行
    for g in (_slice(), _zoned_hub_graph()):
        plain = render.to_html(g)
        explicit_none = render.to_html(g, default_hidden=None)
        assert "const HIDDEN=" not in _consts_block(plain)
        assert "const HIDDEN=" not in _consts_block(explicit_none)
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    # 分层、无配置
    banded = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm)
    assert "const HIDDEN=" not in _consts_block(banded)


#### 隐藏面板的全部标记齐备，且对普通图也增量存在 [@380kkm 2026-06-05] ####
def test_hide_panel_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("id='hp'", "hp-list", "hp-apply", "hp-export", "hp-fmin",
                "setupHidePanel", "hp-export-ta", "hp-selmatch", "hp-selfan", "ms-counts"):
        assert tok in out, tok
    # facet 在运行时由 HAS_ZONES / BANDS 按值门控
    assert "HAS_ZONES" in out and "BANDS.length >= 2" in out
    # 对普通/扁平图也增量存在（facet 在运行时降级到 kind+fan_in）
    plain = render.to_html(_slice())
    assert "id='hp'" in plain and "setupHidePanel" in plain


#### 两段式预览/应用标记齐备且预览以半透明灰减淡 [@380kkm 2026-06-05] ####
def test_two_stage_preview_apply_markers():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("ST.preview", "togglePreview", "refreshDeltaHint", "Apply: hide ",
                "hiddenView", "hiddenCfg", "hiddenManual", "recomputeHidden", "unhidden"):
        assert tok in out, tok
    # 预览分支以半透明灰减淡
    assert "rgba(120,120,140,0.28)" in out
    # applyView 写 ST.hiddenView，绝不 ST.hidden = new Set()
    assert "ST.hiddenView = new Set()" in out
    assert "ST.hidden = new Set()" not in out


#### 视图切换只重建 hiddenView 并重算并集，绝不清掉 cfg/manual 隐藏 [@380kkm 2026-06-05] ####
def test_view_toggle_preserves_cfg_hidden():
    # 视图切换只重建 hiddenView + 调 recomputeHidden
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    # applyView 体重建 hiddenView 并重算派生并集
    assert "ST.hiddenView = new Set()" in out
    assert "recomputeHidden(); renderer.refresh(); updateCounts();" in out
    # boot 在布局/Sigma 构建前先播种 hiddenCfg 再 recomputeHidden
    assert "ST.hiddenCfg.add(HIDDEN[hi])" in out
    assert "recomputeHidden();" in out


#### 重排相关标记齐备且子图经 operators 命名空间解析 [@380kkm 2026-06-05] ####
def test_relayout_markers_present():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    for tok in ("operators.subgraph", "partitionBandsOn", "animatedReset",
                "getNodeDisplayData", "FA2.assign(sub", "setCustomBBox"):
        assert tok in out, tok
    # partitionBands() 包装 + 守卫 token 仍在（既有的方框层测试）
    assert "partitionBands()" in out and "span > 1e-9" in out and "NBANDS" in out
    # subgraph 经 operators 命名空间解析，而非（未定义的）顶层
    assert "graphologyLibrary.subgraph(" not in out


#### 导出标记齐备：收集排序后的名字并剥去烘焙的 frontier 后缀 [@380kkm 2026-06-05] ####
def test_export_markers_present():
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    for tok in ("exportHidden", "URL.createObjectURL", "navigator.clipboard.writeText",
                "manyread.view_hide.json", "view_hide", "version:1"):
        assert tok in out, tok
    # 导出收集排序后的名字
    assert "Object.keys(set).sort()" in out
    # 剥去烘焙的 frontier 后缀
    assert "lbl.indexOf('  +')" in out


#### drill-down 子页携带 HIDDEN 与全新面板且 chainKeys 跑全量边 [@380kkm 2026-06-05] ####
def test_drilldown_child_carries_hidden_and_panel():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    out = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h"])
    # buildChild 把子页的 HIDDEN 行写进与 DATA 同一条 consts 串里并重发面板 markup
    assert "'const HIDDEN=' + JSON.stringify(childHidden)" in out
    assert "PRISTINE_HP" in out
    # chainKeys 仍跑全量 DATA 边
    assert "DATA.edges.forEach" in out


#### 隐藏面板离线且确定：排序烘焙 + 无任何网络抓取 [@380kkm 2026-06-05] ####
def test_hide_panel_offline_and_deterministic():
    bo, bm = _bands_for(_zoned_hub_graph(), "four")
    a = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["h", "l"])
    b = render.to_html(_zoned_hub_graph(), band_of=bo, bands_meta=bm, default_hidden=["l", "h"])
    # 逐字节一致
    assert a == b
    # 仍完全离线
    assert "<script src=" not in a
    # HIDDEN 搭车既有的裸 consts 标签
    assert a.count("<script>") >= 4
    # 导出仅 Blob/剪贴板/textarea —— 无网络抓取
    assert "fetch(" not in a and "http://" not in a.split("<script>")[-1]


#### 手动/cfg 隐藏会隐去节点的所有相连边（无悬挂边） [@380kkm 2026-06-05] ####
def test_manual_hidden_hides_incident_edges_no_dangle():
    out = render.to_html(_zoned_hub_graph(), default_hidden=["h"])
    # edgeReducer 的隐藏提前返回在两个端点上都读 ST.hidden
    assert "ST.hidden.has(ex[0]) || ST.hidden.has(ex[1])" in out
    # Python 端不变量：隐藏任一节点 => 其所有相连边都被隐藏
    g = _zoned_hub_graph()
    # 例如高 fan_in 的 hub
    hidden = {"h"}
    for e in g.edges:
        if e.src in hidden or e.dst in hidden:
            # 此边与某个被隐藏节点相连 => reducer 将其隐藏
            assert e.src in hidden or e.dst in hidden
    # 没有可见边引用被隐藏的端点
    for e in g.edges:
        incident = e.src in hidden or e.dst in hidden
        if not incident:
            assert e.src not in hidden and e.dst not in hidden


#### 构造节点带文件路径的分区 hub 图 [@380kkm 2026-06-05] ####
def _zoned_paths_graph():
    g = _zoned_hub_graph()
    g.nodes["p1"].attrs["path"] = "plugin/P1.cpp"
    g.nodes["p2"].attrs["path"] = "plugin/P2.cpp"
    g.nodes["p3"].attrs["path"] = "plugin/P3.cpp"
    g.nodes["h"].attrs["path"] = "engine/Core.h"
    g.nodes["l"].attrs["path"] = "engine/Leaf.h"
    return g


#### 按 scan.py --collapse file 的接线方式渲染商图，返回 (html, module_of, modules_meta) [@380kkm 2026-06-05] ####
def _collapse_render(g, layers="four"):
    from lib import boundary
    from lib.boundary import Zoning
    z = Zoning(target_root="plugin", dep_roots=("engine",))
    bo, bm = boundary.assign_bands(g, layers)
    mo, mm = boundary.assign_modules(g, z, "file", None, bo)
    return render.to_html(g, band_of=bo, bands_meta=bm, module_of=mo, modules_meta=mm), mo, mm


#### 折叠关闭时与普通渲染逐字节一致（门控） [@380kkm 2026-06-05] ####
def test_collapse_off_byte_identical():
    import hashlib
    for g in (_slice(), _zoned_hub_graph()):
        plain = render.to_html(g)
        off = render.to_html(g, module_of=None, modules_meta=None)
        assert plain == off
        assert "const MODULES=" not in _consts_block(off)
        # DATA 载荷无 module 属性
        start = off.index("const DATA=") + len("const DATA=")
        end = off.index(";\n", start)
        assert '"module":' not in off[start:end]
        assert hashlib.md5(render.to_html(g).encode()).hexdigest() == \
            hashlib.md5(off.encode()).hexdigest()


#### 折叠开启时的商图标记齐备 [@380kkm 2026-06-05] ####
def test_collapse_on_markers():
    out, _mo, _mm = _collapse_render(_zoned_paths_graph())
    for tok in ("const MODULES=", "buildQuotient", "'mod:'", "partitionBandsOn",
                "ST.expanded", "renderModuleRows", "hp-mods", "hp-mexpand", "hp-mcollapse"):
        assert tok in out, tok
    # 单一确定性的边去重键
    assert "'q:'" in out
    # 超节点双击的守卫
    assert "indexOf('mod:')" in out


#### 折叠页离线且 MODULES 搭车既有裸 consts 标签 [@380kkm 2026-06-05] ####
def test_collapse_offline_and_bare_tags():
    out, _mo, mm = _collapse_render(_zoned_paths_graph())
    # 完全离线
    assert "<script src=" not in out
    # MODULES 搭车既有的裸 consts 标签
    assert out.count("<script>") >= 4
    assert 'id="ms-boot"' in out
    # MODULES 行在 consts 块内部
    cb = _consts_block(out)
    assert "const DATA=" in cb and "const MODULES=" in cb
    assert cb.index("const DATA=") < cb.index("const MODULES=")


#### 折叠默认全收起且 MODULES 列出每个模块 [@380kkm 2026-06-05] ####
def test_collapse_default_collapsed():
    out, _mo, mm = _collapse_render(_zoned_paths_graph())
    # ST.expanded 初始为空 => 全部收起
    assert "expanded:new Set()" in out
    assert "if(MODS){ if(HAS_ZONES){ applyView(ST.view); } else { buildQuotient(); } }" in out
    # MODULES const 列出 modules_meta 里的每个模块（id 已排序）
    for m in mm:
        assert ('"id": "%s"' % m["id"]) in out


#### drill-down 子页在与 DATA 同一裸 consts 串里重发 MODULES [@380kkm 2026-06-05] ####
def test_collapse_buildchild_reemits_modules():
    out, _mo, _mm = _collapse_render(_zoned_paths_graph())
    assert "'const MODULES=' + JSON.stringify(MODULES)" in out


#### 折叠的侧面板与计数标记齐备且交互处理器商图感知 [@380kkm 2026-06-05] ####
def test_collapse_panel_and_counts_markers():
    out, _mo, _mm = _collapse_render(_zoned_paths_graph())
    # 侧面板是唯一的折叠控件；超节点上双击为 no-op
    assert "renderModuleRows" in out
    # 计数对 displayed.order 核对
    assert "displayed.order" in out
    assert "modules collapsed" in out
    # clickNode
    assert "displayed.getNodeAttributes(e.node)" in out
    # drag
    assert "displayed.setNodeAttribute(dragged" in out
    # edgeReducer
    assert "displayed.extremities(key)" in out
    # locateNode 自动展开被折叠成员所在的模块
    assert "ST.expanded.add(mod); buildQuotient();" in out


#### applyView 与 applyPanel 在 MODS 时重建商图，关闭时保留旧路径 [@380kkm 2026-06-05] ####
def test_collapse_view_and_apply_rebuild_quotient():
    out, _mo, _mm = _collapse_render(_zoned_paths_graph())
    # applyView
    assert "if(MODS){ recomputeHidden(); buildQuotient(); return; }" in out
    # applyPanel
    assert "if(MODS){ buildQuotient(); return; }" in out
    # 关闭路径逐字保留
    assert "recomputeHidden(); renderer.refresh(); updateCounts();" in out


#### 折叠关闭页的 #hp 面板 markup 与 <style> 块与普通渲染逐字节一致 [@380kkm 2026-06-05] ####
def test_collapse_off_panel_markup_byte_identical():
    g = _zoned_hub_graph()
    off = render.to_html(g)
    # markup + <style>，不含 boot 标签
    head = off[:off.index("const DATA=")]
    assert "id='hp-mods'" not in head
    # 关闭 markup 里无分段标题
    assert "hp-sec-hd" not in head
    # 无门控的 CSS 规则
    assert ".hp-mrow{" not in head and ".hp-mbulk{" not in head
    # 开启页头部确实带上它们
    on, _mo, _mm = _collapse_render(_zoned_paths_graph())
    on_head = on[:on.index("const DATA=")]
    assert "id='hp-mods'" in on_head and ".hp-mrow{" in on_head
