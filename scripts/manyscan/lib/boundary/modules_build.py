# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.modules_build —— N 路模块分区的有界构建流水线（build 的并行体）。

按声明模块的路径前缀逐 zone 播种（LIKE 预筛 + ESCAPE，``module_of_path`` 为权威），把每个
被达符号建为带 ``module`` 属性的一等节点，跟随其边界出边解析；落入兜底（External）的符号是
汇点（不展开），声明模块内的符号在预算内可继续展开。与 ``build`` 不同，没有单一“依赖汇”——
任意声明模块都是一等区。``dep_depth`` 控制兜底层背后的额外有界展开层（沿用 ``build`` 语义）。
"""
from __future__ import annotations

from lib import deps
from lib.graph import Budget, Edge, Evidence, Graph, Node

from .build import _MACRO_RE
from .nodes import _NORM, external_node, qualified_name
from .modulespec import ModuleSpec, like_prefix, module_of_path
from .resolve import out_edges


#### 为已索引符号构造带 module 区的节点（id 为 s<id>） [@380kkm 2026-06-05] ####
def module_symbol_node(store, symbol_id: int, spec: ModuleSpec, alias: str | None = None) -> Node:
    """``zone == cluster == module == module_of_path(path)``：cluster 驱动 html 调色板着色，
    故 N 路图无需改 render 即自动按模块上色。缺失符号防御性退化为外部节点。
    """
    row = store.symbol(symbol_id)
    if row is None:
        return external_node(f"#{symbol_id}")
    path = row["path"]
    mod = module_of_path(path, spec)
    np = _NORM(path) if path else ""
    return Node(
        id=f"s{symbol_id}",
        kind=row["kind"] or "symbol",
        label=qualified_name(store, symbol_id),
        store=alias,
        evidence=Evidence(np or None, row["start_line"]),
        attrs={"path": np, "zone": mod, "cluster": mod, "module": mod},
    )


#### 把一条边的 dst_name 在 N 路规格下解析为目标节点 + 置信度 [@380kkm 2026-06-05] ####
def resolve_module_target(store, row, spec: ModuleSpec, alias: str | None = None):
    """返回 ``(node, confidence)``。
    * 设了 ``dst_symbol_id`` -> 该符号，``direct``。
    * 否则全局按名解析：0 -> ``dep:<name>`` 外部，``unresolved``；1 -> 该符号，``unique``；
      N>1 且全部落入同一声明模块 -> 该单一候选，``unique``（同模块无歧义）；
      N>1 跨越多个模块 -> ``amb:<name>`` 多模块歧义汇点，``ambiguous``（绝不静默任选）。
    """
    dst_sid = row["dst_symbol_id"]
    if dst_sid is not None:
        return module_symbol_node(store, int(dst_sid), spec, alias), "direct"
    name = row["dst_name"] or ""
    cands = sorted(deps.resolve_edge_targets(store, name), key=lambda r: (r["path"], r["id"]))
    if not cands:
        return external_node(name), "unresolved"
    if len(cands) == 1:
        return module_symbol_node(store, int(cands[0]["id"]), spec, alias), "unique"
    mods = {module_of_path(c["path"], spec) for c in cands}
    if len(mods) == 1:
        # 全部候选同属一个声明/兜底模块：无歧义，取首候选
        return module_symbol_node(store, int(cands[0]["id"]), spec, alias), "unique"
    # 跨多个模块：建多模块歧义汇点，记录涉及的模块名（供调试谱面）
    n = ambiguous_module_node(name, sorted(mods), len(cands))
    return n, "ambiguous"


#### 为跨多个声明模块的歧义符号构造汇点（绝不锁定到某一个） [@380kkm 2026-06-05] ####
def ambiguous_module_node(name: str, modules: list[str], ambiguity: int) -> Node:
    """id 为 ``amb:<name>``；``zone == cluster == "(ambiguous)"`` 使其在调色板里自成一类，
    ``attrs['modules']`` 列出涉及的模块名，``ambiguity`` 记候选数。作为汇点不展开。"""
    return Node(id=f"amb:{name}", kind="ambiguous", label=name,
                attrs={"zone": "(ambiguous)", "cluster": "(ambiguous)", "module": "(ambiguous)",
                       "modules": list(modules), "ambiguity": ambiguity, "sink": True})


#### 逐 zone 前缀播种声明模块的种子符号行（LIKE 预筛 + module_of_path 权威） [@380kkm 2026-06-05] ####
def _seed_rows(store, spec: ModuleSpec, per_zone_cap: int | None = None) -> list[tuple[int, str]]:
    """对每个 include 前缀跑 ``path LIKE ? ESCAPE '\\'``（前缀||'%'）粗筛，再以
    ``module_of_path`` 确认（处理 exclude + 最长匹配）。规模随声明模块大小，而非库大小。
    把种子按声明序在各模块间轮转交错，使总预算公平覆盖每个模块（避免某个大模块独吞预算）。
    ``per_zone_cap`` 限定每模块取样上限（None=不限）。返回去重、交错后的 (id, path) 列表。
    """
    seen: set[int] = set()
    # zone 名 -> 该模块按 (path, id) 排序的种子列表
    by_zone: dict[str, list[tuple[int, str]]] = {z.name: [] for z in spec.zones}
    # 收集所有 include 前缀（去重）
    prefixes: set[str] = set()
    for z in spec.zones:
        prefixes.update(z.includes)
    for prefix in sorted(prefixes):
        if prefix == "":
            sql = ("SELECT s.id AS id, f.path AS path FROM symbols s "
                   "JOIN files f ON f.id = s.file_id WHERE f.path LIKE ? ORDER BY f.path, s.id")
            params: tuple = ("%",)
        else:
            sql = ("SELECT s.id AS id, f.path AS path FROM symbols s "
                   "JOIN files f ON f.id = s.file_id "
                   "WHERE f.path LIKE ? ESCAPE '\\' ORDER BY f.path, s.id")
            params = (like_prefix(prefix) + "%",)
        for r in store.conn.execute(sql, params):
            sid = int(r["id"])
            if sid in seen:
                continue
            # LIKE 仅粗筛；module_of_path 是权威（剔除兄弟同名前缀 + exclude）
            zone = module_of_path(r["path"], spec)
            if zone == spec.fallback or zone not in by_zone:
                continue
            bucket = by_zone[zone]
            if per_zone_cap is not None and len(bucket) >= per_zone_cap:
                continue
            seen.add(sid)
            bucket.append((sid, r["path"]))
    for name in by_zone:
        by_zone[name].sort(key=lambda t: (t[1], t[0]))
    # 按声明序轮转交错：第 k 轮各取每模块第 k 个种子
    order = [z.name for z in spec.zones]
    out: list[tuple[int, str]] = []
    k = 0
    remaining = True
    while remaining:
        remaining = False
        for name in order:
            bucket = by_zone[name]
            if k < len(bucket):
                out.append(bucket[k])
                remaining = True
        k += 1
    return out


#### 直接构造 N 路模块分区图：声明模块为一等区，兜底为汇点 [@380kkm 2026-06-05] ####
def build_modules(store, spec: ModuleSpec, budget: Budget, alias: str | None = None,
                  dep_depth: int = 1) -> Graph:
    """每个声明模块的种子符号都被纳入；跟随其边界出边（``extends``/``implements``/``uses_type``）
    解析。目标节点落入声明模块（一等，可在预算内继续展开）或兜底 External（汇点）。
    ``budget.max_nodes`` 是带 ``truncated``/``elided`` 计量的硬上限，逐边置信度记于
    ``g.edge_confidence``，UE ``*_API`` 宏被跳过。``dep_depth >= 2`` 在兜底表层后多展开一层，
    新加入的兜底符号标 ``dep_core``。
    """
    g = Graph()
    cap = budget.max_nodes
    confidence: dict[tuple[str, str, str], str] = {}
    truncated = False
    elided = 0

    #### 把种子符号建为一等模块节点，返回可继续展开的种子 id [@380kkm 2026-06-05] ####
    # 为展开预留预算：种子至多占约 60% 上限，使跨模块目标在展开时仍能加入（满则各模块均分）
    n_zones = max(1, len(spec.zones))
    seed_budget = max(n_zones, (cap * 3) // 5)
    per_zone_cap = max(1, seed_budget // n_zones)
    seed_ids: list[str] = []
    for sid, _path in _seed_rows(store, spec, per_zone_cap=per_zone_cap):
        if len(g.nodes) >= cap:
            truncated = True
            elided += 1
            continue
        node = module_symbol_node(store, sid, spec, alias)
        g.add_node(node)
        seed_ids.append(node.id)
    #### /建种子节点 ####

    #### 判断一个符号节点是否可继续展开（声明模块内、非汇点） [@380kkm 2026-06-05] ####
    def _expandable(node: Node) -> bool:
        if not node.id.startswith("s"):
            return False
        if node.attrs.get("sink"):
            return False
        return node.attrs.get("module") != spec.fallback
    #### /可展开判定 ####

    #### 从一组有序源 id 展开一层有界出边，返回新增的可展开符号 id [@380kkm 2026-06-05] ####
    def _expand(src_ids: list[str]) -> list[str]:
        nonlocal truncated, elided
        fresh: set[str] = set()
        for nid in src_ids:
            sid = int(nid[1:])
            src_path = g.nodes[nid].attrs.get("path")
            for er in out_edges(store, sid):
                dn = er["dst_name"]
                if dn and _MACRO_RE.match(dn):
                    continue
                node, conf = resolve_module_target(store, er, spec, alias)
                if node.id not in g.nodes:
                    if len(g.nodes) >= cap:
                        truncated = True
                        elided += 1
                        continue
                    g.add_node(node)
                    if _expandable(node):
                        fresh.add(node.id)
                edge = Edge(nid, node.id, er["relation"], Evidence(src_path, None), 1)
                g.add_edge(edge)
                confidence[edge.key()] = conf
        return sorted(fresh)
    #### /展开一层 ####

    #### 不动点：在预算内反复展开新加入的声明模块符号 [@380kkm 2026-06-05] ####
    frontier = list(seed_ids)
    expanded: set[str] = set()
    while frontier:
        nxt: list[str] = []
        for fresh in _expand(frontier):
            if fresh not in expanded:
                expanded.add(fresh)
                nxt.append(fresh)
        frontier = nxt
    #### /不动点展开 ####

    #### dep_depth>=2：兜底表层之后多展开一层，标记 dep_core [@380kkm 2026-06-05] ####
    if dep_depth >= 2:
        surface = sorted(nid for nid, n in g.nodes.items()
                         if n.id.startswith("s") and n.attrs.get("module") == spec.fallback)
        core = _expand_fallback(g, store, spec, alias, surface, cap, confidence)
        for nid, was_new in core:
            if was_new:
                g.nodes[nid].attrs["dep_core"] = 1
                g.nodes[nid].attrs["dep_depth"] = 2
        if g.truncated:
            truncated = True
    #### /dep_depth>=2 ####

    g.edge_confidence = {e.key(): confidence.get(e.key(), "direct") for e in g.edges}
    if truncated:
        g.truncated = True
        g.elided = max(elided, g.elided)
    return g
#### /直接构造 N 路模块分区图 ####


#### 从兜底表层符号多展开一层兜底依赖，返回 (id, 是否首次加入) 列表 [@380kkm 2026-06-05] ####
def _expand_fallback(g: Graph, store, spec: ModuleSpec, alias, surface: list[str], cap: int,
                     confidence: dict) -> list[tuple[str, bool]]:
    touched: list[tuple[str, bool]] = []
    truncated = False
    elided = 0
    for nid in surface:
        sid = int(nid[1:])
        src_path = g.nodes[nid].attrs.get("path")
        for er in out_edges(store, sid):
            dn = er["dst_name"]
            if dn and _MACRO_RE.match(dn):
                continue
            node, conf = resolve_module_target(store, er, spec, alias)
            was_new = node.id not in g.nodes
            if was_new:
                if len(g.nodes) >= cap:
                    truncated = True
                    elided += 1
                    continue
                g.add_node(node)
            edge = Edge(nid, node.id, er["relation"], Evidence(src_path, None), 1)
            g.add_edge(edge)
            confidence[edge.key()] = conf
            if node.id.startswith("s") and node.attrs.get("module") == spec.fallback:
                touched.append((node.id, was_new))
    if truncated:
        g.truncated = True
        g.elided = g.elided + elided
    return touched
