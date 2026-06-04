---
name: mr-validate
description: Pre-flight STRUCTURAL validator for the UE asset DSLs (matlang/bplisp/animlang) — catch a DSL file's structural errors in milliseconds, fully offline, BEFORE the expensive + fragile UE import.
---

# /mr-validate — pre-flight STRUCTURAL DSL validator

Check an AI- or human-authored UE asset DSL FILE (`.matlang` / `.bplisp` / `.animlang`)
for STRUCTURAL validity **before** you hand it to the (slow, fragile) UE importer. The
validator parses the file with the SAME tree-sitter grammar + `.scm` captures that
`/mr-enrich` uses, runs an ordered list of pure check passes, and reports issues in
milliseconds — no UE, no network, no index db. This is the **AI-generate guardrail** and
the data-driven-config safety net: it turns a multi-minute import blow-up into a sub-second
file check.

It is the **SYNTAX / STRUCTURAL layer only**. A future **SEMANTIC layer** (a schema / type
dictionary: valid node classes, pin existence, type compatibility, CDO defaults — which
needs a one-time UE export) plugs in as additional check passes on the same engine; until
that export exists, unresolved *external* names are reported as **warnings**, not errors
(see below).

## What it checks

### MatLang (`.matlang`) — a UMaterial **DAG**
- **PARSE_ERROR** — tree-sitter rejected the file (any ERROR / MISSING node).
- **MATLANG_NO_MATERIAL** / **MATLANG_NO_OUTPUTS** — required form: a `(material …)` root
  and an `(outputs …)` block must both be present.
- **DUP_ID** — every `$id` node must be unique in-file; each 2nd+ occurrence is reported.
- **DANGLING_WIRE** — every `(connect $id …)` must target a node `$id` defined in the same
  file. A `$id` that resolves nowhere is an error (the only in-file resolution contract).
- **CYCLE** — matlang is a DAG; any cycle in the node↔node wire graph is an error (reuses
  the same SCC cycle detection as `/mr-deps`).

### BlueprintLisp (`.bplisp`) — a Blueprint event/function graph **tree**
- **PARSE_ERROR**, plus **BPLISP_NO_GRAPH** — at least one `(event|func|function|macro …)`
  graph root is required.
- `let`/`set` binds, `call`/`cast` targets that don't resolve in-file → **UNRESOLVED_REF
  warning** (these are engine / cross-graph by design — see warning-vs-error below).

### AnimLang (`.animlang`) — an AnimBP pose tree
- **PARSE_ERROR**, plus **ANIMLANG_NO_GRAPH** — a top-level graph root node is required
  (animlang has no `graph` kind; the root is the sole top-level node, e.g. `anim-blueprint`).
- `(ref "Title")` cross-graph refs that don't resolve in-file → **UNRESOLVED_REF warning**.

### Warning vs error (the key rule)
- **ERROR** = a reference the DSL contract says **must resolve IN-FILE** and does not
  (matlang dangling `(connect $id)`, plus `DUP_ID` / `CYCLE` / required-form for all DSLs,
  and `PARSE_ERROR`). The CLI exits **nonzero** iff at least one error exists.
- **WARNING** = an unresolved dep that is **legitimately external** (bplisp binds/calls/
  casts, animlang `ref`, any engine type name with no in-file def). These resolve against
  the engine/schema in the future **SemanticPass**; flagging them as errors today would
  false-positive on every valid file. Warnings alone → exit 0.

## Call

```
uv run --python 3.12 --with "tree-sitter>=0.23" --with tree-sitter-language-pack \
  ${CLAUDE_PLUGIN_ROOT}/scripts/dsl_validate.py <file> [--lang matlang|bplisp|animlang] [--json]
```

This script declares its tree-sitter dependencies in PEP 723 metadata, so the first
`uv run` auto-installs `tree-sitter` + `tree-sitter-language-pack` (one wheel bundling the
`scheme` grammar all three DSLs share).

## Args

- `<file>` — the DSL file to validate.
- `--lang matlang|bplisp|animlang` — force the DSL (default: auto-detect by extension via
  the same `LANG_FOR_EXT` map `/mr-enrich` uses; `.matlang`/`.bplisp`/`.animlang` are mapped).
- `--json` — emit the issues as a JSON list (`severity`, `code`, `message`, `line`, `byte`)
  for machine consumption (e.g. an AI-generation guardrail loop).

## Output + exit codes

- Plain mode prints one issue per line — `SEVERITY code Lline bbyte: message` — sorted
  deterministically by `(byte, code, message)`, followed by an `N error(s), M warning(s)`
  summary (or `OK … no structural issues` when clean).
- **Exit 0** — no errors (clean, or warnings only). **Exit 1** — at least one error.
  **Exit 2** — unknown DSL / unreadable file (usage error).

## Decision rules

- Run this **before** any UE import of an AI- or hand-authored DSL file; treat a nonzero
  exit as a hard stop and feed the issues back to the author / generator.
- It is **pure + offline**: it never reads an index db, never writes anything, never touches
  the network or UE. No `/mr-index` or `/mr-enrich` is required first.
- When validating a *batch*, use `--json` and gate on the presence of any
  `severity == "error"` issue.
- **SemanticPass is future work**: deeper checks (valid node classes, pin existence,
  type-compat, CDO defaults) need a one-time UE schema export and will be added as extra
  check passes — at which point today's `UNRESOLVED_REF` warnings get upgraded to real
  semantic checks. The validator's `STRUCTURAL_PASSES[lang]` list is the single plug-in seam.
