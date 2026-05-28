# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread L2 enrichment override-rules engine + presets (stdlib only).

Base tree-sitter extraction is generic and occasionally wrong on codebase-specific
idioms (e.g. Unreal's `class SDFPARTICLES_API UFoo` makes tree-sitter-cpp record the
export macro `SDFPARTICLES_API` as the class name). Rather than hardcode per-engine
fixes into enrich_treesitter.py, corrections live in a project-scoped, agent-editable
override layer applied as a transform pass:

    tree-sitter extract (raw)  ->  apply_rules(...)  ->  write symbols/edges

No rules present -> identical to current behavior (backward compatible). The engine is
PURE (no DB, no IO) so it can be unit-tested and driven from enrich.

SHARED CONTRACT (must match enrich integration exactly)
-------------------------------------------------------
rules.json schema:
  { "version": 1,
    "extends_presets": ["unreal"],
    "preset_dirs": ["<abs or root-relative dir>"],
    "rules": [
      { "id": str,
        "when": { "lang"?: str, "kind"?: str, "name_regex"?: str, "path_glob"?: str },
        "action": "rename_to_next_identifier"|"set_attr"|"drop"|"reclassify",
        "skip_token_regex"?: str,   # rename_to_next_identifier
        "set"?: {<attr>: <val>},    # rename_to_next_identifier / set_attr
        "to_kind"?: str } ]         # reclassify
  }

Engine entry point (importable + pure):
  apply_rules(rows, edges, content_by_file_id, rules) -> (rows, edges, provenance)

  rows : list of dicts each like
    {"_local": int, "file_id": int, "name": str, "kind": str, "lang": str,
     "start_line": int, "end_line": int, "start_byte": int, "end_byte": int,
     "parent_local": int|None, "attrs": dict, "provenance": list[str]}
  edges: list of dicts
    {"file_id","src_local","dst_local"|None,"dst_name","relation"}
  content_by_file_id: file_id -> full file text (so rename_to_next_identifier can
    re-slice content[start_byte:] and, after skipping a token matching
    skip_token_regex, take the next identifier as the corrected name).

  Returns transformed rows/edges and a provenance map {rule_id: [local ids touched]}.
  PURE (no DB, no IO); rows untouched when rules == [].

CLI:  rules.py init|list|validate [<alias>] [--root PATH] [--rules PATH]
      (preview/apply that need the DB are driven from enrich; thin stubs here.)
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
from lib import config  # noqa: E402  (import after sys.path tweak, per convention)


# --- where built-in presets live --------------------------------------------
# <repo>/presets/<name>.rules.json  (this file is <repo>/scripts/rules.py)
BUILTIN_PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"

VALID_ACTIONS = (
    "rename_to_next_identifier",
    "set_attr",
    "drop",
    "reclassify",
)

# A C identifier (also matches the export-macro tokens we skip over).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# How far past start_byte we look when re-slicing source for a rename.
_SLICE_WINDOW = 256


# =============================================================================
# Engine (pure)
# =============================================================================
def apply_rules(rows, edges, content_by_file_id, rules):
    """Apply override rules to raw extraction output. PURE: no DB, no IO.

    Args:
      rows:  list[dict] symbol rows (see module docstring / SHARED CONTRACT).
      edges: list[dict] edge rows referencing rows by _local.
      content_by_file_id: dict[int, str] full file text per file_id.
      rules: list[dict] merged rule list (preset + project), in order.

    Returns:
      (rows, edges, provenance) where provenance is {rule_id: [local ids touched]}.
      When rules == [] the inputs are returned untouched (deep-copied for safety).
    """
    # Always work on copies so the engine is side-effect-free for the caller.
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


def _compile_when(when):
    """Return a predicate row->bool from a `when` clause.

    Matches by lang (exact), kind (exact), name_regex (re.fullmatch on name),
    and path_glob (fnmatch on row['path'] if present; optional/no-op otherwise).
    An empty `when` matches every row.
    """
    lang = when.get("lang")
    kind = when.get("kind")
    name_regex = when.get("name_regex")
    path_glob = when.get("path_glob")
    name_re = re.compile(name_regex) if name_regex else None

    def pred(row) -> bool:
        if lang is not None and row.get("lang") != lang:
            return False
        if kind is not None and row.get("kind") != kind:
            return False
        if name_re is not None and not name_re.fullmatch(row.get("name") or ""):
            return False
        if path_glob is not None:
            # path is optional in the row dict; if unavailable treat as no-op
            # (do NOT exclude) so path_glob never silently drops everything.
            path = row.get("path")
            if path is not None and not fnmatch.fnmatch(path, path_glob):
                return False
        return True

    return pred


