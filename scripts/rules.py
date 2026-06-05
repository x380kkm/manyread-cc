# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# rules
"""manyread L2 富化的 override 规则引擎 + 预设(仅依赖标准库)。

基础的 tree-sitter 抽取是通用的,偶尔会在与具体代码库相关的写法上出错(例如 Unreal 的
``class SDFPARTICLES_API UFoo`` 会让 tree-sitter-cpp 把导出宏 ``SDFPARTICLES_API`` 当成类名)。
与其把各引擎的修正硬编码进 enrich_treesitter.py,不如让修正活在一个项目作用域、可由 agent
编辑的 override 层中,作为一道变换 pass 施加:

    tree-sitter 抽取(原始)  ->  apply_rules(...)  ->  写入 symbols/edges

没有任何规则时 -> 与当前行为完全一致(向后兼容)。引擎是纯函数(无 DB、无 IO),因此可被单元
测试,也可从 enrich 驱动。

共享契约(SHARED CONTRACT,必须与 enrich 集成精确一致)
-------------------------------------------------------
rules.json 的 schema:
  { "version": 1,
    "extends_presets": ["unreal"],
    "preset_dirs": ["<绝对或相对于 root 的目录>"],
    "rules": [
      { "id": str,
        "when": { "lang"?: str, "kind"?: str, "name_regex"?: str, "path_glob"?: str },
        "action": "rename_to_next_identifier"|"set_attr"|"drop"|"reclassify",
        "skip_token_regex"?: str,   # rename_to_next_identifier
        "set"?: {<attr>: <val>},    # rename_to_next_identifier / set_attr
        "to_kind"?: str } ]         # reclassify
  }

引擎入口(可导入 + 纯函数):
  apply_rules(rows, edges, content_by_file_id, rules) -> (rows, edges, provenance)

  rows : dict 列表,每个形如
    {"_local": int, "file_id": int, "name": str, "kind": str, "lang": str,
     "start_line": int, "end_line": int, "start_byte": int, "end_byte": int,
     "parent_local": int|None, "attrs": dict, "provenance": list[str]}
  edges: dict 列表
    {"file_id","src_local","dst_local"|None,"dst_name","relation"}
  content_by_file_id: file_id -> 整个文件文本(使 rename_to_next_identifier 能重新
    切片 content[start_byte:],在跳过一个匹配 skip_token_regex 的 token 后,取下一个
    标识符作为修正后的名字)。

  返回变换后的 rows/edges 以及一张 provenance 映射 {rule_id: [被改动的 local id]}。
  纯函数(无 DB、无 IO);当 rules == [] 时 rows 原样返回。

CLI:  rules.py init|list|validate [<alias>] [--root PATH] [--rules PATH]
      (需要 DB 的 preview/apply 从 enrich 驱动;此处仅为薄 stub。)
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 按约定:在调整 sys.path 之后再 import
from lib import config  # noqa: E402


# 内置预设所在目录:<repo>/presets/<name>.rules.json(本文件位于 <repo>/scripts/rules.py)
BUILTIN_PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"

VALID_ACTIONS = (
    "rename_to_next_identifier",
    "set_attr",
    "drop",
    "reclassify",
)

# 一个 C 标识符(也用于匹配我们要跳过的导出宏 token)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# rename 时从 start_byte 起向后重新切片的窗口长度
_SLICE_WINDOW = 256


#### 对原始抽取结果施加 override 规则(纯函数) [@380kkm 2026-06-05] ####
def apply_rules(rows, edges, content_by_file_id, rules):
    """Args:
      rows:  list[dict] 符号行。
      edges: list[dict] 边,通过 _local 引用 rows。
      content_by_file_id: dict[int, str] 每个 file_id 对应的整文件文本。
      rules: list[dict] 合并后的规则列表(预设 + 项目),按顺序。

    Returns:
      (rows, edges, provenance),其中 provenance 为 {rule_id: [被改动的 local id]}。
      当 rules == [] 时原样返回输入。
    """
    # 在副本上工作
    rows = [copy.deepcopy(r) for r in rows]
    edges = [copy.deepcopy(e) for e in edges]
    content_by_file_id = content_by_file_id or {}
    provenance: dict[str, list[int]] = {}

    if not rules:
        return rows, edges, provenance

    dropped_locals: set[int] = set()

    for rule in rules:
        rid = rule.get("id", "<unnamed>")
        action = rule.get("action")
        when = rule.get("when") or {}
        matcher = _compile_when(when)

        for row in rows:
            local = row.get("_local")
            if local in dropped_locals:
                continue
            if not matcher(row):
                continue

            touched = _apply_action(row, rule, action, content_by_file_id)
            if touched:
                if action == "drop":
                    dropped_locals.add(local)
                _mark(row, provenance, rid)

    if dropped_locals:
        rows = [r for r in rows if r.get("_local") not in dropped_locals]
        edges = [
            e for e in edges
            if e.get("src_local") not in dropped_locals
            and e.get("dst_local") not in dropped_locals
        ]

    return rows, edges, provenance
#### /对原始抽取结果施加 override 规则 ####


#### 把 when 子句编译成 行->bool 谓词 [@380kkm 2026-06-05] ####
def _compile_when(when):
    lang = when.get("lang")
    kind = when.get("kind")
    name_regex = when.get("name_regex")
    path_glob = when.get("path_glob")
    name_re = re.compile(name_regex) if name_regex else None

    #### 单行匹配谓词 [@380kkm 2026-06-05] ####
    def pred(row) -> bool:
        if lang is not None and row.get("lang") != lang:
            return False
        if kind is not None and row.get("kind") != kind:
            return False
        if name_re is not None and not name_re.fullmatch(row.get("name") or ""):
            return False
        if path_glob is not None:
            # path 可选,取不到时视为 no-op 不排除
            path = row.get("path")
            if path is not None and not fnmatch.fnmatch(path, path_glob):
                return False
        return True
    #### /单行匹配谓词 ####

    return pred
#### /把 when 子句编译成谓词 ####


#### 按规则的 action 改动一行,返回是否被改动 [@380kkm 2026-06-05] ####
def _apply_action(row, rule, action, content_by_file_id) -> bool:
    if action == "rename_to_next_identifier":
        return _action_rename(row, rule, content_by_file_id)
    if action == "set_attr":
        return _action_set_attr(row, rule)
    if action == "drop":
        # 由调用方移除该行
        return True
    if action == "reclassify":
        return _action_reclassify(row, rule)
    # 未知 action:保持该行不变
    return False
#### /按规则的 action 改动一行 ####


#### rename action：取下一个标识符作为修正名 [@380kkm 2026-06-05] ####
def _action_rename(row, rule, content_by_file_id) -> bool:
    content = content_by_file_id.get(row.get("file_id"))
    if content is None:
        return False
    start = row.get("start_byte") or 0
    window = content[start:start + _SLICE_WINDOW]

    pos = 0
    skip_re_src = rule.get("skip_token_regex")
    if skip_re_src:
        # 跳过前导空白,再跳过一个可选的、匹配 skip_token_regex 的 token
        m_ws = re.match(r"\s*", window)
        scan = m_ws.end() if m_ws else 0
        m_skip = re.compile(skip_re_src).match(window, scan)
        if m_skip:
            pos = m_skip.end()
        else:
            # 没有可跳过的;从空白之后继续
            pos = scan

    m_ident = _IDENT_RE.search(window, pos)
    if not m_ident:
        return False
    new_name = m_ident.group(0)
    if not new_name or new_name == row.get("name"):
        # 无下一个标识符或与原名相同:保持不变
        if new_name == row.get("name"):
            return False
        return False

    row["name"] = new_name
    _merge_set(row, rule.get("set"))
    return True
#### /rename action ####


#### set_attr action：把 set 合并进 row 的 attrs [@380kkm 2026-06-05] ####
def _action_set_attr(row, rule) -> bool:
    return _merge_set(row, rule.get("set"))
#### /set_attr action ####


#### reclassify action：改写 row 的 kind [@380kkm 2026-06-05] ####
def _action_reclassify(row, rule) -> bool:
    to_kind = rule.get("to_kind")
    if not to_kind or row.get("kind") == to_kind:
        return False
    row["kind"] = to_kind
    return True
#### /reclassify action ####


#### 把 set 映射合并进 row 的 attrs [@380kkm 2026-06-05] ####
def _merge_set(row, set_map) -> bool:
    if not set_map:
        return False
    attrs = row.setdefault("attrs", {})
    changed = False
    for k, v in set_map.items():
        if attrs.get(k) != v:
            attrs[k] = v
            changed = True
    return changed
#### /把 set 映射合并进 attrs ####


#### 记录某规则改动了某行的 provenance [@380kkm 2026-06-05] ####
def _mark(row, provenance, rule_id) -> None:
    prov = row.setdefault("provenance", [])
    if rule_id not in prov:
        prov.append(rule_id)
    bucket = provenance.setdefault(rule_id, [])
    local = row.get("_local")
    if local is not None and local not in bucket:
        bucket.append(local)
#### /记录 provenance ####


#### 读取并 JSON 解析一个规则/预设文件 [@380kkm 2026-06-05] ####
def _read_rules_file(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
#### /读取并 JSON 解析规则文件 ####


#### 定位 <name>.rules.json 预设文件 [@380kkm 2026-06-05] ####
def _find_preset(name: str, search_dirs: list[Path]) -> Path | None:
    fname = f"{name}.rules.json"
    for d in [BUILTIN_PRESET_DIR, *search_dirs]:
        cand = Path(d) / fname
        if cand.exists():
            return cand
    return None
#### /定位预设文件 ####


#### 递归收集 extends_presets 链上的规则 [@380kkm 2026-06-05] ####
def _collect_preset_rules(
    doc: dict,
    base_dir: Path,
    extra_preset_dirs: list[Path],
    seen: set[str],
    acc: list[dict],
) -> None:
    declared = doc.get("preset_dirs") or []
    resolved_dirs: list[Path] = []
    for d in declared:
        p = Path(d)
        if not p.is_absolute():
            p = (base_dir / p)
        resolved_dirs.append(p)
    search_dirs = [*resolved_dirs, *extra_preset_dirs]

    for name in doc.get("extends_presets") or []:
        if name in seen:
            # 跳过已见预设
            continue
        seen.add(name)
        preset_path = _find_preset(name, search_dirs)
        if preset_path is None:
            raise ValueError(
                f"preset '{name}' not found (searched: built-in dir"
                + ("".join(f", {d}" for d in search_dirs)) + ")"
            )
        preset_doc = _read_rules_file(preset_path)
        # 深度优先递归收集被扩展的预设
        _collect_preset_rules(
            preset_doc, preset_path.parent, extra_preset_dirs, seen, acc
        )
        for rule in preset_doc.get("rules") or []:
            r = dict(rule)
            r.setdefault("_origin", f"preset:{name}")
            acc.append(r)
#### /递归收集预设规则 ####


#### 加载并合并项目 rules.json 与其扩展的预设 [@380kkm 2026-06-05] ####
def load_rules(rules_path, extra_preset_dirs=None) -> list[dict]:
    """合并顺序:预设(按 extends_presets 顺序,先搜内置目录再搜任何 preset_dirs)然后是
    项目规则。同一 id 上项目规则胜出(就地替换)。

    返回的每条规则都带一个 ``_origin`` 标记("preset:<name>" 或 "project")。
    rules_path 不存在时只返回(空的)预设链,即 []。
    """
    extra_preset_dirs = [Path(d) for d in (extra_preset_dirs or [])]
    rules_path = Path(rules_path)

    if rules_path.exists():
        doc = _read_rules_file(rules_path)
        base_dir = rules_path.parent
    else:
        doc = {}
        base_dir = rules_path.parent

    # 1. 预设规则(按 extends_presets 顺序深度优先)
    preset_rules: list[dict] = []
    _collect_preset_rules(doc, base_dir, extra_preset_dirs, set(), preset_rules)

    # 2. 项目规则,打上标记
    project_rules = []
    for rule in doc.get("rules") or []:
        r = dict(rule)
        r.setdefault("_origin", "project")
        project_rules.append(r)

    # 3. 合并:同一 id 上项目规则胜出(就地替换);新 id 追加
    merged: list[dict] = list(preset_rules)
    index_by_id = {r.get("id"): i for i, r in enumerate(merged) if r.get("id")}
    for pr in project_rules:
        rid = pr.get("id")
        if rid in index_by_id:
            merged[index_by_id[rid]] = pr
        else:
            if rid:
                index_by_id[rid] = len(merged)
            merged.append(pr)
    return merged
#### /加载并合并规则 ####


#### 校验一个已解析的 rules.json 文档 [@380kkm 2026-06-05] ####
def validate_rules_doc(doc: dict) -> list[str]:
    """返回一组人类可读的错误信息(空 == 合法)。"""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["top-level value must be a JSON object"]

    version = doc.get("version")
    if version != 1:
        errs.append(f"version must be 1 (got {version!r})")

    for key in ("extends_presets", "preset_dirs"):
        val = doc.get(key)
        if val is not None and not (isinstance(val, list) and all(isinstance(x, str) for x in val)):
            errs.append(f"{key} must be a list of strings")

    rules = doc.get("rules")
    if rules is None:
        errs.append("missing 'rules' (use [] for none)")
        return errs
    if not isinstance(rules, list):
        errs.append("'rules' must be a list")
        return errs

    seen_ids: set[str] = set()
    for i, rule in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(rule, dict):
            errs.append(f"{where}: must be an object")
            continue
        rid = rule.get("id")
        if not rid or not isinstance(rid, str):
            errs.append(f"{where}: missing/invalid 'id'")
        elif rid in seen_ids:
            errs.append(f"{where}: duplicate id {rid!r}")
        else:
            seen_ids.add(rid)

        when = rule.get("when")
        if when is not None:
            if not isinstance(when, dict):
                errs.append(f"{where}: 'when' must be an object")
            else:
                for k in when:
                    if k not in ("lang", "kind", "name_regex", "path_glob"):
                        errs.append(f"{where}.when: unknown key {k!r}")
                nr = when.get("name_regex")
                if nr is not None:
                    try:
                        re.compile(nr)
                    except re.error as exc:
                        errs.append(f"{where}.when.name_regex: bad regex: {exc}")

        action = rule.get("action")
        if action not in VALID_ACTIONS:
            errs.append(f"{where}: action must be one of {VALID_ACTIONS} (got {action!r})")

        if action == "rename_to_next_identifier":
            skip = rule.get("skip_token_regex")
            if skip is not None:
                try:
                    re.compile(skip)
                except re.error as exc:
                    errs.append(f"{where}.skip_token_regex: bad regex: {exc}")
        if action == "reclassify" and not rule.get("to_kind"):
            errs.append(f"{where}: reclassify requires 'to_kind'")
        if action in ("set_attr", "rename_to_next_identifier"):
            s = rule.get("set")
            if action == "set_attr" and not s:
                errs.append(f"{where}: set_attr requires a non-empty 'set'")
            if s is not None and not isinstance(s, dict):
                errs.append(f"{where}: 'set' must be an object")
    return errs
#### /校验 rules.json 文档 ####


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
