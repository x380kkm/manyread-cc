# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread — pre-flight STRUCTURAL validator for the UE asset DSLs.

Checks an AI- or human-authored DSL FILE (matlang / bplisp / animlang) for
STRUCTURAL validity BEFORE the expensive + fragile UE import: it catches the
errors that would otherwise blow up the importer (unparseable text, a wire that
targets nothing, a duplicate node id, a cycle in a DAG, a missing required root)
in milliseconds, fully OFFLINE — no UE, no network, no index db.

This is the SYNTAX / STRUCTURAL layer only. A future SEMANTIC layer (a schema /
type dictionary: valid node classes, pin existence, type compatibility, CDO
defaults — which needs a one-time UE export) PLUGS IN as additional check passes:
append a `(Context) -> Iterable[Issue]` callable to `STRUCTURAL_PASSES[lang]` and
it consumes the SAME immutable Context (rows already carry `kind` + `attrs`,
edges carry `relation` + `dst_name`). No change to `dsl_validate` is needed.

Entry: the pure function `dsl_validate(text, lang) -> list[Issue]` runs the
lang's ordered check passes and returns the issues SORTED deterministically by
(byte, code, message). A thin __main__ CLI validates one file, prints the issues
and a summary, and exits nonzero iff any error-severity issue exists.

REUSE: there is ONE parse path and ONE set of .scm captures — the validator
imports `enrich_treesitter` and calls its in-memory helpers `_load_language`,
`_load_query_specs`, `Query` and `_extract_file(file_id, content, lang, parser,
do_refs, query) -> (rows, edges)`. `_extract_file` writes NOTHING to a DB; the
matlang `(connect $id)` wire arrives as a `uses_type` edge with `dst_local=None`
(in-file id resolution happens only at DB insert, which the validator never
calls), so the validator does its OWN name-set resolution. Cycle detection
reuses manyscan's `graph.scc` + the `analyze.cycles` self-loop filter, loaded by
EXPLICIT FILE PATH under a private module name to avoid the `lib` package-name
collision between scripts/lib (enrich) and scripts/manyscan/lib (analyze/graph).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)  # scripts/ — same as scripts/tests/test_enrich_query.py
import enrich_treesitter as E  # noqa: E402  reuse _extract_file / _load_* / Query / Parser


