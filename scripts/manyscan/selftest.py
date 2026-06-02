# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest>=8"]
# ///
"""manyscan selftest — run the pytest suite; exit nonzero on any failure.

    uv run --python 3.12 scripts/selftest.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
raise SystemExit(pytest.main(["-q", str(ROOT / "tests")]))
