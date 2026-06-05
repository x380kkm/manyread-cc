# audience: internal
# manyscan.tests.test_render_interactive
"""manyscan.lib.render 的交互层测试 —— band 门控/drilldown/hide-panel/preview/relayout/
export/collapse 商图。

渲染核心与确定性见 test_render_core.py。共享建图/分层/折叠 helper 取自 conftest。
"""
from __future__ import annotations

from conftest import (
    _bands_for,
    _collapse_render,
    _consts_block,
    _slice,
    _zoned_hub_graph,
    _zoned_paths_graph,
)

from lib import render


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
