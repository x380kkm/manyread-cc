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
- target↔dependency boundary: separate the code analyzed from what it depends on

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

**Target↔dependency boundary (symbol-level)** — `scan.py boundary --root <repo>
--target-root <rel>` (or `/mr-boundary`): for a coupled body of code, deterministically
separate INTERNAL deps (the TARGET — the code analyzed) from what it DEPENDS ON (its
dependencies, possibly MANY distinct sources), at the SYMBOL level, and mark the
dependency interface. Edges (`extends`/`implements`/`uses_type`) resolve with a confidence
(`unique`/`ambiguous`/`unresolved` — never silently picked); the dependency side is a
depth-1 sink. `--view internal|dependency|both`; `--format html` is ONE page (GPU/WebGL
sigma.js + forceAtlas2, node size = fan-in/hubs big, bridges+hubs highlighted, target/dependency
zones by color+cluster, in-page view toggle, tap→path). Pass `--target-root` explicitly (markers aren't
indexed); `--target-root ""` = whole index is the target; `--dep-root` is repeatable for
multiple dependency sources. Use it to find split seams + the dependency API surface to
abstract. (The old `plugin-boundary` / `--plugin-root` / `--engine-root` names still work.)

### Layered views (`--layers flat|two|four`) + drill-down
The boundary html draws N ORDERED, FRAMED bands left→right (forceAtlas2 still does the
organic layout WITHIN each band); `--layers`/`--dep-depth` affect ONLY `--format html`.
- `--layers four` (default): `[target-core | target-iface || dep-iface | dep-core]` —
  target-core = insulated target symbols; target-iface = target symbols that touch a
  dependency (the call sites to wrap); dep-iface = the dependency API surface directly
  referenced (what to abstract); dep-core = dependency symbols behind the surface (empty
  unless `--dep-depth 2`, but the band is always drawn/labelled — not an error).
- `--layers two`: `[target || dependency]`; `--layers flat`: no boxes (plain zone color = today).
- `--dep-depth 2`: one extra bounded pass populates dep-core (behind the API surface).
  Default 1. NOTE `--dep-depth` (dependency expansion layers) is distinct from `--depth`
  (the BFS budget — leave it).
PRE-PROCESS by composition: a "chain of a file" is just `scan <seed> --max-nodes N` (a
bounded slice); for the framed boundary use `boundary --target-root <dir> --layers four`.
Presets are EXAMPLES — pick the seed/roots/layers per question (no rigid menu).
DRILL-DOWN: double-click any node in the html to open a NEW TAB with that node's
up+downstream chain (computed client-side over the LOADED slice). Honest limit: the
in-tab chain only sees the currently loaded slice — for a deeper/fresh chain, re-run
manyscan with that node as the seed.

### Hide noise + persistent ignore config
Ubiquitous high-fan-in symbols (`int32` / `FString` / `TArray` / primitives) are
pointed-to by huge numbers of nodes and drown the boundary graph. The html has a
collapsible **HIDE panel** (grab the tab on the right edge) to hide them:
- a searchable/sortable node list (sorted by **fan_in DESC** by default to surface the
  offenders), kind/zone/band filters (facets value-gated — flat/plain graphs show only
  kind+fan_in), `select matching` + `select fan_in>=X` bulk helpers, and a per-row checkbox.
- **TWO-STAGE**: a checkbox is an INSTANT translucent **preview** (dims the node + its
  edges, no relayout). The separate **[Apply]** button commits + re-lays-out the VISIBLE
  subgraph (forceAtlas2 + bands re-run in-browser, boxes redrawn). A **delta hint**
  ("Apply: hide N, restore M") shows what the pending change will do BEFORE you commit.
  `fit` reframes the camera; bidirectional **locate** (click a node → its list row flashes;
  click a row → the camera animates to + flashes that node). Hiding a node hides its
  incident edges (no dangling edges).
- **PERSISTENT config** ("记录默认配置，下次也隐藏"): a COMMITTED `view_hide` key in
  `<store>/manyread.json` — `{version:1, names:["int32","FString"], patterns:["TArray*"]
  (fnmatch), min_fan_in:20}` (all keys optional). Matched symbols start APPLIED-hidden on
  load but stay LISTED + re-enableable; the match scope is the label OR its trailing `::`
  segment (catches bare-name externals + qualified internals together). Auto-discovered
  each run; override ad-hoc with `boundary --ignore <file>` (precedence: `--ignore` >
  `manyread.json[view_hide]` > none). No config => identical to prior behavior.