# --- reuse manyscan graph.scc WITHOUT pulling its `lib` onto sys.path ---------
# analyze.py hard-codes `from lib import graph` while enrich does `from lib import
# config, db`; scripts/lib and scripts/manyscan/lib are BOTH the package name
# `lib` and collide in one process, so `from lib import analyze` raises after
# enrich loads. graph.py imports nothing from lib, so a file-path load is safe —
# but the private module MUST be registered in sys.modules BEFORE exec_module or
# the @dataclass introspection raises NoneType.__dict__.
def _load_ms_graph():
    p = os.path.join(_HERE, "manyscan", "lib", "graph.py")
    spec = importlib.util.spec_from_file_location("_dsl_ms_graph", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_dsl_ms_graph"] = m  # register BEFORE exec (dataclass introspection)
    spec.loader.exec_module(m)
    return m


_G = _load_ms_graph()


def _cycles(g):
    """== manyscan analyze.cycles (graph.scc + self-loop filter), inlined."""
    self_loops = {e.src for e in g.edges if e.src == e.dst}
    return [c for c in _G.scc(g) if len(c) > 1 or (len(c) == 1 and c[0] in self_loops)]


# --- the Issue + Context contract --------------------------------------------
@dataclass(frozen=True)
class Issue:
    """One structural finding. Frozen -> hashable/comparable (determinism test)."""

    severity: str  # 'error' | 'warning'
    code: str  # stable SEMANTIC string (PARSE_ERROR, DANGLING_WIRE, ...)
    message: str
    line: int  # 1-based
    byte: int  # 0-based byte offset

    def sort_key(self):
        return (self.byte, self.code, self.message)


@dataclass
class Context:
    """Immutable-by-convention bundle built ONCE, shared by every check pass.

    The future SEMANTIC layer consumes the SAME Context (rows.kind/attrs +
    edges.relation/dst_name), so adding a schema pass needs no new plumbing.
    """

    lang: str
    text: str
    tree: object  # tree_sitter.Tree (for the PARSE error-node walk)
    rows: list[dict]
    edges: list[dict]
    by_local: dict  # _local -> row
    names: set  # all in-file symbol names

    def _row_loc(self, local):
        """edges carry no byte -> attribute an edge issue to its source row."""
        r = self.by_local.get(local)
        return (r["start_line"], r["start_byte"]) if r else (1, 0)


# --- CHECK PASSES: each is a pure (Context) -> Iterable[Issue] ----------------
# Severity rule (the single highest-risk correctness decision):
#   ERROR   = a reference the DSL contract says MUST resolve IN-FILE and does not
#             (matlang (connect $id) dangling, plus DUP_ID / CYCLE / required-form
#             for all DSLs, and PARSE_ERROR).
#   WARNING = an unresolved dep that is LEGITIMATELY external (bplisp binds/calls/
#             casts, animlang ref) — these resolve against the engine/schema in the
#             future SemanticPass; flagging them as errors now would false-positive
#             on every valid file (the existing enrich tests assert they stay
#             unresolved by design).
def pass_parse(ctx: Context) -> Iterable[Issue]:
    """PARSE_ERROR: tree-sitter rejected the file (any ERROR / MISSING node)."""
    if not ctx.tree.root_node.has_error:
        return []
    out: list[Issue] = []
    stack = [ctx.tree.root_node]
    while stack:
        n = stack.pop()
        if n.is_error or n.is_missing:
            out.append(Issue(
                "error", "PARSE_ERROR",
                f"tree-sitter {'missing' if n.is_missing else 'error'} node",
                n.start_point[0] + 1, n.start_byte))
        stack.extend(n.children)
    # has_error True but no ERROR/MISSING surfaced (rare): one generic blocker.
    return out or [Issue("error", "PARSE_ERROR", "grammar rejected file", 1, 0)]


def pass_matlang_required(ctx: Context) -> Iterable[Issue]:
    """matlang required form: a (material ...) root + an (outputs ...) block."""
    if not any(r["kind"] == "material" for r in ctx.rows):
        yield Issue("error", "MATLANG_NO_MATERIAL", "no (material ...) root", 1, 0)
    if not any(r["kind"] == "outputs" for r in ctx.rows):
        yield Issue("error", "MATLANG_NO_OUTPUTS", "no (outputs ...) block", 1, 0)


def pass_matlang_dup_id(ctx: Context) -> Iterable[Issue]:
    """matlang $id uniqueness: report each 2nd+ occurrence of a node name."""
    counts = Counter(r["name"] for r in ctx.rows if r["kind"] == "node")
    seen: set[str] = set()
    for r in sorted((r for r in ctx.rows if r["kind"] == "node"),
                    key=lambda r: (r["start_byte"], r["end_byte"])):
        if counts[r["name"]] > 1:
            if r["name"] in seen:  # the 2nd+ occurrence is the duplicate
                yield Issue("error", "DUP_ID",
                            f"duplicate node id {r['name']}",
                            r["start_line"], r["start_byte"])
            seen.add(r["name"])


def pass_matlang_dangling(ctx: Context) -> Iterable[Issue]:
    """matlang dangling wire: a (connect $id) whose $id is not defined in-file.

    matlang wires are emitted as relation 'uses_type' (deliberate reuse for the
    manyscan boundary gate) with dst_local=None, so resolve by name here.
    """
    node_names = {r["name"] for r in ctx.rows if r["kind"] == "node"}
    for e in ctx.edges:
        if e["relation"] != "uses_type":
            continue
        dst = e["dst_name"]
        # only $id wires resolve in-file; guard so a future non-$ dep can't be
        # mis-flagged. A $id is dangling iff it matches no in-file node symbol.
        if dst.startswith("$") and dst not in node_names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("error", "DANGLING_WIRE",
                        f"(connect {dst}) targets undefined id", ln, by)


def pass_matlang_cycle(ctx: Context) -> Iterable[Issue]:
    """matlang is a DAG: any cycle in the node<->node wire graph is an error.

    The graph intentionally includes ONLY node<->node 'uses_type' edges (the
    'outputs'/'material' literals are excluded) so they can never be false cycle
    participants. Built in sorted node order so graph.scc output is deterministic.

    SKIP when any $id is duplicated: this pass collapses the wire graph BY NAME,
    which is only sound under the in-file uniqueness invariant that DUP_ID enforces.
    With a duplicate id the two physical nodes fuse into one graph node, turning a
    legitimate `(connect $thatid)` into a phantom self-loop CYCLE on top of the
    (correct) DUP_ID — a false positive. DUP_ID already flags the real problem; the
    cycle graph is ambiguous until the user makes ids unique, so emit nothing here.
    """
    counts = Counter(r["name"] for r in ctx.rows if r["kind"] == "node")
    if any(c > 1 for c in counts.values()):
        return
    node_names = set(counts)
    g = _G.Graph()
    for n in sorted(node_names):
        g.add_node(_G.Node(id=n, kind="node", label=n))
    for e in ctx.edges:
        if e["relation"] != "uses_type":
            continue
        s = (ctx.by_local.get(e["src_local"]) or {}).get("name")
        d = e["dst_name"]
        if s in node_names and d in node_names:
            g.add_edge(_G.Edge(src=s, dst=d, relation="wire"))
    for comp in _cycles(g):
        members = sorted(comp)
        loc = min((r["start_line"], r["start_byte"]) for r in ctx.rows
                  if r["name"] in comp)
        yield Issue("error", "CYCLE",
                    "wire cycle: " + " -> ".join(members), loc[0], loc[1])


def pass_bplisp_required(ctx: Context) -> Iterable[Issue]:
    """bplisp required form: at least one (event|func|function|macro ...) graph
    root — emitted as a row of kind 'graph'."""
    if not any(r["kind"] == "graph" for r in ctx.rows):
        yield Issue("error", "BPLISP_NO_GRAPH",
                    "no (event|func|function|macro ...) graph root", 1, 0)


def pass_animlang_required(ctx: Context) -> Iterable[Issue]:
    """animlang required form: a top-level graph root.

    animlang.scm emits NO 'graph' kind — the root (anim-blueprint in samples) is
    the sole top-level kind=='node' (parent_local is None). A naive shared
    kind=='graph' check would wrongly fail EVERY animlang file.
    """
    if not any(r["kind"] == "node" and r["parent_local"] is None for r in ctx.rows):
        yield Issue("error", "ANIMLANG_NO_GRAPH", "no top-level anim graph root", 1, 0)


def pass_external_warn(ctx: Context) -> Iterable[Issue]:
    """Legit-external unresolved deps -> WARNING (the SemanticPass resolves these
    against the engine/schema later), NEVER error."""
    external_rels = {
        "matlang": (),  # matlang wires resolve in-file (handled by pass_matlang_dangling)
        "bplisp": ("binds", "calls", "casts"),
        "animlang": ("ref",),
    }
    rels = external_rels.get(ctx.lang, ())
    for e in ctx.edges:
        if e["relation"] not in rels:
            continue
        # animlang (ref "Title") edges keep their quotes (the reused _query_edges
        # runs _simplify_dep, not _dsl_name), while ctx.names stores UNQUOTED
        # symbol names. Normalize so an in-file ref resolves and only genuinely
        # external names warn (otherwise EVERY ref would false-warn even when it
        # names an in-file node — a latent bug the future SemanticPass would inherit).
        dst = e["dst_name"].strip('"')
        if dst not in ctx.names:
            ln, by = ctx._row_loc(e["src_local"])
            yield Issue("warning", "UNRESOLVED_REF",
                        f"{e['relation']} target {dst} not defined in-file "
                        "(resolves against engine/schema later)", ln, by)


# --- the check-pass registry (the plug-in seam) ------------------------------
# Ordered, per-lang. The future SEMANTIC layer simply APPENDS passes here; each
# pass is pure + independent and consumes the same Context, so no other code
# changes. Structural now; schema later.
STRUCTURAL_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_parse, pass_matlang_required, pass_matlang_dup_id,
                pass_matlang_dangling, pass_matlang_cycle],
    "bplisp": [pass_parse, pass_bplisp_required, pass_external_warn],
    "animlang": [pass_parse, pass_animlang_required, pass_external_warn],
}


