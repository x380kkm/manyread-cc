# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.views —— boundary graph 之上的派生 VIEW。

两个派生 view 及其分层 / 折叠呈现：
  * INTERNAL coupling —— target 区子图（仅 target→target 边），用于切分缝 / SCC。
  * DEPENDENCY surface —— 二部 boundary（够到某个 dependency 的 target 符号）→
    它们的 dependency target，可选按 module rollup。
此外还有 band 分配（4 层重构 view）、module 分配（可折叠的商图 view），以及
target→dependency 的 crossing 列表。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from lib import rollup
from lib.graph import Edge, Graph, Node

from .zoning import DEPENDENCY, TARGET, Zoning

#### 4 层重构 view 的 band 下标（从左到右的阅读序） [@380kkm 2026-06-05] ####
# target-core   = 没有跨越边进入 dependency 的 target 符号
# target-iface  = 有 >=1 条跨越边的 target 符号（待包裹的 call site）
# dep-iface     = 被某 target 直接引用的 dependency 符号（API 表面）
# dep-core      = 表面之后的 dependency 符号（仅经 --dep-depth 2 才出现）
TARGET_CORE, TARGET_IFACE, DEP_IFACE, DEP_CORE = 0, 1, 2, 3


#### 把 src 的相关 edge-confidence 拷贝到 dst [@380kkm 2026-06-05] ####
def _carry_confidence(src: Graph, dst: Graph) -> None:
    """把 ``src`` 上相关的 edge-confidence 条目拷贝到 ``dst``。"""
    base = getattr(src, "edge_confidence", {})
    dst.edge_confidence = {e.key(): base.get(e.key(), "direct") for e in dst.edges}


#### 取 target 区子图（仅 target→target 边） [@380kkm 2026-06-05] ####
def internal_view(g: Graph) -> Graph:
    """target 区子图（仅 target→target 边），用于切分缝 / SCC。"""
    ids = sorted(nid for nid, n in g.nodes.items() if n.attrs.get("zone") == TARGET)
    sub = g.subgraph(ids)
    _carry_confidence(g, sub)
    return sub


#### 取至少有一条出边进入 dependency 区的 target 节点 [@380kkm 2026-06-05] ####
def boundary_nodes(g: Graph) -> list[str]:
    """至少有一条出边进入 dependency 区的 target 区节点 id（已排序）。"""
    out: set[str] = set()
    for e in g.edges:
        src = g.nodes.get(e.src)
        dst = g.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if src.attrs.get("zone") == TARGET and dst.attrs.get("zone") == DEPENDENCY:
            out.add(e.src)
    return sorted(out)


#### 为分层 html view 把每个节点分到一个有序 band [@380kkm 2026-06-05] ####
def assign_bands(g: Graph, layers: str) -> tuple[dict[str, int], list[dict]]:
    """为分层 html view 把每个节点分到一个有序 band（从左到右）。

    PURE + 有序；从不改动 ``g``（同一个 ``g`` 会被 ``internal_view`` /
    ``dependency_surface`` 在非 html 格式下复用）。返回 ``(band_of, bands_meta)``，其中
    ``band_of`` 把每个节点 id 映射到一个整数 band，``bands_meta`` 是有序的
    ``{"band": i, "label": str}`` 盒子列表。

    * ``flat``（或无 zone 的 graph）-> 每个节点都在 band 0，无盒子（``[]``）
      => 渲染器发出 ``const BANDS=[]``，盒子 / 分区层成为空操作（即历史 flat 行为）。
    * ``two`` -> band 0 = 每个 ``target`` 节点，band 1 = 每个 ``dependency`` 节点。
    * ``four`` -> ``[target-core | target-iface || dep-iface | dep-core]``：
        - target-core  (0)：没有跨越边进入 dependency 的 target 节点，
        - target-iface (1)：有 >=1 条跨越边的 target 节点（``boundary_nodes``），
        - dep-iface    (2)：被某 target 直接引用的 dependency 节点，或任何未标
          ``dep_core`` 的 dependency 节点，
        - dep-core     (3)：标了 ``dep_core``（仅经 ``build(dep_depth=2)``）且不属于
          表面的 dependency 节点。
      即使第 4 个（dep-core）band 为空（在 ``--dep-depth 1`` 下），它也会保留在
      ``bands_meta`` 里，使其带框 / 带标签的盒子始终被画出 —— 这是一个有据可查的非错误
      状态。
    """
    has_zone = any(n.attrs.get("zone") in (TARGET, DEPENDENCY) for n in g.nodes.values())
    if layers == "flat" or not has_zone:
        return ({nid: 0 for nid in sorted(g.nodes)}, [])
    if layers == "two":
        band_of = {nid: (TARGET_CORE if g.nodes[nid].attrs.get("zone") == TARGET else 1)
                   for nid in sorted(g.nodes)}
        return band_of, [{"band": 0, "label": "target"},
                         {"band": 1, "label": "dependency"}]
    #### four：四层分配 [@380kkm 2026-06-05] ####
    # 带跨越边的 target 节点
    iface_targets = set(boundary_nodes(g))
    # 被某 target 直接引用的 dep 节点
    dep_surface = {e.dst for e in g.edges
                   if (g.nodes.get(e.src) is not None and g.nodes.get(e.dst) is not None
                       and g.nodes[e.src].attrs.get("zone") == TARGET
                       and g.nodes[e.dst].attrs.get("zone") == DEPENDENCY)}
    band_of = {}
    for nid in sorted(g.nodes):
        n = g.nodes[nid]
        if n.attrs.get("zone") == TARGET:
            band_of[nid] = TARGET_IFACE if nid in iface_targets else TARGET_CORE
        else:
            band_of[nid] = DEP_IFACE if (nid in dep_surface or not n.attrs.get("dep_core")) else DEP_CORE
    return band_of, [{"band": 0, "label": "target-core"}, {"band": 1, "label": "target-iface"},
                     {"band": 2, "label": "dep-iface"}, {"band": 3, "label": "dep-core"}]
    #### /four ####


