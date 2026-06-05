---
name: mr-equiv
description: Canonical S-expr EQUIVALENCE check for the UE asset DSLs (matlang/bplisp/animlang) — machine-verify that an AI-regenerated or hand-edited DSL file is SEMANTICALLY equivalent to a reference, fully offline, by canonicalizing the parse tree (not the enrich graph).
---

# /mr-equiv — canonical S-expr DSL equivalence checker

When an AI regenerates or edits a UE asset DSL FILE (`.matlang` / `.bplisp` / `.animlang`),
machine-check that the result is **semantically equivalent** to a reference file — before you
trust the regeneration or feed it to the (slow, fragile) UE importer. The checker parses both
files with the SAME tree-sitter `scheme` grammar `/mr-enrich` and `/mr-validate` use,
**canonicalizes the parse trees**, and compares them in lockstep, reporting every difference
with a human path and the original line numbers on each side.

## Why the parse tree, not the enrich graph

A graph-level (enrich rows/edges) diff is **provably too coarse** for equivalence:

- **matlang edges carry no pin names.** Swapping the `:a`/`:b` connections of a `multiply`
  leaves the edge multiset byte-identical — a graph diff sees no change, yet the materials
  differ. `/mr-equiv` catches it (`… > multiply > :a > connect[0]`).
- **Literal property values are not in the rows at all.** A `:value 0.0` vs `:value 0.5`
  property change never reaches the symbol/edge tables. `/mr-equiv` compares them.

So `/mr-equiv` canonicalizes the **parse tree** and compares that.

## The canonical form (tolerances are deliberate)

Each file is parsed; the trees are recursively canonicalized, **skipping comment nodes
entirely** (comment + whitespace insensitivity). Then:

- **Atoms** (`symbol` / `string` / `number` / `boolean`) become leaves. **Numbers compare by
  numeric VALUE** when both parse as numbers (`0.50 == 0.5`), else by text.
- In a list/vector, the **head** (first child) stays the head. The rest split into:
  - **keyword pairs** — a `:keyword` symbol pairs with the **immediately following** child as
    `(key, value)`, unless that follower is itself a `:keyword` or absent (then the key is a
    standalone flag, `value=None`).
  - **positional children** — everything else, **order strictly preserved** (bplisp statement
    sequences are ordered).
- **keyword pairs are stable-sorted by key** — cross-key order is normalized away, but
  **repeated same-key pairs keep their relative order** (e.g. bplisp `:param (A X) :param (B Y)`
  — parameter order is signature order, never collapsed into a dict).
- **positional-vs-keyword interleaving is normalized away** — positionals form one ordered
  list, keywords another. This is a deliberate tolerance.

What this means in practice — **equivalent**: reformatting (whitespace), added/removed
comments, cross-key `:keyword` reordering, `0.50` vs `0.5`. **NOT equivalent**: a `multiply`
`:a`/`:b` connection swap, a bplisp statement reorder in a body, a repeated `:param` swap, a
literal value change (`0.0` → `0.5`).

## `--ignore-keys` (exporter GUIDs)

`--ignore-keys id,event-id` drops keyword pairs whose key is in the list **from both sides**
before comparison. Keys are given **without** the leading `:` (so `id` matches `:id`). The use
case: when comparing a fresh regeneration against an export, the exporter assigns fresh GUIDs
(`:id`, `:event-id`) that are semantically irrelevant — ignore them. Default is empty (strict).

## The guardrail sequence

`/mr-equiv` assumes its inputs **parse**. Equivalence of an unparseable file is undefined, so
the tool exits **2** on a parse error. Run the guardrails in order:

1. `/mr-validate --schema …` on the **candidate** first — catch structural / schema errors
   (the equivalence checker is not a validator; it will not tell you a file is malformed, only
   that it differs from the reference).
2. `/mr-equiv candidate.matlang reference.matlang` — confirm the validated candidate is
   semantically equivalent to the reference.

## Call

```
uv run --python 3.12 --with "tree-sitter>=0.23" --with tree-sitter-language-pack \
  ${CLAUDE_PLUGIN_ROOT}/scripts/extensions/ue/dsl_equiv.py A B \
    [--ignore-keys id,event-id] [--json]
```

`A` is the reference, `B` the candidate (order only affects which side is labelled
`left`/`right` in the diff — equivalence is symmetric). Both files' extensions must map to the
**same** DSL via the same `LANG_FOR_EXT` map `/mr-enrich` uses. This script declares its
tree-sitter deps in PEP 723 metadata, so the first `uv run` auto-installs them.

## Args

- `A`, `B` — the two DSL files to compare (`.matlang` / `.bplisp` / `.animlang`).
- `--ignore-keys k1,k2,…` — comma-separated keyword keys (no leading `:`) to drop from both
  sides before comparison (e.g. `id,event-id` for exporter-assigned GUIDs). Default: empty.
- `--json` — emit the diff list as JSON: a list of
  `{path, kind, left?, right?, left_line?, right_line?}` where `kind` is one of
  `missing_left` / `missing_right` / `head` / `atom` / `arity`.

## Output + exit codes

- Plain mode prints `EQUIVALENT A == B (lang)` when equivalent, else one block per difference
  (`kind  Lleft/Lright  path` + the `left`/`right` values) followed by an `N difference(s)`
  summary. At most the **first 50** differences are reported (the summary marks `(capped)`).
- **Exit 0** — equivalent. **Exit 1** — not equivalent. **Exit 2** — usage error (unknown DSL
  extension, files mapping to different grammars, unreadable input) or a **parse failure** on
  either side (run `/mr-validate` first — equivalence of an unparseable file is undefined).

## Decision rules

- Run this **after** `/mr-validate` (a candidate that does not validate is not worth diffing).
- It is **pure + offline**: no index db, no UE, no network, no store. No `/mr-index` or
  `/mr-enrich` is required first.
- For an AI regeneration loop, gate on the exit code: **0 = accept**, **1 = feed the diff
  paths back to the generator**, **2 = the file does not even parse — fix that first**.
- Use `--ignore-keys` only for keys you can prove are exporter-assigned and semantically
  irrelevant (GUIDs); ignoring a meaningful key hides real differences.
