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

There are **two layers**, both running on the same parse + the same `Context`:

- **STRUCTURAL** (always on) — the syntax/shape checks below. Unresolved *external* names
  are reported as **warnings**, not errors (see the warning-vs-error rule).
- **SEMANTIC** (OPTIONAL, `--schema`) — a TYPE-DICTIONARY check driven by a JSON schema (a
  harvest-ready node-type dictionary): is the node a known expression class, are its
  properties known, are its **required input pins** connected. It runs **only** when you
  pass `--schema PATH`; with no flag the validator is structural-only and **byte-identical**
  to before. The bundled `scripts/schemas/matlang.sample.json` is **PARTIAL + matlang-only +
  inferred from the two bundled examples** — see the honest boundary at the bottom.

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

### SEMANTIC layer (`--schema`) — a node-type dictionary (matlang)
Pass `--schema scripts/schemas/matlang.sample.json` to additionally run the schema-driven
semantic pass. Per node it looks up the expression type in the dictionary and reports:
- **UNKNOWN_NODE_TYPE** *(warning)* — the node's type is not in the dictionary. The sample
  schema is **partial**, so an unlisted type is "not yet harvested", **not** invalid — this
  is a warning, never an error (a future *complete* harvested schema could flip it to an
  error via a strict flag).
- **UNKNOWN_PROP** *(warning)* — a `:keyword` property that is neither a known property nor
  a known pin of that type.
- **MISSING_REQUIRED_PIN** *(error)* — a schema **input pin** marked `required:true` has no
  incoming `(connect …)` wire. This is the one real semantic catch — but `required:true` is
  reserved for pins with **no const fallback** (e.g. `component-mask`'s `:input`: a
  ComponentMask is meaningless without an incoming connection). It is **NOT** applied to
  math-op inputs like `multiply.{a,b}`: UE's `UMaterialExpressionMultiply` carries
  `ConstA`/`ConstB` UPROPERTY fallbacks, so an unconnected A or B is a **valid, common**
  authoring pattern (multiply a texture by a scalar `ConstB`). Marking those `required` would
  false-error real round-tripped materials (the exporter omits the `:b` wire when B is on its
  const default), which is exactly the false-positive the design avoids.

**Defaults are never flagged.** In UE delta-serialization every UPROPERTY has a CDO default,
so an **absent property == its default**, never "missing". Required-ness therefore lives on
**input pins**, not properties — only an unconnected *required pin* is an error. (`BAD_PROP_TYPE`
is intentionally **not shipped** in v1: matlang prop values are too heterogeneous — the same
`:value` is a float on `constant` but a vec3 list on `constant3-vector` — for a cheap text
check to avoid false positives; it may return later as a narrow optional warning.)

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
  ${CLAUDE_PLUGIN_ROOT}/scripts/dsl_validate.py <file> \
    [--lang matlang|bplisp|animlang] [--schema PATH] [--json]
```

To also run the SEMANTIC layer, point `--schema` at the bundled matlang dictionary:

```
uv run --python 3.12 --with "tree-sitter>=0.23" --with tree-sitter-language-pack \
  ${CLAUDE_PLUGIN_ROOT}/scripts/dsl_validate.py <file.matlang> \
    --schema ${CLAUDE_PLUGIN_ROOT}/scripts/schemas/matlang.sample.json
