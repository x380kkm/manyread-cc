# Enrichment Override Rules + Presets

Base tree-sitter extraction (L2, see `enrichment-treesitter.md`) is generic and
occasionally wrong on **codebase-specific idioms**. The canonical example: Unreal
Engine's `class SDFPARTICLES_API UFoo : public UObject` confuses tree-sitter-cpp
into recording the export macro `SDFPARTICLES_API` as the class name and dropping
the real name `UFoo`; forward declarations and merely *used* types also leak in as
fake `class` symbols. Rather than hardcode per-engine fixes into
`enrich_treesitter.py`, corrections live in a **project-scoped, agent-editable
override layer** applied as a transform pass after raw extraction.

> Status: this layer is **designed** (spec §16); the engine
> (`apply_rules`) lands inside `enrich_treesitter.py` and a thin authoring CLI
> (`rules.py`). This doc is normative for both. See `../commands/mr-rules.md` for
> the command flow.

---

## 1. The additive transform pipeline

```
tree-sitter extract (raw)  ──►  apply_rules(...)  ──►  write symbols / edges
```

The override layer is a **pure, additive transform** that sits between raw
extraction and the DB write. Three properties make it safe to ship on top of an
already-published plugin:

- **No rules ⇒ no change.** With an empty rule list the output is byte-for-byte
  the current behavior. Backward compatible by construction.
- **Idempotent.** Rules are always applied to a *fresh raw extraction*, never
  stacked on already-transformed rows. Re-running enrich (or `--rebuild`) yields
  the same result.
- **Slots in without touching the L1/L3/L4 contracts.** The DB schema (spec §6) is
  unchanged; corrections ride in the `attrs` JSON blob and a `provenance` marker
  on each symbol row.

### The engine entry point

The transform is a single pure function, importable and testable with no DB or IO:

```python
apply_rules(rows, edges, content_by_file_id, rules) -> (rows, edges, provenance)
```

- `rows` — list of symbol dicts, each:
  `{"_local": int, "file_id": int, "name": str, "kind": str, "lang": str,
    "start_line": int, "end_line": int, "start_byte": int, "end_byte": int,
    "parent_local": int|None, "attrs": dict, "provenance": list[str]}`.
  `_local`/`parent_local` are pre-DB local ids so containment survives the
  transform before real `symbol_id`s exist.
- `edges` — list of `{"file_id", "src_local", "dst_local"|None, "dst_name",
  "relation"}`.
- `content_by_file_id` — maps `file_id` → full file text, so source-context
  actions (e.g. `rename_to_next_identifier`) can re-slice
  `content[start_byte:]` to recover the correct token.
- returns transformed `rows`/`edges` plus a **provenance map** (which rule id
  touched which row).

It is **PURE**: no DB, no IO, no globals; given `rules=[]` it returns the input
rows/edges unchanged. The enrich integration is the only caller that touches the
DB — it builds the three inputs from the raw parse, calls `apply_rules`, then
writes the result.

---

## 2. `rules.json` schema

Rules live in `<store>/rules.json`, schema-versioned and committable:

