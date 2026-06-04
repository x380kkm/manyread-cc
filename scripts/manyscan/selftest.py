# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8"]
# ///
"""manyscan selftest — 运行 pytest 套件；有任何失败即以非零码退出。

    uv run --python 3.12 scripts/selftest.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

#### 跑 tests 目录下的 pytest 套件，并以其退出码退出 [@380kkm 2026-06-05] ####
ROOT = Path(__file__).resolve().parent
raise SystemExit(pytest.main(["-q", str(ROOT / "tests")]))
