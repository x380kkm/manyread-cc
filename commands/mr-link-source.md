---
name: mr-link-source
description: ASSET↔SOURCE cross-layer linker — resolve each DSL asset node (e.g. a matlang material node) to the C++ CLASS that implements it, via the schema's classPath, reporting node -> {source class symbol, file:line, confidence}. Deterministic, read-only on both stores.
---

# /mr-link-source — bridge a DSL asset graph to the C++ source that implements it

Given a **DSL asset store** (e.g. a matlang material), a **code store** (engine
C++), and the **type-dictionary schema** (`nodeType -> classPath`), resolve every
DSL node to the C++ class symbol that implements it: `multiply` ->
`UMaterialExpressionMultiply` at `engine/.../Multiply.h:NN`, with a **confidence**
(`unique` / `ambiguous(N)` / `unresolved`). This lets a reader jump from a material
node straight into the indexed engine source.

It is a **read-only cross-store linker**: it opens BOTH stores `mode=ro` (any write
would raise), changes nothing in either, and never touches enrich/validate. Output
is **deterministic** — identical bytes across runs.

## How it works (script-driven, no agent judgment)
For each DSL node symbol:
1. `node_type` = `symbols.attrs.node_type` (e.g. `"multiply"`); the **material root**
   (`kind='material'`, no `node_type`) uses `node_type='material'`.
2. `classPath` = `schema[lang][node_type].classPath` (e.g.
   `/Script/Engine.MaterialExpressionMultiply`). **Absent from the schema ->** the
   node is reported `no-classPath` (a designed state, not an error).
3. **ReflectedName** = the part after the last `.` (`MaterialExpressionMultiply`).
4. Look it up in the code store, **PREFIX-PRIORITY**: try the tiers `U`, `A`, `F`,
   then bare `""`, and stop at the FIRST that yields `class`/`struct` candidates (a
   reflected name maps to exactly one C++ prefix). The bare name is last-resort only —
   trying it eagerly explodes on common names (against a real engine, bare `"Material"`
   matched **254** unrelated symbols).
5. **Definition-preference**: within that tier, drop FORWARD-DECLARATIONS — UE headers
   forward-declare a class in hundreds of files (`class UMaterial;`, a body-less
   ~15-byte symbol); only the real definition has a body (large span). If a definition
   exists, keep only definitions; if ONLY fwd-decls are indexed, keep them all (honest).
6. **Confidence** (mirrors `manyscan` boundary resolution): 0 candidates ->
   `unresolved`; exactly 1 -> `resolved-unique` (symbol + `file:line`); N>1 ->
   `resolved-ambiguous` — a deterministic SAMPLE of candidates is listed (`ambiguity`
   carries the true total count), **never silently picked**.

## Preconditions
- A **DSL store** with matlang symbols: `/mr-init` + `/mr-enrich` over the DSL text
  (`/mr-enrich` emits `node` symbols with `attrs.node_type`).
- A **code store** that actually indexes the engine classes (`/mr-init` +
  `/mr-enrich` over the C++; the cpp walker emits `kind='class'`/`'struct'`).
- A **schema** (the type dictionary): `scripts/schemas/matlang.sample.json` ships one.

## Call
```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/link_source.py" \
    --dsl-store  <dsl store dir | source.db | hub alias> \
    --code-store <code store dir | source.db | hub alias> \
    --schema     "$MR/scripts/schemas/matlang.sample.json" \
    [--lang matlang] [--code-lang cpp] [--json]
```
- `--dsl-store` / `--code-store` each accept a store **dir**, a `source.db` path, OR
  a **hub alias** (same resolution as the manyscan subcommands).
- `--lang` (the DSL lang to link) defaults to `matlang`.
- `--code-lang` (the code-store lang the classes live in) defaults to `cpp`; pass
  `--code-lang any` to resolve `class`/`struct` symbols across every lang in the code
  store (see the cross-lang note under *Honest boundary*).
- Text report by default; `--json` emits the machine report (per-node records +
  summary counts). Exit `0` on success, `2` on a bad store path or malformed schema.

## Output
Per node: `node_id`, `node_name`, `node_type`, `node_loc` (DSL `file:line`),
`classPath`, `status` (`resolved-unique` | `resolved-ambiguous` | `unresolved` |
`no-classPath`), and `resolved` (`{symbol_name, loc, confidence}` for unique,
`{confidence, ambiguity, candidates[...]}` for ambiguous, else `null`). Plus a
**summary**: `resolved_unique`, `resolved_ambiguous`, `unresolved`, `no_class_path`,
`total`.

## Dogfood against the real engine index (read-only)
Point `--code-store` at the real engine store to resolve nodes against the real
`UMaterialExpression*` classes (opened `mode=ro` — the 7.6 GB store is never
modified):
```bash
uv run --python 3.12 "$MR/scripts/link_source.py" \
    --dsl-store  <a real matlang store> \
    --code-store W:/3dgs/NS_UE_5_6_1/manyread \
    --schema     "$MR/scripts/schemas/matlang.sample.json"
```
Verified against the real `NS_UE_5_6_1` index: **15/17** matlang nodes across the two
bundled materials resolve **unique** to their real `UMaterialExpression*` definitions
(e.g. `multiply -> UMaterialExpressionMultiply @ .../Materials/MaterialExpressionMultiply.h:13`).
The 2 remaining are the `material` roots (`/Script/Engine.Material -> UMaterial`), which
that index only FORWARD-declares — no definition under that name, so honestly `ambiguous`.
Node types not yet in the sample schema surface as `no-classPath`.

## Honest boundary (read before trusting the report)
- **Prefix-priority + definition-preference heuristics.** Resolution tries the prefix
  tiers `U`/`A`/`F`/`""` in order (first non-empty tier wins; bare name last-resort),
  then drops forward-declarations (declaration-sized spans) in favor of the definition.
  Both are UE conventions, not guarantees: an exotic/templated prefix is a known miss,
  and a body-less or anomalously-named definition (the real `UMaterial` in some indexes)
  can stay `ambiguous` — **never auto-picked**.
- **Needs a code store that indexes the classes.** If the engine isn't indexed, real
  nodes report `unresolved` — that is honest, not a bug.
- **Only schema nodeTypes get a classPath.** The shipped sample schema is **PARTIAL**
  (only the node types in the two bundled examples). Against a richer real material,
  **most** nodes will report `no-classPath` until a full UE reflection harvest
  produces a complete schema — do not read the `no-classPath` count as a failure.
- **`symbols_named` caps at 500.** A pathologically over-named symbol could
  under-count ambiguity `N` (irrelevant for distinct `UMaterialExpression*` names).
- **Code resolution is by name + kind, restricted to one lang.** Candidates are
  `class`/`struct` symbols whose name matches a prefix variant **and whose `lang`
  equals `--code-lang` (default `cpp`)**. This assumes the code store is a single C++
  engine index. If you point `--code-store` at a multi-lang store (or merge a DSL
  store into it) and a non-cpp `class`/`struct` symbol shares a ReflectedName, it is
  excluded by default; pass `--code-lang any` to count it (which can legitimately turn
  a `unique` match into `ambiguous` — surfaced, never auto-picked).
- This is a **deterministic linker, not a guess**: same stores + schema => identical
  bytes; ambiguous matches are surfaced, never resolved for you.

## Rules
- Read-only on BOTH stores (`mode=ro`); deterministic; changes nothing in
  `enrich_treesitter.py` / `dsl_validate.py`.
- An `ambiguous`/`unresolved` node is a **candidate to review**, not a fact.
