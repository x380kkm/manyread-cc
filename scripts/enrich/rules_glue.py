from __future__ import annotations

from pathlib import Path

from lib import config
import rules


# --- override-rules helpers --------------------------------------------------
def _default_rules_path(root: Path) -> Path:
    """The project rules file: <root>/.manyread/rules.json."""
    return Path(root) / ".manyread" / "rules.json"


def _resolve_merged_rules(cfg: config.ProjectConfig, rules_path: str | None,
                          no_rules: bool):
    """Load + merge override rules once. Returns (rules_list, rules_file_used).

    --no-rules  -> ([], None): skip the transform entirely (base behavior).
    explicit --rules PATH wins; else <root>/.manyread/rules.json IF it exists.
    preset_dirs are passed from the resolved rules doc by load_rules itself, but
    we also pass the resolved rules file's own dir context implicitly via the path.
    Returns [] when no rules file is present -> backward compatible.
    """
    if no_rules:
        return [], None
    path = Path(rules_path) if rules_path else (cfg.store / "rules.json")
    if not path.exists():
        return [], None
    # load_rules reads preset_dirs from the doc itself (resolved relative to the
    # rules file dir). No extra_preset_dirs needed here.
    merged = rules.load_rules(path, extra_preset_dirs=None)
    return merged, path


def _preview_diff(before_rows: list[dict], after_rows: list[dict], path: str) -> list[str]:
    """Return human-readable diff lines for symbols changed by the rules pass.

    Matches before/after rows by `_local` (rules never change `_local`). Reports
    rename / kind change / new attrs / drop, and lists which rules touched a row.
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
