# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.render.html
"""manyscan.lib.render.html — 自包含的交互式 HTML 视图（sigma.js）。

模块级不变量：输出逐字节恒等——`assets/*.js`（graphology/sigma/bootstrap/module_addon）
被内联烘焙进每个 HTML 文件，同一图两次渲染产出完全相同的字节（golden 字节恒等）；
受门控的 HIDDEN / MODULES / N 路分区 const 在参数省略时一律不烘焙，输出与不带这些参数时一致。
图→视图数据的纯烘焙在 `htmlbake.py`，本文件持 HTML/CSS 外壳、素材内联与 `to_html` 编排，
并门面再导出 `_importance` 以保 `render` 包对外表面不变。
"""
from __future__ import annotations

import json
from pathlib import Path

from lib.graph import Graph

from .htmlbake import (
    _bake_consts, _bake_edges, _bake_nodes, _color_keys, _importance)


#### 自包含交互式 HTML（sigma.js / WebGL + graphology 力导布局）的素材与库清单 [@380kkm 2026-06-05] ####
_ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
_SIGMA_LIBS = [
    (_ASSET_DIR / "graphology.umd.min.js",
     "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"),
    (_ASSET_DIR / "graphology-library.min.js",
     "https://cdn.jsdelivr.net/npm/graphology-library@0.8.0/dist/graphology-library.min.js"),
    (_ASSET_DIR / "sigma.umd.min.js",
     "https://unpkg.com/sigma@2.4.0/build/sigma.min.js"),
]

_BOOTSTRAP_ASSET = _ASSET_DIR / "boundary_bootstrap.js"
_HTML_BOOTSTRAP = _BOOTSTRAP_ASSET.read_text(encoding="utf-8")
# N 路模块附加脚本：仅 module_mode 时追加
_MODULE_ADDON_ASSET = _ASSET_DIR / "module_addon.js"
#### /自包含交互式 HTML 的素材与库清单 ####


#### 隐藏面板（#hp）的 MODULES（可折叠商图）区段的静态标记 [@380kkm 2026-06-05] ####
_HP_MODULES_SECTION = (
    "<div id='hp-mods' class='hp-sec active'>"
    "<div class='hp-sec-body'>"
    "<div class='hp-mbulk'><button id='hp-mexpand'>expand all</button>"
    "<button id='hp-mcollapse'>collapse all</button>"
    "<span id='hp-mdelta' class='hp-delta'></span>"
    "<button id='hp-mapply' class='primary'>Apply</button></div>"
    "<div class='hp-mlist' id='hp-mlist'></div>"
    "</div></div>"
)


#### 生成隐藏面板（#hp）的 HTML 标记 [@380kkm 2026-06-05] ####
def _hide_panel_html(with_modules: bool) -> str:
    body = (
        "<div class='hp-hd'>"
        "<input id='hpq' placeholder='filter symbols...'>"
        "<select id='hp-kind'><option value=''>kind: any</option></select>"
        "<select id='hp-zone'><option value=''>zone: any</option></select>"
        "<select id='hp-band'><option value=''>band: any</option></select>"
        "<span>fan_in&ge;<input id='hp-fmin' class='hp-num' type='number' min='0' value='0'></span>"
        "</div>"
        "<div class='hp-act'>"
        "<button id='hp-selmatch'>select matching</button>"
        "<button id='hp-selfan'>select fan_in&ge;X</button>"
        "<button id='hp-clear'>clear preview</button>"
        "<span id='hp-delta' class='hp-delta'></span>"
        "<button id='hp-apply' class='primary'>Apply</button>"
        "<button id='hp-fit'>fit</button>"
        "<button id='hp-export'>Export</button>"
        "</div>"
        "<div class='hp-cols'>"
        "<span></span><span class='sortable' data-k='label'>symbol</span>"
        "<span class='sortable active' data-k='fan_in'>fan_in</span>"
        "<span class='sortable' data-k='zone'>zone/band</span>"
        "</div>"
        "<div class='hp-list' id='hp-list'></div>"
        "<textarea id='hp-export-ta' readonly placeholder='exported view_hide JSON appears here'></textarea>"
        "<div class='hp-foot' id='hp-foot'></div>"
    )
    head = ("<div id='hp' class='collapsed'>"
            "<div class='hp-tab' id='hp-tab'>HIDE</div>"
            "<div class='hp-grip' id='hp-grip' title='drag to resize'></div>")
    if not with_modules:
        return head + body + "</div>"
    return (head
            + "<div class='hp-tabs'>"
            + "<button class='hp-tabb active' data-sec='hp-mods'>Modules <span id='hp-mods-n' class='mq'></span></button>"
            + "<button class='hp-tabb' data-sec='hp-hide-sec'>Hide</button>"
            + "</div>"
            + _HP_MODULES_SECTION
            + "<div id='hp-hide-sec' class='hp-sec'><div class='hp-sec-body'>"
            + body
            + "</div></div>"
            + "</div>")
