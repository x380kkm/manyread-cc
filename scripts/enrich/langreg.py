# audience: internal
# enrich.langreg
from __future__ import annotations

from tree_sitter import Language, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language


#### 语言注册表：把 manyread 语言名映射到路由到它的文件扩展名 [@380kkm 2026-06-05] ####
LANG_FOR_EXT: dict[str, str] = {
    # cpp
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".inl": "cpp", ".ipp": "cpp",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hxx": "cpp",
    # HLSL / 类 shader 扩展 -> cpp 文法
    ".hlsl": "cpp", ".cginc": "cpp", ".usf": "cpp", ".ush": "cpp",
    ".compute": "cpp", ".fx": "cpp", ".shader": "cpp",
    # python
    ".py": "python", ".pyi": "python",
    # javascript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    # typescript / tsx（真实的 tree-sitter-typescript 文法；tsx 用 tsx 方言）
    ".ts": "typescript", ".tsx": "tsx",
    # csharp
    ".cs": "csharp",
    # glsl shader 源（tree-sitter-glsl）。HLSL 仍走上面的 cpp 文法。
    ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl", ".comp": "glsl",
    ".geom": "glsl", ".tesc": "glsl", ".tese": "glsl",
    # java（Android / JVM）
    ".java": "java",
    # gdscript（Godot）
    ".gd": "gdscript",
}
#### /语言注册表 ####

#### 我们实际能解析的语言集合（list：扩展须就地改动，from-import 消费者才能看见） [@380kkm 2026-06-05] ####
SUPPORTED_LANGS: list[str] = [
    "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
    "java", "gdscript",
]
#### /我们实际能解析的语言集合 ####


#### manyread 语言名 -> tree-sitter-language-pack 文法名 [@380kkm 2026-06-05] ####
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
}
#### /语言名到文法名映射 ####


#### 扩展注册一种语言：写入文法名并把 lang 并入 SUPPORTED_LANGS（就地改动） [@380kkm 2026-06-05] ####
def register_lang(lang: str, grammar: str) -> None:
    _PACK_NAME[lang] = grammar
    if lang not in SUPPORTED_LANGS:
        SUPPORTED_LANGS.append(lang)
#### /扩展注册一种语言 ####


#### 扩展注册一个 扩展名->语言 的路由（就地改动 LANG_FOR_EXT） [@380kkm 2026-06-05] ####
def register_ext(ext: str, lang: str) -> None:
    LANG_FOR_EXT[ext] = lang
#### /扩展注册一个 扩展名->语言 的路由 ####


#### 经 language-pack 取受支持文法对应的 tree-sitter Language [@380kkm 2026-06-05] ####
def _load_language(lang: str) -> Language:
    pack = _PACK_NAME.get(lang)
    if pack is None:
        raise ValueError(f"unsupported language: {lang}")
    return get_language(pack)
#### /经 language-pack 取受支持文法对应的 tree-sitter Language ####
