from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


# --- gdscript (Godot) --------------------------------------------------------
def _gd_first_ident(node: Node, src: bytes) -> str:
    """Fallback name extraction: first name/identifier child text."""
    for ch in node.named_children:
        if ch.type in ("name", "identifier"):
            return _text(ch, src).strip()
    return ""


def _walk_gdscript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t == "class_name_statement":
        # `class_name Foo` declares the script's own class name.
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src)
        if name:
            pend.add(name, "class", node, parent_local)
    elif t == "class_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        idx = pend.add(name, "class", node, parent_local)
        cur_parent = idx
    elif t == "function_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        kind = "method" if (parent_local is not None
                            and pend.rows[parent_local].kind == "class") else "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_gdscript(ch, src, pend, cur_parent)