def _apply_action(row, rule, action, content_by_file_id) -> bool:
    """Mutate `row` per the rule's action. Return True if the row was touched."""
    if action == "rename_to_next_identifier":
        return _action_rename(row, rule, content_by_file_id)
    if action == "set_attr":
        return _action_set_attr(row, rule)
    if action == "drop":
        return True  # caller records + removes the row.
    if action == "reclassify":
        return _action_reclassify(row, rule)
    # Unknown action: leave the row untouched (validate() catches these).
    return False


def _action_rename(row, rule, content_by_file_id) -> bool:
    """Slice content[start_byte : start_byte+256], optionally skip a token matching
    skip_token_regex, then take the next C identifier as the corrected name. On a
    match: rename and merge `set` attrs. Returns True iff renamed."""
    content = content_by_file_id.get(row.get("file_id"))
    if content is None:
        return False
    start = row.get("start_byte") or 0
    window = content[start:start + _SLICE_WINDOW]

    pos = 0
    skip_re_src = rule.get("skip_token_regex")
    if skip_re_src:
        # Skip leading whitespace, then an optional token matching skip_token_regex.
        m_ws = re.match(r"\s*", window)
        scan = m_ws.end() if m_ws else 0
        m_skip = re.compile(skip_re_src).match(window, scan)
        if m_skip:
            pos = m_skip.end()
        else:
            pos = scan  # nothing to skip; carry on from after whitespace.

    m_ident = _IDENT_RE.search(window, pos)
    if not m_ident:
        return False
    new_name = m_ident.group(0)
    if not new_name or new_name == row.get("name"):
        # No usable next identifier (or it's identical): leave untouched.
        if new_name == row.get("name"):
            # still merge set attrs? No — contract: only on a real rename.
            return False
        return False

    row["name"] = new_name
    _merge_set(row, rule.get("set"))
    return True


def _action_set_attr(row, rule) -> bool:
    """Merge rule['set'] into row['attrs']. Returns True iff anything changed."""
    return _merge_set(row, rule.get("set"))


def _action_reclassify(row, rule) -> bool:
    """Set row['kind'] = rule['to_kind']. Returns True iff kind changed."""
    to_kind = rule.get("to_kind")
    if not to_kind or row.get("kind") == to_kind:
        return False
    row["kind"] = to_kind
    return True


def _merge_set(row, set_map) -> bool:
    """Merge a `set` map into row['attrs']; return True iff a value changed."""
    if not set_map:
        return False
    attrs = row.setdefault("attrs", {})
    changed = False
    for k, v in set_map.items():
        if attrs.get(k) != v:
            attrs[k] = v
            changed = True
    return changed


def _mark(row, provenance, rule_id) -> None:
    """Record that rule_id touched this row, in both the row and the prov map."""
    prov = row.setdefault("provenance", [])
    if rule_id not in prov:
        prov.append(rule_id)
    bucket = provenance.setdefault(rule_id, [])
    local = row.get("_local")
    if local is not None and local not in bucket:
        bucket.append(local)


