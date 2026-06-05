from __future__ import annotations

from pathlib import Path

from lib import config
import rules


#### 给出项目 rules 文件路径 <root>/.manyread/rules.json [@380kkm 2026-06-05] ####
def _default_rules_path(root: Path) -> Path:
    return Path(root) / ".manyread" / "rules.json"


#### 一次性加载并合并 override 规则 [@380kkm 2026-06-05] ####
def _resolve_merged_rules(cfg: config.ProjectConfig, rules_path: str | None,
                          no_rules: bool):
    if no_rules:
        return [], None
    path = Path(rules_path) if rules_path else (cfg.store / "rules.json")
    if not path.exists():
        return [], None
    # load_rules 从文档自身读取 preset_dirs
    merged = rules.load_rules(path, extra_preset_dirs=None)
    return merged, path


#### 给出规则遍历改动的符号的人类可读 diff 行 [@380kkm 2026-06-05] ####
def _preview_diff(before_rows: list[dict], after_rows: list[dict], path: str) -> list[str]:
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
