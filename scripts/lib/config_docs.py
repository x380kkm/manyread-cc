# audience: internal
# lib.config_docs
"""manyread.json 内可选配置文档（view_hide / modules / macro_strip）的校验与加载（仅依赖标准库）。

每个文档都是 ``manyread.json`` 下的一个可选键，亦可由命令行覆盖文件（``--ignore`` / ``--modules``）
提供。每个加载器：优先覆盖文件 > ``manyread.json`` 内的同名键 > 默认值；结构损坏一律向 stderr
告警并退回默认（``None`` 或默认 dict），绝不抛出。

view_hide 与 modules 共享同一套「覆盖文件或 manyread.json 键」加载骨架，由 ``_load_section``
统一承载，仅告警文案/包装键/校验器/合法键集不同。macro_strip 行为不同（默认 dict 而非 None、
无覆盖文件），自成一体。

本模块由 config.py 在末尾再导出，外部一律以 ``config.load_view_hide`` 等形式调用；UTF-8 BOM
（utf-8-sig）容忍与各告警文案逐字节保持历史行为。

保持导入安全：导入时无任何副作用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


#### 读取一个 JSON 文件为 dict，缺失/损坏则返回空 dict [@380kkm 2026-06-05] ####
def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        # utf-8-sig：容忍 UTF-8 BOM
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


#### 加载 manyread.json 下一段配置文档：覆盖文件 > 共享键 > None，畸形则告警退回 None [@380kkm 2026-06-05] ####
def _load_section(store: Path, section: str, override_path: Path | None,
                  override_flag: str, validator, known_keys: set[str],
                  known_hint: str) -> dict | None:
    """``section`` 为包装键与共享键名（'view_hide'/'modules'）；``override_flag`` 为覆盖文件的
    命令行标志名（'--ignore'/'--modules'）；``validator`` 返回错误列表；``known_keys`` 为合法
    顶层键集合；``known_hint`` 为告警里 '(known: ...)' 的提示串。

    覆盖文件可为 ``{section:{...}}`` 包装形式或裸形式。结构损坏 => 向 stderr 告警并返回 None。
    """
    if override_path is not None:
        p = Path(override_path)
        if not p.is_file():
            print(f"manyread: {override_flag} file not found: {p}", file=sys.stderr)
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"manyread: {override_flag} file is not valid JSON ({p}): {exc}", file=sys.stderr)
            return None
        if not isinstance(doc, dict):
            print(f"manyread: {override_flag} file must be a JSON object: {p}", file=sys.stderr)
            return None
        # 接受包装形式或裸形式
        sec = doc.get(section, doc)
    else:
        mr_json = store / "manyread.json"
        if mr_json.is_file():
            # 存在但不可读/为空时告警
            shared = _read_json(mr_json)
            if not shared:
                print(f"manyread: {mr_json} present but unreadable/empty — "
                      f"shared config (incl. {section}) ignored", file=sys.stderr)
                return None
            sec = shared.get(section)
        else:
            sec = None
    if not sec or not isinstance(sec, dict):
        return None
    errs = validator(sec)
    if errs:
        print(f"manyread: ignoring malformed {section} config: " + "; ".join(errs), file=sys.stderr)
        return None
    unknown = sorted(set(sec) - known_keys)
    if unknown:
        print(f"manyread: {section} has unknown key(s) " + ", ".join(unknown)
              + f" ({known_hint}) — proceeding", file=sys.stderr)
    return sec


#### view-hide 配置的合法键集合（已提交、共享、视图级、可恢复） [@380kkm 2026-06-05] ####
_VIEW_HIDE_KEYS = {"version", "names", "patterns", "min_fan_in"}


#### 校验一份 view_hide 文档，返回人类可读的错误列表 [@380kkm 2026-06-05] ####
def validate_view_hide(vh: dict) -> list[str]:
    """空列表 == 合法。"""
    if not isinstance(vh, dict):
        return ["view_hide must be an object"]
    errs: list[str] = []
    if vh.get("version", 1) != 1:
        errs.append("view_hide.version must be 1")
    for k in ("names", "patterns"):
        v = vh.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"view_hide.{k} must be a list of strings")
    mfi = vh.get("min_fan_in")
    if mfi is not None and (not isinstance(mfi, int) or isinstance(mfi, bool) or mfi < 0):
        errs.append("view_hide.min_fan_in must be an int >= 0")
    return errs


#### 解析已提交的符号 view-hide 配置（优先 --ignore 文件） [@380kkm 2026-06-05] ####
def load_view_hide(store: Path, override_path: Path | None = None) -> dict | None:
    """优先级：``override_path``（--ignore 文件）> ``manyread.json['view_hide']`` > None。

    一个 --ignore 文件既可以是 ``{view_hide:{...}}`` 包装形式，也可以是裸的
    ``{names,patterns,min_fan_in}``。结构损坏 => 向 stderr 告警并返回 None。
    """
    return _load_section(
        store, "view_hide", override_path, "--ignore",
        validate_view_hide, _VIEW_HIDE_KEYS,
        "known: version/names/patterns/min_fan_in",
    )


#### modules 配置的合法顶层键集合（已提交、共享、N 路模块分区声明） [@380kkm 2026-06-05] ####
_MODULES_KEYS = {"version", "fallback", "zones"}
#### 单个 zone 的合法键集合 [@380kkm 2026-06-05] ####
_MODULE_ZONE_KEYS = {"name", "include", "exclude", "glob"}


#### 校验一份 modules 文档，返回人类可读的错误列表 [@380kkm 2026-06-05] ####
def validate_modules(doc: dict) -> list[str]:
    """空列表 == 合法。要求 ``version==1``、``zones`` 为列表，每个 zone 名唯一非空、
    ``include``/``exclude`` 为字符串列表。``fallback`` 可选（字符串）。"""
    if not isinstance(doc, dict):
        return ["modules must be an object"]
    errs: list[str] = []
    if doc.get("version", 1) != 1:
        errs.append("modules.version must be 1")
    fb = doc.get("fallback")
    if fb is not None and not isinstance(fb, str):
        errs.append("modules.fallback must be a string")
    zones = doc.get("zones")
    if not isinstance(zones, list) or not zones:
        errs.append("modules.zones must be a non-empty list")
        return errs
    seen: set[str] = set()
    for i, z in enumerate(zones):
        if not isinstance(z, dict):
            errs.append(f"modules.zones[{i}] must be an object")
            continue
        name = z.get("name")
        if not isinstance(name, str) or not name:
            errs.append(f"modules.zones[{i}].name must be a non-empty string")
        elif name in seen:
            errs.append(f"modules.zones[{i}].name {name!r} is duplicated")
        else:
            seen.add(name)
        inc = z.get("include")
        if not (isinstance(inc, list) and inc and all(isinstance(x, str) for x in inc)):
            errs.append(f"modules.zones[{i}].include must be a non-empty list of strings")
        for k in ("exclude", "glob"):
            v = z.get(k)
            if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                errs.append(f"modules.zones[{i}].{k} must be a list of strings")
    return errs


#### 解析已提交的 N 路 modules 配置（优先 --modules 文件） [@380kkm 2026-06-05] ####
def load_modules(store: Path, override_path: Path | None = None) -> dict | None:
    """优先级：``override_path``（--modules 文件）> ``manyread.json['modules']`` > None。

    一个 --modules 文件既可以是 ``{modules:{...}}`` 包装形式，也可以是裸的
    ``{version,fallback,zones}``。结构损坏 => 向 stderr 告警并返回 None。
    """
    return _load_section(
        store, "modules", override_path, "--modules",
        validate_modules, _MODULES_KEYS,
        "known: version/fallback/zones",
    )


#### macro_strip 配置的合法键集合（已提交、共享、解析输入变换） [@380kkm 2026-06-05] ####
_MACRO_STRIP_KEYS = {"enabled", "extra_names", "extra_patterns"}


#### 校验一份 macro_strip 文档，返回人类可读的错误列表 [@380kkm 2026-06-05] ####
def validate_macro_strip(ms: dict) -> list[str]:
    """空列表 == 合法。"""
    if not isinstance(ms, dict):
        return ["macro_strip must be an object"]
    errs: list[str] = []
    en = ms.get("enabled", True)
    if not isinstance(en, bool):
        errs.append("macro_strip.enabled must be a bool")
    for k in ("extra_names", "extra_patterns"):
        v = ms.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"macro_strip.{k} must be a list of strings")
    import re as _re
    for pat in (ms.get("extra_patterns") or []):
        if isinstance(pat, str):
            try:
                _re.compile(pat)
            except _re.error as exc:
                errs.append(f"macro_strip.extra_patterns bad regex {pat!r}: {exc}")
    return errs


#### 解析已提交的 c 系 macro-strip 配置（缺失键时默认启用） [@380kkm 2026-06-05] ####
def load_macro_strip(store: Path) -> dict:
    """键缺失 => {"enabled": True, "extra_names": [], "extra_patterns": []}。

    结构损坏 / 正则错误 => 默认 + 向 stderr 告警。未知键 => 告警 + 继续。manyread.json
    存在但不可读 => 默认 + 告警。总是返回一个 dict（绝不为 None）。
    """
    DEFAULT = {"enabled": True, "extra_names": [], "extra_patterns": []}
    mr_json = store / "manyread.json"
    if not mr_json.is_file():
        return dict(DEFAULT)
    shared = _read_json(mr_json)
    if not shared:
        # 存在但不可读/为空时按默认处理并告警
        if mr_json.stat().st_size > 0:
            print(f"manyread: {mr_json} present but unreadable/empty — "
                  "shared config (incl. macro_strip) ignored, using defaults", file=sys.stderr)
        return dict(DEFAULT)
    ms = shared.get("macro_strip")
    if ms is None:
        return dict(DEFAULT)
    if not isinstance(ms, dict):
        print("manyread: macro_strip must be an object — using defaults", file=sys.stderr)
        return dict(DEFAULT)
    errs = validate_macro_strip(ms)
    if errs:
        print("manyread: ignoring malformed macro_strip config: " + "; ".join(errs)
              + " — using defaults", file=sys.stderr)
        return dict(DEFAULT)
    unknown = sorted(set(ms) - _MACRO_STRIP_KEYS)
    if unknown:
        print("manyread: macro_strip has unknown key(s) " + ", ".join(unknown)
              + " (known: enabled/extra_names/extra_patterns) — ignoring them", file=sys.stderr)
    out = dict(DEFAULT)
    out.update({k: v for k, v in ms.items() if k in _MACRO_STRIP_KEYS})
    return out
