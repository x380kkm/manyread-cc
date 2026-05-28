# manyread

**English** · [中文](README.zh-CN.md) · [日本語](README.ja.md)

> A re-packaging of **[SQL-ManyThing](https://github.com/IOchair/SQL-ManyThing)** (IOchair, MIT)
> as a Claude Code plugin. The core idea is theirs — read their repo first; this one only does the
> packaging. See [PROVENANCE.md](PROVENANCE.md) for what is ported / replaced / new.

Index a large source tree into local SQLite (FTS5 full-text + tree-sitter symbols/graph) so the AI
reads code cheaply — **narrow with SQL, then extract bounded slices**, instead of grep + whole-file
reads. Once installed, the agent **prefers it automatically for reading/searching** (Edit/Write are
unchanged).

## Install

**Prerequisite: [`uv`](https://docs.astral.sh/uv/)** — required. It auto-manages Python dependencies
via PEP 723 inline metadata (no manual `pip install`). Install it from the
[uv site](https://docs.astral.sh/uv/) (Windows: `winget install astral-sh.uv`).

**Install the plugin** (in Claude Code — just these two):

```text
/plugin marketplace add x380kkm/manyread-cc
/plugin install manyread@manyread
```

**Test:** in a fresh session, ask "what is this project / how does X work?" in any repo — the agent
offers to build an index, then answers via manyread (not grep + cat).

## Usage

```text
/mr-init     pick a folder (default ./manyread) + build the index
/mr-enrich   add tree-sitter symbols / call graph
/mr-query    query code (manyread becomes the default read path)
/mr-ref      create / prune / annotate a reading workspace
/mr-rules    project-scoped parser-fix rules (e.g. UE *_API macros)
```

Data lives in a visible `manyread/` folder in the repo (committable); `user/` (personal config) and
`short/` (short-term, version-tied) are gitignored — clear `short/` by hand after committing.

## License

MIT. Derived from **SQL-ManyThing** (MIT, IOchair) — <https://github.com/IOchair/SQL-ManyThing>.
See [PROVENANCE.md](PROVENANCE.md).
