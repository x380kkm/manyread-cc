# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread L2 — tree-sitter symbol/edge enrichment.

Reads the `files` table from a project's <root>/.manyread/source.db, parses each
file by language with tree-sitter, and fills the `symbols` and `edges` tables:

  * symbols: name, kind, lang, precise start/end line + byte, parent_id (for
    containment via lexical nesting).
  * edges:   `contains` (parent -> child), `extends`/`implements` (from base
    class clauses / heritage), and optional best-effort `references` (--refs).

Grammar source: ALL grammars come from the single `tree-sitter-language-pack`
wheel (300+ languages) via get_language(); it returns a standard tree_sitter
Language so the standard Parser (bytes input, `children` property) drives every
walker below. Adding a language = map its ext + pack name + a small walker.

Languages: cpp, python, javascript, typescript, csharp, glsl, java, gdscript.
  - Java (.java) uses the java grammar: class/interface/enum/record + method/
    constructor; superclass -> extends, interfaces -> implements.
  - GDScript (.gd, Godot) uses the gdscript grammar: class_name + inner classes,
    functions (methods when nested under a class).
  - TypeScript (.ts) / TSX (.tsx) use tree-sitter-typescript: classes, interfaces,
    enums, type aliases, functions, methods, arrow consts, extends/implements.
    (.ts and .tsx are a pair: requesting "typescript" covers both grammars.)
  - GLSL (.glsl/.vert/.frag/.comp/.geom/.tesc/.tese) uses tree-sitter-glsl:
    functions + structs (C-like; no inheritance).
  - C# (.cs) uses tree-sitter-c-sharp: class/struct/interface/enum + method/
    constructor declarations, containment via nesting, base types -> extends.
  - HLSL / shader-ish exts (.hlsl .cginc .usf .ush .compute .fx .shader) are routed
    through the cpp grammar as *best-effort C-like parsing*. ShaderLab `.shader`
    files embed HLSL blocks, so the cpp grammar yields only partial function/struct
    symbols for them; treat the result as approximate.
  - For cpp we ALSO record `preproc_ifdef` / `preproc_if` (and their #elif/#else
    arms) as symbols of kind `ifdef_branch` so the prune layer (ref strip-ifdef)
    can mechanically cut non-matching spans.

After raw tree-sitter extraction, an optional project-scoped OVERRIDE-RULES pass
(spec section 16) corrects codebase-specific idioms (e.g. Unreal export macros
misread as class names). Rules live in <root>/.manyread/rules.json and are applied
via the pure engine in rules.py; symbols gain `attrs` (json) + `provenance` (json).
No rules file (and no --rules) -> identical to base behavior (backward compatible).

Idempotent: clears existing `symbols`/`edges` then refills (full rebuild).
Writes meta(enriched_at, enrich_langs). Prints per-language symbol/edge counts.

CLI:  enrich_treesitter.py <alias|--root PATH> [--langs cpp,python,csharp] [--refs]
                           [--rules PATH] [--no-rules] [--rules-preview]

NOTE on grammars: tree-sitter-language-pack's get_language(name) returns a ready
tree_sitter.Language (NOT a capsule), so Parser(get_language(name)) + parser.parse(
bytes) is the supported path. The pack pins its own tree-sitter; do not also pin
individual `tree-sitter-<lang>` wheels (they would fight over the binding).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db
import rules  # sibling module: pure override-rules engine + loader (spec section 16)

from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language

# --- Language registry -------------------------------------------------------
# Map a manyread language name -> the file extensions that route to it. Note
# typescript routes through the javascript grammar (see module docstring).
LANG_FOR_EXT: dict[str, str] = {
    # cpp
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".inl": "cpp", ".ipp": "cpp",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hxx": "cpp",
    # HLSL / shader-ish exts -> cpp grammar (best-effort C-like parsing; ShaderLab
    # .shader files embed HLSL blocks so the cpp grammar yields only PARTIAL
    # function/struct symbols for them — treat as approximate).
    ".hlsl": "cpp", ".cginc": "cpp", ".usf": "cpp", ".ush": "cpp",
    ".compute": "cpp", ".fx": "cpp", ".shader": "cpp",
    # python
    ".py": "python", ".pyi": "python",
    # javascript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    # typescript / tsx (real tree-sitter-typescript grammar; tsx uses the tsx dialect)
    ".ts": "typescript", ".tsx": "tsx",
    # csharp
    ".cs": "csharp",
    # glsl shader sources (tree-sitter-glsl). HLSL stays on the cpp grammar above.
    ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl", ".comp": "glsl",
    ".geom": "glsl", ".tesc": "glsl", ".tese": "glsl",
    # java (Android / JVM)
    ".java": "java",
    # gdscript (Godot)
    ".gd": "gdscript",
    # UE asset DSLs (S-expression text emitted by external editor plugins). These
    # have NO walker — symbols + edges come entirely from their .scm query (see
    # _query_symbols + the walker-less branch in _extract_file). They all parse
    # with the `scheme` grammar (see _PACK_NAME); distinct lang keys give each its
    # own stem-keyed .scm via _load_query_specs while sharing one grammar.
    ".matlang": "matlang", ".bplisp": "bplisp", ".animlang": "animlang",
}

# The languages we can actually parse.
SUPPORTED_LANGS: tuple[str, ...] = (
    "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
    "java", "gdscript", "matlang", "bplisp", "animlang",
)


# manyread language name -> tree-sitter-language-pack grammar name.
# language-pack bundles 300+ grammars in ONE wheel; get_language() returns a
# standard tree_sitter.Language driven by the standard Parser (bytes + children
# property), so all walkers below are unchanged by the grammar source.
_PACK_NAME: dict[str, str] = {
    "cpp": "cpp",
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "csharp": "csharp",
    "glsl": "glsl",
    "java": "java",
    "gdscript": "gdscript",
    # UE asset DSLs all use the `scheme` grammar (every (...) form is a `list`;
    # head/keyword/$id are `symbol`; "..." is `string` with the quotes included).
    # VERIFIED: get_language("scheme") parses all real .matlang/.bplisp/.animlang
    # samples with has_error=False.
    "matlang": "scheme",
    "bplisp": "scheme",
    "animlang": "scheme",
}


def _load_language(lang: str) -> Language:
    """Return the tree-sitter Language for a supported grammar via language-pack."""
    pack = _PACK_NAME.get(lang)
    if pack is None:
        raise ValueError(f"unsupported language: {lang}")
    return get_language(pack)


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


# --- cpp ---------------------------------------------------------------------
def _cpp_name(node: Node, src: bytes) -> str:
    """Best-effort declarator/name extraction for a cpp definition node."""
    # class/struct/enum/namespace expose a `name` field.
    nm = node.child_by_field_name("name")
    if nm is not None:
        return _text(nm, src)
    # function_definition: dig into the declarator for the function identifier.
    decl = node.child_by_field_name("declarator")
    return _cpp_declarator_name(decl, src) if decl is not None else ""


