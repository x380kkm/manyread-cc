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
import fnmatch
import json
import os
import sqlite3
import sys
from pathlib import Path

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


def _default_hidden_keys(g, vh: dict) -> list[str]:
    """DETERMINISTIC sorted list of node ids hidden by a resolved view_hide config.

    UNION semantics — a node is default-hidden if ANY holds:
      * its label (qualified) in ``names`` OR its trailing ``::`` segment in ``names``
      * fnmatch(label, p) OR fnmatch(segment, p) for any p in ``patterns``
      * ``min_fan_in`` is not None AND the node's fan_in >= min_fan_in

    ENGINE-SIDE ONLY: in a boundary graph (any node carries a target/dependency
    zone), only ``dependency``-zone nodes are eligible — the config can NEVER
    default-hide a target/internal symbol (you never want to drop the code you are
    analyzing). A non-boundary graph (no zones) is matched as before.

    Matching BOTH label and trailing segment catches bare-name externals
    (``dep:<name>`` / ``amb:<name>``, label == bare) AND qualified internals
    (``Outer::Inner::FString``) together. CASE-SENSITIVE (C++ identifiers are).
    Reuses ``render._importance`` for fan_in (already sorted/deterministic).
    """
    names = set(vh.get("names") or [])
    pats = list(vh.get("patterns") or [])
    mfi = vh.get("min_fan_in")
    imp = render._importance(g)              # {nid: {fan_in, ...}}, reused (no new metric)
    zoned = any(g.nodes[n].attrs.get("zone") in ("target", "dependency") for n in g.nodes)
    hit: set[str] = set()
    for nid in g.nodes:                      # g.nodes is a dict id->Node
        node = g.nodes[nid]
        if zoned and node.attrs.get("zone") != "dependency":
            continue                         # engine-side only: never hide target/internal symbols
        label = node.label or nid
        seg = label.rsplit("::", 1)[-1]
        if label in names or seg in names:
            hit.add(nid)
        elif any(fnmatch.fnmatch(label, p) or fnmatch.fnmatch(seg, p) for p in pats):
            hit.add(nid)
        elif mfi is not None and imp.get(nid, {}).get("fan_in", 0) >= mfi:
            hit.add(nid)
    return sorted(hit)                       # SORTED => byte-stable bake


def cmd_boundary(args) -> int:
    info = _store_info(args)
    with stores.Store(info.db_path) as st:
        # SOUNDNESS GUARD: autodetect relies on indexed module markers
        # (*.uplugin / *.Build.cs). The L1 indexer only stores configured source
        # extensions, so these markers are usually NOT indexed — autodetect would
        # then return "" and silently classify the ENTIRE repo (dependencies
        # included) as the target, which is unsound. Require an explicit
        # --target-root in that case. Pass --target-root "" to deliberately opt
        # into whole-repo = target.
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
        # HTML is ONE self-contained, in-page-toggleable page: always emit the FULL
        # graph (both zones + crossings) and let render.to_html's view selector switch
        # internal|dependency|both client-side. The internal_view / dependency_surface
        # projections stay for the non-HTML formats (json/text/dot), which are static.
        # --layers / --dep-depth affect ONLY the html path (bands are inert otherwise).
        if args.format == "html":
            band_of, bands_meta = boundary.assign_bands(g, args.layers)
            # Resolve the committed/overridden view-hide config (None => v0.6.0 bytes).
            mr_cfg = stores.manyread_lib()[0]
            vh = mr_cfg.load_view_hide(info.store, Path(args.ignore) if args.ignore else None)
            default_hidden = _default_hidden_keys(g, vh) if vh else None
            # GATED collapsible module<->symbol quotient view. off (default) => module_of /
            # modules_meta stay None, so NO module node-attr + NO `const MODULES=` line are
            # baked => DATA/consts bytes byte-identical to v0.6.2.
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


if __name__ == "__main__":
    raise SystemExit(main())
