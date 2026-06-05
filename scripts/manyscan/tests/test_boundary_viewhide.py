# audience: internal
# manyscan.tests.test_boundary_viewhide
"""manyscan boundary view_hide 测试 —— 默认隐藏键计算 + CLI --ignore/提交键自动发现。

覆盖：_default_hidden_keys 的排序确定性、label/尾段/fnmatch 并集语义、仅作用依赖侧、
boundary --ignore 烘焙 HIDDEN、提交在 manyread.json['view_hide'] 的键自动发现、--ignore
覆盖提交键，以及 rollup.roots_by_len 的全序。共享 helper（_zoned_hub_graph/_json_dumps_sorted）
取自 conftest。
"""
from __future__ import annotations

import scan

from conftest import _json_dumps_sorted, _zoned_hub_graph

from lib import render, stores


#### _default_hidden_keys 返回排序、确定的列表，命中名字匹配与高扇入节点 [@380kkm 2026-06-05] ####
def test_default_hidden_keys_sorted_deterministic():
    g = _zoned_hub_graph()
    # 'Hub' 是节点 id 'h'（fan_in=3）的 label；min_fan_in=3 也会命中它。
    keys = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    # 已排序
    assert keys == sorted(keys)
    # Hub 同时被名字与 fan_in 命中
    assert "h" in keys
    again = scan._default_hidden_keys(g, {"names": ["Hub"], "min_fan_in": 3})
    # 确定
    assert keys == again
    # 烘焙出的 HIDDEN 常量反映已排序的列表
    out = render.to_html(g, default_hidden=keys)
    assert "const HIDDEN=" + _json_dumps_sorted(keys) in out


#### label 或尾段匹配 + fnmatch 模式（并集语义） [@380kkm 2026-06-05] ####
def test_default_hidden_keys_segment_and_pattern(tmp_path):
    from lib.graph import Edge, Graph, Node
    g = Graph()
    # 裸名外部节点
    g.add_node(Node("amb:FString", "ambiguous", label="FString"))
    # 限定名内部节点
    g.add_node(Node("s9", "class", label="Outer::Inner::FString"))
    # 模式命中目标
    g.add_node(Node("dep:TArrayView", "external", label="TArrayView"))
    # 不受影响
    g.add_node(Node("s10", "class", label="Keep"))
    g.add_edge(Edge("s10", "amb:FString", "uses_type"))
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "patterns": ["TArray*"]})
    # 裸 label 匹配
    assert "amb:FString" in keys
    # 限定 label 的尾段匹配
    assert "s9" in keys
    # fnmatch 模式
    assert "dep:TArrayView" in keys
    # 未匹配
    assert "s10" not in keys


#### 在分区图中 view_hide 仅作用于依赖侧，绝不默认隐藏目标符号 [@380kkm 2026-06-05] ####
def test_default_hidden_keys_engine_side_only():
    from lib.graph import Edge, Graph, Node
    g = Graph()
    g.add_node(Node("dep:FString", "external", label="FString", attrs={"zone": "dependency"}))
    # 同名，但在目标侧
    g.add_node(Node("s1", "class", label="FString", attrs={"zone": "target"}))
    # 高扇入的目标枢纽
    g.add_node(Node("hub", "class", label="Hub", attrs={"zone": "target"}))
    for s in ("a", "b", "c"):
        g.add_node(Node(s, "class", label=s, attrs={"zone": "target"}))
        # hub 的 fan_in = 3
        g.add_edge(Edge(s, "hub", "uses_type"))
    keys = scan._default_hidden_keys(g, {"names": ["FString"], "min_fan_in": 2})
    # 依赖侧名字匹配 -> 隐藏
    assert "dep:FString" in keys
    # 同名但在目标侧 -> 受保护
    assert "s1" not in keys
    # 高扇入的目标枢纽 -> 受保护（min_fan_in 只作用于依赖侧）
    assert "hub" not in keys


#### boundary --ignore <裸文件> 把命中的 id 烘焙进排序后的 const HIDDEN [@380kkm 2026-06-05] ####
def test_cli_boundary_ignore_bakes_hidden(boundary_store, capsys, tmp_path):
    ig = tmp_path / "ignore.json"
    # 目标符号 Foo（id s1）
    ig.write_text('{"names": ["Foo"]}', encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    # Foo -> s1 默认隐藏
    assert "const HIDDEN=" in out and '"s1"' in out


#### 无 --ignore 且无提交的 view_hide 时无 HIDDEN 行，两次渲染逐字节相同 [@380kkm 2026-06-05] ####
def test_cli_boundary_no_config_byte_identical(boundary_store, capsys):
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    a = capsys.readouterr().out
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    b = capsys.readouterr().out
    assert a == b
    # 受门控的 HIDDEN 行不出现在常量块里
    consts = a[a.index("const DATA="):a.index('<script id="ms-boot">')]
    assert "const HIDDEN=" not in consts


#### 提交在 manyread.json['view_hide'] 的键在渲染时自动发现（无需标志） [@380kkm 2026-06-05] ####
def test_cli_boundary_committed_view_hide_autodiscovered(boundary_store, capsys):
    import json as _json

    # <tmp>/manyread/
    store_dir = boundary_store.parent
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "languages": [], "view_hide": {"names": ["Foo"]}}),
        encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html"])
    assert rc == 0
    out = capsys.readouterr().out
    # Foo 经提交的键被自动隐藏
    assert "const HIDDEN=" in out and '"s1"' in out


#### --ignore 优先于提交的 view_hide 键（不同的匹配） [@380kkm 2026-06-05] ####
def test_cli_boundary_ignore_overrides_committed(boundary_store, capsys, tmp_path):
    import json as _json

    store_dir = boundary_store.parent
    (store_dir / "manyread.json").write_text(
        _json.dumps({"alias": "t", "view_hide": {"names": ["Foo"]}}), encoding="utf-8")
    ig = tmp_path / "ig.json"
    # Core -> s3
    ig.write_text('{"view_hide": {"names": ["Core"]}}', encoding="utf-8")
    rc = scan.main(["boundary", "--store", str(boundary_store), "--target-root", "plugin",
                    "--format", "html", "--ignore", str(ig)])
    assert rc == 0
    out = capsys.readouterr().out
    consts = out[out.index("const HIDDEN="):out.index('<script id="ms-boot">')]
    # Core 被隐藏（来自 --ignore）
    assert '"s3"' in consts
    # Foo 未被隐藏（提交的键被覆盖）
    assert '"s1"' not in consts


#### rollup.roots_by_len 在长度相等时按字典序断绑（确定性） [@380kkm 2026-06-05] ####
def test_roots_by_len_total_order(module_store):
    from lib import rollup
    with stores.Store(module_store) as st:
        roots = rollup.roots_by_len(st)
        # modA 与 modB 等长 -> 必须按 (-len, str) 顺序
        assert roots == sorted(roots, key=lambda r: (-len(r), r))