#### /生成隐藏面板的 HTML 标记 ####


#### 转义字符串中的 HTML 特殊字符 [@380kkm 2026-06-05] ####
def _html_escape(s: str | None) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


#### 按顺序内联一个 vendored UMD 库，缺失时回退 CDN [@380kkm 2026-06-05] ####
def _script_for(asset: Path, cdn: str) -> str:
    if asset.is_file():
        return "<script>" + asset.read_text(encoding="utf-8") + "</script>"
    return f'<script src="{cdn}"></script>'


#### 装配顶部条与 #hp 面板的 <head>/<style> 标记 [@380kkm 2026-06-05] ####
def _head_html(title: str, meta: str, legend_kind: str, banner: str,
               view_ctl: str, legend: str, with_modules: bool) -> str:
    # 受门控的可折叠商图 CSS：仅当烘焙了 MODULES 时追加
    modules_css = (
        ".hp-tabs{display:flex;padding:0 8px 0 24px;background:#222838;border-bottom:1px solid #39415a}"
        ".hp-tabb{flex:1;background:transparent;color:#9aa6b2;border:none;border-bottom:2px solid transparent;"
        "padding:6px 4px;cursor:pointer;font-weight:600;font-size:12px;user-select:none}"
        ".hp-tabb.active{color:#fff;border-bottom-color:#3a9457}"
        ".hp-sec{display:none}"
        ".hp-sec.active{display:flex;flex-direction:column;flex:1 1 auto;min-height:0}"
        ".hp-sec-body{display:flex;flex-direction:column;flex:1 1 auto;min-height:0}"
        ".hp-mlist{flex:1;overflow:auto;min-height:0}"
        ".hp-mod-grp{color:#9aa6b2;font-size:10px;padding:4px 8px 2px 24px;text-transform:uppercase}"
        ".hp-mrow{display:grid;grid-template-columns:16px 1fr 40px;gap:4px;align-items:center;"
        "padding:2px 8px 2px 24px;cursor:pointer;border-bottom:1px solid #2b3142}"
        ".hp-mrow.expanded{color:#8ad18a}"
        ".hp-mrow.mpending{background:rgba(255,207,92,0.13)}"
        ".hp-mrow .mn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".hp-mrow .mq{color:#9aa6b2;font-size:10px;text-align:right}"
        ".hp-mbulk{display:flex;gap:4px;padding:2px 8px 4px 24px}"
        ".hp-mbulk button{background:#39415a;color:#dfe3ea;border:1px solid #556;border-radius:4px;"
        "padding:2px 6px;font-size:11px;cursor:pointer}"
    ) if with_modules else ""

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_html_escape(title)}</title><style>"
        "html,body{margin:0;height:100%;font:13px system-ui,Segoe UI,sans-serif}"
        "#bar{position:fixed;top:0;left:0;right:0;padding:6px 10px;background:#1f2430;color:#eee;"
        "z-index:10;display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
        "#bar b{font-weight:600}.meta{color:#9aa6b2}.warn{color:#ffcf5c}"
        "#q{width:200px;padding:3px 6px;border-radius:4px;border:1px solid #556;background:#2b3142;color:#eee}"
        ".lg{display:inline-flex;align-items:center;gap:4px;color:#cdd6e0}"
        ".lg i{width:10px;height:10px;border-radius:50%;display:inline-block}"
        ".vc{color:#cdd6e0;display:inline-flex;align-items:center;gap:4px}"
        ".vc select{padding:2px 4px;border-radius:4px;border:1px solid #556;background:#2b3142;color:#eee}"
        "#cy{position:fixed;top:44px;left:0;right:0;bottom:0;background:#fbfbfd}"
        "#info{display:none;position:fixed;left:10px;bottom:10px;max-width:60%;z-index:20;"
        "background:#1f2430;color:#eee;padding:8px 10px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.3)}"
        "#info b{font-weight:600}#info .k{color:#9aa6b2;font-size:11px;margin:2px 0}"
        "#info code{display:block;color:#a6e3a1;word-break:break-all;-webkit-user-select:all;user-select:all}"
        # 隐藏面板（#hp）：可折叠的右侧面板，#cy 的同级
        "#hp{position:fixed;top:44px;right:0;bottom:0;width:340px;z-index:15;display:flex;"
        "flex-direction:column;background:#222838;color:#dfe3ea;box-shadow:-2px 0 8px rgba(0,0,0,.3);"
        "transition:transform .18s ease;font-size:12px}"
        # 宽度相关：标签在任意宽度下都保持可见
        "#hp.collapsed{transform:translateX(calc(100% - 18px))}"
        ".hp-tab{position:absolute;left:-0px;top:0;width:18px;height:64px;background:#39415a;"
        "color:#cdd6e0;writing-mode:vertical-rl;text-align:center;font-weight:600;cursor:pointer;"
        "border-radius:4px 0 0 4px;padding:6px 1px;user-select:none}"
        # 拖动左边缘（标签下方）加宽面板，使长符号名不被裁剪
        ".hp-grip{position:absolute;left:0;top:70px;bottom:0;width:6px;cursor:ew-resize;z-index:16}"
        ".hp-grip:hover{background:#3a9457}"
        "#hp .hp-hd{display:flex;flex-wrap:wrap;gap:4px;padding:8px 8px 4px 24px}"
        "#hp .hp-hd input,#hp .hp-hd select{background:#2b3142;color:#eee;border:1px solid #556;"
        "border-radius:4px;padding:2px 4px;font-size:11px}"
        "#hp #hpq{flex:1 1 100%}.hp-num{width:56px}"
        "#hp .hp-act{display:flex;flex-wrap:wrap;gap:4px;padding:2px 8px 4px 24px;align-items:center}"
        "#hp .hp-act button{background:#39415a;color:#dfe3ea;border:1px solid #556;border-radius:4px;"
        "padding:2px 6px;font-size:11px;cursor:pointer}"
        "#hp .hp-act button.primary{background:#2f7d46;border-color:#3a9457}"
        ".hp-delta{color:#ffcf5c;flex:1 1 100%;font-size:11px}"
        ".hp-cols{display:grid;grid-template-columns:20px 1fr 64px 52px;gap:2px;padding:2px 8px 2px 24px;"
        "color:#9aa6b2;border-bottom:1px solid #39415a}"
        ".hp-cols .sortable{cursor:pointer;user-select:none}.hp-cols .sortable.active{color:#ffcf5c}"
        ".hp-list{flex:1;overflow:auto;padding:0 8px 0 24px}"
        ".hp-row{display:grid;grid-template-columns:20px 1fr 64px 52px;gap:2px;align-items:center;"
        "padding:2px 0;border-bottom:1px solid #2b3142;cursor:pointer}"
        ".hp-row .hp-lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".hp-row .hp-sub{color:#9aa6b2;font-size:10px}"
        ".hp-row.previewed{opacity:.55}.hp-row.committed{text-decoration:line-through;color:#9aa6b2}"
        ".hp-row.willreturn{color:#8ad18a}.hp-row.flash{outline:2px solid #ffcf5c;outline-offset:-2px}"
        "#hp-export-ta{margin:4px 8px 4px 24px;max-height:120px;background:#1b2030;color:#a6e3a1;"
        "border:1px solid #556;border-radius:4px;font:11px ui-monospace,Consolas,monospace}"
        ".hp-foot{padding:2px 8px 6px 24px;color:#9aa6b2;font-size:10px}"
        + modules_css +
        "</style></head><body><div id='bar'>"
        f"<b>{_html_escape(title)}</b><span class='meta'>{meta} &middot; color={legend_kind} &middot; tap node → path</span>"
        + "<span id='ms-counts' class='meta'></span>"
        + (f"<span class='warn'>&#9888; {_html_escape(banner)}</span>" if banner else "")
        + "<input id='q' placeholder='search node/path...'>"
        + view_ctl
        + f"<span style='display:flex;gap:8px;flex-wrap:wrap'>{legend}</span>"
        + "</div><div id='cy'></div><div id='info'></div>"
        + _hide_panel_html(with_modules)
    )
