from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


# --- csharp ------------------------------------------------------------------
# Type-like declarations (containers) vs callables. A function nested under a
# type container is reported as a `method`.
_CS_TYPE_DEFS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "record_struct_declaration": "struct",
}
_CS_CALLABLE_DEFS = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}
_CS_TYPE_KINDS = frozenset(("class", "struct", "interface"))


def _walk_csharp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CS_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _CS_TYPE_DEFS[t], node, parent_local)
        # Base types live in a `base_list` child: `: Base, IFoo, IBar`. C# does
        # not syntactically distinguish a base class from interfaces, so this is
        # best-effort: the FIRST base type is treated as `extends`, the rest as
        # `implements` (a common C# convention: base class first, interfaces after).
        for ch in node.children:
            if ch.type == "base_list":
                first = True
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    if not bn:
                        continue
                    rel = "extends" if first else "implements"
                    pend.inherit.append((idx, bn, rel))
                    first = False
        cur_parent = idx

    elif t in _CS_CALLABLE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = _CS_CALLABLE_DEFS[t]
        # A callable directly under a type container is a method; otherwise a
        # free function (rare in C#) is reported as a plain function.
        if parent_local is not None and pend.rows[parent_local].kind in _CS_TYPE_KINDS:
            kind = "method"
        else:
            kind = "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx

    for ch in node.children:
        _walk_csharp(ch, src, pend, cur_parent)