def _cpp_declarator_name(node: Node | None, src: bytes) -> str:
    """Walk a (possibly nested) declarator down to the leaf identifier."""
    if node is None:
        return ""
    t = node.type
    if t in ("identifier", "field_identifier", "type_identifier",
             "qualified_identifier", "destructor_name", "operator_name"):
        return _text(node, src)
    # function_declarator / pointer_declarator / reference_declarator / etc.
    inner = node.child_by_field_name("declarator")
    if inner is not None:
        return _cpp_declarator_name(inner, src)
    # Fall back: first identifier-ish descendant.
    for ch in node.children:
        nm = _cpp_declarator_name(ch, src)
        if nm:
            return nm
    return ""


_CPP_DEFS = {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
    "namespace_definition": "namespace",
}
_CPP_PREPROC = {
    "preproc_ifdef": "ifdef_branch",
    "preproc_if": "ifdef_branch",
    "preproc_elif": "ifdef_branch",
    "preproc_else": "ifdef_branch",
}


# tokens that, in a TYPE position, are almost certainly a C/C++ MACRO mis-read as a
# type by tree-sitter (no preprocessor runs): ALL-CAPS-WITH-UNDERSCORE catches the UE
# export/DSL macros (UE_API, ENGINE_API, *_API, SHADER_PARAMETER,
# BEGIN_SHADER_PARAMETER_STRUCT, …); the small EXTRA set catches the underscore-free
# function-specifier macros. Deliberately does NOT match all-caps-no-underscore, so
# real types like GUID / HRESULT / UINT survive.
_MACRO_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_MACRO_TYPE_EXTRA = frozenset({"FORCEINLINE", "FORCENOINLINE", "FORCEINLINE_DEBUGGABLE", "CONSTEXPR"})


def _is_macro_type(name: str) -> bool:
    return name in _MACRO_TYPE_EXTRA or bool(_MACRO_TYPE_RE.match(name))


# --- length-preserving pre-parse declaration-modifier macro strip (c-family) -
# Tree-sitter-cpp mis-parses `class <ALLCAPS_MACRO> <RealName> ...` (export/
# visibility/deprecation macros like ENGINE_API / BASE_EXPORT / PROTOBUF_EXPORT /
# CV_EXPORTS / UE_DEPRECATED(5.0)): it takes the MACRO as the class name and re-homes
# the real name + base list + BODY into an ERROR node, so the real name AND all
# members/methods are LOST (root_node.has_error becomes True, or the name is silently
# wrong). Blanking the macro token (and any trailing balanced `(...)` args) with the
# SAME number of bytes — newlines kept — makes the class parse correctly with the real
# name + a real body, and EVERY surviving token keeps its ORIGINAL byte offset / line.
#
# Only fires when a SECOND identifier (the real name) follows the macro: `class RGBA {}`
# (no second ident) leaves RGBA as the name, untouched. STACKED macros (`class
# DLL_EXPORT ENGINE_API UMaterial {}`) are fully recovered by iterating the single-pass
# strip to a fixed point (each pass blanks the leading macro -> whitespace, the re-scan
# then sees the next macro in modifier position). The blank is applied ONLY to the local
# copy fed to parser.parse(); the stored DB content stays original (length-preserving =>
# all emitted spans remain valid against the unmodified content).
#
# The default macro detector is the production `_is_macro_type` (REUSED, not a divergent
# regex), extended per-project via manyread.json macro_strip.extra_names (literal tokens,
# e.g. trailing-underscore GTEST_API_ that the base regex misses) / extra_patterns
# (regexes OR'd in). Run ONLY for lang=="cpp" (covers HLSL exts, which route to cpp).
#
# Regex groups: 1=keyword+ws (kept), 2=candidate macro token (filtered by is_macro),
# 3=optional single balanced (...) args + ws (BLANKED with the macro), 4=the REAL name
# (a SECOND identifier MUST follow, else no match => byte-identical).
_DECL_MACRO_RE = re.compile(
    r"(\b(?:class|struct)\s+)"        # 1: keyword + ws  (KEPT verbatim). The leading
                                       #    \b stops `class`/`struct` matching as a
                                       #    SUBSTRING of a user identifier (subclass,
                                       #    metaclass, mystruct, superclass) and so
                                       #    blanking arbitrary in-identifier source.
                                       #    `enum class <MACRO> <Name>` still fires: \b
                                       #    matches at the `class` word start after the
                                       #    space, recovering the enum's real name.
    r"([A-Za-z_][A-Za-z0-9_]*)"       # 2: candidate macro token
    r"(\s*(?:\([^()]*\))?\s+)"        # 3: optional single balanced (...) args + ws (BLANKED)
    r"([A-Za-z_][A-Za-z0-9_]*)"       # 4: the REAL name — a SECOND identifier MUST follow
)

_CFAMILY_STRIP_LANGS = frozenset({"cpp"})


def _blank_preserving(s: str) -> str:
    """Blank a stripped span so that, after `.encode("utf-8")`, the result has the
    SAME BYTE LENGTH and the same line structure as the original — every downstream
    start_byte/end_byte/start_line/end_line stays exact.

    BYTE-length, not char-length, is the load-bearing invariant: the content is
    re-encoded to UTF-8 before parser.parse(). A non-ASCII char inside the blanked
    region (e.g. an em-dash in a UE_DEPRECATED(5.0, "Use Foo — instead") message
    string) is multiple UTF-8 bytes, so collapsing it to ONE space would shrink the
    byte length and shift every span after the macro. We therefore emit one space per
    UTF-8 byte of each char (newlines kept verbatim — they are 1 byte and preserve the
    line structure)."""
    return "".join(
        "\n" if c == "\n" else " " * len(c.encode("utf-8")) for c in s
    )


def _macro_strip_predicate(macro_strip: dict):
    """Build the is_macro(token) predicate: the built-in `_is_macro_type` detector
    OR'd with config extra_names (literal) + extra_patterns (compiled regexes)."""
    extra_names = frozenset(macro_strip.get("extra_names") or ())
    extra_pats = [re.compile(p) for p in (macro_strip.get("extra_patterns") or ())]

    def is_macro(tok: str) -> bool:
        return (_is_macro_type(tok) or tok in extra_names
                or any(p.match(tok) for p in extra_pats))

    return is_macro


