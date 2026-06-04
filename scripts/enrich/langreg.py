from __future__ import annotations

from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language


#### 语言注册表：把 manyread 语言名映射到路由到它的文件扩展名 [@380kkm 2026-06-05] ####
# typescript 经由 javascript 文法处理（见模块 docstring）。
LANG_FOR_EXT: dict[str, str] = {
    # cpp
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".inl": "cpp", ".ipp": "cpp",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hxx": "cpp",
    # HLSL / 类 shader 扩展 -> cpp 文法（尽力的类 C 解析；ShaderLab 的
    # .shader 文件内嵌 HLSL 块，cpp 文法只能产出 PARTIAL 的函数/结构体符号
    # —— 视为近似）。
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
    # UE 资产 DSL（外部编辑器插件产出的 S 表达式文本）。它们没有 walker —— 符号
    # 与边完全来自各自的 .scm 查询（见 _query_symbols 与 _extract_file 中的无
    # walker 分支）。三者都用 `scheme` 文法解析（见 _PACK_NAME）；不同的 lang 键
    # 让各自通过 _load_query_specs 拿到以文件名为键的 .scm，同时共享同一文法。
    ".matlang": "matlang", ".bplisp": "bplisp", ".animlang": "animlang",
}
#### /语言注册表 ####

#### 我们实际能解析的语言集合 [@380kkm 2026-06-05] ####
SUPPORTED_LANGS: tuple[str, ...] = (
    "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
    "java", "gdscript", "matlang", "bplisp", "animlang",
)


#### manyread 语言名 -> tree-sitter-language-pack 文法名 [@380kkm 2026-06-05] ####
# language-pack 在一个 wheel 里打包了 300+ 文法；get_language() 返回标准的
# tree_sitter.Language，由标准 Parser（bytes + children 属性）驱动，故下面所有
# walker 都不因文法来源而改变。
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
    # UE 资产 DSL 全部用 `scheme` 文法（每个 (...) 形式都是 `list`；
    # head/keyword/$id 是 `symbol`；"..." 是含引号的 `string`）。
    # 已验证：get_language("scheme") 解析所有真实的 .matlang/.bplisp/.animlang
    # 样本都 has_error=False。
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
