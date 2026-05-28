---
name: mr-index
description: (Re)build the L1 FTS5 trigram index for a manyread project from its per-project config.
---

# /mr-index — build the L1 index

(Re)build `<store>/source.db` with `files` + `files_fts` (FTS5 trigram). A full
DROP+CREATE rebuild driven by the per-project config's extensions and ignore globs. Also
creates the empty `symbols`/`edges`/`meta` tables so L2 enrichment can fill them.

## Call

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/index_build.py --root . [--rebuild]
# or point at an explicit source tree / store:
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/index_build.py --root /abs/path/to/repo [--rebuild]
```

## Args

- `--root PATH` — source tree to index (or omit to discover the store by walking up from cwd).
- `--store PATH` — the `manyread/` store dir (or omit to discover it from cwd).
- `--rebuild` — force a full rebuild (the default behavior is already a full rebuild).

## Decision rules

- Run `/mr-init` first if no `manyread/manyread.json` store exists.
- File enumeration uses `git ls-files` when the root is a git repo, else `os.walk` filtered by
  `ignore_globs` + built-in `SKIP_DIRS` (`.git node_modules dist .venv venv __pycache__ manyread`).
- A rebuild drops `symbols`/`edges` — re-run `/mr-enrich` afterward if you rely on L2.
- Report the printed stats (method, exts, enumerated, indexed, db path/size, build_id) to the user.