def _strip_decl_macros_once(content: str, is_macro) -> tuple[str, bool]:
    """ONE pass: blank every macro token in a `class|struct <MACRO> <RealName>`
    position. Returns (new_content, changed). LENGTH-PRESERVING per `_blank_preserving`.
    """
    out: list[str] = []
    pos = 0
    for m in _DECL_MACRO_RE.finditer(content):
        if not is_macro(m.group(2)):
            continue                      # group2 is a real name (e.g. RGBA) -> untouched
        out.append(content[pos:m.start(2)])
        # blank [macro token start, real-name start): the macro + any (...) args + ws
        out.append(_blank_preserving(content[m.start(2):m.start(4)]))
        pos = m.start(4)                  # group4 (real name) + BODY kept verbatim
    if not out:
        return content, False             # no strip fired -> byte-identical
    out.append(content[pos:])
    return "".join(out), True


def _strip_decl_macros(content: str, macro_strip: dict | None) -> str:
    """PURE, deterministic, LENGTH-PRESERVING strip of declaration-modifier macros in
    the `class|struct <MACRO> <RealName>` position. Returns content UNCHANGED when the
    transform is disabled (macro_strip None or enabled=false) or nothing matches (clean
    cpp is a byte-identical no-op). Idempotent: re-running on blanked output finds none.

    STACKED modifier macros (`class DLL_EXPORT ENGINE_API UMaterial {}`, common with
    export+visibility/attribute macros) are fully recovered: the pass blanks the FIRST
    macro, which turns it into whitespace, so a re-scan now sees `class <2nd-macro>
    <RealName>` and strips that too. We iterate to a FIXED POINT (re-scan after each
    changing pass). Each pass blanks >=1 token and only ever turns macro tokens into
    whitespace (never lengthens / never touches the real name), so the loop strictly
    shrinks the set of macro tokens and terminates; a clean-after-first pass costs one
    extra no-op scan. The `_PASS_LIMIT` cap is belt-and-suspenders against any pathology.
    """
    # Disable ONLY on None or an explicit enabled=false. A `{}`/partial dict respects
    # the enabled-default (True): `{}` is falsy but `{}.get("enabled", True)` is True,
    # so guarding on `not macro_strip` would silently disable an empty config and
    # contradict the default-ON intent. (In the real pipeline config.load_macro_strip
    # always returns the fully-populated DEFAULT, so this only matters for direct
    # callers that construct a partial dict — but the guard is now consistent.)
    if macro_strip is None or not macro_strip.get("enabled", True):
        return content
    is_macro = _macro_strip_predicate(macro_strip)
    _PASS_LIMIT = 64
    for _ in range(_PASS_LIMIT):
        content, changed = _strip_decl_macros_once(content, is_macro)
        if not changed:
            break
    return content


def _collect_type_idents(node: Node | None, src: bytes, out: list[str]) -> None:
    """Gather `type_identifier` leaf texts under node (skips primitive_type, so
    int/float/void/bool never become deps — only named/engine types like UObject).
    Also skips macro-like tokens (`_is_macro_type`) so UE export/DSL macros parsed in a
    type position (UE_API, ENGINE_API, SHADER_PARAMETER, FORCEINLINE, …) never become
    bogus `uses_type` dependencies."""
    if node is None:
        return
    if node.type == "type_identifier":
        t = _text(node, src).strip()
        if t and not _is_macro_type(t):
            out.append(t)
    for ch in node.children:
        _collect_type_idents(ch, src, out)


def _cpp_function_type_idents(node: Node, src: bytes) -> list[str]:
    """Named types in a function's return + parameter declarations (deduped)."""
    out: list[str] = []
    _collect_type_idents(node.child_by_field_name("type"), src, out)       # return type
    _collect_type_idents(node.child_by_field_name("declarator"), src, out)  # params
    return list(dict.fromkeys(out))


def _cpp_ifdef_label(node: Node, src: bytes) -> str:
    """A readable label for a preproc branch (the macro / condition tested)."""
    cond = node.child_by_field_name("name") or node.child_by_field_name("condition")
    if cond is not None:
        return _text(cond, src).strip() or node.type
    # else arm has no condition.
    return node.type


def _walk_cpp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CPP_DEFS:
        name = _cpp_name(node, src) or "<anonymous>"
        idx = pend.add(name, _CPP_DEFS[t], node, parent_local)
        # Inheritance from base_class_clause (class/struct only).
        for ch in node.children:
            if ch.type == "base_class_clause":
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    # strip access-specifier keywords if they leaked in.
                    for kw in ("public ", "private ", "protected ", "virtual "):
                        if bn.startswith(kw):
                            bn = bn[len(kw):].strip()
                    if bn and b.type not in ("access_specifier", "virtual"):
                        pend.inherit.append((idx, bn, "extends"))
        # uses_type: a function's return/param named types are dependencies of it
        # (member/param/return on engine types like UObject/FString = the engine surface).
        if t == "function_definition":
            for tn in _cpp_function_type_idents(node, src):
                pend.inherit.append((idx, tn, "uses_type"))
        cur_parent = idx

    elif t == "field_declaration":
        # a class/struct member's named type is a dependency of the enclosing type;
        # for a method DECLARATION (no body) the declarator holds param types too.
        if parent_local is not None:
            tnames: list[str] = []
            _collect_type_idents(node.child_by_field_name("type"), src, tnames)
            _collect_type_idents(node.child_by_field_name("declarator"), src, tnames)
            for tn in dict.fromkeys(tnames):
                pend.inherit.append((parent_local, tn, "uses_type"))

    elif t in _CPP_PREPROC:
        label = _cpp_ifdef_label(node, src)
        pend.add(label, "ifdef_branch", node, parent_local)
        # do NOT change cur_parent: defs inside an ifdef still belong to the
        # surrounding scope for containment purposes.

    for ch in node.children:
        _walk_cpp(ch, src, pend, cur_parent)


# --- python ------------------------------------------------------------------
_PY_DEFS = {
    "function_definition": "function",
    "class_definition": "class",
}


