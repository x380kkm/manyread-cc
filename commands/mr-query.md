---
name: mr-query
description: Guided A* query loop over a manyread index — run trace preflight first, probe with SQL via query.py, then bounded-extract. Never read whole files.
---

# /mr-query — guided query loop

Execute the A* query discipline against `<store>/source.db` (the project-local `manyread/` dir,
found by walking up from cwd). Queries run through `query.py`, which executes the SQL and
auto-logs it to the per-store trace store.

## MANDATORY preflight (before the first query in a session)

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py preflight [intent terms...] [--limit 12]
```

- Expand the user's intent into multiple SQL-facing terms (table/column names, domain
  synonyms, likely anchors) and pass them so fuzzy matching finds prior patterns.
- Static/tagged rows appear first; reuse one if it overlaps the current question.
- If a dynamic row is flagged `(stale?)`, **surface it and ask the user** "still valuable?"
  then run `trace.py keep|shelve|clear <log_id>` per their answer. Never shelve/clear on your own.

## Query call

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py --root . "<SQL>" [--static] [--task TAG] [--no-log]
```

- Prints rows as TSV and, unless `--no-log`, logs the query (kind `dynamic` by default, or
  `static` with `--static`) capturing `valid_date` + `file_state` for referenced files.
- Always query through `query.py` (not raw SQLite) so logging happens.

## Operators (cheapest signal first)

1. **FTS5 probe** — broad → narrow → `OR` fallback; join `files` to filter by `ext`:
   `SELECT path, rank FROM files_fts WHERE files_fts MATCH '<kw>' ORDER BY rank LIMIT 20;`
2. **Symbol probe** (`symbols` table): `SELECT f.path, s.name, s.kind, s.start_line, s.end_line,
   s.start_byte, s.end_byte FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name LIKE
   '%<sym>%' ORDER BY f.path, s.start_line LIMIT 50;`
3. **Graph probe** (`edges` table): pre-check `SELECT DISTINCT relation, COUNT(*) FROM edges
   GROUP BY relation;` — `contains` = structure, `extends`/`implements` = hierarchy,
   `references` = weak hints. Not a resolved call graph.

## Bounded extraction (goal test)

Locate an offset, then take a small window — prefer a symbol span/anchor:

```sql
SELECT instr(content, '<anchor>') AS off FROM files WHERE path = '<target>';
SELECT substr(content, <start_offset>, <length>) FROM files WHERE path = '<target>';
```

Bounded-extraction rules (enforce strictly):

- **NEVER** `substr(content, 1, length(content))` or any whole-file read.
- Keep each slice ~500–2000 chars; extract per layer only; stop once evidence answers the question.
- Prefer a symbol's `start_byte`/line span over an arbitrary file start.

## Decision rules

- Improve h(n) (narrow with a probe) before spending g(n) (extracting source).
- Treat "not found" as valid after checking coverage (`COUNT(*)` + extension). Don't invent paths.
- Answer only from extracted evidence; state queries + coverage checked when evidence is absent.
- Tag a useful new query (`trace.py tag <log_id> <tag> "<reuse note>" [--kind static]`); offer a
  `/mr-ref` when a multi-file reading set emerges.
