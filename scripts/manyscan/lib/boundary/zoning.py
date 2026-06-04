# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyscan.lib.boundary.zoning — target↔dependency ZONING.

How to split indexed symbols into the TARGET (the code you are analyzing) and the
DEPENDENCY zone (what it depends on, possibly MANY sources), the path primitives
the rest of the boundary package builds on, and the dependency-side LABELLING.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib import deps, rollup

TARGET = "target"
DEPENDENCY = "dependency"

_NORM = deps.PathIndex._norm


# --- zoning ------------------------------------------------------------------
@dataclass(frozen=True)
class Zoning:
    """How to split symbols into the target (analyzed) and dependency zones.

    ``target_root`` is the normalized, trailing-slash-free directory prefix that
    defines the TARGET (``""`` means the whole repo is the target). ``dep_roots``
    are LABEL/grouping hints for the dependency side only — they NEVER change the
    target bit (a symbol is a dependency iff it is not under ``target_root``). The
    dependency side may aggregate MULTIPLE distinct dependency sources, one per hint.
    """

    target_root: str
    dep_roots: tuple[str, ...] = ()  # normalized, sorted longest-first


def norm_root(p: str) -> str:
    """Normalize a root path: slash-normalize, strip leading ``./``, strip trailing ``/``."""
    return _NORM(p or "").rstrip("/")


def detect_target_root(store) -> str:
    """Autodetect the target root: the shortest module root (``*.uplugin`` / ``*.Build.cs`` / …).

    Picks ``min`` by ``(len, str)`` for determinism; ``""`` (whole repo) if no
    module markers are present. NOTE: ``""`` is AMBIGUOUS — it is also the legitimate
    repo-root marker. Callers that must NOT silently classify the whole repo (incl.
    the dependencies) as target should use :func:`has_module_markers` to tell the two
    cases apart, or supply an explicit ``target_root``.
    """
    roots = rollup.module_roots(store)
    if not roots:
        return ""
    return min(roots, key=lambda r: (len(r), r))


def has_module_markers(store) -> bool:
    """True iff the index contains ANY module-marker file (``*.uplugin`` / ``*.Build.cs`` / …).

    When this is False, :func:`detect_target_root` cannot tell the target from its
    dependencies (the L1 indexer only stores configured source extensions, so
    ``.uplugin`` markers are typically absent) — so autodetect must NOT be trusted
    and an explicit ``--target-root`` is required. This avoids the SILENT, UNSOUND
    classification of the entire repo (dependencies included) as the target.
    """
    return bool(rollup.module_roots(store))


def make_zoning(store, target_root: str | None, dep_roots: list[str] | None) -> Zoning:
    """Build a :class:`Zoning`, autodetecting ``target_root`` when not given.

    ``dep_roots`` are normalized, de-duplicated, and sorted LONGEST-FIRST so that
    the most specific dependency module wins in :func:`dependency_label`. Multiple
    distinct dependency sources may be supplied.
    """
    pr = norm_root(target_root) if target_root is not None else detect_target_root(store)
    ers = sorted({norm_root(e) for e in (dep_roots or []) if norm_root(e)},
                 key=lambda r: (-len(r), r))
    return Zoning(target_root=pr, dep_roots=tuple(ers))


def zone_of_path(path: str | None, z: Zoning) -> str:
    """Classify a defining file path into ``target`` or ``dependency`` (sound containment).

    A symbol is TARGET iff its normalized path equals ``target_root`` or starts
    with ``target_root + '/'``. ``target_root == ""`` ⇒ everything is target.
    A missing path (no file) is conservatively a DEPENDENCY.
    """
    if path is None:
        return DEPENDENCY
    p = _NORM(path)
    pr = z.target_root
    if pr == "":
        return TARGET
    if p == pr or p.startswith(pr + "/"):
        return TARGET
    return DEPENDENCY


def dependency_label(path: str, z: Zoning) -> str:
    """A human label for a dependency-side symbol's file: ``<dep_root>::<basename>``.

    Uses the longest matching ``dep_root`` prefix (roots are longest-first);
    falls back to the bare basename when no dependency root matches.
    """
    p = _NORM(path or "")
    base = p.rsplit("/", 1)[-1]
    for er in z.dep_roots:  # already longest-first
        if er and (p == er or p.startswith(er + "/")):
            return f"{er}::{base}"
    return base
