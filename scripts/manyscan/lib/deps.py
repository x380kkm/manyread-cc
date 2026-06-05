# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.deps —— 基于存储库的真实跨文件依赖提取。

manyread 不存任何 import 图，且只在「单个文件内」解析继承。本模块补上 manyscan
构建真实跨文件边所需的两件事：

  * 从 ``files.content`` 提取 import/include（``extract_imports`` 是纯函数、可单测；
    ``file_imports`` 为某个存储库文件抓取并提取），再尽力把每条 import 解析到
    同一存储库内的目标文件（``resolve_import``）—— 得到真实的「文件 -> 文件」边。
  * 把 manyread 边的 ``dst_name``（extends/implements/references，manyread 只在
    文件内解析）全局解析为跨所有文件的候选符号（``resolve_edge_targets``）；
    ``len(result)`` 即歧义度。

全部为只读、且携带证据（行号 / 文件路径）。
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from lib import stores

#### 扩展名 -> import 规则族 [@380kkm 2026-06-05] ####
_PY = {".py", ".pyi"}
_CPP = {".h", ".hpp", ".hh", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx", ".hxx", ".cu", ".cuh"}
_CS = {".cs"}
_JS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}


#### 单条 import/include 出现记录及其证据 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ImportRef:
    # 去空白后的源码行
    raw: str
    # 提取出的模块 / 头文件 / specifier 词元
    target: str
    # 1 起的行号
    line: int
    # python | cpp_include | csharp_using | js_import
    kind: str


#### 各语言族的 import 匹配正则 [@380kkm 2026-06-05] ####
_RE_PY_FROM = re.compile(r"^\s*from\s+([.\w]+)\s+import\b")
_RE_PY_IMPORT = re.compile(r"^\s*import\s+(.+)$")
_RE_CPP_INC = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')
_RE_CS_USING = re.compile(r"^\s*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")
_RE_JS_SPEC = re.compile(
    r"""(?:\bfrom|\bimport|\brequire\s*\()\s*['"]([^'"]+)['"]"""
)


#### 由文件扩展名返回 import 规则族，不支持则返回 None [@380kkm 2026-06-05] ####
def family(ext: str | None) -> str | None:
    ext = (ext or "").lower()
    if ext in _PY:
        return "python"
    if ext in _CPP:
        return "cpp"
    if ext in _CS:
        return "csharp"
    if ext in _JS:
        return "js"
    return None


#### 纯函数：按扩展名从源码 content 提取 import/include 引用 [@380kkm 2026-06-05] ####
def extract_imports(content: str, ext: str | None) -> list[ImportRef]:
    fam = family(ext)
    if fam is None or not content:
        return []
    out: list[ImportRef] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if fam == "python":
            m = _RE_PY_FROM.match(line)
            if m:
                out.append(ImportRef(line.strip(), m.group(1), i, "python"))
                continue
            m = _RE_PY_IMPORT.match(line)
            if m:
                for part in m.group(1).split(","):
                    tok = part.split(" as ")[0].split("#")[0].strip()
                    if tok:
                        out.append(ImportRef(line.strip(), tok, i, "python"))
        elif fam == "cpp":
            m = _RE_CPP_INC.match(line)
            if m:
                out.append(ImportRef(line.strip(), m.group(1), i, "cpp_include"))
        elif fam == "csharp":
            m = _RE_CS_USING.match(line)
            # 跳过 `using (var x = ...)` 资源语句
            if m and "(" not in line:
                out.append(ImportRef(line.strip(), m.group(1), i, "csharp_using"))
        elif fam == "js":
            for m in _RE_JS_SPEC.finditer(line):
                out.append(ImportRef(line.strip(), m.group(1), i, "js_import"))
    return out


#### 为某个存储库文件提取 import（抓取其 content + ext）[@380kkm 2026-06-05] ####
def file_imports(store: "stores.Store", file_id: int) -> list[ImportRef]:
    row = store.file(file_id)
    if row is None or row["content"] is None:
        return []
    return extract_imports(row["content"], row["ext"])


#### 返回（斜杠归一化后）路径匹配某候选的 file_id，否则 None [@380kkm 2026-06-05] ####
def _match_path(store: "stores.Store", candidates: list[str], *,
                suffix: bool = False, basename: bool = False) -> int | None:
    # 把 Windows 反斜杠归一化为 '/'
    norm = "replace(path, char(92), '/')"
    cands = [c.replace("\\", "/").lstrip("./") for c in candidates if c]
    # 精确匹配
    for c in cands:
        row = store.conn.execute(f"SELECT id FROM files WHERE {norm} = ?", (c,)).fetchone()
        if row:
            return row["id"]
    if suffix:
        for c in cands:
            row = store.conn.execute(
                f"SELECT id FROM files WHERE {norm} LIKE ? ORDER BY length(path) LIMIT 1",
                ("%/" + c,),
            ).fetchone()
            if row:
                return row["id"]
    if basename:
        for c in cands:
            bn = PurePosixPath(c).name
            row = store.conn.execute(
                f"SELECT id FROM files WHERE {norm} LIKE ? ORDER BY length(path) LIMIT 1",
                ("%/" + bn,),
            ).fetchone()
            if row:
                return row["id"]
    return None


