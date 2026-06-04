from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _text
from enrich.macro_strip import _is_macro_type


#### 尽力提取一个 cpp 定义节点的声明符 / 名字 [@380kkm 2026-06-05] ####
def _cpp_name(node: Node, src: bytes) -> str:
    """尽力提取一个 cpp 定义节点的声明符 / 名字。"""
    # class/struct/enum/namespace 暴露 name 字段
    nm = node.child_by_field_name("name")
    if nm is not None:
        return _text(nm, src)
    # function_definition：深入声明符取函数标识符
    decl = node.child_by_field_name("declarator")
    return _cpp_declarator_name(decl, src) if decl is not None else ""


#### 沿（可能嵌套的）声明符向下走到叶子标识符 [@380kkm 2026-06-05] ####
def _cpp_declarator_name(node: Node | None, src: bytes) -> str:
    """沿（可能嵌套的）声明符向下走到叶子标识符。"""
    if node is None:
        return ""
    t = node.type
    if t in ("identifier", "field_identifier", "type_identifier",
             "qualified_identifier", "destructor_name", "operator_name"):
        return _text(node, src)
    # function_declarator / pointer_declarator / reference_declarator / 等
    inner = node.child_by_field_name("declarator")
    if inner is not None:
        return _cpp_declarator_name(inner, src)
    # 兜底：第一个像标识符的后代
    for ch in node.children:
        nm = _cpp_declarator_name(ch, src)
        if nm:
            return nm
    return ""


#### cpp 定义节点类型到符号 kind 的映射 [@380kkm 2026-06-05] ####
_CPP_DEFS = {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
    "namespace_definition": "namespace",
}

#### cpp 预处理分支节点类型到符号 kind 的映射 [@380kkm 2026-06-05] ####
_CPP_PREPROC = {
    "preproc_ifdef": "ifdef_branch",
    "preproc_if": "ifdef_branch",
    "preproc_elif": "ifdef_branch",
    "preproc_else": "ifdef_branch",
}


#### 递归收集 node 下的具名类型标识符（跳过基本类型与宏） [@380kkm 2026-06-05] ####
def _collect_type_idents(node: Node | None, src: bytes, out: list[str]) -> None:
    """递归收集 node 下的 type_identifier 叶子文本。

    跳过 primitive_type，所以 int/float/void/bool 永远不会成为 dependency —— 只有
    UObject 这类具名 / 引擎类型才会。同时跳过像宏的 token（``_is_macro_type``），所以
    解析到类型位置上的 UE 导出 / DSL 宏（UE_API、ENGINE_API、SHADER_PARAMETER、
    FORCEINLINE 等）永远不会变成假的 ``uses_type`` 依赖。
    """
    if node is None:
        return
    if node.type == "type_identifier":
        t = _text(node, src).strip()
        if t and not _is_macro_type(t):
            out.append(t)
    for ch in node.children:
        _collect_type_idents(ch, src, out)


#### 取一个函数返回类型 + 形参声明中的具名类型（去重） [@380kkm 2026-06-05] ####
def _cpp_function_type_idents(node: Node, src: bytes) -> list[str]:
    """一个函数返回类型 + 形参声明中的具名类型（去重）。"""
    out: list[str] = []
    # 返回类型
    _collect_type_idents(node.child_by_field_name("type"), src, out)
    # 形参
    _collect_type_idents(node.child_by_field_name("declarator"), src, out)
    return list(dict.fromkeys(out))


#### 为一个预处理分支生成可读标签（被测的宏 / 条件） [@380kkm 2026-06-05] ####
def _cpp_ifdef_label(node: Node, src: bytes) -> str:
    """为一个预处理分支生成可读标签（被测的宏 / 条件）。"""
    cond = node.child_by_field_name("name") or node.child_by_field_name("condition")
    if cond is not None:
        return _text(cond, src).strip() or node.type
    # else 分支没有 condition
    return node.type


#### 递归遍历 cpp 语法树，收集符号、继承边与 uses_type 边 [@380kkm 2026-06-05] ####
def _walk_cpp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CPP_DEFS:
        name = _cpp_name(node, src) or "<anonymous>"
        idx = pend.add(name, _CPP_DEFS[t], node, parent_local)
        # 从 base_class_clause 取继承（仅 class/struct）
        for ch in node.children:
            if ch.type == "base_class_clause":
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    # 剥掉漏进来的访问限定符关键字
                    for kw in ("public ", "private ", "protected ", "virtual "):
                        if bn.startswith(kw):
                            bn = bn[len(kw):].strip()
                    if bn and b.type not in ("access_specifier", "virtual"):
                        pend.inherit.append((idx, bn, "extends"))
        # uses_type：函数的返回 / 形参具名类型是它的依赖
        # （引擎类型如 UObject/FString 的成员 / 形参 / 返回 = 引擎表面）
        if t == "function_definition":
            for tn in _cpp_function_type_idents(node, src):
                pend.inherit.append((idx, tn, "uses_type"))
        cur_parent = idx

    elif t == "field_declaration":
        # class/struct 成员的具名类型是外围类型的依赖；
        # 对方法 DECLARATION（无函数体）声明符里还带有形参类型
        if parent_local is not None:
            tnames: list[str] = []
            _collect_type_idents(node.child_by_field_name("type"), src, tnames)
            _collect_type_idents(node.child_by_field_name("declarator"), src, tnames)
            for tn in dict.fromkeys(tnames):
                pend.inherit.append((parent_local, tn, "uses_type"))

    elif t in _CPP_PREPROC:
        label = _cpp_ifdef_label(node, src)
        pend.add(label, "ifdef_branch", node, parent_local)
        # 不改 cur_parent：ifdef 内的定义在 containment 上仍归属外围 scope

    for ch in node.children:
        _walk_cpp(ch, src, pend, cur_parent)
