from __future__ import annotations

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
