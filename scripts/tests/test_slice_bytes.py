# /// script
# requires-python = ">=3.12"
# ///
# audience: internal
# tests.test_slice_bytes
"""slice_bytes 按 UTF-8 字节偏移切 content，配符号表的 start_byte/end_byte。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入路径
sys.path.insert(0, os.path.dirname(HERE))
from lib import db  # noqa: E402


#### 含中文注释时，按字节偏移取符号片段对齐而内建 substr 错位 [@380kkm 2026-06-16] ####
def test_slice_bytes_aligns_where_substr_misaligns():
    src = "# 中文注释占多字节\ndef foo():\n    return 1\n"
    start_byte = src.encode("utf-8").index(b"def")
    conn = db.connect(":memory:")
    try:
        # slice_bytes 按字节切，取到符号开头
        got = conn.execute(
            "SELECT slice_bytes(?, ?, 3)", (src, start_byte)
        ).fetchone()[0]
        assert got == "def"
        # 内建 substr 按字符计数，同样的字节偏移会错位
        miss = conn.execute(
            "SELECT substr(?, ?, 3)", (src, start_byte + 1)
        ).fetchone()[0]
        assert miss != "def"
    finally:
        conn.close()


#### 纯 ASCII 内容下字节偏移与字符偏移一致 [@380kkm 2026-06-16] ####
def test_slice_bytes_ascii_matches_offsets():
    src = "def foo():\n    return 1\n"
    start_byte = src.encode("utf-8").index(b"return")
    conn = db.connect(":memory:")
    try:
        got = conn.execute(
            "SELECT slice_bytes(?, ?, 6)", (src, start_byte)
        ).fetchone()[0]
        assert got == "return"
    finally:
        conn.close()


#### 用 end_byte-start_byte 取出整个符号体 [@380kkm 2026-06-16] ####
def test_slice_bytes_extracts_full_symbol_span():
    src = "# 头注\ndef foo():\n    return 1\n"
    raw = src.encode("utf-8")
    start_byte = raw.index(b"def")
    end_byte = raw.index(b"return 1") + len(b"return 1")
    conn = db.connect(":memory:")
    try:
        got = conn.execute(
            "SELECT slice_bytes(?, ?, ?)", (src, start_byte, end_byte - start_byte)
        ).fetchone()[0]
        assert got == "def foo():\n    return 1"
    finally:
        conn.close()


#### None 内容返回 None，越界与负偏移被夹到合法范围 [@380kkm 2026-06-16] ####
def test_slice_bytes_edge_inputs():
    conn = db.connect(":memory:")
    try:
        assert conn.execute("SELECT slice_bytes(NULL, 0, 3)").fetchone()[0] is None
        # 负偏移夹到 0
        assert conn.execute("SELECT slice_bytes('abc', -5, 2)").fetchone()[0] == "ab"
        # 越界长度只取到末尾
        assert conn.execute("SELECT slice_bytes('abc', 1, 99)").fetchone()[0] == "bc"
        # length 为 NULL 取到末尾
        assert conn.execute("SELECT slice_bytes('abc', 1, NULL)").fetchone()[0] == "bc"
    finally:
        conn.close()