def _walk_python(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _PY_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = _PY_DEFS[t]
        # A function nested under a class is a method.
        if kind == "function" and parent_local is not None and pend.rows[parent_local].kind == "class":
            kind = "method"
        idx = pend.add(name, kind, node, parent_local)
        if t == "class_definition":
            supers = node.child_by_field_name("superclasses")
            if supers is not None:
                for arg in supers.named_children:
                    bn = _text(arg, src).strip()
                    if bn:
                        pend.inherit.append((idx, bn, "extends"))
        cur_parent = idx

    for ch in node.children:
        _walk_python(ch, src, pend, cur_parent)


# --- javascript --------------------------------------------------------------
def _js_lexical_fn_name(node: Node, src: bytes) -> str | None:
    """If a lexical_declaration binds an arrow/function expression, return its name."""
    for decl in node.named_children:
        if decl.type != "variable_declarator":
            continue
        val = decl.child_by_field_name("value")
        if val is not None and val.type in ("arrow_function", "function", "function_expression"):
            return _named_child_text(decl, "name", src) or _text(decl.child_by_field_name("name"), src)
    return None


def _walk_javascript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t == "class_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "class", node, parent_local)
        heritage = None
        for ch in node.children:
            if ch.type == "class_heritage":
                heritage = ch
                break
        if heritage is not None:
            # class_heritage -> `extends <expr>` (+ optional ts implements clause)
            for ch in heritage.named_children:
                bn = _text(ch, src).strip()
                if not bn:
                    continue
                rel = "implements" if ch.type == "implements_clause" else "extends"
                if ch.type == "implements_clause":
                    for impl in ch.named_children:
                        nm = _text(impl, src).strip()
                        if nm:
                            pend.inherit.append((idx, nm, "implements"))
                else:
                    pend.inherit.append((idx, bn, rel))
        cur_parent = idx

    elif t == "function_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "function", node, parent_local)
        cur_parent = idx

    elif t == "method_definition":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "method", node, parent_local)
        cur_parent = idx

    elif t == "lexical_declaration":
        nm = _js_lexical_fn_name(node, src)
        if nm:
            idx = pend.add(nm, "function", node, parent_local)
            cur_parent = idx

    for ch in node.children:
        _walk_javascript(ch, src, pend, cur_parent)


# --- csharp ------------------------------------------------------------------
# Type-like declarations (containers) vs callables. A function nested under a
# type container is reported as a `method`.
_CS_TYPE_DEFS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "record_struct_declaration": "struct",
}
_CS_CALLABLE_DEFS = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}
_CS_TYPE_KINDS = frozenset(("class", "struct", "interface"))


def _walk_csharp(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

    if t in _CS_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _CS_TYPE_DEFS[t], node, parent_local)
        # Base types live in a `base_list` child: `: Base, IFoo, IBar`. C# does
        # not syntactically distinguish a base class from interfaces, so this is
        # best-effort: the FIRST base type is treated as `extends`, the rest as
        # `implements` (a common C# convention: base class first, interfaces after).
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
        # A callable directly under a type container is a method; otherwise a
        # free function (rare in C#) is reported as a plain function.
        if parent_local is not None and pend.rows[parent_local].kind in _CS_TYPE_KINDS:
            kind = "method"
        else:
            kind = "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx

    for ch in node.children:
        _walk_csharp(ch, src, pend, cur_parent)


# --- typescript / tsx --------------------------------------------------------
_TS_TYPE_DEFS = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}


def _walk_typescript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type

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
            elif ch.type == "extends_type_clause":  # interface extends
                for b in ch.named_children:
                    bn = _text(b, src).strip()
                    if bn:
                        pend.inherit.append((idx, bn, "extends"))
        cur_parent = idx

    elif t == "type_alias_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        pend.add(name, "type", node, parent_local)

    elif t == "function_declaration":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "function", node, parent_local)
        cur_parent = idx

    elif t == "method_definition":
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, "method", node, parent_local)
        cur_parent = idx

    elif t == "lexical_declaration":
        nm = _js_lexical_fn_name(node, src)
        if nm:
            idx = pend.add(nm, "function", node, parent_local)
            cur_parent = idx

    for ch in node.children:
        _walk_typescript(ch, src, pend, cur_parent)


# --- glsl --------------------------------------------------------------------
_GLSL_DEFS = {
    "function_definition": "function",
    "struct_specifier": "struct",
}


