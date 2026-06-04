from __future__ import annotations

from tree_sitter import Node

from enrich.langs.cpp import _cpp_name
from enrich.model import Pending


# --- glsl --------------------------------------------------------------------
_GLSL_DEFS = {
    "function_definition": "function",
    "struct_specifier": "struct",
}


def _walk_glsl(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _GLSL_DEFS:
        name = _cpp_name(node, src) or "<anonymous>"
        idx = pend.add(name, _GLSL_DEFS[t], node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_glsl(ch, src, pend, cur_parent)
