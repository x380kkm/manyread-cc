# manyread — Design Spec (2026-05-28)

A cross-platform Claude Code **plugin** that turns any large source tree into a local
SQLite search space and gives an AI agent a disciplined, low-token way to read it.
Derived from the open-source **SQL-ManyThing** project, with all Unreal-Engine-specific
material stripped, the query layer rewritten for Windows/macOS/Linux, symbol enrichment
moved to **tree-sitter**, and a new **ref/prune** layer that is the project's signature
differentiator.

> Runtime baseline (already verified on the dev machine): no system Python or sqlite3.
> Everything runs through **`uv`-managed Python 3.12** (SQLite 3.50.4, FTS5 trigram OK;
> tree-sitter C++/Python parsing OK). Scripts use **PEP 723 inline metadata** so
> `uv run scripts/<x>.py ...` auto-installs their dependencies.

---

## 1. Goals

1. **Pre-flight initialization** for a specific large repo: build a queryable index so
   the AI can retrieve precisely instead of grep+cat.
2. **Retrieval-augmented reading**: A* search discipline — narrow with SQL probes, then
   extract bounded source slices; never read whole files.
3. **Cross-session learning**: a query-trace store split into **static** (durable patterns)
   and **dynamic** (dated, decayable findings) with a human-in-the-loop shelve/keep/clear step.
4. **Ref/prune reading aid** (signature): copy relevant files into a dated, task-tagged,
   git-worktree-managed **ref** workspace; interactively prune branches and add semantic
   annotations; reuse/select refs across sessions and projects; optional re-index.
5. **Per-project config with dynamic paths** so it works across machines and collaborators.

## 2. Non-goals (v1)

- No full inter-procedural call-graph resolution (edges are containment + inheritance +
  best-effort name references; precise `calls` resolution is future work).
- No automatic index rebuild on a schedule (reactive only; the AI asks before building).
- No GUI. No network services. No MCP server (possible future).

---

## 3. Plugin layout (all paths under `${CLAUDE_PLUGIN_ROOT}/`)

```
.claude-plugin/plugin.json          # plugin manifest
README.md                           # human-facing, generic (UE stripped)
skills/manyread/SKILL.md            # query-time discipline + 4-layer protocol
commands/
  mr-init.md                        # /mr-init   — create config + register + index
  mr-index.md                       # /mr-index  — (re)build L1 FTS5 index
  mr-enrich.md                      # /mr-enrich — L2 tree-sitter enrich
  mr-query.md                       # /mr-query  — preflight + guided query loop
  mr-trace.md                       # /mr-trace  — trace import/search/tag/shelve
  mr-ref.md                         # /mr-ref    — create/list/prune/select/shelve refs
scripts/
  lib/config.py                     # config + dynamic path resolution (stdlib)
  lib/db.py                         # schema DDL + sqlite helpers (stdlib)
  index_build.py                    # L1 (PEP723: stdlib only)
  query.py                          # execute SQL against project db + auto-log to trace
  enrich_treesitter.py              # L2 (PEP723: tree-sitter, tree-sitter-cpp/python/javascript)
  trace.py                          # L3 trace store CLI (PEP723: stdlib)
  ref.py                            # L4 ref/prune/worktree CLI (PEP723: stdlib)
references/
  INDEX.md
  query-discipline.md               # A* model, operators, bounded substr (generic)
  indexing-and-profiles.md          # per-project config + language profiles
  enrichment-treesitter.md
  trace-static-dynamic.md
  ref-prune-workflow.md
config/manyread.example.jsonc
docs/specs/2026-05-28-manyread-design.md   # this file
```

### plugin.json (Claude Code plugin manifest)
```json
{
  "name": "manyread",
  "version": "0.1.0",
  "description": "Turn a large source tree into a local SQLite search space; A* query discipline, static/dynamic query traces, and a ref/prune reading workspace.",
  "author": { "name": "manyread" },
  "license": "MIT"
}
```
Commands and the skill are auto-discovered from `commands/` and `skills/`.

---

## 4. Runtime convention

Every script begins with PEP 723 metadata, e.g. `enrich_treesitter.py`:
```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-cpp", "tree-sitter-python", "tree-sitter-javascript"]
# ///
```
Stdlib-only scripts (`index_build.py`, `query.py`, `trace.py`, `ref.py`, `lib/*`) declare
`dependencies = []`.

Invocation (documented in commands + skill), always from anywhere:
```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py <args>
```
`lib/` is imported via `sys.path.insert(0, <scripts dir>)` at the top of each script, or
scripts add their own dir to the path. Keep `lib` import-safe (no side effects at import).

