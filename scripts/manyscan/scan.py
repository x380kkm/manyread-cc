# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan CLI —— 对 manyread 存储库做有界、按需驱动的依赖扫描。

提供开箱即用的子命令，让你调用工具而非手写查询：

    uv run --python 3.12 scripts/scan.py list-stores
    uv run --python 3.12 scripts/scan.py scan <seed>    --store <dir|alias> [opts]
    uv run --python 3.12 scripts/scan.py analyze <seed> --store <dir|alias> [opts]
    uv run --python 3.12 scripts/scan.py export <seed>  --store <dir|alias> [opts]

``scan`` 打印有界的依赖切片（json/mermaid/dot/text）；``analyze`` 打印重构度量；
``export`` 默认输出 graphviz dot。常用选项：
``--max-nodes N --depth D --dir out|in|both --level file|dir|module``。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import analyze, boundary, cache, render, rollup, scope, stores  # noqa: E402
from lib.graph import Budget  # noqa: E402


#### 从命令行参数解析出目标存储库 [@380kkm 2026-06-05] ####
def _store_info(args) -> stores.StoreInfo:
    target = args.store or args.alias
    if not target and not args.root:
        raise SystemError("specify --store <dir|alias>, --alias <name>, or --root <path>")
    return stores.resolve(store=target, root=args.root)


#### 从命令行参数构造遍历预算 [@380kkm 2026-06-05] ####
def _budget(args) -> Budget:
    return Budget(max_nodes=args.max_nodes, max_depth=args.depth, direction=args.dir)


#### 向 stdout 写一行文本（必要时补换行） [@380kkm 2026-06-05] ####
def _emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


#### list-stores 子命令：列出 hub 注册的存储库 [@380kkm 2026-06-05] ####
def cmd_list(args) -> int:
    for si in stores.list_stores():
        print(f"{si.alias:<30} {si.db_path}")
    return 0