def _walk_glsl(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _GLSL_DEFS:
        name = _cpp_name(node, src) or "<anonymous>"
        idx = pend.add(name, _GLSL_DEFS[t], node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_glsl(ch, src, pend, cur_parent)


# --- java --------------------------------------------------------------------
_JAVA_TYPE_DEFS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "annotation_type_declaration": "interface",
}
_JAVA_CALLABLE = {
    "method_declaration": "method",
    "constructor_declaration": "method",
}
_JAVA_TYPE_KINDS = frozenset(("class", "interface", "enum"))


def _java_type_names(node: Node, src: bytes) -> list[str]:
    """Collect type-identifier texts under a superclass / interfaces node."""
    out: list[str] = []
    for ch in node.named_children:
        if ch.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
            out.append(_text(ch, src).strip())
        else:
            out.extend(_java_type_names(ch, src))
    return [x for x in out if x]


def _walk_java(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t in _JAVA_TYPE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _JAVA_TYPE_DEFS[t], node, parent_local)
        sc = node.child_by_field_name("superclass")
        if sc is not None:
            for bn in _java_type_names(sc, src):
                pend.inherit.append((idx, bn, "extends"))
        ifaces = node.child_by_field_name("interfaces")
        if ifaces is not None:
            for bn in _java_type_names(ifaces, src):
                pend.inherit.append((idx, bn, "implements"))
        cur_parent = idx
    elif t in _JAVA_CALLABLE:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = "method" if (parent_local is not None
                             and pend.rows[parent_local].kind in _JAVA_TYPE_KINDS) else "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_java(ch, src, pend, cur_parent)


# --- gdscript (Godot) --------------------------------------------------------
def _gd_first_ident(node: Node, src: bytes) -> str:
    """Fallback name extraction: first name/identifier child text."""
    for ch in node.named_children:
        if ch.type in ("name", "identifier"):
            return _text(ch, src).strip()
    return ""


def _walk_gdscript(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    t = node.type
    if t == "class_name_statement":
        # `class_name Foo` declares the script's own class name.
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src)
        if name:
            pend.add(name, "class", node, parent_local)
    elif t == "class_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        idx = pend.add(name, "class", node, parent_local)
        cur_parent = idx
    elif t == "function_definition":
        name = _named_child_text(node, "name", src) or _gd_first_ident(node, src) or "<anonymous>"
        kind = "method" if (parent_local is not None
                            and pend.rows[parent_local].kind == "class") else "function"
        idx = pend.add(name, kind, node, parent_local)
        cur_parent = idx
    for ch in node.children:
        _walk_gdscript(ch, src, pend, cur_parent)


WALKERS = {
    "cpp": _walk_cpp,
    "python": _walk_python,
    "javascript": _walk_javascript,
    "typescript": _walk_typescript,
    "tsx": _walk_typescript,
    "csharp": _walk_csharp,
    "glsl": _walk_glsl,
    "java": _walk_java,
    "gdscript": _walk_gdscript,
}

# A language with a WALKER owns its symbols (cpp/python/...): the walker yields rows,
# the .scm query adds EDGE-only `@dep` captures (byte-identical to pre-DSL behavior).
# A language WITHOUT a walker but WITH a .scm (the UE asset DSLs: matlang/bplisp/
# animlang) is fully QUERY-DRIVEN: `@def.<kind>` captures become SYMBOLS and
# `@dep.<relation>` captures become edges. Absence from WALKERS is the gate — see
# the walker-less branch in _extract_file.
HAS_WALKER = frozenset(WALKERS)


# --- walker-less DSL symbol extraction (UE asset graphs) ---------------------
# S-expression asset DSLs have no walker; their NODE GRAPH (the "连连看" wiring)
# comes entirely from the .scm query: `@def.<kind>` -> a SYMBOL, `@dep.<relation>`
# -> an edge from the enclosing @def-symbol (via the reused _query_edges). The
# scheme grammar parses every (...) form as a `list`, so a captured token's
# CONTAINMENT span is its nearest enclosing `list` ancestor (the token itself —
# the $id / quoted-string / head symbol — does NOT cover the node's nested wires).
def _dsl_list_ancestor(node: Node) -> Node | None:
    """Nearest enclosing `list` ancestor of a captured token (its symbol span)."""
    n = node
    while n is not None and n.type != "list":
        n = n.parent
    return n  # may be None (defensive) -> caller skips the capture


def _dsl_name(node: Node, src: bytes) -> str:
    """Symbol name from THIS captured node only (never zip a sibling capture).

    A scheme `string` node's text INCLUDES the surrounding quotes, so strip them
    for a quoted name (material "M_X" -> M_X). KEEP the leading '$' on matlang
    $ids: _simplify_dep leaves '$mul1' intact, so the (connect $mul1) edge's
    dst_name '$mul1' must equal the node symbol name '$mul1' for by-name resolution.
    """
    nm = _text(node, src)
    if node.type == "string":          # scheme `string` text includes the quotes
        nm = nm.strip('"')
    return nm or "<anon>"


def _query_symbols(file_id: int, tree, src: bytes, query, lang: str) -> list[dict]:
    """Symbols from `@def.<kind>` captures, for a walker-less DSL (the query OWNS
    symbols). Each capture lands on a token; the symbol's span is the token's
    enclosing `list` ancestor. parent = innermost STRICTLY-enclosing @def span.

    Returns the SHARED-CONTRACT row-dict shape (same keys walkers produce).
    Deterministic: captures() membership is stable but ORDER is not, so we sort
    the tuples by a TOTAL key (start_byte, end_byte, kind, name) before assigning
    `_local` indices.
    """
    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a bad query must never abort enrichment
        return []

    # Collect (start_byte, end_byte, kind, name, head) for every `@def.*` capture,
    # using the captured token's enclosing `list` ancestor as the span. `head` is
    # the first child `symbol` of that list (the node TYPE), promoted into attrs.
    raw: dict[tuple[int, int, str, str], str] = {}  # 4-key -> head (de-dupes lists
    #   matched by >1 pattern; Node is NOT part of the key — span is recoverable).
    for cap_name in sorted(caps):
        if not cap_name.startswith("def."):
            continue
        kind = cap_name[4:]
        for node in caps[cap_name]:
            anc = _dsl_list_ancestor(node)
            if anc is None:
                continue
            name = _dsl_name(node, src)
            head = ""
            for ch in anc.children:
                if ch.type == "symbol":
                    head = _text(ch, src)
                    break
            key = (anc.start_byte, anc.end_byte, kind, name)
            raw.setdefault(key, head)

    # Total-order the surviving rows; assign deterministic _local indices.
    keys = sorted(raw)            # (start_byte, end_byte, kind, name) is a total order
    spans = [(k[0], k[1]) for k in keys]

    def _parent_of(i: int) -> int | None:
        si, ei = spans[i]
        best = None  # (size, local) of the smallest STRICTLY-enclosing prior span
        for j, (sj, ej) in enumerate(spans):
            if j == i:
                continue
            if sj <= si and ei <= ej and (ej - sj) > (ei - si):
                size = ej - sj
                if best is None or size < best[0] or (size == best[0] and j < best[1]):
                    best = (size, j)
        return best[1] if best is not None else None

    # Need line numbers from the ancestor node; re-collect ancestor nodes by span.
    # (A span is unique per `list`; map span -> node from the first capture seen.)
    span_to_node: dict[tuple[int, int], Node] = {}
    for cap_name in sorted(caps):
        if not cap_name.startswith("def."):
            continue
        for node in caps[cap_name]:
            anc = _dsl_list_ancestor(node)
            if anc is None:
                continue
            span_to_node.setdefault((anc.start_byte, anc.end_byte), anc)

    rows: list[dict] = []
    for i, (sb, eb, kind, name) in enumerate(keys):
        head = raw[(sb, eb, kind, name)]
        anc = span_to_node[(sb, eb)]
        # Promote the node TYPE into attrs only when it differs from the name (the
        # matlang case: head=type e.g. 'multiply', name=$id e.g. '$mul1'). For
        # material/outputs/graph/... head==name (or is redundant) -> no attr.
        attrs = {"node_type": head} if (head and head != name and kind == "node") else {}
        rows.append({
            "_local": i,
            "file_id": file_id,
            "name": name,
            "kind": kind,
            "lang": lang,
            "start_line": anc.start_point[0] + 1,
            "end_line": anc.end_point[0] + 1,
            "start_byte": sb,
            "end_byte": eb,
            "parent_local": _parent_of(i),
            "attrs": attrs,
            "provenance": [],
        })
    return rows


# --- raw extraction -> SHARED CONTRACT dicts ---------------------------------
# --- declarative dependency-edge queries (project-customizable) --------------
# Symbols come from the walkers above; dependency EDGES can be declared per language
# in a tree-sitter query (.scm): every `@dep.<relation>` capture becomes an edge from
# the enclosing symbol to the captured name (relation = the suffix). Built-in presets
# live in scripts/queries/<lang>.scm; a project overrides one at
# <root>/.manyread/queries/<lang>.scm (full replace). A language with no .scm keeps
# walker-only edges (e.g. C++), so this is purely additive + backward compatible.
_QUERY_DIR = Path(__file__).resolve().parent / "queries"


def _load_query_specs(root) -> dict[str, str]:
    """lang -> .scm text: built-in presets, then project overrides (which win)."""
    specs: dict[str, str] = {}
    if _QUERY_DIR.is_dir():
        for p in sorted(_QUERY_DIR.glob("*.scm")):
            try:
                specs[p.stem] = p.read_text(encoding="utf-8")
            except OSError:
                pass
    if root is not None:
        odir = Path(root) / ".manyread" / "queries"
        if odir.is_dir():
            for p in sorted(odir.glob("*.scm")):
                try:
                    specs[p.stem] = p.read_text(encoding="utf-8")
                except OSError:
                    pass
    return specs


def _simplify_dep(name: str) -> str:
    """Reduce a captured type/name to a bare identifier for by-name resolution
    (mirrors the inherit simplification): union -> first, strip generics, last segment."""
    s = name.split("|")[0].strip()
    s = s.split("[")[0].split("<")[0].strip()
    return s.split("::")[-1].split(".")[-1].strip()


def _query_edges(file_id: int, tree, src: bytes, query, rows: list[dict]) -> list[dict]:
    """Edges from `@dep.<relation>` captures, each attributed to the enclosing symbol
    (smallest row span containing the capture). Sorted + deduped => deterministic."""
    if not rows:
        return []
    spans = sorted(((r["start_byte"], r["end_byte"], r["_local"]) for r in rows),
                   key=lambda s: (s[0], -s[1]))

    def enclosing(byte: int):
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best

    try:
        caps = QueryCursor(query).captures(tree.root_node)
    except Exception:  # noqa: BLE001 - a bad query must never abort enrichment
        return []
    out: list[dict] = []
    seen: set[tuple] = set()
    for cap_name in sorted(caps):
        if not cap_name.startswith("dep."):
            continue
        relation = cap_name[4:]
        for node in caps[cap_name]:
            src_local = enclosing(node.start_byte)
            if src_local is None:
                continue
            dst = _simplify_dep(_text(node, src))
            if not dst:
                continue
            key = (src_local, relation, dst)
            if key in seen:
                continue
            seen.add(key)
            out.append({"file_id": file_id, "src_local": src_local,
                        "dst_local": None, "dst_name": dst, "relation": relation})
    out.sort(key=lambda e: (e["src_local"], e["relation"], e["dst_name"]))
    return out


def _extract_file(file_id: int, content: str, lang: str, parser: Parser,
                  do_refs: bool, query=None, macro_strip: dict | None = None):
    """Parse one file into the SHARED-CONTRACT dict shape (rows + edges).

    Returns (rows, edges) where rows is a list of symbol dicts keyed by a per-file
    `_local` index, and edges reference rows by `src_local`/`dst_local`. This is
    the form apply_rules() consumes; nothing is written to the DB here.

    For c-family langs (cpp; HLSL exts route to cpp) an optional LENGTH-PRESERVING
    pre-parse strip of declaration-modifier macros runs on a LOCAL copy of `content`
    fed only to parser.parse() (see `_strip_decl_macros`). macro_strip is None => no
    transform (the committed golden harness calls this with 6 positional args, so the
    default keeps it byte-identical). The ORIGINAL `content` is unchanged for callers.
    """
    if lang in _CFAMILY_STRIP_LANGS:
        content = _strip_decl_macros(content, macro_strip)
    src = content.encode("utf-8", "replace")
    tree = parser.parse(src)

    if lang in HAS_WALKER:
        # WALKER-OWNED langs (cpp/python/...): byte-identical to pre-DSL behavior.
        pend = Pending()
        WALKERS[lang](tree.root_node, src, pend, None)

        rows: list[dict] = []
        for local_idx, r in enumerate(pend.rows):
            rows.append({
                "_local": local_idx,
                "file_id": file_id,
                "name": r.name,
                "kind": r.kind,
                "lang": lang,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "start_byte": r.start_byte,
                "end_byte": r.end_byte,
                "parent_local": r.parent_local,
                "attrs": {},
                "provenance": [],
            })

        edges: list[dict] = []
        # contains edges (parent -> child) from lexical containment.
        for local_idx, r in enumerate(pend.rows):
            if r.parent_local is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r.parent_local,
                    "dst_local": local_idx,
                    "dst_name": r.name,
                    "relation": "contains",
                })
        # extends/implements edges from base clauses. dst_local stays None: these are
        # resolved to a same-file symbol id at insert time (after any rule renames).
        for src_local, dst_name, relation in pend.inherit:
            simple = dst_name.split("<")[0].strip()
            simple = simple.split("::")[-1].split(".")[-1].strip()
            edges.append({
                "file_id": file_id,
                "src_local": src_local,
                "dst_local": None,
                "dst_name": simple or dst_name,
                "relation": relation,
            })

        # declarative dependency edges from the language's .scm query (if any).
        if query is not None:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

        # optional best-effort references (off by default). Computed on raw spans;
        # attributed to the enclosing symbol by _local index. refs needs `pend`,
        # so it stays WALKER-ONLY (the DSL branch has no pend).
        if do_refs:
            edges.extend(_reference_edges(file_id, tree, src, pend))
    else:
        # WALKER-LESS DSL (matlang/bplisp/animlang): the query OWNS the symbols.
        rows = _query_symbols(file_id, tree, src, query, lang) if query is not None else []
        edges = []
        # Synthesize `contains` from parent_local (same shape the walkers use).
        for r in rows:
            if r["parent_local"] is not None:
                edges.append({
                    "file_id": file_id,
                    "src_local": r["parent_local"],
                    "dst_local": r["_local"],
                    "dst_name": r["name"],
                    "relation": "contains",
                })
        # @dep -> wire edges, attributed to the innermost enclosing @def-symbol.
        if query is not None and rows:
            edges.extend(_query_edges(file_id, tree, src, query, rows))

    return rows, edges