---

## 5. Config model + dynamic paths (`scripts/lib/config.py`)

**Home:** `MANYREAD_HOME` env, else `~/.manyread`.
**Global registry:** `<home>/registry.json` = `{ "<alias>": { "root": "<abs path>" } }`.
**Per-project config:** `<root>/.manyread/config.json` (travels with repo, committable):
```json
{
  "alias": "myproj",
  "languages": ["cpp", "python"],
  "exts": [".h", ".cpp", ".hpp", ".inl", ".py"],
  "profile": null,
  "ignore_globs": ["ThirdParty/*", "Intermediate/*", "*/node_modules/*"],
  "db_path": ".manyread/source.db",
  "refs_dir": ".manyread/refs"
}
```

**Dynamic path resolution (for collaboration):** a project root is resolved in priority
order so a committed config works on any machine:
1. env `MANYREAD_ROOT_<ALIAS>` (uppercased alias)
2. global registry entry
3. an explicit `--root` argument
Never hardcode absolute roots inside committed artifacts (ref manifests store paths
**relative to project root** + the alias, resolved at runtime).

**Public API (used by all scripts):**
```python
home() -> Path
def resolve_project(alias_or_path: str, root: str | None = None) -> ProjectConfig
@dataclass ProjectConfig: alias, root: Path, db_path: Path, refs_dir: Path,
                          languages: list[str], exts: list[str],
                          profile: str | None, ignore_globs: list[str]
def load_config(root: Path) -> ProjectConfig            # reads .manyread/config.json (+ defaults)
def save_config(cfg: ProjectConfig) -> None             # writes .manyread/config.json
def register(alias: str, root: Path) -> None            # updates global registry
def default_exts_for(languages: list[str]) -> list[str] # cpp/python/js/ts/... presets
```
Language→extension presets (built-in): cpp `.h .hpp .hh .inl .ipp .c .cc .cpp .cxx`,
python `.py .pyi`, javascript `.js .jsx .mjs .cjs`, typescript `.ts .tsx`,
csharp `.cs`, shader `.hlsl .usf .ush`, plus `.md .json .ini` as docs when requested.

---

## 6. L1 — Index (`scripts/index_build.py`) + DB schema (`scripts/lib/db.py`)

Phase-1 behaviour matches SQL-ManyThing (full DROP+CREATE rebuild) but uses config-driven
extensions/ignore instead of UE profiles. File enumeration: `git ls-files` when the root is
a git repo, else `os.walk` with `ignore_globs` + a built-in `SKIP_DIRS`
(`.git node_modules dist .venv venv __pycache__ .manyread`).

**Schema (project `<root>/.manyread/source.db`):**
```sql
CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, ext TEXT,
                    size INTEGER, mtime INTEGER, content TEXT);
CREATE VIRTUAL TABLE files_fts USING fts5(path, content, tokenize='trigram');
CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id),
                      name TEXT, kind TEXT, lang TEXT,
                      start_line INTEGER, end_line INTEGER,
                      start_byte INTEGER, end_byte INTEGER, parent_id INTEGER);
CREATE TABLE edges (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id),
                    src_symbol_id INTEGER, dst_symbol_id INTEGER,
                    dst_name TEXT, relation TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- indexes
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_file ON symbols(file_id);
CREATE INDEX idx_symbols_kind ON symbols(kind);
CREATE INDEX idx_edges_src ON edges(src_symbol_id);
CREATE INDEX idx_edges_rel ON edges(relation);
```
`db.py` exposes: `SCHEMA_SQL`, `connect(path)`, `init_schema(conn)`,
`set_meta(conn,k,v)`, `get_meta(conn,k)`. L1 writes `files`+`files_fts`+`meta(build_id=<ts>,
built_at, langs, exts)`; it creates the empty `symbols`/`edges`/`meta` tables so L2 can fill them.

**CLI:** `index_build.py <alias|--root PATH> [--rebuild]`
Prints: method, exts, files enumerated, indexed count, db size, build_id.

## 7. query.py — execute + auto-log

`query.py <alias> "<SQL>" [--static] [--task TAG] [--no-log]`
1. resolve project db, execute SQL, print rows (TSV).
2. unless `--no-log`, append to the trace store via `trace.py log` semantics: kind defaults
   `dynamic` (or `static` with `--static`), `valid_date = <today>`, `file_state` captured
   for any file paths referenced in the SQL (best-effort: paths that exist in `files`).
