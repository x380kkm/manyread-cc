# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.lib.boundary.modules_views
"""manyscan.lib.boundary.modules_views —— N 路模块分区图之上的解耦派生 VIEW。

全部为纯图→数据变换（除有界 build 外不再查库）：边内/跨分类、NxN 区矩阵、按有序模块对的
按需符号列表（带证据 + 引用计数 = 切割代价权重）、模块级环检测（在 N 节点商图上复用迭代
Tarjan）、每模块扇入/扇出 + 不稳定度、按对的最便宜切割汇总。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from lib import graph as graphmod
from lib.graph import Edge, Graph, Node

from .modulespec import ModuleSpec, match_path


#### 取节点的模块名（即 attrs['zone']，由构建烘焙） [@380kkm 2026-06-05] ####
def _module_of_node(g: Graph, nid: str) -> str | None:
    n = g.nodes.get(nid)
    return n.attrs.get("zone") if n is not None else None


#### 把一条边分类为 intra（同模块）或 cross（跨模块） [@380kkm 2026-06-05] ####
def classify_edge(g: Graph, e) -> tuple[str, str | None, str | None]:
    """返回 ``(kind, src_module, dst_module)``，``kind`` 取 ``"intra"`` / ``"cross"``；
    任一端模块缺失时按 ``"cross"`` 处理（保守）。"""
    sm = _module_of_node(g, e.src)
    dm = _module_of_node(g, e.dst)
    if sm is not None and sm == dm:
        return "intra", sm, dm
    return "cross", sm, dm


#### 一个有序模块对 (A,B) 的跨模块统计 [@380kkm 2026-06-05] ####
@dataclass
class CrossStat:
    edge_count: int = 0
    weight: int = 0
    by_relation: Counter = field(default_factory=Counter)
    # 去重的目标符号 id 集合（切割代价 = 其大小）
    dst_symbols: set = field(default_factory=set)
    src_symbols: set = field(default_factory=set)


#### 一次 O(E) 扫描算出 NxN 区矩阵（每有序模块对的跨模块统计） [@380kkm 2026-06-05] ####
def zone_matrix(g: Graph) -> dict[tuple[str, str], CrossStat]:
    """键为 ``(src_module, dst_module)``，含对角线（intra）。在有界切片上 O(E)。"""
    mat: dict[tuple[str, str], CrossStat] = {}
    for e in g.edges:
        sm = _module_of_node(g, e.src)
        dm = _module_of_node(g, e.dst)
        if sm is None or dm is None:
            continue
        st = mat.get((sm, dm))
        if st is None:
            st = CrossStat()
            mat[(sm, dm)] = st
        st.edge_count += 1
        st.weight += e.weight
        st.by_relation[e.relation] += 1
        st.dst_symbols.add(e.dst)
        st.src_symbols.add(e.src)
    return mat


#### A 从 B 需要的一个符号（一条解耦缝） [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Need:
    src_module: str
    dst_module: str
    dst: str
    dst_label: str
    dst_path: str
    relations: tuple[str, ...]
    # 多个 A 侧符号引用它的次数（切割代价权重）
    ref_count: int
    # 该 dst 节点的 winning include 前缀（调试重叠规格用，无则空串）
    winning_prefix: str


#### 取某路径在规格下命中的 winning include 前缀（最长匹配，供调试重叠） [@380kkm 2026-06-05] ####
def winning_prefix(path: str | None, spec: ModuleSpec) -> str:
    m = match_path(path, spec)
    return m[0] if m is not None else ""


#### 按 (A,B,dst) 累积跨模块边证据：relations / 引用源 / label / path / winning [@380kkm 2026-06-05] ####
def _accumulate_needs(g: Graph, spec: ModuleSpec) -> dict[tuple[str, str, str], dict]:
    # (A,B,dst) -> {relations:set, refs:set(src), label, path, winning}
    acc: dict[tuple[str, str, str], dict] = {}
    for e in g.edges:
        sm = _module_of_node(g, e.src)
        dm = _module_of_node(g, e.dst)
        if sm is None or dm is None or sm == dm:
            continue
        dn = g.nodes[e.dst]
        key = (sm, dm, e.dst)
        rec = acc.get(key)
        if rec is None:
            rec = {"relations": set(), "refs": set(),
                   "label": dn.label or e.dst,
                   "path": dn.attrs.get("path") or "",
                   "winning": winning_prefix(dn.attrs.get("path"), spec)}
            acc[key] = rec
        rec["relations"].add(e.relation)
        rec["refs"].add(e.src)
    return acc


#### 把累积证据装配成按对 (A,B) 的 Need 列表（每对按 dst 排序） [@380kkm 2026-06-05] ####
def _build_needs(acc: dict[tuple[str, str, str], dict]) -> dict[tuple[str, str], list[Need]]:
    out: dict[tuple[str, str], list[Need]] = {}
    for (sm, dm, dst), rec in acc.items():
        out.setdefault((sm, dm), []).append(Need(
            src_module=sm, dst_module=dm, dst=dst, dst_label=rec["label"],
            dst_path=rec["path"], relations=tuple(sorted(rec["relations"])),
            ref_count=len(rec["refs"]), winning_prefix=rec["winning"]))
    for pair in out:
        out[pair].sort(key=lambda nd: (nd.dst,))
    return out


#### 按有序模块对 (A,B) 列出 A 从 B 需要的符号（带证据 + 引用计数） [@380kkm 2026-06-05] ####
def needed_symbols(g: Graph, spec: ModuleSpec) -> dict[tuple[str, str], list[Need]]:
    """泛化 ``crossings``：键为 ``(src_module, dst_module)``（A≠B），值为去重的 :class:`Need`
    列表（按 dst 排序）。``ref_count`` 记多少个 A 侧符号引用该 dst（切割代价权重）。
    """
    return _build_needs(_accumulate_needs(g, spec))


#### 在模块级商图上检测环：返回参与环的模块组列表（每组 >1） [@380kkm 2026-06-05] ####
def module_cycles(g: Graph) -> list[list[str]]:
    """构造 N 节点商图（每声明/兜底模块一节点，A->B 当且仅当存在跨模块边 A->B），复用迭代
    Tarjan。任何 size>1 的 SCC 即一个模块环。N 是模块数（非符号数），平凡有界。
    """
    quo = Graph()
    mods = sorted({m for nid in g.nodes if (m := _module_of_node(g, nid)) is not None})
    for m in mods:
        quo.add_node(Node(id=m, kind="module", label=m))
    seen: set[tuple[str, str]] = set()
    for e in g.edges:
        sm = _module_of_node(g, e.src)
        dm = _module_of_node(g, e.dst)
        if sm is None or dm is None or sm == dm:
            continue
        if (sm, dm) not in seen:
            seen.add((sm, dm))
            quo.add_edge(Edge(sm, dm, "depends"))
    return [comp for comp in graphmod.scc(quo) if len(comp) > 1]


#### 一个模块的扇入/扇出 + 不稳定度 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ModuleFanStat:
    module: str
    fan_in: int
    fan_out: int
    # I = fan_out / (fan_in + fan_out)，与 analyze 对文件的口径一致；无边时为 0.0
    instability: float


#### 由区矩阵算出每模块的扇入/扇出 + 不稳定度 [@380kkm 2026-06-05] ####
def fan_stats(mat: dict[tuple[str, str], CrossStat]) -> list[ModuleFanStat]:
    mods = sorted({a for a, _ in mat} | {b for _, b in mat})
    out: list[ModuleFanStat] = []
    for m in mods:
        fo = sum(s.edge_count for (a, b), s in mat.items() if a == m and b != m)
        fi = sum(s.edge_count for (a, b), s in mat.items() if b == m and a != m)
        denom = fi + fo
        inst = round(fo / denom, 4) if denom else 0.0
        out.append(ModuleFanStat(module=m, fan_in=fi, fan_out=fo, instability=inst))
    return out


#### 一个有序模块对 (A,B) 的切割代价 = A 从 B 需要的去重符号数 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class CutCost:
    src_module: str
    dst_module: str
    # 须切断/前向声明的去重 dst 符号数
    cost: int
    edge_count: int


#### 由按需符号列表算出每有序模块对的切割代价（按 cost 升序，最便宜在前） [@380kkm 2026-06-05] ####
def cut_costs(needed: dict[tuple[str, str], list], mat: dict[tuple[str, str], CrossStat]
              ) -> list[CutCost]:
    out: list[CutCost] = []
    for (a, b), needs in needed.items():
        ec = mat[(a, b)].edge_count if (a, b) in mat else 0
        out.append(CutCost(src_module=a, dst_module=b, cost=len(needs), edge_count=ec))
    out.sort(key=lambda c: (c.cost, c.src_module, c.dst_module))
    return out
