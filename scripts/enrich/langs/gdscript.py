from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


#### 兜底取名：返回第一个 name/identifier 子节点的文本 [@380kkm 2026-06-05] ####
def _gd_first_ident(node: Node, src: bytes) -> str:
    for ch in node.named_children:
        if ch.type in ("name", "identifier"):
            return _text(ch, src).strip()
    return ""


#### 递归遍历 GDScript 语法树，收集类与函数/方法符号 [@380kkm 2026-06-05] ####
def _walk_gdscript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t == "class_name_statement":
        # `class_name Foo` 声明脚本自身的类名
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src)
        if name:
            pend.add(name, "class", node, parent_local)
    elif t == "class_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        idx = pend.add(name, "class", node, parent_local)
        cur_parent = idx
    elif t == "function_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        # 父节点是 class 时为方法，否则为顶层函数
        kind = "method" if (parent_local is not None
                            and pend.rows[parent_local].kind == "class") else "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_gdscript(ch, src, pend, cur_parent)