#### 内存中的文件路径索引，用于快速 import 解析 [@380kkm 2026-06-05] ####
class PathIndex:
    """一条 ``SELECT id,path`` 构建后，以 O(1)/O(小) 回答精确 / 后缀 / 基名查询。"""

    #### 从存储库一次性加载所有文件路径，建立三种查询索引 [@380kkm 2026-06-05] ####
    def __init__(self, store: "stores.Store"):
        self.by_path: dict[str, int] = {}
        self.path_of: dict[int, str] = {}
        self.by_basename: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for row in store.conn.execute("SELECT id, path FROM files"):
            p = (row["path"] or "").replace("\\", "/")
            self.by_path[p] = row["id"]
            self.path_of[row["id"]] = p
            self.by_basename[p.rsplit("/", 1)[-1]].append((row["id"], p))

    #### 返回按 Store 缓存的 PathIndex（仅构建一次，O(files) 加载摊销到整次扫描）[@380kkm 2026-06-05] ####
    @classmethod
    def for_store(cls, store: "stores.Store") -> "PathIndex":
        cached = getattr(store, "_ms_path_index", None)
        if cached is None:
            cached = cls(store)
            store._ms_path_index = cached
        return cached

    #### 归一化候选路径：反斜杠转 '/' 并去掉前导 './' [@380kkm 2026-06-05] ####
    @staticmethod
    def _norm(c: str) -> str:
        return c.replace("\\", "/").lstrip("./")

    #### 按精确 / 后缀 / 基名顺序匹配候选，返回 file_id 或 None [@380kkm 2026-06-05] ####
    def match(self, candidates: list[str], *, suffix: bool = False,
              basename: bool = False) -> int | None:
        cands = [self._norm(c) for c in candidates if c]
        # 精确匹配
        for c in cands:
            hit = self.by_path.get(c)
            if hit is not None:
                return hit
        if suffix:
            for c in cands:
                bn = c.rsplit("/", 1)[-1]
                ms = [(i, p) for (i, p) in self.by_basename.get(bn, ()) if p == c or p.endswith("/" + c)]
                if ms:
                    return min(ms, key=lambda t: len(t[1]))[0]
        if basename:
            for c in cands:
                ms = self.by_basename.get(PurePosixPath(c).name, [])
                if ms:
                    return min(ms, key=lambda t: len(t[1]))[0]
        return None
#### /内存中的文件路径索引 ####


#### 为一条 import 返回 (候选路径, 是否后缀匹配, 是否基名匹配)，无法映射时返回 None [@380kkm 2026-06-05] ####
def _candidates_for(ref: ImportRef, from_path: str | None) -> tuple[list[str], bool, bool] | None:
    if ref.kind == "python":
        mod = ref.target
        if mod.startswith("."):
            base = PurePosixPath((from_path or "").replace("\\", "/")).parent
            ups = len(mod) - len(mod.lstrip("."))
            for _ in range(max(0, ups - 1)):
                base = base.parent
            rest = mod.lstrip(".").replace(".", "/")
            stem = str(base / rest) if rest else str(base)
            return ([stem + ".py", stem + "/__init__.py"], False, False)
        rel = mod.replace(".", "/")
        return ([rel + ".py", rel + "/__init__.py"], True, False)
    if ref.kind == "cpp_include":
        return ([ref.target], True, True)
    if ref.kind == "js_import":
        spec = ref.target
        if not spec.startswith("."):
            # 裸模块 = 外部依赖
            return None
        base = PurePosixPath((from_path or "").replace("\\", "/")).parent
        stem = str(base / spec)
        exts = ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"]
        return ([stem + e for e in exts], True, False)
    # csharp_using 命名空间不与文件 1:1 对应
    return None


#### 尽力而为：把一条 ImportRef 映射为同一存储库内的目标 file_id [@380kkm 2026-06-05] ####
def resolve_import(store: "stores.Store", ref: ImportRef, from_path: str | None = None,
                   index: "PathIndex | None" = None) -> int | None:
    """传入 :class:`PathIndex` 走索引匹配，不传则回退到 SQL 匹配；外部依赖 / 无法解析返回 None。"""
    spec = _candidates_for(ref, from_path)
    if spec is None:
        return None
    cands, suffix, basename = spec
    if index is not None:
        return index.match(cands, suffix=suffix, basename=basename)
    return _match_path(store, cands, suffix=suffix, basename=basename)


#### 把一条边的 dst_name 全局解析为跨所有文件的候选符号 [@380kkm 2026-06-05] ####
def resolve_edge_targets(store: "stores.Store", dst_name: str,
                         kinds: set[str] | None = None) -> list[sqlite3.Row]:
    """按精确名跨整个存储库解析；``len(result)`` 即歧义度（0 = 外部 / 不存在）。

    候选 >1 且其中含带函数体的定义时，丢弃声明大小的前向声明；该名下只有声明时全部保留。
    """
    cands = store.symbols_named(dst_name, kinds=kinds)
    if len(cands) <= 1:
        return cands
    ids = [int(c["id"]) for c in cands]
    placeholders = ",".join("?" * len(ids))
    span_of = {r["id"]: r["end_byte"] - r["start_byte"] for r in store.conn.execute(
        f"SELECT id, start_byte, end_byte FROM symbols WHERE id IN ({placeholders})", ids)}
    definitional = len(dst_name) + 16
    defs = [c for c in cands if span_of.get(int(c["id"]), 0) > definitional]
    return defs if defs else cands
