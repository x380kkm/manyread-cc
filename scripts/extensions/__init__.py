# audience: internal
# extensions
"""manyread 扩展发现 —— 通用、与具体扩展无关的注册机制。

通用核心只保留与任何具体领域无关的语言/查询/校验机制；可选的领域扩展（如 UE 资产 DSL）
经本包按需挂入。一个扩展是 scripts/extensions/<name>/ 下的一个包，导出两个钩子：
``register_ingest(reg)`` 只触碰纯 stdlib 的摄取接缝（默认扩展名预设、schema 目录、命令
文档），无 tree-sitter 的环境（如 L1 索引器）也能调用；``register_enrich(reg)`` 把语言
文法、.scm 查询目录与校验 pass 接到 enrich 世界，其 import 在钩子体内延迟进行。

``run_discovery_ingest(cfg)`` 只应用摄取钩子；``run_discovery(cfg)`` 应用两者。二者都读取
``config.active_extensions(cfg)``，对同一进程幂等（已应用的扩展不重复挂入）。``reset()``
清空守卫并把所有被改动的核心注册表还原为通用初始态，供测试隔离。本文件不含任何具体扩展
的字符串，模块顶层也不 import 任何依赖 tree-sitter 的模块。
"""
from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path

from lib import config


#### 经文件路径加载 manyscan 适配器模块并把适配器加入其注册表 [@380kkm 2026-06-05] ####
def _ms_register_adapter(adapter) -> None:
    """manyscan 自带独立的 ``lib`` 命名空间，无法直接从本包 import；按路径懒加载，
    仅在某扩展真正注册适配器时才触及它。"""
    import importlib.util
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    ms_lib = os.path.normpath(os.path.join(here, "..", "manyscan", "lib"))
    if ms_lib not in sys.path:
        sys.path.insert(0, ms_lib)
    p = os.path.join(ms_lib, "adapters", "__init__.py")
    spec = importlib.util.spec_from_file_location("_ms_adapters", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ms_adapters"] = mod
    spec.loader.exec_module(mod)
    mod.register_adapter(adapter)


#### 扩展把自身能力接到核心接缝上的单一接收面 [@380kkm 2026-06-05] ####
class Registry:
    #### 注册一种语言及其文法、并把扩展名路由到它（enrich 接缝，体内延迟 import） [@380kkm 2026-06-05] ####
    def add_lang(self, ext: str, lang: str, grammar: str) -> None:
        from enrich import langreg
        _capture_enrich_base()
        langreg.register_lang(lang, grammar)
        langreg.register_ext(ext, lang)

    #### 把若干扩展名登记为某语言的默认扩展名预设（纯 stdlib 接缝） [@380kkm 2026-06-05] ####
    def add_default_exts(self, lang: str, exts: list[str]) -> None:
        cur = config.LANG_EXTS.setdefault(lang, [])
        for e in exts:
            if e not in cur:
                cur.append(e)

    #### 追加一个 .scm 查询目录（内置之后、项目覆盖之前加载；enrich 接缝） [@380kkm 2026-06-05] ####
    def add_scm_dir(self, path) -> None:
        from enrich import query
        p = Path(path)
        if p not in query.EXTRA_SCM_DIRS:
            query.EXTRA_SCM_DIRS.append(p)

    #### 追加一个 schema 目录 [@380kkm 2026-06-05] ####
    def add_schema_dir(self, path) -> None:
        self.schema_dirs.append(Path(path))

    #### 注册某语言的结构/语义校验 pass（顺序原样保留；enrich 接缝） [@380kkm 2026-06-05] ####
    def register_passes(self, lang: str, structural=None, semantic=None) -> None:
        import dsl_validate
        dsl_validate.register_passes(lang, structural=structural, semantic=semantic)

    #### 登记一份扩展提供的命令文档路径 [@380kkm 2026-06-05] ####
    def add_command(self, md) -> None:
        self.commands.append(Path(md))

    #### 把一个 manyscan 来源适配器加入注册表（绝不替换默认适配器） [@380kkm 2026-06-05] ####
    def register_adapter(self, adapter) -> None:
        _ms_register_adapter(adapter)

    def __init__(self) -> None:
        self.schema_dirs: list[Path] = []
        self.commands: list[Path] = []
#### /Registry ####


# 进程级单例：摄取/enrich 两级已应用守卫 + 缺失报错守卫 + 共享 Registry
_REGISTRY = Registry()
_APPLIED_INGEST: set[str] = set()
_APPLIED_ENRICH: set[str] = set()
_MISSING: set[str] = set()

# 通用初态快照：config 级在 import 时拍下；langreg 级在首次 enrich 改动前延迟拍下
_BASE_LANG_EXTS = copy.deepcopy(config.LANG_EXTS)
_ENRICH_BASE: dict | None = None


#### 首次 enrich 改动前拍下 langreg 通用初态（reset() 据此还原） [@380kkm 2026-06-05] ####
def _capture_enrich_base() -> None:
    global _ENRICH_BASE
    if _ENRICH_BASE is None:
        from enrich import langreg
        _ENRICH_BASE = {
            "lang_for_ext": copy.deepcopy(langreg.LANG_FOR_EXT),
            "supported_langs": list(langreg.SUPPORTED_LANGS),
            "pack_name": copy.deepcopy(langreg._PACK_NAME),
        }


#### 把一个 ProjectConfig 或扩展名列表归一化为扩展名列表 [@380kkm 2026-06-05] ####
def _names_of(cfg_or_names) -> list[str]:
    if isinstance(cfg_or_names, (list, tuple)):
        return [str(x) for x in cfg_or_names]
    return config.active_extensions(cfg_or_names)


#### import 一个扩展包；缺失时向 stderr 报一行（每进程每名一次）并返回 None [@380kkm 2026-06-05] ####
def _load_extension(name: str):
    try:
        return importlib.import_module(f"extensions.{name}")
    except ImportError as exc:  # noqa: BLE001
        if name not in _MISSING:
            _MISSING.add(name)
            print(f"manyread: extension {name!r} not found ({exc})", file=sys.stderr)
        return None


#### 解析活动扩展并应用其摄取钩子（纯 stdlib，L1 索引器可用）；幂等 [@380kkm 2026-06-05] ####
def run_discovery_ingest(cfg_or_names) -> Registry:
    for name in _names_of(cfg_or_names):
        if name in _APPLIED_INGEST:
            continue
        mod = _load_extension(name)
        if mod is None:
            continue
        mod.register_ingest(_REGISTRY)
        _APPLIED_INGEST.add(name)
    return _REGISTRY


#### 解析活动扩展并应用其全部钩子（摄取 + enrich）；幂等 [@380kkm 2026-06-05] ####
def run_discovery(cfg_or_names) -> Registry:
    names = _names_of(cfg_or_names)
    run_discovery_ingest(names)
    for name in names:
        if name in _APPLIED_ENRICH:
            continue
        mod = _load_extension(name)
        if mod is None:
            continue
        mod.register_enrich(_REGISTRY)
        _APPLIED_ENRICH.add(name)
    return _REGISTRY


#### 清空守卫并把核心注册表还原为通用初态（测试隔离用） [@380kkm 2026-06-05] ####
def reset() -> None:
    _APPLIED_INGEST.clear()
    _APPLIED_ENRICH.clear()
    config.LANG_EXTS.clear()
    config.LANG_EXTS.update(copy.deepcopy(_BASE_LANG_EXTS))
    # enrich 侧仅在真被改动过（快照已拍）时就地还原，使既有 from-import 绑定一并看见
    if _ENRICH_BASE is not None:
        from enrich import langreg
        langreg.LANG_FOR_EXT.clear()
        langreg.LANG_FOR_EXT.update(copy.deepcopy(_ENRICH_BASE["lang_for_ext"]))
        langreg.SUPPORTED_LANGS[:] = list(_ENRICH_BASE["supported_langs"])
        langreg._PACK_NAME.clear()
        langreg._PACK_NAME.update(copy.deepcopy(_ENRICH_BASE["pack_name"]))
    # 仅清已 import 的模块（reset 自身不得引入 tree-sitter 依赖）
    query = sys.modules.get("enrich.query")
    if query is not None:
        query.EXTRA_SCM_DIRS.clear()
    dv = sys.modules.get("dsl_validate")
    if dv is not None:
        dv.STRUCTURAL_PASSES.clear()
        dv.SEMANTIC_PASSES.clear()
    _REGISTRY.schema_dirs.clear()
    _REGISTRY.commands.clear()
