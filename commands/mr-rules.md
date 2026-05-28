---
name: mr-rules
description: Author and inspect manyread enrichment override rules — project-scoped, agent-editable corrections that fix codebase-specific tree-sitter noise (macro-as-class, fwd-decl leakage) before symbols are written.
---

# /mr-rules — enrichment override rules (L2 transform)

Base tree-sitter extraction is generic and occasionally wrong on codebase-specific
idioms (e.g. Unreal's `class SDFPARTICLES_API UFoo` records the export macro as the
class name). Corrections live in a **project-scoped, agent-editable** override layer
applied as a pure transform after raw extraction and before the DB write — see
`../references/enrich-overrides.md` for the full design, schema, and the four
actions.

> No rules ⇒ enrichment is byte-for-byte the current behavior. The layer is
> additive and idempotent (rules always run on a fresh raw extraction).

## Calls

```
# scaffold an empty <store>/rules.json (version 1)
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/rules.py init

# list active rules (project rules + any extended presets), with their match counts
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/rules.py list [--ref DIR]

# validate rules.json against the schema (and any referenced preset_dirs)
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/rules.py validate [--ref DIR]
```

Preview the effect of the current rules against real extraction — **shows the
before/after symbol diff without writing to the DB**:

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py --root <p> --rules-preview
# (or by alias)
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py --root . --rules-preview
```

Related enrich flags: `--rules PATH` (use a specific rules file), `--no-rules`
(force raw extraction, ignoring any `<store>/rules.json`).

## Args

- `--store PATH` / `--root PATH` — resolve the project (same dynamic-path rules as
  every other command).
- `--ref DIR` — point at an external preset directory (absolute or root-relative)
  when listing/validating, e.g. a shared `unreal` rule pack.

## The four actions (params recap)

- `rename_to_next_identifier` (`skip_token_regex`) — skip the leading token, take
  the next identifier as the corrected name (the export-macro fix).
- `set_attr` (`set: {<attr>: <val>}`) — merge facts into the symbol's `attrs`.
- `drop` — remove a mislabeled symbol (fwd-decl / used-type leakage).
- `reclassify` (`to_kind`) — change the symbol kind.

## Decision rules

- **Always preview, then ask, before applying.** Run
  `enrich_treesitter.py --rules-preview`, show the user the before/after diff, and
  apply only on explicit approval. Never write a rule silently.
- **Self-repair loop** (see `../references/enrich-overrides.md` §6): detect noise →
  propose a rule → preview diff → discuss with the user → write the rule to
  `<store>/rules.json` → re-run `/mr-enrich` to verify.
- **Remind, don't auto-apply.** When you spot a recurring quirk, remind the user
  they can set up a project preset and that a project may **reference an external
  preset dir** (`preset_dirs` + `extends_presets`) to reuse a rule pack across
  projects — rather than auto-injecting anything.
- **Declarative only.** Use `rules.json` actions; the Python script-hook is a
  dormant interface enabled only on the user's explicit request — never reach for
  arbitrary code on your own.
- After writing or changing rules, re-run `/mr-enrich` (rules apply to a fresh raw
  extraction, so the result stays idempotent). `provenance` records which rule
  touched which symbol, so changes are auditable and reversible.

## See also

- `../references/enrich-overrides.md` — full override-rules design, `rules.json`
  schema, the `apply_rules` engine contract, provenance/auditability.
- `mr-enrich.md` — the L2 enrichment this layer transforms.
