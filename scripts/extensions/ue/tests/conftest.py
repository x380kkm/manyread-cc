# audience: internal
# extensions.ue.tests.conftest
"""UE 扩展测试套件的 pytest 夹具。

UE pass、.scm 与 schema 都在扩展里；这些测试通过 ``dsl_validate``/``link_source``/
``enrich_treesitter`` 间接消费它们，因此整套测试在会话开始时主动跑一次 UE 扩展发现
（``run_discovery(['ue'])``），结束时 ``reset()`` 还原核心注册表为通用初态——这样合并的
pytest 进程里 UE 状态绝不泄漏给通用套件。通用测试从不调用 discovery。
"""
from __future__ import annotations

import os
import sys

import pytest

# 把 scripts/ 加入 sys.path（本文件在 scripts/extensions/ue/tests/ 下，上溯三级）
_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _SCRIPTS)


#### 整会话开启 UE 扩展，结束后还原核心注册表（全局可变状态隔离） [@380kkm 2026-06-05] ####
@pytest.fixture(scope="session", autouse=True)
def _ue_discovery():
    import extensions
    extensions.run_discovery(["ue"])
    yield
    extensions.reset()
