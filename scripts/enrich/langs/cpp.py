from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _text
from enrich.macro_strip import _is_macro_type


# --- cpp ---------------------------------------------------------------------
def _cpp_name(node: Node, src: bytes) -> str:
    """Best-effort declarator/name extraction for a cpp definition node."""
    # class/struct/enum/namespace expose a `name` field.
    nm = node.child_by_field_name("name")
    if nm is not None:
        return _text(nm, src)
    # function_definition: dig into the declarator for the function identifier.
    decl = node.child_by_field_name("declarator")
    return _cpp_declarator_name(decl, src) if decl is not None else ""


def _cpp_declarator_name(node: Node | None, src: bytes) -> str:
    """Walk a (possibly nested) declarator down to the leaf identifier."""
    if node is None:
        return ""
    t = node.type
    if t in ("identifier", "field_identifier", "type_identifier",
             "qualified_identifier", "destructor_name", "operator_name"):
        return _text(node, src)
    # function_declarator / pointer_declarator / reference_declarator / etc.
    inner = node.child_by_field_name("declarator")
    if inner is not None:
        return _cpp_declarator_name(inner, src)
    # Fall back: first identifier-ish descendant.
    for ch in node.children:
        nm = _cpp_declarator_name(ch, src)
        if nm:
            return nm
    return ""


_CPP_DEFS = {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
    "namespace_definition": "namespace",
}
_CPP_PREPROC = {
    "preproc_ifdef": "ifdef_branch",
    "preproc_if": "ifdef_branch",
    "preproc_elif": "ifdef_branch",
    "preproc_else": "ifdef_branch",
}


def _collect_type_idents(node: Node | None, src: bytes, out: list[str]) -> None:
    """Gather `type_identifier` leaf texts under node (skips primitive_type, so
    int/float/void/bool never become deps — only named/engine types like UObject).
    Also skips macro-like tokens (`_is_macro_type`) so UE export/DSL macros parsed in a
    type position (UE_API, ENGINE_API, SHADER_PARAMETER, FORCEINLINE, …) never become
    bogus `uses_type` dependencies."""
    if node is None:
        return
    if node.type == "type_identifier":
        t = _text(node, src).strip()
        if t and not _is_macro_type(t):
            out.append(t)
    for ch in node.children:
        _collect_type_idents(ch, src, out)


def _cpp_function_type_idents(node: Node, src: bytes) -> list[str]:
    """Named types in a function's return + parameter declarations (deduped)."""
    out: list[str] = []
    _collect_type_idents(node.child_by_field_name("type"), src, out)       # return type
    _collect_type_idents(node.child_by_field_name("declarator"), src, out)  # params
    return list(dict.fromkeys(out))


def _cpp_ifdef_label(node: Node, src: bytes) -> str:
    """A readable label for a preproc branch (the macro / condition tested)."""
    cond = node.child_by_field_name("name") or node.child_by_field_name("condition")
    if cond is not None:
        return _text(cond, src).strip() or node.type
    # else arm has no condition.
    return node.type


def _walk_cpp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CPP_DEFS:
        name = _cpp_name(node, src) or "<anonymous>"
        idx = pend.add(name, _CPP_DEFS[t], node, parent_local)
        # Inheritance from base_class_clause (class/struct only).
        for ch in node.children:
            if ch.type == "base_class_clause":
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    # strip access-specifier keywords if they leaked in.
                    for kw in ("public ", "private ", "protected ", "virtual "):
                        if bn.startswith(kw):
                            bn = bn[len(kw):].strip()
                    if bn and b.type not in ("access_specifier", "virtual"):
                        pend.inherit.append((idx, bn, "extends"))
        # uses_type: a function's return/param named types are dependencies of it
        # (member/param/return on engine types like UObject/FString = the engine surface).
        if t == "function_definition":
            for tn in _cpp_function_type_idents(node, src):
                pend.inherit.append((idx, tn, "uses_type"))
        cur_parent = idx

    elif t == "field_declaration":
        # a class/struct member's named type is a dependency of the enclosing type;
        # for a method DECLARATION (no body) the declarator holds param types too.
        if parent_local is not None:
            tnames: list[str] = []
            _collect_type_idents(node.child_by_field_name("type"), src, tnames)
            _collect_type_idents(node.child_by_field_name("declarator"), src, tnames)
            for tn in dict.fromkeys(tnames):
                pend.inherit.append((parent_local, tn, "uses_type"))

    elif t in _CPP_PREPROC:
        label = _cpp_ifdef_label(node, src)
        pend.add(label, "ifdef_branch", node, parent_local)
        # do NOT change cur_parent: defs inside an ifdef still belong to the
        # surrounding scope for containment purposes.

    for ch in node.children:
        _walk_cpp(ch, src, pend, cur_parent)