# =============================================================================
# Preset loading + merge
# =============================================================================
def _read_rules_file(path: Path) -> dict:
    """Read + JSON-parse a rules/preset file. Raises ValueError on bad JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def _find_preset(name: str, search_dirs: list[Path]) -> Path | None:
    """Locate <name>.rules.json in the built-in dir first, then extra dirs."""
    fname = f"{name}.rules.json"
    for d in [BUILTIN_PRESET_DIR, *search_dirs]:
        cand = Path(d) / fname
        if cand.exists():
            return cand
    return None


def _collect_preset_rules(
    doc: dict,
    base_dir: Path,
    extra_preset_dirs: list[Path],
    seen: set[str],
    acc: list[dict],
) -> None:
    """Recursively collect rules from extends_presets into `acc` (preset order).

    preset_dirs declared inside `doc` are resolved relative to base_dir (the dir
    of the file that declared them) when not absolute, and added to the search
    path. Built-in dir is always searched first (see _find_preset)."""
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
            continue  # guard against cycles / repeats.
        seen.add(name)
        preset_path = _find_preset(name, search_dirs)
        if preset_path is None:
            raise ValueError(
                f"preset '{name}' not found (searched: built-in dir"
                + ("".join(f", {d}" for d in search_dirs)) + ")"
            )
        preset_doc = _read_rules_file(preset_path)
        # A preset may itself extend other presets (depth-first, before its own).
        _collect_preset_rules(
            preset_doc, preset_path.parent, extra_preset_dirs, seen, acc
        )
        for rule in preset_doc.get("rules") or []:
            r = dict(rule)
            r.setdefault("_origin", f"preset:{name}")
            acc.append(r)


def load_rules(rules_path, extra_preset_dirs=None) -> list[dict]:
    """Load + merge a project rules.json with the presets it extends.

    Merge order: presets (in extends_presets order, searched built-in dir then any
    preset_dirs) THEN project rules. Project rules WIN on the same id (a project
    rule with id X replaces a preset rule with id X, in place).

    Each returned rule carries an `_origin` marker ("preset:<name>" or "project").
    If rules_path does not exist, only the (empty) preset chain is returned, which
    is [] — backward compatible.
    """
    extra_preset_dirs = [Path(d) for d in (extra_preset_dirs or [])]
    rules_path = Path(rules_path)

    if rules_path.exists():
        doc = _read_rules_file(rules_path)
        base_dir = rules_path.parent
    else:
        doc = {}
        base_dir = rules_path.parent

    # 1. preset rules (depth-first by extends_presets order).
    preset_rules: list[dict] = []
    _collect_preset_rules(doc, base_dir, extra_preset_dirs, set(), preset_rules)

    # 2. project rules, marked.
    project_rules = []
    for rule in doc.get("rules") or []:
        r = dict(rule)
        r.setdefault("_origin", "project")
        project_rules.append(r)

    # 3. merge: project rules win on the same id (replace in place); new ids append.
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


# =============================================================================
# Validation
# =============================================================================
def validate_rules_doc(doc: dict) -> list[str]:
    """Lint a parsed rules.json document. Returns a list of human-readable errors
    (empty == valid)."""
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


# =============================================================================
# CLI
# =============================================================================
_STARTER_DOC = {
    "version": 1,
    "extends_presets": ["unreal"],
    "preset_dirs": [],
    "rules": [],
}


def _rules_path(args) -> Path:
    """The store's shared rules file: <store>/rules.json (or explicit --rules)."""
    if getattr(args, "rules", None):
        return Path(args.rules)
    cfg = config.resolve_project(root=args.root, store=args.store)
    return cfg.store / "rules.json"


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
    # Also try resolving presets so missing presets surface here.
    extra = [d.strip() for d in (args.preset_dir or "").split(",") if d.strip()]
    try:
        load_rules(path, extra_preset_dirs=extra)
    except ValueError as exc:
        print(f"INVALID (preset resolution): {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path} ({len(doc.get('rules') or [])} project rule(s); presets resolve)")
    return 0


def _stub(name: str) -> int:
    print(
        f"`rules.py {name}` is driven from enrich (it needs the project DB). Use:\n"
        f"  uv run --python 3.12 scripts/enrich_treesitter.py <alias|--root PATH> "
        f"--rules <PATH> --rules-preview   # {name} diff\n"
        "The pure engine (apply_rules) and load_rules live here for enrich to import.",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rules.py",
        description="manyread enrichment override-rules: author/inspect/validate.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--root", default=None, help="source tree root (default: store's parent)")
        p.add_argument("--store", default=None, help="explicit manyread store dir (default: discover)")
        p.add_argument("--rules", default=None,
                       help="explicit rules.json path (default <store>/rules.json)")
        p.add_argument("--preset-dir", default=None,
                       help="comma list of extra preset search dirs")

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


if __name__ == "__main__":
    raise SystemExit(main())