def _reference_edges(file_id: int, tree, src: bytes, pend: Pending) -> list[dict]:
    """Best-effort `references` edges as contract dicts (src_local/dst_local)."""
    by_name: dict[str, int] = {}
    for local_idx, r in enumerate(pend.rows):
        if r.kind in ("function", "method", "class", "struct"):
            by_name.setdefault(r.name, local_idx)
    if not by_name:
        return []

    spans = sorted(
        ((r.start_byte, r.end_byte, i) for i, r in enumerate(pend.rows)),
        key=lambda s: (s[0], -(s[1])),
    )

    def enclosing(byte: int) -> int | None:
        best = None
        for s, e, sid in spans:
            if s <= byte < e:
                best = sid
        return best

    out: list[dict] = []
    seen: set[tuple[int, int]] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "field_identifier", "call_expression"):
            ident = node
            if node.type == "call_expression":
                fn = node.child_by_field_name("function")
                ident = fn if fn is not None else node
            if ident is not None and ident.type in ("identifier", "field_identifier"):
                nm = _text(ident, src)
                dst = by_name.get(nm)
                if dst is not None:
                    src_sym = enclosing(ident.start_byte)
                    if src_sym is not None and src_sym != dst:
                        key = (src_sym, dst)
                        if key not in seen:
                            seen.add(key)
                            out.append({
                                "file_id": file_id,
                                "src_local": src_sym,
                                "dst_local": dst,
                                "dst_name": nm,
                                "relation": "references",
                            })
        stack.extend(node.children)
    return out


