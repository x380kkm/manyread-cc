# Provenance & Attribution

`manyread` is **derived from** the open-source project
**SQL-ManyThing** (https://github.com/IOchair/SQL-ManyThing, MIT License,
authored as "Hermes Agent"). It is not written from scratch: the indexing core,
the query-trace store, and the A* query discipline are adaptations of that
project's proven design. This file records exactly what was ported, replaced,
stripped, and newly added, so the lineage is auditable.

## Ported / adapted (reference-based)

| manyread file | Derived from (SQL-ManyThing) | What carried over |
|---|---|---|
| `scripts/index_build.py` | `scripts/phase1/manything_build_db.py` | `git ls-files --cached --others --exclude-standard` then `os.walk` + SKIP_DIRS enumeration; full DROP+CREATE FTS5 **trigram** index; `files` + `files_fts` insert pattern. |
| `scripts/trace.py` | `scripts/phase3/manything_query_log.py` | `query_log` / `query_notes` / `query_trace` view + indexes; `init` / `log` / `search` / `tag` / `preflight` command structure. |
| `skills/manyread/SKILL.md` | `SKILL.md` | A* search model, FTS5 probe / symbol probe / graph probe operators, bounded `substr()` extraction discipline, mandatory pre-flight + trace reuse. |

## Replaced (necessary substitutions)

| Area | Original | manyread | Why |
|---|---|---|---|
| Symbol enrichment | external `cymbal` CLI | `scripts/enrich_treesitter.py` (tree-sitter) | `cymbal` is not installable on the target machine; tree-sitter is cross-platform, multi-language, pip-installable via `uv`. The "enrich into the same DB" concept and the nodes/edges schema come from the original `graphify` design. |
| Query logging transport | bash `sqlite3_wrapper.sh` PATH-intercept + `pending.jsonl` import | `scripts/query.py` (execute + log) | The Unix PATH-intercept does not work on Windows/Claude Code; a direct execute-and-log CLI is cross-platform and simpler. |
| Project aliases | `~/.hermes/manything/aliases.sh` (bash) | `~/.manyread/registry.json` + per-project `.manyread/config.json` | Cross-platform; supports dynamic-path resolution for collaboration. |
| Index location | `.srcidx/source.db` | `.manyread/source.db` | Single namespaced state dir for config, db, and refs. |

## Stripped (removed from the reference)

Unreal-Engine material (`references/unreal/*`, `uht_enrich.py`, UE-GAS analysis,
`unreal-*` indexing profiles, the UE "Shock Test" framing), the Java build-output
enrichment example, the `.hermes/` agent coupling, and the Unix-only installer /
wrapper scripts.

## New (original to manyread)

| Area | Description |
|---|---|
| `scripts/ref.py` — **ref/prune layer** | A dated, task-tagged, git-worktree-managed "ref" reading workspace: copy relevant files out, interactively prune branches (`#ifdef`/dead/unrelated) and add semantic behavior annotations, reuse/select across sessions and projects, optional re-index. This is the project's signature differentiator and has no counterpart in SQL-ManyThing. |
| static / dynamic traces | Trace rows are split into durable **static** patterns and dated **dynamic** findings with a human-in-the-loop shelve/keep/clear lifecycle. |
| `uv`-based runtime | All scripts run via `uv`-managed Python with PEP 723 inline metadata — no system Python/sqlite required. |

## License

`manyread` is MIT-licensed (see `LICENSE`). The upstream SQL-ManyThing project is
also MIT-licensed; this attribution preserves that lineage.
