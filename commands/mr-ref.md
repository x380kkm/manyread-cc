---
name: mr-ref
description: Create, prune, annotate, index, and reuse manyread refs — dated, task-tagged reading workspaces of pruned source copies, optionally git-worktree managed.
---

# /mr-ref — ref / prune reading workspace (L4)

A **ref** is a dated, task-tagged reading workspace under `<store>/refs/<id>/`
(`id = <YYYY-MM-DD>-<task-slug>`) holding pruned + annotated copies of selected files, a
`ref.json` manifest, and a `notes.md`. It is the signature reuse layer: committable and
selectable across sessions and projects.

## Calls

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py create --root . --task "<task>" \
    [--from-query "<SQL returning paths>" | --files a,b] [--worktree]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py list   [--all] [--status active]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py select <ref_id>
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py strip-ifdef <ref_id> --keep MACRO[,MACRO...]
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py index  <ref_id>
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/ref.py shelve|clear|keep <ref_id>
```

## Workflow + decision rules

1. **create** — after `/mr-query` probes identify the relevant files. Use `--from-query` with a
   SQL that returns paths, or `--files a,b`. `--worktree` hosts an isolated git branch dir (git
   repos only), recorded as a path **relative** to the project root.
2. **prune + annotate** (semantic, AI + user) — edit the copies under `files/` to cut
   irrelevant branches (platform `#ifdef`s, dead paths, unrelated call-tree branches) and append
   behavior descriptions to `notes.md`. Mechanical helper `strip-ifdef --keep WIN64` removes
   non-matching `preproc_if*` spans (from L2 `symbols` of kind `ifdef_branch`).
3. **index** (optional) — build a reading-optimized sub-index over the ref dir.
4. **lifecycle / reuse** — `list` (add `--all` to scan every registered project's `refs_dir`),
   `select` prints the manifest + notes + file list for reuse, and `shelve`/`clear`/`keep`
   follow the same dated model as dynamic traces.

- `ref.json` + copies + `notes.md` are committable; `select` resolves the source root via
  dynamic-path resolution, so absolute machine paths never leak. One person initializes a ref;
  others reuse via `select`. Cross-project selection is allowed.