#### scan/export 子命令：打印某 seed 的有界依赖切片 [@380kkm 2026-06-05] ####
def cmd_scan(args) -> int:
    info = _store_info(args)
    budget = _budget(args)
    with stores.Store(info.db_path) as st:
        if args.format == "json" and args.level == "file":
            data, _hit = cache.cached_scan(st, args.seed, budget, alias=info.alias,
                                           use_cache=not args.no_cache)
            if not data["nodes"]:
                print(f"warning: seed {args.seed!r} resolved to no nodes", file=sys.stderr)
            _emit(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            g = scope.scan(st, args.seed, budget, alias=info.alias)
            if not g.nodes:
                print(f"warning: seed {args.seed!r} resolved to no nodes", file=sys.stderr)
            if args.level != "file":
                g = rollup.rollup(g, args.level, store=st)
            _emit(render.render(g, args.format))
    return 0
#### /cmd_scan ####


#### analyze 子命令：打印某 seed 切片的重构度量 [@380kkm 2026-06-05] ####
def cmd_analyze(args) -> int:
    info = _store_info(args)
    budget = _budget(args)
    with stores.Store(info.db_path) as st:
        g = scope.scan(st, args.seed, budget, alias=info.alias)
        if not g.nodes:
            print(f"warning: seed {args.seed!r} resolved to no nodes", file=sys.stderr)
        if args.level != "file":
            g = rollup.rollup(g, args.level, store=st)
        m = analyze.metrics(g)
        _emit(render.to_json(m) if args.format == "json" else render.metrics_text(m))
    return 0
#### /cmd_analyze ####


#### 由 view-hide 配置算出默认隐藏的节点 id（确定性排序列表） [@380kkm 2026-06-05] ####
def _default_hidden_keys(g, vh: dict) -> list[str]:
    """由已解析的 view_hide 配置算出默认隐藏的节点 id 的确定性排序列表。

    并集语义 —— 满足以下任一条件即默认隐藏：
      * 其 label（限定名）在 ``names`` 中，或其末段 ``::`` 片段在 ``names`` 中
      * 对 ``patterns`` 中任一 p，fnmatch(label, p) 或 fnmatch(segment, p)
      * ``min_fan_in`` 非 None 且该节点的 fan_in >= min_fan_in

    仅作用于依赖侧：在边界图中（任一节点带 target/dependency 区域）只有
    ``dependency`` 区域的节点有资格 —— 配置永远不能默认隐藏 target/内部符号
    （你绝不想丢掉正在分析的代码）。非边界图（无区域）按原逻辑匹配。

    同时匹配 label 与末段片段，可一并命中裸名外部符号（``dep:<name>`` / ``amb:<name>``，
    label == 裸名）与限定的内部符号（``Outer::Inner::FString``）。大小写敏感（C++ 标识符
    本就如此）。复用 ``render._importance`` 取 fan_in（已排序/确定性）。
    """
    names = set(vh.get("names") or [])
    pats = list(vh.get("patterns") or [])
    mfi = vh.get("min_fan_in")
    # {nid: {fan_in, ...}}，复用（不新增度量）
    imp = render._importance(g)
    zoned = any(g.nodes[n].attrs.get("zone") in ("target", "dependency") for n in g.nodes)
    hit: set[str] = set()
    # g.nodes 是 id->Node 的字典
    for nid in g.nodes:
        node = g.nodes[nid]
        if zoned and node.attrs.get("zone") != "dependency":
            # 仅作用于依赖侧：绝不隐藏 target/内部符号
            continue
        label = node.label or nid
        seg = label.rsplit("::", 1)[-1]
        if label in names or seg in names:
            hit.add(nid)
        elif any(fnmatch.fnmatch(label, p) or fnmatch.fnmatch(seg, p) for p in pats):
            hit.add(nid)
        elif mfi is not None and imp.get(nid, {}).get("fan_in", 0) >= mfi:
            hit.add(nid)
    # 排序 => 字节级稳定的烘焙
    return sorted(hit)
#### /_default_hidden_keys ####


#### boundary 子命令：构建并渲染符号级 target↔dependency 边界 [@380kkm 2026-06-05] ####
def cmd_boundary(args) -> int:
    info = _store_info(args)
    with stores.Store(info.db_path) as st:
        # 健全性保护：autodetect 依赖已索引的模块标记（*.uplugin / *.Build.cs）。L1 索引器
        # 只存储已配置的源码扩展名，故这些标记通常未被索引 —— autodetect 这时会返回 "" 并
        # 悄悄把整个仓库（含依赖）都归类为 target，这是不健全的。该情形下要求显式给出
        # --target-root。传 --target-root "" 可故意选择把整个仓库当作 target。
        if args.target_root is None and not boundary.has_module_markers(st):
            print("error: cannot autodetect --target-root (no *.uplugin / *.Build.cs "
                  "markers are indexed; the L1 index only stores source extensions). "
                  "Pass --target-root <dir> explicitly (or --target-root \"\" to treat "
                  "the whole index as the target).", file=sys.stderr)
            return 2
        z = boundary.make_zoning(st, args.target_root, args.dep_root)
        budget = Budget(max_nodes=args.max_nodes, max_depth=max(2, args.depth), direction="out")
        g = boundary.build(st, z, budget, alias=info.alias, dep_depth=args.dep_depth)
        if not g.nodes:
            print(f"warning: no target-zone symbols under target-root "
                  f"{z.target_root!r}", file=sys.stderr)
        # HTML 是单个自包含、可在页内切换的页面：始终输出完整图（两个区域 + 跨界边），由
        # render.to_html 的视图选择器在客户端切换 internal|dependency|both。
        # internal_view / dependency_surface 投影仅保留给静态的非 HTML 格式
        # （json/text/dot）。--layers / --dep-depth 仅影响 html 路径（否则分带不起作用）。
        if args.format == "html":
            band_of, bands_meta = boundary.assign_bands(g, args.layers)
            # 解析已提交/被覆盖的 view-hide 配置（None => v0.6.0 的字节）。
            mr_cfg = stores.manyread_lib()[0]
            vh = mr_cfg.load_view_hide(info.store, Path(args.ignore) if args.ignore else None)
            default_hidden = _default_hidden_keys(g, vh) if vh else None
            # 受开关控制的可折叠 module<->symbol 商图视图。off（默认）=> module_of /
            # modules_meta 保持 None，故不烘焙 module 节点属性、也不烘焙 `const MODULES=`
            # 行 => DATA/consts 字节与 v0.6.2 完全一致。
            module_of = modules_meta = None
            if args.collapse != "off":
                module_of, modules_meta = boundary.assign_modules(
                    g, z, level=args.collapse, store=st, band_of=band_of)
            _emit(render.to_html(g, view=args.view, band_of=band_of,
                                 bands_meta=bands_meta, default_hidden=default_hidden,
                                 module_of=module_of, modules_meta=modules_meta))
        else:
            if args.view == "internal":
                view = boundary.internal_view(g)
            elif args.view == "dependency":
                view = boundary.dependency_surface(g, rollup_modules=args.rollup_dep, store=st)
            else:
                view = g
            _emit(render.render(view, args.format))
    return 0
#### /cmd_boundary ####


#### 为 scan/analyze/export 子命令注册公共参数 [@380kkm 2026-06-05] ####
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("seed", help="symbol / file path / dir / keyword to scan from")
    p.add_argument("--store", default=None, help="store dir, source.db path, or hub alias")
    p.add_argument("--alias", default=None, help="hub alias (synonym for --store)")
    p.add_argument("--root", default=None, help="source root to discover the store from")
    p.add_argument("--max-nodes", dest="max_nodes", type=int, default=200)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--dir", choices=["out", "in", "both"], default="out")
    p.add_argument("--level", choices=["file", "dir", "module"], default="file")
    p.add_argument("--no-cache", dest="no_cache", action="store_true")


#### CLI 入口：解析参数、分派子命令、统一错误处理 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="manyscan",
        description="bounded, demand-driven dependency scans over manyread stores",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-stores", help="list hub-registered manyread stores").set_defaults(func=cmd_list)

    sc = sub.add_parser("scan", help="print the bounded dependency slice for a seed")
    _add_common(sc)
    sc.add_argument("--format", choices=list(render.FORMATS), default="json")
    sc.set_defaults(func=cmd_scan)

    an = sub.add_parser("analyze", help="print refactoring metrics for a seed's slice")
    _add_common(an)
    an.add_argument("--format", choices=["text", "json"], default="text")
    an.set_defaults(func=cmd_analyze)

    ex = sub.add_parser("export", help="export the slice (default: graphviz dot)")
    _add_common(ex)
    ex.add_argument("--format", choices=list(render.FORMATS), default="dot")
    ex.set_defaults(func=cmd_scan)

    pb = sub.add_parser("boundary", aliases=["plugin-boundary"],
                        help="symbol-level target↔dependency boundary")
    pb.add_argument("--store", default=None, help="store dir, source.db path, or hub alias")
    pb.add_argument("--alias", default=None, help="hub alias (synonym for --store)")
    pb.add_argument("--root", default=None, help="source root to discover the store from")
    pb.add_argument("--target-root", "--plugin-root", dest="target_root", default=None,
                    help="target root prefix (default: autodetect nearest *.uplugin/*.Build.cs)")
    pb.add_argument("--dep-root", "--engine-root", dest="dep_root", action="append", default=[],
                    help="dependency root hint for labelling (repeatable; may be MANY sources)")
    pb.add_argument("--max-nodes", dest="max_nodes", type=int, default=4000)
    pb.add_argument("--depth", type=int, default=2, help=">=2: target layer + dependency sink")
    pb.add_argument("--view", choices=["internal", "dependency", "both"], default="both")
    pb.add_argument("--rollup-dep", "--rollup-engine", dest="rollup_dep", action="store_true",
                    help="(dependency view) group dependency targets by module root")
    pb.add_argument("--layers", choices=["flat", "two", "four"], default="four",
                    help="(html only) ordered framed bands: flat=none, two=[target||dependency], "
                         "four=[target-core|target-iface||dep-iface|dep-core]")
    pb.add_argument("--dep-depth", dest="dep_depth", type=int, default=1,
                    help="dependency expansion layers behind the surface (1=surface only; "
                         "2 populates dep-core). Distinct from --depth (the BFS budget).")
    pb.add_argument("--format", choices=list(render.FORMATS), default="html")
    pb.add_argument("--ignore", default=None,
                    help="(html only) view-hide config JSON (names/patterns/min_fan_in, or a "
                         "{view_hide:{...}} wrapper). Default: auto-discover "
                         "manyread.json['view_hide']. Absent + no config => identical to v0.6.0.")
    pb.add_argument("--collapse", choices=["off", "file", "dir"], default="off",
                    help="(html only) collapsible MODULE<->SYMBOL quotient view: off "
                         "(default; byte-identical to v0.6.2) | file (module=file stem, "
                         ".cpp/.h coalesce) | dir (module=parent dir). Default ALL modules "
                         "collapsed; expand per-module from the side panel MODULES section.")
    pb.set_defaults(func=cmd_boundary)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (SystemError, FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
#### /main ####


if __name__ == "__main__":
    raise SystemExit(main())
