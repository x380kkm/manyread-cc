# Ref / Prune — the signature reading workspace

The **ref/prune** layer is manyread's signature differentiator. Grep, LSP, and RAG
all *locate* code; none of them give you a durable, sharable, pruned reading
workspace tailored to one task. A **ref** is exactly that: a dated, task-tagged
folder holding pruned + annotated copies of the files relevant to a question,
managed by `ref.py` (L4).

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py <subcommand> ...
```

---

## 1. What a ref is

A ref lives at `<store>/refs/<id>/` where `id = <YYYY-MM-DD>-<task-slug>`.
Layout:

```
manyread/refs/2026-05-28-rhi-submit/
├── ref.json        # manifest
├── files/          # copies of selected source files (pruned in place)
│   └── RHI.cpp
└── notes.md        # semantic behavior descriptions (human/AI authored)
```

Manifest `ref.json`:

```json
{
  "id": "2026-05-28-rhi-submit",
  "task": "rhi submit path",
  "date": "2026-05-28",
  "source_project": "myproj",
  "status": "active",
  "worktree": null,
  "files": [
    {"src": "Engine/Source/.../RHI.cpp", "rev": "<git sha|mtime>", "copy": "files/RHI.cpp"}
  ],
  "annotations": "notes.md",
  "sub_index": null
}
```

`src` paths are stored **relative to the project root** (never absolute), so the
manifest is portable across machines (see §4).

---

## 2. The lifecycle

```
create ──► prune + annotate ──► (optional) index ──► reuse (select) ──► shelve / clear / keep
```

### create

```
ref.py create --project A --task "rhi submit" \
    [--from-query "<SQL returning paths>"] [--files a,b] [--worktree]
```

Makes `refs/<id>/`, copies the selected files (from `--from-query` results or an
explicit `--files` list) into `files/`, writes `ref.json` (status `active`, date
today), and creates an empty `notes.md`. With `--worktree`, when the project is a
git repo, it runs `git worktree add` to host an isolated branch directory and
records that worktree's **relative** path in the manifest.

### prune + annotate (the semantic core; AI-driven, guided by the skill)

This is where the value is created — the script scaffolds, the AI + user do the
thinking:

- Edit the copies under `files/` to **cut irrelevant branches**: disabled
  `#ifdef`/platform code, dead paths, unrelated call-tree branches. What remains is
  only the code that matters for the task.
- Append **semantic behavior descriptions** to `notes.md` — what the retained code
  *does*, invariants, gotchas. The notes are the reusable knowledge.

Optional mechanical helper:

```
ref.py strip-ifdef <ref_id> --keep WIN64[,MACRO...]
```

This removes non-matching `preproc_if*` spans from the ref copies. The spans come
from L2 `symbols` of kind `ifdef_branch` (see `enrichment-treesitter.md`) — the
bridge that lets enrichment drive pruning.

### index (optional)

```
ref.py index <ref_id>
```

Runs `index_build` + enrich on just the ref directory, producing a small
reading-optimized **sub-index** (recorded as `sub_index` in the manifest). Useful
when a ref is large enough to want FTS5/symbol queries of its own.

### reuse + lifecycle

```
ref.py list [--project A] [--all] [--status active]
ref.py select <ref_id>     # prints manifest + notes + file list, for reuse
ref.py shelve <ref_id>     # status=shelved (hidden from default list)
ref.py clear  <ref_id>     # status=cleared (retired)
ref.py keep   <ref_id>     # refresh / re-activate
```

Refs use the **same dated keep/shelve/clear model as dynamic traces**
(see `trace-static-dynamic.md`): nothing is destroyed silently; status governs
visibility.

---

## 3. Worktree management

`--worktree` is for tasks where you want to read against an isolated checkout
(e.g. a specific branch/commit) without disturbing the working tree. When the
project is a git repo, `ref create --worktree` uses `git worktree add` to spin up a
separate directory and records its **relative** path in `ref.json`. Non-git roots
simply skip the worktree (files are copied directly). The worktree keeps the ref's
source revision pinned while you prune.

---

## 4. Collaboration via dynamic paths

Refs are designed to be **shared**, and this is what makes manyread collaborative
rather than single-user:

- `ref.json`, the pruned `files/`, and `notes.md` are all committable artifacts.
  No machine-specific absolute path appears anywhere (`src` paths are
  project-relative; the worktree path is relative).
- **One person initializes** a ref (creates, prunes, annotates); **others reuse**
  it. A teammate clones the repo, and `ref select <ref_id>` resolves the source
  project root by walking up from cwd (or an explicit `--root`/`--store`; see
  `indexing-and-profiles.md` §4). So the absolute root never leaks into the shared
  artifact — each machine resolves its own.
- **Cross-project selection** is allowed: `ref list --all` scans every activated
  store's `refs/` dir (via the per-user hub `~/.manyread/stores.json`), so a ref
  built while reading project A can be discovered and reused while working on
  project B.

---

## 5. Why this beats locate-only tools

A grep hit or an LSP jump is ephemeral — it evaporates when the session ends. A
ref is a **persistent, pruned, annotated artifact**: the irrelevant code is gone,
the behavior is described in prose, the source revisions are pinned, and the whole
thing is committable and resolvable on any teammate's machine. The reading work
done once is captured and replayed, which is the same scaling idea as the trace
store applied to *reading* rather than *querying*.

See also: `trace-static-dynamic.md` (shared dated lifecycle),
`enrichment-treesitter.md` (the `ifdef_branch` spans behind `strip-ifdef`),
`indexing-and-profiles.md` (dynamic-path resolution).
