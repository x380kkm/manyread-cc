# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.rollup —— 把文件级切片折叠到目录 / 模块层级。

在 :func:`graph.rollup` 之上包一层 manyscan 的分组（按目录，或按 *module* =
最近的、带有构建标记文件如 ``CMakeLists.txt`` / ``*.Build.cs`` / ``pyproject.toml`` /
``*.uplugin`` 的祖先目录），并且 —— 关键在于 —— 把有界扩展的计量
（``truncated`` / ``frontier`` / ``elided`` / ``depth_bounded``）一并带到折叠后的图上，
把每个被省略的边界计数归属到它所属的分组。这样"在层级 X 返回"就绝不会悄悄丢掉
超预算的尾部。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

from lib import graph, stores
from lib.graph import Graph, Node

_MARKER_NAMES = {
    "cmakelists.txt", "pyproject.toml", "package.json", "cargo.toml",
    "go.mod", "setup.py", "build.gradle",
}


#### 判断 basename 是否为构建标记文件 [@380kkm 2026-06-05] ####
def _is_marker(basename: str) -> bool:
    b = basename.lower()
    return b in _MARKER_NAMES or b.endswith(".build.cs") or b.endswith(".uplugin")


#### 扫描出形似模块根（含构建标记文件）的目录集合 [@380kkm 2026-06-05] ####
def module_roots(store: "stores.Store") -> set[str]:
    """形似模块根（含构建标记文件）的目录集合。

    按 Store 缓存：O(文件数) 的标记扫描只跑一次，而非每次 rollup 调用都跑。
    仓库根处的标记存为 ``""``（空前缀）。
    """
    cached = getattr(store, "_ms_module_roots", None)
    if cached is not None:
        return cached
    roots: set[str] = set()
    for row in store.conn.execute("SELECT path FROM files"):
        p = (row["path"] or "").replace("\\", "/")
        if _is_marker(p.rsplit("/", 1)[-1]):
            roots.add(p.rsplit("/", 1)[0] if "/" in p else "")
    store._ms_module_roots = roots
    return roots
#### /扫描模块根目录集合 ####


#### 模块根，按最长优先再字典序的全序排列 [@380kkm 2026-06-05] ####
def roots_by_len(store: "stores.Store | None") -> list[str]:
    """模块根，按最长优先、再字典序的全序排列。

    最长优先的顺序使 ``_module_of`` 选中最具体的祖先；次级 ``str`` 键消除了当两个根
    长度相同时遍历底层 ``set`` 带来的 hash-seed 不确定性。
    """
    if store is None:
        return []
    return sorted(module_roots(store), key=lambda r: (-len(r), r))
#### /模块根全序排列 ####


#### 取节点的规范化路径 [@380kkm 2026-06-05] ####
def _path_of(node: Node) -> str:
    return (node.label or node.id).replace("\\", "/")


#### 取节点所在目录（根目录归为 (root)） [@380kkm 2026-06-05] ####
def _dir_of(node: Node) -> str:
    parent = PurePosixPath(_path_of(node)).parent.as_posix()
    return parent if parent not in ("", ".") else "(root)"


#### 取节点路径的最近模块根祖先，否则取首段 [@380kkm 2026-06-05] ####
def _module_of(node: Node, roots_by_len: list[str]) -> str:
    """节点路径最近的模块根祖先，否则取顶层路径段。"""
    path = _path_of(node)
    # 最长优先
    for root in roots_by_len:
        # 仓库根标记：兜底所有不在更深根之下的文件
        if root == "":
            return "(root)"
        if path == root or path.startswith(root + "/"):
            return root
    seg = path.split("/", 1)[0]
    return seg or "(root)"
#### /取节点的最近模块根祖先 ####


#### 按层级选出分组函数（dir / module） [@380kkm 2026-06-05] ####
def _group_fn(level: str, store: "stores.Store | None") -> Callable[[Node], str]:
    if level == "dir":
        return _dir_of
    if level == "module":
        # 取全序（长度降序、再 str）使折叠输出跨次运行逐字节一致：module_roots() 是 set，
        # 仅按长度排序会让等长的根停留在 set 遍历（hash-seed）顺序 —— 不确定
        roots = roots_by_len(store) if store else []
        return lambda n: _module_of(n, roots)
    raise ValueError(f"unknown rollup level: {level!r} (use file|dir|module)")
#### /按层级选出分组函数 ####


#### 把图折叠到目标层级，并搬运有界扩展计量 [@380kkm 2026-06-05] ####
def rollup(g: Graph, level: str, store: "stores.Store | None" = None) -> Graph:
    """把 `g` 折叠到 ``level``（``file`` = 恒等，``dir`` 或 ``module``）。

    返回的图带着 `g` 的有界扩展计量，其中每个被省略的边界计数会重新归属到其源
    节点折叠进的那个分组。
    """
    if level == "file":
        return g
    group_of = _group_fn(level, store)
    rolled = graph.rollup(g, group_of=group_of)
    rolled.truncated = g.truncated
    rolled.depth_bounded = g.depth_bounded
    rolled.frontier_depth = g.frontier_depth
    rolled.elided = g.elided
    for node_id, count in g.frontier.items():
        node = g.nodes.get(node_id)
        grp = group_of(node) if node is not None else node_id
        rolled.frontier[grp] = rolled.frontier.get(grp, 0) + count
    return rolled
#### /把图折叠到目标层级 ####
