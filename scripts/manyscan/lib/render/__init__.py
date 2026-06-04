# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.render — deterministic JSON / mermaid / dot / text / HTML views.

Every emitter sorts its output so results are stable (golden-testable). The
bounded-expansion accounting is rendered *explicitly* everywhere — a frontier node
is tagged ``+N⤳`` and a truncated/ depth-bounded slice prints a visible warning —
so a budget-capped slice can never be mistaken for a complete one.

This package is the FACADE: callers ``from lib import render`` and use
``render.<name>`` unchanged. The emitters live in per-format submodules
(jsonfmt / graphfmt / textfmt / html); this module re-exports their public
surface and owns the format registry (the single-match ``FORMATS`` factory).
"""
from __future__ import annotations

from lib.graph import Graph

from .graphfmt import to_dot, to_mermaid
from .html import _importance, to_html
from .jsonfmt import graph_to_dict, metrics_to_dict, to_json
from .textfmt import metrics_text, to_text

FORMATS = {"json": to_json, "mermaid": to_mermaid, "dot": to_dot, "text": to_text, "html": to_html}


def render(g: Graph, fmt: str) -> str:
    """Render a Graph in ``fmt`` (json|mermaid|dot|text|html)."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r} (use {'/'.join(FORMATS)})")
    return FORMATS[fmt](g)


__all__ = [
    "render", "FORMATS",
    "to_json", "to_html", "to_mermaid", "to_dot", "to_text",
    "graph_to_dict", "metrics_to_dict", "metrics_text", "_importance",
]
