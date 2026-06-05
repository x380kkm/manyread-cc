---
name: manyread
description: Use PROACTIVELY — without waiting for any keyword — whenever you read, explore, search, explain, trace, or try to understand code in a repository or large/unfamiliar codebase. Engage it BEFORE using ls/find/cat/grep/Read to look around. manyread indexes the tree into local SQLite (FTS5 + tree-sitter symbols) for bounded, low-token retrieval; if a repo has no index yet, STOP and offer to build one (/mr-init) before exploring by hand.
version: 0.1.0
license: MIT
metadata:
  tags: [code-search, sqlite, fts5, tree-sitter, a-star, source-index, ref-prune]
---

# manyread

## Overview

manyread turns a large source tree into a local SQLite search space and gives the agent a
disciplined, low-token way to read it. Instead of `grep`+`cat` across thousands of files, the
agent narrows with SQL probes (A* search) and extracts only bounded source slices.

Everything runs through `uv`-managed Python 3.12 — there is no system `python` or `sqlite3`.
Each script carries PEP 723 inline metadata, so `uv run` auto-installs dependencies.
This skill governs query-time behavior; build-time detail lives in `references/` and the
`mr-*` commands. Keep this file generic and project-agnostic — per-project specifics live in
each repo's `manyread/manyread.json`.

## Proactive first action (do this without being asked)

When the user asks anything that needs understanding a repo — "what is this project?",
"how does X work?", "where is Y?", "explain/trace …" — your FIRST step is:

1. Check for a manyread store: does `manyread/source.db` exist (walk up from cwd)?
2. **If it does NOT exist: STOP. Do NOT begin exploring with `ls`/`find`/`cat`/`grep`/`Read`.**
   Ask the user: *"This repo isn't indexed yet — shall I build a manyread index (`/mr-init`)?
   It makes reading much cheaper."* Build it if they agree; fall back to manual exploration
   only if they decline (then offer again later).
3. If it exists (or once built): query through manyread (preflight → FTS5 / symbol / graph
   probes → bounded `substr`), not grep/cat.

The user should never have to type "manyread" to get this — it is the default.

## When to Use

Use this skill when:

- A project has a manyread index at `<root>/manyread/source.db`, or one can be built.
- The user asks for code search, tracing, implementation lookup, symbol discovery, or
  architecture inspection of a large/unfamiliar tree.
- You want to minimize token cost and avoid whole-file reads.

Do not use this skill when:

- No manyread DB exists and the task is too small to justify building one.
- The task is non-code, or the index is known stale and rebuilding is disallowed.

If no `manyread/source.db` exists for a large/unfamiliar repo, offer to build one
(`/mr-init` then `/mr-index`, optionally `/mr-enrich`) before falling back to grep/cat.

## 4-Layer Mental Model

manyread is four cooperating layers. Use them in order of cheapest signal first.

| Layer | Name | Backing | Built by | Queried via |
|---|---|---|---|---|
| **L1** | **index** | `files`, `files_fts` (FTS5 trigram) | `index_build.py` | FTS5 MATCH + bounded `substr` |
| **L2** | **enrich** | `symbols`, `edges` (tree-sitter) | `enrich_treesitter.py` | symbol probe, graph probe |
| **L3** | **trace** | static patterns in `<store>/traces/`; dynamic findings in `<store>/short/traces/trace.db` | `query.py` auto-log + `trace.py` | preflight + reuse |
| **L4** | **ref** | `<store>/refs/<id>/` pruned copies | `ref.py` | `ref select` reuse |

- **index** = what text exists where (broad-to-narrow discovery).
- **enrich** = named structure: functions, classes, containment, inheritance (precise spans).
- **trace** = cross-session memory of which queries answered which questions.
- **ref** = a curated, pruned, annotated reading workspace you keep across sessions/projects.

## Invoking manyread scripts (resolve the plugin root)

Scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Claude Code expands `${CLAUDE_PLUGIN_ROOT}`
for **hooks**, but it is NOT reliably set in the Bash tool's environment, and Bash state does
not persist between tool calls — so make each invocation **self-contained**: resolve the
plugin root inline, in the same command.

Canonical form (works whether installed via `/plugin` or run from a local checkout):

```bash
MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
uv run --python 3.12 "$MR/scripts/query.py" --root . "<SQL>"
```