# --- DB write ----------------------------------------------------------------
def _insert_file(conn, file_id: int, lang: str, rows: list[dict],
                 edges: list[dict]) -> tuple[int, int]:
    """Insert (possibly rule-transformed) contract rows+edges into the DB.

    Resolves edge endpoints from `_local` indices to assigned DB ids. `extends`/
    `implements` edges with dst_local=None are matched to a same-file type symbol
    by name (best-effort), using the POST-transform names. Returns (n_sym, n_edge).
    """
    local_to_db: dict[int, int] = {}
    for row in rows:
        attrs = row.get("attrs") or {}
        prov = row.get("provenance") or []
        attrs_json = json.dumps(attrs) if attrs else None
        prov_json = json.dumps(prov) if prov else None
        cur = conn.execute(
            "INSERT INTO symbols(file_id, name, kind, lang, start_line, end_line, "
            "start_byte, end_byte, parent_id, attrs, provenance) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (file_id, row.get("name"), row.get("kind"), row.get("lang") or lang,
             row.get("start_line"), row.get("end_line"),
             row.get("start_byte"), row.get("end_byte"),
             None,  # parent_id wired below once all ids are known
             attrs_json, prov_json),
        )
        local_to_db[row["_local"]] = cur.lastrowid

    # Wire parent_id now that every row has a db id.
    for row in rows:
        parent_local = row.get("parent_local")
        if parent_local is not None and parent_local in local_to_db:
            conn.execute(
                "UPDATE symbols SET parent_id=? WHERE id=?",
                (local_to_db[parent_local], local_to_db[row["_local"]]),
            )

    # Name -> db id for type symbols (resolve inheritance targets in-file). For a
    # walker-less DSL, WIDEN this to ALL symbols so an asset wire ((connect $mul1)
    # / (ref ...)) resolves to its in-file node symbol by name; cpp/python keep the
    # exact class/struct/interface resolvable set (byte-identical). Names are
    # assumed UNIQUE within a DSL file (true for matlang $ids); duplicate names in
    # animlang resolve first-wins by (start_byte,end_byte) order — deterministic
    # because _query_symbols emits rows in a total sort order.
    is_dsl = lang not in HAS_WALKER
    name_to_id: dict[str, int] = {}
    for row in rows:
        if is_dsl or row.get("kind") in ("class", "struct", "interface"):
            name_to_id.setdefault(row.get("name"), local_to_db[row["_local"]])

    n_edges = 0
    for e in edges:
        src_local = e.get("src_local")
        if src_local not in local_to_db:
            continue  # source dropped by a rule.
        src_id = local_to_db[src_local]
        dst_local = e.get("dst_local")
        dst_name = e.get("dst_name")
        if dst_local is not None:
            if dst_local not in local_to_db:
                continue  # target dropped by a rule.
            dst_id = local_to_db[dst_local]
        else:
            dst_id = name_to_id.get(dst_name)
        conn.execute(
            "INSERT INTO edges(file_id, src_symbol_id, dst_symbol_id, dst_name, relation) "
            "VALUES(?,?,?,?,?)",
            (file_id, src_id, dst_id, dst_name, e.get("relation")),
        )
        n_edges += 1

    return len(rows), n_edges


# --- override-rules helpers --------------------------------------------------
def _default_rules_path(root: Path) -> Path:
    """The project rules file: <root>/.manyread/rules.json."""
    return Path(root) / ".manyread" / "rules.json"


def _resolve_merged_rules(cfg: config.ProjectConfig, rules_path: str | None,
                          no_rules: bool):
    """Load + merge override rules once. Returns (rules_list, rules_file_used).

    --no-rules  -> ([], None): skip the transform entirely (base behavior).
    explicit --rules PATH wins; else <root>/.manyread/rules.json IF it exists.
    preset_dirs are passed from the resolved rules doc by load_rules itself, but
    we also pass the resolved rules file's own dir context implicitly via the path.
    Returns [] when no rules file is present -> backward compatible.
    """
    if no_rules:
        return [], None
    path = Path(rules_path) if rules_path else (cfg.store / "rules.json")
    if not path.exists():
        return [], None
    # load_rules reads preset_dirs from the doc itself (resolved relative to the
    # rules file dir). No extra_preset_dirs needed here.
    merged = rules.load_rules(path, extra_preset_dirs=None)
    return merged, path


def _preview_diff(before_rows: list[dict], after_rows: list[dict], path: str) -> list[str]:
    """Return human-readable diff lines for symbols changed by the rules pass.

    Matches before/after rows by `_local` (rules never change `_local`). Reports
    rename / kind change / new attrs / drop, and lists which rules touched a row.
    """
    after_by_local = {r["_local"]: r for r in after_rows}
    lines: list[str] = []
    for b in before_rows:
        local = b["_local"]
        a = after_by_local.get(local)
        if a is None:
            lines.append(f"  {path}: DROP  {b['kind']} {b['name']!r} "
                         f"(L{b['start_line']})")
            continue
        changes = []
        if a["name"] != b["name"]:
            changes.append(f"name {b['name']!r} -> {a['name']!r}")
        if a["kind"] != b["kind"]:
            changes.append(f"kind {b['kind']!r} -> {a['kind']!r}")
        if (a.get("attrs") or {}) != (b.get("attrs") or {}):
            changes.append(f"attrs {b.get('attrs') or {}} -> {a.get('attrs') or {}}")
        if changes:
            prov = ",".join(a.get("provenance") or []) or "?"
            lines.append(f"  {path}: {'; '.join(changes)}  "
                         f"[L{b['start_line']}; rules: {prov}]")
    return lines


