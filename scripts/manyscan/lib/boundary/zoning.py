# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.zoning — 目标↔依赖的分区（ZONING）。

如何把已索引的符号划分到 TARGET（你正在分析的代码）与 DEPENDENCY 区
（它所依赖的部分，可能来自多个源），boundary 包其余部分所依赖的路径原语，
以及依赖侧的标注。
"""
from __future__ import annotations

from dataclasses import dataclass

from lib import deps, rollup

TARGET = "target"
DEPENDENCY = "dependency"

_NORM = deps.PathIndex._norm


#### 分区配置：如何把符号划入目标区与依赖区 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class Zoning:
    """如何把符号划分到目标（被分析）区与依赖区。

    ``target_root`` 是规范化、去掉尾部斜杠的目录前缀，用于界定 TARGET
    （``""`` 表示整个仓库即目标）。``dep_roots`` 仅用于依赖侧的标注/分组提示，
    它们永不改变目标判定位（一个符号是依赖当且仅当它不在 ``target_root`` 之下）。
    依赖侧可聚合多个不同的依赖源，每个提示一个。
    """

    target_root: str
    # 规范化、按最长优先排序
    dep_roots: tuple[str, ...] = ()


#### 规范化根路径：归一斜杠、去前导 ./、去尾部 / [@380kkm 2026-06-05] ####
def norm_root(p: str) -> str:
    return _NORM(p or "").rstrip("/")


#### 自动探测目标根：最短的模块根（*.uplugin / *.Build.cs / …） [@380kkm 2026-06-05] ####
def detect_target_root(store) -> str:
    """自动探测目标根：最短的模块根（``*.uplugin`` / ``*.Build.cs`` / …）。

    按 ``(len, str)`` 取 ``min`` 以保证确定性；若无模块标记文件则返回 ``""``
    （整个仓库）。注意：``""`` 是有歧义的——它同时也是合法的仓库根标记。
    若调用方不能容忍把整个仓库（含依赖）静默归类为目标，应使用
    :func:`has_module_markers` 区分这两种情形，或显式提供 ``target_root``。
    """
    roots = rollup.module_roots(store)
    if not roots:
        return ""
    return min(roots, key=lambda r: (len(r), r))


#### 判断索引中是否含任意模块标记文件 [@380kkm 2026-06-05] ####
def has_module_markers(store) -> bool:
    """当且仅当索引中含任意模块标记文件（``*.uplugin`` / ``*.Build.cs`` / …）时为 True。

    为 False 时，:func:`detect_target_root` 无法把目标与其依赖区分开
    （L1 索引器只存储已配置的源扩展名，故 ``.uplugin`` 等标记通常缺失），
    因此不能信任自动探测，必须显式给出 ``--target-root``。这样可避免把整个仓库
    （含依赖）静默且不可靠地归类为目标。
    """
    return bool(rollup.module_roots(store))


#### 构建 Zoning，未给出 target_root 时自动探测 [@380kkm 2026-06-05] ####
def make_zoning(store, target_root: str | None, dep_roots: list[str] | None) -> Zoning:
    """构建一个 :class:`Zoning`，未给出 ``target_root`` 时自动探测。

    ``dep_roots`` 会被规范化、去重，并按最长优先排序，使最具体的依赖模块在
    :func:`dependency_label` 中胜出。可提供多个不同的依赖源。
    """
    pr = norm_root(target_root) if target_root is not None else detect_target_root(store)
    ers = sorted({norm_root(e) for e in (dep_roots or []) if norm_root(e)},
                 key=lambda r: (-len(r), r))
    return Zoning(target_root=pr, dep_roots=tuple(ers))


#### 把定义文件路径分类为 target 或 dependency [@380kkm 2026-06-05] ####
def zone_of_path(path: str | None, z: Zoning) -> str:
    """把定义文件路径分类为 ``target`` 或 ``dependency``（可靠的包含判定）。

    一个符号是 TARGET 当且仅当其规范化路径等于 ``target_root`` 或以
    ``target_root + '/'`` 开头。``target_root == ""`` ⇒ 一切皆为目标。
    缺失路径（无文件）保守地归为 DEPENDENCY。
    """
    if path is None:
        return DEPENDENCY
    p = _NORM(path)
    pr = z.target_root
    if pr == "":
        return TARGET
    if p == pr or p.startswith(pr + "/"):
        return TARGET
    return DEPENDENCY


#### 为依赖侧符号的文件生成人类可读标签 [@380kkm 2026-06-05] ####
def dependency_label(path: str, z: Zoning) -> str:
    """为依赖侧符号的文件生成人类可读标签：``<dep_root>::<basename>``。

    使用最长匹配的 ``dep_root`` 前缀（根已按最长优先排序）；
    无依赖根匹配时退化为裸文件名。
    """
    p = _NORM(path or "")
    base = p.rsplit("/", 1)[-1]
    # 已按最长优先排序
    for er in z.dep_roots:
        if er and (p == er or p.startswith(er + "/")):
            return f"{er}::{base}"
    return base