# --- Context construction (ONE parse path; in-memory; no DB) -----------------
def _build_context(text: str, lang: str) -> Context:
    L = E._load_language(lang)  # 'scheme' grammar for all three DSLs
    parser = E.Parser(L)
    tree = parser.parse(text.encode("utf-8", "replace"))
    specs = E._load_query_specs(None)  # built-in scripts/queries/<lang>.scm only (pure)
    query = E.Query(L, specs[lang]) if lang in specs else None
    # _extract_file re-parses internally (takes content, not a tree); the two
    # sub-ms parses are accepted to avoid changing enrich's signature.
    rows, edges = E._extract_file(0, text, lang, parser, False, query)
    return Context(lang, text, tree, rows, edges,
                   {r["_local"]: r for r in rows}, {r["name"] for r in rows})


def dsl_validate(text: str, lang: str) -> list[Issue]:
    """PURE pre-flight structural validator: run the lang's check passes and
    return the issues sorted deterministically by (byte, code, message).

    When the parse fails, the other passes STILL run on the partial rows/edges
    (PARSE_ERROR dominates the summary); this keeps the pipeline simple and a
    file can surface both a parse error and structural issues.
    """
    if lang not in STRUCTURAL_PASSES:
        return [Issue("error", "UNKNOWN_LANG", f"no validator for language {lang!r}", 1, 0)]
    ctx = _build_context(text, lang)
    issues = [i for p in STRUCTURAL_PASSES[lang] for i in p(ctx)]
    issues.sort(key=lambda i: i.sort_key())
    return issues


