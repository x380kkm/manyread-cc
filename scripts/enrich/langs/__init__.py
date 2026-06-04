from __future__ import annotations

from enrich.langs.cpp import _walk_cpp
from enrich.langs.csharp import _walk_csharp
from enrich.langs.gdscript import _walk_gdscript
from enrich.langs.glsl import _walk_glsl
from enrich.langs.java import _walk_java
from enrich.langs.javascript import _walk_javascript
from enrich.langs.python import _walk_python
from enrich.langs.typescript import _walk_typescript

#### 语言 -> 遍历器函数的分发表 [@380kkm 2026-06-05] ####
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

#### 拥有遍历器的语言集合，作为「查询驱动 vs 遍历器驱动」的分流闸 [@380kkm 2026-06-05] ####
# 有遍历器的语言（cpp/python/...）自己产出符号：遍历器产出符号行，
# .scm 查询只补充 EDGE-only 的 `@dep` 捕获（与 DSL 之前的行为逐字节一致）。
# 没有遍历器但有 .scm 的语言（UE 资产 DSL：matlang/bplisp/animlang）完全由查询驱动：
# `@def.<kind>` 捕获成为符号，`@dep.<relation>` 捕获成为边。
# 不在 WALKERS 中即为闸门 —— 见 _extract_file 中的无遍历器分支。
HAS_WALKER = frozenset(WALKERS)
