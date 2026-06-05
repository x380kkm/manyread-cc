# audience: internal
# enrich.langs
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
#### /语言 -> 遍历器函数的分发表 ####

#### 拥有遍历器的语言集合，作为查询驱动与遍历器驱动的分流闸 [@380kkm 2026-06-05] ####
HAS_WALKER = frozenset(WALKERS)