#### /装配顶部条与 #hp 面板的标记 ####


#### 把图渲染为单个自包含交互式 HTML 文件（sigma.js / WebGL） [@380kkm 2026-06-05] ####
def to_html(g: Graph, title: str = "manyscan dependency slice", view: str = "both",
            band_of: dict | None = None, bands_meta: list | None = None,
            default_hidden: list[str] | None = None,
            module_of: dict | None = None, modules_meta: list | None = None,
            module_mode: bool | None = None, zone_matrix: list | None = None,
            modules_list: list | None = None) -> str:
    """``default_hidden``（可选）：起始即应用隐藏、但仍列在隐藏面板中可重新启用的节点 id；
    渲染器把已排序的列表烘焙进受门控的 ``const HIDDEN=``，为 ``None`` 时省略该行。
    ``module_of`` / ``modules_meta``（可选，可折叠的 模块↔符号 商图视图）：两者都给出时，
    每个节点获得受门控的 ``attrs['module']``（带侧前缀的模块 id），并烘焙已排序的
    ``const MODULES=`` 列表，bootstrap 渲染默认全折叠的商图；为 ``None`` 时不烘焙这两项。

    ``module_mode`` / ``zone_matrix`` / ``modules_list``（可选，N 路模块分区）：``module_mode``
    为真时，跨 cluster 的边按 cross 上色，并烘焙受门控的 ``const MODULE_MODE`` / ``ZONE_MATRIX``
    / ``MODULE_LIST``，bootstrap 据此装配 N 路视图选择器 + 区矩阵面板；三者均为 ``None`` /
    缺省时一律省略，输出与不带这些参数时逐字节一致（与 HIDDEN/MODULES 同一门控纪律）。
    """
    mod_mode = bool(module_mode)

    # 着色键、重要性、节点/边属性：纯数据烘焙
    zoned, clustered, keys, kcolor, legend_kind = _color_keys(g)
    imp = _importance(g)
    n_bands = len(bands_meta) if bands_meta else 1
    nodes = _bake_nodes(g, imp, zoned, clustered, kcolor, band_of, module_of, mod_mode, n_bands)
    edges = _bake_edges(g, zoned, mod_mode)
    data_json = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)

    banner = ""
    if g.truncated:
        banner = f"bounded: capped at level {g.frontier_depth}, {g.elided} deps elided"
    elif g.depth_bounded:
        banner = f"bounded: depth-capped at level {g.frontier_depth}"

    legend = "".join(
        f'<span class="lg"><i style="background:{kcolor[k]}"></i>{_html_escape(k)}</span>' for k in keys
    )
    meta = f"{len(g.nodes)} nodes &middot; {len(g.edges)} edges"

    # 单页视图切换
    view = view if view in ("both", "internal", "dependency") else "both"
    view_opts = "".join(
        f"<option value='{v}'{' selected' if v == view else ''}>{v}</option>"
        for v in ("both", "internal", "dependency")
    )
    view_ctl = (
        "<span class='vc'>view <select id='view'>" + view_opts + "</select></span>"
        if zoned else ""
    )

    # forceAtlas2 迭代预算：按规模递减
    n_total = len(g.nodes)
    iters = 200 if n_total <= 200 else (90 if n_total <= 1200 else 45)

    lib = "".join(_script_for(asset, cdn) for asset, cdn in _SIGMA_LIBS)

    head = _head_html(title, meta, legend_kind, banner, view_ctl, legend,
                      modules_meta is not None)

    consts = _bake_consts(data_json, zoned, iters, view, bands_meta, default_hidden,
                          modules_meta, mod_mode, modules_list, zone_matrix)
    # const 放在一个裸 <script>，bootstrap 带 id="ms-boot"
    consts_tag = "<script>" + consts + "</script>"
    boot_tag = '<script id="ms-boot">' + _HTML_BOOTSTRAP + "</script>"
    # 受门控的 N 路附加脚本：module_mode 时才读取并追加
    addon_tag = ""
    if mod_mode:
        addon_tag = ('<script id="ms-module-addon">'
                     + _MODULE_ADDON_ASSET.read_text(encoding="utf-8") + "</script>")
    return head + lib + consts_tag + boot_tag + addon_tag + "</body></html>"
#### /把图渲染为单个自包含交互式 HTML 文件 ####
