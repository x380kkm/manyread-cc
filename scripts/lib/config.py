# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread 配置 + 项目本地存储库解析（仅依赖标准库）。

存储模型（v2）：所有内容都放在一个可见、项目本地的存储库文件夹中，名为
``manyread/``（默认 ``./manyread``，在 init 时选定）—— 没有 ``~/.manyread`` 家目录，
因此存储库随仓库一起迁移，便于分享。

存储库布局：
  manyread/
    manyread.json        共享配置（alias、languages、exts、ignore）            [提交]
    source.db            索引 + symbols/edges                                  [提交]
    refs/                共享的精选 ref                                        [提交]
    traces/              共享的静态 / 持久查询模式                             [提交]
    user/                每用户配置（机器路径、覆盖项）                        [gitignore]
    short/               短期、按版本打标签、不归档                           [gitignore]
      refs/  rdc/  traces/   本版本的 ref+rdc 副本；动态 trace
    .gitignore           忽略 user/ + short/（source.db 仍提交）

解析顺序：
  store  = --store 参数 > MANYREAD_STORE 环境变量 > 从 cwd 向上找 ``manyread/`` 目录
  root   = --root 参数 > user/config.json 的 "root" > manyread.json 的 "root" > store.parent
  config = manyread.json（共享）叠加 user/config.json（每用户）

保持导入安全：导入时无任何副作用。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

STORE_DIRNAME = "manyread"

