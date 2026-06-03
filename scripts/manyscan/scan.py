# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan CLI — bounded, demand-driven dependency scans over manyread stores.

Turnkey subcommands so you invoke a tool, not hand-write queries:

    uv run --python 3.12 scripts/scan.py list-stores
    uv run --python 3.12 scripts/scan.py scan <seed>    --store <dir|alias> [opts]
    uv run --python 3.12 scripts/scan.py analyze <seed> --store <dir|alias> [opts]
    uv run --python 3.12 scripts/scan.py export <seed>  --store <dir|alias> [opts]

`scan` prints the bounded dependency slice (json/mermaid/dot/text); `analyze`
prints refactoring metrics; `export` defaults to graphviz dot. Common opts:
``--max-nodes N --depth D --dir out|in|both --level file|dir|module``.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import analyze, boundary, cache, render, rollup, scope, stores  # noqa: E402
from lib.graph import Budget  # noqa: E402


def _store_info(args) -> stores.StoreInfo:
    target = args.store or args.alias
    if not target and not args.root:
        raise SystemError("specify --store <dir|alias>, --alias <name>, or --root <path>")
    return stores.resolve(store=target, root=args.root)


def _budget(args) -> Budget:
    return Budget(max_nodes=args.max_nodes, max_depth=args.depth, direction=args.dir)


def _emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_list(args) -> int:
    for si in stores.list_stores():
        print(f"{si.alias:<30} {si.db_path}")
    return 0


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


def cmd_plugin_boundary(args) -> int:
    info = _store_info(args)
    with stores.Store(info.db_path) as st:
        # SOUNDNESS GUARD: autodetect relies on indexed module markers
        # (*.uplugin / *.Build.cs). The L1 indexer only stores configured source
        # extensions, so these markers are usually NOT indexed — autodetect would
        # then return "" and silently classify the ENTIRE repo (engine included) as
        # plugin, which is unsound. Require an explicit --plugin-root in that case.
        # Pass --plugin-root "" to deliberately opt into whole-repo = plugin.
        if args.plugin_root is None and not boundary.has_module_markers(st):
            print("error: cannot autodetect --plugin-root (no *.uplugin / *.Build.cs "
                  "markers are indexed; the L1 index only stores source extensions). "
                  "Pass --plugin-root <dir> explicitly (or --plugin-root \"\" to treat "
                  "the whole index as plugin).", file=sys.stderr)
            return 2
        z = boundary.make_zoning(st, args.plugin_root, args.engine_root)
        budget = Budget(max_nodes=args.max_nodes, max_depth=max(2, args.depth), direction="out")
        g = boundary.build(st, z, budget, alias=info.alias)
        if not g.nodes:
            print(f"warning: no plugin-zone symbols under plugin-root "
                  f"{z.plugin_root!r}", file=sys.stderr)
        # HTML is ONE self-contained, in-page-toggleable page: always emit the FULL
        # graph (both zones + crossings) and let render.to_html's view selector switch
        # internal|engine|both client-side. The internal_view / engine_surface
        # projections stay for the non-HTML formats (json/text/dot), which are static.
        if args.format == "html":
            _emit(render.to_html(g, view=args.view))
        else:
            if args.view == "internal":
                view = boundary.internal_view(g)
            elif args.view == "engine":
                view = boundary.engine_surface(g, rollup_modules=args.rollup_engine, store=st)
            else:
                view = g
            _emit(render.render(view, args.format))
    return 0


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

    pb = sub.add_parser("plugin-boundary", help="symbol-level plugin↔engine boundary")
    pb.add_argument("--store", default=None, help="store dir, source.db path, or hub alias")
    pb.add_argument("--alias", default=None, help="hub alias (synonym for --store)")
    pb.add_argument("--root", default=None, help="source root to discover the store from")
    pb.add_argument("--plugin-root", dest="plugin_root", default=None,
                    help="plugin root prefix (default: autodetect nearest *.uplugin/*.Build.cs)")
    pb.add_argument("--engine-root", dest="engine_root", action="append", default=[],
                    help="engine root hint for labelling (repeatable)")
    pb.add_argument("--max-nodes", dest="max_nodes", type=int, default=4000)
    pb.add_argument("--depth", type=int, default=2, help=">=2: plugin layer + engine sink")
    pb.add_argument("--view", choices=["internal", "engine", "both"], default="both")
    pb.add_argument("--rollup-engine", dest="rollup_engine", action="store_true",
                    help="(engine view) group engine targets by module root")
    pb.add_argument("--format", choices=list(render.FORMATS), default="html")
    pb.set_defaults(func=cmd_plugin_boundary)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (SystemError, FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
