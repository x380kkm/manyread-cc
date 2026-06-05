# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread dsl-equiv —— DSL 文件的规范 S 表达式等价校验器（纯函数、无 store）。

当 AI 重新生成或编辑一个 UE 资产 DSL 文件（matlang / bplisp / animlang）时，机器核验
其结果是否与一份**参照**在语义上等价。图层（enrich 行/边）比较被证明 **太粗**：matlang
的边不带 pin 名（把一个 multiply 的 :a/:b 连线互换后，边的多重集完全不变），且字面属性值
根本不在行里。故本工具改为规范化**解析树**：

两侧都用 tree-sitter 的 ``scheme`` 文法解析（经 ``enrich.langreg._load_language`` 加载，
以便项目级文法覆盖继续生效；lang 由扩展名经 ``langreg.LANG_FOR_EXT`` 推断，两份文件须映射
到同一文法）。随后递归规范化、整体跳过 comment 节点：

* 原子（symbol/string/number/boolean）-> ``(type, text)`` 叶。数字：两侧都能解析为数时按
  **数值**比较（``0.50 == 0.5``），否则按文本。
* list/vector 节点：遍历非括号、非 comment 子节点。**首个**子节点（head）保持为 head。其余
  子节点划分为：keyword 对 —— 以 ``:`` 开头的 symbol 与其**紧随**的子节点配成
  ``(key, canon(child))``，除非紧随者本身是 ``:`` 关键字或不存在（此时该 key 为独立 flag，
  value=None）；其余皆为**位置**子节点，顺序严格保留（bplisp 语句序列有序）。
* keyword 对按 key 做**稳定**排序 —— 跨 key 的顺序被归一化掉，但**重复的同名 key**对
  （如 bplisp ``:param (A X) :param (B Y)`` —— 参数顺序即签名顺序）保留其相对次序。**不**把
  重复折叠成字典。
* 位置项与 keyword 的交错被归一化掉（位置项归一为一个有序列表，keyword 归为另一个）——这是
  一项**刻意的容差**。

``--ignore-keys a,b,c``：key 在该清单中的 keyword 对，在比较前从**两侧**同时剔除（用例：
导出器赋的 GUID 如 ``:id``/``:event-id``，用于把一次再生与一份导出做比较）。默认空（严格）。

DIFF：以步调一致（lockstep）遍历两棵规范树，报告**每一处**差异（或前 50 处），给出形如
``function[0] > set[1] > :id`` 的人类路径，并附两侧的**原始行号**。输出模式：人类行（默认）
与 ``--json``。等价退出 0，不等价退出 1，用法/解析失败退出 2（不可解析文件的等价性未定义；
调用方应先跑 dsl_validate）。

CLI::

    uv run dsl_equiv.py A.matlang B.matlang [--ignore-keys id,event-id] [--json]
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
# 本文件在 scripts/extensions/ue/ 下；scripts/ 上溯两级，须在路径上以 import enrich.* / extensions
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _SCRIPTS)


# 规范化时整体跳过的节点类型（行注释、#|..|# 块注释、#;datum 数据注释）
_SKIP_TYPES = frozenset({"comment", "block_comment"})
# 视为原子叶的命名节点类型
_ATOM_TYPES = frozenset({"symbol", "string", "number", "boolean"})
# 报告的差异条数上限
_DIFF_CAP = 50


#### 一个规范化节点：原子叶或 list（位置项 + 已排序 keyword） [@380kkm 2026-06-05] ####
@dataclass
class Canon:
    # 'atom' | 'list'
    kind: str
    # 源文件起始行（从 1 起），仅供 diff 报告
    start_line: int
    # atom：('symbol'|'string'|'number'|'boolean', text)；list 时为 None
    atom: tuple[str, str] | None = None
    # atom 为数字且可解析时的数值，否则 None（按值比较 0.50 == 0.5）
    number: float | None = None
    # list 的 head（首个非括号子节点的 Canon），可能为 None（空 list）
    head: "Canon | None" = None
    # list 的位置子节点，顺序严格保留
    positional: list["Canon"] = field(default_factory=list)
    # list 的 keyword 对 (key, Canon|None)，按 key 稳定排序；重复同名 key 保持相对序
    keywords: list[tuple[str, "Canon | None"]] = field(default_factory=list)
#### /Canon ####


