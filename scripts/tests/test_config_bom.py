# /// script
# requires-python = ">=3.12"
# ///
"""Regression: manyread's config + hub readers tolerate a UTF-8 BOM.

A Windows tool (e.g. PowerShell `Out-File`/`Set-Content`) rewriting manyread.json or
the hub stores.json adds a UTF-8 BOM by default. The readers used plain "utf-8", which
raised on the BOM; since both readers swallow JSONDecodeError and return {}, that
SILENTLY emptied the config / made every registered store invisible. The readers now
use "utf-8-sig" (strips a BOM if present, reads plain UTF-8 identically).

Run: cd scripts && uv run --python 3.12 --with pytest -m pytest tests/test_config_bom.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # scripts/  -> `from lib import config`
from lib import config  # noqa: E402

_BOM = b"\xef\xbb\xbf"


def test_read_json_tolerates_bom(tmp_path):
    p = tmp_path / "manyread.json"
    p.write_bytes(_BOM + json.dumps({"langs": ["cpp"]}).encode("utf-8"))
    assert config._read_json(p) == {"langs": ["cpp"]}


def test_read_json_plain_utf8_unaffected(tmp_path):
    p = tmp_path / "manyread.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert config._read_json(p) == {"a": 1}


def test_list_stores_tolerates_bom(tmp_path, monkeypatch):
    """The exact bug: a BOM'd hub stores.json must still list its stores."""
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path))
    hub = config.hub_dir()
    hub.mkdir(parents=True, exist_ok=True)
    reg = {"W:/x/manyread": {"alias": "x", "root": "W:/x"}}
    (hub / "stores.json").write_bytes(_BOM + json.dumps(reg).encode("utf-8"))
    got = config.list_stores()
    assert "W:/x/manyread" in got and got["W:/x/manyread"]["alias"] == "x"
