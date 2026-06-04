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


#### 清空并重建所有 ext 命中所选语言的文件的 symbols/edges [@380kkm 2026-06-05] ####
def enrich(cfg: config.ProjectConfig, langs: list[str], do_refs: bool,
           rules_path: str | None = None, no_rules: bool = False,
           preview: bool = False) -> dict:
    """清空并重新填充每个扩展名映射到所选语言的文件的 symbols/edges。

    在原始 tree-sitter 提取之后、写入之前，把项目 override 规则（spec 第 16 节）
    作为一趟纯变换施加上去。当 preview=True 时只计算变换并收集 before/after 差异，
    但绝不写入数据库（既有的 symbols/edges 原样保留）。
    """
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        raise SystemError(f"no index db at {db_path} — run index_build.py first")

    merged_rules, rules_file = _resolve_merged_rules(cfg, rules_path, no_rules)

    #### 解析 c 系语言的 parse 前宏剥离配置（manyread.json macro_strip；缺省即开启） [@380kkm 2026-06-05] ####
    # 只解析一次，逐文件传给 _extract_file（仅 cpp 路径会消费它）
    macro_strip = config.load_macro_strip(cfg.store)

    conn = db.connect(db_path)
    try:
        # 确保 symbols/edges/meta 存在并完成迁移（幂等）
        db.init_schema(conn)

        if not preview:
            #### 清除既有 enrich 结果以做幂等全量重建 [@380kkm 2026-06-05] ####
            # preview 模式不动数据库，所以带规则重跑才是唯一写入路径
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM symbols")
            conn.commit()
            #### /清除既有 enrich 结果 ####

        # 按需构建 parser，每种实际出现的语言只建一次
        parsers: dict[str, Parser] = {}
        per_lang_sym: dict[str, int] = {}
        per_lang_edge: dict[str, int] = {}
        n_files = 0
        n_errors = 0
        diff_lines: list[str] = []

        query_specs = _load_query_specs(getattr(cfg, "root", None))
        # lang -> 已编译的 Query（或 None）
        query_objs: dict[str, object] = {}

        #### 逐文件提取符号与边，施加规则后写入或预览 [@380kkm 2026-06-05] ####
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
                    # 标记为失败，后续跳过该语言的文件
                    parsers[lang] = None
            parser = parsers.get(lang)
            if parser is None:
                continue
            # 每种语言只编译一次 .scm 查询
            if lang in query_specs and lang not in query_objs:
                try:
                    query_objs[lang] = Query(_load_language(lang), query_specs[lang])
                except Exception as exc:  # noqa: BLE001 - a bad query must not abort enrichment
                    print(f"warning: bad dependency query for {lang}: {exc}", file=sys.stderr)
                    query_objs[lang] = None
            try:
                raw_rows, raw_edges = _extract_file(file_id, content, lang, parser, do_refs,
                                                    query_objs.get(lang), macro_strip)
                # override 规则变换（纯函数；merged_rules == [] 时为恒等）
                new_rows, new_edges, _prov = rules.apply_rules(
                    raw_rows, raw_edges, {file_id: content}, merged_rules,
                )
                if preview:
                    if merged_rules:
                        diff_lines.extend(_preview_diff(raw_rows, new_rows, path))
                    # preview 模式不写入
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
        #### /逐文件提取符号与边 ####

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
#### /清空并重建所有文件的 symbols/edges ####


#### 命令行入口：解析参数、选定语言、跑 enrich 并打印统计 [@380kkm 2026-06-05] ####
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

    # 选定语言：显式 --langs 优先；否则配置中的语言；否则全部
    if args.langs:
        requested = [s.strip().lower() for s in args.langs.split(",") if s.strip()]
    elif cfg.languages:
        requested = [s.lower() for s in cfg.languages]
    else:
        requested = list(SUPPORTED_LANGS)
    # 只保留 v1 实际可解析的语言
    langs = [l for l in requested if l in SUPPORTED_LANGS]
    if not langs:
        langs = list(SUPPORTED_LANGS)
    # typescript 与 tsx 成对（同一 walker、不同 grammar 方言）；
    # 请求其一就一并拉入另一个，使 .ts 与 .tsx 都被覆盖
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
    # rules.json 损坏 / preset 缺失
    except ValueError as exc:
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
#### /命令行入口 ####