#### module 超级节点的 zone 侧着色（镜像 render._ZONE_COLOR） [@380kkm 2026-06-05] ####
# 本地保留以免 boundary.py 引入 render；target=蓝，dependency=橙
_MODULE_ZONE_COLOR = {"target": "#4e79a7", "dependency": "#f28e2b"}


#### 为可折叠商图 view 做确定性的 module 分配 [@380kkm 2026-06-05] ####
def assign_modules(g: Graph, z: "Zoning", level: str = "file", store=None,
                   band_of: dict | None = None) -> tuple[dict[str, str], list[dict]]:
    """为可折叠商图 view 做确定性的 MODULE 分配。

    返回 ``(module_of, modules_meta)``，其中 ``module_of`` 把每个节点 id 映射到一个带侧
    前缀的 module id，``modules_meta`` 是按 id 排序的 module 超级节点描述符列表。

    * TARGET 侧（zone == ``target``）：module = ``attrs['path']`` 的文件 STEM
      （``level='file'``，使 ``.cpp`` + ``.h`` 成对合并）或其父 DIR（``level='dir'``）。
      无路径的 target（``amb:<name>``）-> ``(external)``。
    * DEPENDENCY 侧：带路径的符号 dep -> 经 ``rollup.roots_by_len`` 走
      ``rollup._module_of``（与 ``dependency_surface`` 用的是同一个 resolver，故 id 与
      ``--rollup-dep`` 匹配）；按名 dep（``dep:`` / ``amb:`` 无路径）-> ``(external)``。

    ``module_id = f'{side}:{raw}'`` —— ``side`` 前缀防止 target 文件 stem 与同名
    dependency module 相撞，并使合成的 ``mod:`` 超级节点 id 与
    ``s<id>``/``dep:``/``amb:`` 成员键区分开。

    PURE + 有序（``sorted(g.nodes)``），故两次运行逐字节一致。超级节点的 band 取成员
    band 的 MIN（跨 target-core/target-iface 拆分的同一文件折叠到较低 band）—— 一个有据
    可查的语义折衷。
    """
    roots = rollup.roots_by_len(store)
    module_of: dict[str, str] = {}
    members: dict[str, int] = {}
    band_min: dict[str, int] = {}
    # 有序 => 稳定
    for nid in sorted(g.nodes):
        n = g.nodes[nid]
        side = "target" if n.attrs.get("zone") == TARGET else "dependency"
        path = n.attrs.get("path") or ""
        if side == "target":
            if path:
                pp = PurePosixPath(path)
                raw = pp.stem if level == "file" else (pp.parent.as_posix() or "(root)")
            else:
                raw = "(external)"
        else:
            raw = rollup._module_of(_FakeNode(path), roots) if path else "(external)"
        mid = side + ":" + raw
        module_of[nid] = mid
        members[mid] = members.get(mid, 0) + 1
        b = band_of.get(nid, 0) if band_of is not None else 0
        band_min[mid] = b if mid not in band_min else min(band_min[mid], b)
    meta = [{"id": mid, "label": mid.split(":", 1)[1], "side": mid.split(":", 1)[0],
             "members": members[mid], "band": band_min[mid],
             "zone": mid.split(":", 1)[0], "color": _MODULE_ZONE_COLOR[mid.split(":", 1)[0]]}
            for mid in sorted(members)]
    return module_of, meta