#### 尽力把数字 token 文本解析为 float，无法解析则 None [@380kkm 2026-06-05] ####
def _as_number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        # scheme 的有理数 a/b -> 按比值比较
        if "/" in text:
            a, _, b = text.partition("/")
            try:
                return float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                return None
        return None
#### /解析数字 ####


#### 取节点参与规范化的子节点：丢弃括号与 comment，按源序 [@380kkm 2026-06-05] ####
def _content_children(node) -> list:
    return [c for c in node.children if c.is_named and c.type not in _SKIP_TYPES]
#### /取内容子节点 ####


#### 把一个 tree-sitter 节点递归规范化为 Canon（comment 不敏感） [@380kkm 2026-06-05] ####
def _canon(node, src: bytes, ignore: frozenset[str]) -> Canon:
    line = node.start_point[0] + 1
    if node.type in _ATOM_TYPES:
        text = src[node.start_byte:node.end_byte].decode("utf-8", "replace")
        num = _as_number(text) if node.type == "number" else None
        return Canon("atom", line, atom=(node.type, text), number=num)

    # 非原子一律按 list/vector 处理：(..) 与 [..] 在 scheme 文法里同为 'list'
    kids = _content_children(node)
    out = Canon("list", line)
    if not kids:
        return out

    #### head 取首个内容子节点，其余划分为位置项与 keyword 对 [@380kkm 2026-06-05] ####
    out.head = _canon(kids[0], src, ignore)
    i = 1
    while i < len(kids):
        kid = kids[i]
        key = _keyword_of(kid, src)
        if key is None:
            out.positional.append(_canon(kid, src, ignore))
            i += 1
            continue
        # :key 与紧随子节点配对；紧随者是另一 :key 或缺席时，本 key 为独立 flag
        nxt = kids[i + 1] if i + 1 < len(kids) else None
        if nxt is not None and _keyword_of(nxt, src) is None:
            value = _canon(nxt, src, ignore)
            i += 2
        else:
            value = None
            i += 1
        # ignore 清单按裸 key（无前导 ':'）给出，故剥掉 ':' 再比对
        if key[1:] not in ignore:
            out.keywords.append((key, value))
    #### /划分 head/位置项/keyword ####

    # 仅按 key 稳定排序：跨 key 归一化，重复同名 key 保持相对序
    out.keywords.sort(key=lambda kv: kv[0])
    return out
#### /规范化节点 ####


#### 若节点是 ':' 开头的 symbol 关键字则返回其文本，否则 None [@380kkm 2026-06-05] ####
def _keyword_of(node, src: bytes) -> str | None:
    if node.type != "symbol":
        return None
    text = src[node.start_byte:node.end_byte].decode("utf-8", "replace")
    return text if text.startswith(":") else None
#### /关键字判定 ####


#### 把文件解析并规范化为顶层 Canon 列表（program 的内容子节点） [@380kkm 2026-06-05] ####
def canonicalize(text: str, lang: str, ignore: frozenset[str]) -> list[Canon]:
    """解析失败（tree-sitter 拒绝文件）时抛 ValueError，由 CLI 映射为退出码 2。"""
    from enrich.langreg import _load_language
    from tree_sitter import Parser

    L = _load_language(lang)
    tree = Parser(L).parse(text.encode("utf-8", "replace"))
    if tree.root_node.has_error:
        raise ValueError(f"{lang}: tree-sitter rejected the file (parse error)")
    src = text.encode("utf-8", "replace")
    return [_canon(c, src, ignore) for c in _content_children(tree.root_node)]
#### /规范化文件 ####


#### 一处差异：路径 + 种类 + 两侧文本/行号 [@380kkm 2026-06-05] ####
@dataclass
class Diff:
    # function[0] > set[1] > :id
    path: str
    # missing_left | missing_right | head | atom | arity
    kind: str
    left: str | None = None
    right: str | None = None
    left_line: int | None = None
    right_line: int | None = None

    #### 转为 --json 输出的字典，省略 None 字段 [@380kkm 2026-06-05] ####
    def to_dict(self) -> dict:
        d: dict = {"path": self.path, "kind": self.kind}
        for name in ("left", "right", "left_line", "right_line"):
            v = getattr(self, name)
            if v is not None:
                d[name] = v
        return d

    #### 人类可读单行 [@380kkm 2026-06-05] ####
    def to_line(self) -> str:
        loc = f"L{self.left_line or '-'}/L{self.right_line or '-'}"
        parts = [f"{self.kind:13} {loc:>10}  {self.path}"]
        if self.left is not None or self.right is not None:
            parts.append(f"      left={self.left!r}  right={self.right!r}")
        return "\n".join(parts)
