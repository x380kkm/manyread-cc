# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render.html — 自包含的交互式 HTML 视图（sigma.js）。"""
from __future__ import annotations

import json
import math
from pathlib import Path

from lib import analyze
from lib.graph import Graph


#### 自包含交互式 HTML（sigma.js / WebGL + graphology 力导布局）的素材与库清单 [@380kkm 2026-06-05] ####
# 单个文件，可在任意浏览器中打开——无需服务器/node/构建。为支撑海量节点
# （依赖级别的有界切片）而设计：sigma.js 在 GPU（WebGL）上渲染，故在数百到数千节点上
# 平移/缩放/拖拽仍流畅，而旧的 Canvas2D 渲染器在此会卡死。布局采用
# graphology-forceAtlas2（快速，在浏览器内从确定性烘焙种子细化）。保留的特性：
# 按度数定大小、hub 光晕、bridge 边、搜索、目标/依赖视图切换、点击→路径、
# 拖节点≠平移画布、诚实的截断横幅。分区由 颜色 + 空间聚类 编码（sigma 无复合父节点），
# 取代旧的浅色方框；bridge 为 红色+加粗（sigma 边在无额外程序时不能虚线）。
#
# 离线：sigma + graphology + graphology-library（forceAtlas2）均提供 UMD 构建，
# 按顺序作为普通 <script> 全局内联——graphology（核心，window.graphology）→
# graphology-library（含 forceAtlas2 等布局，window.graphologyLibrary）→
# sigma（window.Sigma）。素材缺失时逐文件回退到 CDN。sigma 的 UMD 自带与 ES5 一致的
# events polyfill，故无 ESM/node-shim 的类构造器错位（这正是不用 ESM 包的原因）。
# 输出字节（静态库文本 + DATA + 烘焙的种子位置）保持确定性；力导布局在浏览器内计算，
# 故没有任何与时间/随机相关的内容被烘焙。
_ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
_SIGMA_LIBS = [
    (_ASSET_DIR / "graphology.umd.min.js",
     "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"),
    (_ASSET_DIR / "graphology-library.min.js",
     "https://cdn.jsdelivr.net/npm/graphology-library@0.8.0/dist/graphology-library.min.js"),
    (_ASSET_DIR / "sigma.umd.min.js",
     "https://unpkg.com/sigma@2.4.0/build/sigma.min.js"),
]
_PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#9d7660"]

# bootstrap 是普通（非 f）字符串：字面的 { } 花括号。注入值以 const 块到达
# （DATA、HAS_ZONES、ITER、INITVIEW）——无 token 重写，故无 mapData/DEGMAX 的脆弱性
# （节点大小逐节点烘焙在 DATA 中）。sigma / graphology / forceAtlas2 全局来自上面内联的
# UMD <script> 标签。内容取自被 vendored 的素材文件（与 sigma 库相同的 _ASSET_DIR）并
# 逐字内联——素材内容即 bootstrap 字符串值，故输出字节与旧的内联字面量一致。
# 强制读取（无 CDN 回退：bootstrap 是应用本身，而非可选的 vendored 库）。
_BOOTSTRAP_ASSET = _ASSET_DIR / "boundary_bootstrap.js"
_HTML_BOOTSTRAP = _BOOTSTRAP_ASSET.read_text(encoding="utf-8")
#### /自包含交互式 HTML 的素材与库清单 ####


#### 隐藏面板（#hp）的 MODULES（可折叠商图）区段的静态标记 [@380kkm 2026-06-05] ####
# 仅当商图开启（已烘焙 modules_meta）时插入面板——故 OFF 页面的面板标记与 v0.6.2
# 逐字节一致。静态以便 PRISTINE_HP 把它逐字带入下钻子图。
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
    """生成隐藏面板（#hp）的标记。

    ``with_modules`` 为 False ⇒ 与 v0.6.2 逐字节一致（OFF 路径）；为 True ⇒
    在前面加上 MODULES 区段，并把 HIDE 块包进可折叠的 ``.hp-sec``，使两个子区段各有
    一个可点击的标题。
    """
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
        # v0.6.2 标记，逐字节一致
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


