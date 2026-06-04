from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from lib import config, db
import rules

from enrich.langreg import (LANG_FOR_EXT, SUPPORTED_LANGS, Parser, Query,
                            _load_language)
from enrich.query import _load_query_specs
from enrich.extract import _extract_file
from enrich.dbwrite import _insert_file
from enrich.rules_glue import _preview_diff, _resolve_merged_rules


def enrich(cfg: config.ProjectConfig, langs: list[str], do_refs: bool,
           rules_path: str | None = None, no_rules: bool = False,
           preview: bool = False) -> dict:
    """Clear and refill symbols/edges for every file whose ext maps to a chosen lang.

    After raw tree-sitter extraction, applies the project override rules (spec
    section 16) as a pure transform pass BEFORE inserting. With preview=True the
    transform is computed and a before/after diff is collected, but NOTHING is
    written to the DB (existing symbols/edges are left untouched).
    """
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        raise SystemError(f"no index db at {db_path} — run index_build.py first")

    merged_rules, rules_file = _resolve_merged_rules(cfg, rules_path, no_rules)

    # c-family pre-parse macro-strip config (manyread.json macro_strip; ABSENT => ON).
    # Resolved once; passed per-file to _extract_file (only the cpp path consumes it).
    macro_strip = config.load_macro_strip(cfg.store)

    conn = db.connect(db_path)
    try:
        db.init_schema(conn)  # ensure symbols/edges/meta exist + migrate (idempotent).

        if not preview:
            # Idempotent full rebuild: clear prior enrichment. (Preview leaves the
            # DB untouched so re-running with rules later is the only write path.)
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM symbols")
            conn.commit()

        # Build parsers lazily, once per language actually present.
        parsers: dict[str, Parser] = {}
        per_lang_sym: dict[str, int] = {}
        per_lang_edge: dict[str, int] = {}
        n_files = 0
        n_errors = 0
        diff_lines: list[str] = []

        query_specs = _load_query_specs(getattr(cfg, "root", None))
        query_objs: dict[str, object] = {}            # lang -> compiled Query (or None)

        rows = conn.execute("SELECT id, path, ext, content FROM files").fetchall()
        for file_id, path, ext, content in rows:
            lang = LANG_FOR_EXT.get((ext or "").lower())
            if lang is None or lang not in langs:
                continue
            if content is None:
                continue
            if lang not in parsers:
                try:
                    parsers[lang] = Parser(_load_language(lang))
                except Exception as exc:  # noqa: BLE001 - grammar load failure is per-lang
                    print(f"warning: could not load {lang} grammar: {exc}", file=sys.stderr)
                    parsers[lang] = None  # mark as failed so we skip its files
            parser = parsers.get(lang)
            if parser is None:
                continue
            if lang in query_specs and lang not in query_objs:   # compile the .scm once per lang
                try:
                    query_objs[lang] = Query(_load_language(lang), query_specs[lang])
                except Exception as exc:  # noqa: BLE001 - a bad query must not abort enrichment
                    print(f"warning: bad dependency query for {lang}: {exc}", file=sys.stderr)
                    query_objs[lang] = None
            try:
                raw_rows, raw_edges = _extract_file(file_id, content, lang, parser, do_refs,
                                                    query_objs.get(lang), macro_strip)
                # Override-rules transform (pure; identity when merged_rules == []).
                new_rows, new_edges, _prov = rules.apply_rules(
                    raw_rows, raw_edges, {file_id: content}, merged_rules,
                )
                if preview:
                    if merged_rules:
                        diff_lines.extend(_preview_diff(raw_rows, new_rows, path))
                    # do NOT write in preview mode.
                    per_lang_sym[lang] = per_lang_sym.get(lang, 0) + len(new_rows)
                    per_lang_edge[lang] = per_lang_edge.get(lang, 0) + len(new_edges)
                else:
                    n_sym, n_edge = _insert_file(conn, file_id, lang, new_rows, new_edges)
                    per_lang_sym[lang] = per_lang_sym.get(lang, 0) + n_sym
                    per_lang_edge[lang] = per_lang_edge.get(lang, 0) + n_edge
                n_files += 1
            except Exception as exc:  # noqa: BLE001 - graceful per-file skip
                n_errors += 1
                print(f"warning: failed to enrich {path}: {exc}", file=sys.stderr)

        if preview:
            total_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            total_edge = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        else:
            conn.commit()
            db.set_meta(conn, "enriched_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
            db.set_meta(conn, "enrich_langs", ",".join(langs))
            db.set_meta(conn, "enrich_rules", str(rules_file) if rules_file else "")
            db.set_meta(conn, "macro_strip", json.dumps(macro_strip, sort_keys=True))
            conn.commit()
            total_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            total_edge = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        conn.close()

    return {
        "files": n_files,
        "errors": n_errors,
        "per_lang_sym": per_lang_sym,
        "per_lang_edge": per_lang_edge,
        "total_sym": total_sym,
        "total_edge": total_edge,
        "db_path": db_path,
        "rules_file": str(rules_file) if rules_file else None,
        "n_rules": len(merged_rules),
        "preview": preview,
        "diff_lines": diff_lines,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enrich_treesitter.py",
        description="manyread L2 tree-sitter symbol/edge enrichment.",
    )
    parser.add_argument("--root", default=None, help="source tree root (default: store's parent)")
    parser.add_argument("--store", default=None,
                        help="explicit manyread store dir (default: discover from cwd)")
    parser.add_argument("--langs", default=None,
                        help="comma list to restrict languages (default: config langs "
                             "intersected with supported, else all supported)")
    parser.add_argument("--refs", action="store_true",
                        help="also emit best-effort `references` edges (off by default)")
    parser.add_argument("--rules", default=None,
                        help="override-rules path (default <root>/.manyread/rules.json "
                             "if present); see /mr-rules")
    parser.add_argument("--no-rules", action="store_true",
                        help="skip the override-rules transform entirely (raw base behavior)")
    parser.add_argument("--rules-preview", action="store_true",
                        help="compute the transform and PRINT a before/after diff of "
                             "changed symbols, but do NOT write to the db")
    args = parser.parse_args(argv)

    if args.no_rules and (args.rules or args.rules_preview):
        parser.error("--no-rules cannot be combined with --rules / --rules-preview")

    try:
        cfg = config.resolve_project(root=args.root, store=args.store)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Determine languages: explicit --langs wins; else config languages; else all.
    if args.langs:
        requested = [s.strip().lower() for s in args.langs.split(",") if s.strip()]
    elif cfg.languages:
        requested = [s.lower() for s in cfg.languages]
    else:
        requested = list(SUPPORTED_LANGS)
    # Keep only languages we can actually parse in v1.
    langs = [l for l in requested if l in SUPPORTED_LANGS]
    if not langs:
        langs = list(SUPPORTED_LANGS)
    # typescript and tsx are a pair (same walker, different grammar dialect);
    # requesting one pulls in the other so .ts and .tsx are both covered.
    if "typescript" in langs and "tsx" not in langs:
        langs.append("tsx")
    if "tsx" in langs and "typescript" not in langs:
        langs.append("typescript")

    try:
        stats = enrich(cfg, langs, do_refs=args.refs, rules_path=args.rules,
                       no_rules=args.no_rules, preview=args.rules_preview)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # bad rules.json / missing preset
        print(f"error: rules: {exc}", file=sys.stderr)
        return 2

    if stats.get("preview"):
        print(f"project    : {cfg.alias}")
        print(f"db         : {stats['db_path']}  (NOT modified — preview only)")
        rf = stats.get("rules_file")
        print(f"rules      : {rf or '(none)'}  ({stats['n_rules']} merged rule(s))")
        diff = stats.get("diff_lines") or []
        if not stats["n_rules"]:
            print("preview    : no rules in effect — nothing would change.")
        elif not diff:
            print("preview    : rules in effect but no symbols would change.")
        else:
            print(f"preview    : {len(diff)} symbol change(s) the rules WOULD make:")
            for line in diff:
                print(line)
        return 0

    print(f"project    : {cfg.alias}")
    print(f"root       : {Path(cfg.root).resolve()}")
    print(f"db         : {stats['db_path']}")
    print(f"langs      : {','.join(langs)}")
    rf = stats.get("rules_file")
    if args.no_rules:
        print("rules      : (disabled via --no-rules)")
    else:
        print(f"rules      : {rf or '(none)'}  ({stats['n_rules']} merged rule(s))")
    print(f"files      : {stats['files']} (errors: {stats['errors']})")
    for lang in langs:
        s = stats["per_lang_sym"].get(lang, 0)
        e = stats["per_lang_edge"].get(lang, 0)
        print(f"  {lang:<11}: {s} symbols, {e} edges")
    print(f"symbols    : {stats['total_sym']}")
    print(f"edges      : {stats['total_edge']}")
    return 0
