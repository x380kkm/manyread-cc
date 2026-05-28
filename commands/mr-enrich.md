---
name: mr-enrich
description: Run L2 tree-sitter enrichment — fill the symbols and edges tables for a manyread project.
---

# /mr-enrich — L2 tree-sitter enrichment

Parse the indexed `files` with tree-sitter and fill the `symbols` (with precise line/byte
spans + `parent_id` containment) and `edges` (`contains`, `extends`/`implements`, optional
best-effort `references`) tables. Languages: cpp, python, javascript, typescript (`.ts`/`.tsx`),
csharp, glsl, java (`.java`), gdscript (`.gd`) — all grammars come from the single
`tree-sitter-language-pack` wheel (300+ languages; adding more is just an ext + walker).
HLSL/shader exts `.hlsl .cginc .usf .ush .compute .fx .shader` route through the cpp grammar as
best-effort C-like parsing.

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