- **EXPORT**: the panel's `Export` button emits the ready-to-paste `{view_hide:{...}}` JSON
  via clipboard + a Blob download + a textarea (offline, network-free). Browsers cannot
  write the repo file — the USER or the AGENT merges it into `manyread.json['view_hide']`
  (the agent edits the JSON directly; config.py has no shared-key writer). Export is a
  SNAPSHOT of the current slice's hidden NAMES — to keep catching NEW noise, keep
  `patterns`/`min_fan_in` by hand in the committed config.
- **IN-SLICE limit**: the panel, fan_in, and config only see the CURRENTLY LOADED slice;
  hiding is view-level + RECOVERABLE (it never deletes from the index — that is the enrich
  `drop`, a different layer). Emitted html bytes stay deterministic (only a SORTED `HIDDEN`
  list is baked; all relayout happens in-browser).

### Collapsible MODULE<->SYMBOL view (`--collapse off|file|dir`, html only)
A subsystem boundary (e.g. PostProcess) can be ~1769 symbol nodes — unreadable. `--collapse`
(default `off` = byte-identical to v0.6.2) groups both sides into collapsible MODULES:
target symbols by FILE (`file`: stem, so a .cpp/.h pair coalesces; `dir`: parent dir),
dependency symbols by ENGINE MODULE (same resolver as `--rollup-dep`; unresolved by-name
deps `dep:`/`amb:` go to an `(external)` bucket). DEFAULT = ALL modules COLLAPSED: you see
~60 module super-nodes (a readable overview), each sized by member count and placed in its
band. EXPAND specific modules from the side panel MODULES section (above the HIDE list) to
reveal their original symbols IN PLACE; the rest stay collapsed (progressive disclosure).
collapse-all / expand-all are there too. Expand/collapse is ONLY in the side panel — the
graph double-click still drills the up/down chain (no-op on a collapsed super-node). Hiding,
search, bands, and drill-down all coexist (they operate on the displayed quotient; a hidden
member drops out of its super-node). Deterministic + offline: only a SORTED `MODULES` const
is baked, the quotient + all positions are computed in-browser. NOTE: a collapsed file split
across target-core/target-iface lands in the LOWER band (min member band). Without indexed
module markers (`*.uplugin`/`*.Build.cs`), the DEPENDENCY side groups by TOP DIRECTORY
(coarse) — index markers or pass `--dep-root` hints for finer modules.

**Visual:** `--format html > deps.html` emits ONE self-contained file (GPU/WebGL sigma.js +
forceAtlas2 layout, smooth pan/zoom/drag, search, color-by-kind/zone, red+thick = bridge,
dashed/dotted = ambiguous/unresolved edge; **tap any node to see its file path**) — open in
any browser, no install. `mermaid`/`dot` render in VS Code / GitHub / mermaid.live. For a
many-node engine-scale slice, roll up with `--level dir|module`.

### Asset↔source linking + cross-language reach
- **`/mr-link-source`** (`scripts/link_source.py`): bridge a UE asset DSL to its source. Given a
  DSL asset store (e.g. a matlang material), a code store (engine C++), and the type-dictionary
  schema, it resolves each asset node to the C++ CLASS that implements it (`multiply` ->
  `UMaterialExpressionMultiply` at `file:line`) with a `unique` / `ambiguous(N)` / `unresolved` /
  `no-classPath` confidence (never silently picked). Read-only on both stores.
- manyscan now reaches beyond cpp/python: **UE asset DSL graphs** (matlang / bplisp / animlang,
  read by `/mr-enrich` as symbol+edge graphs — boundary/deps analyze the asset node graph / "连连看")
  and **javascript / typescript / csharp** dependency graphs (the per-language `.scm` layer). Index
  a DSL in its OWN store — a mixed C++ + matlang store blends `uses_type` semantics.


## Rules
- Needs a manyread store (`<repo>/manyread/source.db`). If absent, build it first
  (`/mr-init` then `/mr-enrich`).
- **Read-only** on the store; derived slices cache under `<store>/manyscan/`.
- manyscan loads manyread's own `lib/config`+`lib/db`, so schema changes follow
  manyread automatically. Future manyread sources (UE blueprints/materials, Unity
  metadata) plug in via the `SourceAdapter` seam — no scope/graph changes.