#### 语言 -> 扩展名预设（内置） [@380kkm 2026-06-05] ####
LANG_EXTS: dict[str, list[str]] = {
    "cpp": [".h", ".hpp", ".hh", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx"],
    "python": [".py", ".pyi"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "csharp": [".cs"],
    "java": [".java"],
    "gdscript": [".gd"],
    "shader": [".hlsl", ".usf", ".ush"],
    "glsl": [".glsl", ".vert", ".frag", ".comp", ".geom", ".tesc", ".tese"],
    "docs": [".md", ".json", ".ini"],
}

#### 默认忽略的 glob 通配 [@380kkm 2026-06-05] ####
DEFAULT_IGNORE_GLOBS: list[str] = [
    "ThirdParty/*",
    "Intermediate/*",
    "*/node_modules/*",
]

#### 写入存储库的 .gitignore 内容：把每用户 + 短期内容排除出 git [@380kkm 2026-06-05] ####
STORE_GITIGNORE = "# manyread store — per-user + short-term content is NOT shared\nuser/\nshort/\n"


#### 一个项目的完整解析后配置 [@380kkm 2026-06-05] ####
@dataclass
class ProjectConfig:
    alias: str
    # manyread/ 目录
    store: Path
    # 被索引的源码树
    root: Path
    # <store>/source.db
    db_path: Path
    # <store>/refs（共享的精选 ref）
    refs_dir: Path
    # <store>/traces（静态 / 持久 trace）
    traces_dir: Path
    # <store>/user（每用户，gitignore）
    user_dir: Path
    # <store>/short（临时，gitignore）
    short_dir: Path
    # <store>/short/refs（按版本打标签的 ref 副本）
    short_refs_dir: Path
    # <store>/short/rdc（按版本打标签的 rdc 副本）
    short_rdc_dir: Path
    # <store>/short/traces（动态 / 带日期的 trace）
    short_traces_dir: Path
    languages: list[str]
    exts: list[str]
    profile: str | None
    ignore_globs: list[str] = field(default_factory=list)
    # 已启用的可选扩展名单（如 ["ue"]）；空 == 仅通用核心
    extensions: list[str] = field(default_factory=list)
#### /项目解析后配置 ####


#### 从 start（默认 cwd）向上查找 manyread/ 存储库目录 [@380kkm 2026-06-05] ####
def find_store(start: Path | None = None) -> Path | None:
    """MANYREAD_STORE 环境变量覆盖此查找，指向一个显式的存储库路径。"""
    env = os.environ.get("MANYREAD_STORE")
    if env:
        p = Path(env)
        return p if p.exists() else None
    cur = Path(start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cand = d / STORE_DIRNAME
        if (cand / "manyread.json").is_file():
            return cand
        # 也接受当前就处在存储库目录内部的情形
        if d.name == STORE_DIRNAME and (d / "manyread.json").is_file():
            return d
    return None


#### 由存储库根目录推导各子路径 [@380kkm 2026-06-05] ####
def _store_paths(store: Path) -> dict[str, Path]:
    return {
        "db_path": store / "source.db",
        "refs_dir": store / "refs",
        "traces_dir": store / "traces",
        "user_dir": store / "user",
        "short_dir": store / "short",
        "short_refs_dir": store / "short" / "refs",
        "short_rdc_dir": store / "short" / "rdc",
        "short_traces_dir": store / "short" / "traces",
    }


#### 由语言列表收集去重后的默认扩展名 [@380kkm 2026-06-05] ####
def default_exts_for(languages: list[str]) -> list[str]:
    exts: list[str] = []
    for lang in languages:
        for ext in LANG_EXTS.get(lang.lower(), []):
            if ext not in exts:
                exts.append(ext)
    return exts


#### 向上从 root 走到 store 范围内探测是否存在 *.uproject 标记 [@380kkm 2026-06-05] ####
def _has_uproject(root: Path, store: Path) -> bool:
    """有界扫描：仅看 root 直属目录、以及 root..store 这条祖先链上的各目录，绝不递归整盘。"""
    seen: set[Path] = set()
    cur = root.resolve()
    stop = store.resolve().parent
    # 沿祖先链上行直到 store 的父目录（含），避免无界遍历
    chain = [cur, *cur.parents]
    for d in chain:
        if d in seen:
            continue
        seen.add(d)
        try:
            if any(d.glob("*.uproject")):
                return True
        except OSError:
            pass
        if d == stop:
            break
    return False


#### 解析一个项目实际启用的扩展名单：显式名单 > profile=='ue' 别名 > .uproject 自动探测 [@380kkm 2026-06-05] ####
def active_extensions(cfg: "ProjectConfig") -> list[str]:
    """优先级：显式 manyread.json `extensions`（user>shared，显式 [] 硬禁用）> `profile=='ue'`
    别名为 ['ue'] > 在 cfg.root..cfg.store 范围内探测到 *.uproject 则推断 ['ue']（推断时向
    stderr 输出一行说明）。返回扩展名列表（可能为空）。
    """
    import sys

    shared = load_shared(cfg.store)
    user = load_user(cfg.store)
    # 显式名单：user 覆盖 shared；显式 [] 也是一个确定的「硬禁用」信号
    explicit = user.get("extensions", shared.get("extensions"))
    if isinstance(explicit, list):
        return [str(x) for x in explicit]

    if cfg.profile == "ue":
        return ["ue"]

    if _has_uproject(Path(cfg.root), Path(cfg.store)):
        print("manyread: inferred UE extension from a *.uproject near the source root "
              "(set \"extensions\": [] in manyread.json to disable)", file=sys.stderr)
        return ["ue"]

    return []


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


#### 读取已提交的共享配置 <store>/manyread.json [@380kkm 2026-06-05] ####
def load_shared(store: Path) -> dict:
    return _read_json(store / "manyread.json")


#### 读取每用户的 gitignore 配置 <store>/user/config.json [@380kkm 2026-06-05] ####
def load_user(store: Path) -> dict:
    return _read_json(store / "user" / "config.json")


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
    import sys

    if override_path is not None:
        p = Path(override_path)
        if not p.is_file():
            print(f"manyread: --ignore file not found: {p}", file=sys.stderr)
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"manyread: --ignore file is not valid JSON ({p}): {exc}", file=sys.stderr)
            return None
        if not isinstance(doc, dict):
            print(f"manyread: --ignore file must be a JSON object: {p}", file=sys.stderr)
            return None
        # 接受包装形式或裸形式
        vh = doc.get("view_hide", doc)
    else:
        mr_json = store / "manyread.json"
        if mr_json.is_file():
            # 存在但不可读/为空时告警
            shared = _read_json(mr_json)
            if not shared:
                print(f"manyread: {mr_json} present but unreadable/empty — "
                      "shared config (incl. view_hide) ignored", file=sys.stderr)
                return None
            vh = shared.get("view_hide")
        else:
            vh = None
    if not vh or not isinstance(vh, dict):
        return None
    errs = validate_view_hide(vh)
    if errs:
        print("manyread: ignoring malformed view_hide config: " + "; ".join(errs), file=sys.stderr)
        return None
    unknown = sorted(set(vh) - _VIEW_HIDE_KEYS)
    if unknown:
        print("manyread: view_hide has unknown key(s) " + ", ".join(unknown)
              + " (known: version/names/patterns/min_fan_in) — proceeding", file=sys.stderr)
    return vh


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
    import sys

    if override_path is not None:
        p = Path(override_path)
        if not p.is_file():
            print(f"manyread: --modules file not found: {p}", file=sys.stderr)
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"manyread: --modules file is not valid JSON ({p}): {exc}", file=sys.stderr)
            return None
        if not isinstance(doc, dict):
            print(f"manyread: --modules file must be a JSON object: {p}", file=sys.stderr)
            return None
        # 接受包装形式或裸形式
        md = doc.get("modules", doc)
    else:
        mr_json = store / "manyread.json"
        if mr_json.is_file():
            # 存在但不可读/为空时告警
            shared = _read_json(mr_json)
            if not shared:
                print(f"manyread: {mr_json} present but unreadable/empty — "
                      "shared config (incl. modules) ignored", file=sys.stderr)
                return None
            md = shared.get("modules")
        else:
            md = None
    if not md or not isinstance(md, dict):
        return None
    errs = validate_modules(md)
    if errs:
        print("manyread: ignoring malformed modules config: " + "; ".join(errs), file=sys.stderr)
        return None
    unknown = sorted(set(md) - _MODULES_KEYS)
    if unknown:
        print("manyread: modules has unknown key(s) " + ", ".join(unknown)
              + " (known: version/fallback/zones) — proceeding", file=sys.stderr)
    return md


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
    import sys

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


#### 在 location 下创建一个全新的 manyread/ 存储库 [@380kkm 2026-06-05] ####
def init_store(location: Path, alias: str | None = None,
               languages: list[str] | None = None, exts: list[str] | None = None,
               ignore_globs: list[str] | None = None, root: Path | None = None) -> Path:
    """写入 manyread.json（共享）+ 子目录骨架 + .gitignore，返回存储库路径。

    幂等：已存在的文件不会被覆盖。
    """
    store = Path(location).resolve() / STORE_DIRNAME
    paths = _store_paths(store)
    for d in (store, paths["refs_dir"], paths["traces_dir"], paths["user_dir"],
              paths["short_dir"], paths["short_refs_dir"], paths["short_rdc_dir"],
              paths["short_traces_dir"]):
        d.mkdir(parents=True, exist_ok=True)

    cfg_file = store / "manyread.json"
    if not cfg_file.exists():
        langs = languages or []
        payload = {
            "alias": alias or Path(location).resolve().name,
            "languages": langs,
            "exts": exts if exts is not None else default_exts_for(langs),
            "profile": None,
            "ignore_globs": ignore_globs if ignore_globs is not None else list(DEFAULT_IGNORE_GLOBS),
        }
        # extensions 键有意缺席：缺席 = 运行时经 active_extensions 推断；显式 [] 才是硬禁用
        # 仅当显式作为共享默认提供时才记录源码根
        if root is not None:
            payload["root"] = str(root)
        cfg_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    gi = store / ".gitignore"
    if not gi.exists():
        gi.write_text(STORE_GITIGNORE, encoding="utf-8")

    return store


#### 写入每用户配置（机器路径 / 覆盖项），被 gitignore [@380kkm 2026-06-05] ####
def save_user(store: Path, data: dict) -> None:
    user_cfg = store / "user" / "config.json"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


#### 由存储库 + 源码根解析出一个 ProjectConfig [@380kkm 2026-06-05] ####
def resolve_project(root: str | None = None, store: str | None = None) -> ProjectConfig:
    """store：显式 --store > MANYREAD_STORE 环境变量 > 从 ``root``（若给定）或 cwd 向上查找。

    找不到时抛出 SystemError 并给出指引。
    root：显式 --root > user 配置 "root" > shared 配置 "root" > store.parent。
    """
    store_path: Path | None
    if store:
        store_path = Path(store).resolve()
        if not (store_path / "manyread.json").is_file() and store_path.name != STORE_DIRNAME:
            # 允许把 --store 指向一个包含 manyread/ 的项目目录
            cand = store_path / STORE_DIRNAME
            if (cand / "manyread.json").is_file():
                store_path = cand
    else:
        start = Path(root).resolve() if root else None
        store_path = find_store(start)

    if store_path is None:
        raise SystemError(
            "no manyread store found. Run mr-init to create one "
            "(default ./manyread), or pass --store PATH / set MANYREAD_STORE."
        )
    store_path = store_path.resolve()

    shared = load_shared(store_path)
    user = load_user(store_path)
    paths = _store_paths(store_path)

    # 源码根：--root > user > shared > store.parent
    root_val = root or user.get("root") or shared.get("root")
    root_path = Path(root_val).resolve() if root_val else store_path.parent

    alias = user.get("alias") or shared.get("alias") or store_path.parent.name
    languages = user.get("languages") or shared.get("languages") or []
    exts = user.get("exts") or shared.get("exts") or default_exts_for(languages)
    profile = user.get("profile", shared.get("profile"))
    ignore_globs = user.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = shared.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = list(DEFAULT_IGNORE_GLOBS)

    # 已启用扩展：user 覆盖 shared，缺省为 []（仅通用核心）
    extensions = user.get("extensions")
    if extensions is None:
        extensions = shared.get("extensions")
    if extensions is None:
        extensions = []

    return ProjectConfig(
        alias=alias,
        store=store_path,
        root=root_path,
        db_path=paths["db_path"],
        refs_dir=paths["refs_dir"],
        traces_dir=paths["traces_dir"],
        user_dir=paths["user_dir"],
        short_dir=paths["short_dir"],
        short_refs_dir=paths["short_refs_dir"],
        short_rdc_dir=paths["short_rdc_dir"],
        short_traces_dir=paths["short_traces_dir"],
        languages=list(languages),
        exts=list(exts),
        profile=profile,
        ignore_globs=list(ignore_globs),
        extensions=list(extensions),
    )


#### 每用户 hub（env 目录）：已激活存储库路径的注册表 [@380kkm 2026-06-05] ####
def hub_dir() -> Path:
    """每用户 hub 目录：MANYREAD_HOME 环境变量，否则 ~/.manyread。"""
    env = os.environ.get("MANYREAD_HOME")
    return Path(env) if env else (Path.home() / ".manyread")


#### hub 注册表文件路径 [@380kkm 2026-06-05] ####
def _hub_registry_path() -> Path:
    return hub_dir() / "stores.json"


#### 返回 hub 注册表 [@380kkm 2026-06-05] ####
def list_stores() -> dict:
    """形状：{ "<store 绝对路径>": {alias, root, updated} }。"""
    p = _hub_registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


#### 在每用户 hub 注册表中记录（或刷新）一个存储库 [@380kkm 2026-06-05] ####
def register_store(store: Path, alias: str | None = None, root: Path | None = None) -> None:
    reg = list_stores()
    reg[str(Path(store).resolve())] = {
        "alias": alias or "",
        "root": str(Path(root).resolve()) if root else "",
        "updated": int(time.time()),
    }
    p = _hub_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


#### 从 hub 注册表中移除一个存储库（不动磁盘上的存储库） [@380kkm 2026-06-05] ####
def unregister_store(store: Path) -> bool:
    reg = list_stores()
    key = str(Path(store).resolve())
    if key in reg:
        del reg[key]
        _hub_registry_path().write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
        return True
    return False


#### 判断 path 是否为不宜就地放置存储库/索引的系统位置 [@380kkm 2026-06-05] ####
def is_system_location(path: Path) -> bool:
    """覆盖盘符根（C:\\、W:\\、/）、用户家目录，以及家目录正下方的常见外壳文件夹

    （Desktop/Documents/Downloads/OneDrive）。
    """
    p = Path(path).resolve()
    # 文件系统 / 盘符根
    if p.parent == p:
        return True
    home = Path.home().resolve()
    if p == home:
        return True
    if p.parent == home and p.name.lower() in {
        "desktop", "documents", "downloads", "onedrive",
    }:
        return True
    return False
