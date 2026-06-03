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
    [--ignore <view-hide.json>] [--collapse off|file|dir] \
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

### Collapsible MODULE<->SYMBOL view (`--collapse off|file|dir`, html only)
A subsystem boundary can be ~1769 symbol nodes — unreadable. `--collapse` (default `off` =
v0.6.2 bytes) groups both sides into collapsible MODULES: target symbols by FILE (`file`:
stem, a .cpp/.h pair coalesces; `dir`: parent dir), dependency symbols by ENGINE MODULE
(same resolver as `--rollup-dep`; unresolved `dep:`/`amb:` → an `(external)` bucket). DEFAULT
= ALL modules COLLAPSED: a readable overview of ~60 super-nodes (sized by member count,
placed in their band). EXPAND modules from the side-panel **MODULES** section (above HIDE)
to reveal their symbols IN PLACE; collapse-all / expand-all are there too. The side panel is
the ONLY collapse control — graph double-click still drills the symbol chain (no-op on a
collapsed super-node). Coexists with hide/search/bands/drill-down (all act on the displayed
quotient; a hidden member drops out of its super-node). Deterministic + offline: only a
SORTED `MODULES` const is baked; the quotient + all positions are in-browser. Caveat: a file
split across target-core/target-iface lands in the LOWER (min-member) band; without indexed
module markers the dependency side groups by TOP DIRECTORY (pass `--dep-root` for finer).

### Hide ubiquitous noise + record a default (`--ignore`, html only)
High-fan-in symbols (`int32`/`FString`/`TArray`/primitives) drown the graph. The html has
a collapsible **HIDE panel** (right edge): a searchable list sorted by fan_in DESC, with
kind/zone/band filters, `select matching` + `select fan_in>=X` helpers, and per-row checkboxes.
- **Two-stage**: a checkbox is an INSTANT translucent **preview** (dims node + edges, no
  relayout); **[Apply]** commits + re-lays-out the VISIBLE subgraph (forceAtlas2 + bands
  re-run in-browser). A **delta hint** ("Apply: hide N, restore M") previews the change.
  `fit` reframes; click a node ↔ its list row (bidirectional locate). Hiding a node hides
  its **incident edges** (no dangling edges).
- **Persistent config** ("记录默认配置，下次也隐藏"): a committed `view_hide` key in
  `<store>/manyread.json` — `{version:1, names:[...], patterns:[fnmatch], min_fan_in:N}`
  (all keys optional). Matched symbols start applied-hidden on load but stay listed +
  re-enableable. **Auto-discovered each run.** `--ignore <file>` overrides ad-hoc (accepts
  a `{view_hide:{...}}` wrapper OR a bare `{names,patterns,min_fan_in}`). **Precedence:**
  `--ignore <file>` > `manyread.json[view_hide]` > none. **Match scope:** the node's label
  OR its trailing `::` segment (case-sensitive) — over-hide caveat: a legitimately-named
  user type sharing a bare name can be caught; prefer `names` over a broad `min_fan_in`
  when precision matters.
- **Export**: the panel's `Export` button emits the ready-to-paste `{view_hide:{...}}` JSON
  via clipboard + a Blob download + a textarea. Browsers can't write the repo file — the
  user OR the AGENT merges it into `manyread.json['view_hide']` (the agent edits the JSON
  directly). Export is a SNAPSHOT of the slice's hidden NAMES; keep `patterns`/`min_fan_in`
  by hand to keep catching NEW noise in larger slices. A malformed/typo'd `--ignore` file
  warns loudly to stderr; a syntax error in `manyread.json` silently resets ALL shared
  config (alias/exts/view_hide) for that run, so validate hand-merges.
- **Determinism / offline**: emitted html bytes stay byte-identical (only a SORTED `HIDDEN`
  list is baked; absent config => identical to v0.6.0). All relayout is in-browser, so
  repeated **Applies visibly rearrange** the layout (deterministic per visible-set via a
  fresh re-seed, never affecting the emitted file). Export uses Blob/clipboard/textarea
  only — no network.

## Rules
- Read-only on the store; deterministic (same index + roots ⇒ identical output).
- Ambiguous/unresolved targets are boundary **candidates to review**, not facts (C++
  by-name resolution is unsound — overloads/templates/macros). UE `*_API` macros are skipped.
- For higher dependency-side fidelity, index the dependencies too; the depth-1 sink keeps the graph bounded regardless.
