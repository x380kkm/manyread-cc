from __future__ import annotations

from enrich.langs.cpp import _walk_cpp
from enrich.langs.csharp import _walk_csharp
from enrich.langs.gdscript import _walk_gdscript
from enrich.langs.glsl import _walk_glsl
from enrich.langs.java import _walk_java
from enrich.langs.javascript import _walk_javascript
from enrich.langs.python import _walk_python
from enrich.langs.typescript import _walk_typescript

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
