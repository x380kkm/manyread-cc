from __future__ import annotations

from pathlib import Path

from lib import config
import rules


#### 给出项目 rules 文件路径 <root>/.manyread/rules.json [@380kkm 2026-06-05] ####
def _default_rules_path(root: Path) -> Path:
    """项目 rules 文件：<root>/.manyread/rules.json。"""
    return Path(root) / ".manyread" / "rules.json"


#### 一次性加载并合并 override 规则 [@380kkm 2026-06-05] ####
def _resolve_merged_rules(cfg: config.ProjectConfig, rules_path: str | None,
                          no_rules: bool):
    """一次性加载并合并 override 规则。返回 (rules_list, rules_file_used)。

    --no-rules  -> ([], None)：完全跳过该变换（基础行为）。
    显式 --rules PATH 优先；否则取 <root>/.manyread/rules.json（若存在）。
    preset_dirs 由 load_rules 从解析后的 rules 文档中自行读取，而本函数还经路径
    隐式传入解析出的 rules 文件自身的目录上下文。
    无 rules 文件时返回 [] —— 向后兼容。
    """
    if no_rules:
        return [], None
    path = Path(rules_path) if rules_path else (cfg.store / "rules.json")
    if not path.exists():
        return [], None
    # load_rules 从文档自身读取 preset_dirs（相对 rules 文件目录解析）。
    # 此处无需额外的 extra_preset_dirs。
    merged = rules.load_rules(path, extra_preset_dirs=None)
    return merged, path


#### 给出规则遍历改动的符号的人类可读 diff 行 [@380kkm 2026-06-05] ####
def _preview_diff(before_rows: list[dict], after_rows: list[dict], path: str) -> list[str]:
    """返回规则遍历改动的符号的人类可读 diff 行。

    以 `_local` 匹配前后两侧的行（规则从不改动 `_local`）。报告重命名 / kind 变更
    / 新增 attrs / 丢弃，并列出触及某行的规则。
    """
    after_by_local = {r["_local"]: r for r in after_rows}
    lines: list[str] = []
    for b in before_rows:
        local = b["_local"]
        a = after_by_local.get(local)
        if a is None:
            lines.append(f"  {path}: DROP  {b['kind']} {b['name']!r} "
                         f"(L{b['start_line']})")
            continue
        changes = []
        if a["name"] != b["name"]:
            changes.append(f"name {b['name']!r} -> {a['name']!r}")
        if a["kind"] != b["kind"]:
            changes.append(f"kind {b['kind']!r} -> {a['kind']!r}")
        if (a.get("attrs") or {}) != (b.get("attrs") or {}):
            changes.append(f"attrs {b.get('attrs') or {}} -> {a.get('attrs') or {}}")
        if changes:
            prov = ",".join(a.get("provenance") or []) or "?"
            lines.append(f"  {path}: {'; '.join(changes)}  "
                         f"[L{b['start_line']}; rules: {prov}]")
    return lines