- Installed via `/plugin`: the glob resolves `~/.claude/plugins/cache/<marketplace>/manyread/<version>/`.
- Local dev / not installed: set it yourself — `MR=/abs/path/to/manyread` (Windows e.g. `MR=W:/cc/manyread`).
- Below and in the `/mr-*` commands, any `${CLAUDE_PLUGIN_ROOT}/scripts/X.py` means `"$MR/scripts/X.py"`
  **after** running the resolver line — re-run that line in each Bash call, since shell state does not persist.

Point a script at the work via `--root PATH` (source tree) and/or `--store PATH` (the
`manyread/` dir), or omit both to discover the store by walking up from cwd. There is no
alias registry — the only per-user env-dir thing is the HUB `~/.manyread/stores.json`, a
registry of activated store PATHS for browse/reuse. Query through `query.py` (not raw
SQLite) so every query is auto-logged to the trace store.

## A* Search Model

| A* term | manyread meaning |
|---|---|
| State space | indexed files, FTS rows, symbols, edges |
| Start state | the user question + known project context |
| Goal state | a bounded source extract that directly answers the question |
| g(n) | cost paid so far: SQL queries, slices, tokens |
| h(n) | optimistic remaining cost from rank, symbol precision, edges, trace reuse |
| f(n) | g(n) + h(n), minimized each step |
| Operator | one SQL probe, one symbol/graph lookup, or one bounded extract |
| Pruning | empty MATCH, weak rank, missing symbol, irrelevant path |
| Goal test | exact bounded evidence obtained — stop reading |

Rules: improve h(n) before spending g(n); prefer high-signal probes over wide reads; extract
source only after a probe pins down path + offset; treat "not found" as a valid result after
checking coverage; never invent code paths the DB does not show.

## Operators

Run every probe through `query.py` so it is logged:

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py --root . "<SQL>" [--static] [--task TAG]
```

### Operator 1 — FTS5 probe (L1)

Broad → narrow → OR fallback; filter by extension via a join to `files`:

```sql
SELECT path, rank FROM files_fts WHERE files_fts MATCH '<kw>' ORDER BY rank LIMIT 20;
SELECT path, rank FROM files_fts WHERE files_fts MATCH '<kw1> <kw2>' ORDER BY rank LIMIT 20;
SELECT path, rank FROM files_fts WHERE files_fts MATCH '<kw1> OR <kw2>' ORDER BY rank LIMIT 20;
SELECT f.path, rank FROM files_fts JOIN files f ON f.rowid = files_fts.rowid
  WHERE files_fts MATCH '<kw>' AND f.ext IN ('.cpp','.h') ORDER BY rank LIMIT 20;
```

`files.ext` keeps the leading dot (`.py`, not `py`). Strongly-negative rank = likely target.

### Operator 2 — Symbol probe (L2, `symbols` table)

Use when the question names a function, class, method, type, or handler. Symbols carry
precise line/byte spans and a `parent_id` for containment — query the **table** directly
(this is the new tree-sitter schema; there is no `file_enrich` JSON):

```sql
SELECT f.path, s.name, s.kind, s.lang, s.start_line, s.end_line, s.start_byte, s.end_byte
FROM symbols s JOIN files f ON f.id = s.file_id
WHERE s.name LIKE '%<symbol>%'
ORDER BY f.path, s.start_line LIMIT 50;
```

If empty: check `SELECT COUNT(*) FROM symbols;`, confirm the target extension was indexed and
enriched, then fall back to FTS5. Do not conclude the symbol is absent until coverage is known.

### Operator 3 — Graph probe (L2, `edges` table)

Use for containment, inheritance, dependency edges, and best-effort references. **Pre-check
relations** before interpreting — edges are containment + inheritance + declarative DEPENDENCY
edges + optional name references; not a fully resolved call graph:

```sql
SELECT DISTINCT relation, COUNT(*) FROM edges GROUP BY relation;
```

| relation | meaning | use |
|---|---|---|
| `contains` | parent symbol → child symbol | structure/outline, not call flow |
| `extends` / `implements` | type hierarchy | OOP inheritance analysis |
| `calls` / `imports` / `uses_type` | dependency edges from a per-language `.scm` query — cpp via the walker; python/javascript/typescript/csharp/… via `scripts/queries/<lang>.scm`, overridable per project at `<root>/.manyread/queries/<lang>.scm` | dependency / impact analysis (feeds manyscan) |
| extension-provided edges | optional domain extensions can add their own DSL graph edges (e.g. the UE asset DSL extension) | see the extension's skill addendum |
| `references` | best-effort name match (`--refs`) | weak dependency hints only |

Inspect edges out of a symbol (resolved by `dst_symbol_id`, else by `dst_name`):

```sql
SELECT s.name AS src, e.relation, COALESCE(d.name, e.dst_name) AS dst
FROM edges e
JOIN symbols s ON s.id = e.src_symbol_id
LEFT JOIN symbols d ON d.id = e.dst_symbol_id
WHERE s.name LIKE '%<symbol>%'
ORDER BY e.relation LIMIT 100;
```

`cpp` also records `#ifdef`/`#if` spans as symbols of kind `ifdef_branch` — the ref/prune
layer uses these.

