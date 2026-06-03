---
name: mr-deps
description: Bounded dependency / impact / refactoring analysis over a manyread index — "what depends on X / who depends on X", module boundaries, coupling, cycles, cut points. Runs the manyscan scan.py; never hand-writes SQL. Not for plain reading.
---

# /mr-deps — bounded dependency & refactoring analysis (manyscan)

Answer dependency / impact / refactoring questions over a manyread store
(`<repo>/manyread/source.db`) by RUNNING `manyscan/scan.py`, not hand-querying SQLite.
Every slice is **bounded**: one seed never drags in the whole engine, and truncation is
never silent. Read-only on the store; derived slices cache under `<store>/manyscan/`.

Use this for: "what does X depend on / who depends on X", module/dir boundaries, coupling,
instability, cycles, cut points, refactoring blast-radius. For plain reading/searching/
symbol lookup, use `/mr-query` instead.

## Preconditions

Needs a manyread store with symbols+edges. If absent, build it first: `/mr-init` then
`/mr-enrich` (dependency analysis needs the enriched edges).

## Call

Resolve the plugin root inline (same as other manyread commands; re-run the `MR=` line in
each Bash call — shell state does not persist):

```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" list-stores
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" scan    <seed> --store <dir|alias> \
    [--dir out|in|both] [--level file|dir|module] [--max-nodes N] [--format json|mermaid|dot|text|html]
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" analyze <seed> --store <dir|alias> [--level dir|module]
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" export  <seed> --store <dir|alias>   # graphviz dot
```

`--store` takes a hub alias, a store dir, or a `source.db` path; or use `--root <repo>`.
`<seed>` = a symbol / file / dir / keyword.

**Visual output:** `--format html > deps.html` writes ONE self-contained file (cytoscape
force-directed layout, pan/zoom/search, color-by-kind, dashed-red = capped frontier node;
**tap a node to see its file path**) — open in any browser, no install. Large slice → `--level
dir|module` first.

**SRP check (`--srp`):** confirm a dependency-driven module split respects single-responsibility.
`analyze <seed> --srp` reports each module's cohesive clusters (candidate responsibilities) + the
import seams to cut; `scan <seed> --srp --format html` colors nodes by cluster + dashes the seams.
Structural proxy — suggests/confirms; a human verifies semantics.

## Rules

- Truncation is never silent: an over-budget node is tagged `+N⤳` and the slice prints a
  capped-at-depth-L warning — a capped slice is never mistaken for complete.
- Bounded by design: tune `--max-nodes` / `--level` / `--dir` to keep one question from
  pulling in the whole engine.
- This is the manyscan layer of manyread; it never modifies the store.
