# Indexing & Profiles — per-project config, language presets, dynamic paths

How manyread decides **what** to index and **where** the index lives. The L1
indexer (`index_build.py`) is config-driven; there are no hardcoded engine or
framework profiles. Everything below is generic and language-agnostic.

---

## 1. Where state lives

| Scope | Path | Purpose | Committable? |
|-------|------|---------|--------------|
| Store (project-local) | `manyread/` (default `./manyread`, walk up from cwd) | the visible per-project store dir | n/a |
| Per-user hub | `~/.manyread/stores.json` | registry of **activated store PATHS** for browse/clean/reuse (NOT data, NOT an alias registry) | no (machine-specific) |
| Shared config | `<store>/manyread.json` | what to index, paths | **yes** |
| Project DB | `<store>/source.db` | the SQLite search space (FTS5 + symbols/edges) | shared |
| Refs | `<store>/refs/` | ref/prune workspaces | shared (selectively) |
| Static traces | `<store>/traces/` | durable STATIC query patterns | shared |
| Override rules | `<store>/rules.json` | enrichment override rules | shared |
| Per-user config | `<store>/user/` | machine-specific config | no (gitignored) |
| Ephemeral | `<store>/short/` (`short/refs`, `short/rdc`, `short/traces`) | ephemera incl. dynamic trace db at `short/traces/trace.db` | no (gitignored) |

> The store's own `.gitignore` shares `source.db`, `refs/`, `traces/`, and
> `manyread.json`, but ignores `user/` and `short/`.

---

## 2. Shared config (`<store>/manyread.json`)

This file **travels with the repo** and is committable. It must never contain
machine-specific absolute paths — the source tree is resolved at runtime via
`--root`/`--store` or by walking up from cwd (§4); machine-specific paths belong
in the gitignored `<store>/user/`.

```json
{
  "languages": ["cpp", "python"],
  "exts": [".h", ".cpp", ".hpp", ".inl", ".py"],
  "profile": null,
  "ignore_globs": ["ThirdParty/*", "Intermediate/*", "*/node_modules/*"]
}
```

| Field | Meaning |
|-------|---------|
| `languages` | enabled languages; drive default ext presets + tree-sitter enrichment |
| `exts` | explicit extension allowlist; if omitted, derived from `languages` |
| `profile` | reserved named profile (forward-compat; `null` disables) |
| `ignore_globs` | glob patterns (relative to root) excluded during the `os.walk` fallback |

The DB and ref/prune workspaces are fixed locations inside the store
(`<store>/source.db`, `<store>/refs/`), so they need no config entries. A starter
template ships at `config/manyread.example.jsonc`.

---

## 3. Language → extension presets

When `exts` is omitted, `config.default_exts_for(languages)` derives it from these
built-in presets:

| Language | Extensions |
|----------|------------|
| `cpp` | `.h .hpp .hh .inl .ipp .c .cc .cpp .cxx` |
| `python` | `.py .pyi` |
| `javascript` | `.js .jsx .mjs .cjs` |
| `typescript` | `.ts .tsx` |
| `csharp` | `.cs` |
| `shader` | `.hlsl .usf .ush` |
| `docs` (on request) | `.md .json .ini` |

List `exts` explicitly in config when you want to trim or extend a preset (e.g.
add `.md` to a code-only language set, or drop `.inl` from a C++ project).

---

## 4. Path resolution (collaboration)

A committed `manyread.json` must work on any machine and for any teammate, so
nothing committed stores an absolute path. Every CLI takes `--root PATH` (the
source tree) and/or `--store PATH` (the `manyread/` dir), or you omit both and the
store is **discovered by walking up from cwd**. There is **no alias registry** and
no `MANYREAD_ROOT_<ALIAS>` env resolution.

The only per-user env-dir artifact is the HUB at `~/.manyread/stores.json` — a
registry of **activated store PATHS** used for browse/clean/reuse (e.g. `ref list
--all` or `index_build.py --list-stores`), not for resolving a project. A teammate
clones the repo and runs commands from inside the tree (or passes `--root`/
`--store`); machine-specific paths live in the gitignored `<store>/user/`. Ref
manifests store file paths **relative to the project root**, resolved the same way
(see `ref-prune-workflow.md`).

---

## 5. File enumeration

`index_build.py` chooses how to enumerate files based on the root:

- **Git repo** → `git ls-files` is authoritative (respects the repo's tracked
  set; `ignore_globs` is not needed). Fast and accurate.
- **Plain directory** → `os.walk` filtered by `ignore_globs` plus a built-in
  `SKIP_DIRS` set: `.git node_modules dist .venv venv __pycache__ manyread`.

Either way, only files whose extension is in `exts` are indexed. L1 does a full
`DROP + CREATE` rebuild (a `DROP TABLE` does not shrink the file, so a compact
replacement deletes/rebuilds the DB rather than relying on `VACUUM`).

**CLI:**

```
uv run --python 3.12 ${CLAUDE_PLUGIN_ROOT}/scripts/index_build.py [--root PATH] [--store PATH] [--rebuild]
```

Prints: enumeration method, exts, files enumerated, indexed count, db size,
`build_id`. L1 fills `files` + `files_fts` + `meta`, and creates empty
`symbols`/`edges` tables so L2 enrichment can fill them later.

See also: `query-discipline.md` (how to query the index),
`enrichment-treesitter.md` (filling `symbols`/`edges`).
