---
name: mr-enrich
description: Run L2 tree-sitter enrichment — fill the symbols and edges tables for a manyread project.
---

# /mr-enrich — L2 tree-sitter enrichment

Parse the indexed `files` with tree-sitter and fill the `symbols` (with precise line/byte
spans + `parent_id` containment) and `edges` (`contains`, `extends`/`implements`, plus
per-language **dependency-edge queries** — `calls`/`uses_type`/`imports` etc. — and optional
best-effort `references`) tables. Languages: cpp, python, javascript, typescript (`.ts`/`.tsx`),
csharp, glsl, java (`.java`), gdscript (`.gd`) — all grammars come from the single
`tree-sitter-language-pack` wheel (300+ languages; adding more is just an ext + walker).
HLSL/shader exts `.hlsl .cginc .usf .ush .compute .fx .shader` route through the cpp grammar as
best-effort C-like parsing.

### UE asset DSLs (S-expression asset graphs)

Three Unreal-Engine asset DSLs — emitted as S-expression text by external UE-editor
exporter plugins — are read as symbol+edge graphs so `/mr-deps` and `/mr-boundary` can
analyze the asset node graph (the "连连看" wiring):

- **MatLang** (`.matlang`) — a UMaterial DAG. `(TYPE $id …)` → a `node` symbol named `$id`
  with `attrs.node_type=TYPE`; `(material "M_X" … (expressions …) (outputs …))` → a
  `material` + `outputs` symbol; `(connect $id idx)` → a `uses_type` wire edge that
  resolves in-file by `$id` to the node symbol (so the material DAG re-knits).
- **BlueprintLisp** (`.bplisp`) — a Blueprint event/function graph tree. `(event|func|…)`
  → `graph`; control/stmt heads (`let`/`set`/`branch`/…) → `node`; capitalized
  UFunction heads → `call`. Exec/data flow comes from the synthesized `contains` tree;
  `let`/`set` emit a `binds` edge, `call-parent`/`call-macro` a `calls`, `cast` a `casts`.
- **AnimLang** (`.animlang`) — an AnimBP pose tree + cached-pose DAG. Pose/state heads →
  `node` (variable type-tags, operators, structural heads are excluded); the pose tree
  comes from `contains`. Exporter forms `(define X …)` → `binding` and `(ref "Title")` →
  a `ref` wire (cross-graph, usually unresolved). NOTE: the `(define …)`/`(ref …)` forms
  are exporter-only and are not present in the bundled samples — best-effort, re-verify
  against a real exporter dump.

All three share the `scheme` grammar and are fully QUERY-DRIVEN: symbols + edges come
from `scripts/queries/{matlang,bplisp,animlang}.scm` (a project override at
`<root>/.manyread/queries/<lang>.scm` wins). A `@def.<kind>` capture → a symbol; a
`@dep.<relation>` capture → an edge from the enclosing symbol. Walker-backed langs
(cpp/python/…) are unaffected — their `.scm` stays edge-only. Only the matlang `uses_type`
wire is in the manyscan boundary REL gate; bplisp/animlang relations (binds/calls/casts/
ref) are asset-graph detail (query the `edges` table directly). For a clean boundary view,
index a DSL in its OWN store (a mixed C++ + matlang store blends `uses_type` semantics).