#### /Diff ####


#### 把 atom 的 ('type', text) 渲染为简短可读标签 [@380kkm 2026-06-05] ####
def _atom_label(c: Canon | None) -> str | None:
    if c is None:
        return None
    if c.kind == "atom" and c.atom is not None:
        return c.atom[1]
    return "(list)"
#### /atom 标签 ####


#### 两个 atom 是否等价：数字按值，否则按 (type, text) [@380kkm 2026-06-05] ####
def _atoms_equal(a: Canon, b: Canon) -> bool:
    if a.number is not None and b.number is not None:
        return a.number == b.number
    return a.atom == b.atom
#### /atom 等价 ####


#### 步调一致对比两个 Canon，把差异追加到 out（达上限即停） [@380kkm 2026-06-05] ####
def _diff(a: Canon | None, b: Canon | None, path: str, out: list[Diff]) -> None:
    if len(out) >= _DIFF_CAP:
        return
    # 两侧皆缺席（如 standalone flag 两边都无值）-> 相等
    if a is None and b is None:
        return
    # 仅一侧缺席
    if a is None or b is None:
        kind = "missing_left" if a is None else "missing_right"
        out.append(Diff(path, kind, _atom_label(a), _atom_label(b),
                        a.start_line if a else None, b.start_line if b else None))
        return
    # 类型不一致（atom vs list）-> 记为 head 差异
    if a.kind != b.kind:
        out.append(Diff(path, "head", _atom_label(a), _atom_label(b),
                        a.start_line, b.start_line))
        return
    if a.kind == "atom":
        if not _atoms_equal(a, b):
            out.append(Diff(path, "atom", a.atom[1], b.atom[1],
                            a.start_line, b.start_line))
        return
    _diff_list(a, b, path, out)
#### /对比 Canon ####


#### 对比两个 list Canon 的 head / 位置项 / keyword 对 [@380kkm 2026-06-05] ####
def _diff_list(a: Canon, b: Canon, path: str, out: list[Diff]) -> None:
    head_label = _atom_label(a.head) or _atom_label(b.head) or "?"
    base = f"{path} > {head_label}" if path else head_label

    #### head 对比 [@380kkm 2026-06-05] ####
    _diff(a.head, b.head, base, out)

    #### 位置项逐位对比（数目不等先记 arity） [@380kkm 2026-06-05] ####
    if len(a.positional) != len(b.positional):
        out.append(Diff(base, "arity", str(len(a.positional)), str(len(b.positional)),
                        a.start_line, b.start_line))
    for idx in range(max(len(a.positional), len(b.positional))):
        la = a.positional[idx] if idx < len(a.positional) else None
        lb = b.positional[idx] if idx < len(b.positional) else None
        _diff(la, lb, f"{base}[{idx}]", out)
        if len(out) >= _DIFF_CAP:
            return
    #### /位置项对比 ####

    #### keyword 对逐位对比（两侧均已按 key 稳定排序） [@380kkm 2026-06-05] ####
    if len(a.keywords) != len(b.keywords):
        out.append(Diff(base, "arity",
                        f"{len(a.keywords)} kw", f"{len(b.keywords)} kw",
                        a.start_line, b.start_line))
    for idx in range(max(len(a.keywords), len(b.keywords))):
        ka = a.keywords[idx] if idx < len(a.keywords) else None
        kb = b.keywords[idx] if idx < len(b.keywords) else None
        _diff_keyword(ka, kb, base, out)
        if len(out) >= _DIFF_CAP:
            return
    #### /keyword 对比 ####
#### /对比 list ####


#### 对比一对位置对齐的 keyword 项 (key, value) [@380kkm 2026-06-05] ####
def _diff_keyword(ka, kb, base: str, out: list[Diff]) -> None:
    # key 不一致（含一侧缺席）-> 记为该 key 的缺失
    key_a = ka[0] if ka else None
    key_b = kb[0] if kb else None
    if key_a != key_b:
        if key_a is None:
            out.append(Diff(f"{base} > {key_b}", "missing_left", None, key_b))
        elif key_b is None:
            out.append(Diff(f"{base} > {key_a}", "missing_right", key_a, None))
        else:
            out.append(Diff(f"{base} > {key_a}|{key_b}", "head", key_a, key_b))
        return
    # key 相同：对比其值（standalone flag 的 value 为 None，两侧皆 None 即相等）
    _diff(ka[1], kb[1], f"{base} > {key_a}", out)
