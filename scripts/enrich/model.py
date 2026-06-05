# audience: internal
# enrich.model
from __future__ import annotations

from tree_sitter import Node


#### 待写入的符号行：遍历时收集，parent_id 在该文件符号入库后回填 [@380kkm 2026-06-05] ####
class SymRow:
    __slots__ = ("name", "kind", "start_line", "end_line",
                 "start_byte", "end_byte", "parent_local", "node", "db_id")

    def __init__(self, name, kind, node: Node, parent_local: int | None):
        self.name = name
        self.kind = kind
        # tree-sitter 的行号从 0 开始
        self.start_line = node.start_point[0] + 1
        self.end_line = node.end_point[0] + 1
        self.start_byte = node.start_byte
        self.end_byte = node.end_byte
        # 指向本地 rows 列表的下标
        self.parent_local = parent_local
        self.node = node
        self.db_id: int | None = None
#### /待写入的符号行 ####


#### 单文件的符号 + 边累加器，入库前完成解析 [@380kkm 2026-06-05] ####
class Pending:
    def __init__(self):
        self.rows: list[SymRow] = []
        # extends/implements：(源行本地下标, 目标名, 关系)
        self.inherit: list[tuple[int, str, str]] = []

    def add(self, name: str, kind: str, node: Node, parent_local: int | None) -> int:
        idx = len(self.rows)
        self.rows.append(SymRow(name, kind, node, parent_local))
        return idx
#### /单文件的符号 + 边累加器 ####


#### 取节点覆盖的源码文本，空节点返回空串 [@380kkm 2026-06-05] ####
def _text(node: Node | None, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")
#### /取节点覆盖的源码文本 ####


#### 取某个具名子节点的文本 [@380kkm 2026-06-05] ####
def _named_child_text(node: Node, field: str, src: bytes) -> str:
    return _text(node.child_by_field_name(field), src)
#### /取某个具名子节点的文本 ####
