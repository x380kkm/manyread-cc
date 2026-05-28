---
name: mr-trace
description: Manage the manyread cross-session query trace store — preflight, search, tag/promote, and the human-in-the-loop keep/shelve/clear of stale dynamic findings.
---

# /mr-trace — query trace store (L3)

The trace store is per-store, inside the project-local `manyread/` dir (found by walking up from
cwd). It splits traces into **static** (durable, reusable query *patterns* committed under
`<store>/traces/`) and **dynamic** (findings tied to current code state, carrying `valid_date` +
`file_state`, in the gitignored `<store>/short/traces/trace.db`).

## Calls

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py init
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py preflight <project> [terms...] [--limit 12] [--stale-days 30]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py search   <project> [terms...]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py tag      <log_id> <tag> "<note>" [--kind static]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py stale    <project> [--stale-days 30]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py keep|shelve|clear <log_id>
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py log --project <p> --sql "<SQL>" [--kind static|dynamic] [--task T] [--db PATH] [--files p1,p2]
```

(Normally `query.py` calls `log` for you; use `trace.py log` directly only for manual entries.)

## Decision rules

- **preflight** prints static/tagged rows first, then active dynamic (most recent), with any
  stale ones flagged `(stale?)`. This is the mandatory first step of `/mr-query`.
- A trace is **stale** when a recorded file's mtime/size changed, the file is missing, or it
  exceeds `--stale-days` (default 30). static traces are never stale.
- **Human-in-the-loop:** stale dynamic traces are never auto-deleted. Surface them to the user
  and ask "still valuable?" — then:
  - `keep` → refreshes `valid_date` and re-captures `file_state`, marks active.
  - `shelve` → status `shelved`, hidden from default preflight.
  - `clear` → status `cleared`.
  Never shelve/clear without asking.
- **tag** adds a note/tag to a log row; `--kind static` also promotes the row to a durable pattern.
- Use **search** for free-text lookup across sql/task/tag/note when preflight is too narrow.
