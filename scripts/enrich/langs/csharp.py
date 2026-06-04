from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text

#### 类型类声明（容器）节点类型 -> 符号 kind [@380kkm 2026-06-05] ####
# 嵌套在类型容器下的可调用成员上报为 `method`。
_CS_TYPE_DEFS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "record_struct_declaration": "struct",
}

#### 可调用声明节点类型 -> 符号 kind [@380kkm 2026-06-05] ####
_CS_CALLABLE_DEFS = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}

#### 视为方法宿主的类型 kind 集合 [@380kkm 2026-06-05] ####
_CS_TYPE_KINDS = frozenset(("class", "struct", "interface"))


#### 递归遍历 C# 语法树，向 pend 累加符号与继承边 [@380kkm 2026-06-05] ####
def _walk_csharp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    #### 类型声明：登记符号并解析 base_list 为继承边 [@380kkm 2026-06-05] ####
    if t in _CS_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _CS_TYPE_DEFS[t], node, parent_local)
        # 基类型位于 `base_list` 子节点：`: Base, IFoo, IBar`。C# 在语法上不区分
        # 基类与接口，故只能尽力而为：第一个基类型按 `extends` 处理，其余按
        # `implements`（C# 常见约定：基类在前、接口在后）。
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
    #### /类型声明 ####

    #### 可调用声明：按宿主区分 method 与 function 并登记 [@380kkm 2026-06-05] ####
    elif t in _CS_CALLABLE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = _CS_CALLABLE_DEFS[t]
        # 直接位于类型容器下的可调用为 method；否则（C# 中罕见的）自由函数
        # 上报为普通 function。
        if parent_local is not None and pend.rows[parent_local].kind in _CS_TYPE_KINDS:
            kind = "method"
        else:
            kind = "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    #### /可调用声明 ####

    for ch in node.children:
        _walk_csharp(ch, src, pend, cur_parent)
#### /递归遍历 C# 语法树 ####
