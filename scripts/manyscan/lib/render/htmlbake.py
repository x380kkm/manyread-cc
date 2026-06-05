# audience: internal
# manyscan.lib.render.htmlbake
"""manyscan.lib.render.htmlbake — 把图烘焙为 HTML 视图的纯数据（确定性，无 IO）。

本模块只把 `Graph` 折算成 sigma.js 渲染所需的素材：重要性信号、布局种子坐标、
着色键、节点/边属性列表、烘焙进 `<script>` 的 `const` 块。所有产出按 id 排序，
两次烘焙逐字节一致。HTML/CSS 外壳与素材内联留在 `html.py`，本模块不碰 IO。
"""
from __future__ import annotations

import json
import math

from lib import analyze
from lib.graph import Graph

_PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
            "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#9d7660"]

#### 分区标识与配色（由 to_html 共享） [@380kkm 2026-06-05] ####
_ZONES = ("target", "dependency")
_ZONE_COLOR = {"target": "#4e79a7", "dependency": "#f28e2b"}


#### 计算每个节点的重要性信号（纯、确定性） [@380kkm 2026-06-05] ####
def _importance(g: Graph) -> dict[str, dict]:
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
        # 取 fan_in 的 p90（最近秩），门限为 max(2, p90)
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


#### 计算确定性的初始布局种子坐标 [@380kkm 2026-06-05] ####
def _seed_xy(i: int, n_total: int, zone: str | None = None,
             band: int | None = None, n_bands: int = 1) -> tuple[float, float]:
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


#### 取节点的展示路径：优先证据路径，否则 label/id [@380kkm 2026-06-05] ####
def _path_of(n) -> str:
    if n.evidence is not None and getattr(n.evidence, "path", None):
        return n.evidence.path
    return n.label or n.id


#### 选着色键与配色：边界图按分区，有簇时按簇，否则按 kind [@380kkm 2026-06-05] ####
def _color_keys(g: Graph) -> tuple[bool, bool, list[str], dict[str, str], str]:
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
    return zoned, clustered, keys, kcolor, legend_kind


#### 烘焙节点属性列表：位置 / 尺寸 / 颜色 / 重要性 / 模块 [@380kkm 2026-06-05] ####
def _bake_nodes(g: Graph, imp: dict[str, dict], zoned: bool, clustered: bool,
                kcolor: dict[str, str], band_of: dict | None,
                module_of: dict | None, mod_mode: bool, n_bands: int) -> list[dict]:
    n_sorted = sorted(g.nodes.values(), key=lambda n: n.id)
    n_total = len(n_sorted)
    degmax = max([1] + [v["deg"] for v in imp.values()])

    #### 按度数与 hub 标志计算节点尺寸 [@380kkm 2026-06-05] ####
    def _size(deg: int, hub: int) -> float:
        # 按度数 4 .. 15
        base = 4.0 + (deg / degmax) * 11.0
        if hub:
            # hub 上抬并获得光晕（reducer）
            base = max(base, 12.0) + 4.0
        return round(base, 2)

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
        if band_of is not None:
            attrs["band"] = band_of.get(n.id, 0)
        # 烘焙带侧前缀的模块 id
        if module_of is not None:
            attrs["module"] = module_of.get(n.id, "")
        # N 路分区把每节点的模块名烘焙到 attrs['module']
        elif mod_mode:
            attrs["module"] = n.attrs.get("module") or n.attrs.get("cluster") or ""
        nodes.append({"key": n.id, "attrs": attrs})
    return nodes


#### 烘焙边属性列表：关系 / 置信度 / bridge / 跨越上色 [@380kkm 2026-06-05] ####
def _bake_edges(g: Graph, zoned: bool, mod_mode: bool) -> list[dict]:
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
        elif mod_mode:
            s, d = g.nodes.get(e.src), g.nodes.get(e.dst)
            if (s is not None and d is not None
                    and s.attrs.get("cluster") != d.attrs.get("cluster")):
                # 跨模块的跨越（解耦缝）
                ea["cross"] = 1
                ea["size"] = 1.5
                ea["color"] = "#7f8a9c"
        edges.append({"key": f"e{i}", "source": e.src, "target": e.dst, "attrs": ea})
    return edges


#### 烘焙裸 const 块：DATA + 受门控的 HIDDEN / MODULES / N 路分区 [@380kkm 2026-06-05] ####
def _bake_consts(data_json: str, zoned: bool, iters: int, view: str,
                 bands_meta: list | None, default_hidden: list[str] | None,
                 modules_meta: list | None, mod_mode: bool,
                 modules_list: list | None, zone_matrix: list | None) -> str:
    consts = (
        f"const DATA={data_json};\n"
        f"const HAS_ZONES={'true' if zoned else 'false'};\n"
        f"const ITER={iters};\n"
        f"const INITVIEW={json.dumps(view)};\n"
        f"const BANDS={json.dumps(bands_meta or [], ensure_ascii=False)};\n"
    )
    # 受门控、已排序的 default-hidden 烘焙：None 时完全省略
    if default_hidden is not None:
        consts += f"const HIDDEN={json.dumps(sorted(default_hidden))};\n"
    # 受门控的可折叠商图：已排序的 modules_meta，为 None 时完全省略
    if modules_meta is not None:
        consts += f"const MODULES={json.dumps(modules_meta, ensure_ascii=False)};\n"
    # 受门控的 N 路模块分区：module_mode 为真时烘焙，其它情形完全省略
    if mod_mode:
        consts += "const MODULE_MODE=true;\n"
        consts += f"const MODULE_LIST={json.dumps(modules_list or [], ensure_ascii=False)};\n"
        consts += f"const ZONE_MATRIX={json.dumps(zone_matrix or [], ensure_ascii=False)};\n"
    return consts
