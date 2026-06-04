# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render.graphfmt — deterministic mermaid / graphviz dot views."""
from __future__ import annotations

from lib.graph import Graph


def _esc(s: str | None) -> str:
    return (s or "").replace('"', "'").replace("\n", " ")


def _mid(node_id: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in node_id)


# --- mermaid -----------------------------------------------------------------
def to_mermaid(g: Graph) -> str:
    lines = ["flowchart TD"]
    if g.truncated:
        lines.append(f"  %% truncated at level {g.frontier_depth}: {g.elided} deps elided")
    if g.depth_bounded:
        lines.append(f"  %% depth-bounded at level {g.frontier_depth}")
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} +{extra}⤳"
        lines.append(f'  {_mid(n.id)}["{_esc(label)}"]')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f"  {_mid(e.src)} -->|{_esc(e.relation)}| {_mid(e.dst)}")
    return "\n".join(lines) + "\n"


# --- graphviz dot ------------------------------------------------------------
def to_dot(g: Graph) -> str:
    lines = ["digraph manyscan {", "  rankdir=LR;"]
    if g.truncated:
        lines.append(f'  label="truncated@L{g.frontier_depth}: {g.elided} elided"; labelloc=b;')
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        label = n.label or n.id
        extra = g.frontier.get(n.id)
        if extra:
            label = f"{label} (+{extra})"
        lines.append(f'  "{n.id}" [label="{_esc(label)}"];')
    for e in sorted(g.edges, key=lambda e: (e.src, e.dst, e.relation)):
        lines.append(f'  "{e.src}" -> "{e.dst}" [label="{_esc(e.relation)}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
