# References Index

Reference docs for the **manyread** Claude Code plugin. These document the
query-time discipline and the four index/trace/ref layers. They are generic and
project-agnostic — per-project specifics live in each repo's
`manyread/manyread.json`. For the full normative design see
`../docs/specs/2026-05-28-manyread-design.md`.

```text
references/
├── INDEX.md                      # this file
├── query-discipline.md           # A* model, the four operators, bounded substr extraction, decision tree
├── indexing-and-profiles.md      # per-project config, language→ext presets, ignore globs, dynamic-path resolution
├── enrichment-treesitter.md      # L2: how symbols/edges get filled; node types per language; the ifdef_branch concept
├── enrich-overrides.md           # L2 transform: project-scoped override rules + presets; rules.json schema, four actions, self-repair loop
├── trace-static-dynamic.md       # L3: static vs dynamic traces, staleness, the ask-before-shelve human-in-the-loop flow
└── ref-prune-workflow.md         # L4: ref/prune lifecycle, worktree management, collaboration via dynamic paths
```

## Reading order

1. **`query-discipline.md`** — start here. The A\* reading model and the four
   operators (Shape → Locate → Symbolize → Extract) that every query follows.
2. **`indexing-and-profiles.md`** — what gets indexed (L1): config, extension
   presets, enumeration, and how project roots resolve across machines.
3. **`enrichment-treesitter.md`** — structural enrichment (L2): tree-sitter fills
   `symbols`/`edges` so you can jump by name and extract by byte span.
4. **`enrich-overrides.md`** — the L2 override-rules transform: project-scoped,
   agent-editable `rules.json` corrections for codebase-specific extraction noise,
   plus the human-in-the-loop self-repair loop.
6. **`trace-static-dynamic.md`** — cross-session memory (L3): the static/dynamic
   trace store and the human-in-the-loop shelve/keep/clear step.
7. **`ref-prune-workflow.md`** — the signature layer (L4): durable, sharable,
   pruned reading workspaces.

## Map to the four layers and commands

| Layer | Reference | Script | Command |
|-------|-----------|--------|---------|
| — (query-time discipline) | `query-discipline.md` | `query.py` | `/mr-query` |
| L1 — index | `indexing-and-profiles.md` | `index_build.py` | `/mr-init`, `/mr-index` |
| L2 — enrich | `enrichment-treesitter.md` | `enrich_treesitter.py` | `/mr-enrich` |
| L2 — override rules | `enrich-overrides.md` | `rules.py`, `enrich_treesitter.py` | `/mr-rules` |
| L3 — trace | `trace-static-dynamic.md` | `trace.py` | `/mr-trace` |
| L4 — ref/prune | `ref-prune-workflow.md` | `ref.py` | `/mr-ref` |

## Organization rules

- Keep references **generic**. No project-, framework-, or engine-specific
  material — that belongs in a downstream repo's `manyread/manyread.json` or its
  own notes.
- Every reference cross-links its siblings under a trailing "See also" so the set
  reads as one connected document.
- All scripts run via `uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py ...`
  (no system Python or sqlite3 required).
