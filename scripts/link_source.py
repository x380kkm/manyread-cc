# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread link-source — ASSET↔SOURCE cross-layer linker (PURE, read-only).

Given a DSL **asset** store (e.g. a matlang material), a **code** store (engine
C++), and the type-dictionary **schema** (nodeType -> classPath), resolve each DSL
node to the C++ CLASS that implements it and report
``node -> {source class symbol, file:line, confidence}``. This bridges the asset
graph to the indexed source so a reader can jump from a material node to its
``UMaterialExpression`` C++ class.

Mechanism (REUSE, not reinvent):
* Both input stores are opened through ``manyscan.lib.stores.Store`` which connects
  sqlite READ-ONLY (``file:...?mode=ro``). Any write would raise — PURITY is
  guaranteed; neither input store is ever mutated.
* Per DSL node: ``node_type`` (from ``symbols.attrs.node_type``, or the row KIND for
  the ``material`` root) -> ``schema[lang][node_type].classPath`` -> ReflectedName
  (the part after the last ``.``) -> by-name lookup in the code store across the
  fixed prefix set ``["", "U", "A", "F"]`` (UE convention) over class/struct symbols.
* The confidence model MIRRORS ``manyscan.lib.boundary.resolve_target``:
  0 candidates -> ``unresolved``; exactly 1 -> ``unique``; N>1 -> ``ambiguous(N)``
  (ALL candidates listed, NEVER silently picked). A 4th REPORT-ONLY bucket
  ``no-classPath`` covers nodeTypes absent from the schema.

NOTHING in enrich_treesitter.py / dsl_validate.py is changed; ``load_schema`` is
re-implemented locally (stdlib-only) so importing this module never drags in
tree-sitter. Output is deterministic: DSL rows in a fixed sort, prefix variants in
a fixed order, candidates sorted ``(path, id)`` — two runs are byte-identical.

CLI::

    uv run --python 3.12 scripts/link_source.py \
        --dsl-store <asset store> --code-store <c++ store> \
        --schema scripts/schemas/matlang.sample.json [--lang matlang] [--json]

Exit 0 on success; 2 on a bad store path or malformed schema.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# Load manyscan's read-only store layer (scripts/manyscan/lib/stores.py) DIRECTLY
# by file path under a UNIQUE module alias, NOT via ``from lib import stores``.
#
# Why not the bare ``lib`` name: manyread's OWN package is scripts/lib, and
# dsl_validate -> enrich_treesitter does ``from lib import config`` which binds
# ``sys.modules['lib']`` to scripts/lib (no ``stores`` submodule). When this module
# shares a Python session with that import path (e.g. the combined core test suite),
# a ``from lib import stores`` here would resolve against the already-cached
# scripts/lib package and raise ImportError. stores.py itself does NOT do
# ``from lib import ...`` (it loads manyread's lib by file path under aliased names),
# so loading it directly by path under a private alias sidesteps the collision
# entirely and keeps this module PURE (stdlib + stores' read-only sqlite only).
def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec (mirrors stores._load_module)
    spec.loader.exec_module(mod)
    return mod


_MANYSCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manyscan")
stores = _load_module("manyscan_stores", os.path.join(_MANYSCAN_DIR, "lib", "stores.py"))

# UE classes are emitted kind='class'; F-prefixed reflected types are structs.
CLASS_KINDS = {"class", "struct"}
# Try the bare ReflectedName, then the U/A/F prefix variants (fixed order).
PREFIXES = ("", "U", "A", "F")


# --- local, stdlib-only schema loader (a copy of dsl_validate.load_schema) -----
def load_schema(path: str) -> dict:
    """PURE schema loader: json.load + shape validation. Raises ValueError on a
    malformed shape so the CLI reports a clean error. Top-level metadata keys
    starting with '$' are allowed and ignored.

    Re-implemented here (rather than importing dsl_validate.load_schema) because
    dsl_validate imports enrich_treesitter at module load, which imports
    tree-sitter — a hard dependency we must not require for a read-only linker.

    Shape: root is an object; each non-'$' key (a lang) maps to nodeType -> spec
    object; optional 'properties'/'pins' are objects.
    """
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
    return data