#### 计算每个节点的重要性信号（纯、确定性） [@380kkm 2026-06-05] ####
def _importance(g: Graph) -> dict[str, dict]:
    """每个节点的重要性信号（纯函数、确定性）。

    为每个节点 id 返回 ``{deg, fan_in, fan_out, hub, bridge}``。``fan_in``/``fan_out``
    与 :func:`analyze.node_metrics` 一致（去重邻居，排除自环）；``deg = fan_in + fan_out``
    驱动所有图的节点定大小。``hub`` 标记被重度依赖/脆弱的中心——
    ``analyze.cut_nodes`` 与高 fan_in 节点（``fan_in >= max(2, p90)``）的并集。
    ``bridge`` 标记被 ``analyze.bridges`` 关节边触及的节点（逐边 bridge 集另行返回）。
    全程仅整数且已排序，故两次运行逐字节一致。
    """
    info: dict[str, dict] = {}
    fan_in: dict[str, int] = {}
    for nid in sorted(g.nodes):
        ce = len({s for s in g.successors(nid) if s != nid})
        ca = len({p for p in g.predecessors(nid) if p != nid})
        fan_in[nid] = ca
        info[nid] = {"deg": ca + ce, "fan_in": ca, "fan_out": ce, "hub": 0, "bridge": 0}

    hubs: set[str] = set(analyze.cut_nodes(g))
    fins = sorted(fan_in.values())
    if fins:
        # 对 fan_in 取 p90（最近秩）；门限设为 >= max(2, p90)，使只有真正被依赖的中心
        # 点亮（小图至少需要 2 条入边）
        p90 = fins[min(len(fins) - 1, (90 * len(fins)) // 100)]
        gate = max(2, p90)
        hubs |= {nid for nid in g.nodes if fan_in[nid] >= gate}
    for nid in sorted(hubs):
        if nid in info:
            info[nid]["hub"] = 1

    for a, b, _rel in analyze.bridges(g):
        for nid in (a, b):
            if nid in info:
                info[nid]["bridge"] = 1
    return info


#### 分区标识与配色（由 to_html 共享） [@380kkm 2026-06-05] ####
# 依赖区可能容纳多个不同的依赖源；分区由节点 颜色 + 空间聚类 编码
_ZONES = ("target", "dependency")
_ZONE_COLOR = {"target": "#4e79a7", "dependency": "#f28e2b"}


#### 计算确定性的初始布局种子坐标 [@380kkm 2026-06-05] ####
def _seed_xy(i: int, n_total: int, zone: str | None = None,
             band: int | None = None, n_bands: int = 1) -> tuple[float, float]:
    """确定性的初始布局种子（按排序下标摆成圆；band/zone 的 x 偏置使各 band/zone 起始分开）。

    forceAtlas2 在浏览器内从此细化；四舍五入使输出字节稳定。

    给定 ``band``（n_bands > 1）时，种子 x 按 band 列偏置（步距 520），使各 band 起始
    呈左→右顺序——这是仅在跳过 forceAtlas2（离线/无 GPU）时使用的粗略回退；预期几何是
    浏览器内的 ``partitionBands`` 重映射（步距不同，故两个常量不同）。无 band 时，使用
    旧的双分区偏置。
    """
    ang = 2.0 * math.pi * i / max(1, n_total)
    x = math.cos(ang) * 120.0
    y = math.sin(ang) * 120.0
    if band is not None and n_bands > 1:
        x += (band - (n_bands - 1) / 2.0) * 520.0
    elif zone == "target":
        x -= 220.0
    elif zone == "dependency":
        x += 220.0
    return round(x, 3), round(y, 3)


#### 把图渲染为单个自包含交互式 HTML 文件（sigma.js / WebGL） [@380kkm 2026-06-05] ####
def to_html(g: Graph, title: str = "manyscan dependency slice", view: str = "both",
            band_of: dict | None = None, bands_meta: list | None = None,
            default_hidden: list[str] | None = None,
            module_of: dict | None = None, modules_meta: list | None = None) -> str:
    """把一个 Graph 渲染为单个自包含交互式 HTML 文件（sigma.js / WebGL）。

    sigma + graphology + graphology-library（forceAtlas2）从被 vendored 的 UMD 包作为
    普通 ``<script>`` 全局内联（graphology 核心 → graphology-library → sigma），仅在素材
    缺失时逐文件用 CDN ``<script src>`` 回退。图数据 + 烘焙的种子位置乘载于单个裸 const
    ``<script>``，故输出是单个离线文件，可在任意浏览器中渲染 GPU 加速、可缩放、可搜索的图。
    节点位置在浏览器内计算（forceAtlas2 从确定性烘焙种子出发），故输出字节跨运行逐字节一致。

    ``default_hidden``（可选）：一组起始即被应用隐藏（不在启动布局中）但仍列在隐藏面板中
    且可重新启用的节点 id。渲染器把已排序的列表烘焙进一行受门控的 ``const HIDDEN=``；
    为 ``None`` 时该行被完全省略，故未配置的渲染与 v0.6.0 逐字节一致。

    ``module_of`` / ``modules_meta``（可选，可折叠的 模块↔符号 商图视图）：两者都给出时，
    每个节点获得一个受门控的 ``attrs['module']``（其带侧前缀的模块 id），并烘焙一个已排序的
    ``const MODULES=`` 列表；随后 bootstrap 渲染默认全折叠的商图（模块超级节点），用户可在
    侧栏 MODULES 区段逐模块展开。为 ``None``（默认）时不烘焙 module 属性 / ``MODULES`` 行，
    故 DATA/const 块 + 运行时行为与 v0.6.2 逐字节/逐行为一致（静态 bootstrap 中惰性的商图
    机制永不运行，因为 ``MODULES`` 未定义 ⇒ ``displayed===graph``）。
    """
    n_sorted = sorted(g.nodes.values(), key=lambda n: n.id)
    n_total = len(n_sorted)

    # 这是边界图时按 分区 着色（使 目标↔依赖 凸显），否则在有内聚簇（attrs['cluster']）时
    # 按簇着色，再否则按 kind 着色
    zoned = any(n.attrs.get("zone") in _ZONES for n in g.nodes.values())
    clustered = any(n.attrs.get("cluster") for n in g.nodes.values())
    if zoned:
        keys = list(_ZONES)
        kcolor = dict(_ZONE_COLOR)
        legend_kind = "zone"
    elif clustered:
        keys = sorted({(n.attrs.get("cluster") or "?") for n in g.nodes.values()})
        kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}
        legend_kind = "cluster"
    else:
        keys = sorted({(n.kind or "node") for n in g.nodes.values()})
        kcolor = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}
        legend_kind = "kind"

    #### 取节点的展示路径：优先证据路径，否则 label/id [@380kkm 2026-06-05] ####
    def _path_of(n) -> str:
        if n.evidence is not None and getattr(n.evidence, "path", None):
            return n.evidence.path
        return n.label or n.id

    # 重要性：所有图按度数定大小 + hub/bridge 高亮标记
    imp = _importance(g)
    degmax = max([1] + [v["deg"] for v in imp.values()])

    #### 按度数与 hub 标志计算节点尺寸 [@380kkm 2026-06-05] ####
    def _size(deg: int, hub: int) -> float:
        # 按度数 4 .. 15
        base = 4.0 + (deg / degmax) * 11.0
        if hub:
            # hub 上抬并获得光晕（reducer）
            base = max(base, 12.0) + 4.0
        return round(base, 2)

    # 节点：{key, attrs}，烘焙位置 / 尺寸 / 颜色（graphology 模型）。
    # n_bands 统计 bands_meta 中的每个 band（含可能为空的 dep-core band），
    # 故缝隙几何在含/不含 dep-core 的图之间稳定
    n_bands = len(bands_meta) if bands_meta else 1
    nodes: list[dict] = []
    for i, n in enumerate(n_sorted):
        extra = g.frontier.get(n.id, 0)
        label = n.label or n.id
        if extra:
            label = f"{label}  +{extra}⤳"
        cluster = n.attrs.get("cluster") or ""
        zone = n.attrs.get("zone")
        ckey = zone if zoned else (cluster if clustered else (n.kind or "node"))
        ni = imp.get(n.id, {"deg": 0, "fan_in": 0, "hub": 0})
        nb = band_of.get(n.id, 0) if band_of is not None else None
        x, y = _seed_xy(i, n_total, zone if zoned else None, band=nb, n_bands=n_bands)
        attrs = {
            "label": label, "x": x, "y": y, "size": _size(ni["deg"], ni["hub"]),
            "color": kcolor.get(ckey, "#888"), "kind": n.kind or "node",
            "path": _path_of(n), "cluster": cluster, "frontier": extra,
            "deg": ni["deg"], "fan_in": ni["fan_in"], "hub": ni["hub"],
        }
        if zoned and zone in _ZONES:
            attrs["zone"] = zone
        # 门控于 band_of is not None：平坦图的 DATA 字节不变（字节兼容）
        if band_of is not None:
            attrs["band"] = band_of.get(n.id, 0)
        # 门控于 module_of is not None：可折叠商图只烘焙一个属性——带侧前缀的模块 id。
        # 侧由 JS 中的 MODULES[...].side 推导（单一真相源），故不烘焙单独的 modside 属性；
        # OFF ⇒ 不存在
        if module_of is not None:
            attrs["module"] = module_of.get(n.id, "")
        nodes.append({"key": n.id, "attrs": attrs})

    # 边：{key, source, target, attrs}
    edge_conf = getattr(g, "edge_confidence", {})
    bridge_keys = {(a, b, r) for a, b, r in analyze.bridges(g)}
    edges: list[dict] = []
    for i, e in enumerate(sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation))):
        ea = {"rel": e.relation, "size": 1, "color": "#c4c9d4"}
        conf = edge_conf.get(e.key())
        if conf:
            ea["conf"] = conf
            if conf == "ambiguous":
                ea["color"] = "#d98a8a"
            elif conf == "unresolved":
                ea["color"] = "#c3a3bd"
        if e.key() in bridge_keys:
            # reducer 绘成红色 + 加粗
            ea["bridge"] = 1
        if zoned:
            s, d = g.nodes.get(e.src), g.nodes.get(e.dst)
            if (s is not None and d is not None
                    and s.attrs.get("zone") == "target" and d.attrs.get("zone") == "dependency"):
                # 目标→依赖的跨越（缝隙）
                ea["cross"] = 1
                ea["size"] = 1.5
                ea["color"] = "#7f8a9c"
        edges.append({"key": f"e{i}", "source": e.src, "target": e.dst, "attrs": ea})

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

    # 单页视图切换（仅对分区图有意义；JS 在其他情形隐藏它）
    view = view if view in ("both", "internal", "dependency") else "both"
    view_opts = "".join(
        f"<option value='{v}'{' selected' if v == view else ''}>{v}</option>"
        for v in ("both", "internal", "dependency")
    )
    view_ctl = (
        "<span class='vc'>view <select id='view'>" + view_opts + "</select></span>"
        if zoned else ""
    )

    # forceAtlas2 迭代预算——大图迭代更少（布局是唯一的浏览器内 CPU 成本；WebGL 负责渲染）。
    # 按规模确定
    iters = 200 if n_total <= 200 else (90 if n_total <= 1200 else 45)

    #### 按顺序内联一个 vendored UMD 库，缺失时回退 CDN [@380kkm 2026-06-05] ####
    def _script_for(asset: Path, cdn: str) -> str:
        if asset.is_file():
            return "<script>" + asset.read_text(encoding="utf-8") + "</script>"
        return f'<script src="{cdn}"></script>'

    lib = "".join(_script_for(asset, cdn) for asset, cdn in _SIGMA_LIBS)

    # 受门控的可折叠商图 CSS：仅当烘焙了 MODULES 时追加在 .hp-foot 之后，
    # 故 OFF 页面的 <style> 字节与 v0.6.2 逐字节一致
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
    ) if modules_meta is not None else ""

    head = (
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
        + _hide_panel_html(modules_meta is not None)
    )
    consts = (
        f"const DATA={data_json};\n"
        f"const HAS_ZONES={'true' if zoned else 'false'};\n"
        f"const ITER={iters};\n"
        f"const INITVIEW={json.dumps(view)};\n"
        f"const BANDS={json.dumps(bands_meta or [], ensure_ascii=False)};\n"
    )
    # 受门控、已排序的 default-hidden 烘焙：default_hidden 为 None 时完全省略
    # （保留 v0.6.0 字节）。保持在同一个裸 const <script> 内，使离线裸标签计数（>=4）
    # 与 libTexts() 的 `const ` 前缀跳过都仍成立
    if default_hidden is not None:
        consts += f"const HIDDEN={json.dumps(sorted(default_hidden))};\n"
    # 受门控的可折叠商图：已排序的 modules_meta。为 None 时完全省略（保留 v0.6.2 字节）。
    # 乘载在同一个裸 const <script> 内，使离线裸标签计数（>=4）与 libTexts() 的 `const `
    # 前缀跳过都仍成立。该列表在 Python 端已按 id 排序（assign_modules），故两次运行一致
    if modules_meta is not None:
        consts += f"const MODULES={json.dumps(modules_meta, ensure_ascii=False)};\n"
    # 结构性：const 放在一个裸 <script>（使离线守卫仍能数出 4 个裸的 库+const 标签，
    # 且下钻子图能凭 `const ` 前缀区分它）；bootstrap 带 id="ms-boot" 使子图能逐字取回
    consts_tag = "<script>" + consts + "</script>"
    boot_tag = '<script id="ms-boot">' + _HTML_BOOTSTRAP + "</script>"
    return head + lib + consts_tag + boot_tag + "</body></html>"
#### /把图渲染为单个自包含交互式 HTML 文件 ####
