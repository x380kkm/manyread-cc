# audience: internal
# enrich.langs.python
from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text


#### Python 节点类型到符号 kind 的映射 [@380kkm 2026-06-05] ####
_PY_DEFS = {
    "function_definition": "function",
    "class_definition": "class",
}
#### /Python 节点类型到符号 kind 的映射 ####


#### 递归遍历 Python 语法树，收集符号与继承边 [@380kkm 2026-06-05] ####
def _walk_python(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _PY_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = _PY_DEFS[t]
        # 嵌套在 class 下的 function 是 method
        if kind == "function" and parent_local is not None and pend.rows[parent_local].kind == "class":
            kind = "method"
        idx = pend.add(name, kind, node, parent_local)
        if t == "class_definition":
            # 解析基类，逐个登记为 extends 边
            supers = node.child_by_field_name("superclasses")
            if supers is not None:
                for arg in supers.named_children:
                    bn = _text(arg, src).strip()
                    if bn:
                        pend.inherit.append((idx, bn, "extends"))
        cur_parent = idx

    for ch in node.children:
        _walk_python(ch, src, pend, cur_parent)
#### /遍历 Python 语法树 ####
