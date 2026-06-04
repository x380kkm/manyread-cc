from __future__ import annotations

from tree_sitter import Node


# --- Symbol extraction model -------------------------------------------------
# A pending symbol row collected during the walk; parent_id is wired up after
# the file's symbols are inserted (we keep a local node->row index).
class SymRow:
    __slots__ = ("name", "kind", "start_line", "end_line",
                 "start_byte", "end_byte", "parent_local", "node", "db_id")

    def __init__(self, name, kind, node: Node, parent_local: int | None):
        self.name = name
        self.kind = kind
        self.start_line = node.start_point[0] + 1   # tree-sitter rows are 0-based
        self.end_line = node.end_point[0] + 1
        self.start_byte = node.start_byte
        self.end_byte = node.end_byte
        self.parent_local = parent_local            # index into the local rows list
        self.node = node
        self.db_id: int | None = None


class Pending:
    """Per-file accumulation of symbols + edges (resolved before DB insert)."""

    def __init__(self):
        self.rows: list[SymRow] = []
        # extends/implements: (src_local_index, dst_name, relation)
        self.inherit: list[tuple[int, str, str]] = []

    def add(self, name: str, kind: str, node: Node, parent_local: int | None) -> int:
        idx = len(self.rows)
        self.rows.append(SymRow(name, kind, node, parent_local))
        return idx


def _text(node: Node | None, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _named_child_text(node: Node, field: str, src: bytes) -> str:
    return _text(node.child_by_field_name(field), src)
