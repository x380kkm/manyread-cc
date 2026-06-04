from __future__ import annotations

from tree_sitter import Node

from enrich.langs.javascript import _js_lexical_fn_name
from enrich.model import Pending, _named_child_text, _text


# --- typescript / tsx --------------------------------------------------------
_TS_TYPE_DEFS = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}


def _walk_typescript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _TS_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _TS_TYPE_DEFS[t], node, parent_local)
        for ch in node.children:
            if ch.type == "class_heritage":
                for sub in ch.named_children:
                    if sub.type == "extends_clause":
                        for b in sub.named_children:
                            if b.type == "type_arguments":
                                continue
                            bn = _text(b, src).strip()
                            if bn:
                                pend.inherit.append((idx, bn, "extends"))
                    elif sub.type == "implements_clause":
                        for b in sub.named_children:
                            bn = _text(b, src).strip()
                            if bn:
                                pend.inherit.append((idx, bn, "implements"))
            elif ch.type == "extends_type_clause":  # interface extends
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    if bn:
                        pend.inherit.append((idx, bn, "extends"))
        cur_parent = idx

    elif t == "type_alias_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        pend.add(name, "type", node, parent_local)

    elif t == "function_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "function", node, parent_local)
        cur_parent = idx

    elif t == "method_definition":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "method", node, parent_local)
        cur_parent = idx

    elif t == "lexical_declaration":
        nm = _js_lexical_fn_name(node, src)
        if nm:
            idx = pend.add(nm, "function", node, parent_local)
            cur_parent = idx

    for ch in node.children:
        _walk_typescript(ch, src, pend, cur_parent)
