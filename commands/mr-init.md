---
name: mr-init
description: Initialize manyread for a repo — prompt for a store folder (default ./manyread), create it, and build the L1 index.
---

# /mr-init — initialize a manyread store

Create a visible, project-local `manyread/` store and build the first index. Run once per repo.

## Steps

1. **Check the location.** The store indexes the *current folder* by default. If the working
   dir is a **system folder** (a drive root like `W:\`, the home dir, or Desktop/Documents/
   Downloads), do NOT index it in place — **ask the user to pick or create a dedicated project
   folder** first. (`index_build.py --init` also warns when it detects a system location.)

2. **Reuse first (avoid re-indexing).** Browse the per-user hub for an already-indexed codebase
   that this project shares (e.g. a common engine):
   ```bash
   MR="${CLAUDE_PLUGIN_ROOT:-$(ls -d ~/.claude/plugins/cache/*/manyread/*/ 2>/dev/null | sort | tail -1)}"
   uv run --python 3.12 "$MR/scripts/index_build.py" --list-stores
   ```
   If a useful store exists, offer to **reuse it by COPYING it in** (copy, not link — safe against
   the source being deleted): `index_build.py --init --root <repo> --copy-from <that store>`.

3. **Ask where to put the store** (default: `./manyread` at the repo root — visible + shareable;
   `--store-at DIR` to place elsewhere) and **detect languages** (cpp, python, javascript,
   typescript, csharp, java, gdscript, glsl, …) to seed `--langs`.

4. **Init + build** (re-run the `MR=` line in each Bash call — shell state does not persist):
   ```bash
   uv run --python 3.12 "$MR/scripts/index_build.py" --init --root /abs/path/to/repo --langs cpp,python
   ```
   Creates `<repo>/manyread/` (`manyread.json`, `source.db`, `refs/ traces/ user/ short/`, and a
   `.gitignore` that shares `source.db` but ignores `user/`+`short/`), builds the L1 FTS5 index,
   and **registers the store in the per-user hub** (`~/.manyread/stores.json`) for browse/reuse.

5. **Enrich** (L2 tree-sitter symbols/edges) via `/mr-enrich`; optional parser-fix rules via
   `/mr-rules` (e.g. Unreal `*_API`), then re-`/mr-enrich`.

## Decision rules

- A git repo enumerates via `git ls-files`; otherwise `os.walk` honoring `ignore_globs` + `SKIP_DIRS`.
- **Commit** `manyread/manyread.json`, `source.db`, `refs/`, `traces/`. **Gitignored:** `user/` (a
  collaborator fills `user/config.json` with their machine paths) and `short/` (version-tagged
  ephemera — clear it by hand after committing).
- Subsequent builds: `uv run "$MR/scripts/index_build.py" --root <repo>` (store auto-discovered);
  add `--rebuild` after changing extensions in `manyread.json`.
- Report the printed stats: method, exts, files enumerated/indexed, db size, build_id.
