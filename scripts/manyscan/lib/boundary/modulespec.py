# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.modulespec —— N 路模块分区的规格原语（与二进制 Zoning 并行）。

把已索引符号的路径划分到 N 个用户声明的模块 ZONE，外加一个兜底 ZONE（默认 ``External``）。
:class:`ModuleSpec` 是从 config → build → views 贯穿的唯一边界类型；``module_of_path`` 是
其总分类器，复用二进制 ``zone_of_path`` 的逐前缀语义，仅以最长匹配在 N 个 include 间裁决。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from .zoning import _NORM, norm_root

#### 兜底 ZONE 的默认名（无任何 include 命中的路径归此） [@380kkm 2026-06-05] ####
DEFAULT_FALLBACK = "External"


#### 一个声明的模块 ZONE：名 + include 前缀集 + 可选 exclude / glob [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ModuleZone:
    name: str
    # 规范化、去尾斜杠的目录前缀
    includes: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()


#### N 路模块分区规格：ZONE 元组 + 兜底名 + 预排序的匹配器 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ModuleSpec:
    """``_matchers`` 是预先展平的 ``(include_prefix, zone_name)`` 对，按 ``(-len, prefix,
    decl_order)`` 全序排列以保证最长匹配 + 确定性平局裁决。``zones`` 保留声明序供渲染分列。

    空 include（``""``）表示整仓库归该 ZONE，复刻 ``zone_of_path`` 的 ``target_root==""`` 特例。
    """

    zones: tuple[ModuleZone, ...]
    fallback: str = DEFAULT_FALLBACK
    _matchers: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    # zone_name -> 编译后的 exclude/glob 谓词输入（按名查）
    _by_name: dict[str, ModuleZone] = field(default_factory=dict, compare=False)


#### 把一份已校验的 doc + 内联 zone 规范化为 ModuleSpec [@380kkm 2026-06-05] ####
def make_module_spec(doc: dict | None, inline: list[tuple[str, list[str]]] | None = None,
                     fallback: str | None = None) -> ModuleSpec:
    """``doc`` 是 config.load_modules 的输出（``{version,fallback,zones}``）或 None；
    ``inline`` 是 ``--module NAME=PREFIX[,...]`` 解析出的 (name, prefixes) 列表，合并为附加 zone
    （文件规格为基底，同名内联 zone 追加新 include 到既有 zone）。``fallback`` 显式覆盖兜底名。
    """
    raw_fb = (doc or {}).get("fallback") if doc else None
    fb = fallback or raw_fb or DEFAULT_FALLBACK

    #### 按声明序累积 zone，按名归并 include/exclude/glob [@380kkm 2026-06-05] ####
    order: list[str] = []
    acc: dict[str, dict] = {}

    def _add(name: str, includes, excludes=(), globs=()):
        if name not in acc:
            acc[name] = {"inc": [], "exc": [], "glob": []}
            order.append(name)
        acc[name]["inc"].extend(norm_root(x) for x in includes)
        acc[name]["exc"].extend(norm_root(x) for x in excludes)
        acc[name]["glob"].extend(globs)
    #### /累积 zone ####

    for z in (doc or {}).get("zones", []) if doc else []:
        _add(z["name"], z.get("include", []), z.get("exclude", []) or [], z.get("glob", []) or [])
    for name, prefixes in (inline or []):
        _add(name, prefixes)

    zones = tuple(ModuleZone(name=n, includes=tuple(acc[n]["inc"]),
                             excludes=tuple(acc[n]["exc"]), globs=tuple(acc[n]["glob"]))
                  for n in order)
    by_name = {z.name: z for z in zones}

    #### 展平 (include_prefix, zone_name)，按 (-len, prefix, decl_order) 全序 [@380kkm 2026-06-05] ####
    flat: list[tuple[int, str, int, str]] = []
    for di, z in enumerate(zones):
        for inc in z.includes:
            flat.append((-len(inc), inc, di, z.name))
    flat.sort()
    matchers = tuple((inc, name) for _nl, inc, _di, name in flat)
    #### /展平匹配器 ####

    return ModuleSpec(zones=zones, fallback=fb, _matchers=matchers, _by_name=by_name)


#### 判断规范化路径是否被某 zone 的 exclude / glob 排除 [@380kkm 2026-06-05] ####
def _excluded(p: str, zone: ModuleZone) -> bool:
    for ex in zone.excludes:
        if ex and (p == ex or p.startswith(ex + "/")):
            return True
    for gl in zone.globs:
        if gl and fnmatch.fnmatch(p, gl):
            return True
    return False


#### 把定义文件路径分类为某个 zone 名或兜底名（最长匹配 + exclude） [@380kkm 2026-06-05] ####
def module_of_path(path: str | None, spec: ModuleSpec) -> str:
    """缺失路径归兜底。逐 ``_matchers``（最长优先）检查 ``p == prefix`` 或
    ``p.startswith(prefix + '/')``；命中且未被该 zone exclude 即返回 zone 名；空前缀匹配一切。
    无任何命中归 ``spec.fallback``。逐前缀语义与 ``zone_of_path`` 完全一致。
    """
    if path is None:
        return spec.fallback
    p = _NORM(path)
    for prefix, name in spec._matchers:
        if prefix == "" or p == prefix or p.startswith(prefix + "/"):
            if not _excluded(p, spec._by_name[name]):
                return name
    return spec.fallback


#### 把一条 ``--module NAME=PREFIX[,PREFIX...]`` 字面量解析为 (name, prefixes) [@380kkm 2026-06-05] ####
def parse_inline_module(spec_str: str) -> tuple[str, list[str]]:
    """``"Core=Engine/Source/Core,Engine/Source/CoreUObject"`` -> ``("Core", [两个前缀])``；
    缺 ``=`` 或名为空时抛 ValueError。"""
    if "=" not in spec_str:
        raise ValueError(f"--module must be NAME=PREFIX[,PREFIX...], got {spec_str!r}")
    name, rest = spec_str.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"--module NAME must be non-empty, got {spec_str!r}")
    prefixes = [pp.strip() for pp in rest.split(",") if pp.strip()]
    if not prefixes:
        raise ValueError(f"--module {name!r} needs at least one PREFIX")
    return name, prefixes


#### SQL ``LIKE`` 前缀转义：%、_、\ 在 ESCAPE '\' 下逐字符转义 [@380kkm 2026-06-05] ####
_LIKE_SPECIAL = re.compile(r"([%_\\])")


def like_prefix(prefix: str) -> str:
    """返回可直接拼 ``'%'`` 的 LIKE 模式体（已转义特殊字符），配合 ``ESCAPE '\\'`` 使用。"""
    return _LIKE_SPECIAL.sub(r"\\\1", prefix)
