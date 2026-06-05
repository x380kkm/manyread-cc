# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# rules
"""manyread override 规则的编写/检视 CLI,并把引擎重导出供 enrich import。

引擎本体(纯函数 apply_rules/load_rules/validate_rules_doc + 预设解析)活在
``lib.rules_engine``;本文件是顶层入口脚本,只装配 init|list|validate 子命令,并把
引擎的公共面重导出(``import rules; rules.apply_rules(...)`` 对 enrich 保持可用)。

CLI:  rules.py init|list|validate [<alias>] [--root PATH] [--rules PATH]
      (需要 DB 的 preview/apply 从 enrich 驱动;此处仅为薄 stub。)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 按约定:在调整 sys.path 之后再 import
from lib import config  # noqa: E402
# 重导出引擎公共面:enrich 以 `import rules; rules.apply_rules/load_rules` 消费
from lib.rules_engine import (  # noqa: E402,F401
    BUILTIN_PRESET_DIR,
    VALID_ACTIONS,
    apply_rules,
    load_rules,
    validate_rules_doc,
    _read_rules_file,
)


# init 子命令写出的起始 rules.json 文档
_STARTER_DOC = {
    "version": 1,
    "extends_presets": ["unreal"],
    "preset_dirs": [],
    "rules": [],
}


#### 解析出 store 共享的 rules.json 路径 [@380kkm 2026-06-05] ####
def _rules_path(args) -> Path:
    if getattr(args, "rules", None):
        return Path(args.rules)
    cfg = config.resolve_project(root=args.root, store=args.store)
    return cfg.store / "rules.json"
#### /解析 rules.json 路径 ####


#### init 子命令：写出起始 rules.json [@380kkm 2026-06-05] ####
def cmd_init(args) -> int:
    path = _rules_path(args)
    if path.exists() and not args.force:
        print(f"refusing to overwrite existing {path} (use --force)", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_STARTER_DOC, indent=2) + "\n", encoding="utf-8")
    print(f"wrote starter rules to {path}")
    print("  extends_presets: [\"unreal\"]   rules: []")
    return 0
#### /init 子命令 ####


#### list 子命令：打印合并后的有效规则 [@380kkm 2026-06-05] ####
def cmd_list(args) -> int:
    path = _rules_path(args)
    extra = [d.strip() for d in (args.preset_dir or "").split(",") if d.strip()]
    try:
        merged = load_rules(path, extra_preset_dirs=extra)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not merged:
        print(f"no effective rules (rules file: {path})")
        return 0
    print(f"effective merged rules ({len(merged)}) for {path}:")
    for r in merged:
        origin = r.get("_origin", "?")
        print(f"  [{origin}] {r.get('id')}  when={r.get('when') or {}}  action={r.get('action')}")
        doc = r.get("doc")
        if doc and args.verbose:
            print(f"      {doc}")
    return 0
#### /list 子命令 ####


#### validate 子命令：lint rules.json 并解析预设 [@380kkm 2026-06-05] ####
def cmd_validate(args) -> int:
    path = _rules_path(args)
    if not path.exists():
        print(f"no rules file at {path} (nothing to validate; that is OK)")
        return 0
    try:
        doc = _read_rules_file(path)
    except ValueError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    errs = validate_rules_doc(doc)
    if errs:
        print(f"INVALID: {path}", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    # 尝试解析预设
    extra = [d.strip() for d in (args.preset_dir or "").split(",") if d.strip()]
    try:
        load_rules(path, extra_preset_dirs=extra)
    except ValueError as exc:
        print(f"INVALID (preset resolution): {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path} ({len(doc.get('rules') or [])} project rule(s); presets resolve)")
    return 0
#### /validate 子命令 ####


#### preview/apply 的 stub：提示改用 enrich 驱动 [@380kkm 2026-06-05] ####
def _stub(name: str) -> int:
    print(
        f"`rules.py {name}` is driven from enrich (it needs the project DB). Use:\n"
        f"  uv run --python 3.12 scripts/enrich_treesitter.py <alias|--root PATH> "
        f"--rules <PATH> --rules-preview   # {name} diff\n"
        "The pure engine (apply_rules) and load_rules live here for enrich to import.",
        file=sys.stderr,
    )
    return 0
#### /preview/apply stub ####


#### CLI 入口：解析参数并分派到子命令 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rules.py",
        description="manyread enrichment override-rules: author/inspect/validate.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    #### 给子解析器追加各命令的公共参数 [@380kkm 2026-06-05] ####
    def add_common(p):
        p.add_argument("--root", default=None, help="source tree root (default: store's parent)")
        p.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
        p.add_argument("--rules", default=None,
                       help="explicit rules.json path (default <store>/rules.json)")
        p.add_argument("--preset-dir", default=None,
                       help="comma list of extra preset search dirs")
    #### /追加公共参数 ####

    p_init = sub.add_parser("init", help="write a starter <root>/.manyread/rules.json")
    add_common(p_init)
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")

    p_list = sub.add_parser("list", help="print effective merged rules (incl. preset origin)")
    add_common(p_list)
    p_list.add_argument("-v", "--verbose", action="store_true", help="also print rule docs")

    p_val = sub.add_parser("validate", help="lint a rules.json")
    add_common(p_val)

    for name in ("preview", "apply"):
        p = sub.add_parser(name, help=f"(stub) {name} is driven from enrich")
        add_common(p)

    args = parser.parse_args(argv)

    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "list":
            return cmd_list(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd in ("preview", "apply"):
            return _stub(args.cmd)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.cmd!r}")
    return 2
#### /CLI 入口 ####


if __name__ == "__main__":
    raise SystemExit(main())