# --- resolution core -----------------------------------------------------------
def reflected_name(class_path: str) -> str | None:
    """``/Script/Engine.MaterialExpressionMultiply`` -> ``MaterialExpressionMultiply``."""
    if not class_path or "." not in class_path:
        return None
    return class_path.rsplit(".", 1)[-1]


def _norm(path: str) -> str:
    """Normalize a stored file path for output (backslashes -> '/') so reports are
    identical across OSes. files.path is stored with the OS separator at index time."""
    return (path or "").replace("\\", "/")


def resolve_class(code: "stores.Store", reflected: str, code_lang: str = "cpp") -> dict:
    """Resolve a ReflectedName to code-store class/struct candidates.

    UNION candidates across ALL prefix variants (de-dup by symbol id), sort
    ``(path, id)``, then count — variant-ORDER-INDEPENDENT and deterministic; a
    genuine cross-prefix collision surfaces as ``ambiguous`` rather than being
    masked by stopping at the first variant.

    Candidates are restricted to ``code_lang`` (default 'cpp'). The C++ classes are
    the only meaningful resolution target; without this filter a class/struct-kind
    symbol from a DIFFERENT lang in the code store that happened to share a
    ReflectedName (e.g. a DSL 'material' root if both stores were merged) would be
    counted as a candidate and could flip a 'unique' resolution to 'ambiguous'.
    ``symbols_named`` has no lang filter (it is shared boundary infra), so the lang
    cut is applied here. Pass ``code_lang=None`` to resolve across ALL langs.

    Returns {"confidence": "unique"|"ambiguous"|"unresolved", "cands": [Row, ...]}.
    """
    seen: set[int] = set()
    cands: list = []
    for prefix in PREFIXES:
        for row in code.symbols_named(prefix + reflected, kinds=CLASS_KINDS):
            if code_lang is not None and row["lang"] != code_lang:
                continue
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            cands.append(row)
    cands.sort(key=lambda r: (_norm(r["path"]), r["id"]))
    if not cands:
        return {"confidence": "unresolved", "cands": []}
    if len(cands) == 1:
        return {"confidence": "unique", "cands": cands}
    return {"confidence": "ambiguous", "cands": cands}  # NEVER pick one


def dsl_nodes(dsl: "stores.Store", lang: str):
    """Yield ``(row, lookup_key)`` for every DSL node/material symbol, sorted.

    lookup_key = attrs.node_type if present, else the row KIND for kind=='material'
    (the material ROOT has attrs=={} but the schema carries a 'material' entry).
    kind=='outputs' (a pure container) is excluded by the WHERE clause.
    """
    rows = dsl.conn.execute(
        "SELECT s.id, s.name, s.kind, f.path, s.start_line, s.attrs "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.lang = ? AND s.kind IN ('node', 'material') "
        "ORDER BY f.path, s.start_line, s.start_byte, s.id",
        (lang,),
    ).fetchall()
    for r in rows:
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        key = attrs.get("node_type") or (r["kind"] if r["kind"] == "material" else None)
        yield r, key


