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
