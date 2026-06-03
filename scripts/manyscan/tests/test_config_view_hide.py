"""Tests for the committed `view_hide` config loader (manyread's own lib/config.py).

Covers validate_view_hide structural checks, load_view_hide precedence
(--ignore override > manyread.json['view_hide'] > None), wrapped-or-bare --ignore
files, and the loud-vs-silent failure contract (a missing/malformed explicit
--ignore warns; an absent committed key is silent v0.6.0 behavior).
"""
from __future__ import annotations

import json

from lib import stores


def _cfg():
    return stores.manyread_lib()[0]


def _store(tmp_path, view_hide=None, *, write_json=True, raw=None):
    """A bare store dir with a manyread.json (optionally carrying a view_hide key)."""
    store = tmp_path / "manyread"
    store.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (store / "manyread.json").write_text(raw, encoding="utf-8")
    elif write_json:
        payload = {"alias": "t", "languages": [], "exts": []}
        if view_hide is not None:
            payload["view_hide"] = view_hide
        (store / "manyread.json").write_text(json.dumps(payload), encoding="utf-8")
    return store


# --- validate_view_hide ------------------------------------------------------
def test_validate_view_hide_accepts_valid():
    cfg = _cfg()
    assert cfg.validate_view_hide(
        {"version": 1, "names": ["int32", "FString"], "patterns": ["TArray*"], "min_fan_in": 5}
    ) == []
    assert cfg.validate_view_hide({}) == []          # all keys optional


def test_validate_view_hide_rejects_bad():
    cfg = _cfg()
    assert cfg.validate_view_hide({"version": 2})
    assert cfg.validate_view_hide({"names": [1, 2]})
    assert cfg.validate_view_hide({"patterns": "TArray*"})
    assert cfg.validate_view_hide({"min_fan_in": -1})
    assert cfg.validate_view_hide({"min_fan_in": True})     # bool is not an int here
    assert cfg.validate_view_hide({"min_fan_in": "5"})


# --- load_view_hide precedence + shapes --------------------------------------
def test_load_view_hide_absent_is_none(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)                          # manyread.json with NO view_hide key
    assert cfg.load_view_hide(store) is None


def test_load_view_hide_committed_key(tmp_path):
    cfg = _cfg()
    vh = {"version": 1, "names": ["FString"], "min_fan_in": 20}
    store = _store(tmp_path, view_hide=vh)
    got = cfg.load_view_hide(store)
    assert got == vh


def test_load_view_hide_override_wins(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path, view_hide={"names": ["Committed"]})
    ov = tmp_path / "ignore.json"
    ov.write_text(json.dumps({"view_hide": {"names": ["FromFile"]}}), encoding="utf-8")
    got = cfg.load_view_hide(store, ov)
    assert got == {"names": ["FromFile"]}             # --ignore beats committed


def test_load_view_hide_accepts_bare_ignore_file(tmp_path):
    cfg = _cfg()
    store = _store(tmp_path)
    ov = tmp_path / "bare.json"
    ov.write_text(json.dumps({"names": ["int32"], "min_fan_in": 30}), encoding="utf-8")
    got = cfg.load_view_hide(store, ov)
    assert got == {"names": ["int32"], "min_fan_in": 30}


def test_load_view_hide_malformed_returns_none_and_warns(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path, view_hide={"version": 2, "names": ["x"]})
    assert cfg.load_view_hide(store) is None
    assert "malformed view_hide" in capsys.readouterr().err


def test_load_view_hide_missing_ignore_file_warns_loud(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path)
    bogus = tmp_path / "does-not-exist.json"
    assert cfg.load_view_hide(store, bogus) is None
    assert "--ignore file not found" in capsys.readouterr().err


def test_load_view_hide_unknown_key_warns_but_proceeds(tmp_path, capsys):
    cfg = _cfg()
    # 'name' typo (should be 'names') validates clean but would hide nothing silently;
    # we warn so the persistence-loop failure is visible. min_fan_in still takes effect.
    store = _store(tmp_path, view_hide={"name": ["FString"], "min_fan_in": 10})
    got = cfg.load_view_hide(store)
    assert got is not None and got.get("min_fan_in") == 10
    assert "unknown key" in capsys.readouterr().err


def test_load_view_hide_broken_manyread_json_warns(tmp_path, capsys):
    cfg = _cfg()
    store = _store(tmp_path, raw='{"alias": "t", "view_hide": {trailing,}')  # invalid JSON
    assert cfg.load_view_hide(store) is None
    assert "unreadable/empty" in capsys.readouterr().err
