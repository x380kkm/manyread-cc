# /// script
# requires-python = ">=3.12"
# ///
"""回归测试：manyread 的 config 与 hub 读取器能容忍 UTF-8 BOM。

Windows 工具（如 PowerShell 的 `Out-File`/`Set-Content`）重写 manyread.json 或 hub 的
stores.json 时默认会加上 UTF-8 BOM。读取器原先用纯 "utf-8"，遇到 BOM 即抛错；由于两个
读取器都吞掉 JSONDecodeError 并返回 {}，这会静默清空配置 / 使每个已注册的存储库不可见。
读取器现改用 "utf-8-sig"（存在 BOM 则剥除，否则与纯 UTF-8 读取完全一致）。

运行：cd scripts && uv run --python 3.12 --with pytest -m pytest tests/test_config_bom.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入路径，使 `from lib import config` 可解析
sys.path.insert(0, os.path.dirname(HERE))
from lib import config  # noqa: E402

_BOM = b"\xef\xbb\xbf"


#### 验证 _read_json 能读取带 BOM 前缀的 JSON [@380kkm 2026-06-05] ####
def test_read_json_tolerates_bom(tmp_path):
    p = tmp_path / "manyread.json"
    p.write_bytes(_BOM + json.dumps({"langs": ["cpp"]}).encode("utf-8"))
    assert config._read_json(p) == {"langs": ["cpp"]}


#### 验证不带 BOM 的纯 UTF-8 读取不受影响 [@380kkm 2026-06-05] ####
def test_read_json_plain_utf8_unaffected(tmp_path):
    p = tmp_path / "manyread.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert config._read_json(p) == {"a": 1}


#### 验证带 BOM 的 hub stores.json 仍能列出其存储库（即此前的确切 bug） [@380kkm 2026-06-05] ####
def test_list_stores_tolerates_bom(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path))
    hub = config.hub_dir()
    hub.mkdir(parents=True, exist_ok=True)
    reg = {"W:/x/manyread": {"alias": "x", "root": "W:/x"}}
    (hub / "stores.json").write_bytes(_BOM + json.dumps(reg).encode("utf-8"))
    got = config.list_stores()
    assert "W:/x/manyread" in got and got["W:/x/manyread"]["alias"] == "x"
