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

There are two layers, both consuming the SAME immutable Context:

  * STRUCTURAL (always on) — the syntax/shape checks (unparseable text, dangling
    wire, dup id, cycle, missing root). Registered in `STRUCTURAL_PASSES[lang]`.
  * SEMANTIC (OPTIONAL, schema-driven) — a TYPE-DICTIONARY check: is the node a
    known expression class, are its properties known, are its REQUIRED INPUT PINS
    connected. Registered in `SEMANTIC_PASSES[lang]`; it runs ONLY when a `--schema`
    JSON is supplied. Each pass is a pure `(Context) -> Iterable[Issue]` callable and
    reads `ctx.schema` (rows carry `kind` + `attrs.node_type`; the connected pins /
    present props are re-walked from `ctx.tree`, because the `uses_type` edges record
    the wired SOURCE id but NOT the pin keyword). No change to either pass list's
    callers is needed to add more passes — just append.

The semantic schema is HARVEST-READY: it mirrors what a future one-time UE
reflection export would emit (lang -> nodeType -> {classPath?, properties, pins}).
In UE delta-serialization every UPROPERTY has a CDO default (absent == default,
NEVER missing), so required-ness lives on INPUT PINS, never on properties. The
bundled `scripts/schemas/matlang.sample.json` is PARTIAL + inferred from the two
example files; bplisp/animlang semantic schemas await the harvest.

