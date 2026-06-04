# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.stores —— 对 manyread 存储库的只读访问。

manyscan 构建于 manyread 之上。它从不重新声明 manyread 的 schema 或存储布局；而是
按文件路径（以别名化的模块名）加载 manyread 自己的 ``lib/config.py`` 与 ``lib/db.py``，
这样 manyread 对其存储模型所做的任何改动都会自动传导到这里。对存储库 ``source.db``
的每次访问都是只读的（``file:...?mode=ro``）。

可作为冒烟测试独立运行::

    uv run --python 3.12 scripts/lib/stores.py --list
    uv run --python 3.12 scripts/lib/stores.py --root W:/3dgs/references/DrivingForward
"""
from __future__ import annotations

import glob
import importlib.util
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


#### 解析 manyread 插件的 scripts/ 目录（兼容性骨架的所在） [@380kkm 2026-06-05] ####
def manyread_scripts_dir() -> Path:
    """解析 manyread 插件的 ``scripts/`` 目录（其中含 ``lib/config.py``）。

    优先遵从 ``MANYSCAN_MANYREAD`` 覆盖（插件根或其 ``scripts/``）；否则按文档化的缓存
    glob ``~/.claude/plugins/cache/*/manyread/*/scripts`` 选取版本号最高的已安装插件。
    """
    # 插件内（已并入 manyread）：manyread 的 lib 是位于 scripts/lib 的同级目录。
    # 本文件是 scripts/manyscan/lib/stores.py，故 parents[2] == scripts/。
    # 此同仓分支是正常路径；下面的 env + cache-glob 仅作为从独立 checkout 运行 manyscan
    # 时的回退。
    in_plugin = Path(__file__).resolve().parents[2]
    if not os.environ.get("MANYSCAN_MANYREAD") and (in_plugin / "lib" / "config.py").is_file():
        return in_plugin

    env = os.environ.get("MANYSCAN_MANYREAD")
    if env:
        p = Path(env)
        cand = p / "scripts" if (p / "scripts").is_dir() else p
        if (cand / "lib" / "config.py").is_file():
            return cand
        raise FileNotFoundError(f"MANYSCAN_MANYREAD={env} has no lib/config.py")
    pattern = str(Path.home() / ".claude" / "plugins" / "cache" / "*" / "manyread" / "*" / "scripts")
    for cand in reversed(sorted(glob.glob(pattern))):
        if (Path(cand) / "lib" / "config.py").is_file():
            return Path(cand)
    raise FileNotFoundError("could not locate manyread plugin scripts/ (set MANYSCAN_MANYREAD)")


#### 按文件路径以指定名加载一个模块 [@380kkm 2026-06-05] ####
def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    # 在 exec 之前注册：dataclasses + `from __future__ import annotations` 会经
    # sys.modules[cls.__module__] 解析字符串注解，故类体执行期间该模块必须已在
    # sys.modules 中。
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_MR: dict[str, ModuleType] = {}


#### 返回 manyread 的 (config, db) 模块（加载一次并缓存） [@380kkm 2026-06-05] ####
def manyread_lib() -> tuple[ModuleType, ModuleType]:
    """返回 manyread 的 ``(config, db)`` 模块，加载一次并缓存。

    按文件路径以别名化的名字加载，使 manyscan 自己的 ``lib`` 包永不被遮蔽。
    ``config.py`` / ``db.py`` 仅依赖标准库且自包含。
    """
    if "config" not in _MR:
        libdir = manyread_scripts_dir() / "lib"
        _MR["config"] = _load_module("manyread_config", libdir / "config.py")
        _MR["db"] = _load_module("manyread_db", libdir / "db.py")
    return _MR["config"], _MR["db"]


#### 一个 manyread 存储库：其 manyread/ 目录、db、别名与源码根 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class StoreInfo:
    store: Path
    db_path: Path
    alias: str
    root: Path


#### 从存储库目录构造 StoreInfo [@380kkm 2026-06-05] ####
def _info_from_store_dir(store: Path, alias: str | None = None, root: Path | None = None) -> StoreInfo:
    store = Path(store)
    return StoreInfo(
        store=store,
        db_path=store / "source.db",
        alias=alias or store.parent.name,
        root=Path(root) if root else store.parent,
    )


#### 列出 manyread hub 中注册的所有存储库 [@380kkm 2026-06-05] ####
def list_stores() -> list[StoreInfo]:
    """manyread hub（``~/.manyread/stores.json``）中注册的所有存储库。"""
    mr_config, _ = manyread_lib()
    out = [
        _info_from_store_dir(Path(s), info.get("alias"), info.get("root"))
        for s, info in mr_config.list_stores().items()
    ]
    out.sort(key=lambda s: s.alias.lower())
    return out


#### 从 --store/--root 或 hub 别名解析单个存储库 [@380kkm 2026-06-05] ####
def resolve(store: str | None = None, root: str | None = None) -> StoreInfo:
    """从显式的 ``--store``/``--root`` 或一个 hub 别名解析出单个存储库。

    委托给 manyread 的 ``resolve_project``，使发现语义保持一致；裸别名（非已存在的
    路径）会与 hub 做匹配。
    """
    if store:
        sp = Path(store)
        # 当作 hub 别名
        if not sp.exists():
            for si in list_stores():
                if si.alias == store:
                    return si
        # 直接的 db 路径
        elif sp.is_file() and sp.name == "source.db":
            return _info_from_store_dir(sp.parent)
        # 含 source.db 的存储库目录
        elif (sp / "source.db").is_file():
            return _info_from_store_dir(sp)
    mr_config, _ = manyread_lib()
    cfg = mr_config.resolve_project(root=root, store=store)
    return _info_from_store_dir(Path(cfg.store), cfg.alias, Path(cfg.root))


#### manyread source.db 的只读句柄（files/symbols/edges/meta） [@380kkm 2026-06-05] ####
class Store:
    """对 manyread ``source.db``（files/symbols/edges/meta）的只读句柄。"""

    #### 以只读模式打开 source.db [@380kkm 2026-06-05] ####
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if not self.db_path.is_file():
            raise FileNotFoundError(f"no manyread index db at {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    #### 由 StoreInfo 构造 Store [@380kkm 2026-06-05] ####
    @classmethod
    def from_info(cls, info: StoreInfo) -> "Store":
        return cls(info.db_path)

    #### 关闭底层连接 [@380kkm 2026-06-05] ####
    def close(self) -> None:
        self.conn.close()

    #### 进入上下文管理器 [@380kkm 2026-06-05] ####
    def __enter__(self) -> "Store":
        return self

    #### 退出上下文管理器并关闭连接 [@380kkm 2026-06-05] ####
    def __exit__(self, *exc) -> None:
        self.close()

    #### 读取 meta 表中某键的值 [@380kkm 2026-06-05] ####
    def meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    #### 统计 files/symbols/edges 的行数 [@380kkm 2026-06-05] ####
    def counts(self) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM files)   AS files, "
            "       (SELECT COUNT(*) FROM symbols) AS symbols, "
            "       (SELECT COUNT(*) FROM edges)   AS edges"
        ).fetchone()
        return {"files": cur["files"], "symbols": cur["symbols"], "edges": cur["edges"]}

    #### 按关系类型汇总边数（降序） [@380kkm 2026-06-05] ####
    def relation_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT relation, COUNT(*) AS n FROM edges GROUP BY relation ORDER BY n DESC"
        ).fetchall()
        return {r["relation"]: r["n"] for r in rows}

    #### 按语言汇总符号数（降序） [@380kkm 2026-06-05] ####
    def lang_summary(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT lang, COUNT(*) AS n FROM symbols GROUP BY lang ORDER BY n DESC"
        ).fetchall()
        return {r["lang"]: r["n"] for r in rows}

    #### 按名字 LIKE 查询符号（带文件路径） [@380kkm 2026-06-05] ####
    def symbols_by_name(self, like: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name LIKE ? ORDER BY f.path LIMIT ?",
            (like, limit),
        ).fetchall()

    #### 取某文件的 (id, path, ext, size, content) 行，缺失返回 None [@380kkm 2026-06-05] ####
    def file(self, file_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT id, path, ext, size, content FROM files WHERE id = ?", (file_id,)
        ).fetchone()

    #### 遍历文件行 (id, path, ext, size)，可按扩展名集合过滤 [@380kkm 2026-06-05] ####
    def iter_files(self, exts: set[str] | None = None) -> Iterator[sqlite3.Row]:
        """逐个产出 (id, path, ext, size) 行，可选地过滤到 ``exts``（带点）。"""
        for row in self.conn.execute("SELECT id, path, ext, size FROM files ORDER BY path"):
            if exts is None or (row["ext"] or "").lower() in exts:
                yield row

    #### 按 id 取符号（连接其文件路径），缺失返回 None [@380kkm 2026-06-05] ####
    def symbol(self, symbol_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id = ?",
            (symbol_id,),
        ).fetchone()

    #### 按精确名跨所有文件查找符号（供跨文件边解析） [@380kkm 2026-06-05] ####
    def symbols_named(self, name: str, kinds: set[str] | None = None,
                      limit: int = 500) -> list[sqlite3.Row]:
        """跨所有文件的精确名符号查找（供跨文件边解析）。"""
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            return self.conn.execute(
                "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line "
                "FROM symbols s JOIN files f ON f.id = s.file_id "
                f"WHERE s.name = ? AND s.kind IN ({placeholders}) ORDER BY f.path LIMIT ?",
                (name, *sorted(kinds), limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT s.id, s.file_id, f.path, s.name, s.kind, s.lang, s.start_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? ORDER BY f.path LIMIT ?",
            (name, limit),
        ).fetchall()


#### CLI 入口：列出存储库或打印单个存储库的概要（冒烟测试） [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="stores.py", description="manyscan store access smoke test")
    ap.add_argument("--list", action="store_true", help="list all hub-registered stores")
    ap.add_argument("--store", default=None, help="store dir / alias")
    ap.add_argument("--root", default=None, help="source root to discover the store from")
    args = ap.parse_args(argv)

    try:
        print(f"# manyread scripts: {manyread_scripts_dir()}", file=sys.stderr)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list or (not args.store and not args.root):
        stores = list_stores()
        print(f"# {len(stores)} store(s) in hub")
        for si in stores:
            print(f"  {si.alias:<30} {si.db_path}")
        return 0

    si = resolve(store=args.store, root=args.root)
    with Store.from_info(si) as st:
        print(json.dumps({
            "alias": si.alias,
            "root": str(si.root),
            "db": str(si.db_path),
            "enriched_at": st.meta("enriched_at"),
            "counts": st.counts(),
            "langs": st.lang_summary(),
            "relations": st.relation_summary(),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
