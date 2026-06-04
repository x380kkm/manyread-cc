from __future__ import annotations

from tree_sitter import Node

from enrich.langs.javascript import _js_lexical_fn_name
from enrich.model import Pending, _named_child_text, _text

#### 类型类声明节点类型 -> 符号 kind（typescript / tsx 共用）[@380kkm 2026-06-05] ####
_TS_TYPE_DEFS = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}


#### 递归遍历 TypeScript 语法树，向 pend 累加符号与继承边 [@380kkm 2026-06-05] ####
def _walk_typescript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    #### 类型声明：登记符号并解析 extends/implements 为继承边 [@380kkm 2026-06-05] ####
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
            # interface 的 extends 子句
            elif ch.type == "extends_type_clause":
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    if bn:
                        pend.inherit.append((idx, bn, "extends"))
        cur_parent = idx
    #### /类型声明 ####

    #### 类型别名声明：登记为 type 符号 [@380kkm 2026-06-05] ####
    elif t == "type_alias_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        pend.add(name, "type", node, parent_local)
    #### /类型别名声明 ####

    #### 函数声明：登记为 function 符号 [@380kkm 2026-06-05] ####
    elif t == "function_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "function", node, parent_local)
        cur_parent = idx
    #### /函数声明 ####

    #### 方法定义：登记为 method 符号 [@380kkm 2026-06-05] ####
    elif t == "method_definition":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "method", node, parent_local)
        cur_parent = idx
    #### /方法定义 ####

    #### 词法声明：识别 const fn = ... 形式的具名函数并登记 [@380kkm 2026-06-05] ####
    elif t == "lexical_declaration":
        nm = _js_lexical_fn_name(node, src)
        if nm:
            idx = pend.add(nm, "function", node, parent_local)
            cur_parent = idx
    #### /词法声明 ####

    for ch in node.children:
        _walk_typescript(ch, src, pend, cur_parent)
#### /递归遍历 TypeScript 语法树 ####
