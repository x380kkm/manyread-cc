# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# manyscan.scan
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
    names = set(vh.get("names") or [])
    pats = list(vh.get("patterns") or [])
    mfi = vh.get("min_fan_in")
    # 每节点的 fan_in 度量字典
    imp = render._importance(g)
    zoned = any(g.nodes[n].attrs.get("zone") in ("target", "dependency") for n in g.nodes)
    hit: set[str] = set()
    for nid in g.nodes:
        node = g.nodes[nid]
        if zoned and node.attrs.get("zone") != "dependency":
            # 仅隐藏 dependency 区节点
            continue
        label = node.label or nid
        seg = label.rsplit("::", 1)[-1]
        if label in names or seg in names:
            hit.add(nid)
        elif any(fnmatch.fnmatch(label, p) or fnmatch.fnmatch(seg, p) for p in pats):
            hit.add(nid)
        elif mfi is not None and imp.get(nid, {}).get("fan_in", 0) >= mfi:
            hit.add(nid)
    return sorted(hit)
#### /_default_hidden_keys ####


#### boundary 子命令：构建并渲染符号级 target↔dependency 边界 [@380kkm 2026-06-05] ####
def cmd_boundary(args) -> int:
    info = _store_info(args)
    with stores.Store(info.db_path) as st:
        # 无模块标记且未给 --target-root 时拒绝 autodetect
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
        # html 输出完整图，视图切换在客户端进行
        if args.format == "html":
            band_of, bands_meta = boundary.assign_bands(g, args.layers)
            # 解析 view-hide 配置
            mr_cfg = stores.manyread_lib()[0]
            vh = mr_cfg.load_view_hide(info.store, Path(args.ignore) if args.ignore else None)
            default_hidden = _default_hidden_keys(g, vh) if vh else None
            # collapse 关闭时不构建 module 商图
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


#### 从 --modules / --module / manyread.json 解析出 N 路模块规格 [@380kkm 2026-06-05] ####
def _resolve_module_spec(args, info):
    """优先级：--modules 文件（含 --module 内联合并）> manyread.json['modules']（含内联合并）。
    任何来源都没有时返回 None（CLI 据此退出 2）。内联 ``--module NAME=PREFIX[,...]`` 解析出错
    抛 ValueError（由 main 统一处理）。
    """
    inline = [boundary.parse_inline_module(s) for s in (args.module or [])]
    mr_cfg = stores.manyread_lib()[0]
    doc = mr_cfg.load_modules(info.store, Path(args.modules) if args.modules else None)
    if doc is None and not inline:
        return None
    return boundary.make_module_spec(doc, inline=inline, fallback=args.fallback)


#### modules 子命令：构建并渲染符号级 N 路模块分区 [@380kkm 2026-06-05] ####
def cmd_modules(args) -> int:
    info = _store_info(args)
    spec = _resolve_module_spec(args, info)
    if spec is None:
        print("error: no module spec given. Provide --modules <file>, one or more "
              "--module NAME=PREFIX[,PREFIX...], or a manyread.json['modules'] block.",
              file=sys.stderr)
        return 2
    with stores.Store(info.db_path) as st:
        budget = Budget(max_nodes=args.max_nodes, max_depth=2, direction="out")
        g = boundary.build_modules(st, spec, budget, alias=info.alias, dep_depth=args.dep_depth)
        if not g.nodes:
            print("warning: no symbols under any declared module prefix", file=sys.stderr)
        if args.format == "html":
            band_of, bands_meta, modules_list, zmatrix = _module_render_data(g, spec)
            _emit(render.to_html(g, band_of=band_of, bands_meta=bands_meta,
                                 module_mode=True, modules_list=modules_list,
                                 zone_matrix=zmatrix))
        elif args.format in ("json", "text"):
            data = render.modules_to_dict(g, spec)
            _emit(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            _emit(render.render(g, args.format))
    return 0
#### /cmd_modules ####


#### 为 N 路 html 渲染算出 band / bands_meta / 模块列表 / 区矩阵 [@380kkm 2026-06-05] ####
def _module_render_data(g, spec):
    band_of, bands_meta = _module_bands(g, spec)
    modules_list = [z.name for z in spec.zones] + [spec.fallback]
    mat = boundary.zone_matrix(g)
    # 区矩阵的可序列化网格：每对 {src,dst,edge_count}
    zmatrix = [{"src": a, "dst": b, "edge_count": s.edge_count, "kind": "intra" if a == b else "cross"}
               for (a, b), s in sorted(mat.items())]
    return band_of, bands_meta, modules_list, zmatrix
#### /N 路 html 渲染数据 ####


#### 按声明序（External 末列）把每个节点分到一个模块 band [@380kkm 2026-06-05] ####
def _module_bands(g, spec):
    order = [z.name for z in spec.zones] + [spec.fallback, "(ambiguous)"]
    band_index = {name: i for i, name in enumerate(order)}
    band_of = {nid: band_index.get(g.nodes[nid].attrs.get("module"), len(order) - 1)
               for nid in sorted(g.nodes)}
    present = sorted({band_of[nid] for nid in band_of})
    # 仅保留出现的 band，重新致密化下标，使列连续
    dense = {b: i for i, b in enumerate(present)}
    band_of = {nid: dense[b] for nid, b in band_of.items()}
    label_of = {dense[b]: order[b] for b in present}
    bands_meta = [{"band": i, "label": label_of[i]} for i in sorted(label_of)]
    return band_of, bands_meta
#### /模块 band 分配 ####


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

    md = sub.add_parser("modules", help="symbol-level N-way module zoning (decoupling views)")
    md.add_argument("--store", default=None, help="store dir, source.db path, or hub alias")
    md.add_argument("--alias", default=None, help="hub alias (synonym for --store)")
    md.add_argument("--root", default=None, help="source root to discover the store from")
    md.add_argument("--modules", default=None,
                    help="module spec file ({version,fallback,zones} or a {modules:{...}} "
                         "wrapper). Default: auto-discover manyread.json['modules'].")
    md.add_argument("--module", action="append", default=[],
                    help="inline zone NAME=PREFIX[,PREFIX...] (repeatable; merged onto file spec)")
    md.add_argument("--fallback", default="External",
                    help="zone name for paths matching no module (default: External)")
    md.add_argument("--max-nodes", dest="max_nodes", type=int, default=4000)
    md.add_argument("--dep-depth", dest="dep_depth", type=int, default=1,
                    help="fallback (External) expansion layers behind the surface (1=surface only)")
    md.add_argument("--format", choices=list(render.FORMATS), default="html")
    md.set_defaults(func=cmd_modules)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (SystemError, FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
#### /main ####


if __name__ == "__main__":
    raise SystemExit(main())