#### /对比 keyword ####


#### 比较两份文件：返回 (差异列表, 是否等价) [@380kkm 2026-06-05] ####
def compare(text_a: str, text_b: str, lang: str, ignore_keys=()) -> tuple[list[Diff], bool]:
    """解析失败时抛 ValueError，由 CLI 映射为退出码 2。"""
    ignore = frozenset(ignore_keys)
    ca = canonicalize(text_a, lang, ignore)
    cb = canonicalize(text_b, lang, ignore)
    out: list[Diff] = []
    # 顶层 form 序列按位置对比
    if len(ca) != len(cb):
        out.append(Diff("<toplevel>", "arity", str(len(ca)), str(len(cb))))
    for idx in range(max(len(ca), len(cb))):
        la = ca[idx] if idx < len(ca) else None
        lb = cb[idx] if idx < len(cb) else None
        _diff(la, lb, f"[{idx}]" if len(ca) > 1 or len(cb) > 1 else "", out)
        if len(out) >= _DIFF_CAP:
            break
    return out, not out
#### /比较文件 ####


#### 由两份文件名推断公共 lang；不一致或未知则抛 ValueError [@380kkm 2026-06-05] ####
def infer_lang(path_a: str, path_b: str) -> str:
    from enrich.langreg import LANG_FOR_EXT

    la = LANG_FOR_EXT.get(os.path.splitext(path_a)[1].lower())
    lb = LANG_FOR_EXT.get(os.path.splitext(path_b)[1].lower())
    if la is None:
        raise ValueError(f"unknown DSL extension for {path_a!r}")
    if lb is None:
        raise ValueError(f"unknown DSL extension for {path_b!r}")
    if la != lb:
        raise ValueError(f"files map to different grammars: {la} vs {lb}")
    return la
#### /推断 lang ####


#### 启用 ue 扩展，使 .matlang/.bplisp/.animlang 文法路由生效 [@380kkm 2026-06-05] ####
def _discover() -> None:
    from extensions import run_discovery
    run_discovery(["ue"])
#### /启用扩展 ####


#### CLI：解析参数、规范化对比、按 --json 或文本输出，返回退出码 [@380kkm 2026-06-05] ####
def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        prog="dsl_equiv.py",
        description="Canonical S-expr equivalence checker for UE asset DSLs "
                    "(matlang/bplisp/animlang). Pure + offline; exit 0 equivalent, "
                    "1 different, 2 usage/parse failure.")
    ap.add_argument("file_a", help="reference DSL file")
    ap.add_argument("file_b", help="candidate DSL file to compare against the reference")
    ap.add_argument("--ignore-keys", default="",
                    help="comma-separated keyword keys to drop from BOTH sides before "
                         "comparison (e.g. id,event-id for exporter GUIDs)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the diff list as JSON")
    a = ap.parse_args(argv)

    # 读取 LANG_FOR_EXT / 文法注册表之前先启用 ue 扩展
    _discover()

    try:
        lang = infer_lang(a.file_a, a.file_b)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with open(a.file_a, encoding="utf-8") as fh:
            text_a = fh.read()
        with open(a.file_b, encoding="utf-8") as fh:
            text_b = fh.read()
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 2

    ignore = [k.strip() for k in a.ignore_keys.split(",") if k.strip()]
    try:
        diffs, equivalent = compare(text_a, text_b, lang, ignore)
    except ValueError as exc:
        # 不可解析文件的等价性未定义；先跑 dsl_validate
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if a.as_json:
        print(json.dumps([d.to_dict() for d in diffs], ensure_ascii=False, indent=2))
    elif equivalent:
        print(f"EQUIVALENT  {a.file_a} == {a.file_b}  ({lang})")
    else:
        for d in diffs:
            print(d.to_line())
        print(f"-- {len(diffs)} difference(s)"
              + (" (capped)" if len(diffs) >= _DIFF_CAP else ""))
    return 0 if equivalent else 1
#### /CLI ####


if __name__ == "__main__":
    raise SystemExit(main())
