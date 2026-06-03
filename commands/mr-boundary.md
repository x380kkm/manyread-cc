---
name: mr-boundary
description: Symbol-level target↔dependency boundary — separate the code you are analyzing (the TARGET) from what it depends on (its DEPENDENCIES, possibly many sources), mark the dependency interface, for modular splitting. Deterministic; runs manyscan, never hand-writes SQL.
---

# /mr-boundary — symbol-level target↔dependency boundary

For a heavily-coupled body of code: deterministically separate its **internal**
dependencies (the **target** — the code you are analyzing) from its **dependencies**
(what it relies on — possibly MANY distinct dependency sources), at the **symbol
level** (classes/methods/types — file-level is useless for C++ refactoring), and
**mark the dependency interface boundary** — to drive splitting it into modules.

How it works (all script-driven, reproducible — no agent judgment): every symbol is
zoned **target** (defining file under the target root) or **dependency** (outside / a
referenced name not in the index). Each symbol edge (`extends`/`implements`/`uses_type`)
is resolved with a **confidence** (`unique`/`ambiguous(N)`/`unresolved`) — an ambiguous
by-name match is **never silently picked**. The dependency side is a **depth-1 sink**
(its own internals are never expanded), so the graph stays the target + its one-layer
dependency interface.

## Preconditions
A manyread store with symbols+edges: `/mr-init` then `/mr-enrich` (the C++ enrichment
emits `uses_type` edges that surface dependency types like `UObject`/`FString`).

## Call
Resolve the plugin root inline (re-run the `MR=` line each Bash call):
```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" boundary --root <repo> \
    --target-root <target-rel-path> [--dep-root <dep-rel> ...] \
    [--view both|internal|dependency] [--layers flat|two|four] [--dep-depth N] \
    [--format html|json|text|dot] [--max-nodes N]
```
The old name `plugin-boundary` (and the flags `--plugin-root` / `--engine-root`) still
work as deprecated aliases mapping to `--target-root` / `--dep-root`.
- `--target-root` is **required** unless a `*.uplugin`/`*.Build.cs` marker is indexed
  (those aren't indexed by default, so pass it). `--target-root ""` = treat the whole
  index as the target (dependency types then surface as the external interface).
- `--dep-root` is repeatable: the dependency side may aggregate **multiple distinct
  dependency sources**, each labelled/grouped by its own root.
- **`--format html`** emits ONE self-contained page (open in any browser): a **GPU/WebGL
  sigma.js renderer + forceAtlas2 layout** (smooth pan/zoom/drag on hundreds–thousands of
  nodes, deterministic file), **node size = how heavily-depended-on** (hubs are big),
  **module bridges + hub nodes highlighted** (catch them at bird's-eye), target/dependency
  zones by color+cluster, an in-page **view toggle** (internal/dependency/both), tap a node
  → its file path. Drag a node to move it; drag the background to pan.

## Views
- **internal** — the target-only coupling graph → split seams + cycles (`scc`).
- **dependency** — the bipartite **dependency API surface** (which dependency
  symbols/modules the target leans on) → what to preserve or abstract when modularizing.
- **both** — the whole picture with the boundary between them.

### Layered bands (`--layers`, html only) + drill-down
`--layers`/`--dep-depth` change ONLY `--format html`; `json`/`text`/`dot` are unchanged.
The html draws N ORDERED, FRAMED bands left→right (forceAtlas2 still lays out WITHIN each band):
- **four** (default) — `[target-core | target-iface || dep-iface | dep-core]`, read
  left→right as "what is insulated → what touches deps (call sites to wrap) → the dep API
  surface to abstract → what is behind it". dep-core is empty unless `--dep-depth 2` (the
  band is still drawn/labelled — a documented non-error state).
- **two** — `[target || dependency]` (the gross split); **flat** — no boxes (zone color only).
- **`--dep-depth 2`** runs one extra bounded pass to populate dep-core. Default 1.
  `--dep-depth` (dependency expansion layers) is a DIFFERENT axis from `--depth` (the BFS
  budget — leave it at the default).
- **Double-click any node** in the html to open a NEW TAB with that node's up+downstream
  chain (computed client-side over the loaded slice). The tab shows the limitation banner
  "this slice only — re-run manyscan for a deeper chain": the in-browser chain only sees
  the currently loaded slice, so for a deeper/fresh chain re-run manyscan with that node
  as the seed.

## Rules
- Read-only on the store; deterministic (same index + roots ⇒ identical output).
- Ambiguous/unresolved targets are boundary **candidates to review**, not facts (C++
  by-name resolution is unsound — overloads/templates/macros). UE `*_API` macros are skipped.
- For higher dependency-side fidelity, index the dependencies too; the depth-1 sink keeps the graph bounded regardless.