This **replaces** the bash `sqlite3` PATH-intercept wrapper — same effect (queries are
logged), cross-platform, no PATH games. The skill instructs the agent to query through
`query.py` so logging happens automatically.

---

## 8. L2 — tree-sitter enrichment (`scripts/enrich_treesitter.py`)

Reads `files` from the project db, parses each by language with tree-sitter, fills
`symbols` and `edges`. Languages v1: **cpp, python, javascript** (typescript via the
javascript grammar acceptably; note limitation). Graceful per-file try/except.

Extract per language (node types):
- **cpp:** `function_definition`, `class_specifier`, `struct_specifier`, `enum_specifier`,
  `namespace_definition`; method containment via nesting; inheritance via `base_class_clause`
  → `extends` edges. Also record `preproc_ifdef/preproc_if` spans as symbols of kind
  `ifdef_branch` (used by the prune layer).
- **python:** `function_definition`, `class_definition`; containment via nesting;
  base classes → `extends`.
- **javascript:** `function_declaration`, `class_declaration`, `method_definition`,
  `lexical_declaration` (arrow/const fns); `class_heritage` → `extends`.
Symbols carry precise `start_line/end_line/start_byte/end_byte` and `parent_id`
(containment). Edges: `contains` (parent→child), `extends`/`implements`, and optional
best-effort `references` by identifier match (off by default; `--refs` flag).

**CLI:** `enrich_treesitter.py <alias|--root PATH> [--langs cpp,python] [--refs]`
Idempotent: clears existing `symbols`/`edges` then refills (or per-file if `--incremental`).
Writes `meta(enriched_at, enrich_langs)`. Prints per-language symbol/edge counts.

---

## 9. L3 — Trace store (`scripts/trace.py`), static vs dynamic

Global store `<home>/trace.db`:
```sql
CREATE TABLE query_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  project TEXT NOT NULL, db_path TEXT, sql_text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'dynamic',       -- static | dynamic
  task_tag TEXT, valid_date TEXT, file_state TEXT, imported_at TEXT DEFAULT (datetime('now')));
CREATE TABLE query_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,
  log_id INTEGER REFERENCES query_log(id), note TEXT, tag TEXT,
  status TEXT NOT NULL DEFAULT 'active',       -- active | shelved | cleared
  created_at INTEGER);
CREATE VIEW query_trace AS
  SELECT ql.id, ql.ts, ql.project, ql.kind, ql.task_tag, ql.valid_date, ql.sql_text,
         qn.note, qn.tag, qn.status
  FROM query_log ql LEFT JOIN query_notes qn ON qn.log_id = ql.id;
CREATE INDEX idx_ql_project ON query_log(project);
CREATE INDEX idx_ql_kind ON query_log(kind);
```

**Semantics:**
- **static** = durable, reusable query *pattern* (overview/schema-intro). Never auto-shelved.
- **dynamic** = a finding tied to current code state. Always carries `valid_date` and
  `file_state` (json `[{path,mtime,size}]`). A dynamic trace is **stale** when any recorded
  file's current mtime/size differs, or it exceeds an age threshold (`--stale-days`, default 30).
- **Human-in-the-loop:** stale dynamic traces are NOT auto-deleted. At preflight, `trace.py`
  lists relevant dynamic traces flagged `(stale?)`; the AI surfaces them and asks the user
  "still valuable?" → user → `keep` (refresh valid_date/file_state), `shelve`
  (status=shelved, hidden from default preflight), or `clear` (status=cleared).

**CLI:**
```
trace.py init
trace.py log --project A --sql "..." [--kind static|dynamic] [--task T] [--db PATH] [--files p1,p2]
trace.py preflight <project> [terms...] [--limit 12]   # static first, then active dynamic (+stale flag)
trace.py search <project> [terms...]
trace.py tag <log_id> <tag> <note> [--kind static]
trace.py stale <project>                               # list dynamic traces whose file_state diverged
trace.py keep|shelve|clear <log_id>
```
Preflight ordering: tagged/static rows first, then active dynamic (most recent), stale ones
marked but still shown so the AI can ask about them.

---

## 10. L4 — Ref / Prune (`scripts/ref.py`) — signature layer

A **ref** = a dated, task-tagged reading workspace holding pruned + annotated copies of
selected files. Stored under `<root>/.manyread/refs/<id>/` where `id = <YYYY-MM-DD>-<task-slug>`.
Each ref has a manifest `ref.json`:
```json
{
  "id": "2026-05-28-rhi-submit",
  "task": "rhi submit path",
  "date": "2026-05-28",
  "source_project": "myproj",
  "status": "active",                  // active | shelved | cleared
  "worktree": null,                    // or relative worktree path if git-managed
  "files": [
    {"src": "Engine/Source/.../RHI.cpp", "rev": "<git sha|mtime>", "copy": "files/RHI.cpp"}
  ],
  "annotations": "notes.md",           // semantic behavior descriptions
  "sub_index": null                    // optional .manyread/source.db inside the ref
}
```

