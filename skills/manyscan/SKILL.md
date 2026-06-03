---
name: manyscan
description: Use ONLY for codebase DEPENDENCY / IMPACT / REFACTORING questions over a manyread-indexed repo — "what does X depend on / who depends on X", module boundaries, coupling, instability, cycles, cut points, refactoring blast-radius. NOT for plain reading, searching, or explaining code — that is the manyread skill. Bounded & demand-driven (one seed never pulls in the whole engine); prefer the scan.py subcommands over hand-writing SQL.
version: 0.1.0
license: MIT
metadata:
  tags: [dependencies, refactoring, graph, manyread, sqlite, bounded]
---

# manyscan

manyscan answers dependency / refactoring questions over a manyread-indexed repo
as a **tool you run** (`scan.py`) — not by hand-querying SQLite (error-prone).
Every result is grounded in manyread's real symbols/edges + import parsing, and is
**bounded**: one seed never drags in the whole engine.

## When to use
- "what does `<X>` depend on?" / "who depends on `<X>`?" (impact analysis)
- module/dir boundaries, coupling, instability, cycles, cut points (refactoring)

## Mental model
`seed` (symbol / file / dir / keyword) → **bounded, level-complete** dependency
slice → roll up to `file|dir|module` → refactoring metrics. Truncation is never
silent: an over-budget node is tagged `+N⤳` and the slice prints
`⚠ 已在第 L 层封顶,省略 N 个依赖`. So a capped slice can't be mistaken for complete.

## Invoke (manyscan ships inside the manyread plugin; prefer these subcommands)
Resolve the plugin root inline (same as manyread; re-run the `MR=` line each Bash call —
shell state does not persist), then call `scripts/manyscan/scan.py`:
```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" list-stores
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" scan    <seed> --store <dir|alias> \
    [--dir out|in|both] [--level file|dir|module] [--max-nodes N] [--format json|mermaid|dot|text|html]
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" analyze <seed> --store <dir|alias> [--level dir|module]
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" export  <seed> --store <dir|alias>   # graphviz dot
```
`--store` takes a hub alias, a store dir, or a `source.db` path; or use `--root <repo>`.
Also available as the `/mr-deps` command.

**Plugin↔engine boundary (symbol-level)** — `scan.py plugin-boundary --root <repo>
--plugin-root <rel>` (or `/mr-boundary`): for a coupled engine plugin, deterministically
separate INTERNAL deps from dependencies ON the engine, at the SYMBOL level, and mark the
engine interface. Edges (`extends`/`implements`/`uses_type`) resolve with a confidence
(`unique`/`ambiguous`/`unresolved` — never silently picked); engine is a depth-1 sink.
`--view internal|engine|both`; `--format html` is ONE page (force layout, node size =
fan-in/hubs big, bridges+hubs highlighted, faint plugin/engine zones, in-page view toggle,
tap→path). Pass `--plugin-root` explicitly (markers aren't indexed); `--plugin-root ""` =
whole index is the plugin. Use it to find split seams + the engine API surface to abstract.

**Visual:** `--format html > deps.html` emits ONE self-contained file (cytoscape force
layout, pan/zoom/search, color-by-kind, dashed-red = bounded/capped node; **tap any node to
see its file path**) — open in any browser, no install. `mermaid`/`dot` render in VS Code /
GitHub / mermaid.live. For a many-node engine-scale slice, roll up with `--level dir|module`.


## Rules
- Needs a manyread store (`<repo>/manyread/source.db`). If absent, build it first
  (`/mr-init` then `/mr-enrich`).
- **Read-only** on the store; derived slices cache under `<store>/manyscan/`.
- manyscan loads manyread's own `lib/config`+`lib/db`, so schema changes follow
  manyread automatically. Future manyread sources (UE blueprints/materials, Unity
  metadata) plug in via the `SourceAdapter` seam — no scope/graph changes.
