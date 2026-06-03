---
name: mr-boundary
description: Symbol-level plugin↔engine dependency boundary — separate a coupled plugin's INTERNAL deps from its dependencies ON the engine, mark the engine interface, for modular splitting. Deterministic; runs manyscan, never hand-writes SQL.
---

# /mr-boundary — symbol-level plugin↔engine boundary

For a heavily-coupled engine plugin: deterministically separate its **internal**
dependencies (the code you own) from its **engine** dependencies (the external API
surface), at the **symbol level** (classes/methods/types — file-level is useless for
C++ refactoring), and **mark the engine interface boundary** — to drive splitting it
into modules.

How it works (all script-driven, reproducible — no agent judgment): every symbol is
zoned **plugin** (defining file under the plugin root) or **engine** (outside / a
referenced name not in the index). Each symbol edge (`extends`/`implements`/`uses_type`)
is resolved with a **confidence** (`unique`/`ambiguous(N)`/`unresolved`) — an ambiguous
by-name match is **never silently picked**. The engine is a **depth-1 sink** (its own
internals are never expanded), so the graph stays the plugin + its one-layer interface.

## Preconditions
A manyread store with symbols+edges: `/mr-init` then `/mr-enrich` (the C++ enrichment
emits `uses_type` edges that surface engine types like `UObject`/`FString`).

## Call
Resolve the plugin root inline (re-run the `MR=` line each Bash call):
```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/manyscan/scan.py" plugin-boundary --root <repo> \
    --plugin-root <plugin-rel-path> [--engine-root <eng-rel> ...] \
    [--view both|internal|engine] [--format html|json|text|dot] [--max-nodes N]
```
- `--plugin-root` is **required** unless a `*.uplugin`/`*.Build.cs` marker is indexed
  (those aren't indexed by default, so pass it). `--plugin-root ""` = treat the whole
  index as the plugin (engine types then surface as the external interface).
- **`--format html`** emits ONE self-contained page (open in any browser): force layout,
  **node size = how heavily-depended-on** (hubs are big), **module bridges + hub nodes
  highlighted** (catch them at bird's-eye), faint plugin/engine zones, an in-page
  **view toggle** (internal/engine/both), tap a node → its file path. Drag a node to move
  it; drag the background to pan.

## Views
- **internal** — the plugin-only coupling graph → split seams + cycles (`scc`).
- **engine** — the bipartite **engine API surface** (which engine symbols/modules the
  plugin leans on) → what to preserve or abstract when modularizing.
- **both** — the whole picture with the boundary between them.

## Rules
- Read-only on the store; deterministic (same index + roots ⇒ identical output).
- Ambiguous/unresolved targets are boundary **candidates to review**, not facts (C++
  by-name resolution is unsound — overloads/templates/macros). UE `*_API` macros are skipped.
- For higher engine-side fidelity, index the engine too; the depth-1 sink keeps the graph bounded regardless.