**Workflow (script provides scaffolding; AI+user do the semantic prune):**
1. `ref create --project A --task "rhi submit" [--from-query "<SQL returning paths>"] [--files a,b] [--worktree]`
   → makes `refs/<id>/`, copies selected files into `files/`, writes `ref.json` (status active,
   date today), creates empty `notes.md`. If `--worktree` and the project is a git repo, uses
   `git worktree add` to host an isolated branch dir and records its **relative** path.
2. **Prune + annotate** (AI-driven, guided by SKILL.md): edit the copies under `files/` to cut
   irrelevant branches (`#ifdef`/platform, dead paths, unrelated call-tree branches) and append
   semantic behavior descriptions to `notes.md`. Optional mechanical helper:
   `ref strip-ifdef <ref_id> --keep WIN64` removes non-matching `preproc_if*` spans
   (spans come from L2 `symbols` of kind `ifdef_branch`).
3. `ref index <ref_id>` (optional) → runs index_build + enrich on the ref dir for a
   reading-optimized sub-index.
4. Lifecycle: `ref list [--project A] [--all]`, `ref select <ref_id>` (prints manifest +
   notes + file list for reuse), `ref shelve <ref_id>`, `ref clear <ref_id>`.
   Same dated/shelve model as dynamic traces.
5. **Collaboration / dynamic paths:** `ref.json` + the copies + `notes.md` are committable.
   A teammate clones, and `ref select` resolves the source project root via
   dynamic-path resolution (§5), so machine-specific absolute paths never leak.
   One person initializes a ref; others reuse via `ref select`. Cross-project selection
   allowed (`ref list --all` scans every registered project's `refs_dir`).

**CLI summary:**
```
ref.py create --project A --task "..." [--from-query SQL | --files a,b] [--worktree]
ref.py list [--project A] [--all] [--status active]
ref.py select <ref_id>
ref.py strip-ifdef <ref_id> --keep MACRO[,MACRO...]
ref.py index <ref_id>
ref.py shelve|clear|keep <ref_id>
```

---

## 11. Skill + commands

`skills/manyread/SKILL.md` (frontmatter `name: manyread`, a precise `description` for
auto-trigger) ports the generic A* discipline from SQL-ManyThing's SKILL.md, UE removed,
and adds: the 4-layer mental model, the cross-platform `uv run` invocation, the
query→trace static/dynamic protocol (incl. the ask-before-shelve step), and the ref/prune
protocol. It points to `references/` and the `mr-*` commands. Keep it generic and
project-agnostic; per-project specifics live in each repo's `.manyread/config.json`.

Each `commands/mr-*.md` is a thin command doc: states the `uv run scripts/<x>.py` call(s),
expected args, and the agent's decision rules. `mr-query.md` encodes the mandatory preflight
(`trace.py preflight`) before first query and bounded-extraction rules.

## 12. Memory behaviors (auto-memory, separate from trace store)

Already seeded: `user-work-domain`, `code-reading-ref-prune`. Add one durable **feedback**
memory: *"For large/unfamiliar repos, before grep/cat, check for a manyread index
(`.manyread/source.db`); if absent, offer to build one; then query via `query.py` with A*
discipline (narrow → bounded substr), never whole-file reads."* This is the durable behavior
that pairs the skill with the user's reading habit.

## 13. What is stripped / replaced from SQL-ManyThing

- **Removed:** `references/unreal/*`, `references/phase2/ue-uht-generated-files.md`,
  `references/query/ue-gas-attribute-analysis.md`, `references/phase2/enrich-java-build.md`
  (+ script), `scripts/phase2/uht_enrich.py`, `scripts/verify/verify_ue_uht_sql.py`,
  `unreal-*` profiles in the indexer, `.hermes/`, the UE "Shock Test" framing.
- **Replaced:** cymbal symbol enrich → tree-sitter; `sqlite3_wrapper.sh` +
  `SQL-ManyThing-query-log` + `install.sh` (Unix PATH-intercept) → cross-platform
  `query.py`/`trace.py`; `~/.hermes/manything/aliases.sh` → `~/.manyread/registry.json` +
  per-project `.manyread/config.json`; `.srcidx/` → `.manyread/`.
- **Kept (generalized):** A* query discipline, FTS5 trigram index, bounded `substr`
  extraction, query-trace concept (now static/dynamic + cross-platform).

## 14. Build order (single plugin, four layers, one pass via workflow)

1. **Foundation:** `lib/config.py`, `lib/db.py`, `plugin.json`, dir skeleton, config example.
2. **Layers (parallel, each conforms to this spec + foundation):** L1 `index_build.py`+`query.py`;
   L2 `enrich_treesitter.py`; L3 `trace.py`; L4 `ref.py`; SKILL.md + commands; references.
3. **Verify:** each script self-smoke-tests via `uv run` against the SQL-ManyThing clone at
   `W:/cc/SQL-ManyThing` (a real, mixed py/md/sh tree) or a tiny fixture.
4. **Integrate (main loop):** full pipeline init→index→enrich→query→trace→ref on a real repo;
   fix gaps; seed the §12 memory.

## 15. Acceptance (smoke) criteria

- `index_build.py` builds `.manyread/source.db` with `files`+`files_fts`; FTS5 MATCH returns ranked rows.
- `enrich_treesitter.py` fills `symbols` (with line spans) and `contains`/`extends` edges for cpp+python.
- `query.py` executes SQL, prints rows, and creates a `query_log` row in `trace.db`.
- `trace.py preflight` returns static rows first, then dynamic with a stale flag when a file changed.
- `ref.py create` produces `refs/<date>-<task>/` with copies + `ref.json` + `notes.md`; `ref list`/`select` work; `--worktree` works in a git repo.
- Plugin loads: `plugin.json` valid; `skills/manyread/SKILL.md` frontmatter valid; `commands/mr-*.md` present.
- All scripts run via `uv run --python 3.12` with no system Python/sqlite.

---

## 16. Enrichment override rules + presets (planned extension)

Base tree-sitter extraction is generic and occasionally wrong on codebase-specific
idioms (e.g. Unreal's `class SDFPARTICLES_API UFoo` makes tree-sitter-cpp record the
export macro `SDFPARTICLES_API` as the class name and displace the real name `UFoo`;
forward declarations and used types also leak in as fake "classes"). Rather than
hardcode per-engine fixes into `enrich_treesitter.py`, corrections live in a
**project-scoped, agent-editable override layer** applied as a transform pass.

### Pipeline (compatibility-first)

```
tree-sitter extract (raw)  ->  apply project override rules  ->  write symbols/edges
```

No rules present  ->  identical to current behavior (backward compatible). Re-running
is idempotent: rules are always applied to a fresh raw extraction, never stacked on
already-transformed data.

### Rule expression (two layers; declarative-first)

- **Declarative (default, the only path the agent uses).** Rules in
  `<repo>/.manyread/rules.json`, schema-versioned. Each rule is `when` (match on
  symbol kind / name regex / language / is_forward_declaration / surrounding-token
  pattern) -> `action` (`rename` via "use next identifier", `set` attributes e.g.
  `api_exported=true`, `drop`, `reclassify` kind). Auditable; cannot execute code.
- **Script hook (dormant interface).** A documented extension point for a Python
  transform function exists in the design but is **only enabled on the user's explicit
  request** — the agent never reaches for arbitrary code on its own.

Symbols gain an optional `attrs` JSON field (e.g. `{"api_exported": true}`) and a
`provenance` marker recording which rule modified them (auditable / reversible).

### Agent self-repair loop (human-in-the-loop)

1. After enrich, the agent self-checks for noise (macro-as-class, repeated junk, fwd-decls).
2. It **proposes** a rule with a before/after **preview diff** (`enrich --rules-preview`).
3. **Discuss with the user; apply only on approval.**
4. Write the rule to `.manyread/rules.json`; re-run enrich to verify.

This generalizes to any language/engine quirk, not just Unreal.

### Presets (shareable + referenceable)

- Presets are **stored in project scope** (committable, travel with the repo) so a team
  shares the same corrections.
- A project may **reference an external preset directory** (a user-specified path) to
  reuse rule packs across projects (e.g. a shared `unreal` pack).
- The tool **reminds the user** to set up / reference presets when recurring quirks are
  detected, rather than auto-applying anything.

### CLI deltas (when built)

```
enrich_treesitter.py <alias|--root> [--rules PATH] [--rules-preview] [--no-rules]
rules.py init|preview|apply|list [--ref DIR]   # author/inspect override rules
```

Status: **designed, not yet implemented.** Slots in after L2 without changing the L1/L3/L4 contracts.