#### 取二部 boundary 表面：target boundary 符号 → 其 dependency target [@380kkm 2026-06-05] ####
def dependency_surface(g: Graph, rollup_modules: bool = False, store=None) -> Graph:
    """二部 boundary 表面：target boundary 符号 → 它们的 dependency target。

    只保留跨越（target→dependency）边及其端点。当设置 ``rollup_modules`` 时，dependency
    target 按其 module root 分组（经 :func:`rollup.module_roots` / ``_module_of``）；
    target 节点原样保留，跨越边重新聚合到 dependency 分组上。dependency 侧可跨越多个
    dependency 源。
    """
    bset = set(boundary_nodes(g))
    keep_target = sorted(bset)
    dep_targets: set[str] = set()
    crossing: list[Edge] = []
    base_conf = getattr(g, "edge_confidence", {})
    for e in g.edges:
        if e.src in bset:
            dst = g.nodes.get(e.dst)
            if dst is not None and dst.attrs.get("zone") == DEPENDENCY:
                dep_targets.add(e.dst)
                crossing.append(e)

    out = Graph()
    for nid in keep_target:
        out.add_node(g.nodes[nid])

    if not rollup_modules:
        for nid in sorted(dep_targets):
            out.add_node(g.nodes[nid])
        conf: dict[tuple[str, str, str], str] = {}
        for e in sorted(crossing, key=lambda e: (e.src, e.dst, e.relation)):
            edge = Edge(e.src, e.dst, e.relation, e.evidence, e.weight)
            if out.add_edge(edge):
                conf[edge.key()] = base_conf.get(e.key(), "direct")
            else:
                conf.setdefault(edge.key(), base_conf.get(e.key(), "direct"))
        out.edge_confidence = conf
        return out

    #### 按 module root rollup dependency target [@380kkm 2026-06-05] ####
    # 复用 rollup.roots_by_len，使排序（及由此选出的 module）与 rollup 用的全序一致
    roots = rollup.roots_by_len(store)
    group_of: dict[str, str] = {}
    for nid in sorted(dep_targets):
        node = g.nodes[nid]
        path = node.attrs.get("path") or (node.evidence.path if node.evidence else "") or node.label
        gid = "dep:" + rollup._module_of(_FakeNode(path), roots)
        group_of[nid] = gid
    members: dict[str, int] = {}
    for gid in group_of.values():
        members[gid] = members.get(gid, 0) + 1
    for gid in sorted(members):
        out.add_node(Node(id=gid, kind="external", label=gid,
                          attrs={"zone": DEPENDENCY, "cluster": DEPENDENCY,
                                 "members": members[gid], "unresolved": True}))
    conf = {}
    for e in sorted(crossing, key=lambda e: (e.src, e.dst, e.relation)):
        gid = group_of.get(e.dst)
        if gid is None:
            continue
        edge = Edge(e.src, gid, e.relation, None, e.weight)
        if out.add_edge(edge):
            conf[edge.key()] = base_conf.get(e.key(), "direct")
    out.edge_confidence = conf
    return out
    #### /rollup dependency target ####


#### 最小的类节点 shim，使 rollup._module_of 能作用于裸路径 [@380kkm 2026-06-05] ####
class _FakeNode:
    """最小的类节点 shim，使 ``rollup._module_of``（读 ``.label``）能作用于一个裸路径。"""

    def __init__(self, path: str):
        self.label = path or ""
        self.id = self.label


#### 一次 target→dependency boundary crossing（dependency 制造的缝） [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Crossing:
    """一次 target→dependency boundary crossing：一个 dependency 制造的缝。"""

    src: str
    dst: str
    relation: str
    confidence: str
    evidence: str


#### 取所有 target→dependency crossing，按 (src, dst, relation) 排序 [@380kkm 2026-06-05] ####
def crossings(g: Graph) -> list[Crossing]:
    """所有 target→dependency crossing，按 ``(src, dst, relation)`` 排序。"""
    bset = set(boundary_nodes(g))
    conf = getattr(g, "edge_confidence", {})
    out: list[Crossing] = []
    for e in g.edges:
        if e.src not in bset:
            continue
        dst = g.nodes.get(e.dst)
        if dst is None or dst.attrs.get("zone") != DEPENDENCY:
            continue
        out.append(Crossing(
            src=e.src, dst=e.dst, relation=e.relation,
            confidence=conf.get(e.key(), "direct"),
            evidence=str(e.evidence) if e.evidence else "",
        ))
    out.sort(key=lambda c: (c.src, c.dst, c.relation))
    return out
