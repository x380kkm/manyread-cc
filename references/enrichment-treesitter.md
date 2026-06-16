# L2 Enrichment — tree-sitter symbols & edges

L1 (`index_build.py`) gives you `files` + `files_fts`: full-text search and
bounded extraction. **L2** (`enrich_treesitter.py`) adds *structure*: it parses
each indexed file with [tree-sitter](https://tree-sitter.github.io/) and fills the
`symbols` and `edges` tables, so the **Symbolize** operator
(see `query-discipline.md`) can jump straight to a definition by name and read it
from its byte span — no FTS5 search required.

> L2 is optional. The index is fully usable without it; enrichment just makes
> symbol-precise queries possible.

---

## 1. What L2 fills

L2 reads every row of `files` from the project DB and, per language, parses the
content and writes:

- **`symbols`** — one row per definition, with precise spans:
  `name, kind, lang, start_line, end_line, start_byte, end_byte, parent_id`.
  The byte span lets you `slice_bytes(content, start_byte, end_byte-start_byte)` to
  extract exactly that definition. Use `slice_bytes` (byte offsets), not `substr`
  (character offsets), since the spans are UTF-8 byte positions.
- **`edges`** — relationships between symbols:
  `relation` ∈ {`contains`, `extends`/`implements`, optional `references`},
  with `src_symbol_id`, `dst_symbol_id` (when resolved) and `dst_name` (always).

`parent_id` and `contains` edges encode **containment** (a method inside a class,
a function inside a namespace). Inheritance produces `extends` edges. Edges are
containment + inheritance + best-effort name references — **not** a resolved
inter-procedural call graph (that is future work).

Enrichment is **idempotent**: by default it clears `symbols`/`edges` then refills
(per-file refill with `--incremental`). It records `meta(enriched_at, enrich_langs)`
and prints per-language symbol/edge counts.

---

## 2. Node types per language

Languages in v1: **cpp**, **python**, **javascript**. TypeScript parses acceptably
via the JavaScript grammar (TSX/advanced type syntax is a known limitation).
Parsing is wrapped per-file in try/except so one bad file never aborts the run.

### cpp

| tree-sitter node | symbol kind / edge |
|------------------|--------------------|
| `function_definition` | `function` |
| `class_specifier` | `class` |
| `struct_specifier` | `struct` |
| `enum_specifier` | `enum` |
| `namespace_definition` | `namespace` |
| nesting | `contains` edge + `parent_id` |
| `base_class_clause` | `extends` edge |
| `preproc_ifdef` / `preproc_if` | `ifdef_branch` symbol (span) |

### python

| tree-sitter node | symbol kind / edge |
|------------------|--------------------|
| `function_definition` | `function` |
| `class_definition` | `class` |
| nesting | `contains` edge + `parent_id` |
| base-class list | `extends` edge |

### javascript

| tree-sitter node | symbol kind / edge |
|------------------|--------------------|
| `function_declaration` | `function` |
| `class_declaration` | `class` |
| `method_definition` | `method` |
| `lexical_declaration` (arrow/const fns) | `function` |
| `class_heritage` | `extends` edge |

### Optional `references` edges

With `--refs`, L2 also emits best-effort `references` edges by identifier match.
Off by default because name-only matching is noisy; turn it on only when you want
a rough cross-reference signal.

---

## 3. The `ifdef_branch` concept

For C/C++, L2 records each `preproc_ifdef` / `preproc_if` span as a symbol of kind
`ifdef_branch`. These are not definitions — they mark **conditionally-compiled
regions** by their byte span. Two uses:

1. **Reading awareness.** When you extract a slice, `ifdef_branch` rows tell you
   which platform/feature gate it sits behind, so you don't mistake a disabled
   branch for live code.
2. **Mechanical pruning (L4).** The ref/prune layer's `ref strip-ifdef <id> --keep MACRO`
   uses these spans to mechanically delete non-matching preprocessor branches from
   a ref copy (see `ref-prune-workflow.md`). The `ifdef_branch` spans are the
   bridge between L2 enrichment and the L4 prune step.

---

## 4. CLI

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py \
    <alias|--root PATH> [--langs cpp,python] [--refs] [--incremental]
```

`--langs` restricts which languages to parse (default: all configured). `--refs`
enables the optional `references` edges. `--incremental` refills per-file instead
of a full clear-and-rebuild.

PEP 723 metadata declares the grammar dependencies, so `uv run` auto-installs
`tree-sitter`, `tree-sitter-cpp`, `tree-sitter-python`, and
`tree-sitter-javascript` on first run.

See also: `query-discipline.md` (the Symbolize operator),
`ref-prune-workflow.md` (how `ifdef_branch` drives pruning).
