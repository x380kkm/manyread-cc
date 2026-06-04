# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.deps — truthful cross-file dependency extraction over a store.

manyread stores no import graph and resolves inheritance only *within* a file.
This module adds the two things manyscan needs to build real cross-file edges:

  * import/include extraction from ``files.content`` (``extract_imports`` is pure
    and unit-testable; ``file_imports`` fetches+extracts for a store file), then
    best-effort resolution of each import to a target file in the SAME store
    (``resolve_import``) — yielding real file→file edges.
  * global resolution of a manyread edge's ``dst_name`` (extends/implements/
    references, which manyread only resolves in-file) to candidate symbols across
    all files (``resolve_edge_targets``); ``len(result)`` is the ambiguity.

Everything is read-only and evidence-bearing (line numbers / file paths).
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from lib import stores

# extension -> import-rule family
_PY = {".py", ".pyi"}
_CPP = {".h", ".hpp", ".hh", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx", ".hxx", ".cu", ".cuh"}
_CS = {".cs"}
_JS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}


@dataclass(frozen=True)
class ImportRef:
    """One import/include occurrence with its evidence."""

    raw: str       # trimmed source line
    target: str    # extracted module / header / specifier token
    line: int      # 1-based line number
    kind: str      # python | cpp_include | csharp_using | js_import


_RE_PY_FROM = re.compile(r"^\s*from\s+([.\w]+)\s+import\b")
_RE_PY_IMPORT = re.compile(r"^\s*import\s+(.+)$")
_RE_CPP_INC = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')
_RE_CS_USING = re.compile(r"^\s*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")
_RE_JS_SPEC = re.compile(
    r"""(?:\bfrom|\bimport|\brequire\s*\()\s*['"]([^'"]+)['"]"""
)


def family(ext: str | None) -> str | None:
    """Return the import-rule family for a file extension, or None if unsupported."""
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


def extract_imports(content: str, ext: str | None) -> list[ImportRef]:
    """Pure: extract import/include refs from source ``content`` by extension."""
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
            if m and "(" not in line:  # skip `using (var x = ...)` resource statements
                out.append(ImportRef(line.strip(), m.group(1), i, "csharp_using"))
        elif fam == "js":
            for m in _RE_JS_SPEC.finditer(line):
                out.append(ImportRef(line.strip(), m.group(1), i, "js_import"))
    return out


def file_imports(store: "stores.Store", file_id: int) -> list[ImportRef]:
    """Extract imports for a stored file (fetches its content + ext)."""
    row = store.file(file_id)
    if row is None or row["content"] is None:
        return []
    return extract_imports(row["content"], row["ext"])


def _match_path(store: "stores.Store", candidates: list[str], *,
                suffix: bool = False, basename: bool = False) -> int | None:
    """Return a file_id whose (slash-normalized) path matches a candidate, or None."""
    norm = "replace(path, char(92), '/')"  # normalize Windows backslashes to '/'
    cands = [c.replace("\\", "/").lstrip("./") for c in candidates if c]
    for c in cands:  # exact
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


class PathIndex:
    """In-memory file-path index for fast import resolution.

    Built once from a store (one ``SELECT id,path``), it answers exact / suffix /
    basename lookups in O(1)/O(small) instead of a per-call SQL ``LIKE`` scan —
    essential for bounded expansion over engine-scale stores (100k+ files).
    """

    def __init__(self, store: "stores.Store"):
        self.by_path: dict[str, int] = {}
        self.path_of: dict[int, str] = {}
        self.by_basename: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for row in store.conn.execute("SELECT id, path FROM files"):
            p = (row["path"] or "").replace("\\", "/")
            self.by_path[p] = row["id"]
            self.path_of[row["id"]] = p
            self.by_basename[p.rsplit("/", 1)[-1]].append((row["id"], p))

    @classmethod
    def for_store(cls, store: "stores.Store") -> "PathIndex":
        """Return a per-Store cached PathIndex (built once; the O(files) path load
        is amortized across the whole scan instead of rebuilt per call)."""
        cached = getattr(store, "_ms_path_index", None)
        if cached is None:
            cached = cls(store)
            store._ms_path_index = cached
        return cached

    @staticmethod
    def _norm(c: str) -> str:
        return c.replace("\\", "/").lstrip("./")

    def match(self, candidates: list[str], *, suffix: bool = False,
              basename: bool = False) -> int | None:
        cands = [self._norm(c) for c in candidates if c]
        for c in cands:  # exact
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


def _candidates_for(ref: ImportRef, from_path: str | None) -> tuple[list[str], bool, bool] | None:
    """Return ``(candidate_paths, use_suffix, use_basename)`` for an import, or None
    when it cannot map to an in-tree file (bare js module, C# namespace)."""
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
            return None  # bare module = external
        base = PurePosixPath((from_path or "").replace("\\", "/")).parent
        stem = str(base / spec)
        exts = ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"]
        return ([stem + e for e in exts], True, False)
    return None  # csharp_using namespaces do not map 1:1 to files


def resolve_import(store: "stores.Store", ref: ImportRef, from_path: str | None = None,
                   index: "PathIndex | None" = None) -> int | None:
    """Best-effort: map an :class:`ImportRef` to a target file_id in the same store.

    Pass a :class:`PathIndex` for fast repeated resolution; without it, falls back
    to SQL matching. Returns None for externals / unresolved.
    """
    spec = _candidates_for(ref, from_path)
    if spec is None:
        return None
    cands, suffix, basename = spec
    if index is not None:
        return index.match(cands, suffix=suffix, basename=basename)
    return _match_path(store, cands, suffix=suffix, basename=basename)


def resolve_edge_targets(store: "stores.Store", dst_name: str,
                         kinds: set[str] | None = None) -> list[sqlite3.Row]:
    """Globally resolve an edge ``dst_name`` to candidate symbols across all files.

    manyread resolves extends/implements only within one file; this lifts it to the
    whole store by exact name. ``len(result)`` is the ambiguity (0 = external/absent).

    DEFINITION-PREFERENCE: when >1 candidate and at least one is a body-bearing
    DEFINITION, the declaration-sized FORWARD-DECLARATIONS are dropped. A C++ header
    forward-declares a class in many files (``class UMaterial;`` -> a symbol whose span
    is ~len("class <name>")), which would otherwise mark a sound single definition as
    ``ambiguous``. A forward declaration is never the definition; if ONLY declarations
    exist (no definition under that name) all are kept (honest). Mirrors link_source.
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
