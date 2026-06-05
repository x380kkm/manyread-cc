# audience: internal
# extensions.ue._modload
"""ue 扩展内部的纯 stdlib 模块按路径加载器。

manyscan 自带独立的 ``lib`` 命名空间、无法直接 import；ue 的若干工具（link_source、
validate_passes）需按文件路径把 manyscan 的模块加载到私有别名下。本助手只依赖 stdlib，
故 import 它绝不会拖入 tree-sitter，也不反向 import manyscan 的规范实现（守 L1 边界与
依赖方向）。
"""
from __future__ import annotations

import importlib.util
import sys


#### 按文件路径在私有别名下加载模块（exec 前先注册到 sys.modules） [@380kkm 2026-06-05] ####
def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    # exec 前先注册，使模块内的自引用可解析
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
#### /按文件路径在私有别名下加载模块 ####