```

This script declares its tree-sitter dependencies in PEP 723 metadata, so the first
`uv run` auto-installs `tree-sitter` + `tree-sitter-language-pack` (one wheel bundling the
`scheme` grammar all three DSLs share).

## Args

- `<file>` — the DSL file to validate.
- `--lang matlang|bplisp|animlang` — force the DSL (default: auto-detect by extension via
  the same `LANG_FOR_EXT` map `/mr-enrich` uses; `.matlang`/`.bplisp`/`.animlang` are mapped).
- `--schema PATH` — optional semantic schema JSON (a node-type dictionary). Enables the
  SEMANTIC layer. **No flag → structural-only** (byte-identical to before). A malformed schema
  (bad JSON or bad shape) → a clean `error: malformed schema …` message and **exit 2**, never a
  traceback. Only `matlang` has a bundled sample today.
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
- **Run the semantic layer for AI-generated matlang**: pass `--schema` with the bundled
  dictionary to catch a node whose **no-fallback** required input pin is unconnected (e.g. a
  `component-mask` with no `:input` — a real importer degradation) on top of the structural
  checks. The validator's `STRUCTURAL_PASSES[lang]` / `SEMANTIC_PASSES[lang]` lists are the
  plug-in seams — a new check is just an append.

## Schema format + the honest boundary

The semantic schema is a **harvest-ready** JSON dictionary: `lang → nodeType → { classPath?,
properties:{name:{type, default?}}, pins:{name:{required:bool}} }`. Property and pin names are
stored **without** a leading `:`. The **PROPERTIES** half mirrors exactly what a future one-time **UE reflection
export** (the `MatNodeExporter.cpp` harvest, Phase-2 exporter territory) would emit — it
iterates `UMaterialExpression` subclasses, kebab-cases the class name, and collects the
reflected `UPROPERTY`s (name + type + **CDO default**). The **PROPERTIES vs INPUT PINS** split
is deliberate and load-bearing: in UE delta-serialization every UPROPERTY has a CDO default
(absent == default), so required-ness applies **only** to input pins, never to properties.

**Honest caveat on `pins.required` — it is NOT reflection-derivable.** The actual harvest
(`MatNodeExporter.cpp`) emits only the class name + reflected property names; it does **not**
emit input pins, and UE's `FExpressionInput` carries **no** "connection required" bit. The
exporter/importer read pins via `CountInputs()` / `GetInput(i)` / `GetInputName(i)`
(`MatBPExporter.cpp:440-449`, `MatBPImporter.cpp:567-639`) — none of which expose required-ness.
So the **properties** half of the format is genuinely auto-harvestable, but the
**`pins.required` half must be hand-curated** (or sourced from a separate per-type overlay).
A complete pipeline would model pins as the reflection-emittable part (name + input type via
`GetInputType`) and keep `required` in a clearly hand-authored overlay so the harvest-ready
claim stays honest.

What ships **now** is the **FORMAT + the SEAM + a PROOF**, not a complete dictionary:

- `scripts/schemas/matlang.sample.json` is **SAMPLE / PARTIAL / INFERRED**. It is hand-authored
  to cover the **9 expression types observed in the two bundled examples**
  (`material`, `multiply`, `constant`, `constant3-vector`, `scalar-parameter`,
  `vector-parameter`, `fresnel`, `texture-coordinate`, `texture-sample`) with real CDO
  defaults from `MatBPImporter.cpp`, **plus `component-mask`** as the one genuinely
  required-pin demonstration type. It makes **both bundled examples validate with zero
  semantic errors** (it is inferred from them). On required-ness: only `component-mask.input`
  is `required:true` (no const fallback — meaningless unconnected). `multiply.{a,b}` are
  deliberately `required:false` because UE's `ConstA`/`ConstB` make them optional (see the
  MISSING_REQUIRED_PIN note above); `texture-sample.uv` and `fresnel.normal` are likewise
  optional (valid UE leaves them unconnected → default mesh UVs / vertex normal). Every other
  real matlang type (`add`, `lerp`, `panner`, …) is intentionally absent and surfaces as the
  designed `UNKNOWN_NODE_TYPE` warning.
- A **COMPLETE** schema — all `UMaterialExpression` subclasses, all langs, real CDO defaults
  and all input pins — comes from that **future one-time UE reflection harvest**. The
  **bplisp** and **animlang** semantic schemas (UFunction signatures / anim-node signatures)
  await the same harvest; their `SEMANTIC_PASSES` entries are empty until then.