## Call

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py --root . [--langs cpp,python,csharp] [--refs]
# or by explicit root:
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py --root /abs/path/to/repo [--langs ...] [--refs]
```

This script declares its tree-sitter dependencies in PEP 723 metadata, so the first `uv run`
auto-installs `tree-sitter` + `tree-sitter-language-pack` (one wheel bundling 300+ grammars).

## Args

- `--store PATH` / `--root PATH` — resolve the project (same dynamic-path rules as other commands).
- `--langs cpp,python,csharp` — restrict to a subset of configured languages (default: all configured).
- `--refs` — also emit best-effort `references` edges by identifier match (off by default; noisy).

### C-family macro strip (`macro_strip` in `manyread.json`)

A LENGTH-PRESERVING **pre-parse** transform fixes a generic C++ defect: a declaration-
modifier macro makes tree-sitter take the MACRO as the class name and re-home the real
name + base list + BODY into an ERROR node — so `class ENGINE_API UMaterial : public X { … }`
parses as a class named `ENGINE_API` with the members LOST. This hits export/visibility/
deprecation macros across many codebases (UE `*_API`/`UE_DEPRECATED`, Chromium `BASE_EXPORT`,
protobuf `PROTOBUF_EXPORT`, OpenCV `CV_EXPORTS`, WebRTC `RTC_EXPORT`, ICU `U_I18N_API`, …).

Before parsing a `cpp` file (HLSL/shader exts route to cpp, so they are covered too), the
enricher blanks a modifier macro in the `class|struct <MACRO> <RealName>` position with the
SAME number of bytes (newlines kept), so the class parses with its REAL name + a real body
and **every surviving byte offset / line is unchanged**. Only the local parse copy is blanked;
the stored file content and all extracted spans stay valid. It fires ONLY when a SECOND
identifier (the real name) follows the macro, so `class RGBA {}` (all-caps NAME, no second
ident) is untouched; non-cpp langs never run it.

- **Default: ON** (no config needed) — this is a parse-correctness fix, so it helps every
  cpp project out of the box. The detector is the same all-caps `_API`/`_EXPORT`/… macro
  pattern enrich already uses for type-position macros (real names like `RGBA`/`GUID`/`UINT`
  survive). To get exact pre-fix behavior, opt out with `{"macro_strip": {"enabled": false}}`.
- **Extend** it per-project in `<store>/manyread.json`:
  ```json
  { "macro_strip": {
      "enabled": true,
      "extra_names": ["GTEST_API_"],        // literal macro tokens (e.g. trailing-underscore)
      "extra_patterns": ["^MYLIB_[A-Z]+$"]  // extra regexes OR'd with the built-in detector
  } }
  ```
  Even a configured name/pattern only strips in the strict `class|struct <macro> <name>`
  position, so it cannot rename a real class. Malformed config / bad regex → default + a stderr
  warning. The resolved config is recorded in `meta.macro_strip` for provenance.
- **Re-index required:** the transform runs at enrich time and changes EXTRACTION, not stored
  data, so an existing store benefits only after a fresh `/mr-enrich`. Re-enriching surfaces the
  real class name + base/`extends` edges + nested type defs + methods-WITH-BODIES as child
  symbols, and removes the bogus macro-named symbol. (It restores exactly what a CLEAN class
  would yield: declaration-only members — plain fields `int A;` and decl-only methods `void F();`
  — are still not symbols, same as any clean class in the pre-existing cpp-walker contract;
  only methods with a body `void F(){}` and nested type defs become child rows.)
- **Relation to `unreal/api-export-rename`:** that rule renames a symbol *after* parsing and
  cannot recover the lost body — so the pre-parse strip supersedes it for `_API` classes (the
  name is already correct at parse time). Keep or retire that preset deliberately rather than
  double-handle.
- **Stacked macros handled:** export+visibility/attribute macros (`class DLL_EXPORT ENGINE_API
  UMaterial {}`) are fully recovered — the strip iterates to a fixed point, blanking each
  modifier macro in turn until the real name is reached.
- **`enum class <MACRO> <Name>` handled:** the recovered enum keeps its real name (the macro
  is blanked just as for a plain `class`/`struct`).
- **Out of scope (v1):** leading-attribute (`ENGINE_API class UFoo {}`) and function-return
  modifiers (`ENGINE_API void Foo()`) are a different parse shape and are left untouched.
  Nested-paren macro args (`UE_DEPRECATED(MAKE_VERSION(5,0))`) are not fully consumed; common
  `(5.0)`/`("msg")` forms work.
- **Narrow false-positive (documented):** an *elaborated-type* variable declaration whose TYPE
  is itself all-caps-with-underscore — e.g. `struct AB_CD myvar;` — also matches the
  `class|struct <macro-shaped> <ident>` shape, so the type token is blanked and the variable is
  read as the symbol. This requires a real type named in the macro shape (engines use `FFoo`/
  `UBar`, not `AB_CD`), and the pre-fix parse was *also* wrong on these (it mis-reported `AB_CD`
  as a struct definition), so the transform trades one wrong symbol for another only on
  already-mis-parsed, unusual input. Multi-byte UTF-8 inside the blanked span (e.g. an em-dash
  in a `UE_DEPRECATED(5.0, "… — …")` message) is byte-length-preserved, so spans stay exact.

### Override-rules flags (spec §16; author/validate via `/mr-rules`)

- `--rules PATH` — use an explicit override-rules file (default `<store>/rules.json`
  if present). Rules correct codebase-specific idioms (e.g. Unreal export macros misread as
  class names); symbols gain `attrs`/`provenance` json. No rules file → base behavior.
- `--rules-preview` — compute the transform and PRINT a before/after diff of changed symbols,
  but write NOTHING to the db (use this to discuss a proposed rule with the user first).
- `--no-rules` — skip the override-rules transform entirely (raw base extraction).

## Decision rules

- Run `/mr-index` first so `files` is populated. Enrichment is idempotent — it clears and
  refills `symbols`/`edges` (or per-file with `--incremental`).
- Re-run after any `/mr-index --rebuild`, which drops the L2 tables.
- cpp additionally records `#ifdef`/`#if` spans as `symbols` of kind `ifdef_branch`, used by
  `/mr-ref strip-ifdef`.
- If symbol output looks noisy (macro-as-class, fwd-decl junk, wrong `kind`), propose an
  override rule, show its `--rules-preview` diff, discuss with the user, then write it to
  `<store>/rules.json` and re-enrich. Author/validate rules with `/mr-rules`.
- Report per-language symbol/edge counts. Coverage is bounded by the L1 indexed extensions.
