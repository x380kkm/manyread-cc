from __future__ import annotations

from tree_sitter import Language, Node, Parser, Query, QueryCursor
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
    # UE 资产 DSL（S 表达式文本，无 walker，符号与边来自各自的 .scm 查询）
    ".matlang": "matlang", ".bplisp": "bplisp", ".animlang": "animlang",
}
#### /语言注册表 ####

#### 我们实际能解析的语言集合 [@380kkm 2026-06-05] ####
SUPPORTED_LANGS: tuple[str, ...] = (
    "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
    "java", "gdscript", "matlang", "bplisp", "animlang",
)


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
    # UE 资产 DSL 全部用 `scheme` 文法
    "matlang": "scheme",
    "bplisp": "scheme",
    "animlang": "scheme",
}
#### /语言名到文法名映射 ####


#### 经 language-pack 取受支持文法对应的 tree-sitter Language [@380kkm 2026-06-05] ####
def _load_language(lang: str) -> Language:
    pack = _PACK_NAME.get(lang)
    if pack is None:
        raise ValueError(f"unsupported language: {lang}")
    return get_language(pack)