def enrich(cfg: config.ProjectConfig, langs: list[str], do_refs: bool,
           rules_path: str | None = None, no_rules: bool = False,
           preview: bool = False) -> dict:
    """Clear and refill symbols/edges for every file whose ext maps to a chosen lang.

    After raw tree-sitter extraction, applies the project override rules (spec
    section 16) as a pure transform pass BEFORE inserting. With preview=True the
    transform is computed and a before/after diff is collected, but NOTHING is
    written to the DB (existing symbols/edges are left untouched).
    """
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        raise SystemError(f"no index db at {db_path} — run index_build.py first")

    merged_rules, rules_file = _resolve_merged_rules(cfg, rules_path, no_rules)

    # c-family pre-parse macro-strip config (manyread.json macro_strip; ABSENT => ON).
    # Resolved once; passed per-file to _extract_file (only the cpp path consumes it).
    macro_strip = config.load_macro_strip(cfg.store)

    conn = db.connect(db_path)
    try:
        db.init_schema(conn)  # ensure symbols/edges/meta exist + migrate (idempotent).

        if not preview:
            # Idempotent full rebuild: clear prior enrichment. (Preview leaves the
            # DB untouched so re-running with rules later is the only write path.)
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM symbols")
            conn.commit()

        # Build parsers lazily, once per language actually present.
        parsers: dict[str, Parser] = {}
        per_lang_sym: dict[str, int] = {}
        per_lang_edge: dict[str, int] = {}
        n_files = 0
        n_errors = 0
        diff_lines: list[str] = []

        query_specs = _load_query_specs(getattr(cfg, "root", None))
        query_objs: dict[str, object] = {}            # lang -> compiled Query (or None)

        rows = conn.execute("SELECT id, path, ext, content FROM files").fetchall()
        for file_id, path, ext, content in rows:
            lang = LANG_FOR_EXT.get((ext or "").lower())
            if lang is None or lang not in langs:
                continue
            if content is None:
                continue
            if lang not in parsers:
                try:
                    parsers[lang] = Parser(_load_language(lang))
                except Exception as exc:  # noqa: BLE001 - grammar load failure is per-lang
                    print(f"warning: could not load {lang} grammar: {exc}", file=sys.stderr)
                    parsers[lang] = None  # mark as failed so we skip its files
            parser = parsers.get(lang)
            if parser is None:
                continue
            if lang in query_specs and lang not in query_objs:   # compile the .scm once per lang
                try:
                    query_objs[lang] = Query(_load_language(lang), query_specs[lang])
                except Exception as exc:  # noqa: BLE001 - a bad query must not abort enrichment
                    print(f"warning: bad dependency query for {lang}: {exc}", file=sys.stderr)
                    query_objs[lang] = None
            try:
                raw_rows, raw_edges = _extract_file(file_id, content, lang, parser, do_refs,
                                                    query_objs.get(lang), macro_strip)
                # Override-rules transform (pure; identity when merged_rules == []).
                new_rows, new_edges, _prov = rules.apply_rules(
                    raw_rows, raw_edges, {file_id: content}, merged_rules,
                )
                if preview:
                    if merged_rules:
                        diff_lines.extend(_preview_diff(raw_rows, new_rows, path))
                    # do NOT write in preview mode.
                    per_lang_sym[lang] = per_lang_sym.get(lang, 0) + len(new_rows)
                    per_lang_edge[lang] = per_lang_edge.get(lang, 0) + len(new_edges)
                else:
                    n_sym, n_edge = _insert_file(conn, file_id, lang, new_rows, new_edges)
                    per_lang_sym[lang] = per_lang_sym.get(lang, 0) + n_sym
                    per_lang_edge[lang] = per_lang_edge.get(lang, 0) + n_edge
                n_files += 1
            except Exception as exc:  # noqa: BLE001 - graceful per-file skip
                n_errors += 1
                print(f"warning: failed to enrich {path}: {exc}", file=sys.stderr)

        if preview:
            total_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            total_edge = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        else:
            conn.commit()
            db.set_meta(conn, "enriched_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
            db.set_meta(conn, "enrich_langs", ",".join(langs))
            db.set_meta(conn, "enrich_rules", str(rules_file) if rules_file else "")
            db.set_meta(conn, "macro_strip", json.dumps(macro_strip, sort_keys=True))
            conn.commit()
            total_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            total_edge = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        conn.close()

    return {
        "files": n_files,
        "errors": n_errors,
        "per_lang_sym": per_lang_sym,
        "per_lang_edge": per_lang_edge,
        "total_sym": total_sym,
        "total_edge": total_edge,
        "db_path": db_path,
        "rules_file": str(rules_file) if rules_file else None,
        "n_rules": len(merged_rules),
        "preview": preview,
        "diff_lines": diff_lines,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enrich_treesitter.py",
        description="manyread L2 tree-sitter symbol/edge enrichment.",
    )
    parser.add_argument("--root", default=None, help="source tree root (default: store's parent)")
    parser.add_argument("--store", default=None,
                        help="explicit manyread store dir (default: discover from cwd)")
    parser.add_argument("--langs", default=None,
                        help="comma list to restrict languages (default: config langs "
                             "intersected with supported, else all supported)")
    parser.add_argument("--refs", action="store_true",
                        help="also emit best-effort `references` edges (off by default)")
    parser.add_argument("--rules", default=None,
                        help="override-rules path (default <root>/.manyread/rules.json "
                             "if present); see /mr-rules")
    parser.add_argument("--no-rules", action="store_true",
                        help="skip the override-rules transform entirely (raw base behavior)")
    parser.add_argument("--rules-preview", action="store_true",
                        help="compute the transform and PRINT a before/after diff of "
                             "changed symbols, but do NOT write to the db")
    args = parser.parse_args(argv)

    if args.no_rules and (args.rules or args.rules_preview):
        parser.error("--no-rules cannot be combined with --rules / --rules-preview")

    try:
        cfg = config.resolve_project(root=args.root, store=args.store)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Determine languages: explicit --langs wins; else config languages; else all.
    if args.langs:
        requested = [s.strip().lower() for s in args.langs.split(",") if s.strip()]
    elif cfg.languages:
        requested = [s.lower() for s in cfg.languages]
    else:
        requested = list(SUPPORTED_LANGS)
    # Keep only languages we can actually parse in v1.
    langs = [l for l in requested if l in SUPPORTED_LANGS]
    if not langs:
        langs = list(SUPPORTED_LANGS)
    # typescript and tsx are a pair (same walker, different grammar dialect);
    # requesting one pulls in the other so .ts and .tsx are both covered.
    if "typescript" in langs and "tsx" not in langs:
        langs.append("tsx")
    if "tsx" in langs and "typescript" not in langs:
        langs.append("typescript")

    try:
        stats = enrich(cfg, langs, do_refs=args.refs, rules_path=args.rules,
                       no_rules=args.no_rules, preview=args.rules_preview)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # bad rules.json / missing preset
        print(f"error: rules: {exc}", file=sys.stderr)
        return 2

    if stats.get("preview"):
        print(f"project    : {cfg.alias}")
        print(f"db         : {stats['db_path']}  (NOT modified — preview only)")
        rf = stats.get("rules_file")
        print(f"rules      : {rf or '(none)'}  ({stats['n_rules']} merged rule(s))")
        diff = stats.get("diff_lines") or []
        if not stats["n_rules"]:
            print("preview    : no rules in effect — nothing would change.")
        elif not diff:
            print("preview    : rules in effect but no symbols would change.")
        else:
            print(f"preview    : {len(diff)} symbol change(s) the rules WOULD make:")
            for line in diff:
                print(line)
        return 0

    print(f"project    : {cfg.alias}")
    print(f"root       : {Path(cfg.root).resolve()}")
    print(f"db         : {stats['db_path']}")
    print(f"langs      : {','.join(langs)}")
    rf = stats.get("rules_file")
    if args.no_rules:
        print("rules      : (disabled via --no-rules)")
    else:
        print(f"rules      : {rf or '(none)'}  ({stats['n_rules']} merged rule(s))")
    print(f"files      : {stats['files']} (errors: {stats['errors']})")
    for lang in langs:
        s = stats["per_lang_sym"].get(lang, 0)
        e = stats["per_lang_edge"].get(lang, 0)
        print(f"  {lang:<11}: {s} symbols, {e} edges")
    print(f"symbols    : {stats['total_sym']}")
    print(f"edges      : {stats['total_edge']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