```json
{
  "version": 1,
  "extends_presets": ["unreal"],
  "preset_dirs": ["<abs or root-relative dir>"],
  "rules": [
    {
      "id": "ue-strip-api-macro",
      "when": {
        "lang": "cpp",
        "kind": "class",
        "name_regex": "_API$",
        "path_glob": "Source/**/*.h"
      },
      "action": "rename_to_next_identifier",
      "skip_token_regex": "^[A-Z0-9_]+_API$"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `version` | schema version (currently `1`). |
| `extends_presets` | named preset packs to load first (e.g. `"unreal"`); project rules apply after, so the project can override a preset. |
| `preset_dirs` | directories to search for those preset packs — absolute, or relative to the project root. This is how a shared `unreal` pack is referenced across projects. |
| `rules` | ordered list; rules apply top-to-bottom. |

Each rule is a `when` matcher → an `action` (+ its params):

**`when`** — all present keys must match (AND):

- `lang` — exact language, e.g. `"cpp"`.
- `kind` — exact symbol kind, e.g. `"class"`.
- `name_regex` — regex against the symbol `name`.
- `path_glob` — glob against the file path (project-relative).

**`action`** + params — see §3.

---

## 3. The four actions

| action | params | effect |
|--------|--------|--------|
| `rename_to_next_identifier` | `skip_token_regex` | Re-slice `content[start_byte:]`, skip the leading token matching `skip_token_regex`, take the **next identifier** as the corrected `name`. Fixes the export-macro-as-class case. |
| `set_attr` | `set: {<attr>: <val>}` | Merge key/values into the symbol's `attrs` JSON (e.g. `{"api_exported": true}`). Records *facts* without changing identity. |
| `drop` | — | Remove the symbol entirely (and its dangling edges). Use for fwd-decl / used-type leakage that the parser mislabels as definitions. |
| `reclassify` | `to_kind` | Change `kind` (e.g. a mislabeled `class` that is really a `struct`). |

Every touched row gets a `provenance` entry naming the rule `id`, so changes are
auditable and reversible (drop the rule, re-enrich).

### Worked example — the UE `*_API` case

Raw tree-sitter on `class SDFPARTICLES_API UFoo : public UObject` emits a `class`
symbol named `SDFPARTICLES_API`. Three rules clean it up:

1. **`rename_to_next_identifier`** with `skip_token_regex: "^[A-Z0-9_]+_API$"` —
   re-slices the source, skips `SDFPARTICLES_API`, takes `UFoo` as the name.
2. **`set_attr`** `{ "api_exported": true }` on the same match — so the fact that
   `UFoo` is DLL-exported is preserved, not lost.
3. **`drop`** matching forward-declaration `class` rows (no body span / `name_regex`
   for known fwd-decl patterns) — removes the fake "classes".

The example is **generic**: substitute any language's macro/idiom (a Qt
`Q_OBJECT` artifact, a Unity `[Serializable]` attribute leak) and the same four
actions apply.

---

## 4. Declarative default, script hook as dormant explicit opt-in

There are two layers, and the agent only ever uses the first:

- **Declarative (default).** Everything above — `rules.json`, four actions,
  matched by `when`. Auditable, **cannot execute code**, travels with the repo.
  This is the *only* path the agent reaches for.
- **Script hook (dormant interface).** A documented extension point for a Python
  transform function exists in the design, but it is **disabled by default and
  enabled only on the user's explicit request**. The agent never proposes or
  reaches for arbitrary code on its own — declarative rules cover the real cases,
  and arbitrary code is the user's deliberate, opt-in escape hatch.

---

## 5. Presets — project scope + referenceable external dirs

- **Stored in project scope.** Presets are committable and travel with the repo,
  so a whole team shares the same corrections (no per-developer drift).
- **Referenceable external preset dirs.** A project's `rules.json` may name an
  external `preset_dirs` path to reuse a rule pack across projects — e.g. a single
  shared `unreal` pack consumed by every UE project via
  `"extends_presets": ["unreal"]` + `"preset_dirs": ["../shared/manyread-presets"]`.
- **The tool reminds; it never auto-applies.** When recurring quirks are detected
  the agent *reminds* the user to set up or reference a preset rather than silently
  injecting one. Nothing is applied without approval (§6).

---

## 6. The agent self-repair loop (human-in-the-loop)

Override rules are how the agent fixes enrichment noise it observes — but only
with the user in the loop:

1. **Detect noise.** After enrich, the agent self-checks `symbols` for the usual
   tells: macro-as-class names, repeated junk identifiers, fwd-decls /
   used-types masquerading as definitions.
2. **Propose a rule.** It drafts a `rules.json` entry targeting just that noise.
3. **Preview the diff.** `enrich_treesitter.py --rules-preview` shows the
   before/after symbol changes **without writing** to the DB.
4. **Discuss with the user; apply only on approval.** The user sees exactly what
   the rule changes before anything is committed.
5. **Write + re-enrich.** On approval the rule is written to `<store>/rules.json`
   and enrich re-runs to verify the fix landed.

This loop generalizes to any language/engine quirk — Unreal is just the worked
example.

---

## 7. Provenance + auditability

Two mechanisms make the layer trustworthy:

- **`attrs`** — an optional JSON field on each symbol carrying derived facts
  (`{"api_exported": true}`), queryable like any other column.
- **`provenance`** — a per-symbol marker recording *which rule id* modified it.
  Because `apply_rules` returns a provenance map and rules always run on a fresh
  raw extraction, every correction is **traceable** (which rule did this?) and
  **reversible** (delete the rule, re-enrich, back to raw). Nothing is a silent,
  baked-in mutation.

---

See also: `enrichment-treesitter.md` (the raw L2 extraction this transforms),
`../commands/mr-rules.md` (authoring/inspecting rules),
`indexing-and-profiles.md` (project scope + dynamic-path resolution that presets
ride on).
