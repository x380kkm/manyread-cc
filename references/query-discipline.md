# Query Discipline — A\* model, operators, bounded extraction

This is the query-time reading discipline for manyread. It is **generic**: it
applies to any source tree you index, regardless of language. Per-project specifics
(extensions, ignore globs) live in that repo's `manyread/manyread.json`
(see `indexing-and-profiles.md`).

> Always query through `query.py` so the trace store logs your queries
> automatically (see `trace-static-dynamic.md`):
>
> ```
> uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py --root . "<SQL>"
> ```

---

## 1. The A\* model

manyread models reading an unfamiliar codebase as an **A\* search** over a finite
state space. Every read costs tokens, so the search is biased toward narrow,
targeted probes instead of whole-file scans.

```
state space = files + FTS5 rows + symbols + edges + trace history
g(n)        = queries / tool calls / tokens already spent
h(n)        = remaining cost, estimated by FTS5 rank, symbol precision,
              edge (graph) coverage, and trace reuse potential
operator    = one SQL query, or one bounded substr() source extract
goal        = an evidence-rich answer read from the minimum necessary source text
```

**Narrow first. Extract second. Answer from evidence.**

This is nearly inverted from mainstream RAG: instead of retrieving chunks and
stuffing context, FTS5 *locates* targets, `substr()` *extracts* proof, and whole
files never enter the context window.

---

## 2. The four operators

Every step in the search is exactly one of four operators. Pick the cheapest one
that advances `h(n)`.

| # | Operator | Purpose | Cost |
|---|----------|---------|------|
| 1 | **Shape** — aggregate over `files` | learn the tree's size/layout before reading anything | tiny (counts only) |
| 2 | **Locate** — FTS5 `MATCH` over `files_fts` | rank candidate files/lines for a term | small (ranked paths) |
| 3 | **Symbolize** — query `symbols`/`edges` (L2) | jump to a precise definition + its containment/inheritance | small (rows with line spans) |
| 4 | **Extract** — bounded `substr()` over `files.content` | read only the proven slice | bounded (you choose the window) |

### Operator 1 — Shape (do this first, every time)

```sql
SELECT COUNT(*) FROM files;
SELECT ext, COUNT(*) FROM files GROUP BY ext ORDER BY COUNT(*) DESC;
SELECT substr(path,1,instr(path||'/', '/')-1) AS top, COUNT(*)
FROM files GROUP BY top ORDER BY COUNT(*) DESC LIMIT 20;
SELECT COUNT(*) FROM symbols;          -- 0 until L2 enrichment runs
SELECT DISTINCT relation FROM edges;   -- e.g. contains, extends
```

Group before listing. **Never** dump a bare `SELECT path FROM files` without a
`LIMIT`, grouping, or narrowing predicate.

### Operator 2 — Locate (FTS5 trigram MATCH)

```sql
SELECT path, rank FROM files_fts
WHERE files_fts MATCH 'layout prepare'
ORDER BY rank LIMIT 20;
```

The trigram tokenizer handles `CamelCase`, `snake_case`, CJK, and natural language
equally — no per-language tokenizer tuning. Combine terms to tighten `h(n)`.

### Operator 3 — Symbolize (after L2 enrichment)

Symbols carry precise line **and** byte spans, so you can extract without a search:

```sql
SELECT f.path, s.name, s.kind, s.start_line, s.end_line, s.start_byte, s.end_byte
FROM symbols s JOIN files f ON f.id = s.file_id
WHERE s.name LIKE '%Layout%' AND s.kind IN ('class','function')
ORDER BY f.path, s.start_line LIMIT 50;

-- containment / inheritance
SELECT relation, COUNT(*) FROM edges GROUP BY relation;
SELECT dst_name FROM edges WHERE relation='extends' AND src_symbol_id=?;
```

### Operator 4 — Extract (bounded substr)

Evidence lives in small regions of large files. Read only the slice:

```sql
-- find an anchor, then read a window around it
SELECT instr(content, 'export function prepare') AS off
FROM files WHERE path='src/layout.ts';

SELECT substr(content, max(1, :off - 200), 600)
FROM files WHERE path='src/layout.ts';
```

Or extract directly from a symbol's byte span (no search needed):

```sql
SELECT substr(f.content, s.start_byte + 1, s.end_byte - s.start_byte)
FROM symbols s JOIN files f ON f.id = s.file_id
WHERE f.path='src/layout.ts' AND s.name='prepare';
```

`substr()` is a **token-efficiency primitive**: you pay for the relevant context
window, not the full file. It mirrors how a developer scrolls to a line number
instead of reading top-to-bottom.

---

## 3. Decision tree

```
New question about the repo
│
├─ Have I run the Shape probes (Operator 1) this session?
│     no  → run them; learn ext mix + top-level layout, then continue
│     yes ↓
│
├─ Did trace preflight surface a reusable pattern? (see trace-static-dynamic.md)
│     yes → adapt that SQL instead of inventing a new probe
│     no  ↓
│
├─ Do I know the symbol name?
│     yes → Operator 3 (Symbolize): look it up in `symbols`, get its byte span
│     no  → Operator 2 (Locate): FTS5 MATCH to rank candidate files
│
├─ Do I have a precise location (path + line/byte or anchor)?
│     no  → narrow further (add MATCH terms / filter by kind) — DO NOT extract yet
│     yes ↓
│
└─ Operator 4 (Extract): bounded substr() around the location → answer from that slice
```

**Stop conditions**

- You can answer from extracted slices → stop; do not read more.
- A probe returns too many rows → narrow (add terms, add `LIMIT`, filter by
  `ext`/`kind`/path prefix); do not widen the extract window to compensate.
- You catch yourself about to read a whole file → that is a search failure; go
  back up the tree and narrow with Operator 2 or 3 first.

---

## 4. Expanding intent into SQL-facing terms

Do not search only with the user's natural words. Expand intent into terms likely
to appear in code or schema, so both FTS5 MATCH and trace preflight hit:

- *implementation overview* → `files`, `ext`, `path`, `symbols`, `edges`, `README`, `main`, `src`
- *library layout* → `entry`, `exports`, `package`, `README`, `src`, `test`, `index`
- *class hierarchy* → the base-class name, `extends`, `class`, `struct`

See also: `indexing-and-profiles.md` (what gets indexed),
`enrichment-treesitter.md` (how `symbols`/`edges` get filled),
`trace-static-dynamic.md` (how queries become reusable memory).
