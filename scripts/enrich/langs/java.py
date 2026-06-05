from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


#### Java 类型定义节点到符号 kind 的映射 [@380kkm 2026-06-05] ####
_JAVA_TYPE_DEFS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "annotation_type_declaration": "interface",
}

#### Java 可调用单元节点到符号 kind 的映射 [@380kkm 2026-06-05] ####
_JAVA_CALLABLE = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}

#### 判定父节点是否为类型容器（决定 method/function）的 kind 集合 [@380kkm 2026-06-05] ####
_JAVA_TYPE_KINDS = frozenset(("class", "interface", "enum"))


#### 收集 superclass / interfaces 节点下的类型标识符文本 [@380kkm 2026-06-05] ####
def _java_type_names(node: Node, src: bytes) -> list[str]:
    out: list[str] = []
    for ch in node.named_children:
        if ch.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
            out.append(_text(ch, src).strip())
        else:
            out.extend(_java_type_names(ch, src))
    return [x for x in out if x]
#### /收集类型标识符 ####


#### 递归遍历 Java 语法树，收集类型/方法符号与继承边 [@380kkm 2026-06-05] ####
def _walk_java(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _JAVA_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _JAVA_TYPE_DEFS[t], node, parent_local)
        sc = node.child_by_field_name("superclass")
        if sc is not None:
            for bn in _java_type_names(sc, src):
                pend.inherit.append((idx, bn, "extends"))
        ifaces = node.child_by_field_name("interfaces")
        if ifaces is not None:
            for bn in _java_type_names(ifaces, src):
                pend.inherit.append((idx, bn, "implements"))
        cur_parent = idx
    elif t in _JAVA_CALLABLE:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        # 父节点为类型容器时记为 method，否则为 function
        kind = "method" if (parent_local is not None
                             and pend.rows[parent_local].kind in _JAVA_TYPE_KINDS) else "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_java(ch, src, pend, cur_parent)
#### /遍历 Java 语法树 ####
