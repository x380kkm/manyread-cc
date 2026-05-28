# Trace store — static vs dynamic, and the ask-before-shelve flow

Every agent session starts cold. The **trace store** is manyread's cross-session
memory: it records the SQL you run so future sessions can replay working patterns
instead of re-deriving them. It lives inside the project-local `manyread/` store
(found by walking up from cwd): durable **static** patterns are committed under
`<store>/traces/`, while **dynamic** findings go to the gitignored DB at
`<store>/short/traces/trace.db`.

Queries reach the trace store automatically: when you query through `query.py`,
the query is logged unless you pass `--no-log`. This replaces the old Unix
`sqlite3` PATH-intercept wrapper — same effect (queries get logged), but
cross-platform with no PATH games.

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/query.py --root . "<SQL>" [--static] [--task TAG] [--no-log]
```

---

## 1. Two kinds of trace

A trace row carries a `kind`. The distinction is the heart of the store.

| | **static** | **dynamic** |
|---|------------|-------------|
| What it is | a durable, reusable query *pattern* (overview/schema-intro/anchor) | a *finding* tied to the current code state |
| Tied to code? | no — pattern survives edits | yes — references specific files |
| Carries `valid_date`? | no | yes (the day it was true) |
| Carries `file_state`? | no | yes — `[{path,mtime,size}]` for referenced files |
| Auto-shelved? | **never** | flagged stale, then **human decides** |
| Example | "ext histogram of the tree" | "the submit path lives in X.cpp:412, behind WIN64" |

The default `kind` is `dynamic`. Pass `--static` (or `trace.py log --kind static`)
for patterns worth keeping verbatim.

---

## 2. Staleness (dynamic only)

A dynamic trace is **stale** when:

- any file recorded in its `file_state` has a current `mtime`/`size` that differs
  from what was recorded (the code moved underneath the finding), **or**
- it exceeds an age threshold: `--stale-days` (default **30**).

Static traces are never stale — a pattern like "group files by ext" is true
regardless of edits.

Stale traces are **not** auto-deleted. That is a deliberate safety rule: a stale
finding may still be valuable, and only a human can judge.

---

## 3. The ask-before-shelve human-in-the-loop flow

This is the §9 lifecycle. At preflight the agent surfaces relevant traces; for any
flagged `(stale?)` it asks the user before changing anything.

```
preflight  ──►  agent shows: static patterns first,
                then active dynamic findings (stale ones marked "(stale?)")
                │
                └─► for each stale finding, agent asks the user: "still valuable?"
                         │
                         ├─ keep   → refresh valid_date + file_state (un-stale it)
                         ├─ shelve → status=shelved (hidden from default preflight,
                         │            recoverable)
                         └─ clear  → status=cleared (retired)
```

The agent never silently keeps, shelves, or clears a stale dynamic trace — it
asks. Notes carry a `status` ∈ {`active`, `shelved`, `cleared`}; only `active`
rows appear in the default preflight.

---

## 4. Preflight ordering

`trace.py preflight <project> [terms...]` is the **mandatory first step** before
the first query of a session (see `query-discipline.md` and `commands/mr-query.md`).
It returns, in order:

1. tagged / **static** rows first (the durable, reusable patterns), then
2. **active dynamic** rows, most recent first, with stale ones **marked but still
   shown** so the agent can ask about them.

Expand the user's intent into SQL-facing `terms` (table/column/identifier words),
not just their natural-language phrasing, so the term match hits real SQL text.

---

## 5. CLI reference

```
trace.py init
trace.py log --project A --sql "..." [--kind static|dynamic] [--task T] [--db PATH] [--files p1,p2]
trace.py preflight <project> [terms...] [--limit 12]
trace.py search <project> [terms...]
trace.py tag <log_id> <tag> <note> [--kind static]
trace.py stale <project>                 # list dynamic traces whose file_state diverged
trace.py keep|shelve|clear <log_id>
```

(Run any of these via `uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py ...`.)

### Schema (dynamic findings in `<store>/short/traces/trace.db`)

- `query_log` — one row per executed query: `ts, project, db_path, sql_text,
  kind, task_tag, valid_date, file_state, imported_at`.
- `query_notes` — annotations on a log row: `note, tag, status, created_at`.
- `query_trace` — a VIEW joining the two for convenient preflight/search.

The store is the scaling mechanism: N agents over M projects converge on efficient
patterns with no training — just replay of working queries.

See also: `query-discipline.md` (the A\* operators that produce these queries),
`ref-prune-workflow.md` (the same dated keep/shelve/clear model applied to refs).
