# audience: internal
# enrich.langs.javascript
from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


#### 取 lexical_declaration 绑定的箭头/函数表达式的名字 [@380kkm 2026-06-05] ####
def _js_lexical_fn_name(node: Node, src: bytes) -> str | None:
    for decl in node.named_children:
        if decl.type != "variable_declarator":
            continue
        val = decl.child_by_field_name("value")
        if val is not None and val.type in ("arrow_function", "function", "function_expression"):
            return _named_child_text(decl, "name", src) or _text(decl.child_by_field_name("name"), src)
    return None
#### /取 lexical_declaration 绑定的箭头/函数表达式的名字 ####


#### 递归遍历 JavaScript 语法树，收集类/函数/方法符号与继承边 [@380kkm 2026-06-05] ####
def _walk_javascript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t == "class_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "class", node, parent_local)
        heritage = None
        for ch in node.children:
            if ch.type == "class_heritage":
                heritage = ch
                break
        if heritage is not None:
            # class_heritage 形如 `extends <expr>`（可附带 ts 的 implements 子句）
            for ch in heritage.named_children:
                bn = _text(ch, src).strip()
                if not bn:
                    continue
                rel = "implements" if ch.type == "implements_clause" else "extends"
                if ch.type == "implements_clause":
                    for impl in ch.named_children:
                        nm = _text(impl, src).strip()
                        if nm:
                            pend.inherit.append((idx, nm, "implements"))
                else:
                    pend.inherit.append((idx, bn, rel))
        cur_parent = idx

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
        _walk_javascript(ch, src, pend, cur_parent)
#### /遍历 JavaScript 语法树 ####
