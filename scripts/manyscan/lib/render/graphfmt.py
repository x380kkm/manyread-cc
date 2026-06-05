# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render.graphfmt —— 确定性的 mermaid / graphviz dot 视图。"""
from __future__ import annotations

from lib.graph import Graph


#### 转义标签文本：双引号转单引号、换行转空格 [@380kkm 2026-06-05] ####
def _esc(s: str | None) -> str:
    return (s or "").replace('"', "'").replace("\n", " ")


#### 把 node id 规整为合法的 mermaid 节点标识符 [@380kkm 2026-06-05] ####
def _mid(node_id: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in node_id)


#### 把图渲染为 mermaid flowchart 文本 [@380kkm 2026-06-05] ####
def to_mermaid(g: Graph) -> str:
    """节点、边按 id 排序;截断/深度封顶以注释行说明,边界节点附加 ``+N⤳`` 表示越界依赖数。"""
    lines = ["flowchart TD"]
    if g.truncated:
        lines.append(f"  %% truncated at level {g.frontier_depth}: {g.elided} deps elided")
    if g.depth_bounded:
        lines.append(f"  %% depth-bounded at level {g.frontier_depth}")
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        # 边界节点：把越界依赖数追加到标签
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} +{extra}⤳"
        lines.append(f'  {_mid(n.id)}["{_esc(label)}"]')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f"  {_mid(e.src)} -->|{_esc(e.relation)}| {_mid(e.dst)}")
    return "\n".join(lines) + "\n"
#### /把图渲染为 mermaid flowchart 文本 ####


#### 把图渲染为 graphviz dot 文本 [@380kkm 2026-06-05] ####
def to_dot(g: Graph) -> str:
    """节点、边按 id 排序;截断时在图底部标注 label;边界节点标签附加 ``(+N)`` 表示越界依赖数。"""
    lines = ["digraph manyscan {", "  rankdir=LR;"]
    if g.truncated:
        lines.append(f'  label="truncated@L{g.frontier_depth}: {g.elided} elided"; labelloc=b;')
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        # 边界节点：把越界依赖数追加到标签
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} (+{extra})"
        lines.append(f'  "{n.id}" [label="{_esc(label)}"];')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f'  "{e.src}" -> "{e.dst}" [label="{_esc(e.relation)}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
#### /把图渲染为 graphviz dot 文本 ####