### Operator 4 — Bounded source extraction (goal test)

Extraction is the A* goal test, not browsing. Locate an offset, then take a small window;
prefer a symbol's `start_byte`/line span as the anchor:

```sql
SELECT instr(content, '<anchor>') AS off FROM files WHERE path = '<target>';
SELECT substr(content, <start_offset>, <length>) FROM files WHERE path = '<target>';
```

Rules: **never** `substr(content, 1, length(content))` (whole file); keep each slice ~500–2000
chars; prefer symbol spans/anchors over file starts; extract per layer only; stop once the
evidence answers the question.

## Mandatory Preflight (L3)

Before the **first** query against a project in a session, run the trace preflight. It lists
durable **static/tagged** patterns first, then active **dynamic** findings (recent results
tied to current code), flagging any that may be `(stale?)`:

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py preflight <project> [intent terms...] [--limit 12]
```

Expand the intent into multiple SQL-facing terms (table/column names, domain synonyms, likely
anchors) before passing them. Reuse a matching static/tagged trace instead of re-exploring.
If a fresh dynamic query proves useful, tag it so it becomes durable:

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py tag <log_id> <tag> "<when to reuse this>" [--kind static]
```

(`--kind static` promotes the logged query to a durable pattern.)

### Static vs Dynamic — and the ask-before-shelve step

- **static** = a durable, reusable query *pattern* (overview/schema intro). Never auto-shelved.
- **dynamic** = a finding tied to current code state; carries a `valid_date` + captured
  `file_state`. It becomes **stale** when a recorded file's mtime/size changed or it exceeds
  `--stale-days` (default 30).

Stale dynamic traces are **never auto-deleted**. When preflight flags one `(stale?)`, you
**MUST surface it and ask the user** "this finding may be stale — still valuable?" before
acting. Then run exactly what the user decides:

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py keep   <log_id>   # refresh valid_date + re-capture file_state
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py shelve <log_id>   # status=shelved, hidden from default preflight
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py clear  <log_id>   # status=cleared
```

Never shelve or clear a trace on your own initiative — the shelve/keep/clear decision is the
human-in-the-loop step.

## Ref / Prune Protocol (L4)

A **ref** is a dated, task-tagged reading workspace under `<store>/refs/<id>/` holding
pruned + annotated copies of selected files, plus a `ref.json` manifest and a `notes.md`.

1. **Create** (after probes identify the relevant files):
   ```
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py create --root . --task "<task>" \
     [--from-query "<SQL returning paths>" | --files a,b] [--worktree]
   ```
   `--worktree` hosts an isolated git branch dir (git repos only); its path is stored relative.
2. **Prune + annotate** (you + the user, semantic): edit the copies under `files/` to cut
   irrelevant branches (platform `#ifdef`s, dead paths, unrelated call-tree branches) and append
   semantic behavior descriptions to `notes.md`. Mechanical helper for ifdef spans (from L2
   `symbols` of kind `ifdef_branch`):
   ```
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py strip-ifdef <ref_id> --keep WIN64
   ```
3. **Index** (optional) a reading-optimized sub-index of the ref dir:
   ```
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py index <ref_id>
   ```
4. **Lifecycle / reuse** — same dated shelve model as dynamic traces:
   ```
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py list [--root .] [--all] [--status active]
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py select <ref_id>          # prints manifest + notes + files
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py shelve|clear|keep <ref_id>
   ```

`ref.json` + copies + `notes.md` are committable; `ref select` resolves the source root via
dynamic paths, so machine-specific absolute paths never leak. One person initializes a ref;
others reuse it via `ref select`. Cross-project selection is allowed (`ref list --all`).

## Enrichment Override Rules (self-repair loop)

Base tree-sitter extraction (L2) is generic and occasionally wrong on codebase-specific idioms.
The common C++ export-macro-as-class-name case (`class MYMOD_API UThing` → symbol `MYMOD_API`,
plus the lost class body) is now auto-corrected **before parse** by the built-in length-preserving
**macro_strip** (default on; generic all-caps detector, extend/disable via `manyread.json`'s
`macro_strip` key) — it needs no rule. The remaining idioms — forward-declaration / used-type junk
leaking in as fake symbols, a project-specific macro the generic detector misses, wrong `kind` —
are corrected by a **project-scoped, agent-editable override layer** at `<store>/rules.json`,
applied as a pure transform pass AFTER raw extraction:

