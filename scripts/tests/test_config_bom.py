# /// script
# requires-python = ">=3.12"
# ///
"""manyread 的 config 与 hub 读取器容忍 UTF-8 BOM。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入路径
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


#### 验证带 BOM 的 hub stores.json 仍能列出其存储库 [@380kkm 2026-06-05] ####
def test_list_stores_tolerates_bom(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path))
    hub = config.hub_dir()
    hub.mkdir(parents=True, exist_ok=True)
    reg = {"W:/x/manyread": {"alias": "x", "root": "W:/x"}}
    (hub / "stores.json").write_bytes(_BOM + json.dumps(reg).encode("utf-8"))
    got = config.list_stores()
    assert "W:/x/manyread" in got and got["W:/x/manyread"]["alias"] == "x"
