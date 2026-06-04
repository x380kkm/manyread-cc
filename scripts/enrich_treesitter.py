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

THIN FACADE (Phase-1 cleancode split): the implementation now lives in the
`enrich` package (enrich/model.py, langreg.py, macro_strip.py, langs/*, query.py,
extract.py, dbwrite.py, rules_glue.py, pipeline.py). This module re-exports the
full public surface so `import enrich_treesitter as E; E.<name>` and
`from enrich_treesitter import <name>` keep working unchanged, and keeps the
`main()` + `__main__` entry point for `uv run enrich_treesitter.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Put scripts/ on sys.path FIRST (before importing enrich.*), so the package
# modules that do `from lib import config, db` / `import rules` resolve, and so
# `enrich` is importable as a top-level package. Identical mechanism to the
# pre-split module (which inserted this line before `from lib import config, db`).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db
import rules  # sibling module: pure override-rules engine + loader (spec section 16)

# Re-export the third-party tree-sitter surface (langreg is the wrapper boundary).
from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language

# --- re-export the full public surface from the enrich package ---------------
from enrich.langreg import (LANG_FOR_EXT, SUPPORTED_LANGS, _PACK_NAME,
                            _load_language)
from enrich.model import (Pending, SymRow, _named_child_text, _text)
from enrich.macro_strip import (_CFAMILY_STRIP_LANGS, _DECL_MACRO_RE,
                                _MACRO_TYPE_EXTRA, _MACRO_TYPE_RE,
                                _blank_preserving, _is_macro_type,
                                _macro_strip_predicate, _strip_decl_macros,
                                _strip_decl_macros_once)
from enrich.langs.cpp import (_CPP_DEFS, _CPP_PREPROC, _collect_type_idents,
                              _cpp_declarator_name, _cpp_function_type_idents,
                              _cpp_ifdef_label, _cpp_name, _walk_cpp)
from enrich.langs.python import _PY_DEFS, _walk_python
from enrich.langs.javascript import _js_lexical_fn_name, _walk_javascript
from enrich.langs.csharp import (_CS_CALLABLE_DEFS, _CS_TYPE_DEFS,
                                 _CS_TYPE_KINDS, _walk_csharp)
from enrich.langs.typescript import _TS_TYPE_DEFS, _walk_typescript
from enrich.langs.glsl import _GLSL_DEFS, _walk_glsl
from enrich.langs.java import (_JAVA_CALLABLE, _JAVA_TYPE_DEFS,
                               _JAVA_TYPE_KINDS, _java_type_names, _walk_java)
from enrich.langs.gdscript import _gd_first_ident, _walk_gdscript
from enrich.langs import HAS_WALKER, WALKERS
from enrich.query import (_QUERY_DIR, _dsl_list_ancestor, _dsl_name,
                          _load_query_specs, _query_edges, _query_symbols,
                          _simplify_dep)
from enrich.extract import _extract_file, _reference_edges
from enrich.dbwrite import _insert_file
from enrich.rules_glue import (_default_rules_path, _preview_diff,
                               _resolve_merged_rules)
from enrich.pipeline import enrich, main


if __name__ == "__main__":
    raise SystemExit(main())