# --- thin CLI ----------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        prog="dsl_validate.py",
        description="Pre-flight STRUCTURAL validator for UE asset DSLs "
                    "(matlang/bplisp/animlang). Pure + offline; nonzero exit on error.")
    ap.add_argument("file", help="the DSL file to validate")
    ap.add_argument("--lang", default=None,
                    help="matlang|bplisp|animlang (default: auto-detect by extension)")
    ap.add_argument("--json", action="store_true", help="emit issues as a JSON list")
    a = ap.parse_args(argv)

    lang = a.lang or E.LANG_FOR_EXT.get(os.path.splitext(a.file)[1].lower())
    if lang not in STRUCTURAL_PASSES:
        print(f"error: unknown DSL for {a.file!r} (use --lang matlang|bplisp|animlang)",
              file=sys.stderr)
        return 2
    try:
        with open(a.file, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: cannot read {a.file!r}: {exc}", file=sys.stderr)
        return 2

    issues = dsl_validate(text, lang)
    if a.json:
        print(json.dumps([asdict(i) for i in issues], indent=2))
    else:
        for i in issues:
            print(f"{i.severity.upper():7} {i.code:18} L{i.line} b{i.byte}: {i.message}")
        n_err = sum(i.severity == "error" for i in issues)
        n_warn = sum(i.severity == "warning" for i in issues)
        if not issues:
            print(f"OK      {a.file} ({lang}): no structural issues")
        else:
            print(f"-- {n_err} error(s), {n_warn} warning(s)")
    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