```
tree-sitter extract (raw)  ->  apply project override rules  ->  write symbols/edges
```

No rules file (and no `--rules`) → identical to base behavior; the transform is additive
and fully optional (`--no-rules` reverts to raw symbols). Symbols gain `attrs` (json, e.g.
`{"api_exported": true}`) and `provenance` (json, which rule id touched the row) for audit.

**Self-repair loop (human-in-the-loop — never silent):**

1. After `/mr-enrich`, self-check the `symbols` output for noise (macro-as-class, repeated
   junk, fwd-decl entries, wrong `kind`).
2. **Propose** a declarative rule (match on `lang`/`kind`/`name_regex`/`path_glob` → action
   `rename_to_next_identifier` | `set_attr` | `drop` | `reclassify`) and show its effect
   with a preview — this prints a before/after diff and writes NOTHING:
   ```
   uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/enrich_treesitter.py --root . --rules-preview
   ```
3. **Discuss the diff with the user. Apply only on approval.** Then write the rule into
   `<store>/rules.json` (use `/mr-rules` to scaffold/validate) and re-run `/mr-enrich`.

Presets are **project-scoped** (committable, travel with the repo). A project may reference
an **external preset dir** to reuse a shared rule pack (e.g. the built-in `unreal` pack) via
`extends_presets` + `preset_dirs` in `rules.json`. The tool **reminds** you to set up/reference
presets on recurring quirks rather than auto-applying anything.

A dormant Python **script-hook** extension point exists in the design but is **only enabled
on the user's explicit request** — never reach for arbitrary code on your own; the declarative
rules above are the only path you use. See `references/enrich-overrides.md` and `/mr-rules`.

## Query-Time Decision Tree

1. Run the preflight; surface and ask about any `(stale?)` dynamic traces.
2. Reuse a relevant static/tagged trace if one exists.
3. If the question names a symbol → symbol probe (Operator 2).
4. If it names behavior/error/endpoint/UI text → FTS5 probe (Operator 1).
5. If it asks relationships → graph probe (Operator 3) after pre-checking `relation`s.
6. Locate offsets via `instr` or a symbol span, then bounded-extract (Operator 4).
7. Answer only from extracted evidence; if absent, state what was queried and the coverage checked.
8. Tag a useful new query; offer a ref when a multi-file reading set emerges.

## References and Commands

| Topic | Reference |
|---|---|
| A* model, operators, bounded substr | `references/query-discipline.md` |
| Per-project config + language profiles | `references/indexing-and-profiles.md` |
| tree-sitter enrichment | `references/enrichment-treesitter.md` |
| enrichment override rules + presets | `references/enrich-overrides.md` |
| static vs dynamic traces | `references/trace-static-dynamic.md` |
| ref/prune workflow | `references/ref-prune-workflow.md` |
| Reference index | `references/INDEX.md` |

Commands — build/read: `/mr-init`, `/mr-index`, `/mr-enrich`, `/mr-query`, `/mr-trace`,
`/mr-ref`, `/mr-rules`. Dependency / refactoring analysis (the **manyscan** skill):
`/mr-deps`, `/mr-boundary`. Optional domain extensions add their own commands when enabled
(e.g. the UE asset DSL extension's `/mr-validate` + `/mr-link-source` — see
`scripts/extensions/ue/skill_addendum.md`).

## Common Pitfalls

1. Skipping the preflight or querying raw SQLite (queries then go unlogged) — always go through `query.py`.
2. Reading whole files. Bounded extraction is mandatory; never `substr(content,1,length(content))`.
3. Forgetting the dot in `files.ext` (use `.py`, not `py`).
4. Assuming a symbol/edge is absent from one empty query — check counts and extension coverage.
5. Treating `contains` edges as call flow — pre-check `relation`s; dependency edges
   (`calls`/`imports`/`uses_type`) come from per-language `.scm` queries, not a fully-resolved call graph.
6. Rebuilding L1 and forgetting to re-run `/mr-enrich` — a rebuild drops `symbols`/`edges`.
7. Shelving/clearing a stale trace without asking the user — that decision is human-in-the-loop.
8. Hardcoding absolute roots in committed artifacts — refs store paths relative to the project root.
