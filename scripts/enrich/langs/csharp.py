# audience: internal
# enrich.langs.csharp
from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text

#### 类型类声明（容器）节点类型 -> 符号 kind [@380kkm 2026-06-05] ####
_CS_TYPE_DEFS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "record_struct_declaration": "struct",
}
#### /类型类声明（容器）节点类型 -> 符号 kind ####

#### 可调用声明节点类型 -> 符号 kind [@380kkm 2026-06-05] ####
_CS_CALLABLE_DEFS = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}
#### /可调用声明节点类型 -> 符号 kind ####

#### 视为方法宿主的类型 kind 集合 [@380kkm 2026-06-05] ####
_CS_TYPE_KINDS = frozenset(("class", "struct", "interface"))


#### 递归遍历 C# 语法树，向 pend 累加符号与继承边 [@380kkm 2026-06-05] ####
def _walk_csharp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CS_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _CS_TYPE_DEFS[t], node, parent_local)
        # base_list 中第一个基类型按 extends、其余按 implements 处理
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
        # 直接位于类型容器下的可调用记为 method，否则为 function
        if parent_local is not None and pend.rows[parent_local].kind in _CS_TYPE_KINDS:
            kind = "method"
        else:
            kind = "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx

    for ch in node.children:
        _walk_csharp(ch, src, pend, cur_parent)
#### /递归遍历 C# 语法树 ####
