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
    """``target_root`` 是规范化、去尾斜杠的目录前缀，界定 TARGET（``""`` 表示整个仓库）。

    ``dep_roots`` 仅作依赖侧标注/分组提示，不改变目标判定（符号为依赖当且仅当不在
    ``target_root`` 之下）。
    """

    target_root: str
    # 规范化、按最长优先排序
    dep_roots: tuple[str, ...] = ()


#### 规范化根路径：归一斜杠、去前导 ./、去尾部 / [@380kkm 2026-06-05] ####
def norm_root(p: str) -> str:
    return _NORM(p or "").rstrip("/")


#### 自动探测目标根：最短的模块根（*.uplugin / *.Build.cs / …） [@380kkm 2026-06-05] ####
def detect_target_root(store) -> str:
    """按 ``(len, str)`` 取 ``min``；无模块标记文件时返回 ``""``（整个仓库）。"""
    roots = rollup.module_roots(store)
    if not roots:
        return ""
    return min(roots, key=lambda r: (len(r), r))


#### 判断索引中是否含任意模块标记文件 [@380kkm 2026-06-05] ####
def has_module_markers(store) -> bool:
    return bool(rollup.module_roots(store))


#### 构建 Zoning，未给出 target_root 时自动探测 [@380kkm 2026-06-05] ####
def make_zoning(store, target_root: str | None, dep_roots: list[str] | None) -> Zoning:
    """``dep_roots`` 被规范化、去重，并按最长优先排序。"""
    pr = norm_root(target_root) if target_root is not None else detect_target_root(store)
    ers = sorted({norm_root(e) for e in (dep_roots or []) if norm_root(e)},
                 key=lambda r: (-len(r), r))
    return Zoning(target_root=pr, dep_roots=tuple(ers))


#### 把定义文件路径分类为 target 或 dependency [@380kkm 2026-06-05] ####
def zone_of_path(path: str | None, z: Zoning) -> str:
    """规范化路径等于 ``target_root`` 或以 ``target_root + '/'`` 开头即 TARGET；``target_root == ""`` ⇒ 全为目标；缺失路径归 DEPENDENCY。"""
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
    """用最长匹配的 ``dep_root`` 前缀生成 ``<dep_root>::<basename>``；无匹配时退化为裸文件名。"""
    p = _NORM(path or "")
    base = p.rsplit("/", 1)[-1]
    # 已按最长优先排序
    for er in z.dep_roots:
        if er and (p == er or p.startswith(er + "/")):
            return f"{er}::{base}"
    return base