Entry: the pure function `dsl_validate(text, lang, schema=None) -> list[Issue]`
runs the lang's structural passes (and, iff `schema` is given, its semantic
passes) and returns the issues SORTED deterministically by (byte, code, message).
With `schema=None` the result is BYTE-IDENTICAL to the structural-only validator.
A thin __main__ CLI validates one file, optionally loads a `--schema`, prints the
issues and a summary, and exits nonzero iff any error-severity issue exists.

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

    The SEMANTIC layer consumes the SAME Context (rows.kind/attrs +
    edges.relation/dst_name) plus the OPTIONAL `schema` type-dictionary, so
    adding a schema pass needs no new plumbing.
    """

    lang: str
    text: str
    tree: object  # tree_sitter.Tree (for the PARSE error-node walk)
    rows: list[dict]
    edges: list[dict]
    by_local: dict  # _local -> row
    names: set  # all in-file symbol names
    schema: dict | None = None  # the per-lang node-type dictionary, or None (structural-only)

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


# --- SEMANTIC layer: schema (type-dictionary) driven check passes ------------
# These run ONLY when a schema is supplied (dsl_validate(..., schema=...)). Each
# is a pure (Context)->Iterable[Issue]; it looks up a node row's
# attrs.node_type in ctx.schema[lang] and checks unknown type / unknown property
# / missing-required-pin. The schema is HARVEST-READY (mirrors a future UE
# reflection export): lang -> nodeType -> {classPath?, properties, pins}.
def _find_node_list(node, sb, eb):
    """Locate the `list` subtree whose byte span == (sb, eb) in ctx.tree.

    The semantic pass re-walks the node's own list to recover its :keyword
    properties and which input pins are CONNECTED — neither is on the edges (the
    matlang `uses_type` edge records the wired SOURCE id but NOT the pin keyword).
    """
    if node.type == "list" and node.start_byte == sb and node.end_byte == eb:
        return node
    for c in node.children:
        r = _find_node_list(c, sb, eb)
        if r is not None:
            return r
    return None


def _matlang_node_fields(ctx: Context, row: dict) -> tuple[set[str], set[str]]:
    """Return (connected_pins, present_props) for a node/material row.

    Walks the row's `list` children IN ORDER. A child whose type is `symbol` and
    whose text starts with ':' is a keyword; its value is the IMMEDIATELY-following
    child. The (:keyword, value) pair is a PIN connection iff value is a `list`
    whose first `symbol` child is exactly `connect`; otherwise it is a PROPERTY.
    Keyword names are returned WITHOUT the leading ':' (to match schema keys).

    CRITICAL: the child iteration excludes ONLY the structural tokens '(', ')'
    and 'comment'. It must NOT filter to a symbol/list/string allowlist — numeric
    values parse as `number` nodes (e.g. ':u-tiling 2.0', ':value 0.3'), so an
    allowlist would drop them and MISALIGN the keyword/value pairing. Pure +
    deterministic (the tree is already on ctx.tree).

    DEFENSIVE PAIRING: a value-less keyword (one whose immediately-following child
    is itself another ':'-keyword, or which is the last child) is recorded as a
    PROPERTY and consumes only ITSELF — it does NOT swallow the next keyword as its
    value. This stops a malformed `(:a :b (connect ...))` from mis-pairing `:b` as
    the value of `:a` and then dropping `:b` from the pin set (which could spuriously
    fire MISSING_REQUIRED_PIN for b). Such input is malformed and not in any bundled
    example; the structural layer doesn't catch it either, so this is best-effort
    hardening on top of structural validation.
    """
    pins: set[str] = set()
    props: set[str] = set()
    n = _find_node_list(ctx.tree.root_node, row["start_byte"], row["end_byte"])
    if n is None:
        return pins, props
    src = ctx.text.encode("utf-8", "replace")
    kids = [c for c in n.children if c.type not in ("(", ")", "comment")]

    def _is_keyword(c) -> bool:
        return c.type == "symbol" and src[c.start_byte:c.end_byte].startswith(b":")

    i = 0
    while i < len(kids):
        k = kids[i]
        if _is_keyword(k):
            name = src[k.start_byte:k.end_byte].decode("utf-8", "replace")[1:]  # strip one ':'
            val = kids[i + 1] if i + 1 < len(kids) else None
            if val is None or _is_keyword(val):
                # value-less keyword: record as a property, do NOT consume the next
                # keyword as its value (advance by 1, re-examine the next child).
                props.add(name)
                i += 1
                continue
            is_connect = (val.type == "list" and any(
                c.type == "symbol" and src[c.start_byte:c.end_byte] == b"connect"
                for c in val.children))
            (pins if is_connect else props).add(name)
            i += 2
        else:
            i += 1
    return pins, props


def pass_semantic_schema(ctx: Context) -> Iterable[Issue]:
    """SEMANTIC type-dictionary check (matlang). Emits NOTHING unless a schema is
    carried on the Context (so the no-schema path stays byte-identical).

    Per node/material row, look up attrs.node_type (or 'material') in
    ctx.schema[lang] and emit:
      * UNKNOWN_NODE_TYPE (warning) — type not in the (PARTIAL) dictionary.
      * UNKNOWN_PROP (warning)      — a :keyword that is neither a known property
                                      nor a known pin name.
      * MISSING_REQUIRED_PIN (error) — a schema pin with required:true whose
                                      keyword is absent from the connected set.
    Absent OPTIONAL properties are NEVER flagged (absent == CDO default). Only
    kind=='node' (with a non-empty node_type) and kind=='material' rows are
    processed; outputs / node_type-less rows are skipped (their slot keywords
    would otherwise false-warn). Iterates rows + sorted props/pins so emission is
    deterministic; the single final sort in dsl_validate orders the combined list.
    """
    if not ctx.schema:
        return
    lang_schema = ctx.schema.get(ctx.lang)
    if not lang_schema:  # no dictionary for this lang -> emit nothing
        return
    for r in sorted(ctx.rows, key=lambda r: (r["start_byte"], r["end_byte"])):
        if r["kind"] == "node":
            nt = (r.get("attrs") or {}).get("node_type")
        elif r["kind"] == "material":
            nt = "material"
        else:
            continue
        if not nt:
            continue
        spec = lang_schema.get(nt)
        if spec is None:
            yield Issue("warning", "UNKNOWN_NODE_TYPE",
                        f"node type {nt!r} not in schema (dictionary is partial)",
                        r["start_line"], r["start_byte"])
            continue
        connected, props = _matlang_node_fields(ctx, r)
        known_props = set((spec.get("properties") or {}).keys())
        known_pins = set((spec.get("pins") or {}).keys())
        for p in sorted(props):
            if p not in known_props and p not in known_pins:
                yield Issue("warning", "UNKNOWN_PROP",
                            f"{nt}: unknown property :{p}",
                            r["start_line"], r["start_byte"])
        for pin, pspec in sorted((spec.get("pins") or {}).items()):
            if pspec.get("required") and pin not in connected:
                yield Issue("error", "MISSING_REQUIRED_PIN",
                            f"{nt} (id {r['name']}): required pin :{pin} not connected",
                            r["start_line"], r["start_byte"])


# --- the check-pass registries (the plug-in seam) ----------------------------
# Ordered, per-lang. STRUCTURAL always runs; SEMANTIC runs only when a schema is
# supplied. Each pass is pure + independent and consumes the same Context, so a
# new pass is just an APPEND here — no other code changes. Structural now; the
# matlang schema dictionary now; bplisp/animlang schemas after a UE harvest.
STRUCTURAL_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_parse, pass_matlang_required, pass_matlang_dup_id,
                pass_matlang_dangling, pass_matlang_cycle],
    "bplisp": [pass_parse, pass_bplisp_required, pass_external_warn],
    "animlang": [pass_parse, pass_animlang_required, pass_external_warn],
}

# Mirrors STRUCTURAL_PASSES. Only matlang has a sample schema today; bplisp /
# animlang await a UE reflection harvest (UFunction / anim-node signatures).
SEMANTIC_PASSES: dict[str, list[Callable[[Context], Iterable[Issue]]]] = {
    "matlang": [pass_semantic_schema],
    "bplisp": [],
    "animlang": [],
}


def load_schema(path: str) -> dict:
    """PURE semantic-schema loader: json.load + shape validation. Raises
    ValueError (with a clear message) on a malformed shape so the CLI can report a
    clean error instead of a traceback. Top-level metadata keys starting with '$'
    (e.g. '$schema_note') are allowed and ignored.

    Shape: root is an object; each non-'$' key (a lang) maps to an object;
    each nodeType maps to an object; optional 'properties' is an object; optional
    'pins' is an object whose entries are objects with an optional bool 'required'.
    """
    import json

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)  # JSONDecodeError surfaces to the CLI
    if not isinstance(data, dict):
        raise ValueError("schema root must be a JSON object (lang -> nodeType -> spec)")
    for lang, types in data.items():
        if lang.startswith("$"):  # metadata key -> ignore
            continue
        if not isinstance(types, dict):
            raise ValueError(f"schema[{lang!r}] must be an object of nodeType -> spec")
        for nt, spec in types.items():
            if not isinstance(spec, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}] must be an object")
            props = spec.get("properties", {})
            pins = spec.get("pins", {})
            if not isinstance(props, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}].properties must be an object")
            if not isinstance(pins, dict):
                raise ValueError(f"schema[{lang!r}][{nt!r}].pins must be an object")
            for pn, pv in pins.items():
                if not isinstance(pv, dict):
                    raise ValueError(
                        f"schema[{lang!r}][{nt!r}].pins[{pn!r}] must be an object")
                if "required" in pv and not isinstance(pv["required"], bool):
                    raise ValueError(
                        f"schema[{lang!r}][{nt!r}].pins[{pn!r}].required must be a bool")
    return data


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


def dsl_validate(text: str, lang: str, schema: dict | None = None) -> list[Issue]:
    """PURE pre-flight validator: run the lang's STRUCTURAL passes and, iff a
    `schema` is supplied, its SEMANTIC passes, then return the issues sorted
    deterministically by (byte, code, message).

    With schema=None the result is BYTE-IDENTICAL to the structural-only validator
    (no semantic pass is constructed or run, the same single final sort applies) —
    so every existing 2-arg caller is unaffected.

    When the parse fails, the other passes STILL run on the partial rows/edges
    (PARSE_ERROR dominates the summary); this keeps the pipeline simple and a
    file can surface both a parse error and structural issues.
    """
    if lang not in STRUCTURAL_PASSES:
        return [Issue("error", "UNKNOWN_LANG", f"no validator for language {lang!r}", 1, 0)]
    ctx = _build_context(text, lang)
    issues = [i for p in STRUCTURAL_PASSES[lang] for i in p(ctx)]
    if schema is not None:
        ctx.schema = schema  # carry the dictionary; structural passes ignore it
        issues += [i for p in SEMANTIC_PASSES.get(lang, []) for i in p(ctx)]
    issues.sort(key=lambda i: i.sort_key())  # ONE final sort -> determinism preserved
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
    ap.add_argument("--schema", default=None,
                    help="optional semantic schema JSON (a node-type dictionary); "
                         "enables the SEMANTIC layer (unknown type/prop, missing required pin). "
                         "No --schema -> structural-only.")
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

    schema = None
    if a.schema:
        try:
            schema = load_schema(a.schema)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: malformed schema {a.schema!r}: {exc}", file=sys.stderr)
            return 2

    issues = dsl_validate(text, lang, schema)
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
