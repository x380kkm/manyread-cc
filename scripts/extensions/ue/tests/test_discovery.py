"""扩展发现机制（scripts/extensions/）的回归测试 —— 注册表复现、还原、启用解析与摄取惰性。"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 把 scripts/ 加入路径（本文件在 scripts/extensions/ue/tests/ 下，上溯三级）
_SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, _SCRIPTS)

import extensions  # noqa: E402
import dsl_validate as V  # noqa: E402
from enrich import langreg as L  # noqa: E402
from lib import config as C  # noqa: E402

_DSL_LANGS = ("matlang", "bplisp", "animlang")


#### 断言核心注册表处于 UE-ON 态且与 v0.8.16 完全一致 [@380kkm 2026-06-05] ####
def _assert_ue_on():
    assert {".matlang": "matlang", ".bplisp": "bplisp",
            ".animlang": "animlang"}.items() <= L.LANG_FOR_EXT.items()
    assert L.SUPPORTED_LANGS == [
        "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
        "java", "gdscript", "matlang", "bplisp", "animlang",
    ]
    assert all(L._PACK_NAME[lang] == "scheme" for lang in _DSL_LANGS)
    assert all(C.LANG_EXTS[lang] == [f".{lang}"] for lang in _DSL_LANGS)
    assert set(V.STRUCTURAL_PASSES) == set(_DSL_LANGS)
    names = {k: [f.__name__ for f in v] for k, v in V.STRUCTURAL_PASSES.items()}
    assert names["matlang"] == ["pass_parse", "pass_matlang_required",
                                "pass_matlang_dup_id", "pass_matlang_dangling",
                                "pass_matlang_cycle"]
    assert names["bplisp"] == ["pass_parse", "pass_bplisp_required", "pass_external_warn"]
    assert names["animlang"] == ["pass_parse", "pass_animlang_required", "pass_external_warn"]
    assert [f.__name__ for f in V.SEMANTIC_PASSES["matlang"]] == ["pass_semantic_schema"]
    assert V.SEMANTIC_PASSES["bplisp"] == [] and V.SEMANTIC_PASSES["animlang"] == []
#### /断言 UE-ON 态 ####


#### 会话级 discovery（conftest）后注册表精确复现 v0.8.16 [@380kkm 2026-06-05] ####
def test_ue_on_reproduces_registries():
    _assert_ue_on()


#### reset 还原通用初态，再次 discovery 完整恢复（往返不漏） [@380kkm 2026-06-05] ####
def test_reset_then_rediscover_round_trip():
    extensions.reset()
    try:
        assert not any(ext in L.LANG_FOR_EXT
                       for ext in (".matlang", ".bplisp", ".animlang"))
        assert not any(lang in L.SUPPORTED_LANGS for lang in _DSL_LANGS)
        assert not any(lang in L._PACK_NAME for lang in _DSL_LANGS)
        assert not any(lang in C.LANG_EXTS for lang in _DSL_LANGS)
        assert V.STRUCTURAL_PASSES == {} and V.SEMANTIC_PASSES == {}
    finally:
        # 恢复会话级 UE-ON 态，后续测试不受影响
        extensions.run_discovery(["ue"])
    _assert_ue_on()


#### 摄取级 discovery 保持纯 stdlib：不触发 enrich / tree-sitter 的 import [@380kkm 2026-06-05] ####
def test_ingest_discovery_stays_stdlib():
    code = (
        f"import sys; sys.path.insert(0, {_SCRIPTS!r}); "
        "import extensions; extensions.run_discovery_ingest(['ue']); "
        "from lib import config; "
        "assert 'matlang' in config.LANG_EXTS; "
        "assert 'enrich.langreg' not in sys.modules; "
        "assert 'tree_sitter' not in sys.modules; "
        "print('ok')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


#### init_store 不写 extensions 键（缺席 = 运行时推断仍然活着） [@380kkm 2026-06-05] ####
def test_init_store_omits_extensions_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "home"))
    store = C.init_store(tmp_path / "p")
    payload = json.loads((store / "manyread.json").read_text(encoding="utf-8"))
    assert "extensions" not in payload


#### active_extensions 解析全路径：显式名单 > profile 别名 > .uproject 推断 [@380kkm 2026-06-05] ####
def test_active_extensions_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYREAD_HOME", str(tmp_path / "home"))
    proj = tmp_path / "p"
    proj.mkdir()
    store = C.init_store(proj, root=proj)
    cfg_file = store / "manyread.json"

    def resolve():
        return C.resolve_project(root=str(proj), store=str(store))

    def rewrite(**kw):
        payload = json.loads(cfg_file.read_text(encoding="utf-8"))
        for k, v in kw.items():
            if v is None:
                payload.pop(k, None)
            else:
                payload[k] = v
        cfg_file.write_text(json.dumps(payload), encoding="utf-8")

    # 无键、无 uproject -> []
    assert C.active_extensions(resolve()) == []
    # 无键、有 uproject -> 推断 ['ue']
    (proj / "Game.uproject").write_text("{}", encoding="utf-8")
    assert C.active_extensions(resolve()) == ["ue"]
    # 显式 [] 硬禁用，即使 uproject 存在
    rewrite(extensions=[])
    assert C.active_extensions(resolve()) == []
    # user 覆盖 shared（两个方向）
    user_file = store / "user" / "config.json"
    user_file.write_text(json.dumps({"extensions": ["ue"]}), encoding="utf-8")
    assert C.active_extensions(resolve()) == ["ue"]
    rewrite(extensions=["ue"])
    user_file.write_text(json.dumps({"extensions": []}), encoding="utf-8")
    assert C.active_extensions(resolve()) == []
    user_file.unlink()
    # profile=='ue' 别名（去掉显式名单与 uproject 后生效）
    rewrite(extensions=None, profile="ue")
    (proj / "Game.uproject").unlink()
    assert C.active_extensions(resolve()) == ["ue"]


#### 裸 --root 索引：通用树不摄取 stray .matlang，UE 树经 uproject 推断摄取 [@380kkm 2026-06-05] ####
def test_bare_index_leak_fixed_and_ue_autodetect(tmp_path):
    # 子进程隔离：本会话的 UE-ON 全局状态不得污染判定
    env = dict(os.environ, MANYREAD_HOME=str(tmp_path / "home"))
    ib = os.path.join(_SCRIPTS, "index_build.py")
    for name, marker, expect in (("plain", None, ["x.py"]),
                                 ("ueproj", "Game.uproject", ["stray.matlang", "x.py"])):
        proj = tmp_path / name
        proj.mkdir()
        (proj / "x.py").write_text("def f():\n    pass\n", encoding="utf-8")
        (proj / "stray.matlang").write_text("(material (id M))", encoding="utf-8")
        if marker:
            (proj / marker).write_text("{}", encoding="utf-8")
        r = subprocess.run([sys.executable, ib, "--init",
                            "--store-at", str(proj), "--root", str(proj)],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        conn = sqlite3.connect(proj / "manyread" / "source.db")
        paths = sorted(p for (p,) in conn.execute("SELECT path FROM files"))
        conn.close()
        assert paths == expect, (name, paths)