def link(dsl_store: str, code_store: str, schema_path: str, lang: str = "matlang",
         code_lang: str | None = "cpp") -> dict:
    """Build the deterministic link report (PURE, read-only). May raise
    ValueError / FileNotFoundError (bad schema / store) for the CLI to map to exit 2.

    ``code_lang`` restricts code-store candidates to that lang ('cpp' by default);
    pass None to resolve class/struct symbols across every lang in the code store.
    """
    schema = load_schema(schema_path)
    types = schema.get(lang, {})
    nodes: list[dict] = []

    dsl_info = stores.resolve(store=dsl_store)
    code_info = stores.resolve(store=code_store)
    with stores.Store(dsl_info.db_path) as dsl, stores.Store(code_info.db_path) as code:
        for r, key in dsl_nodes(dsl, lang):
            class_path = (types.get(key) or {}).get("classPath") if key else None
            entry: dict = {
                "node_id": r["id"],
                "node_name": r["name"],
                "node_type": key,
                "node_loc": f'{_norm(r["path"])}:{r["start_line"]}',
                "classPath": class_path,
                "status": "no-classPath",
                "resolved": None,
            }
            if class_path:
                rn = reflected_name(class_path)
                res = (resolve_class(code, rn, code_lang) if rn
                       else {"confidence": "unresolved", "cands": []})
                cands = res["cands"]
                if res["confidence"] == "unique":
                    c = cands[0]
                    entry["status"] = "resolved-unique"
                    entry["resolved"] = {
                        "symbol_name": c["name"],
                        "loc": f'{_norm(c["path"])}:{c["start_line"]}',
                        "confidence": "unique",
                    }
                elif res["confidence"] == "ambiguous":
                    entry["status"] = "resolved-ambiguous"
                    entry["resolved"] = {
                        "confidence": "ambiguous",
                        "ambiguity": len(cands),
                        "candidates": [
                            f'{_norm(c["path"])}:{c["start_line"]}' for c in cands
                        ],
                    }
                else:
                    entry["status"] = "unresolved"
            nodes.append(entry)

    summary = {
        "resolved_unique": 0,
        "resolved_ambiguous": 0,
        "unresolved": 0,
        "no_class_path": 0,
        "total": len(nodes),
    }
    bucket = {
        "resolved-unique": "resolved_unique",
        "resolved-ambiguous": "resolved_ambiguous",
        "unresolved": "unresolved",
        "no-classPath": "no_class_path",
    }
    for e in nodes:
        summary[bucket[e["status"]]] += 1
    return {
        "lang": lang,
        # Provenance paths are normalized (backslash -> '/') so the WHOLE report —
        # not just the load-bearing resolution locs — is byte-identical across OSes.
        "dsl_store": _norm(str(dsl_info.db_path)),
        "code_store": _norm(str(code_info.db_path)),
        "schema": _norm(str(schema_path)),
        "nodes": nodes,
        "summary": summary,
    }


# --- text rendering ------------------------------------------------------------
def render_text(rep: dict) -> str:
    lines: list[str] = []
    lines.append(f'# link-source  lang={rep["lang"]}')
    lines.append(f'#   dsl  : {rep["dsl_store"]}')
    lines.append(f'#   code : {rep["code_store"]}')
    lines.append(f'#   schema: {rep["schema"]}')
    lines.append("")
    for e in rep["nodes"]:
        name = e["node_name"] or "-"
        nt = e["node_type"] or "-"
        line = f'{name:<16} {nt:<20} {e["status"]:<20} {e["classPath"] or ""}'
        res = e["resolved"]
        if e["status"] == "resolved-unique" and res:
            line += f'  -> {res["symbol_name"]} @ {res["loc"]}'
        elif e["status"] == "resolved-ambiguous" and res:
            line += f'  -> AMBIGUOUS({res["ambiguity"]}): ' + ", ".join(res["candidates"])
        lines.append(line)
    s = rep["summary"]
    lines.append("")
    lines.append(
        f'resolved-unique={s["resolved_unique"]} '
        f'resolved-ambiguous={s["resolved_ambiguous"]} '
        f'unresolved={s["unresolved"]} '
        f'no-classPath={s["no_class_path"]} '
        f'total={s["total"]}'
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="link_source.py",
        description="ASSET->SOURCE cross-layer linker: resolve DSL nodes to C++ classes.",
    )
    ap.add_argument("--dsl-store", required=True, help="DSL asset store dir / source.db / hub alias")
    ap.add_argument("--code-store", required=True, help="C++ code store dir / source.db / hub alias")
    ap.add_argument("--schema", required=True, help="type-dictionary JSON (nodeType -> classPath)")
    ap.add_argument("--lang", default="matlang", help="DSL lang to link (default: matlang)")
    ap.add_argument(
        "--code-lang", default="cpp",
        help="restrict code-store candidates to this lang (default: cpp; "
        "pass 'any' to resolve across all langs)",
    )
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit machine JSON")
    args = ap.parse_args(argv)

    code_lang = None if args.code_lang == "any" else args.code_lang
    try:
        rep = link(args.dsl_store, args.code_store, args.schema, args.lang, code_lang)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        # Normalize any embedded path in the diagnostic for cross-OS consistency.
        print(f"error: {_norm(str(exc))}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
